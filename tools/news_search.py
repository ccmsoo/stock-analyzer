"""
Naver 뉴스 검색 (날짜범위) — 과거 임의 날짜 뉴스 제목 수집.
금융 종목뉴스 페이징(40p ~3개월) 한계를 넘어 2025년 등 과거 도달.

search_titles(name, ds, de) — ds/de = 'YYYYMMDD'
"""
from __future__ import annotations
import time, urllib.parse, urllib.request
from bs4 import BeautifulSoup

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def search_titles(name: str, ds: str, de: str, pages: int = 2, sleep: float = 0.4) -> list[str]:
    """name 관련 뉴스 제목 (ds~de 기간). 최대 pages*10건 내외."""
    nso = f"so:dd,p:from{ds}to{de}"
    titles = []
    for pg in range(pages):
        start = 1 + pg * 10
        url = (f"https://search.naver.com/search.naver?where=news&sm=tab_pge"
               f"&query={urllib.parse.quote(name)}&nso={urllib.parse.quote(nso, safe=':,')}&start={start}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
            soup = BeautifulSoup(html, "lxml")
            got = 0
            for sp in soup.select("span.sds-comps-text-type-headline1"):
                t = sp.get_text(strip=True)
                if len(t) >= 8:
                    titles.append(t); got += 1
            if got == 0:
                break
            time.sleep(sleep)
        except Exception:
            break
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for t in titles:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


if __name__ == "__main__":
    import sys
    nm = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
    ds = sys.argv[2] if len(sys.argv) > 2 else "20251001"
    de = sys.argv[3] if len(sys.argv) > 3 else "20251010"
    ts = search_titles(nm, ds, de)
    print(f"{nm} {ds}~{de}: {len(ts)}건")
    for t in ts[:10]:
        print("  ·", t)
