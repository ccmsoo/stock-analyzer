"""시점기준 유니버스 백테스트 (구 final_test.py의 생존편향 수정판, 원본은 삭제됨)

생존편향 제거판 — 유니버스를 '그 시점의 거래대금'으로 매일 다시 뽑는다.

이전 검정의 치명적 약점: 유니버스가 "**오늘** 시총 상위 350"이었다. 5년 전 시점에서
그건 미래 정보다(그때는 어떤 종목이 오늘 대형주가 될지 알 수 없다).

수정: 각 리밸런싱 날짜마다 **직전 60거래일 평균 거래대금** 상위 N종목을 유니버스로 쓴다.
거래대금은 캔들(종가×거래량)에서 그 시점 정보만으로 계산되므로 미래를 보지 않는다.

남은 편향: 종목 풀 자체가 '오늘 존재하는 700종목'이라, 상장폐지된 종목은 빠져있다.
이건 데이터 소스의 한계이므로 결과 해석 시 명시할 것.

사용:  venv/bin/python -m tools.final_test2
"""
from __future__ import annotations

import json
import math
import pickle
import statistics as st

from tools.strategy_lab import index_series

COST = 0.4


def build(px):
    ser = {t: sorted(v) for t, v in px.items()}
    return ser


def run(px, ser, idx, dates, uni_n, top, hold, trend=None, mode="mom",
        mom_n=20, cost=COST):
    """mode: 'mom'=20일 모멘텀 상위, 'ew'=유니버스 동일가중(신호 없음)."""
    ik = sorted(idx)
    ma = {}
    if trend:
        for i, k in enumerate(ik):
            if i >= trend:
                ma[k] = sum(idx[x]["close"] for x in ik[i - trend + 1:i + 1]) / trend
    val, curve, rets, cash = 1.0, [], [], 0
    for d in dates[::hold]:
        if trend and d in ma and idx[d]["close"] < ma[d]:
            cash += 1
            curve.append(val)
            continue
        pool = []
        for t, ds in ser.items():
            if d not in px[t]:
                continue
            i = ds.index(d)
            if i < max(mom_n, 60) + 1 or i + hold >= len(ds):
                continue
            # 시점 유니버스 기준: 직전 60거래일 평균 거래대금 (미래 정보 없음)
            tv = sum(px[t][x]["close"] * px[t][x]["volume"] for x in ds[i - 59:i + 1]) / 60
            e = px[t][ds[i + 1]]["open"]
            if not e:
                continue
            c0 = px[t][ds[i - mom_n]]["close"]
            if not c0:
                continue
            m = (px[t][ds[i]]["close"] / c0 - 1) * 100
            r = (px[t][ds[i + hold]]["close"] / e - 1) * 100 - cost
            pool.append((tv, m, r))
        if len(pool) < uni_n:
            continue
        pool.sort(key=lambda z: -z[0])          # 거래대금 상위 = 그 시점의 대형/유동주
        uni = pool[:uni_n]
        if mode == "ew":
            sel = [r for _tv, _m, r in uni]
        else:
            uni.sort(key=lambda z: -z[1])
            sel = [r for _tv, _m, r in uni[:top]]
        if not sel:
            continue
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
            "sharpe": (m_ / s_ * math.sqrt(246 / hold)) if s_ else 0.0,
            "rets": rets}


def idxsim(idx, dates, hold=10, trend=None):
    ik = sorted(idx)
    ma = {}
    if trend:
        for i, k in enumerate(ik):
            if i >= trend:
                ma[k] = sum(idx[x]["close"] for x in ik[i - trend + 1:i + 1]) / trend
    ds = [d for d in dates if d in idx]
    val, curve, rets = 1.0, [], []
    for j in range(0, len(ds) - hold, hold):
        d, nd = ds[j], ds[j + hold]
        if trend and d in ma and idx[d]["close"] < ma[d]:
            curve.append(val)
            continue
        pr = idx[nd]["close"] / idx[d]["close"] - 1
        val *= (1 + pr)
        rets.append(pr * 100)
        curve.append(val)
    mx, mdd = 1.0, 0.0
    for v in curve:
        mx = max(mx, v)
        mdd = min(mdd, (v / mx - 1) * 100)
    m_, s_ = sum(rets) / len(rets), (st.stdev(rets) if len(rets) > 2 else 0)
    return {"total": (val - 1) * 100, "mdd": mdd,
            "sharpe": (m_ / s_ * math.sqrt(246 / hold)) if s_ else 0.0}


def main():
    from tools.universe_filter import filter_meta
    meta = filter_meta(json.load(open("state/deep_px.pkl.meta.json")))   # ETF/ETN 제외
    px = {t: v for t, v in pickle.load(open("state/deep_px.pkl", "rb")).items()
          if len(v) > 900 and t in meta}
    ser = build(px)
    idx = index_series("KOSPI")
    alld = sorted({d for v in px.values() for d in v})
    alld = [d for d in alld if d in idx]
    n_ok = max(sum(1 for v in px.values() if d in v) for d in alld[::20])
    alld = [d for d in alld if sum(1 for v in px.values() if d in v) > n_ok * 0.8]
    print(f"종목 {len(px)} · 거래일 {len(alld)} ({alld[0]}~{alld[-1]})")
    print("유니버스 = 각 시점 직전 60일 평균 거래대금 상위 N (미래정보 없음)\n")

    a = idxsim(idx, alld)
    print("=" * 92)
    print(f"{'전략':<46}{'총수익':>12}{'MDD':>8}{'샤프':>8}{'현금':>6}")
    print("=" * 92)
    print(f"{'A. KOSPI 단순보유':<46}{a['total']:>+11.1f}%{a['mdd']:>7.0f}%{a['sharpe']:>8.2f}{'-':>6}")
    b = idxsim(idx, alld, trend=100)
    print(f"{'B. KOSPI + MA100':<46}{b['total']:>+11.1f}%{b['mdd']:>7.0f}%{b['sharpe']:>8.2f}{'-':>6}")
    print("-" * 92)
    for un in (50, 100, 200):
        c = run(px, ser, idx, alld, un, 20, 10, mode="ew")
        d = run(px, ser, idx, alld, un, 20, 10, 100, mode="ew")
        e = run(px, ser, idx, alld, un, 20, 10)
        f = run(px, ser, idx, alld, un, 20, 10, 100)
        for lab, r in ((f"C. 거래대금상위{un} 동일가중", c),
                       (f"D. 거래대금상위{un} 동일가중 +MA100", d),
                       (f"E. 거래대금상위{un} mom20 top20", e),
                       (f"F. 거래대금상위{un} mom20 top20 +MA100", f)):
            if r:
                star = " ★" if r["total"] > a["total"] and r["mdd"] > a["mdd"] else ""
                print(f"{lab:<46}{r['total']:>+11.1f}%{r['mdd']:>7.0f}%{r['sharpe']:>8.2f}"
                      f"{r['cash']:>6}{star}")
        print("-" * 92)
    print("★ = KOSPI 단순보유를 수익·낙폭 둘 다 개선")


if __name__ == "__main__":
    main()
