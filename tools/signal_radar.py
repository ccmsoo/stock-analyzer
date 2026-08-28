"""신호 레이더 — 검증된 신호로 매일 종목을 뽑는다.

기존 `presurge_radar.py`는 AI 촉매점수로 종목을 골랐다. 2026-08-20 대조군 감사에서
그 점수는 **역엣지**로 판정됐다(같은날·같은시장 대조군 대비 −2.7%p, t=−6.2).

이 파일은 그 자리를 대신한다. 쓰는 신호는 2026-08-28에 확정된 것 하나뿐:

    거래대금 상위 N 중에서
      · 20일 모멘텀이 높고
      · 기관 5일 순매수가 **낮은** (기관이 사고 있지 **않은**)
    종목 상위 K

근거 (docs/flow_findings.md):
  · 같은 유니버스 동일가중 대비 검증구간 초과 **+35%p, 모든 offset에서 승**
  · 진입을 5일 늦춰도 살아남음 (결제 소급갱신에 의한 look-ahead 아님)
  · 모멘텀·사이즈를 각각 고정한 이중정렬에서도 독립적으로 생존

**반드시 함께 읽을 한계** (docs/verdict_20260828.md):
  · 이 신호는 **같은 유니버스 동일가중을 이기는 것**이지, 시총가중 KOSPI를 이기지 못한다.
    연도별로 쪼개면 **3/6년**(추세필터를 붙여도 4/6). 지수 ETF보다 낫다는 근거는 **없다**.
  · 하락장에 특히 약하다 — 2022년 지수 대비 −16.9%, 시작일 10개 전부 패배.
    롱온리 바스켓이라 모멘텀 크래시를 그대로 맞는다.
  · 기관 역신호 자체는 6년 중 5년 유의하게 음수로 견고하다. 불안정한 쪽은 모멘텀이다.
  · 따라서 이건 "종목을 고를 거라면 이렇게 골라라"이지 "이걸로 지수를 이겨라"가 아니다.

사용:
    venv/bin/python -m tools.signal_radar --top 10 --uni 100
"""
from __future__ import annotations

import argparse
import json
import pickle

UNI_DEFAULT = 100
TOP_DEFAULT = 10


UNIVERSE = "state/universe.json"          # 커밋됨 — cron 환경에서도 읽힌다
UNIVERSE_FALLBACK = "state/deep_px.pkl.meta.json"   # 로컬 수집본 (gitignore)


def load(flows_path="state/flows.pkl", meta_path=None):
    """ETF/ETN은 뺀다 — 종목 선택 신호에 상품이 섞이면 의미가 없다.
    (거래대금 상위 유니버스엔 MMF·채권 ETF가 들어와 전략을 조용히 희석한다)

    유니버스는 **커밋된 state/universe.json**을 먼저 본다. deep_px 메타는 gitignore라
    cron 환경엔 없기 때문이다(실제로 이것 때문에 CI에서 죽을 뻔했다)."""
    import os
    from tools.universe_filter import filter_meta
    path = meta_path or (UNIVERSE if os.path.exists(UNIVERSE) else UNIVERSE_FALLBACK)
    meta = filter_meta(json.load(open(path)))
    flows = pickle.load(open(flows_path, "rb"))
    flows = {k: v for k, v in flows.items() if k in meta}
    return flows, meta


