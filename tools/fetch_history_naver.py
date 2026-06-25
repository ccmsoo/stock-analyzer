"""
네이버 sise_day 깊은 수집 — 토스 100일 한계를 넘는 장기 OHLCV.
'더 과거로' 재검증용 (/tmp/hist_px.json, kw_px와 동일 포맷).

CLI:
  python -m tools.fetch_history_naver --tickers 120 --pages 30
"""
from __future__ import annotations
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import urllib.request
from bs4 import BeautifulSoup

OUT = Path("/tmp/hist_px.json")
UNIV = Path("/tmp/univ_px.json")
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Referer": "https://finance.naver.com/"}


def fetch_one(code, pages):
    series = {}
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=10).read()
            html = raw.decode("euc-kr", errors="replace")
            soup = BeautifulSoup(html, "lxml")
            added = 0
            for tr in soup.select("table.type2 tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                dt = tds[0].get_text(strip=True)
                if "." not in dt:
                    continue
                try:
                    y, m, d = dt.split(".")
                    ymd = f"{y}{m.zfill(2)}{d.zfill(2)}"
                    close = int(tds[1].get_text(strip=True).replace(",", ""))
                    op = int(tds[3].get_text(strip=True).replace(",", ""))
                    hi = int(tds[4].get_text(strip=True).replace(",", ""))
                    lo = int(tds[5].get_text(strip=True).replace(",", ""))
                    vol = int(tds[6].get_text(strip=True).replace(",", ""))
                    series[ymd] = {"date": ymd, "open": op, "high": hi, "low": lo, "close": close, "volume": vol}
                    added += 1
                except (ValueError, IndexError):
                    continue
            if added == 0:
                break
            time.sleep(0.12)
        except Exception:
            break
    return code, series


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", type=int, default=120)
    p.add_argument("--pages", type=int, default=30, help="페이지(10일/페이지) — 30=약300일")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    # 유니버스 캐시에서 (시총상위, 시장정보 포함) 종목 선택
    if not UNIV.exists():
        print("⚠️ /tmp/univ_px.json 없음 — fetch_universe 먼저"); sys.exit(1)
    upx = json.loads(UNIV.read_text())
    names = upx.get("#names", {})
    market = upx.get("#market", {})
    tickers = [t for t in names if t not in ("069500", "229200")][: args.tickers]
    # 벤치 포함
    targets = tickers + ["069500", "229200"]
    print(f"📜 네이버 sise_day {len(targets)}종목 × {args.pages}페이지(~{args.pages*10}일) 수집...")

    px = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch_one, c, args.pages) for c in targets]
        done = 0
        for f in as_completed(futs):
            done += 1
            code, series = f.result()
            if len(series) >= 30:
                order = sorted(series.keys(), reverse=True)  # newest-first (toss와 동일)
                px[code] = series
                px[code + "#order"] = order
            if done % 30 == 0:
                print(f"   {done}/{len(targets)} (유효 {len([k for k in px if '#order' not in k])})")

    px["#names"] = {t: names.get(t, t) for t in px if "#order" not in t and not t.startswith("#")}
    px["#market"] = {t: market.get(t, "KOSDAQ") for t in px if "#order" not in t and not t.startswith("#")}
    OUT.write_text(json.dumps(px))
    valid = [k for k in px if "#order" not in k and not k.startswith("#")]
    # 날짜 범위
    alld = sorted({d for t in valid for d in px[t + "#order"]})
    print(f"\n💾 저장: {OUT} — {len(valid)}종목, 범위 {alld[0]} ~ {alld[-1]} ({len(alld)}일)")


if __name__ == "__main__":
    main()
