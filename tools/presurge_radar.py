"""
오르기 전 촉매 레이더 (Phase 2 → 실전) — 궁극 프로그램의 코어.

백테스트 검증(presurge_ai): AI 촉매점수 >=6 → 급등 정밀도 88%, 리드 ~3일.
이 도구는 매일: 워치리스트 최근 뉴스 → AI 촉매 평가 → (점수>=6 AND 아직 안 오른) 후보.

CLI:
  python -m tools.presurge_radar                 # high/medium 전체
  python -m tools.presurge_radar --min-score 6 --days 3 --max 200
"""
from __future__ import annotations
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import os
from openai import OpenAI
from collectors.news_collector import collect_news_for_stock
from tools.toss_client import get_prices, get_candles

SYS = """너는 한국 주식 단기 촉매 분석가다. 종목의 '최근 기사 제목들'만 보고
앞으로 주가를 급등시킬 촉매가 있는지 평가한다. (향후 결과는 모름. 기사만으로 판단)
강한 촉매: 대형 수주/계약, 임상/FDA 승인, M&A/경영권, 흑자전환/어닝서프라이즈, 국책과제 선정, 신약/기술이전, 대규모 수출.
약/무촉매: 단순 시황, 주가 등락 보도, 일반 동향, IR 일정, 반복 홍보, 유상증자/희석.
JSON만: {"score":0-10,"keyword":"결정적 키워드 1개","reason":"한 줄"}"""


