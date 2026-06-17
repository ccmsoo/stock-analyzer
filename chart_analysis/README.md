# 급등 종목 차트 분석 모듈

기존 뉴스/AI 분석 파이프라인과 분리된 독립 모듈입니다. 리포트 CSV 또는 현재 등락률 TOP 종목을 입력으로 받아 차트 지표와 진입 리스크를 계산합니다.

## 실행

최신 리포트 CSV 기준:

```bash
./venv/bin/python -m chart_analysis.run_chart_analysis
```

특정 리포트와 과거 캐시 사용:

```bash
./venv/bin/python -m chart_analysis.run_chart_analysis \
  --report reports/report_20260504.csv \
  --cache state/hist_cache_20260504_20260511.pkl
```

현재 네이버 등락률 TOP 수집 후 분석:

```bash
./venv/bin/python -m chart_analysis.run_chart_analysis --top 10
```

## 산출물

`chart_analysis/output/` 아래에 생성됩니다.

- `chart_report_YYYYMMDD.json`
- `chart_report_YYYYMMDD.csv`
- `chart_report_YYYYMMDD.html`

## 계산 항목

- 이동평균: 5/20/60/120일
- RSI14
- 20일 평균 대비 거래량/거래대금 배수
- 갭 상승률
- 윗꼬리 비율
- 종가 위치
- 20일선 이격도
- 20일/60일 신고가 돌파 여부
- 차트 패턴, 진입 리스크, 차트 점수, 코멘트

## 리스크 판정 의도

이 모듈은 매수 추천기가 아니라 `추격 매수 위험도 필터`입니다. 급등 종목이 좋은 뉴스 시그널을 갖고 있더라도 RSI 과열, 20일선 과이격, 긴 윗꼬리, 큰 갭상승이 겹치면 `high` 또는 `extreme` 리스크로 분류합니다.
