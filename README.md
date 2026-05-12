# 📊 한국 주식 일일 분석 시스템

매일 장 마감 후 자동으로:
1. 코스피/코스닥 등락률 TOP 종목 수집
2. 종목별 뉴스 자동 수집
3. Claude AI로 상승 원인 분석 (지엽적 시그널 추출 중심)
4. 마크다운 리포트 + 누적 CSV 생성

## 📁 프로젝트 구조

```
stock_analyzer/
├── main.py                          # 메인 실행
├── requirements.txt
├── collectors/
│   ├── price_collector.py          # 네이버 금융 등락률 순위
│   └── news_collector.py           # 종목별 뉴스 수집
├── analyzers/
│   └── claude_analyzer.py          # Claude API 분석
├── reporters/
│   └── report_generator.py         # 마크다운+CSV 리포트
└── reports/                         # 생성된 리포트 저장소
    ├── report_20260512.md
    └── report_20260512.csv
```

## 🚀 설치

```bash
# 1. 가상환경 만들고 진입 (권장)
python3 -m venv venv
source venv/bin/activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. Claude API 키 설정 (https://console.anthropic.com 에서 발급)
export ANTHROPIC_API_KEY='sk-ant-...'
```

## ▶️ 실행

```bash
# 최근 영업일 분석
python main.py

# 특정 날짜 분석
python main.py --date 20260512

# TOP 5만 (API 호출 줄이기)
python main.py --top 5
```

## ⏰ 자동화

### macOS / Linux (cron)

매일 오후 4시 30분(장 마감 후) 자동 실행:

```bash
crontab -e
```

```cron
30 16 * * 1-5 cd /Users/yourname/stock_analyzer && /Users/yourname/stock_analyzer/venv/bin/python main.py >> logs/cron.log 2>&1
```

### macOS launchd (대안)
`~/Library/LaunchAgents/com.stock.analyzer.plist` 생성. cron보다 안정적.

### GitHub Actions (클라우드 자동화)
`.github/workflows/daily.yml` 추가하면 PC를 켜놓을 필요 없음.
API 키는 Repository Secrets에 저장.

## 🔧 다음 단계로 확장 가능한 것

1. **백테스트 모듈**: 과거 30일치 CSV를 누적해, AI가 뽑은 시그널의 적중률 측정
2. **연관 종목 자동 매칭**: 임베딩 + 벡터DB(ChromaDB)로 비슷한 시그널 과거 사례 검색
3. **알림**: 신뢰도 'high' 종목 발견시 슬랙/디스코드 알림
4. **거래량/수급 데이터 추가**: 외국인·기관 매수 정보까지 함께 분석
5. **하락 종목도 분석**: 현재는 상승만 분석 중

## 💰 비용 추정 (Claude API)

- TOP 20개 종목 × 평균 2K 토큰 입력 + 500 토큰 출력
- Sonnet 4.5 기준 하루 약 **$0.10~0.20** (약 130~270원)
- 한 달 약 3,000~6,000원

## ⚠️ 주의사항

- 네이버 금융 크롤링은 사이트 구조 변경에 영향받을 수 있음
- 과도한 크롤링은 IP 차단 위험. `time.sleep()` 유지
- AI 분석은 **참고용**이며 투자 결정은 본인 책임
- 시그널 신뢰도 검증을 위해 반드시 백테스트 병행 권장
