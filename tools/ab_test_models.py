"""
A/B 테스트: gpt-5-mini vs gpt-4o (또는 임의의 두 모델)
======================================================
같은 샘플 10건에 대해 두 모델을 호출하고 결과를 비교 저장한다.

**중요**: 이 스크립트는 운영 모델을 변경하지 않는다. 비교 결과만 JSON/MD 로 저장한다.
state/signals.json, reports/* 은 절대 건드리지 않는다 (read-only).

사용 예:
    ./venv/bin/python -m tools.ab_test_models                 # 최신 리포트의 unclear 10건
    ./venv/bin/python -m tools.ab_test_models --report reports/report_20260514.csv
    ./venv/bin/python -m tools.ab_test_models --sample-size 5 --model-a gpt-5-mini --model-b gpt-4o
    ./venv/bin/python -m tools.ab_test_models --tickers 000650,048770,001540

산출:
    tools/output/ab_test_<YYYYMMDD_HHMMSS>.json   — 전체 raw 결과
    tools/output/ab_test_<YYYYMMDD_HHMMSS>.md    — 사람이 읽는 요약 + 시그널 일치도 표
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from openai import OpenAI

from analyzers.gpt_analyzer import SYSTEM_PROMPT, _build_user_prompt
from collectors.news_collector import collect_news_for_stock
from collectors.general_news_collector import search_news


def _call_model(client: OpenAI, model: str, stock: dict, articles: list[dict]) -> dict:
    """모델 호환 파라미터로 한 종목 분석. gpt-5* 만 reasoning_effort/max_completion_tokens 사용,
    그 외 (gpt-4o 등) 는 max_tokens 사용."""
    is_gpt5 = model.startswith("gpt-5")
    kwargs = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(stock, articles)},
        ],
    }
    if is_gpt5:
        kwargs["max_completion_tokens"] = 3000
        kwargs["reasoning_effort"] = "medium"
    else:
        kwargs["max_tokens"] = 3000
        kwargs["temperature"] = 0.2
    try:
        resp = client.chat.completions.create(**kwargs)
        txt = resp.choices[0].message.content.strip()
        return json.loads(txt)
    except json.JSONDecodeError as e:
        return {"main_theme": "분석 실패", "specific_signal": f"JSON 파싱 오류: {e}",
                "trigger_type": "unknown", "confidence": "low", "watch_keywords": [],
                "reason_unknown_category": "data_missing"}
    except Exception as e:
        return {"main_theme": "분석 실패", "specific_signal": f"API 오류: {e}",
                "trigger_type": "unknown", "confidence": "low", "watch_keywords": [],
                "reason_unknown_category": "data_missing"}


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports"
OUTPUT_DIR = ROOT / "tools" / "output"


def _latest_report() -> Path:
    cands = sorted(REPORT_DIR.glob("report_*.csv"))
    if not cands:
        raise FileNotFoundError("reports/ 에 report_*.csv 가 없습니다.")
    return cands[-1]


def _load_report(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _pick_samples(rows: list[dict], sample_size: int, only_unclear: bool,
                   explicit_tickers: list[str] | None) -> list[dict]:
    if explicit_tickers:
        wanted = {t.strip() for t in explicit_tickers if t.strip()}
        return [r for r in rows if r["ticker"] in wanted]
    if only_unclear:
        rows = [r for r in rows if r.get("status") == "unclear" or r.get("confidence") == "low"]
    return rows[:sample_size]


def _merge_news(stock_news: list[dict], general_news: list[dict]) -> list[dict]:
    seen, merged = set(), []
    for a in stock_news + general_news:
        link = a.get("link", "")
        if link in seen:
            continue
        seen.add(link)
        merged.append(a)
    return merged


def _gather_articles(ticker: str, date_str: str, name: str, use_general: bool = True) -> list[dict]:
    stock_news = collect_news_for_stock(ticker, date_str, days_before=14)
    general_news: list[dict] = []
    if use_general and name:
        try:
            general_news = search_news(name, date_str, days_before=14, max_results=10) or []
        except Exception:
            general_news = []
    return _merge_news(stock_news, general_news)


def _stock_dict_from_row(row: dict) -> dict:
    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "market": row.get("market", ""),
        "close": float(row.get("close") or 0),
        "change_pct": float(row.get("change_pct") or 0),
        "volume": int(float(row.get("volume") or 0)),
        "date": row.get("date") or "",
    }


def _signal_match(a: dict, b: dict) -> dict:
    """두 결과의 핵심 필드 일치도."""
    keys = ("main_theme", "specific_signal", "trigger_type", "confidence",
            "reason_unknown_category")
    diffs = {}
    for k in keys:
        va, vb = (a.get(k) or "").strip(), (b.get(k) or "").strip()
        diffs[k] = {"a": va, "b": vb, "same": va == vb}
    # watch_keyword 자카드
    sa = {(k or "").strip().lower() for k in (a.get("watch_keywords") or [])}
    sb = {(k or "").strip().lower() for k in (b.get("watch_keywords") or [])}
    jaccard = round(len(sa & sb) / len(sa | sb), 3) if (sa | sb) else 0.0
    diffs["watch_keywords"] = {
        "a": sorted(sa), "b": sorted(sb),
        "jaccard": jaccard, "shared": sorted(sa & sb),
    }
    return diffs


def _call(client: OpenAI, model: str, stock: dict, articles: list[dict]) -> tuple[dict, float]:
    t0 = time.time()
    result = _call_model(client, model, stock, articles)
    return result, round(time.time() - t0, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="gpt-5-mini ↔ gpt-4o A/B 비교 (운영 영향 없음)")
    parser.add_argument("--report", type=Path, help="reports/report_YYYYMMDD.csv (기본: 최신)")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--model-a", default="gpt-5-mini")
    parser.add_argument("--model-b", default="gpt-4o")
    parser.add_argument("--tickers", help="콤마로 구분된 명시적 티커들 (예: 000650,048770)")
    parser.add_argument("--include-all", action="store_true",
                        help="unclear 뿐 아니라 전체 리포트에서 샘플링")
    parser.add_argument("--no-general", action="store_true",
                        help="search:일반뉴스 호출 스킵 (네트워크 절약)")
    args = parser.parse_args()

    report_path = args.report or _latest_report()
    rows = _load_report(report_path)
    samples = _pick_samples(
        rows,
        sample_size=args.sample_size,
        only_unclear=not args.include_all,
        explicit_tickers=args.tickers.split(",") if args.tickers else None,
    )
    if not samples:
        print("샘플이 없습니다.")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 가 필요합니다.")
    client = OpenAI(api_key=api_key)

    print(f"\n[A/B] {report_path.name} → {len(samples)}건 비교 ({args.model_a} vs {args.model_b})")
    results = []
    for i, row in enumerate(samples, 1):
        stock = _stock_dict_from_row(row)
        articles = _gather_articles(stock["ticker"], stock["date"], stock["name"],
                                    use_general=not args.no_general)
        print(f"  {i:2d}. {stock['ticker']} {stock['name']:18s} (기사 {len(articles)}건) ... ", end="", flush=True)
        a_result, a_latency = _call(client, args.model_a, stock, articles)
        b_result, b_latency = _call(client, args.model_b, stock, articles)
        diff = _signal_match(a_result, b_result)
        print(f"theme(A={a_result.get('main_theme','')[:8]} vs B={b_result.get('main_theme','')[:8]}) "
              f"trig(A={a_result.get('trigger_type','')} vs B={b_result.get('trigger_type','')}) "
              f"latency(A={a_latency}s B={b_latency}s)")
        results.append({
            "ticker": stock["ticker"], "name": stock["name"], "change_pct": stock["change_pct"],
            "article_count": len(articles),
            "model_a": args.model_a, "model_b": args.model_b,
            "a_result": a_result, "b_result": b_result,
            "a_latency_s": a_latency, "b_latency_s": b_latency,
            "diff": diff,
        })
        time.sleep(0.4)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"ab_test_{ts}.json"
    md_path = OUTPUT_DIR / f"ab_test_{ts}.md"

    json_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report": str(report_path),
        "model_a": args.model_a,
        "model_b": args.model_b,
        "sample_count": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 요약 마크다운
    md = [
        f"# A/B 모델 비교 — {ts}",
        f"- 입력: `{report_path.name}` · 샘플 {len(results)}건",
        f"- Model A = `{args.model_a}` · Model B = `{args.model_b}`",
        f"- 운영 모델은 변경하지 않음. 참고용 비교 결과만 저장.",
        "",
        "## 핵심 필드 일치 (A vs B)",
        "",
        "| ticker | name | trigger A | trigger B | conf A | conf B | reason_unknown A | reason_unknown B | kw jaccard |",
        "|---|---|---|---|---|---|---|---|---:|",
    ]
    same_trig = same_conf = 0
    avg_jac = []
    for r in results:
        d = r["diff"]
        if d["trigger_type"]["same"]:
            same_trig += 1
        if d["confidence"]["same"]:
            same_conf += 1
        avg_jac.append(d["watch_keywords"]["jaccard"])
        md.append(
            f"| {r['ticker']} | {r['name']} | "
            f"{d['trigger_type']['a']} | {d['trigger_type']['b']} | "
            f"{d['confidence']['a']} | {d['confidence']['b']} | "
            f"{d['reason_unknown_category']['a']} | {d['reason_unknown_category']['b']} | "
            f"{d['watch_keywords']['jaccard']} |"
        )
    md += [
        "",
        f"### 종합",
        f"- trigger_type 일치: **{same_trig}/{len(results)}**",
        f"- confidence 일치: **{same_conf}/{len(results)}**",
        f"- watch_keywords 평균 Jaccard: **{round(sum(avg_jac)/len(avg_jac), 3) if avg_jac else 0}**",
        "",
        "## 종목별 specific_signal 비교",
        "",
    ]
    for r in results:
        d = r["diff"]
        md += [
            f"### {r['ticker']} {r['name']} ({r['change_pct']:+.2f}%, 기사 {r['article_count']}건)",
            f"- **A({args.model_a})**: {d['specific_signal']['a'] or '(없음)'}",
            f"- **B({args.model_b})**: {d['specific_signal']['b'] or '(없음)'}",
            f"- reason_unknown A=`{d['reason_unknown_category']['a']}`  B=`{d['reason_unknown_category']['b']}`",
            f"- 공통 키워드: {', '.join(d['watch_keywords']['shared']) or '(없음)'}",
            "",
        ]
    md_path.write_text("\n".join(md), encoding="utf-8")

    print(f"\n✓ 저장: {json_path}")
    print(f"✓ 저장: {md_path}")
    print(f"\n요약:")
    print(f"  trigger_type 일치: {same_trig}/{len(results)}")
    print(f"  confidence 일치:    {same_conf}/{len(results)}")
    if avg_jac:
        print(f"  watch_kw 평균 Jaccard: {round(sum(avg_jac)/len(avg_jac), 3)}")


if __name__ == "__main__":
    main()
