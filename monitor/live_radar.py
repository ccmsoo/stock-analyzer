"""
실시간 키워드 레이더
==========================
누적된 watch_keywords + deep_keywords 사전으로
주기적으로 일반 뉴스를 검색해서, 새 기사가 등장하면 매칭된 종목 후보 알림.

핵심 아이디어:
- 종목이 오른 *후*에 분석하는 게 아니라,
- 그 시그널의 키워드가 다른 기사에서 또 나오면 *즉시* 잡기
- 천일고속(서울고속터미널 60층) 기사가 5/11에 등장했을 때 미리 잡았어야 했던 그것.

사용:
  python -m monitor.live_radar --once               # 한 번만 실행
  python -m monitor.live_radar --interval 600       # 10분마다 무한 루프
  python -m monitor.live_radar --once --hours 24    # 최근 24시간 기사로 백트래킹
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')
except ImportError:
    pass

import os
import requests
from state_manager import load_state, save_state, article_hash
from collectors.general_news_collector import search_news
from recommenders.stopwords import NAME_MATCH_STOPWORDS


def send_telegram(text: str) -> bool:
    """텔레그램 봇으로 알림. TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 환경변수 필요.
    설정 없으면 조용히 skip."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown',
                  'disable_web_page_preview': True},
            timeout=10,
        )
        return r.ok
    except Exception:
        return False


def format_alert(alert: dict) -> str:
    """텔레그램 메시지 포맷"""
    kw = alert['keyword']
    owners = alert['matched_owners']
    owner_names = [o['name'] for o in owners]
    clusters = alert.get('clusters', [])

    lines = [
        f'🔑 *{kw}*',
        f'매핑 종목: {", ".join(owner_names[:5])}',
    ]
    if clusters:
        lines.append(f'클러스터: {", ".join(clusters[:3])}')

    lines.append('\n📰 새 기사:')
    for a in alert['new_articles'][:3]:
        title = a['title'][:80]
        link = a.get('link', '')
        lines.append(f'• [{title}]({link})')
    return '\n'.join(lines)


# 알림 사전 ANSI 색상 (터미널)
C = {'r':'\033[91m','g':'\033[92m','y':'\033[93m','c':'\033[96m','b':'\033[94m','x':'\033[0m','B':'\033[1m'}


def _load_all_stock_names() -> set:
    """KOSPI/KOSDAQ 모든 종목명을 set으로 — 키워드 사전에서 종목명 자동 제외"""
    try:
        import FinanceDataReader as fdr
        names = set()
        for mkt in ('KOSPI', 'KOSDAQ'):
            df = fdr.StockListing(mkt)
            for n in df['Name'].tolist():
                names.add(n)
                # 4글자 이상은 부분 매칭으로도 제외 (예: '삼성전자' 들어간 키워드)
        return names
    except Exception:
        return set()


_STOCK_NAMES_CACHE = None
def _get_stock_names():
    global _STOCK_NAMES_CACHE
    if _STOCK_NAMES_CACHE is None:
        _STOCK_NAMES_CACHE = _load_all_stock_names()
    return _STOCK_NAMES_CACHE


# 매칭 우선순위 — 본문 추출 카테고리별
# products/partners: 고유한 신호 → 우선 사용
# places/events: 광범위 → deep만, watch_keywords 매크로는 카테고리만 표시
HIGH_VALUE_CATEGORIES = ('products', 'partners')
LOW_VALUE_CATEGORIES = ('places', 'events', 'people')


