"""널 테스트 — 검정 프레임워크가 거짓 양성을 만들지 않는지 확인한다.

무작위 신호를 N번 넣어서 t값 분포를 본다. |t|>2가 5% 안팎(정상)이 아니라 훨씬 많이
나오면, t 계산이 과대평가되고 있다는 뜻이고 그 위에서 내린 모든 판정이 무효다.

2026-08-28 실행 결과: 무작위 30회 중 |t|>2가 **1회(3%)**, |t|>2.8이 **0회**.
같은 프레임워크에서 실제 신호(기관5일)는 **t=−7.35**로 무작위 30회 전부보다 극단이었다.
→ 프레임워크는 보정돼 있고, 신호는 진짜다.

**새로운 검정 방식을 만들 때마다 여기부터 통과시킬 것.**

사용:  venv/bin/python -m tools.null_test --n 30
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
from collections import defaultdict

from tools.flow_test import _t, build_panel
from tools.universe_filter import filter_meta


def evaluate_scores(panel, score_fn, hold=10, top=20, min_group=30):
    g = defaultdict(list)
    for row in panel:
        r = row["r"].get(hold)
        s = score_fn(row)
        if r is None or s is None:
            continue
        g[(row["d"], row["m"])].append((s, r))
    byd = defaultdict(list)
    for (d, _m), rows in g.items():
        if len(rows) < min_group:
            continue
        rows.sort(key=lambda x: -x[0])
        base = sum(r for _s, r in rows) / len(rows)
        byd[d].append(sum(r for _s, r in rows[:top]) / min(top, len(rows)) - base)
    return _t(byd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="무작위 신호 반복 횟수")
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--signal", default="기관5일", help="비교할 실제 신호")
    a = ap.parse_args()

    flows = pickle.load(open("state/flows.pkl", "rb"))
    meta = filter_meta(json.load(open("state/deep_px.pkl.meta.json")))
    flows = {k: v for k, v in flows.items() if k in meta}
    panel = build_panel(flows, meta, holds=(a.hold,))
    print(f"패널 {len(panel):,} · 거래일 {len({r['d'] for r in panel})}")

    ts = []
    for seed in range(a.n):
        rnd = random.Random(seed)
        cache = {}

        def sc(row, rnd=rnd, cache=cache):
            k = id(row)
            if k not in cache:
                cache[k] = rnd.random()
            return cache[k]
        r = evaluate_scores(panel, sc, a.hold)
        if r:
            ts.append(r[1])
    ts.sort()
    n2 = sum(1 for t in ts if abs(t) > 2)
    print(f"\n무작위 신호 {len(ts)}회 t 분포: "
          f"최소 {ts[0]:+.2f} · 중앙 {ts[len(ts)//2]:+.2f} · 최대 {ts[-1]:+.2f}")
    print(f"  |t|>2   : {n2}/{len(ts)} ({100*n2/len(ts):.0f}%)   기대 5% 안팎")
    print(f"  |t|>2.8 : {sum(1 for t in ts if abs(t) > 2.8)}/{len(ts)}")
    ok = n2 <= len(ts) * 0.15
    print(f"  판정: {'✅ 프레임워크 정상' if ok else '❌ 거짓 양성 과다 — t 계산을 의심하라'}")

    real = evaluate_scores(panel, lambda row: row["f"].get(a.signal), a.hold)
    if real:
        more = sum(1 for t in ts if abs(t) >= abs(real[1]))
        print(f"\n실제 신호 '{a.signal}' t = {real[1]:+.2f}"
              f"  (무작위 {len(ts)}회 중 이보다 극단적인 것: {more}개)")


if __name__ == "__main__":
    main()
