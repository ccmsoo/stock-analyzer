# Profitability — 수익성 검증 레이어

뉴스/AI/차트 시그널이 **실제로 돈이 됐는지** 사후 검증한다.
매수 추천기가 아니라, "AI가 설명한 시그널 → 실제 D+N 수익률 → 룰 기반 진입/청산
가정으로 단순 백테스트" 까지 자동화한 검증 도구.

## 구성

| 모듈 | 역할 |
|---|---|
| `backtest.py` | 사후수익률(D+1 시가 진입 기준) + 룰 기반 전략 시뮬레이션 |
| `scoring.py` | 50점 시작 + 가점/감점 → profitability_score, 진입 제외 평가 |
| `keyword_perf.py` | watch_keywords / specific_signal / main_theme 단위 성과 집계 |
| `dashboard.py` | 6 섹션 HTML 대시보드 |

## 데이터 소스 (기존 자산 재사용)

- `reports/report_YYYYMMDD.csv` — 뉴스/AI 시그널
- `chart_analysis/output/chart_report_YYYYMMDD.json` — 차트 지표
- `state/hist_cache_*.pkl` — 캐시된 OHLCV (있으면 우선)
- `state/signals.json` — consecutive_days 등 컨텍스트
- 위 캐시가 없으면 `FinanceDataReader` 직접 fetch (graceful fallback)

## CLI

```bash
# 단일 리포트
./venv/bin/python -m profitability.backtest --report reports/report_20260514.csv

# 기간 (backtest_trades_all.json/csv 도 함께 저장)
./venv/bin/python -m profitability.backtest --from 20260501 --to 20260515

# 키워드 성과 집계
./venv/bin/python -m profitability.keyword_perf

# HTML 대시보드
./venv/bin/python -m profitability.dashboard
```

## 진입/청산 룰 (`backtest.py` 상단 상수)

- **진입가**: D+1 시가 (다음 거래일 시초가)
- **청산 조건** (먼저 도달하는 것 우선):
  1. 손절: 진입가 −4%
  2. 익절: 진입가 +8%
  3. 시간 청산: 진입 후 5거래일 종가
- 같은 날 손절·익절 모두 발동 시 보수적 가정으로 **손절 우선**
- `strategy_exit_reason` 값: `take_profit | stop_loss | time_exit | not_eligible | data_missing`

### 진입 제외 조건 (`scoring.is_strategy_eligible`)

다음 중 하나라도 해당하면 `strategy_eligible=False`, `exclusion_reasons` 에 사유 기록:

- `confidence_low`: confidence == low
- `trigger_unknown`: trigger_type == unknown
- `entry_risk_high`: entry_risk in {high, extreme}
- `chart_score_low`: chart_score < 60
- `value_ratio_low`: value_ratio_20d < 1.5
- `upper_shadow_overheat`: upper_shadow_pct ≥ 45
- `distance_ma20_overheat`: distance_ma20_pct ≥ 25

## profitability_score 산식

`BASE = 50` 에서 시작 → 가점/감점 누적 → 0~100 clamp.

### 가점

| 조건 | 가점 |
|---|---:|
| confidence == high | +12 |
| confidence == medium | +6 |
| trigger_type ∈ {disclosure, earnings, contract, policy} | +10 |
| chart_score ≥ 80 | +12 |
| chart_score ≥ 70 | +8 |
| high_60d_breakout | +8 |
| high_20d_breakout | +6 |
| value_ratio_20d ≥ 3 | +8 |
| close_position_pct ≥ 70 | +5 |

### 감점

| 조건 | 감점 |
|---|---:|
| confidence == low | −25 |
| trigger_type == unknown | −20 |
| entry_risk ∈ {high, extreme} | −20 |
| RSI14 ≥ 80 | −10 |
| upper_shadow_pct ≥ 45 | −12 |
| distance_ma20_pct ≥ 25 | −12 |
| chart_score < 60 | −15 |
| value_ratio_20d < 1.5 | −10 |

### score_label

- 80 이상 → `strong_watch`
- 65 이상 → `watch`
- 50 이상 → `neutral`
- 35 이상 → `weak`
- 그 미만 → `avoid`

## 키워드 quality_label (`keyword_perf.py`)

- `promising`: appearances ≥ 3 AND avg_return_5d > 3 AND win_rate_5d ≥ 60
- `weak`: appearances ≥ 3 AND avg_return_5d < −2
- `noisy`: appearances ≥ 3 AND win_rate_5d < 40
- `unproven`: 그 외 (표본 부족)

## 대시보드 섹션

1. **오늘의 관찰 우선 후보** — profitability_score 상위
2. **진입 제외 후보** — exclusion_reasons 노출
3. **전략 백테스트 요약 (누적)** — 총 거래/적격/평균 수익률/승률/청산 사유 분포
4. **최근 성과 좋은 키워드** — promising
5. **최근 성과 약한 키워드** — weak / noisy
6. **데이터 오류·누락** — note, errors

## 산출물

```
profitability/output/
├── backtest_trades_YYYYMMDD.json    # 일자별
├── backtest_trades_YYYYMMDD.csv
├── backtest_trades_all.json         # --from/--to 사용 시
├── backtest_trades_all.csv
├── keyword_performance_YYYYMMDD.json
├── profitability_dashboard_YYYYMMDD.html
└── profitability_dashboard.html     # 최신 사본
```

## 한계 / 주의사항

- **이 시스템은 매매 추천이 아닙니다.** profitability_score 는 관찰 우선순위.
- **백테스트는 과거 데이터** 기반이며 미래 성과를 보장하지 않습니다.
- D+10 까지 OHLCV가 없는 시그널 (예: 오늘 시그널) 은 사후 지표 None.
- 키워드 매칭은 string equality. 동의어/임베딩 매칭은 추후.
- 진입/청산은 **장중 가격 순서를 모르므로** 보수적으로 손절을 우선 가정.
- 슬리피지/수수료는 계산하지 않음 (단순 시뮬레이션).
- **실제 거래 전 종이매매로 검증**하세요.
