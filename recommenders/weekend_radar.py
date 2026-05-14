"""
주말/장 종료 후 단기 트레이딩 후보 발굴
========================================
워크플로우:
- 누적된 분석 결과(state/signals.json)에서 "활성 테마" 식별
- 각 테마별로 추격(이미 폭등 종목) vs 신규(같은 테마 안 오른 종목) 분리
- 주말 일반 뉴스로 그 키워드가 여전히 살아있는지 모멘텀 체크
- 단기(1~7일) 변동성·거래량 기반 점수로 정렬

사용:
  python -m recommenders.weekend_radar           # 기본 — 최근 3일 데이터 사용
  python -m recommenders.weekend_radar --days 5  # 누적 5일까지 봄
  python -m recommenders.weekend_radar --news    # 최근 뉴스도 함께 검색
"""
import argparse
import json
import re
import sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import FinanceDataReader as fdr

sys.path.insert(0, str(Path(__file__).parent.parent))

from state_manager import load_state
from recommenders.reverse_signal import _build_listing_index, _norm
from recommenders.stopwords import CLUSTER_STOPWORDS, NAME_MATCH_STOPWORDS
from collectors.general_news_collector import search_news


def _extract_theme_keywords(s: dict) -> set:
    """specific_signal + watch_keywords에서 의미있는 키워드 추출.
    - 3자 이상
    - CLUSTER_STOPWORDS(일반어/회사명 접미사) 제외
    → 결과적으로 'HBM4', 'CPO 본딩' 같은 지엽적 명사만 남음"""
    text = ' '.join([s.get('specific_signal', '') or '',
                     ' '.join(s.get('watch_keywords', []))])
    tokens = set()
    for m in re.finditer(r'[가-힣]{3,}|[A-Za-z][A-Za-z0-9\-]{2,}', text):
        tok = m.group(0)
        if tok in CLUSTER_STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def find_active_themes(state: dict, lookback_days: int = 3, min_members: int = 2) -> List[dict]:
    """
    활성 테마 = cluster_tag 기반 그룹핑.
    토큰 매칭이 아니라 AI가 직접 부여한 정규화된 cluster_tag로 묶음.
    """
    today = datetime.now()
    cutoff = (today - timedelta(days=lookback_days * 2)).strftime('%Y%m%d')

    recent_sigs = {}
    for t, s in state['signals'].items():
        if s.get('last_seen', '') < cutoff:
            continue
        if s.get('confidence') == 'low':
            continue
        if not s.get('cluster_tag'):
            continue
        recent_sigs[t] = s

    # cluster_tag → 종목 매핑
    tag_to_tickers: Dict[str, set] = defaultdict(set)
    for t, s in recent_sigs.items():
        tag = s.get('cluster_tag', '').strip()
        if tag:
            tag_to_tickers[tag].add(t)

    themes = []
    for tag, tickers in tag_to_tickers.items():
        if len(tickers) < min_members:
            continue
        members = [recent_sigs[t] for t in tickers]
        all_dates = sorted({h['date'] for m in members for h in m.get('history', [])})
        avg_pct = sum(h['change_pct'] for m in members for h in m.get('history', [])) / sum(len(m.get('history', [1])) for m in members)
        themes.append({
            'keyword': tag,    # cluster_tag (snake_case)
            'member_tickers': sorted(tickers),
            'members': members,
            'date_span': all_dates,
            'avg_change_pct': round(avg_pct, 1),
            'momentum_score': round(len(tickers) * len(all_dates) * max(avg_pct, 1) / 10, 1),
        })

    themes.sort(key=lambda x: -x['momentum_score'])
    return themes


def collect_related_candidates(themes: List[dict], listing: Dict[str, dict],
                                already_seen: set, min_marcap: int = 5_000_000_000) -> Dict[str, list]:
    """각 테마별로 멤버 종목의 related_stocks + watch_keywords 명사가 종목명에 포함된 종목.
    cluster_tag는 영어라 종목명 매칭 안 됨 → 멤버들의 watch_keywords에서 한국어 명사 사용."""
    pool: Dict[str, set] = defaultdict(set)

    for theme in themes:
        kw = theme['keyword']
        # related_stocks 합집합
        for m in theme['members']:
            for rs in m.get('related_stocks', []):
                if rs and rs.strip():
                    pool[kw].add(_norm(rs))
        # 멤버들의 watch_keywords에서 종목명 매칭용 한국어 토큰 추출
        kr_tokens = set()
        for m in theme['members']:
            for wkw in m.get('watch_keywords', []):
                for tok in re.findall(r'[가-힣]{4,}', wkw):
                    if tok in NAME_MATCH_STOPWORDS:
                        continue
                    kr_tokens.add(tok)
        # 그 토큰들로 종목명 부분 일치
        for tok in kr_tokens:
            if 4 <= len(tok) <= 8:
                norm_tok = _norm(tok)
                for norm_name in listing:
                    if norm_tok in norm_name and len(norm_name) > len(norm_tok):
                        pool[kw].add(norm_name)

    # listing에 있고 시총·이미 본 종목 필터
    result = {}
    for kw, names in pool.items():
        cand = []
        for n in names:
            info = listing.get(n)
            if not info or info['code'] in already_seen or info['marcap'] < min_marcap:
                continue
            cand.append(info)
        if cand:
            result[kw] = cand
    return result


