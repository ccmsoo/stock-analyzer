"""지수 타이밍 랩 — 종목을 고르지 말고, 지수에 있을 때/없을 때만 정하면 어떤가.

왜 이걸 보게 됐나 (2026-08-20):
  종목선택 전략의 검증구간 성적을 뜯어보니, 번 돈의 정체가 '종목을 잘 골라서'가 아니라
  '급락장에 현금으로 빠져서'였다. 같은 구간에서 지수 MA20 타이밍만 한 쪽이 +27.8%(MDD 0%)로
  종목선택(+18.5%, MDD −7.1%)을 이겼다. 종목선택 장치 전체가 ETF 한 종목에 진 것이다.

그래서 질문을 바꾼다: **추세 규칙 하나로 지수를 언제 들고 있을지만 정하면, 단순보유를
이기는가?** 이건 종목 데이터가 필요 없고, 거래비용도 훨씬 싸고(ETF), 실행도 쉽다.

정직성 규칙 (앞선 실패에서 배운 것 그대로):
  · 규칙마다 **레짐 6구간 전부**에서 성적을 본다. 한 구간만 잘하면 베타지 실력이 아니다.
  · 신호는 **종가로 판정하고 다음날 시가에 체결**한다. 종가 체결은 실현 불가능.
  · 거래비용 왕복 0.2%(ETF) — 매매 횟수가 많은 규칙에 제대로 불리하게 작동해야 한다.
  · 규칙을 여러 개 던지므로 승자는 **홀드아웃**에서 다시 확인한다.

사용:
    venv/bin/python -m tools.timing_lab
    venv/bin/python -m tools.timing_lab --index KOSDAQ --cost 0.2
"""
from __future__ import annotations

import argparse
import math
import statistics as st

from tools.strategy_lab import index_series

REGIMES = [("20211102", "20220930", "2022 하락"),
           ("20221001", "20230731", "2023 회복"),
           ("20230801", "20240731", "2024 횡보"),
           ("20240801", "20250430", "2025초 정체"),
           ("20250501", "20260622", "대세상승"),
           ("20260623", "20261231", "고점후 급락")]


# ---------------------------------------------------------------- 규칙

def _ma(cl, i, n):
    return sum(cl[i - n + 1:i + 1]) / n if i >= n - 1 else None


def rules():
    """각 규칙: (이름, 함수(closes, i) -> True면 다음날 지수 보유)"""
    r = {}
    for n in (5, 10, 20, 60, 120, 200):
        r[f"MA{n}"] = (lambda n: lambda cl, i: (_ma(cl, i, n) is not None
                                                and cl[i] > _ma(cl, i, n)))(n)
    for f, s in ((5, 20), (10, 60), (20, 60), (20, 120), (50, 200)):
        r[f"MA{f}>{s}"] = (lambda f, s: lambda cl, i: (_ma(cl, i, f) is not None
                                                       and _ma(cl, i, s) is not None
                                                       and _ma(cl, i, f) > _ma(cl, i, s)))(f, s)
    for n in (60, 120, 246):
        r[f"수익률{n}일>0"] = (lambda n: lambda cl, i: (i >= n and cl[i] > cl[i - n]))(n)
    r["항상보유"] = lambda cl, i: True     # 대조군
    return r


# ---------------------------------------------------------------- 시뮬

