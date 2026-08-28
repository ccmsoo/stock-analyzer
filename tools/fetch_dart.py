"""DART 일별 공시 수집 — 백테스트용 (실시간 알림용 dart_monitor와 별개).

왜 별도로 만드나: `dart_monitor.fetch_dart_day`는 5페이지(500건)에서 끊는다.
하루 공시가 586건인 날이 있고 목록이 **시간 역순**이라, 잘리는 쪽은 항상 오전 공시다.
실시간 알림엔 문제없지만 백테스트에선 표본이 체계적으로 편향된다.

왜 공시 축인가: 가격·수급 가설 17개가 전부 기각됐고(docs/verdict_20260828.md),
같은 데이터를 더 파면 이제 거짓 양성만 나온다. 공시는 **AI 해석이 필요 없는
딱딱한 사건**이다 — 날짜가 확정돼 있고 내용이 모호하지 않다. 오늘 아침 역엣지로
판정된 'AI 촉매점수'와 정확히 반대 성격이라, 검정할 가치가 있다.

주의: DART 행에는 종목코드가 없다. corp_code(DART 내부 ID)와 회사명뿐이라
      유니버스와는 **회사명으로 매칭**한다.

사용:
    venv/bin/python -m tools.fetch_dart --days 400
"""
from __future__ import annotations

import argparse
import pickle
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from bs4 import BeautifulSoup

HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
       "Referer": "https://dart.fss.or.kr/"}
OUT = "state/dart_days.pkl"

# ⚠️ DART는 공격적 수집에 IP를 즉시 차단한다 (RemoteDisconnected).
# 2026-08-28에 워커 4개로 16초에 ~2,600요청을 날려 차단당했다.
# 그래서 기본값을 **단일 워커 + 요청 간 대기**로 둔다. 빠르게 하고 싶어도 하지 말 것.
PAGE_SLEEP = 0.6      # 페이지 사이
DAY_SLEEP = 1.2       # 날짜 사이
MARKET = {"tagCom_kospi": "KOSPI", "tagCom_kosdaq": "KOSDAQ",
          "tagCom_konex": "KONEX", "tagCom_etc": "기타"}


def fetch_day(ymd: str) -> tuple[str, list[dict]]:
    """하루치 전체 공시. 페이지 상한 없이 소진될 때까지 (기존 코드의 500건 절단 제거)."""
    dot = f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:8]}"
    out, page = [], 1
    while page <= 30:
        p = {"selectDate": dot, "currentPage": str(page), "maxResults": "100",
             "maxLinks": "10", "publicYn": "Y", "search_type": "AS"}
        url = "https://dart.fss.or.kr/dsac001/search.ax?" + urllib.parse.urlencode(p)
        try:
            html = urllib.request.urlopen(
                urllib.request.Request(url, headers=HDR), timeout=12
            ).read().decode("utf-8", errors="replace")
        except Exception as e:
            # 즉시 끊기면 차단 신호 — 조용히 빈손으로 돌아가지 말고 알린다
            raise Blocked(f"{ymd} p{page}: {type(e).__name__}") from e
        rows = BeautifulSoup(html, "lxml").select("table.tbList tbody tr")
        added = 0
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            a = tds[1].select_one("a")
            if not a:
                continue
            tag = tds[1].select_one("span[class^=tagCom]")
            cls = (tag.get("class") or [""])[0] if tag else ""
            ra = tds[2].select_one("a")
            rname = ra.get_text(strip=True) if ra else tds[2].get_text(strip=True)
            out.append({
                "corp": a.get_text(strip=True),
                "market": MARKET.get(cls, ""),
                "report": rname,
                "time": tds[0].get_text(strip=True),
                "submitter": tds[3].get_text(strip=True) if len(tds) > 3 else "",
            })
            added += 1
        if added < 100:
            break
        page += 1
        time.sleep(PAGE_SLEEP)
    return ymd, out


class Blocked(Exception):
    """DART가 연결을 끊기 시작하면 즉시 중단한다 — 계속 두들기면 차단이 길어진다."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400, help="오늘부터 며칠 과거까지")
    ap.add_argument("--workers", type=int, default=1,
                    help="1 유지 권장 — DART는 병렬 수집에 IP를 차단한다")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    try:
        store = pickle.load(open(a.out, "rb"))
        # 차단 중에 저장된 '0건'은 진짜 휴장일과 구별되지 않으므로 버리고 다시 받는다
        store = {k: v for k, v in store.items() if v}
    except Exception:
        store = {}
    today = date(2026, 8, 28)
    want = []
    for i in range(a.days):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:          # 주말 제외
            continue
        ymd = d.strftime("%Y%m%d")
        if ymd not in store:
            want.append(ymd)
    print(f"수집 대상 {len(want)}일 (보유 {len(store)}일)")
    t0, done = time.time(), 0
    try:
        if a.workers <= 1:
            for d in want:                      # 순차 — 기본이자 권장 경로
                ymd, items = fetch_day(d)
                store[ymd] = items
                done += 1
                if done % 10 == 0:
                    n = sum(len(v) for v in store.values())
                    print(f"  {done}/{len(want)} {time.time()-t0:.0f}s 누적 {n:,}건", flush=True)
                    pickle.dump(store, open(a.out, "wb"))
                time.sleep(DAY_SLEEP)
        else:
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                for f in as_completed([ex.submit(fetch_day, d) for d in want]):
                    ymd, items = f.result()
                    store[ymd] = items
                    done += 1
                    if done % 25 == 0:
                        pickle.dump(store, open(a.out, "wb"))
    except Blocked as e:
        pickle.dump(store, open(a.out, "wb"))
        print(f"\n⛔ DART 차단 감지 ({e}). {done}일 수집 후 중단.")
        print("   재시도는 최소 30분 뒤에, --workers 1 로. 계속 두들기면 차단이 길어진다.")
        return
    pickle.dump(store, open(a.out, "wb"))
    n = sum(len(v) for v in store.values())
    days = sorted(store)
    print(f"DONE {len(store)}일 · {n:,}건 · {days[0]}~{days[-1]} · {int(time.time()-t0)}s")


if __name__ == "__main__":
    main()
