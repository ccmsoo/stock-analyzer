#!/bin/bash
# 기존 날짜 리포트를 본문 기반으로 재분석한다.
#
# 사용:
#   ./scripts/backfill_body_reports.sh
#   ./scripts/backfill_body_reports.sh 20260504 20260514
#   ./scripts/backfill_body_reports.sh 20260504 20260514 --top 10
#
# 주의:
#   - 날짜별 TOP 종목을 다시 만들고, 종목뉴스/일반뉴스 본문을 수집한 뒤 AI 분석을 다시 수행한다.
#   - API 비용과 네이버 요청 시간이 늘어나므로 한 번에 긴 기간을 돌릴 때는 로그를 확인한다.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f "$ROOT/.env" ] && { set -a; source "$ROOT/.env"; set +a; }

PYTHON="$ROOT/venv/bin/python"

START="${1:-}"
END="${2:-}"

if [ -n "$START" ] && [[ "$START" =~ ^[0-9]{8}$ ]]; then
    shift
else
    START="$("$PYTHON" - <<'PY'
from pathlib import Path
import re
dates = []
for p in Path("reports").glob("report_*.csv"):
    m = re.match(r"report_(\d{8})\.csv", p.name)
    if m:
        dates.append(m.group(1))
print(min(dates) if dates else "")
PY
)"
fi

if [ -n "$END" ] && [[ "$END" =~ ^[0-9]{8}$ ]]; then
    shift
else
    END="$("$PYTHON" - <<'PY'
from pathlib import Path
import re
dates = []
for p in Path("reports").glob("report_*.csv"):
    m = re.match(r"report_(\d{8})\.csv", p.name)
    if m:
        dates.append(m.group(1))
print(max(dates) if dates else "")
PY
)"
fi

if [ -z "$START" ] || [ -z "$END" ]; then
    echo "❌ reports/report_*.csv 를 찾지 못했습니다."
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📰 본문 기반 과거 리포트 백필: $START ~ $END"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

"$PYTHON" -u main.py \
    --start "$START" \
    --end "$END" \
    --top 20 \
    --fetch-body \
    --body-max-chars 2000 \
    "$@"

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📈 차트/수익성/통합 홈 재생성"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON" -u -m stock_assistant.build --life-sample

echo
echo "✅ 완료"
echo "   홈: file://$ROOT/assistant_home.html"
echo "   수익성: file://$ROOT/profitability/output/profitability_dashboard.html"
