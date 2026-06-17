# 통합 홈 대시보드

개별 모듈은 유지하되, 사용자는 하나의 홈페이지처럼 볼 수 있게 합친 정적 HTML입니다.

포함 섹션:

- 홈 요약
- 뉴스 × 차트 통합 후보
- 기사 키워드 기회 카드
- 생활 기사 테마 레이더
- 상세 페이지 링크

## 생성

```bash
./venv/bin/python -m unified_dashboard.build_unified_dashboard
```

## 산출물

- `unified_dashboard/output/index.html`
- `unified_dashboard/output/unified_dashboard.json`
- `reports/home.html`

