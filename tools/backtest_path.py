"""
경로(path) 백테스트 — "한번 오르고 떨어져도 다시 오르는" 2차 상승 체크.

D+5 종가로 끊으면 눌림 구간에서 손실처럼 보이지만, 이후 다시 오르는 경우가 있음.
급등 후 D+20까지 추적 → 회복률·2차상승률·언제 고점 오는지 측정.

CLI:
  python -m tools.backtest_path --surge 12 --from 20260401 --until 20260523
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KW_CACHE = Path("/tmp/kw_px.json")


def agg(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return (0.0, 0.0, 0)
    return (sum(vals) / len(vals), sum(1 for x in vals if x > 0) / len(vals) * 100, len(vals))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--from", dest="dfrom", default="20260401")
    p.add_argument("--until", default="20260523")  # D+20 확보
    p.add_argument("--cooldown", type=int, default=10)
    args = p.parse_args()
    if not KW_CACHE.exists():
        print("⚠️ /tmp/kw_px.json 없음 — backtest_keywords 먼저"); sys.exit(1)
    px = json.loads(KW_CACHE.read_text())
    tickers = sorted({k for k in px if "#order" not in k} - {"069500", "229200"})
    sigs = json.load(open(ROOT / "state" / "signals.json"))["signals"]
    names = {t: sigs.get(t, {}).get("name", t) for t in tickers}

    alld = sorted({d for t in tickers for d in px[t + "#order"]})
    dpos = {d: i for i, d in enumerate(alld)}

    rows, last = [], {}
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        for i, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            if i + 1 >= len(order) or i - 20 < 0:  # 전일 + D+20 확보
                continue
            prev = m[order[i + 1]]["close"]
            if not prev:
                continue
            chg = (m[d]["close"] / prev - 1) * 100
            if chg < args.surge:
                continue
            if t in last and dpos.get(d, 0) - last[t] < args.cooldown:
                continue
            last[t] = dpos.get(d, 0)
            entry = m[order[i - 1]]["open"]  # D+1 시초
            if not entry:
                continue
            close = lambda k: m[order[i - k]]["close"]
            high = lambda k: m[order[i - k]]["high"]
            low = lambda k: m[order[i - k]]["low"]
            r = lambda k: (close(k) / entry - 1) * 100
            mg = lambda a, b: (max(high(k) for k in range(a, b + 1)) / entry - 1) * 100
            # 초기(D+1~3) 고점 이후 눌림, 이후 재상승 탐지
            early_hi = max(high(k) for k in range(1, 4))
            after_low = min(low(k) for k in range(4, 21))
            after_hi = max(high(k) for k in range(4, 21))
            dipped = after_low <= early_hi * 0.92          # 초기고점서 8%+ 눌림
            rerise = dipped and after_hi >= early_hi        # 눌린 뒤 초기고점 재돌파
            rows.append({
                "ticker": t, "name": names.get(t, t), "date": d, "chg": round(chg, 1),
                "r5": r(5), "r10": r(10), "r20": r(20),
                "mg5": mg(1, 5), "mg10": mg(1, 10), "mg20": mg(1, 20),
                "dipped": dipped, "rerise": rerise,
            })

    print(f"📈 급등(>={args.surge}%) {len(rows)}건 (D+20 확보, {args.dfrom}~{args.until})\n")
    a5, a10, a20 = agg([x["r5"] for x in rows]), agg([x["r10"] for x in rows]), agg([x["r20"] for x in rows])
    print("=== 보유기간별 (D+1 시초 진입, 절대 종가수익) ===")
    print(f"   D+5  {a5[0]:>+5.1f}%  승률 {a5[1]:>4.0f}%")
    print(f"   D+10 {a10[0]:>+5.1f}%  승률 {a10[1]:>4.0f}%")
    print(f"   D+20 {a20[0]:>+5.1f}%  승률 {a20[1]:>4.0f}%")
    mg5, mg10, mg20 = agg([x["mg5"] for x in rows]), agg([x["mg10"] for x in rows]), agg([x["mg20"] for x in rows])
    print(f"\n=== 기간내 최대이익(고점이 언제 오나) ===")
    print(f"   D+5내 {mg5[0]:+.1f}% · D+10내 {mg10[0]:+.1f}% · D+20내 {mg20[0]:+.1f}%")

    # 핵심: 한번 오르고 떨어져도 다시 오르는가
    losers5 = [x for x in rows if x["r5"] is not None and x["r5"] < 0]
    recov = [x for x in losers5 if x["r20"] is not None and x["r20"] > 0]
    newhi = [x for x in losers5 if x["mg20"] is not None and x["mg5"] is not None and x["mg20"] > x["mg5"] + 3]
    print(f"\n=== 🔑 '오르고 떨어져도 다시' 체크 ===")
    print(f"   D+5 손실 종목: {len(losers5)}건")
    if losers5:
        print(f"   → 그중 D+20에 플러스 회복: {len(recov)}건 ({len(recov)/len(losers5)*100:.0f}%)")
        print(f"   → 그중 D+5 이후 신고가 재상승: {len(newhi)}건 ({len(newhi)/len(losers5)*100:.0f}%)")
    dip = [x for x in rows if x["dipped"]]
    rer = [x for x in rows if x["rerise"]]
    print(f"\n   초기고점서 8%+ 눌림: {len(dip)}건 ({len(dip)/len(rows)*100:.0f}%)")
    print(f"   → 눌린 뒤 초기고점 재돌파(2차상승): {len(rer)}건 ({len(rer)/max(len(dip),1)*100:.0f}% of 눌림)")
    # 2차상승 종목의 눌림목에서 D+20 수익 (재진입 가정)
    if rer:
        a = agg([x["r20"] for x in rer])
        print(f"   2차상승 종목 D+20 평균 {a[0]:+.1f}%")

    print(f"\n=== 예시 (D+5 손실 → D+20 회복) ===")
    for x in sorted(recov, key=lambda z: -z["r20"])[:10]:
        print(f"   {x['name'][:11]:11} {x['date']} 급등+{x['chg']:.0f}% → D+5 {x['r5']:+.0f}% → D+20 {x['r20']:+.0f}% (최대 {x['mg20']:+.0f}%)")

    out = ROOT / "profitability" / "output" / "path_backtest.json"
    json.dump({"n": len(rows), "rows": rows}, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
