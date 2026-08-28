"""가설 검증 랩 — 신호가 '같은 날 같은 시장의 평균'을 이기는지만 잰다.

2026-08-20 감사에서 배운 것:
  1. 지수 대비 알파는 증거가 아니다. 하락장에선 아무 중소형주나 사도 지수를 이긴다.
  2. 픽 수는 표본 수가 아니다. 같은 날 픽 40개는 사실상 표본 1개다.
  3. 가설을 여러 개 던지면 그중 하나는 우연히 통과한다.

그래서 이 랩의 규칙:
  · **벤치마크 없음.** 같은 (날짜, 시장) 안에서 상위그룹 − 그날 전체평균.
    지수가 오르든 빠지든 양쪽에서 상쇄되므로 레짐 중립이 구조적으로 보장된다.
  · **거래일 클러스터 t.** 유효 표본은 관측치 수가 아니라 거래일 수.
  · **다중검정 보정.** 가설 N개를 던졌으면 임계값도 N배 엄격하게(본페로니).
  · **홀드아웃.** 앞 60% 기간에서 찾고, 뒤 40%에서 확인. 둘 다 통과해야 살아남는다.
  · **단조성.** 진짜 팩터는 5분위가 단조롭게 정렬된다. 우연은 그렇지 않다.

사용:
    venv/bin/python -m tools.hypothesis_lab --panel state/panel.pkl
    venv/bin/python -m tools.hypothesis_lab --panel state/panel.pkl --hold 5 --top 20
"""
from __future__ import annotations

import argparse
import math
import pickle
import statistics as st
from collections import defaultdict

COST = 0.4      # 왕복 거래비용 % — 세금+수수료+슬리피지. 0%는 금지(감사 결론).
MIN_GROUP = 30  # 그날 그 시장에 이보다 적으면 횡단면 비교가 무의미


# ============================================================ 가설 등록소
#
# 각 가설은 과거만 보는 함수: f(c, v) -> float | None   (클수록 '산다')
#   c = 종가 리스트(과거→현재, 마지막이 신호일), v = 거래량 리스트
# 새 가설은 여기 한 줄 추가하면 자동으로 전 구간 검증에 들어간다.

def _ret(c, n):
    return (c[-1] / c[-1 - n] - 1) * 100 if len(c) > n and c[-1 - n] else None


def _std(c, n):
    if len(c) < n + 1:
        return None
    r = [(c[-i] / c[-i - 1] - 1) * 100 for i in range(1, n + 1) if c[-i - 1]]
    return st.stdev(r) if len(r) > 2 else None


HYPOTHESES = {
    # --- 추세 계열
    "mom_20":      lambda c, v: _ret(c, 20),                       # 20일 모멘텀
    "mom_60":      lambda c, v: _ret(c, 60),                       # 60일 모멘텀
    "near_high":   lambda c, v: (c[-1] / max(c[-120:]) * 100       # 120일 고점 근접(돌파)
                                 if len(c) >= 120 and max(c[-120:]) else None),
    # --- 역추세 계열
    "rev_1":       lambda c, v: -_ret(c, 1) if _ret(c, 1) is not None else None,
    "rev_5":       lambda c, v: -_ret(c, 5) if _ret(c, 5) is not None else None,
    "rev_20":      lambda c, v: -_ret(c, 20) if _ret(c, 20) is not None else None,
    "from_high":   lambda c, v: (-(c[-1] / max(c[-120:]) * 100)    # 고점 대비 낙폭 큰 순
                                 if len(c) >= 120 and max(c[-120:]) else None),
    # --- 거래량/관심 계열
    "vol_spike":   lambda c, v: (v[-1] / (sum(v[-21:-1]) / 20)     # 거래량 급증
                                 if len(v) >= 21 and sum(v[-21:-1]) else None),
    "dry_up":      lambda c, v: (-(v[-1] / (sum(v[-21:-1]) / 20))  # 거래량 고갈(조용한 축적)
                                 if len(v) >= 21 and sum(v[-21:-1]) else None),
    # --- 변동성 계열
    "lowvol":      lambda c, v: (-_std(c, 20) if _std(c, 20) is not None else None),
    "squeeze":     lambda c, v: (-(_std(c, 5) / _std(c, 60))       # 변동성 압축
                                 if _std(c, 5) is not None and _std(c, 60) else None),
    # --- 결합 (교과서적: 장기추세 살아있고 단기만 눌린 것)
    "trend_dip":   lambda c, v: (_ret(c, 60) - 2 * _ret(c, 5)
                                 if _ret(c, 60) is not None and _ret(c, 5) is not None else None),
}


# ============================================================ 패널

