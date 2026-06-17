#!/usr/bin/env bash
# 빌드 전 백엔드 JSON 을 ui/data/ 로 복사.
# Vercel 빌드 환경: ui/ 가 root → 상위 폴더 (../state, ../reports) 가 repo 루트의 그것들.
set -e

cd "$(dirname "$0")/.."   # ui/
mkdir -p data

# Vercel 환경에서는 ROOT_REPO 가 위 한 단계
if [ -n "$VERCEL" ]; then
  ROOT=".."
else
  ROOT=".."
fi

echo "📦 sync-data — ROOT=$ROOT"

# state
if [ -f "$ROOT/state/signals.json" ]; then
  cp "$ROOT/state/signals.json" data/signals.json
  echo "  ✓ signals.json ($(wc -c < data/signals.json) bytes)"
fi

# reports
if [ -f "$ROOT/reports/dashboard.json" ]; then
  cp "$ROOT/reports/dashboard.json" data/dashboard.json
fi
if [ -f "$ROOT/reports/alerts.json" ]; then
  cp "$ROOT/reports/alerts.json" data/alerts.json
fi

# 최신 backtest_trades_*.json (날짜순 정렬, 마지막)
LATEST_BT=$(ls -1 "$ROOT/profitability/output"/backtest_trades_*.json 2>/dev/null | sort | tail -1)
if [ -n "$LATEST_BT" ]; then
  cp "$LATEST_BT" data/backtest_trades_latest.json
  echo "  ✓ backtest_trades_latest ($(basename $LATEST_BT))"
fi

# 모든 backtest_trades_*.json 모아서 합치기 (D-day close map 용)
ls -1 "$ROOT/profitability/output"/backtest_trades_*.json 2>/dev/null | sort | while read f; do
  cp "$f" "data/$(basename $f)"
done

echo "✅ data/ 동기화 완료"
ls -la data/ | head -10
