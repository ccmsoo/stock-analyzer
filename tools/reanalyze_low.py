"""
low confidence 종목 GPT-4o로 재분석
=====================================
gpt-5-mini reasoning_effort='low' 가 일부 종목에서 추론 약해
low로 처리된 케이스 → GPT-4o로 다시 호출해서 시그널 재추정.

대상:
- confidence == 'low' 인 종목
- 옵션 --since YYYYMMDD: 특정 날짜 이후 등장 종목만

뉴스는 종목의 last_seen 기준으로 14일 윈도우 새로 fetch.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')
except ImportError:
    pass

from openai import OpenAI

from state_manager import load_state, save_state
from collectors.news_collector import collect_news_for_stock, get_article_body
from collectors.general_news_collector import search_news
from analyzers.gpt_analyzer import analyze_single_stock


def _merge_news(stock_news, general_news):
    seen = set()
    merged = []
    for a in stock_news + general_news:
        link = a.get('link', '')
        if link in seen: continue
        seen.add(link)
        merged.append(a)
    return merged


def reanalyze_one(client, ticker, sig, use_general=True, fetch_body=False,
                   body_max_chars=2000, body_sleep=0.2):
    """한 종목 GPT-4o 재분석. fetch_body=True 면 기사 본문도 fetch 해서 프롬프트에 포함."""
    import time as _t
    date_str = sig.get('last_seen', '')
    if not date_str:
        return ticker, None, 'no last_seen'
    stock = {
        'ticker': ticker,
        'name': sig.get('name'),
        'market': sig.get('market', 'KOSPI'),
        'change_pct': sig.get('history', [{}])[-1].get('change_pct', 0),
        'close': 0,
        'volume': 0,
        'date': date_str,
    }
    try:
        stock_news = collect_news_for_stock(ticker, date_str, articles_per_stock=20, days_before=14)
    except Exception as e:
        return ticker, None, f'stock_news: {e}'
    general = []
    if use_general:
        try:
            general = search_news(sig.get('name', ''), date_str, days_before=14, max_results=10)
        except Exception:
            pass
    articles = _merge_news(stock_news, general)
    if not articles:
        return ticker, None, 'no articles'

    if fetch_body:
        for a in articles:
            link = a.get('link')
            if not link:
                continue
            body = get_article_body(link, max_chars=body_max_chars)
            a['body'] = body.strip() if (body and len(body.strip()) >= 100) else ''
            _t.sleep(body_sleep)

    try:
        result = analyze_single_stock(client, stock, articles, model='gpt-5-mini')
        return ticker, result, None
    except Exception as e:
        return ticker, None, f'ai: {str(e)[:80]}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', help='특정 날짜 이후 (YYYYMMDD) 등장 종목만')
    parser.add_argument('--max', type=int, default=None, help='최대 처리 종목 수 (테스트)')
    parser.add_argument('--no-general', action='store_true', help='일반 뉴스 검색 끄기')
    args = parser.parse_args()

    state = load_state()
    sigs = state['signals']

    targets = []
    for t, s in sigs.items():
        if s.get('confidence') != 'low':
            continue
        if args.since and s.get('last_seen', '') < args.since:
            continue
        targets.append((t, s))

    if args.max:
        targets = targets[:args.max]

    print(f'low → GPT-4o 재분석 대상: {len(targets)}건')
    if not targets:
        print('   ✓ 처리할 종목 없음')
        return

    if not os.environ.get('OPENAI_API_KEY'):
        print('❌ OPENAI_API_KEY 없음')
        return

    client = OpenAI()
    upgraded = 0
    still_low = 0
    error = 0

    print('병렬 3 (네이버 뉴스 부담 고려)...\n')
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(reanalyze_one, client, t, s, not args.no_general): (t, s)
                   for t, s in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, result, err = fut.result()
            sig = sigs[ticker]
            name = sig.get('name')
            if err:
                error += 1
                print(f'  [{i:>3}/{len(targets)}] ❌ {name}: {err}')
                continue
            if not result or result.get('confidence') == 'low':
                still_low += 1
                print(f'  [{i:>3}/{len(targets)}] · {name}: 여전히 low')
                continue
            # 업그레이드 — 기존 시그널 덮어쓰기 (단, history와 first_seen 보존)
            sig['main_theme'] = result.get('main_theme', sig.get('main_theme'))
            sig['specific_signal'] = result.get('specific_signal', sig.get('specific_signal'))
            sig['trigger_type'] = result.get('trigger_type', sig.get('trigger_type'))
            sig['reasoning'] = result.get('reasoning', sig.get('reasoning'))
            sig['related_stocks'] = result.get('related_stocks', sig.get('related_stocks', []))
            sig['watch_keywords'] = result.get('watch_keywords', sig.get('watch_keywords', []))
            sig['confidence'] = result.get('confidence')
            sig['cluster_tag'] = None    # 재분석됐으니 클러스터 다시 만들어야
            upgraded += 1
            print(f'  [{i:>3}/{len(targets)}] ✓ {name} → {result.get("confidence")}: {result.get("specific_signal", "")[:60]}')

    save_state(state)
    print(f'\n완료: 업그레이드 {upgraded} / 여전히 low {still_low} / 오류 {error}')

    if upgraded > 0:
        print('\n💡 다음 단계 — cluster_tag + deep_keywords 추가 처리:')
        print('   venv/bin/python -m tools.rebuild_cluster_tags')
        print('   venv/bin/python -m tools.normalize_cluster_tags')
        print('   venv/bin/python -m analyzers.deep_keywords --confidence both')


if __name__ == '__main__':
    main()
