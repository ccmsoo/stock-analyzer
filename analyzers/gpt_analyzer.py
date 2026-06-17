"""
OpenAI GPT-4o 분석 모듈
========================
종목별 뉴스를 GPT-4o에 보내서 분석:
1. 왜 올랐는지 추정
2. 메인 테마 분류
3. '지엽적' 키워드 추출 (반도체 같은 흔한 키워드 제외)
4. 연관 종목 후보 제시

설치: pip install openai
환경변수: OPENAI_API_KEY
"""
import os
import json
from openai import OpenAI


SYSTEM_PROMPT = """당신은 한국 주식 시장 분석가입니다. 단기 급등 종목에 대해 **최근 1~2주 뉴스에서 직접적 트리거**를 찾아냅니다.

분석 원칙:
1. 뉴스 입력은 두 종류:
   - origin=stock_news → 네이버 금융이 해당 종목에 태깅한 기사 (직접 단서)
   - origin=search:... → 회사명/사업 키워드로 검색한 일반 뉴스 (정책/산업/경쟁사 등 간접 트리거 후보)
   둘 다 활용하되, 일반 뉴스에서 종목과의 연결이 약하면 무리해서 묶지 말 것.
   기사에 본문 발췌가 포함되어 있으면 제목보다 본문 속 구체 단서(계약 규모, 상대방, 정책명, 임상/승인 단계,
   실적 수치, 시점)를 우선 근거로 삼을 것. 본문에도 직접 근거가 없으면 억지로 연결하지 말 것.
2. **흔한 매크로 테마(반도체, AI, 2차전지, 바이오 등)는 1차 분류만 하고, 그보다 지엽적이고 구체적인 서브 테마를 반드시 찾기**
   - 예: '반도체' (X) → 'HBM3E 12단 양산', '실리콘 카바이드(SiC) 전력반도체' (O)
   - 예: '2차전지' (X) → 'LFP 양극재 국산화', '전고체 분리막 공급계약' (O)
3. 가격 변동을 만든 트리거 유형: 공시? 실적? 수주? 정책? 풍문/테마성?
   - **표면 트리거 vs 본질 시그널 구분**: 등락률이 양(+)인데 기사 표면이 악재(적자·손실·감익·소송)면,
     본문에서 미래 모멘텀 단서를 우선 채택하라:
       · 인수합병/통합 진행 (예: "대한항공에 인수되기 위해 화물 매각", "통합 항공사 출범")
       · 사업 구조조정 / 자산 매각 마무리 (적자의 일회성 비용 처리)
       · 가이던스 / 차세대 사업 (예: "벨리카고 확대", "신규 수주 본격화")
       · 환율 헷지·파생 이익 같은 일회성 호재
     이 경우 trigger_type 은 표면 보도(`earnings`) 가 아니라 본질 시그널(`disclosure`/`contract`/M&A 라면 `disclosure`+
     specific_signal 에 "인수합병/통합 마무리" 명시) 을 우선 선택할 것.
4. **상승일(D-day) 기준으로 트리거 뉴스가 며칠 전이었는지** 명시 (예: 'D-3일 정책 발표' → policy_lag_days=3).
   원인 뉴스가 없거나 D-day 당일에만 있고 사전 단서가 전무하면 confidence='low'.
5. 같은 시그널로 함께 움직일 연관 종목 1~3개 (가능한 경우만, 모르면 비워두기).
6. 신뢰도: 'high'(명확한 호재/공시 있음), 'medium'(뉴스로 추정 가능), 'low'(원인 불명/단순 테마성).
7. watch_keywords는 정확히 **4~6개** 추출. 클러스터링용이므로 다음 두 종류를 섞어서:
   - **상위 테마어 (2~3개)**: 다른 종목과 묶일 보편 키워드. 예) "휴머노이드", "AI 데이터센터", "재개발", "한타바이러스", "어닝 서프라이즈", "M&A", "FDA 승인"
   - **지엽적 명사구 (2~3개)**: 이 종목 고유의 구체 트리거. 예) "HBM4 12단", "AP209 8월 투약", "압구정 명품관", "CPO 본딩 첫 수주"
   - 종목명·회사명은 절대 포함하지 말 것 (예: '코스모로보틱스' X, '휴머노이드' O)
   - '관련주', '테마주', '실적', '발표' 같은 공허한 단어 금지
8. **confidence='low' 또는 trigger_type='unknown' 으로 판단할 때는 반드시 `reason_unknown_category` 를 명시**.
   - `no_news_in_window`: 14일 윈도우 안에 종목 태그/일반 뉴스가 전혀 없음
   - `headline_only_generic`: 기사가 있어도 시장 전반 시황·코스닥/코스피·랭킹 뉴스라 트리거 식별 불가
   - `lagging_article`: 가장 최근 기사가 D-2 이전이라 D-day 원인으로 보기 어려움
   - `weak_name_link`: 기사가 있지만 종목명/회사명이 헤드라인에 거의 등장하지 않아 연결이 약함
   - `theme_only_supply`: 매크로 시황·수급·테마성으로만 보이고 종목 고유 트리거 없음
   - `data_missing`: 본문 추출 실패·크롤 누락 등 수집 문제로 판단 불가
   - `other`: 위 어느 것도 아닌 경우 (이유를 reasoning 에 명시)
   - confidence!=low 이고 trigger_type!=unknown 이면 `reason_unknown_category` 는 빈 문자열로 두기.
9. **밸류체인 위치 분석 — 다음 종목 예측을 위한 핵심**:
   - `industry_chain`: 이 종목이 속한 1차 산업/테마 (예: "AI 데이터센터", "휴머노이드 로봇", "반도체 후공정 패키징", "한타바이러스 진단", "압구정 재개발")
   - `chain_position`: 밸류체인 단계 — 다음 중 하나
     · "원소재" (원료/소재 공급)
     · "장비" (제조 장비/설비)
     · "부품" (중간 부품/모듈)
     · "조립/제조" (최종 제조)
     · "유통/판매" (유통·리테일)
     · "서비스" (운영·소프트웨어)
     · "최종재" (B2C/완제품)
     · "지주사" (지주회사)
     · "기타"
   - `chain_role`: 한 줄 역할 명시 (예: "HBM 메모리용 본딩와이어 공급", "재활 웨어러블 로봇 SI")
   - `upstream`: 이 종목의 상류 (원료/장비 공급자) 1~2개 — 종목명만
   - `downstream`: 이 종목의 하류 (고객/소비처) 1~2개 — 종목명 또는 산업
   - `peer_chain`: 같은 밸류체인 단계의 경쟁/동반 종목 1~3개 — 종목명만
   - 모르면 빈 문자열/빈 리스트로 두기. 추측하지 말 것.

반드시 지정된 JSON 형식으로만 응답하세요."""


