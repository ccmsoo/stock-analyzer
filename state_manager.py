"""
상태 관리 모듈
================
"이미 분석한 종목/기사" 를 영구 저장해서:
1. 같은 기사가 여러 날 반복 노출되는 노이즈 제거
2. 며칠 연속 상승 중인 종목은 기존 시그널 재사용 (AI 재호출 안 함)

state/signals.json 한 파일로 관리.
"""
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime, timedelta


STATE_DIR = Path(__file__).parent / "state"
STATE_FILE = STATE_DIR / "signals.json"

# 며칠 이내 재등장하면 "연속 상승"으로 본다
CONTINUATION_WINDOW_DAYS = 7


def _normalize_title(title: str) -> str:
    """제목 정규화 — 공백/구두점/괄호 제거하고 소문자화"""
    title = re.sub(r'[\s 　]+', '', title)
    title = re.sub(r'[\[\]\(\)<>"\'·…,.!?\-‧·•|/]+', '', title)
    return title.lower()


def article_hash(title: str) -> str:
    """기사 제목을 정규화 후 해시 — 동일/유사 제목 중복 판단용"""
    return hashlib.md5(_normalize_title(title).encode('utf-8')).hexdigest()[:12]


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "signals": {}, "seen_articles": {}}
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault("signals", {})
        data.setdefault("seen_articles", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "signals": {}, "seen_articles": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_continuation(state: dict, ticker: str, current_date: str) -> bool:
    """이 종목이 최근 CONTINUATION_WINDOW_DAYS 안에 이미 분석되었는지"""
    prev = state["signals"].get(ticker)
    if not prev:
        return False
    if prev.get("confidence") not in ("high", "medium"):
        return False
    try:
        last = datetime.strptime(prev["last_seen"], "%Y%m%d")
        now = datetime.strptime(current_date, "%Y%m%d")
    except (KeyError, ValueError):
        return False
    return 0 < (now - last).days <= CONTINUATION_WINDOW_DAYS


def get_previous_signal(state: dict, ticker: str) -> dict | None:
    return state["signals"].get(ticker)


def filter_new_articles(state: dict, ticker: str, articles: list) -> tuple[list, list]:
    """이전에 본 적 없는 기사만 골라냄. (신규, 중복) 튜플 반환."""
    seen = set(state["seen_articles"].get(ticker, []))
    new_articles, dup_articles = [], []
    for a in articles:
        h = article_hash(a.get("title", ""))
        if h in seen:
            dup_articles.append(a)
        else:
            new_articles.append(a)
            seen.add(h)
    state["seen_articles"][ticker] = list(seen)
    return new_articles, dup_articles


def record_signal(state: dict, ticker: str, stock: dict, analysis: dict, date_str: str) -> None:
    """오늘 분석 결과를 상태 파일에 누적"""
    prev = state["signals"].get(ticker)
    history_entry = {"date": date_str, "change_pct": stock.get("change_pct")}

    if prev:
        first_seen = prev.get("first_seen", date_str)
        history = prev.get("history", [])
        if not history or history[-1].get("date") != date_str:
            history.append(history_entry)
        consecutive = prev.get("consecutive_days", 0) + 1
    else:
        first_seen = date_str
        history = [history_entry]
        consecutive = 1

    state["signals"][ticker] = {
        "name": stock.get("name"),
        "market": stock.get("market"),
        "first_seen": first_seen,
        "last_seen": date_str,
        "consecutive_days": consecutive,
        "main_theme": analysis.get("main_theme", ""),
        "specific_signal": analysis.get("specific_signal", ""),
        "trigger_type": analysis.get("trigger_type", ""),
        "confidence": analysis.get("confidence", ""),
        "watch_keywords": analysis.get("watch_keywords", []),
        "related_stocks": analysis.get("related_stocks", []),
        "reasoning": analysis.get("reasoning", ""),
        "history": history,
    }


def prune_stale_articles(state: dict, max_keep: int = 500) -> None:
    """티커별 기사 해시 최근 N개만 유지 (파일 비대화 방지)"""
    for ticker, hashes in state["seen_articles"].items():
        if len(hashes) > max_keep:
            state["seen_articles"][ticker] = hashes[-max_keep:]


if __name__ == "__main__":
    # 자가 검증
    s = load_state()
    print(f"signals: {len(s['signals'])}건, seen_articles tickers: {len(s['seen_articles'])}")
    h1 = article_hash("SK하이닉스, HBM3E 12단 양산 본격화")
    h2 = article_hash("SK하이닉스,  HBM3E  12단  양산  본격화 ")
    assert h1 == h2, "공백 정규화 실패"
    print(f"정규화 해시 일관성 OK: {h1}")
