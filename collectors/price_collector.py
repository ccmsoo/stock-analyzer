"""
주가 데이터 수집 모듈 (네이버 금융 크롤링 버전)
================================================
네이버 금융의 '상승률/하락률 순위' 페이지를 직접 파싱합니다.
- 장 마감 후 데이터 사용
- API 키 불필요, 빠름

설치: pip install requests beautifulsoup4 lxml
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

URLS = {
    ('KOSPI', 'up'):    'https://finance.naver.com/sise/sise_rise.naver?sosok=0',
    ('KOSPI', 'down'):  'https://finance.naver.com/sise/sise_fall.naver?sosok=0',
    ('KOSDAQ', 'up'):   'https://finance.naver.com/sise/sise_rise.naver?sosok=1',
    ('KOSDAQ', 'down'): 'https://finance.naver.com/sise/sise_fall.naver?sosok=1',
}

NOISE_KEYWORDS = ['스팩', '우선주', '리츠']

# ETF/ETN 운용사 브랜드 — 종목명이 "브랜드 + 공백"으로 시작하면 ETF/ETN로 판단.
# (예: "KODEX 200" ✓ ETF, "삼성전자" ✗ 일반주). 공백을 요구해야 '삼성/신한/KB/한투/미래에셋'
# 같은 짧은 한국어/영문 브랜드명이 일반 종목명과 충돌하지 않음.
ETF_BRAND_PREFIXES = [
    'KODEX', 'TIGER', 'ACE', 'KBSTAR', 'ARIRANG', 'HANARO', 'KOSEF',
    'SOL', 'TIMEFOLIO', 'RISE', 'PLUS', 'WOORI', 'WON', 'BNK',
    '히어로즈', '마이티', 'KOACT', 'FOCUS', 'KIWOOM', '파워',
    'TRUE', 'QV', '한투', '신한', '삼성', '미래에셋', 'KB',
]

# 종목명 내부에 등장하면 거의 확실히 ETF/ETN인 토큰
ETF_TOKENS = ['ETF', 'ETN', '인버스', '레버리지', '선물']


def _is_etf(name: str) -> bool:
    """종목명이 ETF/ETN으로 보이면 True"""
    if any(name.startswith(p + ' ') for p in ETF_BRAND_PREFIXES):
        return True
    if any(t in name for t in ETF_TOKENS):
        return True
    return False


def _parse_rank_page(url, top_n=10, market=''):
    """네이버 금융 순위 페이지 파싱"""
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.encoding = 'euc-kr'
    soup = BeautifulSoup(res.text, 'lxml')

    rows = soup.select('table.type_2 tr')
    results = []

    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 11:
            continue

        name_tag = cols[1].find('a')
        if not name_tag:
            continue

        name = name_tag.text.strip()
        href = name_tag.get('href', '')
        m = re.search(r'code=(\d{6})', href)
        if not m:
            continue
        ticker = m.group(1)

        if any(kw in name for kw in NOISE_KEYWORDS):
            continue
        if _is_etf(name):
            continue
        
        try:
            close = int(cols[2].text.replace(',', '').strip())
            change_pct = float(cols[4].text.replace('%', '').replace('+', '').strip())
            volume = int(cols[5].text.replace(',', '').strip())
        except (ValueError, IndexError):
            continue
        
        results.append({
            'ticker': ticker,
            'name': name,
            'close': close,
            'change_pct': change_pct,
            'volume': volume,
            'market': market,
        })
        
        if len(results) >= top_n:
            break
    
    return results


def get_latest_business_day(reference_date=None):
    """가장 최근 영업일 (KST 기준)"""
    if reference_date is None:
        now = datetime.now()
        if now.hour < 16:
            now -= timedelta(days=1)
        while now.weekday() >= 5:
            now -= timedelta(days=1)
        return now.strftime('%Y%m%d')
    return reference_date


def get_top_movers(date_str=None, top_n=10):
    """등락률 TOP N 종목 수집"""
    if date_str is None:
        date_str = get_latest_business_day()
    
    result = {}
    key_map = {
        ('KOSPI', 'up'):    'kospi_up',
        ('KOSPI', 'down'):  'kospi_down',
        ('KOSDAQ', 'up'):   'kosdaq_up',
        ('KOSDAQ', 'down'): 'kosdaq_down',
    }
    
    for (market, direction), url in URLS.items():
        print(f"   - {market} {direction} 순위 수집 중...")
        result[key_map[(market, direction)]] = _parse_rank_page(url, top_n, market)
        time.sleep(0.5)
    
    return result


if __name__ == "__main__":
    movers = get_top_movers(top_n=10)
    
    print("\n📈 KOSPI 상승률 TOP 10")
    for s in movers['kospi_up']:
        print(f"  [{s['ticker']}] {s['name']:20s} {s['change_pct']:+6.2f}%  {s['close']:>8,}원")
    
    print("\n📈 KOSDAQ 상승률 TOP 10")
    for s in movers['kosdaq_up']:
        print(f"  [{s['ticker']}] {s['name']:20s} {s['change_pct']:+6.2f}%  {s['close']:>8,}원")
