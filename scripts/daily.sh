#!/bin/bash
# 매일 장 마감 후 한 번 실행 — 전체 파이프라인
#
# 사용:
#   ./scripts/daily.sh           # 가장 최근 영업일 분석
#   ./scripts/daily.sh 20260515  # 특정 날짜
#   ./scripts/daily.sh --top 30  # TOP N 변경
#
# 단계:
#   1) main.py: 등락률 TOP 20 → 14일 뉴스/본문 → AI 분석
#   2) rebuild_cluster_tags: 신규 종목만 cluster_tag 추출 (기존은 스킵)
#   3) normalize_cluster_tags: 동의어 정규화
#   4) weekend_radar: 활성 테마 + 단기 진입 후보 추출

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# .env 로드
[ -f "$ROOT/.env" ] && { set -a; source "$ROOT/.env"; set +a; }

PYTHON="$ROOT/venv/bin/python"

# 1) main 분석 — 추가 인자는 그대로 전달 (예: --date 20260515 --top 30)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 [1/4] main.py 일일 분석 (TOP 20 + 14일 뉴스/본문 + AI)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]{8}$ ]]; then
    DATE="$1"; shift
    "$PYTHON" -u main.py --date "$DATE" --top 20 --fetch-body --body-max-chars 2000 "$@"
else
    "$PYTHON" -u main.py --top 20 --fetch-body --body-max-chars 2000 "$@"
fi

# 2) cluster_tag 추출 (신규 종목만)
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🏷  [2/4] cluster_tag 추출 (신규 종목만)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON" -u -m tools.rebuild_cluster_tags

# 3) 동의어 정규화
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔗 [3/5] cluster_tag 정규화 (동의어 통합)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON" -u -m tools.normalize_cluster_tags 2>&1 | head -25

# 4) 본문 기반 지엽 키워드 추출 (high confidence만, mini 사용)
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 [4/5] 본문 지엽 키워드 추출 (high만, mini)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON" -u -m analyzers.deep_keywords --confidence high

# 5) 단기 트레이딩 레이더
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📡 [5/5] 활성 테마 + 진입 후보 (weekend_radar)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON" -u -m recommenders.weekend_radar --days 7

echo
echo "✅ 완료 — 대시보드: file://$ROOT/reports/index.html"
echo "   또는 서버: python3 -m http.server 5050 --directory reports → http://localhost:5050/index.html"
