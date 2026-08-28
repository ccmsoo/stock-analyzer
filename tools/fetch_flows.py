"""수급 데이터 수집 — 종목별 외국인·기관 순매매 (네이버 frgn).

왜 이걸 하나: 가격 데이터만으로 가설 15개를 검정했고 전부 지수를 못 이겼다
(docs/verdict_20260828.md). 같은 데이터를 더 파면 이제 거짓 양성만 나온다.
방향을 바꾸려면 **새로운 데이터 축**이어야 하고, 수급은 국내에서 가장 자주 거론되면서
이 프로젝트가 한 번도 검정해본 적 없는 축이다.

표 구조: 날짜 | 종가 | 전일비 | 등락률 | 거래량 | 기관순매매 | 외국인순매매 | 보유주수 | 지분율
페이지당 20거래일.

사용:
    venv/bin/python -m tools.fetch_flows --pages 25 --tickers 300
"""
from __future__ import annotations

import argparse
import json
import pickle
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
       "Referer": "https://finance.naver.com/"}
OUT = "state/flows.pkl"
OUT_DEEP = "state/flows_deep.pkl"


def fetch_one(code: str, pages: int) -> tuple[str, dict]:
    out = {}
    for p in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={p}"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=10).read()
            soup = BeautifulSoup(raw.decode("euc-kr", errors="replace"), "lxml")
            added = 0
            for tr in soup.select("tr"):
                tds = tr.find_all("td")
                if len(tds) < 9:
                    continue
                t = [d.get_text(strip=True).replace(",", "") for d in tds]
                if not re.match(r"\d{4}\.\d{2}\.\d{2}", t[0]):
                    continue
                try:
                    out[t[0].replace(".", "")] = {
                        "close": int(t[1]),
                        "volume": int(t[4]),
                        "inst": int(t[5].replace("+", "")),      # 기관 순매매량(주)
                        "foreign": int(t[6].replace("+", "")),   # 외국인 순매매량(주)
                        "for_ratio": float(t[8].replace("%", "")) if "%" in t[8] else None,
                    }
                    added += 1
                except (ValueError, IndexError):
                    continue
            if added == 0:
                break
            time.sleep(0.15)
        except Exception:
            break
    return code, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=25, help="20거래일/페이지 — 25=약 500일")
    ap.add_argument("--out", default=OUT, help="저장 경로 (깊은 수집은 별도 파일 권장)")
    ap.add_argument("--tickers", type=int, default=300)
    ap.add_argument("--update", action="store_true",
                    help="증분 갱신 — 이미 있는 종목도 최근 N페이지만 다시 받아 병합 "
                         "(일일 운용용. 전체 재수집은 17분, 증분은 2~3분)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    # 커밋된 universe.json 우선 — deep_px 메타는 gitignore라 cron 환경엔 없다
    import os
    up = "state/universe.json"
    meta = json.load(open(up if os.path.exists(up) else "state/deep_px.pkl.meta.json"))
    codes = list(meta)[:a.tickers] if a.tickers else list(meta)
    try:
        flows = pickle.load(open(a.out, "rb"))
    except Exception:
        flows = {}
    todo = codes if a.update else [c for c in codes if c not in flows]
    print(f"수집 대상 {len(todo)}종목 × {a.pages}페이지 (≈{a.pages*20}거래일)")
    t0, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for f in as_completed([ex.submit(fetch_one, c, a.pages) for c in todo]):
            c, s = f.result()
            if s:
                # 증분 갱신은 기존 이력을 지우지 않고 덮어쓴다
                flows[c] = {**flows.get(c, {}), **s} if a.update else s
            done += 1
            if done % 25 == 0:
                cov = sorted(len(v) for v in flows.values())
                print(f"  {done}/{len(todo)} {time.time()-t0:.0f}s median={cov[len(cov)//2] if cov else 0}",
                      flush=True)
                pickle.dump(flows, open(a.out, "wb"))
    pickle.dump(flows, open(a.out, "wb"))
    cov = sorted(len(v) for v in flows.values())
    print(f"DONE {len(flows)}종목 median={cov[len(cov)//2]}일 elapsed={int(time.time()-t0)}s")


if __name__ == "__main__":
    main()
