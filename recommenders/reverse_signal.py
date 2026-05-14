"""
역방향 추천 모듈
================
이미 분석한 시그널에서 "강한 테마"를 추출하고,
그 테마와 연관됐지만 아직 안 오른 종목을 후보로 제시.

입력: state/signals.json (누적 분석 결과)
출력: dashboard.json 의 'reverse_candidates' + 별도 HTML 페이지

알고리즘:
1. 시그널 클러스터: specific_signal 키워드 또는 main_theme + watch_keywords로 묶음
2. 강한 테마 = 같은 클러스터에 N≥2 종목 또는 N=1이지만 high confidence
3. 후보 종목 풀 = 각 시그널의 related_stocks 합집합
4. 필터:
   - 누적 state에 이미 등장한 종목 제외 (= 이미 오른 종목)
   - 거래량 너무 적은 종목 제외 (시가총액 100억 미만 등)
5. 정렬: 테마 강도 + 후보의 최근 변동성/거래대금
"""
import json
import re
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import FinanceDataReader as fdr

from state_manager import load_state
from recommenders.stopwords import CLUSTER_STOPWORDS, NAME_MATCH_STOPWORDS


# 종목명 정규화 (괄호·공백 제거 후 매칭 시도)
def _norm(name: str) -> str:
    return re.sub(r'[\s\(\)\[\]]+', '', name or '').upper()


def _build_listing_index() -> Dict[str, dict]:
    """KOSPI+KOSDAQ 종목 인덱스: 정규화 이름 → {code, name, market, marcap}"""
    idx = {}
    for market in ('KOSPI', 'KOSDAQ'):
        try:
            df = fdr.StockListing(market)
        except Exception:
            continue
        for _, row in df.iterrows():
            idx[_norm(row['Name'])] = {
                'code': row['Code'],
                'name': row['Name'],
                'market': market,
                'marcap': int(row.get('Marcap', 0) or 0),
            }
    return idx


def _cluster_signals(signals: Dict[str, dict]) -> List[dict]:
    """
    시그널을 테마별로 묶음.
    1차: specific_signal 안의 핵심 명사 키워드 (5글자 이상) 추출 후 매칭
    2차: watch_keywords 교집합
    """
    # 토큰 추출: specific_signal + watch_keywords 합쳐서 5글자 이상 단어
    token_to_tickers: Dict[str, set] = defaultdict(set)
    ticker_to_info: Dict[str, dict] = {}

    for ticker, s in signals.items():
        if s.get('confidence') == 'low':
            continue
        text = ' '.join([
            s.get('specific_signal', '') or '',
            ' '.join(s.get('watch_keywords', [])),
        ])
        # 한글/영문 토큰 추출
        tokens = set()
        for m in re.finditer(r'[가-힣]{3,}|[A-Za-z][A-Za-z0-9\-]{2,}', text):
            tok = m.group(0)
            if tok in CLUSTER_STOPWORDS:
                continue
            tokens.add(tok)
        for tok in tokens:
            token_to_tickers[tok].add(ticker)
        ticker_to_info[ticker] = s

    # N≥2 종목이 공유하는 토큰만 → 테마 후보
    themes: List[dict] = []
    seen_tickers_per_theme: List[frozenset] = []
    for tok, tickers in sorted(token_to_tickers.items(), key=lambda kv: -len(kv[1])):
        if len(tickers) < 2:
            continue
        frozen = frozenset(tickers)
        # 이미 있는 테마와 거의 같은 멤버면 패스 (서브셋 또는 동일)
        if any(frozen.issubset(prev) or prev.issubset(frozen) and len(frozen & prev) >= len(frozen) * 0.7 for prev in seen_tickers_per_theme):
            continue
        seen_tickers_per_theme.append(frozen)
        themes.append({
            'keyword': tok,
            'tickers': sorted(tickers),
            'members': [ticker_to_info[t] for t in tickers],
        })

    # N=1 high confidence 시그널도 단일 테마로 보존 (related_stocks 후보 풀로 쓰기 위해)
    used = set().union(*[set(t['tickers']) for t in themes]) if themes else set()
    for ticker, s in signals.items():
        if ticker in used or s.get('confidence') != 'high':
            continue
        themes.append({
            'keyword': s.get('specific_signal', '')[:30],
            'tickers': [ticker],
            'members': [s],
        })

    return themes


