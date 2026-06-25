"""
레이더 픽 장부(append-only) — 포워드 페이퍼-트레이드 성적표의 기반.

레이더가 매일 뱉는 후보를 그대로 누적 기록한다(덮어쓰기 금지). 며칠 뒤
`tools.score_ledger`가 D+1 진입→D+3 실현수익을 채점 → 합성표본이 아닌
'실제 라이브 출력'의 진짜 손익을 측정한다. (메모리 교훈: 비율 말고 진입수익)

기록 필드(촉매 + 진입시점 맥락): date, ticker, name, market, score, keyword,
reason, flag_today(당일등락%), flag_price, chg5, from_high, volratio, value_traded.
이평선 상태/실현수익은 채점기가 단일 소스(네이버)에서 일관 계산한다.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "radar_ledger.jsonl"

FIELDS = ("date", "ticker", "name", "market", "score", "keyword", "reason",
          "flag_today", "flag_price", "chg5", "from_high", "volratio", "value_traded")


def load() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _seen_keys() -> set[tuple[str, str]]:
    return {(r.get("date", ""), r.get("ticker", "")) for r in load()}


def append_picks(date_str: str, picks: list[dict]) -> int:
    """레이더 후보(fresh)를 장부에 추가. (date,ticker) 중복은 건너뜀. 추가건수 반환."""
    seen = _seen_keys()
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with open(LEDGER, "a") as f:
        for p in picks:
            tic = p.get("ticker")
            if not tic or (date_str, tic) in seen:
                continue
            row = {
                "date": date_str,
                "ticker": tic,
                "name": p.get("name", tic),
                "market": p.get("market", ""),
                "score": p.get("score"),
                "keyword": p.get("keyword", ""),
                "reason": p.get("reason", ""),
                "flag_today": p.get("today"),
                "flag_price": p.get("price"),
                "chg5": p.get("chg5"),
                "from_high": p.get("from_high"),
                "volratio": p.get("volratio"),
                "value_traded": p.get("value_traded"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            seen.add((date_str, tic))
            added += 1
    return added


if __name__ == "__main__":
    rows = load()
    print(f"장부 {LEDGER}: {len(rows)}건")
    from collections import Counter
    print("날짜별:", dict(Counter(r["date"] for r in rows)))
