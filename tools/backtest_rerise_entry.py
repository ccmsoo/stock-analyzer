"""
눌림목 재진입 '진입 수익' 백테스트 — 레이더 로직 그대로.

레이더 트리거: 최근 8일 내 급등(>=12%) + 현재 얕은 눌림(>-12% from 최근고점) + 20일선 위.
그 날(T) 다음날 시초 진입 → D+3/5/10 수익·승률. 필터 vs 무필터(최근급등+아무 눌림) 비교.

CLI:
  python -m tools.backtest_rerise_entry --cache /tmp/hist_px.json --from 20250501 --until 20260605
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stat(rows, key):
    xs = [r[key] for r in rows if r.get(key) is not None]
    if not xs:
        return (0.0, 0.0, 0)
    return (sum(xs) / len(xs), sum(1 for x in xs if x > 0) / len(xs) * 100, len(xs))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default="/tmp/hist_px.json")
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--from", dest="dfrom", default="20250501")
    p.add_argument("--until", default="20260605")
    p.add_argument("--cooldown", type=int, default=8)
    args = p.parse_args()

    px = json.loads(Path(args.cache).read_text())
    tickers = [k for k in px if not k.endswith("#order") and not k.startswith("#") and k not in ("069500", "229200")]
    names = px.get("#names", {})
    market = px.get("#market", {})
    alld = sorted({d for t in tickers for d in px[t + "#order"]})
    dpos = {d: i for i, d in enumerate(alld)}

    BENCH = {"KOSPI": "069500", "KOSDAQ": "229200"}

    def bench_fwd(mk, d, k):
        sym = BENCH.get(mk, BENCH["KOSDAQ"])
        if sym not in px:
            return None
        order = px[sym + "#order"]; m = px[sym]
        if d not in m:
            return None
        i = order.index(d)
        if i - 1 < 0 or i - 1 - k < 0:
            return None
        e = m[order[i - 1]]["open"]
        return (m[order[i - 1 - k]]["close"] / e - 1) * 100 if e else None

    filt, base = [], []
    last_f, last_b = {}, {}
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        mk = market.get(t, "KOSDAQ")
        for ti, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            if ti - 11 < 0 or ti + 20 >= len(order):  # 포워드10 + MA20 베이스
                continue
            # 최근 8일 내 급등(>=surge)?
            surged = False
            for j in range(ti + 1, ti + 9):
                if j + 1 < len(order):
                    pv = m[order[j + 1]]["close"]
                    if pv and (m[order[j]]["close"] / pv - 1) * 100 >= args.surge:
                        surged = True; break
            if not surged:
                continue
            peak = max(m[order[k]]["high"] for k in range(ti, ti + 9))
            cl = m[order[ti]]["close"]
            pullback = (cl / peak - 1) * 100 if peak else 0
            if not (-25 < pullback < 0):  # 눌림 상태 (고점 아래)
                continue
            ma20 = sum(m[order[ti + j]]["close"] for j in range(0, 20)) / 20
            entry = m[order[ti - 1]]["open"]
            if not entry:
                continue

            def fwd(k):
                j = ti - 1 - k
                return (m[order[j]]["close"] / entry - 1) * 100 if j >= 0 else None

            def xfwd(k):
                r = fwd(k); b = bench_fwd(mk, d, k)
                return (r - b) if r is not None and b is not None else None

            rec = {"ticker": t, "name": names.get(t, t), "date": d, "pullback": round(pullback, 1),
                   "r3": fwd(3), "r5": fwd(5), "r10": fwd(10),
                   "x5": xfwd(5), "x10": xfwd(10)}
            # 무필터 베이스 (최근급등 + 아무 눌림)
            if t not in last_b or dpos.get(d, 0) - last_b[t] >= args.cooldown:
                last_b[t] = dpos.get(d, 0); base.append(rec)
            # 필터: 얕은 눌림(>-12%) + 20일선 위
            if pullback > -12 and cl >= ma20:
                if t not in last_f or dpos.get(d, 0) - last_f[t] >= args.cooldown:
                    last_f[t] = dpos.get(d, 0); filt.append(rec)

    print(f"📊 눌림목 재진입 진입 백테스트 ({args.cache.split('/')[-1]}, {args.dfrom}~{args.until})\n")
    for label, rows in [("🟢 필터 (얕은눌림>-12% + 20일선위)", filt), ("⚪ 무필터 (최근급등+아무눌림)", base)]:
        s3, s5, s10 = stat(rows, "r3"), stat(rows, "r5"), stat(rows, "r10")
        x5, x10 = stat(rows, "x5"), stat(rows, "x10")
        print(f"{label}  n={len(rows)}")
        print(f"   절대  D+3 {s3[0]:+.1f}%({s3[1]:.0f}%) · D+5 {s5[0]:+.1f}%({s5[1]:.0f}%) · D+10 {s10[0]:+.1f}%({s10[1]:.0f}%)")
        print(f"   시장대비  D+5 {x5[0]:+.1f}%({x5[1]:.0f}%) · D+10 {x10[0]:+.1f}%({x10[1]:.0f}%)\n")

    print("=== 필터 진입 예시 (D+5 상위) ===")
    for r in sorted([x for x in filt if x["r5"] is not None], key=lambda z: -z["r5"])[:8]:
        print(f"   {r['name'][:11]:11} {r['date']} 눌림{r['pullback']:+.0f}% → D+5 {r['r5']:+.0f}% D+10 {r['r10']:+.0f}%")

    json.dump({"filter": filt, "base": base}, open(ROOT / "profitability" / "output" / "rerise_entry.json", "w"), ensure_ascii=False, indent=1)
    print("\n💾 저장: profitability/output/rerise_entry.json")


if __name__ == "__main__":
    main()