def build_panel(px: dict, meta: dict, holds: tuple, min_hist: int = 130) -> list:
    """(종목, 날짜)마다 신호일까지의 정보로 피처 계산 + 미래수익 기록.

    미래수익은 익일 시초 진입 · hold 거래일 보유 · 왕복비용 차감. 신호일 종가는
    피처에만 쓰고 진입가로 쓰지 않는다(종가 진입은 실현 불가능한 낙관 편향)."""
    panel = []
    for tk, series in px.items():
        days = sorted(series)
        if len(days) < min_hist + max(holds) + 2:
            continue
        closes = [series[d]["close"] for d in days]
        vols = [series[d]["volume"] for d in days]
        mk = (meta.get(tk) or {}).get("market")
        for i in range(min_hist, len(days) - max(holds) - 1):
            c, v = closes[:i + 1], vols[:i + 1]
            feats = {}
            for name, fn in HYPOTHESES.items():
                try:
                    feats[name] = fn(c, v)
                except Exception:
                    feats[name] = None
            entry = series[days[i + 1]]["open"]
            if not entry:
                continue
            fwd = {}
            for h in holds:
                xi = i + h
                if xi < len(days):
                    fwd[h] = (series[days[xi]]["close"] / entry - 1) * 100 - COST
            if not fwd:
                continue
            panel.append({"t": tk, "d": days[i], "m": mk, "f": feats, "r": fwd})
    return panel


def fetch_data(per_market: int, pages: int, cache: str) -> tuple[dict, dict]:
    """시총 상위 유니버스 + 네이버 깊은 일봉.

    워치리스트(signals.json)는 '과거에 급등했던 종목'으로 선별된 표본이라 팩터 검증에
    쓰면 선택편향이 들어간다. 그래서 여기선 시총 상위를 쓴다. (이것도 오늘 살아남은
    종목만 본다는 생존편향이 남아있다 — 결과 해석 시 반드시 감안할 것)"""
    import json as _json
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tools.fetch_universe import fetch_market
    from tools.fetch_history_naver import fetch_one

    meta_path = cache + ".meta.json"
    if os.path.exists(meta_path):
        meta = _json.load(open(meta_path))
    else:
        meta = {}
        for sosok, mk in ((0, "KOSPI"), (1, "KOSDAQ")):
            for code, name in fetch_market(sosok, per_market):
                meta[code] = {"name": name, "market": mk}
        _json.dump(meta, open(meta_path, "w"), ensure_ascii=False)
    print(f"   유니버스 {len(meta)}종목")

    px = pickle.load(open(cache, "rb")) if os.path.exists(cache) else {}
    todo = [c for c in meta if c not in px]
    if todo:
        print(f"   일봉 수집 {len(todo)}종목 × {pages}페이지(≈{pages * 10}거래일)... 수 분 걸린다")
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, f in enumerate(as_completed([ex.submit(fetch_one, c, pages) for c in todo])):
                c, ser = f.result()
                px[c] = ser
                if i % 50 == 0:
                    print(f"     {i}/{len(todo)}", flush=True)
                    pickle.dump(px, open(cache, "wb"))
        pickle.dump(px, open(cache, "wb"))
    return px, meta


# ============================================================ 검정

def _z_two_sided(alpha: float) -> float:
    """양측 alpha에 해당하는 정규 임계값 (이분탐색 — scipy 의존 없이)."""
    def cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 2 * (1 - cdf(mid)) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _tstat(by_date: dict) -> tuple:
    dm = [sum(v) / len(v) for v in by_date.values()]
    if len(dm) < 5:
        return None
    m = sum(dm) / len(dm)
    se = st.stdev(dm) / math.sqrt(len(dm))
    return m, (m / se if se else 0.0), len(dm)


