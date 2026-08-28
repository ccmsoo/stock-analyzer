"""ETF/ETN 제외 필터 — 종목 선택 전략에 상품(ETF)이 섞이면 검정이 무의미해진다.

네이버 시총 순위에는 ETF/ETN이 포함된다. 실제로 유니버스 700개 중 94개(13.4%)가
ETF/ETN이었다. 이게 왜 치명적인가:
  · `TIGER 미국S&P500` `TIGER 미국나스닥100` — 이 기간 모멘텀이 컸다. 모멘텀 전략이
    이걸 고르면 '한국 종목 선택'이 아니라 그냥 미국 지수 매수다.
  · `KODEX 레버리지` — 종목 선택이 아니라 배율.
  · `KODEX CD금리액티브` 등 MMF — 변동성 0에 거래대금이 커서 '거래대금 상위' 유니버스를
    잠식하고, 수익률 분포를 왜곡한다.

이름 기반 필터라 완벽하진 않다. 새 상품명 패턴이 보이면 여기에 추가할 것.
"""
from __future__ import annotations

import re

# 국내 ETF/ETN은 **항상 운용사 브랜드로 시작**한다. 부분일치로 잡으면
# '메리츠금융지주'가 "리츠"에, '뉴파워프라즈마'가 "파워"에 걸린다(실제로 겪음).
# 그래서 접두어 매칭만 쓴다. 리츠(SK리츠 등)는 상장 주식이므로 남긴다.
BRANDS = ("KODEX", "TIGER", "KBSTAR", "KINDEX", "ARIRANG", "HANARO", "KOSEF",
          "SOL ", "ACE ", "PLUS ", "RISE ", "TIMEFOLIO", "TIME ", "SMART ",
          "FOCUS ", "1Q ", "히어로즈", "마이티", "WOORI ", "BNK ", "파워 ",
          "삼성 ", "한투 ", "KIWOOM ", "키움 ", "하나 ", "메리츠 ")
# 주의: '미래에셋'은 브랜드에서 뺀다 — 미래에셋의 ETF 브랜드는 TIGER이고,
# '미래에셋증권/생명/벤처투자'는 실제 상장기업이다. (오탐으로 걸렸던 사례)
# 브랜드가 안 붙는 ETN/파생상품 흔적
SUFFIX = re.compile(r"ETN$|선물\)|\(합성\)|레버리지$|인버스")


def is_fund(name: str) -> bool:
    if not name:
        return False
    n = name.upper()
    return (any(n.startswith(b.upper()) for b in BRANDS)
            or bool(SUFFIX.search(name)))


def filter_meta(meta: dict) -> dict:
    """ETF/ETN을 뺀 meta를 돌려준다."""
    return {c: v for c, v in meta.items() if not is_fund(v.get("name", ""))}


def stock_codes(meta: dict) -> set:
    return set(filter_meta(meta))