def build_keyword_index(state: dict, min_keyword_len: int = 4,
                         high_value_only: bool = True) -> Dict[str, List]:
    """
    누적 시그널에서 (키워드 → 종목 리스트) 역인덱스 생성.
    - 종목명 자체는 자동 제외 (KOSPI/KOSDAQ 전체 종목명 stopword)
    - high_value_only=True: deep_keywords의 products/partners만 사용 (가장 신호 강한 카테고리)
    """
    stock_names = _get_stock_names()
    idx: Dict[str, set] = defaultdict(set)

    for ticker, sig in state.get('signals', {}).items():
        if sig.get('confidence') not in ('high', 'medium'):
            continue
        name = sig.get('name', '')
        cluster = sig.get('cluster_tag', '')

        candidate_kws = []
        # watch_keywords는 high_value_only=False일 때만
        if not high_value_only:
            candidate_kws.extend(sig.get('watch_keywords', []) or [])

        # deep_keywords — 카테고리별 가중치
        deep = sig.get('deep_keywords', {}) or {}
        cats = HIGH_VALUE_CATEGORIES if high_value_only else (HIGH_VALUE_CATEGORIES + LOW_VALUE_CATEGORIES)
        for cat in cats:
            candidate_kws.extend(deep.get(cat, []) or [])

        for kw in candidate_kws:
            kw = (kw or '').strip()
            if not kw or len(kw) < min_keyword_len:
                continue
            if kw in NAME_MATCH_STOPWORDS:
                continue
            if kw == name:
                continue
            # 종목명 자동 제외 — 정확 일치 또는 종목명이 키워드에 포함
            if kw in stock_names:
                continue
            # 길이 4 이상 종목명이 키워드 안에 포함되어도 제외
            if any(n in kw for n in stock_names if 4 <= len(n) <= 10):
                continue
            idx[kw].add((ticker, name, cluster))

    return {k: sorted(v) for k, v in idx.items()}


def is_new_article(state: dict, ticker_or_global: str, article: dict) -> bool:
    """이미 본 기사인지 (state['seen_articles'])"""
    h = article_hash(article.get('title', ''))
    seen = set(state.get('seen_articles', {}).get(ticker_or_global, []))
    return h not in seen


def record_seen(state: dict, key: str, articles: list):
    """본 기사를 state에 누적 (다음 폴링 때 dedup용)"""
    seen = set(state.get('seen_articles', {}).get(key, []))
    for a in articles:
        seen.add(article_hash(a.get('title', '')))
    state.setdefault('seen_articles', {})[key] = list(seen)[-500:]   # 최근 500개만 유지


def match_article_to_keywords(article: dict, keyword_idx: Dict) -> List[tuple]:
    """기사 제목에서 키워드 사전과 매칭되는 항목 추출.
    Returns: [(keyword, [(ticker, name, cluster), ...]), ...]
    """
    title = article.get('title', '')
    matches = []
    for kw, owners in keyword_idx.items():
        if kw in title:
            matches.append((kw, owners))
    return matches