def _collect_related_pool(themes: List[dict]) -> Dict[str, set]:
    """테마별 관련주 후보 풀 (정규화된 이름) — AI가 추출한 related_stocks"""
    pool: Dict[str, set] = defaultdict(set)
    for theme in themes:
        key = theme['keyword']
        for m in theme['members']:
            for rs in m.get('related_stocks', []):
                if rs and rs.strip():
                    pool[key].add(_norm(rs))
    return pool


def _expand_pool_by_listing(pool: Dict[str, set], listing: Dict[str, dict],
                             themes: List[dict]) -> Dict[str, set]:
    """
    KOSPI/KOSDAQ 전체 종목명에 테마 키워드가 포함되는 경우 후보 풀에 추가.
    예: 키워드 '로봇' → 'XXX로보틱스', 'XXX로봇' 식 종목명 자동 합류.
    """
    expanded = {k: set(v) for k, v in pool.items()}
    for theme in themes:
        key = theme['keyword']
        # 5~12자 + NAME_MATCH_STOPWORDS 아닌 키워드만 종목명 매칭 (노이즈 방지)
        if not (5 <= len(key) <= 12):
            continue
        if key in NAME_MATCH_STOPWORDS:
            continue
        norm_kw = _norm(key)
        for norm_name, info in listing.items():
            if norm_kw in norm_name and len(norm_name) > len(norm_kw):
                expanded.setdefault(key, set()).add(norm_name)
    return expanded


def _filter_candidates(pool: Dict[str, set], listing: Dict[str, dict],
                        already_seen: set, min_marcap: int = 50_000_000_000) -> Dict[str, list]:
    """
    후보 풀에서:
    - 종목 인덱스에 존재
    - 시가총액 ≥ min_marcap (기본 500억)
    - 이미 분석된 종목(already_seen) 제외
    """
    result: Dict[str, list] = {}
    for theme_key, names in pool.items():
        cand = []
        for n in names:
            info = listing.get(n)
            if not info:
                continue
            if info['code'] in already_seen:
                continue
            if info['marcap'] < min_marcap:
                continue
            cand.append(info)
        cand.sort(key=lambda x: -x['marcap'])
        if cand:
            result[theme_key] = cand
    return result


