"""
Claude AI 분석 모듈
====================
종목별 뉴스를 Claude에 보내서:
1. 왜 올랐는지 추정
2. 메인 테마 분류
3. '지엽적' 키워드 추출 (반도체 같은 흔한 키워드 제외)
4. 연관 종목 후보 제시

설치: pip install anthropic
환경변수: ANTHROPIC_API_KEY
"""
import os
import json
from anthropic import Anthropic


SYSTEM_PROMPT = """당신은 한국 주식 시장 분석가입니다. 단기 급등 종목에 대해 **최근 1~2주 뉴스에서 직접적 트리거**를 찾아냅니다.

분석 원칙:
1. 뉴스 입력은 두 종류:
   - origin=stock_news → 네이버 금융이 해당 종목에 태깅한 기사 (직접 단서)
   - origin=search:... → 회사명/사업 키워드로 검색한 일반 뉴스 (정책/산업/경쟁사 등 간접 트리거 후보)
   둘 다 활용하되, 일반 뉴스에서 종목과의 연결이 약하면 무리해서 묶지 말 것.
2. **흔한 매크로 테마(반도체, AI, 2차전지, 바이오 등)는 1차 분류만 하고, 그보다 지엽적이고 구체적인 서브 테마를 반드시 찾기**
   - 예: '반도체' (X) → 'HBM3E 12단 양산', '실리콘 카바이드(SiC) 전력반도체' (O)
   - 예: '2차전지' (X) → 'LFP 양극재 국산화', '전고체 분리막 공급계약' (O)
3. 가격 변동을 만든 트리거 유형: 공시? 실적? 수주? 정책? 풍문/테마성?
4. **상승일(D-day) 기준으로 트리거 뉴스가 며칠 전이었는지** 명시 (trigger_lag_days).
   원인 뉴스가 없거나 D-day 당일에만 있고 사전 단서가 전무하면 confidence='low'.
5. 같은 시그널로 함께 움직일 연관 종목 1~3개 (가능한 경우만, 모르면 비워두기).
6. 신뢰도: 'high'(명확한 호재/공시 있음), 'medium'(뉴스로 추정 가능), 'low'(원인 불명/단순 테마성).
7. watch_keywords는 정확히 **4~6개** 추출. 클러스터링용이므로 다음 두 종류를 섞어서:
   - **상위 테마어 (2~3개)**: 다른 종목과 묶일 보편 키워드. 예) "휴머노이드", "AI 데이터센터", "재개발", "한타바이러스", "어닝 서프라이즈", "M&A", "FDA 승인"
   - **지엽적 명사구 (2~3개)**: 이 종목 고유의 구체 트리거. 예) "HBM4 12단", "AP209 8월 투약", "압구정 명품관", "CPO 본딩 첫 수주"
   - 종목명·회사명은 절대 포함하지 말 것 (예: '코스모로보틱스' X, '휴머노이드' O)
   - '관련주', '테마주', '실적', '발표' 같은 공허한 단어 금지

반드시 JSON 형식으로만 응답하세요. 마크다운 코드블록 없이 순수 JSON만."""


def _build_user_prompt(stock, articles):
    """종목 하나에 대한 프롬프트 생성"""
    if articles:
        article_text = "\n".join([
            f"- [{a.get('date','')}] ({a.get('origin','stock_news')}) {a['title']} — {a.get('source','')}"
            for a in articles
        ])
    else:
        article_text = "(관련 뉴스 없음)"

    return f"""다음 종목의 가격 변동을 분석하세요.

종목: {stock['name']} ({stock['ticker']}, {stock['market']})
등락률 (D-day): {stock['change_pct']:+.2f}%
종가: {stock['close']:,}원
거래량: {stock['volume']:,}주

최근 14일 관련 뉴스 (origin=stock_news는 종목 태그 기사, origin=search:...는 일반 뉴스):
{article_text}

다음 JSON 형식으로 응답:
{{
  "main_theme": "1차 분류 (예: 반도체, 2차전지, 바이오, 정책, 실적, 수급 등)",
  "specific_signal": "지엽적이고 구체적인 트리거 (예: 'HBM3E 12단 SK하이닉스 공급 계약')",
  "trigger_type": "disclosure|earnings|contract|policy|rumor|technical|unknown",
  "trigger_date": "트리거가 된 핵심 기사의 날짜 (YYYY.MM.DD, 모르면 빈 문자열)",
  "trigger_lag_days": 0,
  "reasoning": "왜 올랐다고 보는지 2~3문장 추론",
  "related_stocks": ["연관 종목명1", "연관 종목명2"],
  "confidence": "high|medium|low",
  "watch_keywords": ["향후 추적할 키워드1", "키워드2"]
}}"""


def analyze_single_stock(client, stock, articles, model="claude-sonnet-4-5"):
    """한 종목 분석"""
    try:
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(stock, articles)}],
        )
        
        response_text = message.content[0].text.strip()
        
        # JSON 코드 블록 제거 (혹시 마크다운으로 감싸져 있으면)
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
            response_text = response_text.strip()
        
        return json.loads(response_text)
    
    except json.JSONDecodeError as e:
        return {
            "main_theme": "분석 실패",
            "specific_signal": f"JSON 파싱 오류: {e}",
            "confidence": "low",
            "raw_response": response_text[:500] if 'response_text' in locals() else '',
        }
    except Exception as e:
        return {
            "main_theme": "분석 실패",
            "specific_signal": f"API 오류: {e}",
            "confidence": "low",
        }


def analyze_with_claude(stocks, news_data, model="claude-sonnet-4-5"):
    """
    종목 리스트를 Claude로 일괄 분석
    
    Returns:
        dict: {ticker: analysis_result}
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY 환경변수를 설정하세요.\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'"
        )
    
    client = Anthropic(api_key=api_key)
    results = {}
    
    for stock in stocks:
        ticker = stock['ticker']
        articles = news_data.get(ticker, [])
        print(f"   - [{ticker}] {stock['name']} 분석 중...")
        results[ticker] = analyze_single_stock(client, stock, articles, model)
    
    return results


if __name__ == "__main__":
    # 단독 테스트
    sample_stock = {
        'ticker': '000660',
        'name': 'SK하이닉스',
        'market': 'KOSPI',
        'change_pct': 5.2,
        'close': 250000,
        'volume': 12000000,
    }
    sample_news = [
        {'title': 'SK하이닉스, HBM3E 12단 엔비디아 공급 본격화', 'date': '2026.05.11 09:00', 'source': '한국경제'},
        {'title': 'AI 메모리 수요 폭증… 하이닉스 수주 잔고 최고치', 'date': '2026.05.10 14:30', 'source': '머니투데이'},
    ]
    
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        client = Anthropic(api_key=api_key)
        result = analyze_single_stock(client, sample_stock, sample_news)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("ANTHROPIC_API_KEY 환경변수가 없어서 테스트 스킵")
