"""촉매 이벤트 장부 — 픽(일 단위) 장부를 '촉매 이벤트' 단위로 집계해 실제 결과를 붙인다.

문제의식: radar_ledger는 같은 촉매가 여러 날 재픽되면 여러 줄로 쌓여, 늦은
재진입까지 평균에 섞인다. "촉매 → 실행 결과"를 보려면 이벤트(종목×촉매유형,
첫 등장일) 단위로 접어야 한다.

이벤트 정의: 같은 (ticker, 촉매유형)이 마지막 픽 후 COOLDOWN 거래일 안에
다시 나오면 같은 이벤트, 넘으면 새 이벤트. 진입은 이벤트 첫 픽 다음날 시초.

  venv/bin/python -m tools.catalyst_events            # 리포트
  venv/bin/python -m tools.catalyst_events --dump     # 이벤트 목록 저장(reports/)
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

COOLDOWN = 10  # 거래일: 이 안에 같은 유형 재픽 = 같은 이벤트
STOP = 10.0
HOLDS = (1, 3, 5, 7)

CATS = [("M&A", r"M&A|인수|합병|공개매수|경영권|상장폐지|지분"),
        ("수주", r"수주|계약|공급|낙찰"),
        ("임상/FDA", r"FDA|임상|허가|승인|품목"),
        ("국책", r"국책|정부|과제|조달|국산화"),
        ("투자/증설", r"투자|시설|공장|증설|신설"),
        ("실적", r"흑자|실적|어닝|매출|호황"),
        ("기술수출", r"기술이전|기술수출|수출")]
DONE = r"체결|완료|확정|획득|선정|승인|낙찰|수령|취득|출시|양산"
HOPE = r"추진|검토|기대|전망|가능성|목표|예상|논의|타진|계획|예정|우선협상"


def cat_of(kw: str) -> str:
    for lab, pat in CATS:
        if re.search(pat, kw or ""):
            return lab
    return "기타"


def wording(r: dict) -> str:
    txt = (r.get("keyword") or "") + " " + (r.get("reason") or "")
    d, h = bool(re.search(DONE, txt)), bool(re.search(HOPE, txt))
    if d and not h:
        return "확정형"
    if h and not d:
        return "기대형"
    return "혼합"


def load_events() -> list[dict]:
    rows = [json.loads(l) for l in open(ROOT / "state" / "radar_ledger.jsonl")]
    rows.sort(key=lambda r: (r["ticker"], r["date"]))
    events = []
    cur = {}  # (ticker, cat) -> event
    for r in rows:
        key = (r["ticker"], cat_of(r.get("keyword")))
        ev = cur.get(key)
        if ev and ev["_dates"] and _tdiff(ev["_dates"][-1], r["date"]) <= COOLDOWN:
            ev["_dates"].append(r["date"])
            ev["n_picks"] += 1
        else:
            ev = {"ticker": r["ticker"], "name": r.get("name"), "market": r.get("market"),
                  "cat": key[1], "first_date": r["date"], "score": r.get("score"),
                  "keyword": r.get("keyword"), "wording": wording(r),
                  "n_picks": 1, "_dates": [r["date"]]}
            events.append(ev)
            cur[key] = ev
    for ev in events:
        ev["last_date"] = ev.pop("_dates")[-1]
    return events


def _tdiff(d1: str, d2: str) -> int:
    """달력일 근사(주말 감안 x1.4): 거래일 cooldown 판정용."""
    from datetime import date
    a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:]))
    b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:]))
    return int((b - a).days / 1.4)


def _index_series():
    import urllib.request
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


def score_events(events: list[dict]) -> None:
    """이벤트 첫 픽 기준 D+1/3/5/7 절대·알파를 붙인다(토스 캔들, 네이버 폴백)."""
    from tools.presurge_radar import candles_any
    idxs = _index_series()
    px = {}
    for ev in events:
        t = ev["ticker"]
        if t not in px:
            cs = candles_any(t)
            px[t] = {c["date"]: c for c in cs} if cs else {}
            time.sleep(0.2)
        s = px[t]
        if not s:
            continue
        days = sorted(s)
        fwd = [d for d in days if d > ev["first_date"]]
        if not fwd:
            continue
        edate = fwd[0]
        ei = days.index(edate)
        entry = s[edate]["open"]
        if not entry:
            continue
        ev["entry_date"] = edate
        idx = idxs.get(ev.get("market") or "KOSDAQ") or idxs["KOSDAQ"]
        for hold in HOLDS:
            xi = ei + hold - 1
            if xi >= len(days):
                continue
            ret = None
            stop_px = entry * (1 - STOP / 100)
            for j in range(ei, xi + 1):
                if s[days[j]]["low"] <= stop_px:
                    ret = -STOP
                    break
            if ret is None:
                ret = (s[days[xi]]["close"] / entry - 1) * 100
            ev[f"ret{hold}"] = round(ret, 2)
            ik = [d for d in sorted(idx) if edate <= d <= days[xi]]
            if ik and idx[ik[0]]["open"]:
                iret = (idx[ik[-1]]["close"] / idx[ik[0]]["open"] - 1) * 100
                ev[f"alpha{hold}"] = round(ret - iret, 2)


def _agg(evs, key):
    v = [e[key] for e in evs if e.get(key) is not None]
    if not v:
        return None
    return len(v), sum(v) / len(v), sum(1 for x in v if x > 0) / len(v) * 100


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dump", action="store_true", help="이벤트 목록 reports/catalyst_events.json 저장")
    args = p.parse_args()

    events = load_events()
    print(f"픽 장부 → 촉매 이벤트 {len(events)}건 (중복픽 접기, cooldown {COOLDOWN}거래일)")
    score_events(events)

    def fmt(a):
        return f"n={a[0]:>3} {a[1]:+6.2f}%/{a[2]:3.0f}%" if a else "n=0"

    print("\n== 이벤트 단위 성적 (첫 픽 다음날 시초 진입) ==")
    print(f"{'구분':<16}" + "".join(f"{'D+'+str(h):>20}" for h in HOLDS))
    print(f"{'전체(알파)':<16}" + "".join(f"{fmt(_agg(events, f'alpha{h}')):>20}" for h in HOLDS))
    print(f"{'전체(절대)':<16}" + "".join(f"{fmt(_agg(events, f'ret{h}')):>20}" for h in HOLDS))

    print("\n== 촉매 유형별 (알파, D+3 / D+7) ==")
    for lab, _ in CATS + [("기타", None)]:
        evs = [e for e in events if e["cat"] == lab]
        a3, a7 = _agg(evs, "alpha3"), _agg(evs, "alpha7")
        if a3 and a3[0] >= 8:
            print(f"  {lab:<10} D+3 {fmt(a3):>24}   D+7 {fmt(a7):>24}")

    print("\n== 문구별 (알파 D+3) ==")
    for w in ("확정형", "기대형", "혼합"):
        print(f"  {w:<8} {fmt(_agg([e for e in events if e['wording'] == w], 'alpha3'))}")

    print("\n== 재픽 횟수별 — 촉매가 여러 날 살아있으면 더 좋은가 (알파 D+3) ==")
    for lab, f in [("1회(하루살이)", lambda e: e["n_picks"] == 1),
                   ("2~3회", lambda e: 2 <= e["n_picks"] <= 3),
                   ("4회+(끈질김)", lambda e: e["n_picks"] >= 4)]:
        print(f"  {lab:<12} {fmt(_agg([e for e in events if f(e)], 'alpha3'))}")

    if args.dump:
        out = ROOT / "reports" / "catalyst_events.json"
        json.dump(events, open(out, "w"), ensure_ascii=False, indent=1)
        print(f"\n💾 {out}")


if __name__ == "__main__":
    main()
