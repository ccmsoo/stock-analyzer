"""
누적 state/signals.json 의 watch_keywords 재추출
==================================================
기존 분석 결과(specific_signal + reasoning)를 입력으로 주고,
새 프롬프트(상위 테마어 + 지엽 명사구 조합)로 watch_keywords만 갱신.

뉴스 fetch 없이 분석 텍스트만 사용 → 빠르고 저렴 ($0.005/건)
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


SYSTEM = """당신은 한국 주식 시그널 키워드 추출가입니다.
주어진 분석 결과를 보고, 클러스터링용 watch_keywords를 정확히 4~6개 추출하세요.

조건:
1. **상위 테마어 (2~3개)** — 다른 종목과 묶일 보편 키워드.
   예: "휴머노이드", "AI 데이터센터", "재개발", "한타바이러스", "어닝 서프라이즈",
       "M&A", "FDA 승인", "유상증자", "수주", "신약", "정책 수혜", "구조조정"
2. **지엽적 명사구 (2~3개)** — 이 종목 고유의 구체 트리거.
   예: "HBM4 12단", "AP209 8월 투약", "압구정 명품관", "CPO 본딩 첫 수주"
3. 종목명·회사명은 절대 포함하지 말 것 (예: '코스모로보틱스' X, '휴머노이드' O)
4. '관련주', '테마주', '실적', '발표' 같은 공허한 단어 금지
5. JSON 형식 응답 — {"watch_keywords": ["..", ".."]}
"""


def _build_prompt(sig: dict) -> str:
    return f"""다음 종목 분석 결과를 보고 watch_keywords만 재추출하세요.

종목: {sig.get('name')} ({sig.get('market')})
1차 테마: {sig.get('main_theme', '-')}
지엽적 시그널: {sig.get('specific_signal', '-')}
트리거 유형: {sig.get('trigger_type', '-')}
추정 근거: {sig.get('reasoning', '-')}

JSON 형식으로 watch_keywords 4~6개 반환:"""


def rebuild_one(client, ticker, sig):
    try:
        resp = client.chat.completions.create(
            model='gpt-5-nano',
            max_completion_tokens=800,
            reasoning_effort='minimal',
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': _build_prompt(sig)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        kws = data.get('watch_keywords', [])
        return ticker, kws[:6], None
    except Exception as e:
        return ticker, None, str(e)[:80]


def main():
    state = load_state()
    sigs = state.get('signals', {})

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='이미 4+ 키워드 있는 종목도 재추출')
    args, _ = parser.parse_known_args()

    # high/medium 중 watch_keywords가 부족한 종목만 (--force면 전부)
    targets = []
    for t, s in sigs.items():
        if s.get('confidence') not in ('high', 'medium'):
            continue
        if not args.force and len(s.get('watch_keywords', [])) >= 4:
            continue
        targets.append((t, s))
    print(f'재추출 대상: {len(targets)}건 / 전체 {len(sigs)}건')
    if not targets:
        print('   ✓ 새로 처리할 종목 없음')
        return

    if not os.environ.get('OPENAI_API_KEY'):
        print('❌ OPENAI_API_KEY 없음')
        return

    client = OpenAI()
    updated = 0
    errors = 0

    print('AI 호출 시작 (병렬 5)...')
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(rebuild_one, client, t, s): (t, s) for t, s in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, new_kws, err = fut.result()
            name = sigs[ticker].get('name')
            if err:
                errors += 1
                print(f'  [{i}/{len(targets)}] ❌ {ticker} {name}: {err}')
                continue
            if new_kws:
                sigs[ticker]['watch_keywords'] = new_kws
                updated += 1
                if i % 10 == 0 or i <= 5:
                    print(f'  [{i}/{len(targets)}] ✓ {name}: {new_kws}')

    save_state(state)
    print(f'\n완료: 갱신 {updated} / 오류 {errors}')

    # 대시보드 데이터도 갱신
    from reporters.report_generator import _write_dashboard_data
    _write_dashboard_data(Path(__file__).parent.parent / 'reports', state)


if __name__ == '__main__':
    main()