def rank_today(flows, meta, uni_n, top, date=None):
    """가장 최근 거래일(또는 지정일) 기준 신호 순위."""
    rows = []
    for tk, s in flows.items():
        days = sorted(s)
        if len(days) < 30:
            continue
        d = date or days[-1]
        if d not in s:
            continue
        i = days.index(d)
        if i < 25:
            continue
        seq = [s[x] for x in days[:i + 1]]
        av = sum(r["volume"] for r in seq[-20:]) / 20
        c0 = seq[-21]["close"]
        if not av or not c0:
            continue
        rows.append({
            "ticker": tk,
            "name": (meta.get(tk) or {}).get("name", tk),
            "market": (meta.get(tk) or {}).get("market"),
            "close": seq[-1]["close"],
            "tv": sum(r["close"] * r["volume"] for r in seq[-20:]) / 20,
            "mom20": (seq[-1]["close"] / c0 - 1) * 100,
            "inst5": sum(r["inst"] for r in seq[-5:]) / (av * 5),
            "for5": sum(r["foreign"] for r in seq[-5:]) / (av * 5),
            "date": d,
        })
    if len(rows) < uni_n:
        return [], None
    rows.sort(key=lambda x: -x["tv"])
    uni = rows[:uni_n]
    rm = {x["ticker"]: k for k, x in enumerate(sorted(uni, key=lambda z: -z["mom20"]))}
    ri = {x["ticker"]: k for k, x in enumerate(sorted(uni, key=lambda z: z["inst5"]))}
    for x in uni:
        x["rank_mom"] = rm[x["ticker"]] + 1
        x["rank_inst"] = ri[x["ticker"]] + 1
        x["score"] = rm[x["ticker"]] + ri[x["ticker"]]
    uni.sort(key=lambda x: x["score"])
    return uni[:top], uni[0]["date"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uni", type=int, default=UNI_DEFAULT, help="거래대금 상위 N을 유니버스로")
    ap.add_argument("--top", type=int, default=TOP_DEFAULT)
    ap.add_argument("--flows", default="state/flows.pkl")
    ap.add_argument("--json", help="결과 저장 경로")
    ap.add_argument("--ledger", help="포워드 장부(append-only) 경로. "
                                     "결과를 모르는 상태로 픽을 누적해 "
                                     "나중에 과적합 불가능한 표본을 만든다")
    a = ap.parse_args()

    flows, meta = load(a.flows)
    picks, date = rank_today(flows, meta, a.uni, a.top)
    if not picks:
        print("표본 부족 — tools.fetch_flows 로 수급 데이터를 먼저 수집하라")
        return
    print(f"신호 레이더 · 기준일 {date} · 유니버스 거래대금 상위 {a.uni}")
    print("신호 = 20일 모멘텀 높고 + 기관 5일 순매수 낮은 종목")
    print("=" * 88)
    print(f"{'#':>2} {'종목':<16}{'시장':<8}{'종가':>10}{'20일모멘텀':>11}"
          f"{'기관5일':>9}{'모멘텀순위':>9}{'기관순위':>8}")
    print("-" * 88)
    for k, p in enumerate(picks, 1):
        print(f"{k:>2} {p['name'][:15]:<16}{p['market'] or '-':<8}{p['close']:>10,}"
              f"{p['mom20']:>+10.1f}%{p['inst5']:>+9.2f}{p['rank_mom']:>9}{p['rank_inst']:>8}")
    print("-" * 88)
    print("기관5일 = 기관 5일 순매매량 ÷ 20일 평균거래량 (음수 = 기관 순매도)")
    print()
    print("⚠️ 한계: '같은 유니버스 동일가중'을 이긴다는 근거만 있다.")
    print("   시총가중 KOSPI(지수 ETF) 대비는 연도별 3/6 — 이긴다는 근거 없음.")
    print("   특히 하락장에 약하다 (2022년 지수 대비 −16.9%, 시작일 10/10 패배).")
    print("   docs/verdict_20260828.md 를 함께 읽을 것.")
    if a.json:
        # presurge_radar.json 과 **동일 스키마** — UI(ui/src/lib/data.ts)와 텔레그램이
        # 코드 수정 없이 그대로 읽는다. score는 신호 순위를 0~10으로 뒤집어 매핑한다
        # (UI가 '높을수록 강함'을 가정하므로).
        n = len(picks)
        cands = []
        for k, p in enumerate(picks):
            cands.append({
                "ticker": p["ticker"], "name": p["name"], "market": p["market"],
                "score": round(10 - 9 * k / max(n - 1, 1), 1),
                "keyword": "모멘텀 상위 + 기관 순매수 아님",
                "reason": (f"20일 모멘텀 {p['mom20']:+.1f}% (유니버스 {p['rank_mom']}위), "
                           f"기관 5일 순매매 {p['inst5']:+.2f}배 (순매도 {p['rank_inst']}위). "
                           f"검증: 같은 유니버스 동일가중 대비 +44~50%p, 시작일 10/10 승. "
                           f"단 시총가중 지수 대비는 연도별 3/6으로 기각 — 하락장에 특히 약함."),
                "today": None, "price": p["close"], "chg5": None,
                "from_high": None, "volratio": None, "value_traded": p["tv"],
                "max_spike3": None, "stale_catalyst": False, "chart_ok": True,
                "cat": "수급/모멘텀", "wording": "정량",
                "rank_mom": p["rank_mom"], "rank_inst": p["rank_inst"],
                "mom20": round(p["mom20"], 2), "inst5": round(p["inst5"], 3),
            })
        import datetime as _dt
        out = {"date": date, "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
               "universe": a.uni, "signal": "mom20 + inst5(역방향)",
               "caveat": ("같은 유니버스 동일가중 대비 우위만 검증됨(+44~50%p, 시작일 10/10). "
                          "시총가중 KOSPI 대비는 연도별 3/6으로 기각. "
                          "하락장 취약(2022년 −16.9%p, 10/10 패). "
                          "docs/verdict_20260828.md 참조"),
               "candidates": cands}
        json.dump(out, open(a.json, "w"), ensure_ascii=False, indent=1)
        print(f"\n저장: {a.json}  (presurge_radar.json 과 동일 스키마)")

    if a.ledger:
        # 포워드 장부 (append-only) — **이 프로젝트에서 가장 값진 자산의 방식**을 그대로 쓴다.
        #
        # 2026-08-28 감사에서 배운 것: 촉매 레이더의 1,137픽 장부가 '결과를 모르는 상태로'
        # 기록돼 있었기 때문에, 대조군을 붙이자마자 결론이 뒤집혔다. 과적합이 불가능한
        # 유일한 표본이다. 같은 데이터를 반복해서 파면 이제 거짓 양성만 나오므로
        # (실제로 하루에 여섯 번 속았다), 오염되지 않은 증거를 만들 방법은 이것뿐이다.
        #
        # 덮어쓰지 않는다. 같은 날짜가 이미 있으면 건너뛴다.
        import os
        seen = set()
        if os.path.exists(a.ledger):
            for line in open(a.ledger):
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["date"])
                    except Exception:
                        pass
        if date in seen:
            print(f"장부: {date} 이미 기록됨 — 건너뜀")
        else:
            with open(a.ledger, "a") as f:
                for k, p in enumerate(picks, 1):
                    f.write(json.dumps({
                        "date": date, "rank": k,
                        "ticker": p["ticker"], "name": p["name"], "market": p["market"],
                        "price": p["close"], "value_traded": p["tv"],
                        "mom20": round(p["mom20"], 3), "inst5": round(p["inst5"], 4),
                        "rank_mom": p["rank_mom"], "rank_inst": p["rank_inst"],
                        "universe": a.uni, "signal": "mom20+inst5_rev",
                    }, ensure_ascii=False) + "\n")
            print(f"장부: {a.ledger} 에 {len(picks)}건 append")


if __name__ == "__main__":
    main()
