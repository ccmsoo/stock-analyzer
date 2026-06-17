# 생활 기사 테마 레이더

일상 기사에서 주식시장으로 번질 수 있는 테마를 먼저 감지하고, 관련 종목 중 차트가 먼저 반응하는 종목을 랭킹합니다.

예:

- 코로나 재확산 -> 진단키트/백신/마스크
- 폭염/열대야 -> 냉방가전/전력수요
- 봄나들이 -> 자전거/레저
- 장마/태풍 -> 폐기물/복구/농업
- AI 데이터센터 -> 전력망/반도체/냉각

## 실행

실제 뉴스 검색 + 무료 OHLCV 차트:

```bash
./venv/bin/python -m life_theme_radar.build_life_radar
```

빠른 샘플 생성:

```bash
./venv/bin/python -m life_theme_radar.build_life_radar --sample
```

처리량 제한:

```bash
./venv/bin/python -m life_theme_radar.build_life_radar --max-themes 4 --max-queries 2 --max-stocks 5
```

## 산출물

`life_theme_radar/output/` 아래에 생성됩니다.

- `life_radar.html`: 최신 보드
- `life_radar_YYYYMMDD.html`: 날짜별 보드
- `life_radar_YYYYMMDD.json`: 구조화 데이터

## 데이터

- 뉴스: 기존 `collectors.general_news_collector.search_news()` 재사용
- 차트: `FinanceDataReader` 기반 무료 일봉 OHLCV
- 테마 사전: `theme_dictionary.json`

무료 데이터는 보조 분석용으로 충분하지만, 사이트 구조 변경이나 조회 실패 가능성이 있습니다.

