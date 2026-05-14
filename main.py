"""
한국 주식 일일 분석 시스템
============================
매일 장 마감 후 실행하여:
1. 등락률 TOP N 추출 (코스피/코스닥, ETF 제외)
2. 종목별 뉴스 + 일반 뉴스 검색 (1~2주 윈도우)
3. 이미 분석된 종목은 캐시 재사용 (연속 상승 추적)
4. 신규 종목은 AI 분석 → 신규/연속/불명 분리
5. 마크다운 + CSV + HTML 리포트, 상태 누적

사용법:
    python main.py                    # 가장 최근 영업일 분석
    python main.py --date 20260512    # 특정 날짜
    python main.py --top 10           # TOP N
    python main.py --no-general-news  # 일반 뉴스 검색 끄기 (속도 ↑)
"""
import argparse
import os
from datetime import datetime
from pathlib import Path

# .env 자동 로드 (있으면)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

from collectors.price_collector import get_top_movers, get_latest_business_day
from collectors.historical_collector import (
    get_top_movers_historical, krx_business_days,
    prefetch_period, top_movers_from_cache,
)
from collectors.news_collector import collect_news_for_stocks
from collectors.general_news_collector import collect_general_news_for_stocks
from reporters.report_generator import generate_report
from state_manager import (
    load_state, save_state, is_continuation, get_previous_signal,
    filter_new_articles, record_signal, prune_stale_articles,
)

AI_PROVIDER = os.environ.get('AI_PROVIDER', 'openai').lower()
if AI_PROVIDER == 'anthropic':
    from analyzers.claude_analyzer import analyze_single_stock
    from anthropic import Anthropic
    AI_MODEL_NAME = 'Claude Sonnet 4.5'
    _AI_MODEL = 'claude-sonnet-4-5'
else:
    from analyzers.gpt_analyzer import analyze_single_stock
    from openai import OpenAI
    AI_MODEL_NAME = 'GPT-4o'
    _AI_MODEL = 'gpt-5-mini'


def _make_client():
    if AI_PROVIDER == 'anthropic':
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수를 설정하세요.")
        return Anthropic(api_key=key)
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError("OPENAI_API_KEY 환경변수를 설정하세요.")
    return OpenAI(api_key=key)


def _merge_news(stock_news, general_news):
    """종목 뉴스 + 일반 뉴스 머지 (링크 기준 dedup)"""
    seen = set()
    merged = []
    for a in stock_news + general_news:
        link = a.get('link', '')
        if link in seen:
            continue
        seen.add(link)
        merged.append(a)
    return merged