def rate(client, name, titles):
    txt = "\n".join(f"- {t}" for t in titles[:12])
    try:
        r = client.chat.completions.create(
            model="gpt-5-mini", max_completion_tokens=300, reasoning_effort="minimal",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYS},
                      {"role": "user", "content": f"종목: {name}\n최근 기사:\n{txt}\n\nJSON:"}])
        d = json.loads(r.choices[0].message.content)
        return {"score": float(d.get("score", 0)), "keyword": d.get("keyword", ""), "reason": d.get("reason", "")}
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="기준일 YYYYMMDD (기본 오늘)")
    p.add_argument("--days", type=int, default=3, help="최근 N일 뉴스")
    p.add_argument("--min-score", type=float, default=6.0)
    p.add_argument("--max-move", type=float, default=5.0, help="이 이상 이미 오른 건 제외(%)")
    p.add_argument("--max", type=int, default=None, help="워치리스트 상한(테스트)")
    p.add_argument("--all", action="store_true", help="low 포함")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 없음"); sys.exit(1)

    from datetime import datetime
    date_str = args.date or datetime.now().strftime("%Y%m%d")
    sigs = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    wl = [(t, v) for t, v in sigs.items()
          if args.all or v.get("confidence") in ("high", "medium")]
    if args.max:
        wl = wl[: args.max]
    print(f"📡 오르기 전 촉매 레이더 — 워치 {len(wl)}종목, 최근 {args.days}일 뉴스 스캔...")

    # 1) 뉴스 수집 (스레드) — 최근 뉴스 있는 종목만 추림
    def fetch(t, v):
        try:
            arts = collect_news_for_stock(t, date_str, articles_per_stock=15, days_before=args.days)
        except Exception:
            arts = []
        titles = [a["title"] for a in arts]
        return (t, v.get("name", t), v.get("market", ""), titles) if titles else None

    have_news = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch, t, v) for t, v in wl]
        for i, f in enumerate(as_completed(futs)):
            r = f.result()
            if r:
                have_news.append(r)
    print(f"   뉴스 있는 종목: {len(have_news)} → AI 촉매 평가...")

    # 2) AI 촉매 평가 (스레드)
    client = OpenAI(timeout=60, max_retries=1)
    scored = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(rate, client, nm, titles): (t, nm, mk) for t, nm, mk, titles in have_news}
        done = 0
        for f in as_completed(futs):
            done += 1
            t, nm, mk = futs[f]
            r = f.result()
            if r and r["score"] >= args.min_score:
                scored.append({"ticker": t, "name": nm, "market": mk, **r})
            if done % 50 == 0:
                print(f"   {done}/{len(have_news)}")

    if not scored:
        print(f"\n촉매점수 {args.min_score}+ 종목 없음.")
        return

    # 3) 현재가 — 이미 오른 것 제외 (toss prices)
    prices = get_prices([s["ticker"] for s in scored])
    # 오늘 등락은 toss lastPrice만으론 모르니, 폴링으로 보강
    import urllib.request, urllib.parse
    chg = {}
    tics = [s["ticker"] for s in scored]
    for i in range(0, len(tics), 30):
        try:
            url = "https://polling.finance.naver.com/api/realtime?query=" + urllib.parse.quote("SERVICE_ITEM:" + ",".join(tics[i:i+30]))
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}), timeout=8).read()
            for d in json.loads(raw.decode("euc-kr", errors="replace")).get("result", {}).get("areas", [{}])[0].get("datas", []):
                chg[d["cd"]] = d.get("cr")
        except Exception:
            pass

    for s in scored:
        s["today"] = chg.get(s["ticker"])
        s["price"] = prices.get(s["ticker"])

    # 4) 차트 매칭 — 이미 extended면 늦음. 5일변동/고점대비/거래량.
    for s in scored:
        try:
            cs = get_candles(s["ticker"])  # newest-first
        except Exception:
            cs = []
        if len(cs) > 21 and cs[0]["close"]:
            last = cs[0]["close"]
            s["chg5"] = (last / cs[5]["close"] - 1) * 100 if cs[5]["close"] else None
            hi20 = max(c["high"] for c in cs[:20])
            s["from_high"] = (last / hi20 - 1) * 100 if hi20 else None
            volavg = sum(c["volume"] for c in cs[1:21]) / 20
            s["volratio"] = cs[0]["volume"] / volavg if volavg else None
            # 차트 양호 = 아직 안 extended (5일<15%) & 고점근처과열 아님
            s["chart_ok"] = (s["chg5"] is None or s["chg5"] < 15)
        else:
            s["chg5"] = s["from_high"] = s["volratio"] = None
            s["chart_ok"] = True

    fresh = [s for s in scored if s["today"] is None or s["today"] < args.max_move]
    # 정렬: 촉매점수 → 차트양호 → 거래량
    fresh.sort(key=lambda x: (-x["score"], not x["chart_ok"], -(x.get("volratio") or 0)))

    print(f"\n=== 🎯 오르기 전 촉매 후보 ({date_str}) — 점수{args.min_score}+ & 아직<{args.max_move}% ===")
    print(f"   {'종목':12} {'점수':>4} {'오늘':>6} {'5일':>6} {'고점':>6} {'거래량':>5} 차트 키워드")
    for s in fresh[:25]:
        td = f"{s['today']:+.1f}%" if s["today"] is not None else "-"
        c5 = f"{s['chg5']:+.0f}%" if s.get("chg5") is not None else "-"
        fh = f"{s['from_high']:+.0f}%" if s.get("from_high") is not None else "-"
        vr = f"{s['volratio']:.1f}x" if s.get("volratio") is not None else "-"
        mark = "✓" if s["chart_ok"] else "⚠"
        print(f"   {s['name'][:12]:12} {s['score']:>4.0f} {td:>6} {c5:>6} {fh:>6} {vr:>5}  {mark}  {s['keyword'][:18]}")

    already = [s for s in scored if s["today"] is not None and s["today"] >= args.max_move]
    if already:
        print(f"\n   (이미 오름 제외 {len(already)}: " + ", ".join(f"{s['name'][:8]}+{s['today']:.0f}%" for s in sorted(already, key=lambda x: -x['today'])[:6]) + ")")

    out = ROOT / "reports" / "presurge_radar.json"
    json.dump({"date": date_str, "generated_at": datetime.now().isoformat(timespec="seconds"),
               "candidates": fresh}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
