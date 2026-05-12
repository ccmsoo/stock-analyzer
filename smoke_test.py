"""
엔드투엔드 스모크 테스트 (API 호출 없이)
==========================================
AI 호출을 가짜 응답으로 갈음하고 전체 파이프라인이 도는지 확인.
- 등락률 수집 (네이버 라이브) → 뉴스 수집 → 가짜 분석 → 리포트 생성 → state 저장
- reports/report_*.html, dashboard.json 이 정상 생성되는지 검증
"""
import os
import sys
import json
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


FAKE_SIGNALS = [
    # (main_theme, specific_signal, trigger_type, confidence)
    ('반도체', 'HBM4 엔비디아 단독 공급', 'contract', 'high'),
    ('2차전지', 'LFP 양극재 북미 합작', 'contract', 'high'),
    ('바이오', '키트루다 SC 제형 FDA 승인', 'disclosure', 'high'),
    ('정책', '산자부 K-방산 수출 패키지', 'policy', 'medium'),
    ('수급', '외국인 5거래일 연속 순매수', 'technical', 'medium'),
    ('-', '원인 불명 (단순 테마성)', 'unknown', 'low'),
]


def fake_analyze_single_stock(client, stock, articles, model=None):
    """ticker 끝자리로 가짜 분석 결과 결정"""
    idx = int(stock['ticker'][-1]) % len(FAKE_SIGNALS)
    theme, signal, ttype, conf = FAKE_SIGNALS[idx]
    return {
        'main_theme': theme,
        'specific_signal': signal,
        'trigger_type': ttype,
        'trigger_date': '2026.05.10',
        'trigger_lag_days': 2,
        'reasoning': f'스모크 테스트용 모의 분석. 기사 {len(articles)}건 입력.',
        'related_stocks': [f'연관A_{idx}', f'연관B_{idx}'],
        'confidence': conf,
        'watch_keywords': [f'키워드_{theme}', f'세부_{signal[:6]}'],
    }


def main():
    # AI 호출 mock + general news 끄기 (속도/네트워크 부담↓)
    with patch('analyzers.gpt_analyzer.analyze_single_stock', side_effect=fake_analyze_single_stock), \
         patch('analyzers.claude_analyzer.analyze_single_stock', side_effect=fake_analyze_single_stock), \
         patch('main._make_client', return_value=None):
        import main as runner
        runner.main(top_n=5, use_general_news=False)

    # 산출물 검증
    reports = ROOT / 'reports'
    files = list(reports.glob('report_*.html')) + list(reports.glob('report_*.md')) + list(reports.glob('report_*.csv'))
    print(f"\n[검증] reports 산출물 {len(files)}개")
    for f in sorted(files)[-6:]:
        print(f"  - {f.name} ({f.stat().st_size:,} bytes)")

    dashboard = reports / 'dashboard.json'
    assert dashboard.exists(), "dashboard.json 미생성"
    d = json.loads(dashboard.read_text())
    print(f"  - dashboard.json: 누적 종목 {len(d.get('signals', {}))}건")

    state = ROOT / 'state' / 'signals.json'
    assert state.exists(), "state/signals.json 미생성"
    s = json.loads(state.read_text())
    print(f"  - state/signals.json: signals {len(s['signals'])}건, seen_articles {len(s['seen_articles'])}티커")

    print("\n✅ 스모크 테스트 통과")


if __name__ == '__main__':
    main()
