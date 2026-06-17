"""Profitability backtest + scoring layer.

이 패키지는 reports/report_*.csv (뉴스 시그널) +
chart_analysis/output/chart_report_*.json (차트 지표) 를 입력으로 받아
시그널의 사후 수익률을 계산하고 키워드/테마별 성과를 누적한다.

각 모듈은 독립적으로 CLI 실행 가능하며 stock_assistant.build의
파이프라인 단계에 통합된다.
"""

__all__ = ["backtest", "scoring", "keyword_perf", "dashboard"]
