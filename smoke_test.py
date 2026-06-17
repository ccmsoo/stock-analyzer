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


def _snapshot(paths):
    """기존 파일 백업 + 새로 생긴 파일 추적용 (state 오염 방지)."""
    snaps = {}
    for p in paths:
        snaps[p] = p.read_bytes() if p.exists() else None
    return snaps


def _restore(snaps, created_after=None):
    """백업한 파일 복구 + smoke 가 새로 만든 파일 삭제."""
    for p, blob in snaps.items():
        if blob is None and p.exists():
            try:
                p.unlink()
            except Exception:
                pass
        elif blob is not None:
            p.write_bytes(blob)

    if created_after is not None:
        before, after = created_after
        for p in after - before:
            try:
                p.unlink()
            except Exception:
                pass


def main():
    # === 보호 영역 백업 ===
    state_path = ROOT / 'state' / 'signals.json'
    dash_path = ROOT / 'reports' / 'dashboard.json'
    rev_path = ROOT / 'reports' / 'reverse_candidates.json'

    # 기존 reports/report_*.csv|md|html 도 백업해야 함 — smoke 가 같은 영업일 리포트를 덮어쓰면
    # 실제 분석 결과가 사라진다.
    reports_dir = ROOT / 'reports'
    existing_reports = (
        list(reports_dir.glob('report_*.csv'))
        + list(reports_dir.glob('report_*.md'))
        + list(reports_dir.glob('report_*.html'))
    )
    snaps = _snapshot([state_path, dash_path, rev_path] + existing_reports)

    # 추가로 smoke 가 새로 만든 파일은 정리
    before_files = set(reports_dir.glob('*'))

    try:
        # AI 호출 mock + general news 끄기
        with patch('analyzers.gpt_analyzer.analyze_single_stock', side_effect=fake_analyze_single_stock), \
             patch('analyzers.claude_analyzer.analyze_single_stock', side_effect=fake_analyze_single_stock), \
             patch('main._make_client', return_value=None):
            import main as runner
            runner.main(top_n=5, use_general_news=False)

        # 산출물 검증
        files = list(reports_dir.glob('report_*.html')) + list(reports_dir.glob('report_*.md')) + list(reports_dir.glob('report_*.csv'))
        print(f"\n[검증] reports 산출물 {len(files)}개")
        for f in sorted(files)[-6:]:
            print(f"  - {f.name} ({f.stat().st_size:,} bytes)")

        assert dash_path.exists(), "dashboard.json 미생성"
        d = json.loads(dash_path.read_text())
        print(f"  - dashboard.json: 누적 종목 {len(d.get('signals', {}))}건")

        assert state_path.exists(), "state/signals.json 미생성"
        s = json.loads(state_path.read_text())
        print(f"  - state/signals.json: signals {len(s['signals'])}건, seen_articles {len(s['seen_articles'])}티커")

        print("\n✅ 스모크 테스트 통과")
    finally:
        # === 복구 ===
        after_files = set(reports_dir.glob('*'))
        _restore(snaps, created_after=(before_files, after_files))
        print("\n♻️  state/reports 원래 상태로 복구 완료")


if __name__ == '__main__':
    main()
