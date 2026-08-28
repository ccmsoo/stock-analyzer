"""신호 장부 채점기 — 라이브 픽의 진짜 성적을 대조군과 함께 잰다.

`tools/score_ledger.py`(촉매 레이더용)는 2026-08-20 감사에서 드러난 오류를 그대로
갖고 있다: 거래비용 0%, 손절 −10% 고정(갭 미반영), **그리고 대조군이 없다**.
"지수 대비 알파"만 재면 하락장에서 알파가 통째로 만들어진다.

이 파일은 같은 일을 오늘의 규칙대로 한다:
  · 진입 = 신호일 다음 거래일 **시가** (미래참조 없음)
  · 손절 = −10%, 단 **갭하락이면 시초가 체결** (−10% 고정은 낙관 편향)
  · 왕복비용 0.4% 차감
  · **대조군 = 같은 날·같은 시장의 유니버스 평균** ← 이게 핵심이다
  · 유의성 = **거래일 클러스터 t** (유효 표본은 픽 수가 아니라 거래일 수)

검증: 이 채점기를 기존 촉매 장부(1,137픽)에 돌리면 감사 결과
(−2.7%p, t≈−6)가 재현돼야 한다. 그게 채점기가 맞다는 증거다.

사용:
    venv/bin/python -m tools.score_signal_ledger                      # 신호 장부
    venv/bin/python -m tools.score_signal_ledger --ledger state/radar_ledger.jsonl \\
        --date-field date --verify                                    # 채점기 자체 검증
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics as st
from collections import defaultdict

from tools.universe_filter import filter_meta

COST = 0.4
STOP = 10.0
MIN_PEERS = 30


def load_ledger(path, date_field="date"):
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if date_field in r and r.get("ticker"):
            rows.append(r)
    return rows


def score(px, code, ymd, hold):
    """다음 거래일 시가 진입 · hold 보유 · 손절 −10%(갭이면 시초) · 비용 차감."""
    s = px.get(code) or {}
    days = sorted(d for d in s if not d.startswith("__"))
    nxt = next((d for d in days if d > ymd), None)
    if nxt is None:
        return None
    i = days.index(nxt)
    if i + hold - 1 >= len(days):
        return None
    e = s[days[i]]["open"]
    if not e:
        return None
    stop_px = e * (1 - STOP / 100)
    for j in range(i, i + hold):
        c = s[days[j]]
        if c["low"] <= stop_px:
            return (min(c["open"], stop_px) / e - 1) * 100 - COST
    return (s[days[i + hold - 1]]["close"] / e - 1) * 100 - COST


def run(rows, px, meta, hold):
    """픽 − 같은날·같은시장 유니버스 평균. 거래일 클러스터 t."""
    by_date = defaultdict(list)
    raw, n = [], 0
    for ymd in sorted({r["date"] for r in rows}):
        base = defaultdict(list)
        for c, v in meta.items():
            x = score(px, c, ymd, hold)
            if x is not None:
                base[v.get("market")].append(x)
        picks = defaultdict(list)
        for r in rows:
            if r["date"] != ymd:
                continue
            mk = r.get("market") or (meta.get(r["ticker"]) or {}).get("market")
            x = score(px, r["ticker"], ymd, hold)
            if x is not None:
                picks[mk].append(x)
                raw.append(x)
        for mk, sel in picks.items():
            arr = base.get(mk) or []
            if len(arr) < MIN_PEERS:
                continue
            by_date[ymd].append(sum(sel) / len(sel) - sum(arr) / len(arr))
            n += len(sel)
    dm = [sum(v) / len(v) for v in by_date.values()]
    if len(dm) < 5:
        return None
    m = sum(dm) / len(dm)
    se = st.stdev(dm) / math.sqrt(len(dm))
    return {"n": n, "days": len(dm), "excess": m, "t": (m / se if se else 0.0),
            "abs": (sum(raw) / len(raw)) if raw else 0.0,
            "win": 100 * sum(1 for v in dm if v > 0) / len(dm)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="state/signal_ledger.jsonl")
    ap.add_argument("--hold", type=int, nargs="*", default=[3, 5, 10])
    ap.add_argument("--px", default="state/deep_px.pkl")
    ap.add_argument("--verify", action="store_true",
                    help="채점기 자체 검증 모드 — 기존 촉매 장부로 감사 결과를 재현한다")
    a = ap.parse_args()

    meta = filter_meta(json.load(open("state/deep_px.pkl.meta.json")))
    px = {t: v for t, v in pickle.load(open(a.px, "rb")).items() if t in meta}
    meta = {c: v for c, v in meta.items() if c in px}
    rows = load_ledger(a.ledger)
    if not rows:
        print(f"{a.ledger}: 픽 없음 (아직 쌓이는 중)")
        return
    ds = sorted({r["date"] for r in rows})
    print(f"장부 {len(rows):,}픽 · 거래일 {len(ds)} ({ds[0]}~{ds[-1]}) · 유니버스 {len(meta)}종목")
    print(f"진입 익일시가 · 손절 −{STOP:.0f}%(갭체결) · 비용 {COST}% · 대조군 = 같은날·같은시장 평균\n")
    print("=" * 80)
    print(f"{'보유':>4}{'채점 픽':>9}{'거래일':>7}{'절대수익':>11}{'대조군 대비':>13}{'t':>8}{'이긴날':>8}")
    print("-" * 80)
    for hold in a.hold:
        r = run(rows, px, meta, hold)
        if not r:
            print(f"{hold:>4}  표본 부족")
            continue
        print(f"{hold:>4}{r['n']:>9,}{r['days']:>7}{r['abs']:>+10.2f}%"
              f"{r['excess']:>+12.2f}%p{r['t']:>8.2f}{r['win']:>7.0f}%")
    print("-" * 80)
    if a.verify:
        print("\n검증 모드: 촉매 장부에서 대조군 대비 음수(t≈−6)가 나오면 채점기가 맞다.")
        print("(2026-08-20 감사 결과: −2.7%p, t=−6.2 · docs/audit_20260820.md)")
    else:
        print("\n|t| ≥ 2 이면 우연이 아닐 가능성이 높다는 뜻. 유효 표본은 픽 수가 아니라 거래일 수다.")


if __name__ == "__main__":
    main()
