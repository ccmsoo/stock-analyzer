"""키워드 라이브러리 누적 통계 — 자는 동안 데이터 얼마나 쌓였는지 확인용.

state/signals.json 의 watch_keywords + deep_keywords 를 집계해서
어떤 키워드가 얼마나 자주 등장했는지 + 신뢰도 분포 + 최근 활성도 보여준다.

산출: logs/keyword_library_stats.md
"""
from __future__ import annotations
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state_manager import load_state


def main() -> None:
    state = load_state()
    signals = state.get("signals", {})

    total = len(signals)
    by_conf: Counter = Counter()
    by_trigger: Counter = Counter()
    by_reason: Counter = Counter()
    watch_kw: Counter = Counter()
    deep_kw: Counter = Counter()
    by_date: Counter = Counter()
    high_kw_examples: defaultdict[str, list] = defaultdict(list)

    for ticker, sig in signals.items():
        by_conf[(sig.get("confidence") or "").lower() or "(none)"] += 1
        by_trigger[(sig.get("trigger_type") or "").lower() or "(none)"] += 1
        if sig.get("reason_unknown_category"):
            by_reason[sig.get("reason_unknown_category")] += 1
        for kw in sig.get("watch_keywords") or []:
            kw = (kw or "").strip()
            if kw:
                watch_kw[kw] += 1
                if (sig.get("confidence") or "").lower() in ("high", "medium"):
                    high_kw_examples[kw].append(f"{ticker} {sig.get('name')}")
        deep = sig.get("deep_keywords") or {}
        for cat in ("products", "partners", "places", "events", "people"):
            for kw in deep.get(cat, []) or []:
                kw = (kw or "").strip()
                if kw:
                    deep_kw[kw] += 1
        last = sig.get("last_seen") or ""
        if last:
            by_date[last] += 1

    md = [
        f"# 📚 키워드 라이브러리 통계 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
        f"- 누적 시그널 종목: **{total}건**",
        f"- watch_keywords 고유 수: **{len(watch_kw)}개**",
        f"- deep_keywords 고유 수: **{len(deep_kw)}개**",
        "",
        "## confidence 분포",
        "",
        "| 신뢰도 | 종목 수 |",
        "|---|---:|",
    ]
    for c, n in by_conf.most_common():
        md.append(f"| {c} | {n} |")

    md += [
        "",
        "## trigger_type 분포",
        "",
        "| 트리거 | 종목 수 |",
        "|---|---:|",
    ]
    for t, n in by_trigger.most_common():
        md.append(f"| {t} | {n} |")

    if by_reason:
        md += [
            "",
            "## reason_unknown_category 분포 (이유 불명 종목의 원인)",
            "",
            "| 카테고리 | 종목 수 |",
            "|---|---:|",
        ]
        for r, n in by_reason.most_common():
            md.append(f"| `{r}` | {n} |")

    md += [
        "",
        "## 자주 등장한 watch_keywords TOP 30",
        "",
        "| 키워드 | 등장 횟수 | 대표 종목 (high/medium) |",
        "|---|---:|---|",
    ]
    for kw, n in watch_kw.most_common(30):
        examples = ", ".join(high_kw_examples.get(kw, [])[:3]) or "-"
        md.append(f"| {kw} | {n} | {examples} |")

    if deep_kw:
        md += [
            "",
            "## 자주 등장한 deep_keywords TOP 30",
            "",
            "| 키워드 | 등장 횟수 |",
            "|---|---:|",
        ]
        for kw, n in deep_kw.most_common(30):
            md.append(f"| {kw} | {n} |")

    md += [
        "",
        "## 일자별 누적",
        "",
        "| 일자 | 시그널 수 |",
        "|---|---:|",
    ]
    for d, n in sorted(by_date.items()):
        md.append(f"| {d} | {n} |")

    out = ROOT / "logs" / "keyword_library_stats.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"✓ 저장: {out}")


if __name__ == "__main__":
    main()
