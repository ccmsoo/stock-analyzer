"""
빠른 일일 분석 — KRX prefetch 우회.

state 의 누적 시그널 종목들 (수백 개) 만 단일 fetch → 등락률 TOP 20 추출
→ 본문 fetch + AI 분석 → 정식 리포트/state 갱신.

장점:
  - KRX listing (922 + 1744 종목) hang 안 됨
  - 우리 시스템이 이미 추적 중인 종목만 fetch → 빠름 (~3분)
  - 본문 fetch + 보강 프롬프트 동일

한계:
  - 누적 시그널에 없는 신규 종목 (예: 첫 상장) 은 못 잡음
  - → 정식 fresh-run 보완재. 새 종목은 정식 main.py 에서.

CLI:
  python -m tools.quick_daily --date 20260519
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import urllib.request
from bs4 import BeautifulSoup
from openai import OpenAI

from state_manager import load_state, save_state, record_signal, prune_stale_articles
from collectors.news_collector import collect_news_for_stock, get_article_body
from analyzers.gpt_analyzer import analyze_single_stock
from reporters.report_generator import generate_report
from main import _infer_reason_unknown


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.naver.com/",
}
def fetch_ohlcv_polling(ticker: str) -> dict | None:
    """Naver polling endpoint — 오늘 장 마감 후 데이터 (sise_day 가 늦어질 때 fallback)."""
    try:
        url = "https://polling.finance.naver.com/api/realtime?query=" + urllib.parse.quote(f"SERVICE_ITEM:{ticker}")
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read()
        try:
            txt = raw.decode("euc-kr")
        except UnicodeDecodeError:
            txt = raw.decode("utf-8", errors="replace")
        import json as _json
        data = _json.loads(txt)
        datas = data.get("result", {}).get("areas", [{}])[0].get("datas", [])
        if not datas:
            return None
        d = datas[0]
        nv = d.get("nv"); ov = d.get("ov"); hv = d.get("hv"); lv = d.get("lv"); pcv = d.get("pcv"); aq = d.get("aq", 0)
        if not (nv and pcv):
            return None
        change_pct = ((nv / pcv) - 1) * 100
        return {
            "ticker": ticker,
            "close": nv, "open": ov or nv, "high": hv or nv, "low": lv or nv,
            "volume": aq or 0,
            "change_pct": round(change_pct, 2),
            "prev_close": pcv,
        }
    except Exception:
        return None



def fetch_ohlcv(ticker: str, date_str: str) -> dict | None:
    """토스 공식 API 우선 → 실패 시 네이버 sise_day → polling 폴백."""
    # 1) 토스증권 공식 캔들 API (키 있을 때)
    try:
        from tools import toss_client
        if toss_client.configured():
            r = toss_client.ohlcv_for_date(ticker, date_str)
            if r:
                return r
    except Exception:
        pass
    # 2) Naver sise_day 페이지에서 date_str 의 OHLCV + 전일 종가 추출
    try:
        url = f"https://finance.naver.com/item/sise_day.naver?code={ticker}&page=1"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read()
        try:
            html = raw.decode("euc-kr")
        except UnicodeDecodeError:
            html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        rows = []  # [(YYYYMMDD, close, open, high, low, volume)]
        for tr in soup.select("table.type2 tr"):
            tds = tr.find_all("td")
            if len(tds) < 7:
                continue
            date_text = tds[0].get_text(strip=True)  # 2026.05.19
            if not date_text or "." not in date_text:
                continue
            try:
                y, m, d = date_text.split(".")
                ymd = f"{y}{m.zfill(2)}{d.zfill(2)}"
                close = int(tds[1].get_text(strip=True).replace(",", ""))
                op = int(tds[3].get_text(strip=True).replace(",", ""))
                hi = int(tds[4].get_text(strip=True).replace(",", ""))
                lo = int(tds[5].get_text(strip=True).replace(",", ""))
                vol = int(tds[6].get_text(strip=True).replace(",", ""))
                rows.append((ymd, close, op, hi, lo, vol))
            except (ValueError, IndexError):
                continue
        if not rows:
            return None
        # 우리가 원하는 date_str + 그 이전 일자 찾기
        idx = next((i for i, r in enumerate(rows) if r[0] == date_str), -1)
        if idx == -1 or idx + 1 >= len(rows):
            # sise_day 에 오늘 데이터 아직 없으면 polling fallback
            # sise_day 에 date_str 없으면 polling fallback (최근 마감일 데이터)
            return fetch_ohlcv_polling(ticker)
        target = rows[idx]
        prev_close = rows[idx + 1][1]  # rows 는 최신순 정렬이라 다음 인덱스가 더 과거
        change_pct = ((target[1] / prev_close) - 1) * 100 if prev_close else 0
        return {
            "ticker": ticker,
            "close": target[1],
            "open": target[2],
            "high": target[3],
            "low": target[4],
            "volume": target[5],
            "change_pct": round(change_pct, 2),
            "prev_close": prev_close,
        }
    except Exception:
        return fetch_ohlcv_polling(ticker)


def classify_market(ticker: str, sig: dict) -> str:
    return sig.get("market") or "KOSPI"


def main() -> None:
    parser = argparse.ArgumentParser(description="빠른 일일 분석 (KRX prefetch 우회)")
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--top", type=int, default=20, help="등락률 TOP N (default 20)")
    parser.add_argument("--min-pct", type=float, default=3.0, help="최소 등락률 % (default 3)")
    parser.add_argument("--no-body", action="store_true", help="본문 fetch 끄기")
    args = parser.parse_args()

    date_str = args.date

    if not __import__("os").environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 필요")
        sys.exit(1)

    state = load_state()
    signals_state = state.get("signals", {})

    # 시그널 종목 후보 (high/medium 만 — 노이즈 ↓)
    candidates = [
        (t, s) for t, s in signals_state.items() if s.get("confidence") in ("high", "medium")
    ]
    print(f"📦 누적 시그널 종목 {len(candidates)}개 중 {date_str} 가격 fetch...")

    t0 = time.time()
    quotes = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_ohlcv, t, date_str): (t, s) for t, s in candidates}
        done = 0
        for fut in cf.as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"   ... {done}/{len(candidates)} fetch")
            q = fut.result()
            if q:
                quotes[q["ticker"]] = q
    print(f"   ✓ {len(quotes)}개 fetch ({time.time()-t0:.1f}초)")

    # 등락률 TOP 추출 (KOSPI/KOSDAQ 분리)
    rows = []
    for tic, q in quotes.items():
        if q["change_pct"] < args.min_pct:
            continue
        sig = signals_state.get(tic, {})
        rows.append({
            "ticker": tic,
            "name": sig.get("name", tic),
            "market": sig.get("market", "KOSPI"),
            "close": int(q["close"]),
            "change_pct": q["change_pct"],
            "volume": q["volume"],
            "date": date_str,
        })
    rows.sort(key=lambda r: -r["change_pct"])

    # 등락률 상위 N (시장 통합 랭킹) — 시장별 10/10 분리 대신 진짜 상위 20
    all_stocks = rows[: args.top]
    kospi = [r for r in all_stocks if r["market"] == "KOSPI"]
    kosdaq = [r for r in all_stocks if r["market"] != "KOSPI"]
    cutoff = all_stocks[-1]["change_pct"] if all_stocks else 0
    print(f"📈 등락률 상위 {len(all_stocks)}건 (KOSPI {len(kospi)} / KOSDAQ {len(kosdaq)}) — 컷오프 {cutoff:+.1f}%")

    if not all_stocks:
        print("⚠️  조건 맞는 종목 없음")
        return

    movers = {"kospi_up": kospi, "kosdaq_up": kosdaq}

    # 뉴스 + 본문 fetch
    print(f"\n📰 뉴스 수집 (14일 윈도우) + 본문 fetch...")
    news_data = {}
    for s in all_stocks:
        t = s["ticker"]
        try:
            arts = collect_news_for_stock(t, date_str, articles_per_stock=20, days_before=14)
        except Exception as e:
            print(f"   ⚠️  [{t}] news: {e}")
            arts = []
        if not args.no_body and arts:
            for a in arts[:12]:
                try:
                    body = get_article_body(a["link"], max_chars=1500)
                    a["body"] = body.strip() if body and len(body.strip()) >= 100 else ""
                except Exception:
                    a["body"] = ""
                time.sleep(0.2)
        news_data[t] = arts
        print(f"   - [{t}] {s['name']:15} 기사 {len(arts)}건")

    # AI 분석
    print(f"\n🤖 AI 분석 — gpt-5-mini ({len(all_stocks)}건)...")
    # timeout=120s, retry=1 → 한 종목의 hang 이 배치 전체를 막지 않게
    client = OpenAI(timeout=120.0, max_retries=1)
    analysis = {}
    status_map = {}
    for s in all_stocks:
        t = s["ticker"]
        arts = news_data.get(t, [])
        print(f"   - [{t}] {s['name']:15} 분석 중 (기사 {len(arts)}건)...")
        try:
            result = analyze_single_stock(client, s, arts, model="gpt-5-mini")
        except Exception as e:
            result = {
                "main_theme": "분석 실패",
                "specific_signal": f"API: {e}",
                "confidence": "low",
                "trigger_type": "unknown",
                "watch_keywords": [],
                "related_stocks": [],
                "reasoning": "",
                "reason_unknown_category": "data_missing",
            }
        analysis[t] = result

        conf = (result.get("confidence") or "").lower()
        trig = (result.get("trigger_type") or "").lower()
        if conf == "low" or trig == "unknown" or not result.get("specific_signal"):
            status_map[t] = "unclear"
            if not result.get("reason_unknown_category"):
                result["reason_unknown_category"] = _infer_reason_unknown(s, arts, result)
        else:
            status_map[t] = "new"
            result["reason_unknown_category"] = ""

        record_signal(state, t, s, result, date_str)

    # 리포트 + state 저장
    print(f"\n💾 리포트 + state 저장...")
    output_dir = ROOT / "reports"
    generate_report(date_str, movers, news_data, analysis, status_map, output_dir=output_dir, state=state)
    prune_stale_articles(state)
    save_state(state)

    new_count = sum(1 for v in status_map.values() if v == "new")
    unclear_count = sum(1 for v in status_map.values() if v == "unclear")
    print(f"\n✅ 완료! 신규 {new_count} / 이유 불명 {unclear_count} / 총 {len(all_stocks)}")
    print(f"   리포트: reports/report_{date_str}.html")


if __name__ == "__main__":
    main()
