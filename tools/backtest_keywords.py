"""
키워드 예측력 백테스트 (Phase 1) — "어떤 단어가 주가를 움직였나".

누적 시그널의 키워드(watch_keywords + deep_keywords)별로,
그 키워드가 들어간 시그널들의 이후 수익률(시장대비)을 집계.
→ 결정적 키워드(주가를 좌지우지한 한 단어) 정량화.

주의: 현재 시그널은 급등 시점 기사 기반(coincident) → "이후 연속성" 예측.
      진짜 '오르기 전'은 Phase 2(급등 이전 기사 스크래핑)에서.

CLI:
  python -m tools.backtest_keywords
  python -m tools.backtest_keywords --horizon 5 --min-n 4 --until 20260613
"""
from __future__ import annotations
import argparse, json, sys, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from tools.toss_client import get_candles, configured

BENCH = {"KOSPI": "069500", "KOSDAQ": "229200"}
CACHE = Path("/tmp/kw_px.json")
STOP = {"기대", "기대감", "관련", "이슈", "테마", "상승", "급등", "강세", "전망", "보도", "소식",
        "가능성", "예정", "검토", "추진", "확대", "강화", "주가", "종목", "시장"}


def fwd(px, sym, d, k):
    order = px.get(sym + "#order", []); m = px.get(sym, {})
    if d not in m:
        return None
    i = order.index(d)
    if i - 1 < 0:
        return None
    entry = m[order[i - 1]]["open"]
    j = i - k
    if not entry or j < 0:
        return None
    return (m[order[j]]["close"] / entry - 1) * 100


def agg(vals):
    if not vals:
        return (0.0, 0.0, 0)
    return (sum(vals) / len(vals), sum(1 for x in vals if x > 0) / len(vals) * 100, len(vals))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--until", default="20260613")
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--min-n", type=int, default=4)
    p.add_argument("--sleep", type=float, default=0.05)
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()
    if not configured():
        print("❌ TOSS 키 없음"); sys.exit(1)

    signals = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    items = []
    for t, v in signals.items():
        ls = v.get("last_seen", "")
        if not ls or ls > args.until:
            continue
        kws = set()
        for k in (v.get("watch_keywords") or []):
            k = (k or "").strip()
            if len(k) >= 2 and k not in STOP:
                kws.add(k)
        deep = v.get("deep_keywords") or {}
        for cat in ("products", "partners", "events"):
            for k in (deep.get(cat) or []):
                k = (k or "").strip()
                if len(k) >= 2 and k not in STOP:
                    kws.add(k)
        if kws:
            items.append({"ticker": t, "date": ls,
                          "market": "KOSPI" if v.get("market") == "KOSPI" else "KOSDAQ",
                          "trigger": v.get("trigger_type"), "kws": kws})
    tickers = sorted({it["ticker"] for it in items})
    print(f"📊 시그널 {len(items)}건 / 종목 {len(tickers)} — 캔들 수집...")

    need = list(BENCH.values()) + tickers
    px = {}
    if CACHE.exists() and not args.refresh:
        px = json.loads(CACHE.read_text())
    miss = [s for s in need if s not in px]
    for sym in miss:
        cs = get_candles(sym); time.sleep(args.sleep)
        px[sym] = {c["date"]: c for c in cs}
        px[sym + "#order"] = [c["date"] for c in cs]
    if miss:
        CACHE.write_text(json.dumps(px))
    print(f"   캔들 {len([k for k in px if '#order' not in k])}종목 준비\n")

    # 각 시그널의 시장대비 초과수익
    H = args.horizon
    for it in items:
        r = fwd(px, it["ticker"], it["date"], H)
        b = fwd(px, BENCH[it["market"]], it["date"], H)
        it["x"] = (r - b) if r is not None and b is not None else None

    valid = [it for it in items if it["x"] is not None]
    print(f"유효 {len(valid)}건 — 전체 평균 초과 D+{H}: {agg([it['x'] for it in valid])[0]:+.1f}%\n")

    # 키워드별 집계
    bykw = defaultdict(list)
    for it in valid:
        for k in it["kws"]:
            bykw[k].append(it["x"])
    rows = [(k, *agg(v)) for k, v in bykw.items() if len(v) >= args.min_n]

    print(f"=== 🟢 예측력 상위 키워드 (n>={args.min_n}, 초과 D+{H} 높은순) ===")
    print(f"   {'키워드':18} {'n':>3} {'초과D+'+str(H):>8} {'승률':>6}")
    for k, avg, win, n in sorted(rows, key=lambda x: -x[1])[:18]:
        print(f"   {k[:18]:18} {n:>3} {avg:>+7.1f}% {win:>5.0f}%")

    print(f"\n=== 🔴 마이너스 키워드 (하위) ===")
    for k, avg, win, n in sorted(rows, key=lambda x: x[1])[:8]:
        print(f"   {k[:18]:18} {n:>3} {avg:>+7.1f}% {win:>5.0f}%")

    # 승률 상위 (n 큰 것 중)
    print(f"\n=== 🎯 승률 상위 (n>={args.min_n+2}) ===")
    for k, avg, win, n in sorted([r for r in rows if r[3] >= args.min_n + 2], key=lambda x: -x[2])[:12]:
        print(f"   {k[:18]:18} {n:>3} {avg:>+7.1f}% {win:>5.0f}%")

    out = ROOT / "profitability" / "output" / "keyword_backtest.json"
    res = [{"keyword": k, "avg_excess": round(a, 2), "win_rate": round(w, 1), "n": n}
           for k, a, w, n in sorted(rows, key=lambda x: -x[1])]
    json.dump({"horizon": H, "keywords": res}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out} ({len(res)} 키워드)")


if __name__ == "__main__":
    main()
