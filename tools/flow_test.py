"""수급 가설 검정 — 외국인·기관 순매수가 다음 수익을 예측하는가.

가격 기반 가설 15개가 전부 기각된 뒤(docs/verdict_20260828.md) 시도하는 **새 데이터 축**.
검정 규칙은 앞과 동일하게 엄격히 유지한다 — 여기서 기준을 풀면 지금까지 한 게 무의미해진다.
  · 같은 (날짜, 시장) 안에서 상위그룹 − 그날 전체평균  (레짐 중립, 벤치마크 불필요)
  · 거래일 클러스터 t (유효 표본 = 거래일 수)
  · 본페로니 보정 + 홀드아웃 + 5분위 단조성

사용:  venv/bin/python -m tools.flow_test
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics as st
from collections import defaultdict

COST = 0.4
MIN_GROUP = 30


def signals(rows, i):
    """rows = 날짜 오름차순 [{close,volume,inst,foreign,for_ratio}], i = 신호일 인덱스.
    순매매량은 절대 주식수라 종목 크기에 좌우된다 → 평균거래량으로 나눠 정규화한다."""
    if i < 21:
        return {}
    av = sum(r["volume"] for r in rows[i - 19:i + 1]) / 20
    if not av:
        return {}
    f1 = rows[i]["foreign"] / av
    i1 = rows[i]["inst"] / av
    f5 = sum(r["foreign"] for r in rows[i - 4:i + 1]) / (av * 5)
    i5 = sum(r["inst"] for r in rows[i - 4:i + 1]) / (av * 5)
    f20 = sum(r["foreign"] for r in rows[i - 19:i + 1]) / (av * 20)
    i20 = sum(r["inst"] for r in rows[i - 19:i + 1]) / (av * 20)
    out = {"외인1일": f1, "외인5일": f5, "외인20일": f20,
           "기관1일": i1, "기관5일": i5, "기관20일": i20,
           "외인+기관5일": f5 + i5,
           "외인5일_역": -f5,                      # 반대 방향도 함께 (역엣지 확인용)
           "쌍끌이5일": min(f5, i5)}               # 둘 다 사는 것만
    r0, r1 = rows[i - 5]["for_ratio"], rows[i]["for_ratio"]
    if r0 is not None and r1 is not None:
        out["외인지분율변화5일"] = r1 - r0
    return out


def build_panel(flows, meta, holds=(5, 10)):
    panel = []
    for tk, s in flows.items():
        days = sorted(s)
        if len(days) < 60:
            continue
        rows = [s[d] for d in days]
        mk = (meta.get(tk) or {}).get("market")
        for i in range(21, len(days) - max(holds) - 1):
            f = signals(rows, i)
            if not f:
                continue
            # 진입가는 다음날 종가 기준으로 근사 (frgn 표엔 시가가 없다).
            # 종가 진입은 약간 낙관적이므로, 비용을 넉넉히(0.4%) 물려 상쇄한다.
            e = rows[i + 1]["close"]
            if not e:
                continue
            fwd = {}
            for h in holds:
                if i + h < len(rows):
                    fwd[h] = (rows[i + h]["close"] / e - 1) * 100 - COST
            if fwd:
                panel.append({"d": days[i], "m": mk, "f": f, "r": fwd})
    return panel


def _t(by_date):
    dm = [sum(v) / len(v) for v in by_date.values()]
    if len(dm) < 5:
        return None
    m = sum(dm) / len(dm)
    se = st.stdev(dm) / math.sqrt(len(dm))
    return m, (m / se if se else 0.0), len(dm)


def evaluate(panel, name, hold, top, dates=None):
    g = defaultdict(list)
    for row in panel:
        if dates is not None and row["d"] not in dates:
            continue
        s, r = row["f"].get(name), row["r"].get(hold)
        if s is None or r is None:
            continue
        g[(row["d"], row["m"])].append((s, r))
    byd = defaultdict(list)
    q = [defaultdict(list) for _ in range(5)]
    for _k, rows in g.items():
        if len(rows) < MIN_GROUP:
            continue
        rows.sort(key=lambda x: -x[0])
        base = sum(r for _s, r in rows) / len(rows)
        d = _k[0]
        byd[d].append(sum(r for _s, r in rows[:top]) / min(top, len(rows)) - base)
        n = len(rows)
        for k in range(5):
            seg = rows[k * n // 5:(k + 1) * n // 5]
            if seg:
                q[k][d].append(sum(r for _s, r in seg) / len(seg) - base)
    res = _t(byd)
    if not res:
        return None
    m, t, nd = res
    qs = [(_t(q[k])[0] if _t(q[k]) else 0.0) for k in range(5)]
    return {"excess": m, "t": t, "days": nd, "quintiles": qs,
            "monotone": all(qs[i] >= qs[i + 1] for i in range(4))
                        or all(qs[i] <= qs[i + 1] for i in range(4))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    flows = pickle.load(open("state/flows.pkl", "rb"))
    meta = json.load(open("state/deep_px.pkl.meta.json"))
    panel = build_panel(flows, meta)
    alld = sorted({r["d"] for r in panel})
    cut = alld[int(len(alld) * 0.6)]
    tr = {d for d in alld if d < cut}
    te = {d for d in alld if d >= cut}
    names = list(panel[0]["f"]) if panel else []
    n_h = len(names)

    def z(al):
        lo, hi = 0.0, 10.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if 2 * (1 - 0.5 * (1 + math.erf(mid / math.sqrt(2)))) > al:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    crit = round(z(0.05 / max(n_h, 1)), 2)

    print(f"패널 {len(panel):,}관측 · 종목 {len(flows)} · 거래일 {len(alld)} ({alld[0]}~{alld[-1]})")
    print(f"  탐색 {len(tr)}일 / 검증 {len(te)}일 · 보유 {a.hold}일 · 상위 {a.top}종목 · 비용 {COST}%")
    print(f"  가설 {n_h}개 → 본페로니 임계 |t| ≥ {crit}")
    print()
    print("=" * 92)
    print(f"{'신호':<16} | {'탐색: 초과':>10}{'t':>7}{'일':>5} | {'검증: 초과':>10}{'t':>7}{'일':>5} | {'단조':>4}")
    print("-" * 92)
    surv = []
    for nm in names:
        x = evaluate(panel, nm, a.hold, a.top, tr)
        y = evaluate(panel, nm, a.hold, a.top, te)
        if not x or not y:
            continue
        ok = (abs(x["t"]) >= crit and abs(y["t"]) >= 2.0
              and x["t"] * y["t"] > 0 and x["monotone"])
        if ok:
            surv.append(nm)
        print(f"{nm:<16} | {x['excess']:>+9.2f}%{x['t']:>7.2f}{x['days']:>5} | "
              f"{y['excess']:>+9.2f}%{y['t']:>7.2f}{y['days']:>5} | "
              f"{'○' if x['monotone'] else '×':>4} {'✅' if ok else ''}")
    print("-" * 92)
    print(f"생존: {', '.join(surv) if surv else '없음'}")
    if not surv:
        print("→ 없으면 없는 것이다. 기준을 낮추지 말 것.")


if __name__ == "__main__":
    main()
