# 뉴스 x 차트 통합 대시보드

`reports/report_YYYYMMDD.csv`의 뉴스/AI 분석과 `chart_analysis/output/chart_report_YYYYMMDD.json`의 차트 분석을 합쳐 매매 후보를 시각화합니다.

## 실행

최신 리포트 기준:

```bash
./venv/bin/python -m visual_dashboard.build_dashboard
```

특정 날짜 기준:

```bash
./venv/bin/python -m visual_dashboard.build_dashboard \
  --report reports/report_20260514.csv \
  --chart chart_analysis/output/chart_report_20260514.json
```

## 산출물

`visual_dashboard/output/`에 생성됩니다.

- `dashboard.html`: 최신 대시보드 바로가기
- `dashboard_YYYYMMDD.html`: 날짜별 대시보드
- `combined_signals_YYYYMMDD.json`: 통합 점수 데이터

## 판정 라벨

- `strong_watch`: 뉴스와 차트가 모두 강한 핵심 관찰 후보
- `wait_pullback`: 뉴스는 강하지만 차트가 과열되어 눌림 대기
- `theme_watch`: 차트 수급은 강하지만 뉴스 근거가 약한 테마성 관찰
- `need_chart`: 뉴스는 강하지만 차트 분석이 아직 없음
- `avoid`: 둘 다 약하거나 현재 진입 매력이 낮음