def score_short_term(candidates: Dict[str, list], reference_date: str = None) -> Dict[str, list]:
    """단기 트레이딩 점수 — 변동성×거래량 급증×최근 모멘텀"""
    if reference_date is None:
        reference_date = datetime.now().strftime('%Y%m%d')
    target = datetime.strptime(reference_date, '%Y%m%d')
    start = (target - timedelta(days=45)).strftime('%Y-%m-%d')
    end = target.strftime('%Y-%m-%d')

    cache = {}

    def _get(code):
        if code in cache:
            return cache[code]
        try:
            df = fdr.DataReader(code, start, end)
            if df.empty or len(df) < 10:
                cache[code] = {}
                return {}
            df = df.copy()
            df['Value'] = df['Close'] * df['Volume']
            recent5 = df.tail(5)
            prev = df.iloc[-20:-5] if len(df) >= 20 else df.iloc[:-5]

            avg_abs = (recent5['Change'].abs() * 100).mean()
            avg_value_eok = recent5['Value'].mean() / 1e8
            vol_surge = (recent5['Volume'].mean() / prev['Volume'].mean()) if prev['Volume'].mean() > 0 else 1
            ret_5d = (recent5['Close'].iloc[-1] / recent5['Close'].iloc[0] - 1) * 100

            # 단기 점수: 변동성 + 거래량 급증 (이미 너무 오른 종목엔 패널티)
            base = avg_abs * vol_surge
            # 5일 +50% 넘게 오른 종목은 페널티 (D+1 차익실현 위험)
            if ret_5d > 50: base *= 0.3
            elif ret_5d > 30: base *= 0.6
            elif ret_5d > 0: base *= 1.0 + min(ret_5d / 100, 0.2)

            cache[code] = {
                'last_close': int(recent5['Close'].iloc[-1]),
                'last_change_pct': round(float(recent5['Change'].iloc[-1]) * 100, 2),
                'avg_abs_change_5d': round(avg_abs, 2),
                'avg_value_5d_eok': round(avg_value_eok, 1),
                'vol_surge': round(vol_surge, 2),
                'ret_5d': round(ret_5d, 2),
                'short_term_score': round(base, 2),
            }
            return cache[code]
        except Exception:
            cache[code] = {}
            return {}

    out = {}
    for kw, items in candidates.items():
        enriched = []
        for info in items:
            r = _get(info['code'])
            if not r: continue
            row = dict(info)
            row.update(r)
            enriched.append(row)
        # 너무 illiquid 제외, 변동성 너무 낮으면 제외
        enriched = [x for x in enriched if x.get('avg_value_5d_eok', 0) >= 0.5
                    and x.get('avg_abs_change_5d', 0) >= 1.0]
        enriched.sort(key=lambda x: -x.get('short_term_score', 0))
        if enriched:
            out[kw] = enriched[:5]
    return out


def check_recent_news(themes: List[dict], date_str: str, max_articles: int = 5):
    """각 테마 키워드로 최근 5일 일반 뉴스 검색 — 모멘텀 살아있는지 확인용"""
    print('\n   📰 키워드별 최근 뉴스 모멘텀 체크...')
    for theme in themes[:8]:
        kw = theme['keyword']
        if not (3 <= len(kw) <= 15):
            theme['recent_news_count'] = 0
            continue
        try:
            arts = search_news(kw, date_str, days_before=5, max_results=max_articles)
            theme['recent_news_count'] = len(arts)
            theme['recent_news_titles'] = [a['title'][:50] for a in arts[:3]]
        except Exception:
            theme['recent_news_count'] = 0


