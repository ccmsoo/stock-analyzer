"""
일반 뉴스 검색 콜렉터
======================
네이버 뉴스 검색을 이용해 종목 태그가 없는 사회/정치/산업 뉴스까지 끌어옴.
→ 종목별 뉴스 페이지만으로는 보이지 않는 '간접 트리거' (정책, 경쟁사 동향, 산업 이슈) 포착이 목표.

URL: https://search.naver.com/search.naver?where=news&query=...&sort=1&pd=3&ds=&de=
- sort=1 → 최신순
- pd=3, ds, de → 기간 직접 지정 (YYYY.MM.DD)
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


def _parse_date_text(text: str, ref: datetime) -> datetime | None:
    """'2시간 전', '3일 전', '2026.05.10.' 같은 다양한 표기 처리"""
    text = text.strip()
    m = re.match(r'(\d+)\s*분\s*전', text)
    if m:
        return ref - timedelta(minutes=int(m.group(1)))
    m = re.match(r'(\d+)\s*시간\s*전', text)
    if m:
        return ref - timedelta(hours=int(m.group(1)))
    m = re.match(r'(\d+)\s*일\s*전', text)
    if m:
        return ref - timedelta(days=int(m.group(1)))
    m = re.match(r'(\d{4})\.(\d{1,2})\.(\d{1,2})\.?', text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _find_article_anchors(soup):
    """페이지에서 (article_url -> title) 매핑 추출.
    article_url = 네이버 아닌 외부 + path 있는 URL.
    title = 같은 URL을 가리키는 anchor 중 텍스트 가장 짧은 것 (보통 진짜 제목).
    """
    from urllib.parse import urlparse
    by_url = {}    # url -> {'title': str, 'anchor': bs4 Tag (첫 등장)}
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = (a.get('title') or a.get_text(strip=True)).strip()
        if not text or not href.startswith('http'):
            continue
        if 'naver.com' in href or 'keep.naver' in href:
            continue
        parsed = urlparse(href)
        if not parsed.path or parsed.path == '/':
            continue
        if len(text) < 10:
            continue
        entry = by_url.setdefault(href, {'title': text, 'anchor': a})
        if len(text) < len(entry['title']):
            entry['title'] = text
    return by_url


def _extract_meta_for_anchor(anchor, soup_root):
    """제목 anchor에서 위로 올라가며 (출처, 날짜텍스트, 본문a)을 가진 가장 가까운 부모 찾기"""
    from urllib.parse import urlparse
    target_url = anchor.get('href', '')

    for parent in anchor.parents:
        if parent is soup_root:
            break
        text = parent.get_text(' ', strip=True)
        # 부모가 너무 크면 (여러 기사가 섞임) 더 위로 안 올라감
        if len(text) > 1500:
            break

        # 부모에 같은 URL anchor가 들어있으면서 press_home anchor도 있는지 확인
        has_self = any(b.get('href') == target_url for b in parent.find_all('a', href=True))
        if not has_self:
            continue

        press = ''
        for b in parent.find_all('a', href=True):
            h = b.get('href', '')
            t = b.get_text(strip=True)
            if not t or 'naver.com' in h or h.startswith('#') or h == target_url:
                continue
            parsed = urlparse(h)
            if (not parsed.path or parsed.path == '/') and 2 <= len(t) <= 20:
                press = t
                break

        date_str_raw = ''
        m = re.search(r'(\d+\s*분\s*전|\d+\s*시간\s*전|\d+\s*일\s*전|\d{4}\.\d{1,2}\.\d{1,2}\.?)', text)
        if m:
            date_str_raw = m.group(0)

        if press or date_str_raw:
            return press, date_str_raw

    return '', ''


def search_news(query: str, date_str: str, days_before: int = 14, max_results: int = 15):
    """
    네이버 뉴스 검색 결과를 최대 max_results개 가져옴.

    Args:
        query: 검색어 (회사명, 사업 키워드 등)
        date_str: 기준일 YYYYMMDD
        days_before: 며칠 전까지
    """
    target_date = datetime.strptime(date_str, '%Y%m%d')
    earliest = target_date - timedelta(days=days_before)

    ds = earliest.strftime('%Y.%m.%d')
    de = target_date.strftime('%Y.%m.%d')

    articles = []
    seen_links = set()

    for page in range(1, 4):   # 최대 3페이지
        start = (page - 1) * 10 + 1
        url = (
            f'https://search.naver.com/search.naver?where=news'
            f'&query={requests.utils.quote(query)}'
            f'&sort=1&pd=3'
            f'&ds={ds}&de={de}'
            f'&start={start}'
        )

        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(res.text, 'lxml')
        except Exception as e:
            print(f"      ⚠️  '{query}' 검색 실패: {e}")
            break

        url_map = _find_article_anchors(soup)
        if not url_map:
            break

        added = 0
        for link, entry in url_map.items():
            if link in seen_links:
                continue
            seen_links.add(link)
            press, date_raw = _extract_meta_for_anchor(entry['anchor'], soup)
            article_dt = _parse_date_text(date_raw, target_date) if date_raw else None

            if article_dt:
                if article_dt < earliest - timedelta(days=1) or article_dt > target_date + timedelta(days=1):
                    continue
                date_text = article_dt.strftime('%Y.%m.%d %H:%M')
            else:
                date_text = ''

            articles.append({
                'title': entry['title'],
                'link': link,
                'source': press,
                'date': date_text,
                'article_dt': article_dt or target_date,
                'origin': f'search:{query}',
            })
            added += 1
            if len(articles) >= max_results:
                break

        if len(articles) >= max_results or added == 0:
            break
        time.sleep(0.4)

    articles.sort(key=lambda a: a.get('article_dt') or datetime.min, reverse=True)
    for a in articles:
        a.pop('article_dt', None)
    return articles


def collect_general_news_for_stocks(stocks, date_str, days_before=14, max_per_query=10):
    """
    종목 리스트에 대해 회사명으로 일반 뉴스 검색.

    Returns:
        dict: {ticker: [article, ...]}
    """
    news_data = {}
    for stock in stocks:
        ticker = stock['ticker']
        name = stock['name']
        print(f"   - [{ticker}] {name} 일반 뉴스 검색 (~{days_before}일)...")
        articles = search_news(name, date_str, days_before=days_before, max_results=max_per_query)
        news_data[ticker] = articles
        time.sleep(0.6)
    return news_data


if __name__ == "__main__":
    arts = search_news('SK하이닉스', datetime.now().strftime('%Y%m%d'), days_before=14, max_results=10)
    print(f"\n{len(arts)}건 수집")
    for a in arts[:5]:
        print(f"  [{a['date']}] {a['title']} ({a['source']})")
