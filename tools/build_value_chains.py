"""
누적 시그널 종목들의 밸류체인 위치를 GPT-5-nano 로 일괄 추출.

state/signals.json 의 모든 종목 →
  - industry_chain (산업/테마)
  - chain_position (장비/부품/조립 등)
  - chain_role (한 줄 역할)
  - upstream / downstream / peer_chain

state/chains.json 에 저장 — UI 가 read.

CLI:
  python -m tools.build_value_chains          # 전체 (high+medium 만)
  python -m tools.build_value_chains --all    # low 포함
  python -m tools.build_value_chains --max 50 # 최대 N개 (테스트)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from openai import OpenAI
from state_manager import load_state


CHAINS_PATH = ROOT / "state" / "chains.json"

SYSTEM_PROMPT = """당신은 한국 주식 시장 및 산업 밸류체인 전문가입니다.
종목명 + 현재 시그널 정보를 받으면 해당 종목의 산업 내 위치를 정확히 분류합니다.

원칙:
- industry_chain: 1차 산업/테마 (예: "AI 데이터센터", "휴머노이드 로봇", "반도체 후공정 패키징", "한타바이러스 진단", "압구정 재개발", "K-뷰티 ODM")
  · 한 종목이 여러 산업에 걸친다면 가장 핵심 1개만
  · 너무 광범위한 단어 X ("바이오", "반도체" 만 X → "반도체 후공정", "유전자치료제" 등)
- chain_position: 다음 중 정확히 하나
  · "원소재" / "장비" / "부품" / "조립/제조" / "유통/판매" / "서비스" / "최종재" / "지주사" / "기타"
- chain_role: 10~30자 한 줄 역할
- upstream: 이 종목에 원료/장비 공급하는 종목/회사명 (2개 이내, 모르면 빈 리스트)
- downstream: 이 종목의 고객/소비처 (2개 이내)
- peer_chain: 같은 단계 동반/경쟁 종목 (3개 이내)
- 모르면 추측 X — 빈 문자열/빈 리스트

JSON 만 출력."""


def build_user_prompt(ticker: str, name: str, sig: dict) -> str:
    return f"""종목: {name} ({ticker}, {sig.get('market','')})
최근 시그널 (참고):
- main_theme: {sig.get('main_theme','')}
- specific_signal: {sig.get('specific_signal','')[:200]}
- watch_keywords: {', '.join((sig.get('watch_keywords') or [])[:5])}
- related_stocks: {', '.join((sig.get('related_stocks') or [])[:3])}

JSON 응답:
{{
  "industry_chain": "구체적 산업/테마명(이 예시문구 그대로 복사 금지)",
  "chain_position": "원소재|장비|부품|조립/제조|유통/판매|서비스|최종재|지주사|기타",
  "chain_role": "한 줄 역할",
  "upstream": ["상류1", "상류2"],
  "downstream": ["하류1", "하류2"],
  "peer_chain": ["동반1", "동반2", "동반3"]
}}"""


def classify_one(client: OpenAI, ticker: str, name: str, sig: dict,
                 model: str = "gpt-5-nano") -> dict | None:
    try:
        res = client.chat.completions.create(
            model=model,
            max_completion_tokens=600 if "nano" in model else 2000,
            reasoning_effort="minimal",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(ticker, name, sig)},
            ],
        )
        txt = res.choices[0].message.content.strip()
        data = json.loads(txt)
        return {
            "ticker": ticker,
            "name": name,
            "industry_chain": data.get("industry_chain", ""),
            "chain_position": data.get("chain_position", ""),
            "chain_role": data.get("chain_role", ""),
            "upstream": data.get("upstream", []) or [],
            "downstream": data.get("downstream", []) or [],
            "peer_chain": data.get("peer_chain", []) or [],
        }
    except Exception as e:
        return {"ticker": ticker, "name": name, "error": str(e)[:100]}


def load_existing_chains() -> dict:
    if not CHAINS_PATH.exists():
        return {}
    try:
        return json.loads(CHAINS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    p = argparse.ArgumentParser(description="밸류체인 맵 빌더")
    p.add_argument("--all", action="store_true", help="low 포함 전체")
    p.add_argument("--max", type=int, default=None, help="최대 N개 (테스트)")
    p.add_argument("--refresh", action="store_true", help="이미 있는 종목 재분류")
    p.add_argument("--fix-placeholders", action="store_true",
                   help="placeholder/미분류(예: '1차 산업/테마') 종목만 재분류")
    p.add_argument("--model", default="gpt-5-nano",
                   help="분류 모델 (정확도 위해 gpt-5-mini 권장)")
    p.add_argument("--workers", type=int, default=5)
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY 필요")
        sys.exit(1)

    state = load_state()
    signals = state["signals"]
    existing = load_existing_chains()
    by_ticker = existing.get("by_ticker", {})

    BAD_INDUSTRY = {"1차 산업/테마", "", "(미분류)",
                    "구체적 산업/테마명(이 예시문구 그대로 복사 금지)", "테마"}

    # 대상 추출
    targets = []
    if args.fix_placeholders:
        for tic, r in by_ticker.items():
            if r.get("industry_chain", "") in BAD_INDUSTRY:
                sig = signals.get(tic, {})
                targets.append((tic, r.get("name", sig.get("name", tic)), sig))
    else:
        for tic, sig in signals.items():
            if not args.all and sig.get("confidence") not in ("high", "medium"):
                continue
            if not args.refresh and tic in by_ticker:
                continue
            targets.append((tic, sig.get("name", tic), sig))

    if args.max:
        targets = targets[: args.max]
    print(f"📦 분류 대상: {len(targets)}개 (이미 분류됨 {len(by_ticker)})")

    if not targets:
        print("✅ 모든 종목 이미 분류됨")
        return

    client = OpenAI()
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(classify_one, client, tic, name, sig, args.model): tic
            for tic, name, sig in targets
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r and "error" not in r:
                results.append(r)
                if done % 20 == 0 or done == len(targets):
                    print(f"   {done}/{len(targets)}: {r['ticker']} {r['name']} → "
                          f"{r['industry_chain']} / {r['chain_position']}")
            else:
                print(f"   ⚠️ {futures[fut]}: {r.get('error') if r else 'fail'}")

    print(f"\n✓ {len(results)}개 분류 완료 ({time.time()-t0:.1f}초)")

    # by_ticker 갱신
    for r in results:
        by_ticker[r["ticker"]] = r

    # 역인덱스: 산업/위치별 종목
    by_industry: dict[str, dict[str, list]] = {}
    for tic, r in by_ticker.items():
        industry = r.get("industry_chain") or "(미분류)"
        position = r.get("chain_position") or "기타"
        by_industry.setdefault(industry, {}).setdefault(position, []).append({
            "ticker": tic,
            "name": r.get("name", ""),
            "role": r.get("chain_role", ""),
        })

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(by_ticker),
        "by_ticker": by_ticker,
        "by_industry": by_industry,
    }
    CHAINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHAINS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 저장: {CHAINS_PATH}")
    print(f"   산업 수: {len(by_industry)}")
    for ind, positions in sorted(by_industry.items(), key=lambda x: -sum(len(v) for v in x[1].values()))[:10]:
        cnt = sum(len(v) for v in positions.values())
        print(f"   - {ind}: {cnt}종목 ({', '.join(f'{p}({len(v)})' for p, v in positions.items())})")


if __name__ == "__main__":
    main()
