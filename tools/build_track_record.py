"""성적표 빌더 — 포워드 장부(픽+이벤트)의 실현 성적을 reports/track_record.json 으로.

UI(/track, /radar)가 읽는 단일 소스. cron(presurge_radar.yml)에서 레이더 직후 실행.
가격: 토스 캔들 우선, 없으면(cron) 네이버 일봉 폴백 — tools.presurge_radar.candles_any.

  venv/bin/python -m tools.build_track_record
"""
from __future__ import annotations
import datetime, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from tools.catalyst_events import cat_of, wording, load_events
from tools.presurge_radar import candles_any, SPIKE_DAYS, SPIKE_PCT

STOP = 10.0
HOLDS = (1, 3, 5, 7)


def _index_series() -> dict:
    out = {}
    for code in ("KOSDAQ", "KOSPI"):
        d = {}
        for p in range(1, 9):
            url = f"https://m.stock.naver.com/api/index/{code}/price?pageSize=20&page={p}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                for row in json.loads(urllib.request.urlopen(req, timeout=8).read()):
                    ymd = row["localTradedAt"].replace("-", "")
                    d[ymd] = {"open": float(row["openPrice"].replace(",", "")),
                              "close": float(row["closePrice"].replace(",", ""))}
            except Exception:
                break
        out[code] = d
    return out


def _fetch_px(tickers: list[str]) -> dict:
    px = {}
    def go(t):
        cs = candles_any(t)
        return t, ({c["date"]: c for c in cs} if cs else {})
    with ThreadPoolExecutor(max_workers=4) as ex:
        for f in as_completed([ex.submit(go, t) for t in tickers]):
            t, s = f.result()
            px[t] = s
    return px


def _score(r: dict, hold: int, px: dict, idxs: dict) -> dict | None:
    """픽/이벤트 공통 채점 — 익일 시초 진입, hold 거래일, 손절 -10%, 같은창 지수 알파."""
    s = px.get(r["ticker"], {})
    if not s:
        return None
    days = sorted(s)
    date_key = r.get("first_date") or r["date"]
    fwd = [d for d in days if d > date_key]
    if not fwd:
        return None
    edate = fwd[0]
    ei = days.index(edate)
    xi = ei + hold - 1
    if xi >= len(days):
        return None
    entry = s[edate]["open"]
    if not entry:
        return None
    ret = None
    stop_px = entry * (1 - STOP / 100)
    for j in range(ei, xi + 1):
        if s[days[j]]["low"] <= stop_px:
            ret = -STOP
            break
    if ret is None:
        ret = (s[days[xi]]["close"] / entry - 1) * 100
    idx = idxs.get(r.get("market") or "KOSDAQ") or idxs["KOSDAQ"]
    ik = [d for d in sorted(idx) if edate <= d <= days[xi]]
    alpha = None
    if ik and idx[ik[0]]["open"]:
        alpha = ret - (idx[ik[-1]]["close"] / idx[ik[0]]["open"] - 1) * 100
    # 픽 시점 MA20 / 스파이크 (시나리오용)
    hist = [d for d in days if d <= date_key]
    ma = None
    if len(hist) >= 20:
        closes = [s[d]["close"] for d in hist[-20:]]
        ma = "above" if s[hist[-1]]["close"] >= sum(closes) / 20 else "below"
    spike = 0.0
    for i in range(SPIKE_DAYS):
        if len(hist) >= i + 2:
            a, b = s[hist[-1 - i]]["close"], s[hist[-2 - i]]["close"]
            if b:
                spike = max(spike, (a / b - 1) * 100)
    return {"ret": ret, "alpha": alpha, "ma": ma, "stale": spike >= SPIKE_PCT,
            "date": date_key, "market": r.get("market"), "score": r.get("score"),
            "cat": cat_of(r.get("keyword")), "wording": wording(r)}


def _agg(xs: list, key: str = "ret") -> dict | None:
    v = [x[key] for x in xs if x and x.get(key) is not None]
    if not v:
        return None
    return {"n": len(v), "avg": round(sum(v) / len(v), 2),
            "win": round(sum(1 for a in v if a > 0) / len(v) * 100)}


