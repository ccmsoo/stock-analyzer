#!/bin/bash
# 일일 자동 실행 wrapper
# - main.py 실행 (TOP 20 + 일반 뉴스 + AI 분석)
# - reverse_signal 추천 갱신
# - logs/ 에 날짜별 로그 저장

set -euo pipefail

ROOT="/Users/choiminsoo/Downloads/stock_analyzer"
cd "$ROOT"

DATE=$(date +%Y%m%d)
LOG="$ROOT/logs/run_${DATE}.log"

# .env 명시 로드 (cron/launchd는 셸 프로필을 안 읽음)
if [ -f "$ROOT/.env" ]; then
    set -a
    source "$ROOT/.env"
    set +a
fi

echo "════════════════════════════════════════════"  >> "$LOG"
echo "  실행: $(date)"                                >> "$LOG"
echo "════════════════════════════════════════════"  >> "$LOG"

"$ROOT/venv/bin/python" -u "$ROOT/main.py" --top 20 >> "$LOG" 2>&1
"$ROOT/venv/bin/python" -u -m recommenders.reverse_signal >> "$LOG" 2>&1

echo "✅ 완료: $(date)" >> "$LOG"
