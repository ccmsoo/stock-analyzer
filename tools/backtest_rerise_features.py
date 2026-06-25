"""
2차상승 필터 탐구 — "눌림 후 다시 오르는" 종목을 무엇이 가르나.

급등(>=12%) 후 초기고점→눌림→(이후 신고가=2차상승) 여부를 라벨링하고,
눌림 깊이 / 눌림 거래량 / MA 지지 / 초기 급등크기 별 2차상승률 비교.
→ 예측력 있는 피처를 레이더/UI 태그로.

데이터: hist_px(네이버 장기). 캔들만 → 빠름.

CLI:
  python -m tools.backtest_rerise_features --cache /tmp/hist_px.json --from 20250501 --until 20260602
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rate(rows, key_fn):
    """key_fn(row)->bucket. 버킷별 2차상승률."""
    by = defaultdict(list)
    for r in rows:
        b = key_fn(r)
        if b is not None:
            by[b].append(r)
    out = {}
    for b, rs in by.items():
        n = len(rs)
        rr = sum(1 for r in rs if r["rerose"]) / n * 100 if n else 0
        d20 = sum(r["ret20"] for r in rs) / n if n else 0
        out[b] = (rr, d20, n)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default="/tmp/hist_px.json")
    p.add_argument("--surge", type=float, default=12.0)
    p.add_argument("--from", dest="dfrom", default="20250501")
    p.add_argument("--until", default="20260602")
    p.add_argument("--cooldown", type=int, default=8)
    args = p.parse_args()

    px = json.loads(Path(args.cache).read_text())
    tickers = [k for k in px if not k.endswith("#order") and not k.startswith("#") and k not in ("069500", "229200")]
    names = px.get("#names", {})
    alld = sorted({d for t in tickers for d in px[t + "#order"]})
    dpos = {d: i for i, d in enumerate(alld)}

    rows, last = [], {}
    for t in tickers:
        order = px[t + "#order"]; m = px[t]
        for i, d in enumerate(order):
            if not (args.dfrom <= d <= args.until):
                continue
            if i + 21 >= len(order) or i - 15 < 0:  # 20d 베이스라인 + 15d 포워드
                continue
            prev = m[order[i + 1]]["close"]
            if not prev or (m[d]["close"] / prev - 1) * 100 < args.surge:
                continue
            if t in last and dpos.get(d, 0) - last[t] < args.cooldown:
                continue
            last[t] = dpos.get(d, 0)

            def H(k): return m[order[i - k]]["high"]
            def L(k): return m[order[i - k]]["low"]
            def C(k): return m[order[i - k]]["close"]
            def V(k): return m[order[i - k]]["volume"]

            surge_size = (m[d]["close"] / prev - 1) * 100
            # 초기고점 (D+1~3)
            ep_k = max(range(1, 4), key=lambda k: H(k))
            ep_high = H(ep_k)
            entry = m[order[i - 1]]["open"]
            # 초기고점 이후 ~D+15
            after = [k for k in range(ep_k + 1, 16) if i - k >= 0]
            if not after:
                continue
            trough_k = min(after, key=lambda k: L(k))
            trough_low = L(trough_k)
            dip_depth = (trough_low / ep_high - 1) * 100
            # 트로프 이후 신고가?
            post = [k for k in after if k > trough_k]
            rerose = bool(post) and max(H(k) for k in post) > ep_high
            ret20 = (C(min(15, ep_k + 12)) / entry - 1) * 100 if entry else 0
            # 눌림 거래량 (트로프 부근 3일) vs 급등前 20일 평균
            base_vol = sum(V(-k) for k in range(1, 21)) / 20  # i+1..i+20 (과거)
            dip_vol = sum(V(k) for k in range(max(ep_k, trough_k - 1), trough_k + 2) if i - k >= 0)
            dip_vol_days = len([k for k in range(max(ep_k, trough_k - 1), trough_k + 2) if i - k >= 0])
            dip_vol_ratio = (dip_vol / dip_vol_days) / base_vol if base_vol and dip_vol_days else None
            # 트로프일 MA20 지지 (트로프 종가 vs 트로프 포함 20일 평균)
            ti = i - trough_k
            ma20 = sum(m[order[ti + j]]["close"] for j in range(0, 20) if ti + j < len(order)) / 20
            above_ma = C(trough_k) >= ma20 if ma20 else None

            rows.append({
                "ticker": t, "name": names.get(t, t), "date": d, "rerose": rerose, "ret20": ret20,
                "surge_size": surge_size, "dip_depth": dip_depth,
                "dip_vol_ratio": dip_vol_ratio, "above_ma": above_ma,
            })

    base = sum(1 for r in rows if r["rerose"]) / len(rows) * 100 if rows else 0
    print(f"📊 눌림 후 케이스 {len(rows)}건 · 기본 2차상승률 {base:.0f}%\n")

    def show(title, res, order=None):
        print(f"=== {title} ===")
        keys = order or sorted(res)
        for k in keys:
            if k in res:
                rr, d20, n = res[k]
                print(f"   {str(k):16} 2차상승 {rr:>3.0f}%  D+20 {d20:>+5.1f}%  (n={n})")
        print()

    show("눌림 깊이 (초기고점 대비)", rate(rows, lambda r: (
        "A.얕음(>-12%)" if r["dip_depth"] > -12 else "B.중간(-12~-20%)" if r["dip_depth"] > -20 else "C.깊음(<-20%)")),
        ["A.얕음(>-12%)", "B.중간(-12~-20%)", "C.깊음(<-20%)"])

    show("눌림 거래량 (급등前 평균比)", rate(rows, lambda r: None if r["dip_vol_ratio"] is None else (
        "A.저거래(<1x)" if r["dip_vol_ratio"] < 1 else "B.중간(1~2x)" if r["dip_vol_ratio"] < 2 else "C.고거래(>2x)")),
        ["A.저거래(<1x)", "B.중간(1~2x)", "C.고거래(>2x)"])

    show("트로프 MA20 지지", rate(rows, lambda r: None if r["above_ma"] is None else ("MA위(지지)" if r["above_ma"] else "MA아래(이탈)")),
        ["MA위(지지)", "MA아래(이탈)"])

    show("초기 급등 크기", rate(rows, lambda r: (
        "A.12~18%" if r["surge_size"] < 18 else "B.18~25%" if r["surge_size"] < 25 else "C.25%+(상한가)")),
        ["A.12~18%", "B.18~25%", "C.25%+(상한가)"])

    # 복합: 얕은눌림 + 저거래 + MA위
    good = [r for r in rows if r["dip_depth"] > -12 and r["dip_vol_ratio"] is not None and r["dip_vol_ratio"] < 1.2 and r["above_ma"]]
    if good:
        rr = sum(1 for r in good if r["rerose"]) / len(good) * 100
        d20 = sum(r["ret20"] for r in good) / len(good)
        print(f"=== 복합필터 (얕은눌림+저거래+MA위) ===\n   2차상승 {rr:.0f}%  D+20 {d20:+.1f}%  (n={len(good)})")

    json.dump({"n": len(rows), "base_rerise": base, "rows": rows},
              open(ROOT / "profitability" / "output" / "rerise_features.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n💾 저장: profitability/output/rerise_features.json")


if __name__ == "__main__":
    main()
