"""
야간 일괄 재분석 — 본문 fetch + 보강된 프롬프트로 모든 일자 unclear 재분석.

- 입력: reports/report_*.csv 의 status=='unclear' or confidence=='low' 종목들
- 처리: collect_news_for_stock(14d) + get_article_body + analyze_single_stock
- 결과: state/signals.json 업데이트 + 각 일자별 report CSV/HTML/MD 재생성

매 종목 처리 후 logs/rerun_with_body.log 에 누적, 종료 시 텔레그램 완료 알림.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from openai import OpenAI

from state_manager import load_state, save_state, record_signal
from collectors.news_collector import collect_news_for_stock, get_article_body
from collectors.general_news_collector import search_news
from analyzers.gpt_analyzer import analyze_single_stock
from reporters.report_generator import generate_report
from main import _infer_reason_unknown
from monitor.live_radar import send_telegram


LOG_PATH = ROOT / "logs" / "rerun_with_body.log"
LOG_PATH.parent.mkdir(exist_ok=True)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _merge(a: list, b: list) -> list:
    seen, merged = set(), []
    for x in (a or []) + (b or []):
        link = x.get("link", "")
        if link in seen:
            continue
        seen.add(link)
        merged.append(x)
    return merged


def _stock_from_row(row: dict) -> dict:
    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "market": row.get("market", ""),
        "close": float(row.get("close") or 0),
        "change_pct": float(row.get("change_pct") or 0),
        "volume": int(float(row.get("volume") or 0)),
        "date": row.get("date") or "",
    }


def reanalyze_ticker(client: OpenAI, row: dict, use_general: bool = True,
                     fetch_body: bool = True, body_max_chars: int = 1500,
                     sleep_sec: float = 0.2) -> tuple[str, dict | None, str | None]:
    """한 종목 재분석. (ticker, result, error)"""
    ticker = row["ticker"]
    date_str = row.get("date") or ""
    stock = _stock_from_row(row)
    try:
        stock_news = collect_news_for_stock(ticker, date_str, articles_per_stock=20, days_before=14)
    except Exception as e:
        return ticker, None, f"stock_news: {e}"
    general = []
    if use_general:
        try:
            general = search_news(stock["name"], date_str, days_before=14, max_results=10) or []
        except Exception:
            general = []
    articles = _merge(stock_news, general)
    if not articles:
        return ticker, None, "no articles"

    if fetch_body:
        for a in articles[:12]:  # 본문은 상위 12건만 (속도)
            try:
                body = get_article_body(a["link"], max_chars=body_max_chars)
                a["body"] = body.strip() if body and len(body.strip()) >= 100 else ""
            except Exception:
                a["body"] = ""
            time.sleep(sleep_sec)

    try:
        result = analyze_single_stock(client, stock, articles, model="gpt-5-mini")
    except Exception as e:
        return ticker, None, f"ai: {str(e)[:80]}"
    return ticker, {"result": result, "articles": articles, "stock": stock}, None


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 가 필요합니다.")

    # 어떤 일자/종목을 재분석할지 reports/ 에서 수집
    targets_by_date: dict[str, list[dict]] = {}
    for csv_path in sorted((ROOT / "reports").glob("report_*.csv")):
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        date_str = csv_path.stem.replace("report_", "")
        unclear = [r for r in rows if r.get("status") == "unclear" or r.get("confidence") == "low"]
        if unclear:
            targets_by_date[date_str] = unclear

    total = sum(len(v) for v in targets_by_date.values())
    log(f"=== 야간 재분석 시작: {len(targets_by_date)}일자 / 총 {total}건 ===")
    for d, lst in targets_by_date.items():
        log(f"  {d}: {len(lst)}건")

    state = load_state()
    client = OpenAI()

    upgraded = still_low = errors = 0
    by_date_results: dict[str, dict[str, dict]] = {}  # date_str -> ticker -> {result, articles}

    for date_str, rows in targets_by_date.items():
        log(f"\n--- {date_str} 처리 시작 ({len(rows)}건) ---")
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(reanalyze_ticker, client, r): r for r in rows}
            for i, fut in enumerate(as_completed(futures), 1):
                row = futures[fut]
                ticker = row["ticker"]
                name = row["name"]
                try:
                    _, payload, err = fut.result()
                except Exception as e:
                    err = f"thread: {str(e)[:80]}"
                    payload = None

                if err or not payload:
                    errors += 1
                    log(f"  [{i:>3}/{len(rows)}] ❌ {ticker} {name}: {err}")
                    continue

                result = payload["result"]
                articles = payload["articles"]
                stock = payload["stock"]

                # reason_unknown_category 휴리스틱 보강 (AI 가 안 채웠으면)
                conf = (result.get("confidence") or "").lower()
                trig = (result.get("trigger_type") or "").lower()
                is_unclear_now = conf == "low" or trig == "unknown" or not result.get("specific_signal")
                if is_unclear_now and not result.get("reason_unknown_category"):
                    result["reason_unknown_category"] = _infer_reason_unknown(stock, articles, result)
                if not is_unclear_now:
                    result["reason_unknown_category"] = ""

                # state 업데이트 (per-ticker)
                record_signal(state, ticker, stock, result, date_str)

                by_date_results.setdefault(date_str, {})[ticker] = {
                    "result": result, "articles": articles,
                }

                if is_unclear_now:
                    still_low += 1
                    log(f"  [{i:>3}/{len(rows)}] · {ticker} {name}: 여전히 low ({result.get('reason_unknown_category','-')})")
                else:
                    upgraded += 1
                    sig = result.get("specific_signal", "")[:70]
                    log(f"  [{i:>3}/{len(rows)}] ✓ {ticker} {name} → {conf}/{trig}: {sig}")

        # 일자별 state 저장 (중간 저장)
        save_state(state)
        log(f"--- {date_str} 완료, state 저장됨 ---")

    # 모든 일자 끝나면 각 report CSV/HTML/MD 재생성
    log("\n=== 일자별 리포트 재생성 ===")
    for date_str, results in by_date_results.items():
        csv_path = ROOT / "reports" / f"report_{date_str}.csv"
        if not csv_path.exists():
            continue
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))

        # 전체 movers 복원 (unclear 만 업데이트, new 는 기존 그대로)
        movers_kospi, movers_kosdaq = [], []
        news_data, analysis, status_map = {}, {}, {}
        for r in rows:
            stock = _stock_from_row(r)
            t = stock["ticker"]
            if r["market"] == "KOSPI":
                movers_kospi.append(stock)
            else:
                movers_kosdaq.append(stock)

            if t in results:
                analysis[t] = results[t]["result"]
                news_data[t] = results[t]["articles"]
            else:
                # 기존 결과 보존
                analysis[t] = {
                    "main_theme": r.get("main_theme", ""),
                    "specific_signal": r.get("specific_signal", ""),
                    "trigger_type": r.get("trigger_type", ""),
                    "confidence": r.get("confidence", ""),
                    "reasoning": r.get("reasoning", ""),
                    "watch_keywords": [k.strip() for k in (r.get("watch_keywords") or "").split(",") if k.strip()],
                    "related_stocks": [k.strip() for k in (r.get("related_stocks") or "").split(",") if k.strip()],
                    "reason_unknown_category": r.get("reason_unknown_category", ""),
                    "trigger_date": r.get("trigger_date", ""),
                    "trigger_lag_days": int(float(r.get("trigger_lag_days") or 0)),
                }
                news_data[t] = []

            conf = (analysis[t].get("confidence") or "").lower()
            trig = (analysis[t].get("trigger_type") or "").lower()
            if conf == "low" or trig == "unknown" or not analysis[t].get("specific_signal"):
                status_map[t] = "unclear"
            else:
                status_map[t] = "new"

        movers = {"kospi_up": movers_kospi, "kosdaq_up": movers_kosdaq}
        generate_report(date_str, movers, news_data, analysis, status_map,
                        output_dir=ROOT / "reports", state=state)
        log(f"  ✓ report_{date_str} 재생성")

    # 마지막 stock_assistant.build 호출 → 통합 대시보드 갱신
    log("\n=== stock_assistant.build 통합 빌드 ===")
    try:
        import subprocess
        proc = subprocess.run(
            [str(ROOT / "venv/bin/python"), "-m", "stock_assistant.build", "--skip-life"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
        )
        log("build stdout tail:\n" + proc.stdout[-800:])
        if proc.returncode != 0:
            log("build stderr tail:\n" + proc.stderr[-800:])
    except Exception as e:
        log(f"build 실패: {e}")

    summary = (
        f"🌙 야간 재분석 완료\n"
        f"• 총 {total}건 (upgrade {upgraded} / 여전히 low {still_low} / 오류 {errors})\n"
        f"• 일자: {', '.join(sorted(targets_by_date.keys()))}\n"
        f"• 통합 홈: assistant_home.html"
    )
    log(summary)

    # 결과 요약 마크다운 — 텔레그램 안 가더라도 logs/에서 확인 가능
    md_lines = [
        f"# 🌙 야간 재분석 결과 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
        f"- 총 재분석 시도: **{total}건**",
        f"- ✅ unclear → 명확화: **{upgraded}건**",
        f"- 🔸 여전히 low: **{still_low}건**",
        f"- ❌ 오류: **{errors}건**",
        f"- 처리 일자: {', '.join(sorted(targets_by_date.keys()))}",
        "",
        "## 일자별 결과",
        "",
        "| 일자 | 재분석 시도 | upgrade | 여전히 low |",
        "|---|---:|---:|---:|",
    ]
    for d, rows in sorted(targets_by_date.items()):
        d_up = sum(1 for r in rows if d in by_date_results and r["ticker"] in by_date_results[d]
                   and (by_date_results[d][r["ticker"]]["result"].get("confidence", "").lower() in ("high", "medium"))
                   and by_date_results[d][r["ticker"]]["result"].get("trigger_type", "").lower() != "unknown"
                   and by_date_results[d][r["ticker"]]["result"].get("specific_signal"))
        d_low = sum(1 for r in rows if d in by_date_results and r["ticker"] in by_date_results[d]) - d_up
        md_lines.append(f"| {d} | {len(rows)} | {d_up} | {d_low} |")
    md_lines += [
        "",
        "## 핵심 명확화 사례 (top upgrade)",
        "",
    ]
    upgraded_samples = []
    for d, results in by_date_results.items():
        for tic, payload in results.items():
            res = payload["result"]
            conf = (res.get("confidence") or "").lower()
            trig = (res.get("trigger_type") or "").lower()
            if conf in ("high", "medium") and trig != "unknown" and res.get("specific_signal"):
                upgraded_samples.append((d, tic, res))
    for d, tic, res in upgraded_samples[:30]:
        name = next((r["name"] for r in targets_by_date[d] if r["ticker"] == tic), "")
        md_lines.append(
            f"- **{d} {tic} {name}** → {res.get('trigger_type')}/{res.get('confidence')}: "
            f"{res.get('specific_signal','')[:80]}"
        )
    md_lines += ["", f"전체 로그: `logs/rerun_with_body.log`"]
    summary_md = ROOT / "logs" / "rerun_summary.md"
    summary_md.write_text("\n".join(md_lines), encoding="utf-8")
    log(f"✓ 결과 요약 저장: {summary_md}")

    try:
        ok = send_telegram(summary)
        log("✓ 텔레그램 알림 전송" if ok else "텔레그램 알림 — 키 없음/실패")
    except Exception as e:
        log(f"텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        try:
            send_telegram("⚠️ 야간 재분석 실패 — logs/rerun_with_body.log 확인")
        except Exception:
            pass
        sys.exit(1)
