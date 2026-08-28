# 신호 레이더 적용 가이드

> 지금 cron이 매일 아침 쓰는 **AI 촉매점수는 역엣지**다
> (같은날·같은시장 대조군 대비 −2.7%p, t=−6.2 · [audit_20260820.md](audit_20260820.md)).
> 이걸 검증된 신호로 교체하는 절차. **전환은 소유자가 결정한다.**

## 무엇으로 바꾸는가

| | 현재 (presurge_radar) | 교체 (signal_radar) |
|---|---|---|
| 신호 | AI가 뉴스 제목 읽고 촉매점수 0~10 | 20일 모멘텀 상위 + 기관 5일 순매수 **아닌** 종목 |
| 검증 | **t = −6.2 (역엣지)** | 동일가중 대비 **+44~50%p, 시작일 10/10 승** |
| 비용 | LLM API 매일 호출 | **0원** (가격·수급만) |
| 한계 | — | 시총가중 지수는 **못 이긴다** (연도별 3/6) |

**바꾸면 확실히 나아진다** — 음의 엣지를 양의(상대) 엣지로 바꾸는 것이므로.
**바꿔도 지수를 이기지는 못한다** — 그건 별개 문제다.

## 필요한 것

수급 데이터(`state/flows.pkl`). 최초 1회 전체 수집 후 매일 증분 갱신한다.

```bash
# 최초 1회 — 약 17분
venv/bin/python -m tools.fetch_flows --pages 25 --tickers 400

# 매일 — 약 2~3분 (최근 2페이지=40거래일만 다시 받아 병합)
venv/bin/python -m tools.fetch_flows --pages 2 --tickers 400 --update
```

## 실행

```bash
venv/bin/python -m tools.signal_radar --uni 100 --top 12 --json reports/signal_radar.json
```

출력은 `reports/presurge_radar.json`과 **동일 스키마**라 UI(`ui/src/lib/data.ts`)가
경로만 바꾸면 그대로 읽는다. 진단용 필드(`mom20`, `inst5`, `rank_mom`, `rank_inst`)가 추가돼 있다.

## ✅ cron 활성화 완료 (2026-08-28)

`.github/workflows/presurge_radar.yml`에 두 단계를 추가했다. **기존 촉매 레이더는
그대로 두고 나란히 생성**한다 — 며칠 비교한 뒤 전환을 결정하면 된다.

```yaml
      - name: 수급 수집 (외인·기관, 약 2분)
        run: python -u -m tools.fetch_flows --pages 3 --tickers 400 --out /tmp/flows_daily.pkl || true

      - name: 신호 레이더
        run: python -u -m tools.signal_radar --flows /tmp/flows_daily.pkl                --uni 100 --top 12 --json reports/signal_radar.json || true
```

커밋 대상에 `reports/signal_radar.json` 추가됨.

**설계 결정 세 가지**

1. **상태 파일을 커밋하지 않는다.** `flows.pkl`은 10MB라 매일 커밋하면 저장소가 터진다.
   `/tmp`에 받고 버린다. 신호에 필요한 건 25일치뿐이라 **3페이지(60일)면 충분**하고,
   실측으로 500일 이력본과 **픽이 12/12 동일**했다.
2. **유니버스는 `state/universe.json`(커밋됨)에서 읽는다.**
   원래 `state/deep_px.pkl.meta.json`을 읽었는데 그건 gitignore라 CI에서 죽는다.
   (실제로 이 버그를 잡았다 — cron 환경을 시뮬레이션해서 확인)
3. **텔레그램은 건드리지 않았다.** 매일 폰으로 가는 알림이라 며칠 나란히 보고 결정할 일이다.

**소요 시간**: 수급 수집 약 2분 + 신호 계산 수 초. 기존 워크플로에 2분 추가.

## 텔레그램까지 전환하려면 (아직 안 함)

`.github/workflows/presurge_radar.yml`의 촉매 레이더 단계에서 `--telegram`을 떼고,
신호 레이더에 텔레그램 발송을 붙이면 된다. **며칠 `reports/signal_radar.json`과
`reports/presurge_radar.json`을 나란히 본 뒤 결정할 것.**

## UI에 붙이려면

`ui/src/lib/data.ts:245`의 경로를 `reports/signal_radar.json`으로 바꾸면 된다.
스키마가 동일하고 진단 필드(`mom20`, `inst5`, `rank_mom`, `rank_inst`)가 추가돼 있다.

## (참고) 수동 실행

```bash
venv/bin/python -m tools.fetch_flows --pages 3 --tickers 400 --out /tmp/f.pkl
venv/bin/python -m tools.signal_radar --flows /tmp/f.pkl --uni 100 --top 12
```

## 원래 문서 — cron 활성화 방법 (이미 적용됨)

`.github/workflows/presurge_radar.yml`의 레이더 실행 단계 **뒤에** 아래를 추가하면
기존 레이더를 건드리지 않고 나란히 생성된다. 며칠 비교해본 뒤 전환을 결정하면 된다.

```yaml
      - name: 수급 증분 갱신
        run: python -u -m tools.fetch_flows --pages 2 --tickers 400 --update || true

      - name: 신호 레이더
        run: python -u -m tools.signal_radar --uni 100 --top 12 --json reports/signal_radar.json || true
```

그리고 커밋 단계에 `reports/signal_radar.json state/flows.pkl` 추가.

**주의**: `state/flows.pkl`은 10MB급이라 매일 커밋하면 저장소가 빠르게 커진다.
cron에서 쓸 거면 아티팩트 캐시나 별도 브랜치를 쓰거나, 매일 새로 수집(17분)하는 편이 낫다.

**텔레그램은 자동으로 바뀌지 않는다.** 알림까지 교체하려면 워크플로의
`tools.presurge_radar --telegram` 단계를 직접 바꿔야 한다 — 매일 폰으로 가는 것이므로
며칠 나란히 돌려보고 판단할 것.

## 전환 전 반드시 알 것

- 이 신호는 **"종목을 직접 고를 거라면 이렇게 골라라"**이지 **"지수 ETF보다 낫다"가 아니다.**
- **하락장에 특히 약하다** — 2022년 지수 대비 −16.9%, 시작일 10개 전부 패배.
  롱온리 바스켓이라 모멘텀 크래시를 그대로 맞는다.
- 지수 MA100 추세필터를 붙이면 2022는 +16.4%로 고쳐지지만 2025가 −21.7%로 망가진다(4/6).
- 유니버스가 **200종목 미만이면 작동하지 않는다** (80종목에서 t=+0.80으로 부호가 뒤집힌다).