def evaluate(panel: list, name: str, hold: int, top: int, dates: set | None = None) -> dict | None:
    """상위 top개 − 그날 그 시장 전체평균. 5분위 단조성도 함께 본다."""
    groups = defaultdict(list)
    for row in panel:
        if dates is not None and row["d"] not in dates:
            continue
        s, r = row["f"].get(name), row["r"].get(hold)
        if s is None or r is None:
            continue
        groups[(row["d"], row["m"])].append((s, r))
    by_date, q_by_date = defaultdict(list), [defaultdict(list) for _ in range(5)]
    for (d, _m), rows in groups.items():
        if len(rows) < MIN_GROUP:
            continue
        rows.sort(key=lambda x: -x[0])
        base = sum(r for _s, r in rows) / len(rows)
        by_date[d].append(sum(r for _s, r in rows[:top]) / min(top, len(rows)) - base)
        n = len(rows)
        for q in range(5):
            seg = rows[q * n // 5:(q + 1) * n // 5]
            if seg:
                q_by_date[q][d].append(sum(r for _s, r in seg) / len(seg) - base)
    res = _tstat(by_date)
    if not res:
        return None
    m, t, nd = res
    qs = []
    for q in range(5):
        qr = _tstat(q_by_date[q])
        qs.append(qr[0] if qr else 0.0)
    return {"name": name, "excess": m, "t": t, "days": nd, "quintiles": qs,
            "spread": qs[0] - qs[4],
            "monotone": all(qs[i] >= qs[i + 1] for i in range(4))
                        or all(qs[i] <= qs[i + 1] for i in range(4))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="state/panel.pkl")
    ap.add_argument("--build", action="store_true", help="패널을 새로 수집·생성한다")
    ap.add_argument("--per-market", type=int, default=350, help="시장별 시총 상위 몇 종목")
    ap.add_argument("--pages", type=int, default=45, help="네이버 일봉 페이지(10일/페이지)")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--top", type=int, default=20, help="그날 그 시장에서 상위 몇 종목을 살 것인가")
    ap.add_argument("--split", type=float, default=0.6, help="앞 몇 %를 탐색용으로 쓸지")
    a = ap.parse_args()

    if a.build:
        px, meta = fetch_data(a.per_market, a.pages, "state/deep_px.pkl")
        print("   패널 생성 중...")
        panel = build_panel(px, meta, (1, 3, 5, 7, 10))
        pickle.dump(panel, open(a.panel, "wb"))
        print(f"   저장: {a.panel} ({len(panel):,}관측)")
    panel = pickle.load(open(a.panel, "rb"))
    all_dates = sorted({r["d"] for r in panel})
    cut = all_dates[int(len(all_dates) * a.split)]
    tr = {d for d in all_dates if d < cut}
    te = {d for d in all_dates if d >= cut}
    # 본페로니: 가설 N개를 던지면 그중 하나가 우연히 통과할 확률이 N배가 된다.
    # α=.05를 N으로 나눈 양측 임계값 ≈ 정규 근사. 가설 12개 → |t| 2.87.
    n_hyp = len(HYPOTHESES)
    crit = round(_z_two_sided(0.05 / n_hyp), 2)

    print(f"패널 {len(panel):,}관측 · 종목 {len({r['t'] for r in panel})} · 거래일 {len(all_dates)}일")
    print(f"  탐색구간 {all_dates[0]}~{cut} ({len(tr)}일) / 검증구간 {cut}~{all_dates[-1]} ({len(te)}일)")
    print(f"  보유 {a.hold}거래일 · 상위 {a.top}종목 · 왕복비용 {COST}% · 가설 {n_hyp}개")
    print(f"  다중검정 보정 임계값 |t| ≥ {crit} (가설 {n_hyp}개를 던졌으므로 2.0은 불충분)")
    print()
    print("=" * 92)
    print(f"{'가설':<12} | {'탐색구간':^22} | {'검증구간':^22} | {'5분위':>6} {'단조':>4}")
    print(f"{'':<12} | {'초과수익':>9} {'t':>6} {'일':>4} | {'초과수익':>9} {'t':>6} {'일':>4} | {'스프레드':>6}")
    print("-" * 92)
    out = []
    for name in HYPOTHESES:
        a1 = evaluate(panel, name, a.hold, a.top, tr)
        a2 = evaluate(panel, name, a.hold, a.top, te)
        if not a1 or not a2:
            continue
        mark = "✅" if (abs(a1["t"]) >= crit and abs(a2["t"]) >= 2.0
                       and a1["t"] * a2["t"] > 0 and a1["monotone"]) else "  "
        print(f"{name:<12} | {a1['excess']:>+8.2f}% {a1['t']:>6.2f} {a1['days']:>4} | "
              f"{a2['excess']:>+8.2f}% {a2['t']:>6.2f} {a2['days']:>4} | "
              f"{a1['spread']:>+5.2f}% {'○' if a1['monotone'] else '×':>4} {mark}")
        out.append((name, a1, a2))
    print("-" * 92)
    surv = [n for n, x1, x2 in out
            if abs(x1["t"]) >= crit and abs(x2["t"]) >= 2.0 and x1["t"] * x2["t"] > 0 and x1["monotone"]]
    print(f"생존: {', '.join(surv) if surv else '없음'}")
    print()
    print("통과 조건 — 넷 다 만족해야 한다:")
    print(f"  ① 탐색구간 |t| ≥ {crit} (다중검정 보정)   ② 검증구간 |t| ≥ 2.0 (홀드아웃)")
    print("  ③ 두 구간 부호 일치 (방향이 뒤집히면 우연)  ④ 5분위 단조 (진짜 팩터의 지문)")
    if not surv:
        print("\n→ 하나도 못 살아남았다면 그게 정답이다. 기준을 낮추지 말 것.")


if __name__ == "__main__":
    main()
