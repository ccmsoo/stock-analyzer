# 기사 키워드 기회 보드

종목 중심 리포트가 아니라 `기사/키워드 -> 테마 -> 관련 종목 차트 랭킹` 순서로 보여주는 보드입니다.

## 무료 차트 데이터

현재는 프로젝트에 이미 설치된 `FinanceDataReader`를 사용합니다. API 키 없이 네이버/거래소 공개 데이터를 가져와 60일 미니 차트를 HTML에 직접 SVG로 삽입합니다.

주의할 점:

- 무료/공개 데이터라 장애나 구조 변경에 취약할 수 있습니다.
- 실시간 호가나 체결 데이터가 아니라 일봉 OHLCV 중심입니다.
- 매매 자동화가 아니라 판단 보조용으로 사용하는 것이 안전합니다.

## 실행

먼저 차트 분석을 최신화합니다.

```bash
./venv/bin/python -m chart_analysis.run_chart_analysis --report reports/report_20260514.csv
```

기회 보드 생성:

```bash
./venv/bin/python -m opportunity_engine.build_opportunity_board --report reports/report_20260514.csv
```

차트 삽입 없이 빠르게 만들고 싶으면:

```bash
./venv/bin/python -m opportunity_engine.build_opportunity_board --report reports/report_20260514.csv --no-charts
```

## 산출물

`opportunity_engine/output/` 아래에 생성됩니다.

- `opportunity_board.html`: 최신 보드
- `opportunity_board_YYYYMMDD.html`: 날짜별 보드
- `opportunities_YYYYMMDD.json`: 구조화된 기회 데이터