def search_one_keyword(kw: str, hours_back: int = 4) -> list:
    """한 키워드로 최근 N시간 뉴스 검색"""
    today = datetime.now()
    # 검색은 일 단위라 1~2일치 받음
    date_str = today.strftime('%Y%m%d')
    try:
        return search_news(kw, date_str, days_before=max(1, hours_back // 24 + 1), max_results=10)
    except Exception:
        return []


def filter_recent(articles: list, hours_back: int) -> list:
    """최근 N시간 기사만 필터"""
    cutoff = datetime.now() - timedelta(hours=hours_back)
    out = []
    for a in articles:
        dt_str = a.get('date', '').strip()
        if not dt_str:
            # 날짜 미상은 일단 포함 (오늘 게재로 추정)
            out.append(a); continue
        try:
            dt = datetime.strptime(dt_str, '%Y.%m.%d %H:%M')
            if dt >= cutoff:
                out.append(a)
        except ValueError:
            out.append(a)
    return out


def run_once(state: dict, hours: int = 4, max_keywords: int = 20) -> dict:
    """1회 폴링 + 알림 수집"""
    idx = build_keyword_index(state)
    print(f'{C["c"]}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C["x"]}')
    print(f'{C["c"]}🔭 실시간 레이더 — 키워드 사전 {len(idx)}개{C["x"]}')
    print(f'{C["c"]}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C["x"]}\n')

    if not idx:
        print('   ⚠️  키워드 사전 비어있음 — daily.sh 한 번 돌린 뒤 다시 실행')
        return {'alerts': []}

    # 강한 키워드부터 (여러 종목과 매핑되는 것이 신호 강함)
    sorted_kws = sorted(idx.items(), key=lambda kv: -len(kv[1]))[:max_keywords]
    seen_titles_this_run = set()
    alerts: List[dict] = []

    for kw, owners in sorted_kws:
        articles = search_one_keyword(kw, hours_back=hours)
        articles = filter_recent(articles, hours_back=hours)
        if not articles:
            continue

        new_arts = []
        for a in articles:
            h = article_hash(a.get('title', ''))
            if h in seen_titles_this_run:
                continue
            seen_titles_this_run.add(h)
            if not is_new_article(state, 'live_radar', a):
                continue
            new_arts.append(a)

        if not new_arts:
            continue

        owner_names = [o[1] for o in owners]
        clusters = sorted(set(o[2] for o in owners if o[2]))
        print(f'{C["y"]}🔑 {kw}{C["x"]}  → 매핑 종목: {", ".join(owner_names[:4])} '
              f'{C["b"]}{"+더있음" if len(owner_names)>4 else ""}{C["x"]}')
        for a in new_arts[:3]:
            print(f'   {C["g"]}NEW{C["x"]}  [{a.get("date","")}] {a["title"][:80]}')
            print(f'        {a.get("link","")[:90]}')
        print()

        alert = {
            'keyword': kw,
            'matched_owners': [{'ticker': o[0], 'name': o[1], 'cluster': o[2]} for o in owners],
            'clusters': clusters,
            'new_articles': new_arts,
            'detected_at': datetime.now().isoformat(timespec='seconds'),
        }
        alerts.append(alert)

        # 강한 알림만 텔레그램 — 매핑 종목 2개 이상 OR cluster_tag 있는 경우
        if len(owners) >= 2 or any(c for c in clusters):
            send_telegram(format_alert(alert))

        # 본 기사로 등록 (다음 폴링 때 dedup)
        record_seen(state, 'live_radar', new_arts)
        time.sleep(0.8)

    # state에 live_alerts 누적 (최근 200건만)
    existing = state.get('live_alerts', [])
    existing.extend(alerts)
    state['live_alerts'] = existing[-200:]
    save_state(state)

    # dashboard.json도 갱신
    try:
        from reporters.report_generator import _write_dashboard_data
        _write_dashboard_data(Path(__file__).parent.parent / 'reports', state)
    except Exception:
        pass

    print(f'\n{C["B"]}✓ 신규 매칭 {len(alerts)}건{C["x"]}')
    return {'alerts': alerts}


def run_daemon(interval: int = 600, hours: int = 4):
    """무한 루프 — Ctrl+C로 종료"""
    print(f'{C["B"]}🔭 데몬 모드 시작 — {interval}초마다 폴링 (Ctrl+C 종료){C["x"]}\n')
    try:
        while True:
            state = load_state()
            run_once(state, hours=hours)
            next_run = (datetime.now() + timedelta(seconds=interval)).strftime('%H:%M:%S')
            print(f'\n💤 다음 폴링: {next_run} ({interval}s 후)\n')
            time.sleep(interval)
    except KeyboardInterrupt:
        print('\n중단됨')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='1회만 실행하고 종료')
    parser.add_argument('--interval', type=int, default=600, help='데몬 모드 폴링 주기(초)')
    parser.add_argument('--hours', type=int, default=4, help='최근 N시간 기사만')
    parser.add_argument('--max-keywords', type=int, default=20, help='폴링할 키워드 수')
    args = parser.parse_args()

    if args.once:
        state = load_state()
        run_once(state, hours=args.hours, max_keywords=args.max_keywords)
    else:
        run_daemon(interval=args.interval, hours=args.hours)
