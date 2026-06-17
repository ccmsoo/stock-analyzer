"""키워드/테마별 사후 성과 집계
================================
backtest_trades_*.json 을 누적 로드해서 watch_keyword / main_theme 단위로
사후 평균/승률과 전략 시뮬레이션 결과를 집계한다.

quality_label 기준
- promising: appearances ≥ 3 AND avg_return_5d > 3 AND win_rate_5d ≥ 60
- weak:      appearances ≥ 3 AND avg_return_5d < -2
- noisy:     appearances ≥ 3 AND win_rate_5d < 40
- unproven:  그 외 (표본 부족)
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


# ───────────────────────── 라벨 thresholds ─────────────────────────

PROMISING_MIN_APPEARS = 3
PROMISING_AVG_RETURN_5D = 3.0
PROMISING_WIN_RATE_5D = 60.0

WEAK_MIN_APPEARS = 3
WEAK_AVG_RETURN_5D = -2.0

NOISY_MIN_APPEARS = 3
NOISY_WIN_RATE_5D = 40.0


@dataclass
class KeywordStat:
    keyword: str
    kind: str = "watch_keyword"
    appearances: int = 0
    example_tickers: list[str] = field(default_factory=list)
    latest_seen: str = ""

    avg_return_1d: float | None = None
    avg_return_3d: float | None = None
    avg_return_5d: float | None = None
    avg_return_10d: float | None = None
    win_rate_3d: float | None = None
    win_rate_5d: float | None = None

    avg_strategy_return_pct: float | None = None
    strategy_win_rate: float | None = None
    eligible_count: int = 0

    quality_label: str = "unproven"


def _avg(vals: list) -> float | None:
    valid = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return round(mean(valid), 2) if valid else None


def _win_rate(vals: list) -> float | None:
    valid = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not valid:
        return None
    return round(sum(1 for v in valid if v > 0) / len(valid) * 100, 1)


def _label(st: KeywordStat) -> str:
    n = st.appearances
    avg5 = st.avg_return_5d or 0
    win5 = st.win_rate_5d if st.win_rate_5d is not None else 50.0
    if n >= PROMISING_MIN_APPEARS and avg5 > PROMISING_AVG_RETURN_5D and win5 >= PROMISING_WIN_RATE_5D:
        return "promising"
    if n >= WEAK_MIN_APPEARS and avg5 < WEAK_AVG_RETURN_5D:
        return "weak"
    if n >= NOISY_MIN_APPEARS and win5 < NOISY_WIN_RATE_5D:
        return "noisy"
    return "unproven"


def load_all_trades(backtest_dir: Path = OUTPUT_DIR) -> list[dict]:
    """모든 backtest_trades_*.json 누적. (signal_date, ticker) 키로 dedup, 최신 파일 우선."""
    seen: dict[tuple, dict] = {}
    files = sorted(backtest_dir.glob("backtest_trades_*.json"))
    # backtest_trades_all 은 중복 입력이라 제외
    files = [f for f in files if not f.name.endswith("_all.json")]
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for t in data.get("trades", []):
            key = (t.get("signal_date"), t.get("ticker"))
            seen[key] = t
    return list(seen.values())


def _aggregate(trades: list[dict], extractor, kind: str) -> dict[str, KeywordStat]:
    bucket: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        keys = extractor(t)
        if not keys:
            continue
        if isinstance(keys, str):
            keys = [keys]
        for k in keys:
            k = (k or "").strip()
            if k:
                bucket[k].append(t)

    out: dict[str, KeywordStat] = {}
    for k, items in bucket.items():
        st = KeywordStat(keyword=k, kind=kind)
        st.appearances = len(items)
        st.example_tickers = sorted({i.get("ticker", "") for i in items if i.get("ticker")})[:8]
        dates = sorted({i.get("signal_date", "") for i in items if i.get("signal_date")})
        st.latest_seen = dates[-1] if dates else ""

        st.avg_return_1d = _avg([i.get("return_1d") for i in items])
        st.avg_return_3d = _avg([i.get("return_3d") for i in items])
        st.avg_return_5d = _avg([i.get("return_5d") for i in items])
        st.avg_return_10d = _avg([i.get("return_10d") for i in items])
        st.win_rate_3d = _win_rate([i.get("return_3d") for i in items])
        st.win_rate_5d = _win_rate([i.get("return_5d") for i in items])

        elig = [i for i in items if i.get("strategy_eligible") and i.get("strategy_return_pct") is not None]
        st.eligible_count = len(elig)
        if elig:
            st.avg_strategy_return_pct = _avg([i.get("strategy_return_pct") for i in elig])
            st.strategy_win_rate = _win_rate([i.get("strategy_return_pct") for i in elig])

        st.quality_label = _label(st)
        out[k] = st
    return out


def aggregate_all(trades: list[dict]) -> dict[str, dict]:
    by_watch = _aggregate(trades, lambda t: t.get("watch_keywords") or [], "watch_keyword")
    by_signal = _aggregate(trades, lambda t: t.get("specific_signal"), "specific_signal")
    by_theme = _aggregate(trades, lambda t: t.get("main_theme"), "main_theme")
    return {
        "by_watch_keyword": {k: asdict(v) for k, v in by_watch.items()},
        "by_specific_signal": {k: asdict(v) for k, v in by_signal.items()},
        "by_main_theme": {k: asdict(v) for k, v in by_theme.items()},
    }


def write_outputs(payload: dict, suffix: str | None = None) -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = suffix or datetime.now().strftime("%Y%m%d")
    path = OUTPUT_DIR / f"keyword_performance_{suffix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"json": path}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", help="기본: 오늘 날짜")
    args = parser.parse_args()

    trades = load_all_trades()
    if not trades:
        print("❌ backtest_trades_*.json 없음. profitability.backtest 먼저 실행.")
        return

    agg = aggregate_all(trades)
    counts = {k: len(v) for k, v in agg.items()}

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_trades": len(trades),
        "counts": counts,
        **agg,
    }
    paths = write_outputs(payload, suffix=args.suffix)
    print(f"📊 trades {len(trades)} / watch {counts['by_watch_keyword']} / "
          f"signal {counts['by_specific_signal']} / theme {counts['by_main_theme']}")
    print(f"✅ 저장: {paths['json'].name}")

    # 라벨 분포
    label_counts = defaultdict(int)
    for v in agg["by_watch_keyword"].values():
        label_counts[v["quality_label"]] += 1
    print(f"   라벨: {dict(label_counts)}")


if __name__ == "__main__":
    main()
