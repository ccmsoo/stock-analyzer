"""
본문 기반 지엽 키워드 추출 (2차 분석)
=========================================
1차 분석(GPT-4o)이 끝난 high/medium confidence 종목에 대해서만,
관련 기사 본문 5~7건을 fetch하고 GPT-4o-mini에게 지엽적 명사구만 뽑게 함.

목적:
- 제품/약품 코드 (AP209, ALT-B4, HBM4 12단)
- 기술명 (히알루로니다제, CPO 본딩)
- 사업장/지명 (압구정 명품관, 군산 제2공장)
- 인물/기관 (머크, FDA, 엔비디아)
- 계약 규모/일정 (100억 공급계약, 8월 환자 투약)

이 단어들이 다른 종목 기사에 또 등장하면 자동 매칭 가능.
mini 모델이라 비용 ~$0.001/건, 매일 30종목 분석 시 ~$0.03/일.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')
except ImportError:
    pass

from openai import OpenAI

from state_manager import load_state, save_state
from collectors.news_collector import collect_news_for_stock, get_article_body


SYSTEM = """당신은 기사 본문에서 매수 신호가 될 만한 **고유 명사구**만 추출하는 정밀 키워드 추출기입니다.

추출 카테고리 (각 1~3개):
1. **products** — 제품/약품/기술 코드명. 예: 'AP209', 'ALT-B4', 'HBM4 12단', 'CPO 본딩', '엔비디아 Rubin GPU'
2. **partners** — 계약 상대방/협력사/규제기관. 예: '머크', 'FDA', '엔비디아', 'BMS', 'LS전선'
3. **places** — 사업장/공장/부동산. 예: '압구정 명품관', '군산 제2공장', '서울고속터미널'
4. **events** — 일정/규모 (날짜·금액 포함된 명사구). 예: '8월 환자 투약', '100억 공급계약', '2027년 상용화'
5. **people** — 인물/직책. 예: '구윤철 부총리', '이재용 회장'

규칙:
- 반드시 **본문에 실제 등장하는 표현 그대로**. 변형/요약 X.
- 회사명 자체(예: 'SK하이닉스')는 partners에 회사 외부 파트너만 — 분석 대상 종목 본인 회사명은 제외.
- 한국어/영문 혼합 OK.
- 너무 일반적 단어 (예: '계약', '발표') 단독 금지. 반드시 한정어 포함된 명사구.
- 카테고리별로 본문에서 안 나오면 빈 배열.

JSON 형식:
{
  "products": ["..."],
  "partners": ["..."],
  "places": ["..."],
  "events": ["..."],
  "people": ["..."]
}"""


def _build_prompt(stock_name: str, articles_with_body: list) -> str:
    """기사 본문들을 묶어서 한 번에 mini에 보냄"""
    parts = [f'종목: {stock_name}\n분석할 기사 본문들:\n']
    for i, a in enumerate(articles_with_body, 1):
        body = a.get('body', '')[:1200]  # 1건당 1200자
        parts.append(f"\n--- 기사 {i} [{a.get('date','')}] {a['title']} ---\n{body}\n")
    parts.append('\n위 본문에서 카테고리별 명사구를 추출하세요. JSON 응답.')
    return ''.join(parts)


def _merge_keywords(existing: dict, new: dict) -> dict:
    """기존 deep_keywords와 새로 추출한 거 머지 (중복 제거)"""
    if not existing:
        existing = {}
    out = {}
    for cat in ('products', 'partners', 'places', 'events', 'people'):
        merged = list({*(existing.get(cat, []) or []), *(new.get(cat, []) or [])})
        out[cat] = merged
    return out


def extract_deep_for_stock(client, ticker: str, sig: dict, max_articles: int = 5) -> dict:
    """한 종목에 대해 본문 fetch + mini 추출"""
    target_date = sig.get('last_seen', '')
    if not target_date:
        return {}
    try:
        articles = collect_news_for_stock(ticker, target_date, articles_per_stock=max_articles, days_before=7)
    except Exception as e:
        return {'error': f'news fetch: {e}'}
    if not articles:
        return {}

    # 상위 max_articles 건 본문 fetch (병렬)
    sel_arts = articles[:max_articles]
    def _fetch_body(a):
        a['body'] = get_article_body(a['link'], max_chars=1500)
        return a
    with ThreadPoolExecutor(max_workers=3) as ex:
        sel_arts = list(ex.map(_fetch_body, sel_arts))
    sel_arts = [a for a in sel_arts if a.get('body') and len(a['body']) > 200]
    if not sel_arts:
        return {}

    # mini 호출
    try:
        resp = client.chat.completions.create(
            model='gpt-5-nano',
            max_completion_tokens=1500,
            reasoning_effort='minimal',   # 텍스트 추출, 추론 불필요
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': _build_prompt(sig.get('name','?'), sel_arts)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        return data
    except Exception as e:
        return {'error': f'ai: {str(e)[:80]}'}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='이미 deep_keywords 있는 종목도 재추출')
    parser.add_argument('--max-stocks', type=int, default=None, help='처리할 종목 수 제한 (테스트용)')
    parser.add_argument('--confidence', default='high', choices=['high', 'medium', 'both'],
                        help='어떤 신뢰도까지 처리할지 (기본: high만)')
    args = parser.parse_args()

    state = load_state()
    sigs = state.get('signals', {})

    if args.confidence == 'high':
        conf_filter = {'high'}
    elif args.confidence == 'medium':
        conf_filter = {'medium'}
    else:
        conf_filter = {'high', 'medium'}

    targets = []
    for t, s in sigs.items():
        if s.get('confidence') not in conf_filter:
            continue
        if not args.force and s.get('deep_keywords'):
            continue
        targets.append((t, s))

    if args.max_stocks:
        targets = targets[:args.max_stocks]

    print(f'deep_keywords 추출 대상: {len(targets)}건 ({args.confidence})')
    if not targets:
        print('   ✓ 새로 처리할 종목 없음')
        return

    if not os.environ.get('OPENAI_API_KEY'):
        print('❌ OPENAI_API_KEY 없음'); return

    client = OpenAI()
    success = error = 0

    print('병렬 3 (본문 fetch 부담 고려)...')
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(extract_deep_for_stock, client, t, s): (t, s) for t, s in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                data = fut.result()
            except Exception as e:
                error += 1
                continue
            # 어떤 ticker였는지 역추적
            ticker, sig = futures[fut]
            name = sig.get('name')
            if 'error' in data:
                error += 1
                print(f'  [{i}/{len(targets)}] ❌ {name}: {data["error"]}')
                continue
            if not data or all(not v for v in data.values()):
                print(f'  [{i}/{len(targets)}] · {name}: 추출 결과 없음')
                continue
            # 머지 + 저장
            existing = sigs[ticker].get('deep_keywords', {})
            sigs[ticker]['deep_keywords'] = _merge_keywords(existing, data)
            success += 1
            total = sum(len(v) for v in data.values() if isinstance(v, list))
            print(f'  [{i}/{len(targets)}] ✓ {name:18s} → {total}개 키워드')
            for cat in ('products', 'partners', 'places', 'events'):
                items = data.get(cat, [])
                if items:
                    print(f'        {cat}: {items}')

    save_state(state)
    print(f'\n완료: 성공 {success} / 오류 {error}')

    from reporters.report_generator import _write_dashboard_data
    _write_dashboard_data(Path(__file__).parent.parent / 'reports', state)


if __name__ == '__main__':
    main()
