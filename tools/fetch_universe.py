"""
넓은 유니버스 수집 — 네이버 시총 상위 종목 → 토스 캔들 캐시.
표본 확대 재검증용 (/tmp/univ_px.json).

CLI:
  python -m tools.fetch_universe --per 500      # KOSPI/KOSDAQ 각 500
"""
from __future__ import annotations
import argparse, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import urllib.request
from bs4 import BeautifulSoup
from tools.toss_client import get_candles

OUT = Path("/tmp/univ_px.json")
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def fetch_market(sosok, per):
    """sosok 0=KOSPI 1=KOSDAQ. 시총순 상위 per종목 (code,name)."""
    out = []
    page = 1
    while len(out) < per and page <= 40:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=10).read()
            soup = BeautifulSoup(raw.decode("euc-kr", errors="replace"), "lxml")
            rows = soup.select("table.type_2 tbody tr")
            added = 0
            for tr in rows:
                a = tr.select_one("a.tltle") or tr.select_one("td a[href*='code=']")
                if not a:
                    continue
                m = re.search(r"code=(\d{6})", a.get("href", ""))
                if not m:
                    continue
                out.append((m.group(1), a.text.strip()))
                added += 1
            if added == 0:
                break
        except Exception as e:
            print(f"  p{page} 실패: {e}")
            break
        page += 1
        time.sleep(0.2)
    return out[:per]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--per", type=int, default=500)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--sleep", type=float, default=0.04)
    args = p.parse_args()

    print("📋 네이버 시총 상위 수집...")
    kospi = fetch_market(0, args.per)
    kosdaq = fetch_market(1, args.per)
    uni = {c: n for c, n in kospi + kosdaq}
    bench = {"069500": "KODEX200", "229200": "KODEX코스닥150"}
    uni.update(bench)
    print(f"   KOSPI {len(kospi)} / KOSDAQ {len(kosdaq)} = {len(uni)}종목 → 캔들 수집...")

    px = {}
    names = {}
    market = {}
    kospi_set = {c for c, _ in kospi} | {"069500"}

    def fetch(code):
        cs = get_candles(code)
        time.sleep(args.sleep)
        return code, cs

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch, c) for c in uni]
        done = 0
        for f in as_completed(futs):
            done += 1
            code, cs = f.result()
            if cs:
                px[code] = {c["date"]: c for c in cs}
                px[code + "#order"] = [c["date"] for c in cs]
                names[code] = uni[code]
                market[code] = "KOSPI" if code in kospi_set else "KOSDAQ"
            if done % 100 == 0:
                print(f"   {done}/{len(uni)} (유효 {len(names)})")

    px["#names"] = names
    px["#market"] = market
    OUT.write_text(json.dumps(px))
    print(f"\n💾 저장: {OUT} — 캔들 {len(names)}종목")


if __name__ == "__main__":
    main()