def main(date_str=None, top_n=10, use_general_news=True, historical=False, skip_ai=False,
         state=None, hist_cache=None):
    if date_str is None:
        date_str = get_latest_business_day()
    print(f"\n📅 분석 기준일: {date_str}{' (HISTORICAL/FDR)' if historical else ''}\n")

    if state is None:
        state = load_state()

    # 1. 등락률 TOP 종목
    print("1️⃣  등락률 TOP 종목 수집 (ETF 제외)...")
    if historical:
        if hist_cache is not None:
            movers = top_movers_from_cache(hist_cache, date_str, top_n=top_n)
        else:
            movers = get_top_movers_historical(date_str, top_n=top_n)
    else:
        movers = get_top_movers(date_str, top_n=top_n)
    print(f"   ✓ 코스피 상승 {len(movers['kospi_up'])}, 코스닥 상승 {len(movers['kosdaq_up'])}")

    all_stocks = movers['kospi_up'] + movers['kosdaq_up']

    # 2. 종목별 뉴스 (14일)
    print("\n2️⃣  종목별 뉴스 수집 (14일 윈도우)...")
    stock_news_data = collect_news_for_stocks(all_stocks, date_str, articles_per_stock=20, days_before=14)

    # 3. 일반 뉴스 검색 (회사명 기준)
    general_news_data = {}
    if use_general_news:
        print("\n2️⃣ -2  일반 뉴스 검색...")
        general_news_data = collect_general_news_for_stocks(all_stocks, date_str, days_before=14, max_per_query=10)

    # 4. 머지 + 기사 중복 제거
    print("\n3️⃣  기사 중복 제거...")
    merged_news = {}
    total_new = total_dup = 0
    for stock in all_stocks:
        t = stock['ticker']
        combined = _merge_news(stock_news_data.get(t, []), general_news_data.get(t, []))
        new_articles, dup = filter_new_articles(state, t, combined)
        merged_news[t] = new_articles
        total_new += len(new_articles)
        total_dup += len(dup)
    print(f"   ✓ 신규 {total_new}건 / 이미 본 적 있음 {total_dup}건")

    # 5. 분석: 연속 종목은 캐시 재사용, 신규만 AI 호출
    if skip_ai:
        print("\n4️⃣  AI 분석 건너뜀 (skip_ai=True) — raw 데이터만 누적")
    else:
        print(f"\n4️⃣  분석 — {AI_MODEL_NAME} (신규만 호출, 연속 종목 캐시)...")
    client = None if skip_ai else _make_client()
    analysis = {}
    status_map = {}    # ticker -> 'new' | 'continuation' | 'unclear'
    new_count = cont_count = unclear_count = 0

    for stock in all_stocks:
        t = stock['ticker']
        if is_continuation(state, t, date_str):
            prev = get_previous_signal(state, t)
            analysis[t] = {
                'main_theme': prev.get('main_theme', ''),
                'specific_signal': prev.get('specific_signal', ''),
                'trigger_type': prev.get('trigger_type', ''),
                'reasoning': f"이전 분석 재사용 (최초 {prev.get('first_seen')}, {prev.get('consecutive_days', 1) + 1}일째 등장)",
                'related_stocks': prev.get('related_stocks', []),
                'watch_keywords': prev.get('watch_keywords', []),
                'confidence': prev.get('confidence', 'medium'),
                'trigger_date': '',
                'trigger_lag_days': 0,
            }
            status_map[t] = 'continuation'
            cont_count += 1
            # 캐시 재사용이라도 history는 누적 — 같은 시그널이 며칠 가는지 추적용
            record_signal(state, t, stock, analysis[t], date_str)
            print(f"   - [{t}] {stock['name']} → 연속 상승 (캐시, {state['signals'][t].get('consecutive_days')}일째)")
            continue

        articles = merged_news.get(t, [])
        if skip_ai:
            result = {
                'main_theme': '(미분석)', 'specific_signal': '', 'trigger_type': 'unknown',
                'trigger_date': '', 'trigger_lag_days': 0,
                'reasoning': 'skip_ai=True 모드로 수집만 진행',
                'related_stocks': [], 'watch_keywords': [], 'confidence': 'low',
            }
            print(f"   - [{t}] {stock['name']} 뉴스만 수집 (기사 {len(articles)}건)")
        else:
            print(f"   - [{t}] {stock['name']} 분석 중 (기사 {len(articles)}건)...")
            result = analyze_single_stock(client, stock, articles, model=_AI_MODEL)
        analysis[t] = result

        conf = (result.get('confidence') or '').lower()
        trig = (result.get('trigger_type') or '').lower()
        if conf == 'low' or trig == 'unknown' or not result.get('specific_signal'):
            status_map[t] = 'unclear'
            unclear_count += 1
        else:
            status_map[t] = 'new'
            new_count += 1

        record_signal(state, t, stock, result, date_str)

    print(f"   ✓ 신규 시그널 {new_count} / 연속 {cont_count} / 이유 불명 {unclear_count}")

    # 6. 리포트 생성
    print("\n5️⃣  리포트 생성...")
    output_dir = Path(__file__).parent / "reports"
    output_dir.mkdir(exist_ok=True)

    report_path = generate_report(
        date_str=date_str,
        movers=movers,
        news_data=merged_news,
        analysis=analysis,
        status_map=status_map,
        output_dir=output_dir,
        state=state,
    )
    print(f"   ✓ 리포트: {report_path}")

    # 7. 상태 저장
    prune_stale_articles(state)
    save_state(state)
    print(f"   ✓ 상태 저장: state/signals.json")

    print(f"\n✅ 완료!\n   리포트: {report_path}\n   대시보드: reports/index.html\n")


def run_range(start_date: str, end_date: str, top_n=10, use_general_news=True, skip_ai=False,
              prefetch_workers=2):
    """배치 모드: 영업일 범위 일괄 백필.
    - 전체 종목 시세를 한 번에 prefetch → pickle 캐시 → 날짜별 추출 (네이버 rate-limit 회피)
    - state는 누적 공유
    """
    days = krx_business_days(start_date, end_date)
    state = load_state()
    print(f"\n🗓  배치 모드 — {start_date} ~ {end_date} ({len(days)}영업일)\n")

    print("📦 prefetch — 전 종목 일별 시세 캐시 생성 (한 번만 fetch, resume 가능)")
    hist_cache = prefetch_period(start_date, end_date, max_workers=prefetch_workers)
    print(f"📦 캐시 완료: {len(hist_cache)}종목\n")

    for i, d in enumerate(days, 1):
        print(f"\n{'='*60}\n  [{i}/{len(days)}] {d}\n{'='*60}")
        try:
            main(date_str=d, top_n=top_n, use_general_news=use_general_news,
                 historical=True, skip_ai=skip_ai, state=state, hist_cache=hist_cache)
        except Exception as e:
            print(f"❌ [{d}] 실패: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD, 미지정시 최근 영업일")
    parser.add_argument("--start", help="배치 시작일 YYYYMMDD (--end와 함께)")
    parser.add_argument("--end", help="배치 종료일 YYYYMMDD")
    parser.add_argument("--top", type=int, default=10, help="TOP N 종목")
    parser.add_argument("--no-general-news", action='store_true', help="일반 뉴스 검색 끄기")
    parser.add_argument("--historical", action='store_true', help="과거 일자 (FDR 사용)")
    parser.add_argument("--skip-ai", action='store_true', help="AI 호출 없이 데이터만 수집")
    args = parser.parse_args()

    if args.start and args.end:
        run_range(args.start, args.end, top_n=args.top,
                  use_general_news=not args.no_general_news,
                  skip_ai=args.skip_ai)
    else:
        main(date_str=args.date, top_n=args.top,
             use_general_news=not args.no_general_news,
             historical=args.historical, skip_ai=args.skip_ai)
