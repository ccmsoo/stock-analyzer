#!/usr/bin/env bash
# 4월 마지막 주 (4/27 ~ 4/30) 백필 — 5월 야간 재분석이 끝난 후 자동 트리거.
# - 1단계: main.py --start/--end (TOP 20 → 14일 윈도우 뉴스 → AI 분석, 본문 없이 빠르게)
# - 2단계: tools.rerun_with_body (unclear 만 본문 fetch + 보강된 프롬프트로 재분석)
# - 3단계: stock_assistant.build (통합 대시보드 갱신)
set -uo pipefail

cd "$(dirname "$0")/.."
PY=./venv/bin/python
LOG=logs/chain_apr_backfill.log

echo "[$(date +%H:%M:%S)] chain_apr 대기 시작" >> "$LOG"

WAIT_PID="${1:-}"
if [[ -n "$WAIT_PID" ]]; then
  while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 60
  done
fi
echo "[$(date +%H:%M:%S)] 선행 작업 종료 확인 → 4월 백필 시작" >> "$LOG"

# 1) 4월 마지막 주 백필 (네이버 부담 ↓ 위해 일반 뉴스 + 본문 OFF)
"$PY" -u main.py --start 20260427 --end 20260430 --no-general-news >> "$LOG" 2>&1
RC1=$?
echo "[$(date +%H:%M:%S)] main.py range rc=$RC1" >> "$LOG"

# 2) 4월 백필에서 생긴 unclear 들 본문 fetch + 보강된 프롬프트 재분석
"$PY" -u -m tools.rerun_with_body >> "$LOG" 2>&1
RC2=$?
echo "[$(date +%H:%M:%S)] rerun_with_body rc=$RC2" >> "$LOG"

# 3) 통합 빌드
"$PY" -u -m stock_assistant.build --skip-life >> "$LOG" 2>&1
RC3=$?
echo "[$(date +%H:%M:%S)] stock_assistant.build rc=$RC3" >> "$LOG"

# 4) 누적 키워드 라이브러리 통계 — 아침에 확인용
"$PY" -u -m tools.keyword_library_stats >> "$LOG" 2>&1
echo "[$(date +%H:%M:%S)] keyword_library_stats 작성" >> "$LOG"

# 5) git push — 4월 백필 추가분도 라이브 레이더에 반영
git config user.name "overnight-bot" >> "$LOG" 2>&1
git config user.email "overnight@stockanalyzer" >> "$LOG" 2>&1
git stash push -u -m "overnight-apr-tmp" -- analyzers/ reporters/ main.py >> "$LOG" 2>&1 || true
git pull --rebase origin main >> "$LOG" 2>&1 || true
git stash pop >> "$LOG" 2>&1 || true
git add state/signals.json reports/dashboard.json reports/report_*.csv reports/report_*.md reports/report_*.html logs/rerun_summary.md logs/keyword_library_stats.md 2>>"$LOG" || true
if git diff --staged --quiet; then
  echo "[$(date +%H:%M:%S)] git: 변경 없음" >> "$LOG"
else
  git commit -m "overnight backfill: 4/27~4/30 추가 + 누적 키워드 라이브러리" >> "$LOG" 2>&1
  git push origin main >> "$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] git push 완료 → radar.yml 이 새 키워드로 텔레그램 알림" >> "$LOG"
fi

echo "[$(date +%H:%M:%S)] chain_apr 완료 (rc=$RC1/$RC2/$RC3)" >> "$LOG"
exit $((RC1 + RC2 + RC3))