def _build_user_prompt(stock, articles):
    """종목 하나에 대한 프롬프트 생성"""
    if articles:
        lines = []
        for a in articles:
            lines.append(
                f"- [{a.get('date','')}] ({a.get('origin','stock_news')}) "
                f"{a['title']} — {a.get('source','')}"
            )
            body = (a.get('body') or '').strip()
            if body:
                lines.append(f"  본문 발췌: {body[:1200]}")
        article_text = "\n".join(lines)
    else:
        article_text = "(관련 뉴스 없음)"

    return f"""다음 종목의 가격 변동을 분석하세요.

종목: {stock['name']} ({stock['ticker']}, {stock['market']})
등락률 (D-day): {stock['change_pct']:+.2f}%
종가: {stock['close']:,}원
거래량: {stock['volume']:,}주

최근 14일 관련 뉴스 (origin=stock_news는 종목 태그 기사, origin=search:...는 일반 뉴스,
본문 발췌가 있으면 제목보다 본문 근거를 우선):
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
  "watch_keywords": ["향후 추적할 키워드1", "키워드2"],
  "reason_unknown_category": "no_news_in_window|headline_only_generic|lagging_article|weak_name_link|theme_only_supply|data_missing|other| (confidence!=low/trigger!=unknown 이면 빈 문자열)",
  "industry_chain": "1차 산업/테마 (예: AI 데이터센터, 휴머노이드 로봇, 반도체 후공정)",
  "chain_position": "원소재|장비|부품|조립/제조|유통/판매|서비스|최종재|지주사|기타",
  "chain_role": "한 줄 역할 (예: HBM 메모리용 본딩와이어 공급)",
  "upstream": ["상류 종목1", "상류 종목2"],
  "downstream": ["하류 종목1", "하류 종목2"],
  "peer_chain": ["같은 단계 동반/경쟁 종목1", "종목2"]
}}"""


def analyze_single_stock(client, stock, articles, model="gpt-5-mini"):
    """한 종목 분석"""
    try:
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=3000,
            reasoning_effort='medium',   # 시그널 식별 정확도 우선 (low는 trigger 식별 약함)
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(stock, articles)},
            ],
        )
        
        response_text = response.choices[0].message.content.strip()
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


def analyze_with_gpt(stocks, news_data, model="gpt-5-mini"):
    """
    종목 리스트를 GPT-4o로 일괄 분석
    
    Args:
        model: 'gpt-5-mini' (균형), 'gpt-5-mini-mini' (저렴), 'gpt-4-turbo' (고품질)
    
    Returns:
        dict: {ticker: analysis_result}
    """
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY 환경변수를 설정하세요.\n"
            "  export OPENAI_API_KEY='sk-...'\n"
            "  키 발급: https://platform.openai.com/api-keys"
        )
    
    client = OpenAI(api_key=api_key)
    results = {}
    
    for stock in stocks:
        ticker = stock['ticker']
        articles = news_data.get(ticker, [])
        print(f"   - [{ticker}] {stock['name']} 분석 중...")
        results[ticker] = analyze_single_stock(client, stock, articles, model)
    
    return results


# 기존 코드와 호환되도록 alias
analyze_with_claude = analyze_with_gpt


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
    
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        client = OpenAI(api_key=api_key)
        result = analyze_single_stock(client, sample_stock, sample_news)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OPENAI_API_KEY 환경변수가 없어서 테스트 스킵")