def _enrich_with_recent_data(candidates: Dict[str, list], date_str: str) -> Dict[str, list]:
    """공격형 점수 — 최근 거래량 급증 + 변동성 큰 소형주 우선.
    리스크 감수하고 큰 수익 노리는 후보 발굴이 목적.
    """
    target = datetime.strptime(date_str, '%Y%m%d')
    start = (target - timedelta(days=45)).strftime('%Y-%m-%d')   # 비교용 충분히
    end = target.strftime('%Y-%m-%d')

    cache: Dict[str, dict] = {}

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
            prev_window = df.iloc[-20:-5] if len(df) >= 20 else df.iloc[:-5]
            if prev_window.empty:
                cache[code] = {}
                return {}

            avg_abs_change_5d = (recent5['Change'].abs() * 100).mean()
            avg_value_5d = recent5['Value'].mean() / 1e8     # 억원
            prev_value = prev_window['Value'].mean() / 1e8
            prev_vol = prev_window['Volume'].mean()
            recent_vol = recent5['Volume'].mean()

            vol_surge = (recent_vol / prev_vol) if prev_vol > 0 else 1.0
            value_surge = (avg_value_5d / prev_value) if prev_value > 0 else 1.0
            close_5d_return = (recent5['Close'].iloc[-1] / recent5['Close'].iloc[0] - 1) * 100

            # 위험-수익 점수: 거래량 급증 X 변동성 X 짧은 가격 모멘텀 부스트
            risk_score = vol_surge * max(avg_abs_change_5d, 0.5)
            if close_5d_return > 0:
                risk_score *= 1.0 + min(close_5d_return / 50, 0.5)   # 최대 +50% 부스트

            cache[code] = {
                'last_close': int(recent5['Close'].iloc[-1]),
                'last_change_pct': round(float(recent5['Change'].iloc[-1]) * 100, 2),
                'avg_abs_change_5d': round(avg_abs_change_5d, 2),
                'avg_value_5d_eok': round(avg_value_5d, 1),
                'vol_surge': round(vol_surge, 2),
                'value_surge': round(value_surge, 2),
                'close_5d_return': round(close_5d_return, 2),
                'risk_score': round(risk_score, 2),
            }
            return cache[code]
        except Exception:
            cache[code] = {}
            return {}

    enriched = {}
    for theme_key, items in candidates.items():
        out = []
        for info in items:
            r = _get(info['code'])
            if not r:
                continue
            info = dict(info)
            info.update(r)
            out.append(info)

        # 필터: 거래대금 0.5억 이상 (체결 가능성), 평균 변동성 1% 이상
        out = [x for x in out if x.get('avg_value_5d_eok', 0) >= 0.5
               and x.get('avg_abs_change_5d', 0) >= 1.0]
        # 점수 내림차순 (공격적인 순)
        out.sort(key=lambda x: x.get('risk_score', 0), reverse=True)
        if out:
            enriched[theme_key] = out[:5]
    return enriched


def generate_reverse_recommendations(date_str: str | None = None, top_per_theme: int = 5) -> dict:
    """전체 파이프라인. dashboard용 dict 반환."""
    state = load_state()
    signals = state.get('signals', {})
    if not signals:
        return {'generated_at': '', 'themes': []}

    if not date_str:
        # 가장 최근 등장 날짜
        latest = max((s.get('last_seen', '') for s in signals.values()), default='')
        date_str = latest

    print(f"   - 시그널 클러스터링 ({len(signals)}건 입력)...")
    themes = _cluster_signals(signals)
    print(f"   ✓ {len(themes)}개 테마")

    print("   - 종목 인덱스 (KOSPI+KOSDAQ) 로드...")
    listing = _build_listing_index()
    print(f"   ✓ {len(listing)}종목")

    pool = _collect_related_pool(themes)
    pool = _expand_pool_by_listing(pool, listing, themes)
    candidates = _filter_candidates(
        pool, listing,
        already_seen=set(signals.keys()),
        min_marcap=5_000_000_000,   # 50억 이상 (공격형: 소형주 포함)
    )
    print(f"   ✓ 후보 보유 테마: {len(candidates)}")

    print("   - 후보 최근 시세/거래대금 보강...")
    enriched = _enrich_with_recent_data(candidates, date_str)

    # 출력 정리
    out_themes = []
    for theme in themes:
        key = theme['keyword']
        cand = enriched.get(key, [])
        if not cand:
            continue
        out_themes.append({
            'keyword': key,
            'risen_members': [{
                'ticker': t,
                'name': signals[t].get('name'),
                'change_pct': signals[t].get('history', [{}])[-1].get('change_pct', 0),
                'specific_signal': signals[t].get('specific_signal', ''),
                'confidence': signals[t].get('confidence', ''),
            } for t in theme['tickers']],
            'candidates': cand,
        })
    # 상위 후보 많은 테마 먼저
    out_themes.sort(key=lambda x: -len(x['candidates']))

    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'reference_date': date_str,
        'themes': out_themes,
    }


def save_and_render(out_dir: Path):
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True)
    data = generate_reverse_recommendations()
    path = out_dir / 'reverse_candidates.json'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"   ✓ 저장: {path}")
    return data


if __name__ == "__main__":
    save_and_render(Path(__file__).parent.parent / 'reports')