def generate_report(state: dict, lookback_days: int = 3, with_news: bool = False) -> dict:
    """전체 파이프라인 — 활성 테마 + 후보 + 점수 + (옵션) 최근 뉴스"""
    today = datetime.now().strftime('%Y%m%d')

    print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print(f'📡 주말 레이더 — 단기 트레이딩 후보 발굴')
    print(f'   기준일: {today}, 누적 시그널 {len(state["signals"])}건, lookback {lookback_days}일')
    print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    print('1️⃣ 활성 테마 식별...')
    themes = find_active_themes(state, lookback_days=lookback_days, min_members=2)
    print(f'   ✓ {len(themes)}개 활성 테마')

    print('\n2️⃣ KOSPI/KOSDAQ 종목 인덱스...')
    listing = _build_listing_index()
    print(f'   ✓ {len(listing)}종목')

    print('\n3️⃣ 테마별 관련 종목 후보 수집...')
    candidates = collect_related_candidates(themes, listing,
                                             already_seen=set(state['signals'].keys()),
                                             min_marcap=5_000_000_000)
    print(f'   ✓ 후보 있는 테마: {len(candidates)}')

    print('\n4️⃣ 단기 점수 계산...')
    scored = score_short_term(candidates, reference_date=today)

    if with_news:
        check_recent_news(themes, today)

    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'lookback_days': lookback_days,
        'active_themes': themes,
        'scored_candidates': scored,
    }


CLUSTER_KR = {
    'earnings_surprise': '💰 어닝 서프라이즈 (1분기 실적)',
    'semiconductor_backend': '🔬 반도체 후공정/장비',
    'humanoid_robotics': '🤖 휴머노이드 로보틱스',
    'cell_therapy': '🧬 세포치료제',
    'osteoarthritis_drug': '💊 퇴행성관절염 신약',
    'hantavirus_diagnostics': '🦠 한타바이러스 진단',
    'ai_infrastructure': '🖥 AI 인프라/데이터센터',
    'ai_semiconductor': '💎 AI 반도체',
    'realestate_redevelopment': '🏢 부동산 재개발/재건축',
    'realestate_sales': '🏘 부동산 분양',
    'm_and_a': '🤝 M&A',
    'dilutive_finance': '⚠️ 유상증자/CB (희석 위험)',
    'momentum_speculation': '📈 단순 모멘텀/풍문',
    'preferred_stock_interest': '📊 우선주 수급',
    'solar_energy_policy': '☀️ 태양광 정책',
    'power_grid_expansion': '⚡ 전력망 확장',
    'alzheimer_drug': '🧠 알츠하이머 신약',
    'cosmetics_growth': '💄 화장품 성장',
    'battery_components': '🔋 2차전지 소재',
    'cybersecurity': '🔒 사이버보안',
    'fda_approval': '✅ FDA 승인',
    'policy_beneficiary': '🏛 정책 수혜',
    'free_share_issue': '🎁 무상증자',
    'land_asset_play': '🌏 부동산 자산가치',
}


def print_report(data: dict):
    """터미널 출력"""
    print(f'\n\n{"="*70}')
    print(f'🎯 활성 테마 TOP 10 (momentum_score 순)')
    print(f'{"="*70}\n')

    scored = data['scored_candidates']
    for theme in data['active_themes'][:10]:
        kw = theme['keyword']
        label = CLUSTER_KR.get(kw, kw)
        if len(label) > 30: label = label[:30] + '...'
        cand_list = scored.get(theme['keyword'], [])
        if not cand_list:
            continue

        print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        print(f'🎯 [{theme["momentum_score"]:>5.1f}점] {label}  ({kw})')
        print(f'   등장일: {len(theme["date_span"])}일 / 종목: {len(theme["member_tickers"])}건 / 평균 등락 {theme["avg_change_pct"]:+.1f}%')
        if theme.get('recent_news_count') is not None:
            print(f'   최근 5일 뉴스: {theme.get("recent_news_count",0)}건')
        print(f'\n   ⚠️ 이미 오른 멤버 (추격 위험):')
        for m in theme['members'][:5]:
            h = m.get('history', [])
            traj = ' → '.join(f"{x['date'][-4:]}:{x['change_pct']:+.1f}%" for x in h)
            print(f'     {m.get("name"):16s} {traj}  [{m.get("confidence")}]')

        print(f'\n   📌 신규 후보 (안 오른 같은 테마):')
        for c in cand_list[:5]:
            score = c.get('short_term_score', 0)
            mc = c.get('marcap', 0) / 1e8
            print(f'     [{score:>6.1f}] {c["name"]:16s} ({c["code"]}) | '
                  f'시총 {mc:>5.0f}억 | 거래량 {c.get("vol_surge",0):>4.1f}배 | '
                  f'5일 {c.get("ret_5d",0):+.1f}% | 변동성 {c.get("avg_abs_change_5d",0):>4.1f}%')
        print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=3, help='lookback days (default 3)')
    parser.add_argument('--news', action='store_true', help='최근 뉴스 모멘텀 체크')
    args = parser.parse_args()

    state = load_state()
    data = generate_report(state, lookback_days=args.days, with_news=args.news)
    print_report(data)

    # JSON 저장
    out_path = Path(__file__).parent.parent / 'reports' / 'weekend_radar.json'
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(f'\n   💾 저장: {out_path}')
