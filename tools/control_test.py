"""대조군 검정 — 촉매 픽이 '같은 날 같은 시장 아무 종목'보다 나은가?

이 시스템이 실패하는 유일한 방식은 없는 엣지를 있다고 착각하는 것이고,
그 착각의 원인은 딱 하나였다: **지수를 벤치마크로 썼다.**

하락장에서 지수(특히 KOSPI)는 워치리스트 중소형주보다 훨씬 많이 빠진다.
그래서 아무 종목이나 사도 "지수 대비 알파"가 +2% 넘게 나온다. 그건 실력이 아니라
그냥 유니버스의 성질(사이즈/베타)이다. 진짜 질문은:

    "AI 촉매점수가 붙은 종목이, 같은 날 같은 시장의 다른 워치리스트 종목보다 나은가?"

이 파일이 그 질문만 잰다. 픽 하나마다 (그날·그시장 대조군 평균)을 빼서 초과알파를
구하고, 거래일 단위로 클러스터링해 t값을 낸다. 같은 날 픽 40개는 독립 표본 40개가
아니라 사실상 1개이므로, 픽 수로 유의성을 주장하면 거의 항상 틀린다.

사용:
    venv/bin/python -m tools.control_test              # 기본(보유3/5/7, 유동성 0/30/100억)
    venv/bin/python -m tools.control_test --hold 5 --refresh
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import statistics as st
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from tools.presurge_radar import candles_any

LEDGER = "state/radar_ledger.jsonl"
SIGNALS = "state/signals.json"
CACHE = "state/control_px.pkl"

STOP = 10.0          # 손절 -10% (검증상 넓은 손절이 볼록 페이오프를 지킨다)
COST = 0.4           # 왕복 거래비용 % — 세금 0.18 + 수수료 + 슬리피지. 0%는 금지.
MIN_PEERS = 5        # 그날 그 시장 대조군이 이보다 적으면 비교 자체를 버린다


# ---------------------------------------------------------------- 데이터

def _index_series() -> dict:
    out = {}
    for code in ("KOSDAQ", "KOSPI"):
        d = {}
        for p in range(1, 9):
            url = f"https://m.stock.naver.com/api/index/{code}/price?pageSize=20&page={p}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                for row in json.loads(urllib.request.urlopen(req, timeout=8).read()):
                    d[row["localTradedAt"].replace("-", "")] = {
                        "open": float(row["openPrice"].replace(",", "")),
                        "close": float(row["closePrice"].replace(",", "")),
                    }
            except Exception:
                break
        out[code] = d
    return out


def _universe() -> dict:
    """레이더가 실제로 훑는 워치리스트 = signals.json의 high/medium."""
    sig = json.load(open(SIGNALS))["signals"]
    return {t: {"market": v.get("market")} for t, v in sig.items()
            if v.get("confidence") in ("high", "medium")}


def _prices(tickers: list[str], refresh: bool) -> dict:
    px = {}
    if not refresh and os.path.exists(CACHE):
        try:
            px = pickle.load(open(CACHE, "rb"))
        except Exception:
            px = {}
    todo = [t for t in tickers if t not in px]
    if todo:
        print(f"   일봉 수집 {len(todo)}종목...", flush=True)

        def go(t):
            try:
                return t, {c["date"]: c for c in (candles_any(t) or [])}
            except Exception:
                return t, {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for f in as_completed([ex.submit(go, t) for t in todo]):
                t, s = f.result()
                px[t] = s
        pickle.dump(px, open(CACHE, "wb"))
    return px


# ---------------------------------------------------------------- 채점

def _score(px, idxs, ticker: str, market: str | None, date: str, hold: int) -> dict | None:
    """익일 시초 진입 · hold 거래일 보유 · 손절 -10%(갭이면 시초가 체결) · 비용 차감.

    벤치마크는 **실제 보유 구간과 같은 창**으로 잰다. 손절로 D+1에 나왔으면 지수도
    D+1까지만. 창을 D+7로 고정하면 하락장에서 알파가 통째로 만들어진다."""
    s = px.get(ticker) or {}
    if not s:
        return None
    days = sorted(s)
    fwd = [d for d in days if d > date]
    if not fwd:
        return None
    ei = days.index(fwd[0])
    xi = ei + hold - 1
    if xi >= len(days):
        return None
    entry = s[days[ei]]["open"]
    if not entry:
        return None
    stop_px = entry * (1 - STOP / 100)
    ret, exit_i = None, xi
    for j in range(ei, xi + 1):
        c = s[days[j]]
        if c["low"] <= stop_px:
            exit_i = j
            ret = (min(c["open"], stop_px) / entry - 1) * 100   # 갭하락이면 시초가 체결
            break
    if ret is None:
        ret = (s[days[xi]]["close"] / entry - 1) * 100
    ret -= COST
    idx = idxs.get(market or "KOSDAQ") or idxs["KOSDAQ"]
    ik = [d for d in sorted(idx) if days[ei] <= d <= days[exit_i]]
    if not ik or not idx[ik[0]]["open"]:
        return None
    alpha = ret - (idx[ik[-1]]["close"] / idx[ik[0]]["open"] - 1) * 100
    return {"ret": ret, "alpha": alpha, "date": date, "market": market}


def _feats(px, ticker: str, date: str):
    """레이더와 동일한 차트필터 통과 여부 + 그날 거래대금."""
    s = px.get(ticker) or {}
    hist = [d for d in sorted(s) if d <= date]
    if len(hist) < 8:
        return None
    c = [s[d]["close"] for d in hist]
    ok = ((c[-1] / c[-2] - 1) * 100 < 5                                    # 오늘 아직 안 감
          and (c[-1] / c[-6] - 1) * 100 < 15                               # 5일 안 extended
          and max((c[-1 - i] / c[-2 - i] - 1) * 100 for i in range(5)) < 12)  # 급등후 페이드 아님
    return ok, s[hist[-1]]["close"] * s[hist[-1]]["volume"]


def _tstat(by_date: dict) -> tuple[float, float, int]:
    """거래일 단위 클러스터 t — 유효 표본은 픽 수가 아니라 거래일 수다."""
    dm = [sum(v) / len(v) for v in by_date.values()]
    if len(dm) < 3:
        return 0.0, 0.0, len(dm)
    m = sum(dm) / len(dm)
    se = st.stdev(dm) / math.sqrt(len(dm))
    return m, (m / se if se else 0.0), len(dm)


# ---------------------------------------------------------------- 실행

def run(hold: int, floor_eok: float, px, idxs, uni, rows, pickset) -> dict | None:
    floor = floor_eok * 1e8
    picks = []
    for r in rows:
        f = _feats(px, r["ticker"], r["date"])
        if not f or f[1] < floor:
            continue
        x = _score(px, idxs, r["ticker"], r.get("market"), r["date"], hold)
        if x:
            picks.append(x)
    peers = defaultdict(list)
    for date in sorted({r["date"] for r in rows}):
        for t, v in uni.items():
            if (date, t) in pickset:          # 픽은 대조군에서 제외 (순수 비교)
                continue
            f = _feats(px, t, date)
            if not f or not f[0] or f[1] < floor:
                continue
            x = _score(px, idxs, t, v.get("market"), date, hold)
            if x:
                peers[(date, x["market"])].append(x["alpha"])
    diffs, by_date = [], defaultdict(list)
    for x in picks:
        k = (x["date"], x["market"])
        if len(peers.get(k, [])) < MIN_PEERS:
            continue
        d = x["alpha"] - sum(peers[k]) / len(peers[k])
        diffs.append(d)
        by_date[x["date"]].append(d)
    if not diffs:
        return None
    m, t, nd = _tstat(by_date)
    peer_all = [a for v in peers.values() for a in v]
    return {"hold": hold, "floor": floor_eok, "n": len(diffs),
            "excess": sum(diffs) / len(diffs),
            "winrate": 100 * sum(1 for v in diffs if v > 0) / len(diffs),
            "t": t, "days": nd,
            "pick_alpha": sum(x["alpha"] for x in picks) / len(picks),
            "peer_alpha": sum(peer_all) / len(peer_all) if peer_all else 0.0,
            "pick_abs": sum(x["ret"] for x in picks) / len(picks)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="*", default=[3, 5, 7])
    ap.add_argument("--floor", type=float, nargs="*", default=[0, 30, 100],
                    help="유동성 하한(억). 저유동 종목의 가짜 알파를 걷어내는 용도")
    ap.add_argument("--refresh", action="store_true", help="일봉 캐시 무시하고 재수집")
    ap.add_argument("--json", help="결과를 JSON으로 저장할 경로")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(LEDGER)]
    uni = _universe()
    pickset = {(r["date"], r["ticker"]) for r in rows}
    print(f"장부 {len(rows)}픽 · 거래일 {len({r['date'] for r in rows})}일 · 워치리스트 {len(uni)}종목")
    px = _prices(sorted({r["ticker"] for r in rows} | set(uni)), a.refresh)
    idxs = _index_series()

    print("\n" + "=" * 86)
    print("대조군 검정 — 픽이 '같은 날·같은 시장 워치리스트 평균'을 이겼는가")
    print(f"  진입 익일시초 · 손절 -{STOP:.0f}%(갭체결) · 왕복비용 {COST}% · 벤치마크 창=실보유 구간")
    print("=" * 86)
    print(f"{'보유':>4} {'유동성':>7} | {'픽n':>5} | {'픽알파':>7} {'대조군알파':>9} | "
          f"{'초과알파':>8} {'이긴비율':>7} {'거래일t':>8}")
    print("-" * 86)
    out = []
    for hold in a.hold:
        for fl in a.floor:
            r = run(hold, fl, px, idxs, uni, rows, pickset)
            if not r:
                continue
            out.append(r)
            print(f"{r['hold']:>4} {r['floor']:>6.0f}억 | {r['n']:>5} | "
                  f"{r['pick_alpha']:>+6.2f}% {r['peer_alpha']:>+8.2f}% | "
                  f"{r['excess']:>+7.2f}%p {r['winrate']:>6.0f}% {r['t']:>8.2f}")
    print("-" * 86)
    if out:
        ts = [r["t"] for r in out]
        print(f"판정: t 범위 {min(ts):+.2f} ~ {max(ts):+.2f}  (거래일 {out[0]['days']}일 기준)")
        print("  |t|<2  → 촉매점수는 무작위와 구별 안 됨 (엣지 없음)")
        print("  t<-2   → 픽이 유의하게 열위 (역엣지). 점수를 그대로 쓰면 안 된다")
        print("  t>+2   → 비로소 엣지 주장 가능. 단 표본 레짐(현재 급락장 하나)을 함께 밝힐 것")
    if a.json and out:
        json.dump(out, open(a.json, "w"), ensure_ascii=False, indent=1)
        print(f"\n저장: {a.json}")


if __name__ == "__main__":
    main()
