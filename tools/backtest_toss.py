"""
토스 캔들 기반 시그널 검증 (직접 거래 없이 과거 동향으로 점검).

핵심 질문: "공시/시그널은 떴는데 아직 안 오른 종목을 (다음날 시초가에) 샀으면
            실제로 이후에 (시장 대비) 올랐나?"

방법:
  - state/signals.json 의 시그널(종목·날짜·트리거·신뢰도)
  - 토스 일봉으로 시그널 다음날(D+1) 시초가 진입 → D+1/3/5/10 종가 수익률
  - KODEX(시장) 대비 초과수익(excess=alpha) 으로 베타 제거
  - 시그널일 등락(d0)으로 세그먼트 → "안 오름 vs 이미 폭등" 비교

CLI:
  python -m tools.backtest_toss
  python -m tools.backtest_toss --max 80 --until 20260613
"""
from __future__ import annotations
import argparse, json, time, sys
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

FUND = {"disclosure", "contract", "earnings", "policy"}
BENCH = {"KOSPI": "069500", "KOSDAQ": "229200"}  # KODEX 200 / KODEX 코스닥150


def seg_d0(c):
    if c is None:
        return "?_unknown"
    if c < 3:
        return "A.안오름(<3%)"
    if c < 8:
        return "B.약간(3~8%)"
    if c < 15:
        return "C.오름(8~15%)"
    return "D.이미폭등(15%+)"


def agg(rows, key):
    rs = [r[key] for r in rows if r.get(key) is not None]
    if not rs:
        return (0.0, 0.0, 0)
    return (sum(rs) / len(rs), sum(1 for x in rs if x > 0) / len(rs) * 100, len(rs))


