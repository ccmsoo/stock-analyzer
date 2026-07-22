# SYSTEM — 시스템 구조 한 장 정리

> 흐름을 놓쳤을 때 이 파일만 보면 되도록. (2026-07-23 기준)

**한 문장**: 매일 아침 기사를 읽고 → AI가 촉매를 점수화해서 → "아직 안 오른" 종목을
추천하고 → 그 추천의 실제 성적을 자동 채점해서 → 추천 화면에 다시 붙여주는 시스템.

## ① 매일 아침 자동 흐름 (cron: `.github/workflows/presurge_radar.yml`, 평일 08:00 KST)

```
워치리스트 551종목 (state/signals.json, high/medium)
   │
   ├─ 네이버 뉴스 수집 (collectors/news_collector, 최근 3일)
   ├─ AI(gpt-5-mini)가 기사 제목만 보고 촉매점수 0~10  ← tools/presurge_radar.py
   │     + 촉매 유형(M&A/수주/국책/임상FDA/...) + 문구(확정형/기대형) 분류
   │
   ├─ 필터: 점수≥6 · 오늘<5% · 5일<15% · 최근 5일 +12%↑ 급등 없음(신선도 가드)
   │     차트데이터: 토스 캔들 → 네이버 일봉 폴백 (candles_any)
   │
   ├─→ 텔레그램 발송 + reports/presurge_radar.json → UI /radar
   ├─→ state/radar_ledger.jsonl append  ← 포워드 페이퍼트레이드 장부 (핵심 자산)
   └─→ tools/build_track_record.py → reports/track_record.json → UI /track
```

- 금요일 16:00: `score_ledger.yml` — 주간 성적표 텔레그램.
- cron엔 토스 키 없음 → 네이버 폴백으로 차트필터·가드 동작 (graceful).

## ② 데이터 자산

| 파일 | 내용 |
|---|---|
| `state/radar_ledger.jsonl` | 픽 단위 장부 — 촉매·진입맥락을 미래 모르는 상태로 기록 (과적합 불가 검증 재료) |
| `tools/catalyst_events.py` | 픽을 (종목×촉매유형, cooldown 10거래일) 이벤트로 접음 — "촉매→결과" 쌍 |
| `reports/track_record.json` | 성적표 (전체/주별/유형별/시나리오 생존판) — cron이 매일 재계산 |

채점 규칙(공통): 신호 다음날 시초 진입 · N거래일 보유 · 손절 −10% ·
알파 = 같은 창 지수(KOSPI/KOSDAQ) 대비.

## ③ 검증으로 확정된 운용 규칙 (2026-06-25~07-22, 786픽)

**살아있는 것**
- 촉매 선진입: 이벤트 D+7 알파 +4.8%/65% — 알파는 보유일과 함께 커짐(1→7일)
- 신선도 가드(급등후 페이드 제외), 5거래일 창
- M&A+가드 (알파 +3.9/67%) — M&A는 **소문 단계**가 알파 (완료되면 소진)
- 촉매6+MA아래+KOSPI (n=117 최견고) / KOSPI 선별 ≫ KOSDAQ
- 넓은 손절 −10% (볼록 페이오프 보호), 추격 금지

**죽은 것 (재현 실패 확인)**
- 무버 추격(−12%) · 2차상승/눌림목 재진입(−5.8%) · 깊은눌림 1일 반등 · 수주+촉매8 홈런형 · 거래대금 50억+ 우위

**한계**
- 알파는 있으나 시장 헷지 없음 → 절대수익은 장세 종속. 하락 레짐(지수 MA20 아래)엔 비중 축소.
- 표본 전체가 하락장 하나 — 반등장 검증 대기. 세부 시나리오 등수는 n 수백까지 참고만.

## ④ UI (ui/, Next 15 — dev는 node@20 필수)

| 페이지 | 내용 |
|---|---|
| `/radar` | 오늘의 추천 — 유형 배지 + "이 유형 과거 n건 D+7 알파" + 급등후 경고 |
| `/track` | 성적표 — 보유기간/주별/유형별/시나리오 생존판 |
| `/portfolio` | 토스 실계좌 연동 |
| 나머지 | 구세대 페이지 (/, /themes, /chains, /signals, /opportunities) |

배포(Vercel)는 GitHub raw로 reports/*.json 읽음 → cron push만으로 갱신.

## ⑤ 운영 주의

- Python은 `venv/bin/python` (시스템 python3에 deps 없음).
- UI dev: `cd ui && /opt/homebrew/opt/node@20/bin/npm run dev`.
- 원격 main에 cron이 계속 push → 작업 전 `git fetch && git reset --hard origin/main`,
  코드만 커밋하고 생성물(json)은 cron 소유로 discard.
- 토스 키: 로컬 `.env`(+`ui/.env.local`) — **IP 화이트리스트** 방식이라 IP 바뀌면
  개발자센터에서 재등록 (빈 응답 = 403 IP 차단 의심).
- 분석 도구: `tools/backtest_*.py`, `catalyst_events.py`, `build_track_record.py`,
  `score_ledger.py`. 과거 뉴스는 `news_search`(네이버, ~90쿼리 차단 주의).