def _pair(xs) -> dict:
    return {"abs": _agg(xs, "ret"), "alpha": _agg(xs, "alpha")}


def main() -> None:
    rows = [json.loads(l) for l in open(ROOT / "state" / "radar_ledger.jsonl")]
    events = load_events()
    tickers = sorted({r["ticker"] for r in rows})
    print(f"장부 {len(rows)}픽 / 이벤트 {len(events)}건 / {len(tickers)}종목 — 가격 수신...")
    px = _fetch_px(tickers)
    idxs = _index_series()

    picks = {h: [x for x in (_score(r, h, px, idxs) for r in rows) if x] for h in HOLDS}
    evs = {h: [x for x in (_score(e, h, px, idxs) for e in events) if x] for h in HOLDS}

    # 주별 코호트 (3일 보유, 픽 단위)
    weekly = {}
    for x in picks[3]:
        d = x["date"]
        dt = datetime.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        iso = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        weekly.setdefault(iso, []).append(x)

    # 촉매 유형별 (이벤트 단위 — 첫날 진입이 촉매의 진짜 성적)
    types = {}
    for cat in {e["cat"] for e in evs[3]}:
        types[cat] = {f"d{h}": _pair([x for x in evs[h] if x["cat"] == cat]) for h in (3, 7)}
        types[cat]["n_events"] = len([x for x in evs[3] if x["cat"] == cat])

    # 시나리오 생존판(픽 단위 3일 알파) — 정의 고정, 매일 재계산
    S = picks[3]
    mna = [x for x in S if x["cat"] == "M&A" and not x["stale"]]
    scenarios = [
        {"name": "M&A + 가드", "desc": "M&A/지배구조 촉매 · 최근 급등 없음", **_pair(mna)},
        {"name": "M&A + 가드 + KOSPI", "desc": "위 조건 + KOSPI",
         **_pair([x for x in mna if x["market"] == "KOSPI"])},
        {"name": "촉매6 + MA20아래 + KOSPI", "desc": "낮은 점수·눌린 차트·우량",
         **_pair([x for x in S if (x["score"] or 0) < 7 and x["ma"] == "below" and x["market"] == "KOSPI"])},
        {"name": "가드 통과 전체", "desc": f"최근 {SPIKE_DAYS}일 +{SPIKE_PCT:.0f}%↑ 급등 없음",
         **_pair([x for x in S if not x["stale"]])},
        {"name": "가드 제외(급등후 페이드)", "desc": "촉매 이미 발화 — 회피 근거",
         **_pair([x for x in S if x["stale"]])},
        {"name": "깊은눌림 1일 (사망 확인)", "desc": "고점比 -25%↓ 익일 반등 — 7/10 유망→7/22 사망",
         **_pair([x for x in picks[1] if False])},  # 자리 유지용, 아래서 채움
    ]
    # 깊은눌림은 from_high가 픽 기록에 있음 — rows 기준 재계산
    dip_rows = [r for r in rows if (r.get("from_high") is not None and r["from_high"] <= -25)]
    dip = [x for x in (_score(r, 1, px, idxs) for r in dip_rows) if x]
    scenarios[-1].update(_pair(dip))

    out = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "since": min((r["date"] for r in rows), default=None),
        "n_picks": len(rows), "n_events": len(events),
        "overall": {str(h): _pair(picks[h]) for h in HOLDS},
        "events_overall": {str(h): _pair(evs[h]) for h in HOLDS},
        "weekly": [{"week": w, **_pair(weekly[w])} for w in sorted(weekly)],
        "types": types,
        "wording": {w: _pair([x for x in evs[3] if x["wording"] == w])
                    for w in ("확정형", "기대형", "혼합")},
        "scenarios": scenarios,
    }
    dest = ROOT / "reports" / "track_record.json"
    json.dump(out, open(dest, "w"), ensure_ascii=False, indent=1)
    o3 = out["overall"]["3"]
    print(f"💾 {dest}")
    if o3["abs"]:
        print(f"   3일: 절대 {o3['abs']['avg']:+.2f}%/{o3['abs']['win']}% · "
              f"알파 {o3['alpha']['avg']:+.2f}%/{o3['alpha']['win']}% (n={o3['abs']['n']})")


if __name__ == "__main__":
    main()
