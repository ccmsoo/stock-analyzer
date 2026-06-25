"""
단기 스윙 백테스트 — 익절/손절/타임아웃 (≤7일). 일봉 bracket(과거 분봉 없음).

토스 분봉은 당일 100분만 → 과거 장중 익절/손절 '순서'를 알 수 없음.
→ 보수(손절 먼저) / 낙관(익절 먼저) 두 가정으로 net 승률을 bracket. 실제는 그 사이.

진입 시나리오:
  - pre  : 급등 D-3 시초 (촉매 레이더가 ~3일 전 잡아주는 시점, ~70% 정밀도 가정)
  - chase: 급등 D+1 시초 (추격 — 비교용)

CLI:
  python -m tools.backtest_swing --cache /tmp/hist_px.json --tp 8 --sl 5 --maxdays 7 --entry pre
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def walk(m, order, entry_idx, maxdays, tp, sl, sl_first):
    """entry_idx 시초 진입 → 이후 maxdays 일봉 따라 TP/SL/타임아웃. 반환 %."""
    entry = m[order[entry_idx]]["open"]
    if not entry:
        return None
    for k in range(1, maxdays + 1):
        j = entry_idx - k  # 미래(더 최신)
        if j < 0:
            break
        hi = m[order[j]]["high"]; lo = m[order[j]]["low"]
        hit_tp = hi >= entry * (1 + tp / 100)
        hit_sl = lo <= entry * (1 - sl / 100)
        if sl_first:
            if hit_sl:
                return -sl
            if hit_tp:
                return tp
        else:
            if hit_tp:
                return tp
            if hit_sl:
                return -sl
    # 타임아웃 — 마지막 종가
    j = max(entry_idx - maxdays, 0)
    return (m[order[j]]["close"] / entry - 1) * 100


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default="/tmp/hist_px.json")
    p.add_argument("--tp", type=float, default=8.0)
    p.add_argument("--sl", type=float, default=5.0)
    p.add_argument("--maxdays", type=int, default=7)
    p.add_argument("--entry", choices=["pre", "chase"], default="pre")
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--from", dest="dfrom", default="20250801")
    p.add_argument("--until", default="20260523")
    p.add_argument("--cooldown", type=int, default=8)
    args = p.parse_args()

    px = json.loads(Path(args.cache).read_text())
    tickers = [k for k in px if not k.endswith("#order") and not k.startswith("#") and k not in ("069500", "229200")]
    names = px.get("#names", {})
    off = 3 if args.entry == "pre" else -1  # pre: D-3(index+3), chase: D+1(index-1)

    alld = sorted({d for t in tickers for d in px[t + "#order"]})
    dpos = {d: i for i, d in enumerate(alld)}

    cons, opt, holds = [], [], []
    last = {}
    examples = []
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        for i, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            if i + 1 >= len(order):
                continue
            prev = m[order[i + 1]]["close"]
            if not prev or (m[d]["close"] / prev - 1) * 100 < args.surge:
                continue
            if t in last and dpos.get(d, 0) - last[t] < args.cooldown:
                continue
            last[t] = dpos.get(d, 0)
            ei = i + off
            if ei < 0 or ei >= len(order) or ei - args.maxdays < 0:
                continue
            rc = walk(m, order, ei, args.maxdays, args.tp, args.sl, True)
            ro = walk(m, order, ei, args.maxdays, args.tp, args.sl, False)
            if rc is None or ro is None:
                continue
            # 순수 보유(타임아웃 종가)
            entry = m[order[ei]]["open"]
            jh = max(ei - args.maxdays, 0)
            hold = (m[order[jh]]["close"] / entry - 1) * 100
            cons.append(rc); opt.append(ro); holds.append(hold)
            if ro >= args.tp:
                examples.append((names.get(t, t), d, ro))

    def stat(xs):
        if not xs:
            return (0, 0, 0)
        return (sum(xs) / len(xs), sum(1 for x in xs if x > 0) / len(xs) * 100, len(xs))

    n = len(cons)
    print(f"📊 단기 스윙 백테스트 — {args.cache.split('/')[-1]}, 진입={args.entry}, TP+{args.tp}/SL-{args.sl}/{args.maxdays}일")
    print(f"   급등 {n}건 ({args.dfrom}~{args.until})\n")
    sc, so, sh = stat(cons), stat(opt), stat(holds)
    print(f"   보수(손절먼저): 평균 {sc[0]:+.1f}%  승률 {sc[1]:.0f}%")
    print(f"   낙관(익절먼저): 평균 {so[0]:+.1f}%  승률 {so[1]:.0f}%")
    print(f"   → 실제 net 은 이 사이 (대략 평균 {(sc[0]+so[0])/2:+.1f}%, 승률 {(sc[1]+so[1])/2:.0f}%)")
    print(f"   (참고) 익절없이 {args.maxdays}일 보유: 평균 {sh[0]:+.1f}%  승률 {sh[1]:.0f}%")
    print(f"\n   TP+{args.tp}% 도달(낙관 익절 성공) 비율: {so[1]:.0f}%")


if __name__ == "__main__":
    main()
