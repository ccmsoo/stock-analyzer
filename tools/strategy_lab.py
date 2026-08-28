"""전략 탐색 랩 — "유의한 신호"가 아니라 "지수를 이기는 운용 규칙"이 있는지 잰다.

hypothesis_lab이 답하는 질문:  이 신호는 같은 날 평균보다 나은가? (상대)
이 파일이 답하는 질문:        이 규칙으로 실제 돈이 늘었는가? (절대, 낙폭 포함)

둘은 다르다. 2026-08-20 검증에서 mom_20은 동일가중 대비 +42%p로 유의했지만
절대수익은 KOSPI(+178%)에 크게 못 미쳤고 MDD는 −42%였다. 상대우위는 수익이 아니다.

**과적합 방지 규칙 (어기면 이 파일은 쓸모없어진다)**
  · 그리드 탐색은 **앞 60% 기간에서만**. 승자를 뒤 40%에서 딱 한 번 확인한다.
  · 후보 전체의 검증구간 성적 **분포**를 같이 본다. 승자가 분포의 꼭대기에 불과하면
    그건 발견이 아니라 노이즈다. 상위 후보와 중앙값의 차이가 의미 있어야 한다.
  · 벤치마크는 **KOSPI 단순보유**. 이걸 못 이기면 전략이 아니다.

사용:
    venv/bin/python -m tools.strategy_lab --px state/deep_px.pkl
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics as st
import urllib.request

COST = 0.4          # 왕복 거래비용 % (리밸런싱 1회마다 차감)
# 그날 후보가 유니버스 정원의 절반에 못 미치면(데이터 결손) 리밸런싱을 건너뛴다.
# 고정 하한(80)을 쓰면 '시총 상위 50' 같은 좁은 유니버스가 통째로 탈락해버린다.
MIN_FILL = 0.5


# ---------------------------------------------------------------- 신호

def _ret(c, n):
    return (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n and c[-1 - n] else None


def _std(c, n):
    if len(c) < n + 1:
        return None
    r = [(c[-i] / c[-i - 1] - 1) * 100 for i in range(1, n + 1) if c[-i - 1]]
    return st.stdev(r) if len(r) > 2 else None


SIGNALS = {
    "mom_20":   lambda c, v: _ret(c, 20),
    "mom_60":   lambda c, v: _ret(c, 60),
    "mom_120":  lambda c, v: _ret(c, 120),
    # 모멘텀을 변동성으로 나눈 것 — 같은 상승이면 덜 흔들린 쪽. 낙폭 개선을 노린다.
    "mom20_iv": lambda c, v: (_ret(c, 20) / _std(c, 20)
                              if _ret(c, 20) is not None and _std(c, 20) else None),
    "mom60_iv": lambda c, v: (_ret(c, 60) / _std(c, 60)
                              if _ret(c, 60) is not None and _std(c, 60) else None),
    # 장기추세는 살아있고 단기만 눌린 것 (모멘텀 + 단기 되돌림)
    "mom60_dip": lambda c, v: (_ret(c, 60) - 2 * _ret(c, 5)
                               if _ret(c, 60) is not None and _ret(c, 5) is not None else None),
    "none":     lambda c, v: 0.0,     # 신호 없음 = 유니버스 동일가중 (대조군)
}


# ---------------------------------------------------------------- 데이터

def index_series(code: str = "KOSPI") -> dict:
    d = {}
    # 150페이지 ≈ 3,000거래일(12년). 2015 차이나쇼크·2018 하락·2020 코로나·2022 하락·
    # 2025-26 급등락이 모두 들어간다. 레짐이 많아야 '그 장세용 규칙'을 걸러낼 수 있다.
    for p in range(1, 151):
        u = f"https://m.stock.naver.com/api/index/{code}/price?pageSize=20&page={p}"
        r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        try:
            rows = json.loads(urllib.request.urlopen(r, timeout=8).read())
            if not rows:
                break
            for row in rows:
                d[row["localTradedAt"].replace("-", "")] = {
                    "open": float(row["openPrice"].replace(",", "")),
                    "close": float(row["closePrice"].replace(",", "")),
                }
        except Exception:
            break
    return d


def size_rank(meta: dict) -> dict:
    """meta 삽입 순서가 곧 시총 순위 (fetch_universe가 시총순으로 넣는다)."""
    rank = {}
    for mk in ("KOSPI", "KOSDAQ"):
        for i, c in enumerate([c for c, v in meta.items() if v.get("market") == mk]):
            rank[c] = i
    return rank


# ---------------------------------------------------------------- 시뮬

def simulate(px, rank, idx, cfg, dates, min_periods: int = 8) -> dict | None:
    """H거래일마다 리밸런싱. 익일 시초 진입, H일 뒤 종가 청산, 왕복비용 차감.

    regime='ma20'이면 신호일의 지수가 20일선 아래일 때 현금(= 그 구간 수익 0)."""
    sig = SIGNALS[cfg["signal"]]
    lo, hi = cfg["universe"]
    H = cfg["hold"]
    ser = {t: sorted(v) for t, v in px.items()}
    ik = sorted(idx)
    ma = {}
    for i, k in enumerate(ik):
        if i >= 20:
            ma[k] = sum(idx[x]["close"] for x in ik[i - 19:i + 1]) / 20
    val, curve, periods, cash_periods = 1.0, [], [], 0
    for d in dates[::H]:
        if cfg["regime"] == "ma20" and d in ma and idx[d]["close"] < ma[d]:
            cash_periods += 1
            curve.append((d, val))
            continue
        cand = []
        for t, ds in ser.items():
            r = rank.get(t, 9999)
            if not (lo <= r < hi) or d not in px[t]:
                continue
            i = ds.index(d)
            if i < 130 or i + H >= len(ds):
                continue
            c = [px[t][x]["close"] for x in ds[:i + 1]]
            v = [px[t][x]["volume"] for x in ds[:i + 1]]
            try:
                s = sig(c, v)
            except Exception:
                s = None
            if s is None:
                continue
            e = px[t][ds[i + 1]]["open"]
            if not e:
                continue
            cand.append((s, (px[t][ds[i + H]]["close"] / e - 1) * 100 - COST))
        if len(cand) < max(cfg["top"] * 2, int((hi - lo) * 2 * MIN_FILL)):
            continue
        cand.sort(key=lambda z: -z[0])
        sel = [r for _s, r in cand[:cfg["top"]]]
        pr = sum(sel) / len(sel) / 100
        val *= (1 + pr)
        periods.append(pr * 100)
        curve.append((d, val))
    if len(periods) < min_periods:
        return None
    mx, mdd = 1.0, 0.0
    for _d, v in curve:
        mx = max(mx, v)
        mdd = min(mdd, (v / mx - 1) * 100)
    m = sum(periods) / len(periods)
    sd = st.stdev(periods) if len(periods) > 2 else 0.0
    per_year = 246 / H
    return {"total": (val - 1) * 100, "mdd": mdd,
            "sharpe": (m / sd * math.sqrt(per_year)) if sd else 0.0,
            "n": len(periods), "cash": cash_periods,
            "winrate": 100 * sum(1 for p in periods if p > 0) / len(periods)}


def bench_timed(idx, dates, hold: int = 10) -> dict:
    """지수만 타이밍 — 지수가 20일선 위일 때만 지수 보유, 아래면 현금.

    이게 왜 중요한가: 종목 선택 장치(신호·유니버스·상위N) 전체가 이걸 못 이기면
    그 장치는 존재 이유가 없다. 지수 ETF 하나로 같은 성과가 나오기 때문이다."""
    ds = [d for d in dates if d in idx]
    ik = sorted(idx)
    ma = {}
    for i, k in enumerate(ik):
        if i >= 20:
            ma[k] = sum(idx[x]["close"] for x in ik[i - 19:i + 1]) / 20
    val, curve, periods = 1.0, [], []
    for j in range(0, len(ds) - hold, hold):
        d, nd = ds[j], ds[j + hold]
        if d in ma and idx[d]["close"] < ma[d]:
            curve.append(val)
            continue
        pr = idx[nd]["close"] / idx[d]["close"] - 1
        val *= (1 + pr)
        periods.append(pr * 100)
        curve.append(val)
    mx, mdd = 1.0, 0.0
    for v in curve:
        mx = max(mx, v)
        mdd = min(mdd, (v / mx - 1) * 100)
    m = sum(periods) / len(periods) if periods else 0
    sd = st.stdev(periods) if len(periods) > 2 else 0
    return {"total": (val - 1) * 100, "mdd": mdd,
            "sharpe": (m / sd * math.sqrt(246 / hold)) if sd else 0.0}


def bench(idx, dates) -> dict:
    ds = [d for d in dates if d in idx]
    tot = (idx[ds[-1]]["close"] / idx[ds[0]]["open"] - 1) * 100
    mx, mdd = idx[ds[0]]["close"], 0.0
    for d in ds:
        mx = max(mx, idx[d]["close"])
        mdd = min(mdd, (idx[d]["close"] / mx - 1) * 100)
    return {"total": tot, "mdd": mdd}


# 레짐 구간 — 한 장세가 총수익을 지배할 때, 총수익 랭킹은 실력을 못 가린다.
# "6개 구간 중 몇 개에서 지수를 이겼는가"가 훨씬 잘 가른다.
REGIMES = [("20211102", "20220930", "2022 하락장"),
           ("20221001", "20230731", "2023 회복"),
           ("20230801", "20240731", "2024 횡보"),
           ("20240801", "20250430", "2025초 정체"),
           ("20250501", "20260622", "대세상승"),
           ("20260623", "20261231", "고점후 급락")]


def by_regime(px, rank, idx, cfg, alld):
    """구간별 성적 + 각 구간에서 KOSPI 단순보유를 이겼는지."""
    out = []
    for a, b, lab in REGIMES:
        ds = [d for d in alld if a <= d <= b]
        if len(ds) < cfg["hold"] * 4:
            out.append(None)
            continue
        r = simulate(px, rank, idx, cfg, ds, min_periods=3)
        if not r:
            out.append(None)
            continue
        bh = bench(idx, ds)
        out.append({"lab": lab, "ret": r["total"], "mdd": r["mdd"],
                    "bh": bh["total"], "win": r["total"] > bh["total"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px", default="state/deep_px.pkl")
    ap.add_argument("--split", type=float, default=0.6)
    a = ap.parse_args()

    px = {t: v for t, v in pickle.load(open(a.px, "rb")).items() if len(v) > 200}
    meta = json.load(open(a.px + ".meta.json"))
    rank = size_rank(meta)
    idx = index_series("KOSPI")
    alld = sorted({d for v in px.values() for d in v})
    alld = [d for d in alld if d in idx][130:]
    cut = int(len(alld) * a.split)
    tr, te = alld[:cut], alld[cut:]
    print(f"종목 {len(px)} · 거래일 {len(alld)} ({alld[0]}~{alld[-1]})")
    print(f"  탐색 {tr[0]}~{tr[-1]} ({len(tr)}일) / 검증 {te[0]}~{te[-1]} ({len(te)}일)")
    b_tr, b_te = bench(idx, tr), bench(idx, te)
    t_tr, t_te = bench_timed(idx, tr), bench_timed(idx, te)
    print(f"  [벤치1] KOSPI 단순보유 — 탐색 {b_tr['total']:+8.1f}% (MDD {b_tr['mdd']:>6.1f}%) / "
          f"검증 {b_te['total']:+8.1f}% (MDD {b_te['mdd']:>6.1f}%)")
    print(f"  [벤치2] KOSPI MA20 타이밍 — 탐색 {t_tr['total']:+8.1f}% (MDD {t_tr['mdd']:>6.1f}%) / "
          f"검증 {t_te['total']:+8.1f}% (MDD {t_te['mdd']:>6.1f}%)  ← 종목선택이 이걸 못 이기면 무의미")

    grid = []
    for sg in SIGNALS:
        for uni in ((0, 50), (0, 100), (0, 200), (100, 350), (0, 350)):
            for top in (10, 20, 40):
                for hold in (5, 10, 20):
                    for rg in (None, "ma20"):
                        grid.append({"signal": sg, "universe": uni, "top": top,
                                     "hold": hold, "regime": rg})
    print(f"  후보 {len(grid)}개 — 탐색구간에서만 순위를 매기고, 승자만 검증구간에서 확인\n")

    res = []
    for i, cfg in enumerate(grid):
        r = simulate(px, rank, idx, cfg, tr)
        if r:
            res.append((cfg, r))
        if i % 100 == 0:
            print(f"    {i}/{len(grid)}...", flush=True)
    # 탐색구간 기준 정렬: 지수 초과수익을 MDD로 나눈 값 (낙폭당 초과수익)
    def sc(r):
        return (r["total"] - b_tr["total"]) / max(abs(r["mdd"]), 5)
    res.sort(key=lambda x: -sc(x[1]))
    print(f"\n{'='*104}")
    print("탐색구간 상위 12 (정렬: 지수초과 ÷ MDD) — 검증구간은 참고용으로만 함께 표시")
    print(f"{'='*104}")
    print(f"{'신호':<11}{'유니버스':<11}{'상위':>4}{'보유':>4}{'레짐':>6} | "
          f"{'탐색수익':>9}{'MDD':>7}{'샤프':>6} | {'검증수익':>9}{'MDD':>7}{'샤프':>6}{'vs지수':>8}{'회':>4}")
    print("-" * 104)
    tests = []
    for cfg, r in res[:12]:
        t = simulate(px, rank, idx, cfg, te, min_periods=4)
        if not t:
            continue
        tests.append((cfg, r, t))
        u = f"{cfg['universe'][0]}-{cfg['universe'][1]}"
        print(f"{cfg['signal']:<11}{u:<11}{cfg['top']:>4}{cfg['hold']:>4}"
              f"{(cfg['regime'] or '-'):>6} | {r['total']:>+8.1f}%{r['mdd']:>6.0f}%{r['sharpe']:>6.2f} | "
              f"{t['total']:>+8.1f}%{t['mdd']:>6.0f}%{t['sharpe']:>6.2f}{t['total']-b_te['total']:>+7.1f}%{t['n']:>4}")
    # 노이즈 판별: 전체 후보의 검증 성적 분포와 비교
    allt = [simulate(px, rank, idx, cfg, te, min_periods=4) for cfg, _r in res]
    allt = [x["total"] for x in allt if x]
    allt.sort()
    print("-" * 104)
    print(f"전체 후보 {len(allt)}개의 검증구간 수익 분포: "
          f"하위25% {allt[len(allt)//4]:+.0f}% · 중앙 {allt[len(allt)//2]:+.0f}% · "
          f"상위25% {allt[3*len(allt)//4]:+.0f}% · 최고 {allt[-1]:+.0f}%")
    print(f"KOSPI 검증구간 {b_te['total']:+.1f}%")
    beat = sum(1 for x in allt if x > b_te["total"])
    beat2 = sum(1 for x in allt if x > t_te["total"])
    print(f"→ KOSPI 단순보유를 이긴 후보: {beat}/{len(allt)} ({100*beat/max(len(allt),1):.0f}%)")
    print(f"→ KOSPI MA20 타이밍을 이긴 후보: {beat2}/{len(allt)} ({100*beat2/max(len(allt),1):.0f}%)")
    print(f"\n{'='*104}")
    print("레짐 분해 — 탐색 상위 6개가 '언제' 벌었는가 (구간수익 / KOSPI 구간수익)")
    print(f"{'='*104}")
    hdr = "".join(f"{lab[:9]:>13}" for _a, _b, lab in REGIMES)
    print(f"{'전략':<30}{hdr}{'승':>5}")
    print("-" * 104)
    for cfg, _r, _t in tests[:6]:
        rows = by_regime(px, rank, idx, cfg, alld)
        u = f"{cfg['universe'][0]}-{cfg['universe'][1]}"
        name = f"{cfg['signal']}/{u}/top{cfg['top']}/{cfg['hold']}d/{cfg['regime'] or '-'}"
        cells, wins = "", 0
        for x in rows:
            if not x:
                cells += f"{'-':>13}"
            else:
                wins += 1 if x["win"] else 0
                cells += f"{x['ret']:>+6.0f}/{x['bh']:>+5.0f}"
        print(f"{name:<30}{cells}{wins:>4}/6")
    kb = "".join(f"{bench(idx, [d for d in alld if a <= d <= b])['total']:>+13.0f}"
                 if len([d for d in alld if a <= d <= b]) > 20 else f"{'-':>13}"
                 for a, b, _l in REGIMES)
    print(f"{'(KOSPI 단순보유)':<30}{kb}")
    print("\n→ 대세상승 한 칸에서만 크게 벌고 나머지에서 지는 전략은 '실력'이 아니라 '베타'다.")

    if tests:
        win = tests[0]
        print(f"\n탐색 1위의 검증 성적: {win[2]['total']:+.1f}% "
              f"(전체 후보 중 상위 {100 - 100*sum(1 for x in allt if x < win[2]['total'])/len(allt):.0f}%)")
        print("  → 탐색 1위가 검증에서도 상위권이어야 실력이다. 중앙값 근처면 그냥 운이었다.")


if __name__ == "__main__":
    main()