def fwd_returns(cs, idx, sd):
    """시그널일(sd) 기준 D+1 시초 진입 → D+k 종가 수익률 + maxgain/maxdd(D+1~5)."""
    if sd not in idx:
        return None
    di = idx[sd]
    if di < 1:
        return None
    entry = cs[di - 1]["open"]
    if not entry:
        return None
    def ret(k):
        j = di - k
        return (cs[j]["close"] / entry - 1) * 100 if 0 <= j < len(cs) else None
    highs = [cs[di - k]["high"] for k in range(1, 6) if 0 <= di - k < len(cs)]
    lows = [cs[di - k]["low"] for k in range(1, 6) if 0 <= di - k < len(cs)]
    d0 = (cs[di]["close"] / cs[di + 1]["close"] - 1) * 100 if di + 1 < len(cs) and cs[di + 1]["close"] else None
    return {
        "d0": d0,
        "r1": ret(1), "r3": ret(3), "r5": ret(5), "r10": ret(10),
        "maxgain5": (max(highs) / entry - 1) * 100 if highs else None,
        "maxdd5": (min(lows) / entry - 1) * 100 if lows else None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--until", default="20260613")
    p.add_argument("--max", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.1)
    args = p.parse_args()

    if not configured():
        print("❌ TOSS 키 없음 (.env)"); sys.exit(1)

    # 벤치마크(시장) 캔들 1회 로드
    print("📈 시장 벤치마크(KODEX) 로드...")
    benches = {}
    for mk, sym in BENCH.items():
        cs = get_candles(sym); time.sleep(args.sleep)
        benches[mk] = {"cs": cs, "idx": {c["date"]: k for k, c in enumerate(cs)}}
        print(f"   {mk}({sym}): {len(cs)}봉")

    s = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    items = [(t, v) for t, v in s.items()
             if v.get("last_seen") and v["last_seen"] <= args.until
             and v.get("confidence") in ("high", "medium")]
    if args.max:
        items = items[: args.max]
    print(f"📊 검증 대상 {len(items)}종목 — 캔들 수집...")

    results, fail = [], 0
    for i, (t, v) in enumerate(items):
        cs = get_candles(t); time.sleep(args.sleep)
        if not cs:
            fail += 1; continue
        idx = {c["date"]: k for k, c in enumerate(cs)}
        sd = v["last_seen"]
        fr = fwd_returns(cs, idx, sd)
        if not fr:
            fail += 1; continue
        # 시장 대비 초과수익
        mk = "KOSPI" if v.get("market") == "KOSPI" else "KOSDAQ"
        bm = benches.get(mk)
        br = fwd_returns(bm["cs"], bm["idx"], sd) if bm else None
        rec = {"ticker": t, "name": v.get("name", t), "trigger": v.get("trigger_type"),
               "conf": v.get("confidence"), "signal_date": sd, "market": mk,
               "d0_change": round(fr["d0"], 1) if fr["d0"] is not None else None}
        for k in ("r1", "r3", "r5", "r10", "maxgain5", "maxdd5"):
            rec[k] = fr[k]
        for k in ("r1", "r3", "r5", "r10"):
            rec["x" + k[1:]] = (fr[k] - br[k]) if (br and fr[k] is not None and br[k] is not None) else None
        results.append(rec)
        if (i + 1) % 50 == 0:
            print(f"   {i + 1}/{len(items)} (유효 {len(results)})")

    print(f"\n✓ 유효 {len(results)}건 / 실패·스킵 {fail}\n")
    if not results:
        return

    a = lambda k: agg(results, k)
    print(f"=== 전체 (D+1 시초 진입) n={len(results)} ===")
    print(f"   절대  D+1 {a('r1')[0]:+.1f}% · D+3 {a('r3')[0]:+.1f}% · D+5 {a('r5')[0]:+.1f}%({a('r5')[1]:.0f}%승) · D+10 {a('r10')[0]:+.1f}%")
    print(f"   초과  D+1 {a('x1')[0]:+.1f}% · D+3 {a('x3')[0]:+.1f}% · D+5 {a('x5')[0]:+.1f}%({a('x5')[1]:.0f}%승) · D+10 {a('x10')[0]:+.1f}%  ← 시장대비 alpha")

    print(f"\n=== [핵심] 시그널일 등락별 → 시장대비 초과수익(alpha) ===")
    print(f"{'세그먼트':16} {'n':>4} {'xD+1':>7} {'xD+3':>7} {'xD+5':>7} {'xD+5승률':>8} {'D+5내최대':>8}")
    bys = defaultdict(list)
    for r in results:
        bys[seg_d0(r["d0_change"])].append(r)
    for k in sorted(bys):
        rows = bys[k]
        print(f"{k:16} {len(rows):>4} {agg(rows,'x1')[0]:>+6.1f}% {agg(rows,'x3')[0]:>+6.1f}% "
              f"{agg(rows,'x5')[0]:>+6.1f}% {agg(rows,'x5')[1]:>7.0f}% {agg(rows,'maxgain5')[0]:>+7.1f}%")

    print(f"\n=== 트리거별 (시장대비 D+5) ===")
    byt = defaultdict(list)
    for r in results:
        byt[r["trigger"]].append(r)
    for k in sorted(byt, key=lambda x: -agg(byt[x], "x5")[0]):
        g = agg(byt[k], "x5")
        print(f"   {k:12} n={g[2]:>3}  초과D+5 {g[0]:>+6.1f}%  승률 {g[1]:>4.0f}%")

    print(f"\n=== [가설 검증] 시장대비 초과수익 ===")
    tgt = [r for r in results if r["trigger"] in FUND and r["d0_change"] is not None and r["d0_change"] < 3]
    g = agg(tgt, "x5"); mg = agg(tgt, "maxgain5")
    print(f"   ✅ 펀더멘털+안오름(<3%): n={g[2]}  초과D+5 {g[0]:+.1f}%  승률 {g[1]:.0f}%  D+5내최대 {mg[0]:+.1f}%")
    ch = [r for r in results if r["d0_change"] is not None and r["d0_change"] >= 15]
    gc = agg(ch, "x5")
    print(f"   ⚠️ 이미폭등(15%+) 추격:  n={gc[2]}  초과D+5 {gc[0]:+.1f}%  승률 {gc[1]:.0f}%")
    ru = [r for r in results if r["trigger"] in ("rumor", "technical")]
    gr = agg(ru, "x5")
    print(f"   ⚠️ 루머/수급:            n={gr[2]}  초과D+5 {gr[0]:+.1f}%  승률 {gr[1]:.0f}%")

    out = ROOT / "profitability" / "output" / "toss_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"n": len(results), "results": results}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
