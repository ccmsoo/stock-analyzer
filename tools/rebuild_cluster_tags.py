"""
누적 시그널에 cluster_tag 추가
================================
AI에게 "이 종목을 다른 종목과 묶을 단일 라벨(snake_case)"을 직접 받는다.
- 휴머노이드 종목들은 모두 `humanoid_robotics`로 통일
- 한타바이러스 종목들은 모두 `hantavirus_diagnostics`
- 토큰 매칭의 모호함 대신 명시적 카테고리로 클러스터링
"""
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


SYSTEM = """당신은 한국 주식 테마 분류가입니다.
주어진 종목 분석을 보고, 이 종목을 다른 종목과 묶을 단일 cluster_tag를 영어 snake_case로 만드세요.

조건:
1. 같은 트리거/테마의 종목들은 **반드시 동일한 cluster_tag** 가지도록.
2. 너무 일반적이지 않고, 너무 지엽적이지도 않은 중간 단위.
3. 형식: 영어 snake_case, 2~4 단어. 예시:
   - 휴머노이드/2족보행 로봇 → `humanoid_robotics`
   - 한타바이러스 진단/백신 → `hantavirus_diagnostics`
   - HBM 메모리/D램 → `hbm_memory`
   - 반도체 후공정/테스트 → `semiconductor_backend`
   - AI 데이터센터/인프라 → `ai_datacenter`
   - 알츠하이머 신약 → `alzheimer_drug`
   - 어닝 서프라이즈 (실적만) → `earnings_surprise`
   - 부동산 재개발/재건축 → `realestate_redevelopment`
   - M&A/지분 인수 → `m_and_a`
   - 유상증자/CB → `dilutive_finance`
   - 단순 수급/풍문 → `momentum_speculation`
   - 정책 수혜 → `policy_beneficiary`

4. JSON 형식: {"cluster_tag": "snake_case_string", "rationale": "한 줄 근거"}
"""


def _build_prompt(sig):
    return f"""다음 종목의 cluster_tag를 추출하세요.

종목: {sig.get('name')} ({sig.get('market')})
1차 테마: {sig.get('main_theme', '-')}
지엽적 시그널: {sig.get('specific_signal', '-')}
트리거 유형: {sig.get('trigger_type', '-')}
추정 근거: {sig.get('reasoning', '-')}
기존 watch_keywords: {sig.get('watch_keywords', [])}

JSON으로 cluster_tag + rationale 반환:"""


def rebuild_one(client, ticker, sig):
    try:
        resp = client.chat.completions.create(
            model='gpt-5-nano',
            max_completion_tokens=500,
            reasoning_effort='minimal',
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': _build_prompt(sig)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        return ticker, data.get('cluster_tag', ''), data.get('rationale', ''), None
    except Exception as e:
        return ticker, None, None, str(e)[:80]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='이미 cluster_tag 있는 종목도 다시 추출')
    args = parser.parse_args()

    state = load_state()
    sigs = state.get('signals', {})

    # high/medium 중 cluster_tag 없는 종목만 (또는 --force면 전부)
    targets = []
    for t, s in sigs.items():
        if s.get('confidence') not in ('high', 'medium'):
            continue
        if not args.force and s.get('cluster_tag'):
            continue
        targets.append((t, s))
    print(f'cluster_tag 추출 대상: {len(targets)}건 (전체 {len(sigs)}건 중)')
    if not targets:
        print('   ✓ 새로 처리할 종목 없음')
        return

    if not os.environ.get('OPENAI_API_KEY'):
        print('❌ OPENAI_API_KEY 없음'); return

    client = OpenAI()
    updated = 0

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(rebuild_one, client, t, s): (t, s) for t, s in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, tag, rationale, err = fut.result()
            name = sigs[ticker].get('name')
            if err or not tag:
                print(f'  [{i}/{len(targets)}] ❌ {ticker} {name}')
                continue
            sigs[ticker]['cluster_tag'] = tag
            updated += 1
            if i <= 10 or i % 15 == 0:
                print(f'  [{i}/{len(targets)}] {name:18s} → {tag}  ({rationale[:40]})')

    save_state(state)
    print(f'\n완료: {updated}건 cluster_tag 추가')

    # 태그별 종목 수 통계
    from collections import Counter
    tags = Counter(s.get('cluster_tag', '-') for s in sigs.values() if s.get('cluster_tag'))
    print('\n=== cluster_tag 분포 (TOP 15) ===')
    for tag, n in tags.most_common(15):
        print(f'  {n:>3}건  {tag}')

    from reporters.report_generator import _write_dashboard_data
    _write_dashboard_data(Path(__file__).parent.parent / 'reports', state)


if __name__ == '__main__':
    main()
