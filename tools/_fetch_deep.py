"""장기 일봉 백필 — state/deep_px.pkl 을 더 과거로 늘린다 (strategy_lab용).
네이버 sise_day는 페이지당 10거래일. 130페이지 ≈ 1300거래일 ≈ 5년.
여러 레짐(2021 상승·2022 하락·2023-24 횡보·2025-26 급등락)을 담아야
'그 장세에만 맞는 규칙'을 걸러낼 수 있다."""
import json, pickle, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '.')
from tools.fetch_history_naver import fetch_one

PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 130
CACHE = "state/deep_px.pkl"
meta = json.load(open(CACHE + ".meta.json"))
px = pickle.load(open(CACHE, "rb"))
t0, done = time.time(), 0
with ThreadPoolExecutor(max_workers=4) as ex:
    for f in as_completed([ex.submit(fetch_one, c, PAGES) for c in meta]):
        c, s = f.result()
        if s:
            px[c] = {**px.get(c, {}), **s}
        done += 1
        if done % 40 == 0:
            cov = sorted(len(v) for v in px.values())
            print(f"{done}/{len(meta)} {time.time()-t0:.0f}s median={cov[len(cov)//2]}", flush=True)
            pickle.dump(px, open(CACHE, "wb"))
pickle.dump(px, open(CACHE, "wb"))
cov = sorted(len(v) for v in px.values())
print(f"DONE {len(px)} median={cov[len(cov)//2]} max={cov[-1]} elapsed={int(time.time()-t0)}s")
