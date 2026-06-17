#!/usr/bin/env bash
# 야간 체인:
#   1) 5/14 fresh-run (이미 백그라운드 PID 인자로 받음) 대기
#   2) tools.rerun_with_body 로 5/4 ~ 5/13 unclear 일괄 재분석
#   3) stock_assistant.build 로 통합 대시보드 재생성
#   4) 텔레그램 완료 알림 (rerun_with_body 가 자체 알림 보냄)
set -uo pipefail

cd "$(dirname "$0")/.."
PY=./venv/bin/python
LOG=logs/chain_overnight.log

echo "[$(date +%H:%M:%S)] chain start" >> "$LOG"

WAIT_PID="${1:-}"
if [[ -n "$WAIT_PID" ]]; then
  echo "[$(date +%H:%M:%S)] waiting for fresh-run PID $WAIT_PID" >> "$LOG"
  while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 30
  done
  echo "[$(date +%H:%M:%S)] fresh-run done" >> "$LOG"
fi

echo "[$(date +%H:%M:%S)] rerun_with_body 시작" >> "$LOG"
"$PY" -u -m tools.rerun_with_body >> "$LOG" 2>&1
RC=$?
echo "[$(date +%H:%M:%S)] rerun_with_body 종료 (rc=$RC)" >> "$LOG"

# git push — GitHub Actions 라이브 레이더가 새 state 로 텔레그램 알림 보내게
git config user.name "overnight-bot" >> "$LOG" 2>&1
git config user.email "overnight@stockanalyzer" >> "$LOG" 2>&1
# 현재 작업 중인 modified 파일은 건드리지 않고 specific 파일만 add
git stash push -u -m "overnight-chain-tmp" -- analyzers/ reporters/ main.py >> "$LOG" 2>&1 || true
git pull --rebase origin main >> "$LOG" 2>&1 || true
git stash pop >> "$LOG" 2>&1 || true
git add state/signals.json reports/dashboard.json reports/report_*.csv reports/report_*.md reports/report_*.html logs/rerun_summary.md 2>>"$LOG" || true
if git diff --staged --quiet; then
  echo "[$(date +%H:%M:%S)] git: 변경 없음" >> "$LOG"
else
  git commit -m "overnight: 5월 unclear 본문+AI 재분석 — radar 키워드 풍부화" >> "$LOG" 2>&1
  git push origin main >> "$LOG" 2>&1
  echo "[$(date +%H:%M:%S)] git push 완료 → 다음 radar cron 에서 텔레그램 알림 정밀도 ↑" >> "$LOG"
fi

echo "[$(date +%H:%M:%S)] chain done (rc=$RC)" >> "$LOG"
exit $RC
