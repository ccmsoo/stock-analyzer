"""사후수익률 + 룰 기반 백테스트
=====================================
시그널 발생일(D-day) 이후의 진짜 매매 가정으로 사후성과를 측정한다.

가정
- 진입: D+1 시가 (다음 거래일 시초가)
- 단순 청산 (exit_close_1d/3d/5d/10d): D+N 종가
- 룰 기반 전략:
    · 보유 중 -4% 손절 OR +8% 익절 OR 5일 후 종가 청산
    · 같은 날 손절·익절 모두 발동 시 보수적으로 손절을 우선 가정
    · 진입 제외 조건 (eligible=False) 만족 시 strategy_return 없음

데이터 소스
- reports/report_YYYYMMDD.csv
- chart_analysis/output/chart_report_YYYYMMDD.json (있으면 결합)
- state/hist_cache_*.pkl (있으면 우선)
- FinanceDataReader (graceful fallback)

산출물
- profitability/output/backtest_trades_YYYYMMDD.json + .csv
- profitability/output/backtest_trades_all.json + .csv  (--from/--to 사용 시)

이 모듈은 매매 실행기가 아니라 검증 도구다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import FinanceDataReader as fdr  # noqa: E402

from chart_analysis.analyzer import fetch_ohlcv as _chart_fetch_ohlcv  # noqa: E402
from profitability.scoring import (  # noqa: E402
    ScoringInput,
    compute as compute_score,
    is_strategy_eligible,
)
from state_manager import load_state  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
CHART_OUT_DIR = ROOT / "chart_analysis" / "output"
CACHE_DIR = ROOT / "state"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ───────────────────────── 전략 룰 상수 ─────────────────────────
# 튜닝하려면 여기만 바꾸면 됨

HOLD_DAYS = 5                 # 시간 청산 N영업일
STOP_LOSS_PCT = -4.0          # 손절 (entry 대비 %)
TAKE_PROFIT_PCT = 8.0         # 익절 (entry 대비 %)

# 진입 제외 조건 임계값 (scoring.is_strategy_eligible 에서 사용)
EXCLUDE_CONFIDENCE_LOW = "low"
EXCLUDE_TRIGGER_UNKNOWN = "unknown"
EXCLUDE_ENTRY_RISK_HIGH = ("high", "extreme")
EXCLUDE_CHART_SCORE_BELOW = 60
EXCLUDE_VALUE_RATIO_BELOW = 1.5
EXCLUDE_UPPER_SHADOW_ABOVE = 45.0
EXCLUDE_DISTANCE_MA20_ABOVE = 25.0


# ───────────────────────── 데이터 모델 ─────────────────────────


@dataclass
class BacktestTrade:
    # 식별
    signal_date: str
    ticker: str
    name: str = ""
    market: str = ""

    # 뉴스 시그널
    main_theme: str = ""
    specific_signal: str = ""
    trigger_type: str = ""
    confidence: str = ""
    watch_keywords: list[str] = field(default_factory=list)

    # 차트 (chart_analysis 결과 결합)
    chart_score: int | None = None
    entry_risk: str = ""
    rsi14: float | None = None
    volume_ratio_20d: float | None = None
    value_ratio_20d: float | None = None
    distance_ma20_pct: float | None = None
    upper_shadow_pct: float | None = None
    close_position_pct: float | None = None
    high_20d_breakout: bool = False
    high_60d_breakout: bool = False
    trend_state: str = ""
    candle_state: str = ""
    pattern: str = ""

    # 가격
    close_on_signal_date: float | None = None
    entry_price_next_open: float | None = None

    # 단순 N일 종가 청산 결과
    exit_close_1d: float | None = None
    exit_close_3d: float | None = None
    exit_close_5d: float | None = None
    exit_close_10d: float | None = None

    # 진입가 기준 수익률 (%)
    return_1d: float | None = None
    return_3d: float | None = None
    return_5d: float | None = None
    return_10d: float | None = None
    max_gain_5d: float | None = None
    max_drawdown_5d: float | None = None
    max_gain_10d: float | None = None
    max_drawdown_10d: float | None = None

    # 룰 기반 전략 결과
    strategy_eligible: bool = False
    exclusion_reasons: list[str] = field(default_factory=list)
    strategy_entry_price: float | None = None
    strategy_exit_price: float | None = None
    strategy_exit_reason: str = "not_eligible"   # take_profit|stop_loss|time_exit|not_eligible|data_missing
    strategy_exit_date: str = ""
    strategy_return_pct: float | None = None

    # profitability 점수
    profitability_score: int | None = None
    score_label: str = ""
    score_breakdown: dict = field(default_factory=dict)

    # 컨텍스트 / 메타
    consecutive_days: int = 1
    cluster_size: int = 1
    bars_available: int = 0
    note: str = ""

    # 이유 불명 진단 (unclear 시 채워짐) — CSV/HTML 트레이스용
    reason_unknown_category: str = ""
    article_count: int | None = None
    article_origin_dist: str = ""           # "stock_news:6; search:1"
    latest_article_date: str = ""
    trigger_lag_candidate: int | None = None


# ───────────────────────── 유틸 ─────────────────────────


def _safe_float(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    f = _safe_float(v)
    return None if f is None else int(f)


def _safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "yes", "1", "y", "t")
    return bool(v) if v is not None else False


def _parse_keywords(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [tok.strip() for tok in re.split(r"[,;|]", raw) if tok.strip()]
    return []


def _pct(now: float, base: float) -> float | None:
    if base is None or base == 0:
        return None
    return round((now / base - 1) * 100, 2)


# ───────────────────────── 입력 로딩 ─────────────────────────


def load_signals_from_report(report_csv: Path) -> list[dict]:
    if not report_csv.exists():
        raise FileNotFoundError(report_csv)
    with open(report_csv, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_chart_results(date_str: str) -> dict[str, dict]:
    path = CHART_OUT_DIR / f"chart_report_{date_str}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    results = data.get("results", []) if isinstance(data, dict) else []
    return {row.get("ticker"): row for row in results if row.get("ticker")}


def load_hist_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def latest_hist_cache() -> Path | None:
    cands = []
    for p in CACHE_DIR.glob("hist_cache_*_*.pkl"):
        m = re.match(r"hist_cache_(\d{8})_(\d{8})\.pkl", p.name)
        if m:
            cands.append((m.group(2), p))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1]


# ───────────────────────── OHLCV 조회 ─────────────────────────


def fetch_forward_ohlcv(
    ticker: str, signal_date: str, n_days: int = 10, cache: dict | None = None
) -> pd.DataFrame:
    target = datetime.strptime(signal_date, "%Y%m%d")
    start = target.strftime("%Y-%m-%d")
    end = (target + timedelta(days=n_days * 2 + 7)).strftime("%Y-%m-%d")

    if cache and ticker in cache:
        df = cache[ticker].get("df")
        if isinstance(df, pd.DataFrame) and not df.empty:
            mask = (df.index >= target) & (df.index <= datetime.strptime(end, "%Y-%m-%d"))
            sub = df.loc[mask]
            if len(sub) >= 2:
                return sub

    try:
        return fdr.DataReader(ticker, start, end)
    except Exception:
        return pd.DataFrame()


# ───────────────────────── 룰 기반 시뮬레이션 ─────────────────────────


def simulate_strategy(
    df_post: pd.DataFrame,
    hold_days: int = HOLD_DAYS,
    stop_loss_pct: float = STOP_LOSS_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
) -> dict:
    """D+1 시가 진입 → 손절/익절/시간청산 시뮬레이션.

    df_post 는 D-day 행을 0번 인덱스로 가지는 OHLCV. D+1 ~ D+hold_days 행이
    필요.

    Returns
    -------
    dict
        strategy_entry_price, strategy_exit_price, strategy_exit_reason,
        strategy_exit_date, strategy_return_pct, max_gain_*, max_drawdown_*
    """
    out = {
        "strategy_entry_price": None,
        "strategy_exit_price": None,
        "strategy_exit_reason": "data_missing",
        "strategy_exit_date": "",
        "strategy_return_pct": None,
        "max_gain_5d": None,
        "max_drawdown_5d": None,
        "max_gain_10d": None,
        "max_drawdown_10d": None,
    }
    if df_post is None or len(df_post) < 2:
        return out

    # D+1 시가 진입
    next_row = df_post.iloc[1]
    entry = float(next_row["Open"])
    if entry <= 0:
        return out
    out["strategy_entry_price"] = round(entry, 2)

    # 진입 후 보유 윈도우 (D+1 부터 D+hold_days)
    window = df_post.iloc[1 : 1 + hold_days]
    if window.empty:
        return out

    # 최대수익/낙폭 (진입가 기준)
    for horizon, label in [(5, "5d"), (10, "10d")]:
        w = df_post.iloc[1 : 1 + horizon]
        if w.empty:
            continue
        high_max = float(w["High"].max())
        low_min = float(w["Low"].min())
        out[f"max_gain_{label}"] = round((high_max / entry - 1) * 100, 2)
        out[f"max_drawdown_{label}"] = round((low_min / entry - 1) * 100, 2)

    stop_price = entry * (1 + stop_loss_pct / 100)
    target_price = entry * (1 + take_profit_pct / 100)

    # 일별로 손절/익절 체크 (보수적: 같은 날 둘 다 발동 시 손절 우선)
    exit_price = None
    exit_reason = None
    exit_date = None
    for ts, row in window.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        # 손절 먼저
        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
            exit_date = ts.strftime("%Y%m%d")
            break
        if high >= target_price:
            exit_price = target_price
            exit_reason = "take_profit"
            exit_date = ts.strftime("%Y%m%d")
            break

    # 둘 다 발동 안 했으면 시간 청산
    if exit_price is None:
        last = window.iloc[-1]
        exit_price = float(last["Close"])
        exit_reason = "time_exit"
        exit_date = window.index[-1].strftime("%Y%m%d")

    out["strategy_exit_price"] = round(exit_price, 2)
    out["strategy_exit_reason"] = exit_reason
    out["strategy_exit_date"] = exit_date
    out["strategy_return_pct"] = round((exit_price / entry - 1) * 100, 2)
    return out


# ───────────────────────── 단순 청산 / 트레이드 빌드 ─────────────────────────


def _align_dday(df_forward: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    """df_forward 에서 D-day 부터 시작하도록 슬라이스. 휴장일이면 그 직후 첫 영업일."""
    if df_forward is None or df_forward.empty:
        return pd.DataFrame()
    target = datetime.strptime(signal_date, "%Y%m%d")
    same = df_forward.loc[df_forward.index.normalize() == target]
    if not same.empty:
        idx = df_forward.index.get_loc(same.index[0])
        return df_forward.iloc[idx:]
    after = df_forward.loc[df_forward.index > target]
    return after


def build_trade(
    signal_row: dict,
    chart_row: dict | None,
    cache: dict | None,
    state_signals: dict | None = None,
) -> BacktestTrade:
    signal_date = (signal_row.get("date") or signal_row.get("signal_date") or "").strip()
    ticker = (signal_row.get("ticker") or "").strip()

    t = BacktestTrade(
        signal_date=signal_date,
        ticker=ticker,
        name=(signal_row.get("name") or "").strip(),
        market=(signal_row.get("market") or "").strip(),
        main_theme=signal_row.get("main_theme", "") or "",
        specific_signal=signal_row.get("specific_signal", "") or "",
        trigger_type=signal_row.get("trigger_type", "") or "",
        confidence=(signal_row.get("confidence") or "").lower(),
        watch_keywords=_parse_keywords(signal_row.get("watch_keywords")),
        reason_unknown_category=(signal_row.get("reason_unknown_category") or "").strip(),
        article_count=_safe_int(signal_row.get("news_count")),
        article_origin_dist=(signal_row.get("article_origin_dist") or "").strip(),
        latest_article_date=(signal_row.get("latest_article_date") or "").strip(),
        trigger_lag_candidate=_safe_int(signal_row.get("trigger_lag_candidate")),
    )

    if chart_row:
        t.chart_score = _safe_int(chart_row.get("chart_score"))
        t.entry_risk = (chart_row.get("entry_risk") or "").lower()
        t.rsi14 = _safe_float(chart_row.get("rsi14"))
        t.volume_ratio_20d = _safe_float(chart_row.get("volume_ratio_20d"))
        t.value_ratio_20d = _safe_float(chart_row.get("value_ratio_20d"))
        t.distance_ma20_pct = _safe_float(chart_row.get("distance_ma20_pct"))
        t.upper_shadow_pct = _safe_float(chart_row.get("upper_shadow_pct"))
        t.close_position_pct = _safe_float(chart_row.get("close_position_pct"))
        t.high_20d_breakout = _safe_bool(chart_row.get("high_20d_breakout"))
        t.high_60d_breakout = _safe_bool(chart_row.get("high_60d_breakout"))
        t.trend_state = chart_row.get("trend_state", "") or ""
        t.candle_state = chart_row.get("candle_state", "") or ""
        t.pattern = chart_row.get("pattern", "") or ""

    if not ticker or not signal_date:
        t.note = "missing ticker or date"
        return t

    # OHLCV
    df_forward = fetch_forward_ohlcv(ticker, signal_date, n_days=10, cache=cache)
    df_post = _align_dday(df_forward, signal_date)
    if df_post.empty:
        t.note = "no D-day OHLCV"
        t.strategy_exit_reason = "data_missing"
    else:
        t.close_on_signal_date = round(float(df_post.iloc[0]["Close"]), 2)
        if len(df_post) >= 2:
            entry_open = float(df_post.iloc[1]["Open"])
            t.entry_price_next_open = round(entry_open, 2)

            # 단순 N일 종가 + 진입가 기준 수익률
            for n in (1, 3, 5, 10):
                if len(df_post) > n:
                    close_n = float(df_post.iloc[n]["Close"])
                    setattr(t, f"exit_close_{n}d", round(close_n, 2))
                    setattr(t, f"return_{n}d", _pct(close_n, entry_open))

            # 룰 기반 전략 시뮬레이션 (max_gain/drawdown 포함)
            sim = simulate_strategy(df_post)
            t.max_gain_5d = sim["max_gain_5d"]
            t.max_drawdown_5d = sim["max_drawdown_5d"]
            t.max_gain_10d = sim["max_gain_10d"]
            t.max_drawdown_10d = sim["max_drawdown_10d"]

            # 진입 제외 조건 평가
            eligible, reasons = is_strategy_eligible({
                "confidence": t.confidence,
                "trigger_type": t.trigger_type,
                "entry_risk": t.entry_risk,
                "chart_score": t.chart_score,
                "value_ratio_20d": t.value_ratio_20d,
                "upper_shadow_pct": t.upper_shadow_pct,
                "distance_ma20_pct": t.distance_ma20_pct,
            })
            t.strategy_eligible = eligible
            t.exclusion_reasons = reasons

            if eligible:
                t.strategy_entry_price = sim["strategy_entry_price"]
                t.strategy_exit_price = sim["strategy_exit_price"]
                t.strategy_exit_reason = sim["strategy_exit_reason"]
                t.strategy_exit_date = sim["strategy_exit_date"]
                t.strategy_return_pct = sim["strategy_return_pct"]
            else:
                t.strategy_exit_reason = "not_eligible"
        else:
            # D-day는 있지만 D+1 OHLCV 없음 (최신 시그널)
            t.note = "no D+1 OHLCV"
            t.strategy_exit_reason = "data_missing"
            # 제외 평가는 데이터 없어도 메타데이터로 가능
            eligible, reasons = is_strategy_eligible({
                "confidence": t.confidence,
                "trigger_type": t.trigger_type,
                "entry_risk": t.entry_risk,
                "chart_score": t.chart_score,
                "value_ratio_20d": t.value_ratio_20d,
                "upper_shadow_pct": t.upper_shadow_pct,
                "distance_ma20_pct": t.distance_ma20_pct,
            })
            t.strategy_eligible = eligible
            t.exclusion_reasons = reasons

        t.bars_available = max(0, len(df_post) - 1)

    # state 컨텍스트
    if state_signals and ticker in state_signals:
        sig = state_signals[ticker]
        t.consecutive_days = int(sig.get("consecutive_days", 1) or 1)

    if signal_row.get("cluster_size"):
        try:
            t.cluster_size = int(signal_row["cluster_size"])
        except (TypeError, ValueError):
            pass

    # profitability score
    si = ScoringInput(
        confidence=t.confidence,
        trigger_type=t.trigger_type,
        chart_score=t.chart_score,
        entry_risk=t.entry_risk,
        rsi14=t.rsi14,
        value_ratio_20d=t.value_ratio_20d,
        distance_ma20_pct=t.distance_ma20_pct,
        upper_shadow_pct=t.upper_shadow_pct,
        close_position_pct=t.close_position_pct,
        high_20d_breakout=t.high_20d_breakout,
        high_60d_breakout=t.high_60d_breakout,
    )
    sc = compute_score(si)
    t.profitability_score = sc["profitability_score"]
    t.score_label = sc["label"]
    t.score_breakdown = sc["breakdown"]

    return t


def _compute_cluster_sizes(signals: list[dict]) -> dict[str, dict[str, int]]:
    by_date: dict[str, dict[str, int]] = {}
    for row in signals:
        d = (row.get("date") or "").strip()
        sig = (row.get("specific_signal") or "").strip()
        if not d or not sig:
            continue
        by_date.setdefault(d, {})
        by_date[d][sig] = by_date[d].get(sig, 0) + 1
    return by_date


def backtest_signals(
    signals: list[dict],
    cache: dict | None = None,
    state_signals: dict | None = None,
) -> tuple[list[BacktestTrade], list[dict]]:
    cluster_sizes = _compute_cluster_sizes(signals)
    chart_cache: dict[str, dict[str, dict]] = {}

    trades: list[BacktestTrade] = []
    errors: list[dict] = []

    for row in signals:
        date_str = (row.get("date") or "").strip()
        if not date_str:
            errors.append({"ticker": row.get("ticker"), "error": "missing date"})
            continue
        if date_str not in chart_cache:
            chart_cache[date_str] = load_chart_results(date_str)
        chart_row = chart_cache[date_str].get(row.get("ticker"))

        sig_text = (row.get("specific_signal") or "").strip()
        row_with_cluster = dict(row)
        row_with_cluster["cluster_size"] = cluster_sizes.get(date_str, {}).get(sig_text, 1)

        try:
            t = build_trade(row_with_cluster, chart_row, cache, state_signals)
            trades.append(t)
        except Exception as e:  # noqa: BLE001
            errors.append({"ticker": row.get("ticker"), "error": str(e)[:120]})

    return trades, errors


# ───────────────────────── 입력 수집 / 출력 ─────────────────────────


def collect_reports(
    report_csv: Path | None,
    from_date: str | None,
    to_date: str | None,
) -> list[Path]:
    if report_csv:
        return [Path(report_csv)]
    out = []
    for p in sorted(REPORTS_DIR.glob("report_*.csv")):
        m = re.match(r"report_(\d{8})\.csv", p.name)
        if not m:
            continue
        d = m.group(1)
        if from_date and d < from_date:
            continue
        if to_date and d > to_date:
            continue
        out.append(p)
    return out


def write_outputs(
    trades: list[BacktestTrade],
    errors: list[dict],
    suffix: str,
    include_all: bool = False,
) -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}

    json_path = OUTPUT_DIR / f"backtest_trades_{suffix}.json"
    csv_path = OUTPUT_DIR / f"backtest_trades_{suffix}.csv"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(trades),
        "errors": errors,
        "trades": [asdict(t) for t in trades],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_trades_csv(trades, csv_path)
    paths["json"] = json_path
    paths["csv"] = csv_path

    if include_all:
        all_json = OUTPUT_DIR / "backtest_trades_all.json"
        all_csv = OUTPUT_DIR / "backtest_trades_all.csv"
        all_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_trades_csv(trades, all_csv)
        paths["all_json"] = all_json
        paths["all_csv"] = all_csv

    return paths


def _write_trades_csv(trades: list[BacktestTrade], csv_path: Path) -> None:
    if not trades:
        csv_path.write_text("", encoding="utf-8")
        return
    sample = asdict(trades[0])
    fields = list(sample.keys())
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in trades:
            row = asdict(t)
            if isinstance(row.get("watch_keywords"), list):
                row["watch_keywords"] = ", ".join(row["watch_keywords"])
            if isinstance(row.get("exclusion_reasons"), list):
                row["exclusion_reasons"] = ", ".join(row["exclusion_reasons"])
            if isinstance(row.get("score_breakdown"), dict):
                row["score_breakdown"] = json.dumps(row["score_breakdown"], ensure_ascii=False)
            w.writerow(row)


# ───────────────────────── 요약 / CLI ─────────────────────────


def summary(trades: list[BacktestTrade]) -> dict:
    if not trades:
        return {}
    eligibles = [t for t in trades if t.strategy_eligible and t.strategy_return_pct is not None]
    out = {
        "total_trades": len(trades),
        "eligible_trades": len(eligibles),
    }
    if eligibles:
        rets = [t.strategy_return_pct for t in eligibles]
        out["strategy_avg_return_pct"] = round(sum(rets) / len(rets), 2)
        out["strategy_win_rate"] = round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
        reasons = {}
        for t in eligibles:
            reasons[t.strategy_exit_reason] = reasons.get(t.strategy_exit_reason, 0) + 1
        out["exit_reasons"] = reasons
    return out


def _resolve_suffix(reports: list[Path], explicit: str | None) -> str:
    if explicit:
        return explicit
    if reports:
        m = re.match(r"report_(\d{8})\.csv", reports[-1].name)
        if m:
            return m.group(1)
    return datetime.now().strftime("%Y%m%d")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="단일 리포트 CSV")
    parser.add_argument("--from", dest="from_date", help="시작일 YYYYMMDD")
    parser.add_argument("--to", dest="to_date", help="종료일 YYYYMMDD")
    parser.add_argument("--cache", type=Path, help="OHLCV pickle 캐시")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    reports = collect_reports(args.report, args.from_date, args.to_date)
    if not reports:
        print("❌ 입력할 리포트가 없습니다.")
        return
    print(f"📦 리포트 {len(reports)}개")

    cache: dict = {}
    if not args.no_cache:
        cp = args.cache or latest_hist_cache()
        if cp:
            cache = load_hist_cache(cp)
            print(f"📦 OHLCV 캐시: {cp.name} ({len(cache)}종목)")

    try:
        state = load_state()
        state_signals = state.get("signals", {})
    except Exception:
        state_signals = {}

    all_signals = []
    for p in reports:
        all_signals.extend(load_signals_from_report(p))
    print(f"🔍 시그널 {len(all_signals)}건 백테스트")

    trades, errors = backtest_signals(all_signals, cache=cache, state_signals=state_signals)

    suffix = _resolve_suffix(reports, args.to_date)
    is_range = bool(args.from_date or args.to_date) or len(reports) > 1
    paths = write_outputs(trades, errors, suffix=suffix, include_all=is_range)

    print(f"✅ 저장: {paths['json'].name}, {paths['csv'].name}")
    if "all_json" in paths:
        print(f"   누적: {paths['all_json'].name}, {paths['all_csv'].name}")

    s = summary(trades)
    if s:
        print(f"\n📊 총 {s.get('total_trades')}건 / 진입 적격 {s.get('eligible_trades')}건")
        if s.get("eligible_trades"):
            print(f"   전략 평균 수익률 {s['strategy_avg_return_pct']:+.2f}% / "
                  f"승률 {s['strategy_win_rate']:.1f}%")
            print(f"   청산 사유: {s.get('exit_reasons')}")


if __name__ == "__main__":
    main()
