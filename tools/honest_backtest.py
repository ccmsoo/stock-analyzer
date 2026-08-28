"""정직한 백테스트 프로토콜 — 오늘 데인 세 가지를 구조적으로 막는다.

2026-08-28에 세 번 속았다. 그때마다 결과가 뒤집혔다:
  1. **유니버스 생존편향** — '오늘 시총 상위'로 과거를 백테스트 (동일가중 +94.9% → +2.3%)
  2. **리밸런싱 시작일 운** — offset만 바꿔도 지수대비 −52%p ~ +87%p (중앙값 +5.6%p)
  3. **단일 분할 홀드아웃** — 어디서 자르냐에 따라 결론이 갈림

그래서 이 모듈은 **단일 숫자를 반환하지 않는다.** 항상 offset 전체의 분포를 준다.
숫자 하나를 인용하고 싶어지면, 그게 바로 속고 있다는 신호다.

사용:
    from tools.honest_backtest import sweep, report
    res = sweep(pick_fn, dates, hold=10)   # pick_fn(date, hold) -> [기간수익률 %]
    report(res, bench_rets)
"""
from __future__ import annotations

import math
import statistics as st

DIV_Y = 1.8      # KOSPI 배당수익률 — 가격지수 벤치마크에 반드시 더한다
CASH_Y = 3.0     # 현금 구간 이자


def _curve(rets, lev=1.0):
    v, curve = 1.0, []
    for r in rets:
        v *= (1 + (r / 100) * lev)
        if v <= 0:
            return None, None
        curve.append(v)
    return v, curve


def stats(rets, hold, lev=1.0):
    v, curve = _curve(rets, lev)
    if v is None or len(curve) < 5:
        return None
    mx, mdd = 1.0, 0.0
    for x in curve:
        mx = max(mx, x)
        mdd = min(mdd, (x / mx - 1) * 100)
    m = sum(rets) / len(rets) * lev
    s = (st.stdev(rets) * lev) if len(rets) > 2 else 0.0
    ann = math.sqrt(246 / hold)
    return {"total": (v - 1) * 100, "mdd": mdd, "n": len(rets),
            "vol": s * ann, "sharpe": (m / s * ann) if s else 0.0}


def sweep(period_returns, dates, hold):
    """시작일 offset 0..hold-1 전부 돌린다.

    period_returns(dates_slice) -> 기간수익률 리스트(%). 호출자가 전략을 구현하고,
    이 함수는 '시작일 운'을 제거한 분포만 만든다."""
    out = []
    for off in range(hold):
        r = period_returns(dates[off::hold])
        if not r:
            continue
        s = stats(r, hold)
        if s:
            s["offset"] = off
            out.append(s)
    return out


def bench_periods(idx, dates, hold, offset=0, with_div=True):
    ds = [d for d in dates if d in idx]
    pd = ((1 + DIV_Y / 100) ** (hold / 246) - 1) * 100 if with_div else 0.0
    return [(idx[ds[j + hold]]["close"] / idx[ds[j]]["close"] - 1) * 100 + pd
            for j in range(offset, len(ds) - hold, hold)]


def report(res, bench_res, label="전략"):
    """offset 분포를 표로. **중앙값과 범위만 신뢰할 것.**"""
    if not res or not bench_res:
        print("표본 부족")
        return None
    tot = sorted(x["total"] for x in res)
    mdd = sorted(x["mdd"] for x in res)
    shp = sorted(x["sharpe"] for x in res)
    btot = sorted(x["total"] for x in bench_res)
    ex = sorted(a - b for a, b in zip([x["total"] for x in res],
                                      [x["total"] for x in bench_res]))
    n = len(tot)
    med = lambda v: v[len(v) // 2]
    win = sum(1 for e in ex if e > 0)
    print(f"  {label:<26} 수익 중앙 {med(tot):>+8.1f}%  (범위 {tot[0]:>+7.1f} ~ {tot[-1]:>+7.1f})")
    print(f"  {'':<26} MDD  중앙 {med(mdd):>+8.1f}%  샤프 중앙 {med(shp):>5.2f}")
    print(f"  {'벤치마크(배당포함)':<26} 수익 중앙 {med(btot):>+8.1f}%")
    print(f"  {'→ 초과수익':<26} 중앙 {med(ex):>+8.1f}%p  (범위 {ex[0]:>+7.1f} ~ {ex[-1]:>+7.1f})"
          f"  이긴 offset {win}/{n}")
    verdict = ("✅ 우위 (중앙값 양수 + 8할 이상 offset에서 승)"
               if med(ex) > 0 and win >= n * 0.8 else
               "❌ 미확정 (시작일 운으로 설명 가능)")
    print(f"  {'판정':<26} {verdict}")
    return {"excess_median": med(ex), "win": win, "n": n,
            "total_median": med(tot), "mdd_median": med(mdd)}
