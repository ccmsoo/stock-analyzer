"""수급+모멘텀 결합 전략 — "오른 종목 중 기관이 사지 않는 것"

이중정렬로 확인된 두 축 (docs/flow_findings.md):
  · 기관 5일 순매수 ↑ → 이후 열위. **모멘텀 상위 구간에서 가장 강함**(t=−5.08)
  · 모멘텀 상위 → 우위. **기관이 순매도 중일 때 가장 강함**(t=+4.88)
  · 둘 다 lag를 5일 줘도 살아남음 = 결제 소급갱신(look-ahead) 아님
  · 사이즈·모멘텀을 고정해도 각각 독립적으로 살아남음

그래서 신호 = 모멘텀 순위 − 기관순매수 순위 (둘 다 횡단면 순위로 표준화).

정직성 장치 (앞의 실패에서 배운 것 그대로):
  · 유니버스는 **그 시점 거래대금** 상위 N — 오늘 기준 시총으로 뽑지 않는다
  · 진입은 신호 다음날 종가(+lag), 왕복비용 0.4%
  · 벤치마크는 **KOSPI + 배당** (가격지수만 쓰면 벤치마크를 과소평가한다)

사용:  venv/bin/python -m tools.flow_strategy --flows state/flows.pkl
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics as st

from tools.strategy_lab import index_series

COST = 0.4
DIV_Y = 1.8      # KOSPI 배당수익률 대략치 — 가격지수엔 빠져있으므로 벤치마크에 더한다
CASH_Y = 3.0     # 현금 구간 이자 (파킹/MMF)


def build(flows, meta):
    """종목별 (날짜 오름차순) 시계열 + 파생값 캐시."""
    out = {}
    for tk, s in flows.items():
        days = sorted(s)
        if len(days) < 60:
            continue
        out[tk] = {"days": days, "rows": [s[d] for d in days],
                   "market": (meta.get(tk) or {}).get("market")}
    return out


def features(rows, i):
    av = sum(r["volume"] for r in rows[i - 19:i + 1]) / 20
    c0 = rows[i - 20]["close"]
    if not av or not c0:
        return None
    return {"mom": (rows[i]["close"] / c0 - 1) * 100,
            "inst": sum(r["inst"] for r in rows[i - 4:i + 1]) / (av * 5),
            "tv": sum(r["close"] * r["volume"] for r in rows[i - 19:i + 1]) / 20}


def run(ser, idx, dates, uni_n, top, hold, lag=1, w_inst=1.0, w_mom=1.0,
        trend=None, cash_y=CASH_Y):
    ik = sorted(idx)
    ma = {}
    if trend:
        for j, k in enumerate(ik):
            if j >= trend:
                ma[k] = sum(idx[x]["close"] for x in ik[j - trend + 1:j + 1]) / trend
    val, curve, rets, cash = 1.0, [], [], 0
    for d in dates[::hold]:
        if trend and d in ma and idx[d]["close"] < ma[d]:
            cash += 1
            val *= (1 + cash_y / 100 * hold / 246)
            curve.append(val)
            continue
        pool = []
        for tk, S in ser.items():
            days, rows = S["days"], S["rows"]
            if d not in S.get("idx_set", set()):
                pass
            try:
                i = days.index(d)
            except ValueError:
                continue
            if i < 25 or i + 1 + lag + hold >= len(rows):
                continue
            f = features(rows, i)
            if not f:
                continue
            e = rows[i + 1 + lag]["close"]
            if not e:
                continue
            r = (rows[i + 1 + lag + hold]["close"] / e - 1) * 100 - COST
            pool.append((f["tv"], f["mom"], f["inst"], r))
        if len(pool) < uni_n:
            continue
        pool.sort(key=lambda z: -z[0])
        uni = pool[:uni_n]
        n = len(uni)
        # 횡단면 순위로 표준화 (단위가 다른 두 신호를 더하려면 순위가 안전하다)
        rank_m = {id(x): k for k, x in enumerate(sorted(uni, key=lambda z: -z[1]))}
        rank_i = {id(x): k for k, x in enumerate(sorted(uni, key=lambda z: z[2]))}  # 기관 순매도가 좋음
        scored = sorted(uni, key=lambda x: w_mom * rank_m[id(x)] + w_inst * rank_i[id(x)])
        sel = [x[3] for x in scored[:top]]
        pr = sum(sel) / len(sel) / 100
        val *= (1 + pr)
        rets.append(pr * 100)
        curve.append(val)
    if len(rets) < 8:
        return None
    mx, mdd = 1.0, 0.0
    for v in curve:
        mx = max(mx, v)
        mdd = min(mdd, (v / mx - 1) * 100)
    m_, s_ = sum(rets) / len(rets), (st.stdev(rets) if len(rets) > 2 else 0)
    return {"total": (val - 1) * 100, "mdd": mdd, "n": len(rets), "cash": cash,
            "sharpe": (m_ / s_ * math.sqrt(246 / hold)) if s_ else 0.0}


def bench(idx, dates, with_div=True):
    ds = [d for d in dates if d in idx]
    cl = [idx[d]["close"] for d in ds]
    yrs = len(ds) / 246
    tot = (cl[-1] / cl[0] - 1) * 100
    if with_div:
        tot = ((1 + tot / 100) * (1 + DIV_Y / 100) ** yrs - 1) * 100
    mx, mdd = cl[0], 0.0
    for c in cl:
        mx = max(mx, c)
        mdd = min(mdd, (c / mx - 1) * 100)
    return {"total": tot, "mdd": mdd, "yrs": yrs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flows", default="state/flows.pkl")
    ap.add_argument("--lag", type=int, default=1)
    a = ap.parse_args()
    flows = pickle.load(open(a.flows, "rb"))
    meta = json.load(open("state/deep_px.pkl.meta.json"))
    ser = build(flows, meta)
    idx = index_series("KOSPI")
    alld = sorted({d for S in ser.values() for d in S["days"]})
    alld = [d for d in alld if d in idx]
    n_ok = max(sum(1 for S in ser.values() if d in S["days"]) for d in alld[::20])
    alld = [d for d in alld if sum(1 for S in ser.values() if d in S["days"]) > n_ok * 0.8]
    b = bench(idx, alld)
    bp = bench(idx, alld, with_div=False)
    print(f"종목 {len(ser)} · 거래일 {len(alld)} ({alld[0]}~{alld[-1]}, {b['yrs']:.2f}년) · lag {a.lag}")
    print(f"벤치마크: KOSPI 가격 {bp['total']:+.1f}% / **배당포함 {b['total']:+.1f}%** (MDD {b['mdd']:.0f}%)\n")
    print("=" * 96)
    print(f"{'전략':<44}{'총수익':>11}{'MDD':>8}{'샤프':>8}{'현금':>6}")
    print("=" * 96)
    for uni in (100, 200):
        for hold in (5, 10, 20):
            for wi, wm, lab in ((0.0, 1.0, "모멘텀만"), (1.0, 0.0, "기관역방향만"),
                                (1.0, 1.0, "모멘텀+기관역방향")):
                for trend in (None, 100):
                    r = run(ser, idx, alld, uni, 20, hold, a.lag, wi, wm, trend)
                    if not r:
                        continue
                    name = f"{lab} 상위{uni} {hold}일" + (f" +MA{trend}" if trend else "")
                    star = " ★" if r["total"] > b["total"] and r["mdd"] > b["mdd"] else (
                        " ○" if r["total"] > b["total"] else "")
                    print(f"{name:<44}{r['total']:>+10.1f}%{r['mdd']:>7.0f}%{r['sharpe']:>8.2f}"
                          f"{r['cash']:>6}{star}")
        print("-" * 96)
    print("★ = KOSPI(배당포함)를 수익·낙폭 둘 다 개선   ○ = 수익만 개선")


if __name__ == "__main__":
    main()