def run(idx, rule, dates, cost: float) -> dict | None:
    """신호는 당일 종가로 판정, 체결은 다음날 시가. 포지션이 바뀔 때만 비용을 문다."""
    ds = [d for d in dates if d in idx]
    cl = [idx[d]["close"] for d in ds]
    val, pos, trades, rets = 1.0, 0, 0, []
    curve = []
    for i in range(len(ds) - 1):
        want = 1 if rule(cl, i) else 0
        # i일 종가 신호 → i+1일 시가 체결 → i+1일 종가까지 보유분 반영
        o, c = idx[ds[i + 1]]["open"], idx[ds[i + 1]]["close"]
        if want != pos:
            trades += 1
            val *= (1 - cost / 100 / 2)      # 편도 비용
        # 갈아탄 날은 시가부터, 유지한 날은 전일 종가부터
        base = o if want != pos else cl[i]
        pr = (c / base - 1) if want else 0.0
        val *= (1 + pr)
        rets.append(pr * 100)
        pos = want
        curve.append(val)
    if len(curve) < 30:
        return None
    mx, mdd = 1.0, 0.0
    for v in curve:
        mx = max(mx, v)
        mdd = min(mdd, (v / mx - 1) * 100)
    m = sum(rets) / len(rets)
    sd = st.stdev(rets) if len(rets) > 2 else 0.0
    return {"total": (val - 1) * 100, "mdd": mdd, "trades": trades,
            "sharpe": (m / sd * math.sqrt(246)) if sd else 0.0,
            "exposure": 100 * sum(1 for x in rets if x != 0) / len(rets)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="KOSPI")
    ap.add_argument("--cost", type=float, default=0.2, help="왕복 거래비용 % (ETF 기준)")
    ap.add_argument("--split", type=float, default=0.6)
    a = ap.parse_args()

    idx = index_series(a.index)
    alld = sorted(idx)
    cut = int(len(alld) * a.split)
    tr, te = alld[:cut], alld[cut:]
    R = rules()
    print(f"{a.index} {alld[0]}~{alld[-1]} · {len(alld)}거래일 · 왕복비용 {a.cost}%")
    print(f"  탐색 {tr[0]}~{tr[-1]} ({len(tr)}일) / 검증 {te[0]}~{te[-1]} ({len(te)}일)")
    print()
    print("=" * 108)
    print(f"{'규칙':<12} | {'탐색: 수익':>10}{'MDD':>7}{'샤프':>6}{'매매':>5} | "
          f"{'검증: 수익':>10}{'MDD':>7}{'샤프':>6}{'매매':>5} | {'보유율':>6}")
    print("-" * 108)
    rows = []
    for name, fn in R.items():
        x, y = run(idx, fn, tr, a.cost), run(idx, fn, te, a.cost)
        if not x or not y:
            continue
        rows.append((name, fn, x, y))
        print(f"{name:<12} | {x['total']:>+9.1f}%{x['mdd']:>6.0f}%{x['sharpe']:>6.2f}{x['trades']:>5} | "
              f"{y['total']:>+9.1f}%{y['mdd']:>6.0f}%{y['sharpe']:>6.2f}{y['trades']:>5} | {y['exposure']:>5.0f}%")
    print("-" * 108)

    print()
    print("=" * 108)
    print("레짐 분해 — 각 규칙이 6구간에서 '항상보유' 대비 어땠는가 (구간수익 %)")
    print("=" * 108)
    hdr = "".join(f"{lab:>13}" for _a, _b, lab in REGIMES)
    print(f"{'규칙':<12}{hdr}{'승':>6}")
    print("-" * 108)
    base = {}
    for a2, b2, lab in REGIMES:
        ds = [d for d in alld if a2 <= d <= b2]
        r = run(idx, R["항상보유"], ds, a.cost)
        base[lab] = r["total"] if r else None
    for name, fn, _x, _y in rows:
        cells, wins, cnt = "", 0, 0
        for a2, b2, lab in REGIMES:
            ds = [d for d in alld if a2 <= d <= b2]
            r = run(idx, fn, ds, a.cost)
            if not r or base[lab] is None:
                cells += f"{'-':>13}"
                continue
            cnt += 1
            if r["total"] > base[lab]:
                wins += 1
            cells += f"{r['total']:>+12.1f}%"
        print(f"{name:<12}{cells}{wins:>4}/{cnt}")
    print("-" * 108)
    print(f"{'(항상보유)':<12}" + "".join(
        f"{base[lab]:>+12.1f}%" if base[lab] is not None else f"{'-':>13}"
        for _a, _b, lab in REGIMES))
    print()
    print("판정 기준: ① 검증구간에서 항상보유보다 낫고 ② 6구간 중 4구간 이상 이기고")
    print("          ③ 하락장(2022·급락)에서 낙폭을 실제로 줄여야 한다. 셋 다여야 한다.")


if __name__ == "__main__":
    main()
