# Stock Assistant

사용자가 실제로 만지는 통합 진입점입니다.

내부 엔진은 계속 분리되어 있습니다.

- `chart_analysis`: 차트 분석
- `visual_dashboard`: 뉴스 x 차트 통합 후보
- `opportunity_engine`: 기사 키워드 기회 카드
- `life_theme_radar`: 생활 기사 테마 레이더
- `unified_dashboard`: 홈 UI

하지만 실행과 화면은 하나로 묶습니다.

## 전체 갱신

```bash
./venv/bin/python -m stock_assistant.build
```

## 빠른 데모

생활 뉴스 검색 대신 샘플 기사로 빠르게 만듭니다.

```bash
./venv/bin/python -m stock_assistant.build --life-sample
```

## 산출물

- `assistant_home.html`: 루트 메인 홈
- `stock_assistant/output/index.html`: 통합 홈
- `stock_assistant/output/build_manifest.json`: 빌드 결과

