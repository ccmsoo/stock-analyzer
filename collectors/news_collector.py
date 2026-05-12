"""
뉴스 수집 모듈
==============
네이버 금융 종목별 뉴스 페이지에서 최근 기사 수집

URL: https://finance.naver.com/item/news_news.naver?code={ticker}
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


def _build_news_url(ticker: str, page: int) -> tuple[str, dict]:
    """네이버 금융 종목뉴스 iframe URL — 1페이지는 page=빈값, 이후는 숫자.
    Referer 헤더가 없으면 빈 응답이 오는 경우가 있어 함께 반환."""
    p = '' if page == 1 else str(page)
    url = f'https://finance.naver.com/item/news_news.naver?code={ticker}&page={p}&clusterId='
    headers = {**HEADERS, 'Referer': f'https://finance.naver.com/item/news.naver?code={ticker}'}
    return url, headers


def collect_news_for_stock(ticker, date_str, articles_per_stock=20, days_before=14, max_pages=3):
    """
    한 종목의 최근 뉴스 수집 (네이버 금융 종목 뉴스 페이지)

    Args:
        ticker: 종목코드 (6자리)
        date_str: 기준일 YYYYMMDD
        articles_per_stock: 최대 기사 수
        days_before: 며칠 전 기사까지 (기본 14일 — 상승 '이전' 시그널 누적 추적)
        max_pages: 최대 페이지 (네이버 뉴스 페이지당 약 20건)
    """
    target_date = datetime.strptime(date_str, '%Y%m%d')
    earliest = target_date - timedelta(days=days_before)

    articles = []
    for page in range(1, max_pages + 1):
        url, headers = _build_news_url(ticker, page)

        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'lxml')

            rows = soup.select('table.type5 tr')
            page_too_old = True   # 이 페이지 기사가 전부 윈도우 밖이면 멈춤
            page_added = 0

            for row in rows:
                title_tag = row.select_one('td.title a')
                info_tag = row.select_one('td.info')
                date_tag = row.select_one('td.date')

                if not (title_tag and date_tag):
                    continue

                title = title_tag.text.strip()
                link = title_tag.get('href', '')
                if link.startswith('/'):
                    link = 'https://finance.naver.com' + link

                source = info_tag.text.strip() if info_tag else ''
                date_text = date_tag.text.strip()

                try:
                    article_date = datetime.strptime(date_text, '%Y.%m.%d %H:%M')
                except ValueError:
                    continue

                if article_date > target_date + timedelta(days=1):
                    continue
                if article_date < earliest:
                    continue

                page_too_old = False
                articles.append({
                    'title': title,
                    'link': link,
                    'source': source,
                    'date': date_text,
                    'article_dt': article_date,  # 정렬용
                    'origin': 'stock_news',
                })
                page_added += 1

                if len(articles) >= articles_per_stock:
                    return _finalize(articles)

            # 페이지에 윈도우 안 기사가 하나도 없거나, 아예 row가 없으면 더 안 봐도 됨
            if page_added == 0 or page_too_old:
                break

            time.sleep(0.3)

        except Exception as e:
            print(f"      ⚠️  [{ticker}] 뉴스 수집 실패(p={page}): {e}")
            break

    return _finalize(articles)


def _finalize(articles):
    """최신순으로 정렬하고 article_dt 키 제거"""
    articles.sort(key=lambda a: a.get('article_dt') or datetime.min, reverse=True)
    for a in articles:
        a.pop('article_dt', None)
    return articles


def get_article_body(url, max_chars=2000):
    """기사 본문 가져오기 (네이버 뉴스 링크인 경우)"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, 'lxml')
        
        # 네이버 뉴스 본문 영역 시도
        for selector in ['#dic_area', '#articleBodyContents', '.article_body', '#newsct_article']:
            content = soup.select_one(selector)
            if content:
                text = content.get_text(separator=' ', strip=True)
                return text[:max_chars]
        
        return ''
    except Exception:
        return ''


def collect_news_for_stocks(stocks, date_str, articles_per_stock=20, days_before=14, fetch_body=False):
    """
    여러 종목의 뉴스 일괄 수집

    Args:
        stocks: [{'ticker', 'name', ...}, ...] 종목 리스트
        days_before: 며칠 전까지 (기본 14일)
        fetch_body: True면 본문도 가져옴 (느림, 토큰도 많이 씀)

    Returns:
        dict: {ticker: [article, ...]}
    """
    news_data = {}
    for stock in stocks:
        ticker = stock['ticker']
        name = stock['name']
        print(f"   - [{ticker}] {name} 뉴스 수집 (~{days_before}일)...")
        articles = collect_news_for_stock(ticker, date_str, articles_per_stock, days_before=days_before)

        if fetch_body:
            for article in articles:
                article['body'] = get_article_body(article['link'])
                time.sleep(0.3)

        news_data[ticker] = articles
        time.sleep(0.5)

    return news_data


if __name__ == "__main__":
    # 테스트: 삼성전자 뉴스
    articles = collect_news_for_stock('005930', datetime.now().strftime('%Y%m%d'))
    for a in articles:
        print(f"  [{a['date']}] {a['title']} ({a['source']})")
