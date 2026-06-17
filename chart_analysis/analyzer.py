"""
Chart analysis engine for daily top movers.

This module is intentionally separate from the main news/AI pipeline. It can
analyze a list of stocks from a generated report CSV, from current top movers,
or from a historical cache created by collectors.historical_collector.
"""
from __future__ import annotations

import csv
import html
import json
import pickle
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import FinanceDataReader as fdr
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass
class ChartSignal:
    date: str
    ticker: str
    name: str
    market: str
    close: int
    change_pct: float
    volume: int
    value_eok: float
    ma5: float | None
    ma20: float | None
    ma60: float | None
    ma120: float | None
    rsi14: float | None
    volume_ratio_20d: float | None
    value_ratio_20d: float | None
    gap_pct: float | None
    upper_shadow_pct: float | None
    close_position_pct: float | None
    distance_ma20_pct: float | None
    high_20d_breakout: bool
    high_60d_breakout: bool
    trend_state: str
    candle_state: str
    pattern: str
    entry_risk: str
    chart_score: int
    comment: str


def load_report_stocks(report_csv: Path) -> list[dict]:
    """Load stocks from reports/report_YYYYMMDD.csv."""
    rows = []
    with open(report_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({
                "date": row.get("date", ""),
                "ticker": str(row.get("ticker", "")).zfill(6),
                "name": row.get("name", ""),
                "market": row.get("market", ""),
                "close": _to_int(row.get("close")),
                "change_pct": _to_float(row.get("change_pct")),
                "volume": _to_int(row.get("volume")),
            })
    return rows


def latest_report_csv(reports_dir: Path | None = None) -> Path:
    reports_dir = reports_dir or ROOT / "reports"
    files = sorted(reports_dir.glob("report_*.csv"))
    if not files:
        raise FileNotFoundError(f"No report_*.csv files in {reports_dir}")
    return files[-1]


def load_cache(cache_path: Path) -> dict:
    with open(cache_path, "rb") as f:
        return pickle.load(f)


def fetch_ohlcv(ticker: str, date_str: str, lookback_days: int = 220,
                cache: dict | None = None) -> pd.DataFrame:
    """Fetch OHLCV data ending at date_str.

    If a historical cache is supplied, prefer it. The cache format matches
    collectors.historical_collector.prefetch_period.
    """
    if cache and ticker in cache:
        df = cache[ticker].get("df", pd.DataFrame()).copy()
        return _trim_to_target(df, date_str)

    target = datetime.strptime(date_str, "%Y%m%d")
    start = (target - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = (target + timedelta(days=1)).strftime("%Y-%m-%d")
    df = fdr.DataReader(ticker, start, end)
    return _trim_to_target(df, date_str)


def analyze_stock(stock: dict, cache: dict | None = None) -> ChartSignal:
    date_str = stock.get("date") or datetime.now().strftime("%Y%m%d")
    ticker = str(stock["ticker"]).zfill(6)
    df = fetch_ohlcv(ticker, date_str, cache=cache)
    if df.empty or len(df) < 25:
        raise ValueError(f"{ticker} has too little OHLCV data ({len(df)} rows)")

    df = _with_indicators(df)
    row = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else row

    close = int(row["Close"])
    open_ = float(row.get("Open", close) or close)
    high = float(row.get("High", close) or close)
    low = float(row.get("Low", close) or close)
    volume = int(row.get("Volume", 0) or 0)
    value_eok = round(close * volume / 100_000_000, 1)

    ma5 = _safe_float(row.get("MA5"))
    ma20 = _safe_float(row.get("MA20"))
    ma60 = _safe_float(row.get("MA60"))
    ma120 = _safe_float(row.get("MA120"))
    rsi14 = _safe_float(row.get("RSI14"))

    prev_close = float(prev.get("Close", close) or close)
    change_pct = _safe_float(stock.get("change_pct"))
    if change_pct is None and prev_close:
        change_pct = round((close / prev_close - 1) * 100, 2)

    vol20_prev = df["Volume"].iloc[-21:-1].mean() if len(df) >= 21 else None
    value = df["Close"] * df["Volume"]
    value20_prev = value.iloc[-21:-1].mean() if len(value) >= 21 else None
    volume_ratio = _ratio(volume, vol20_prev)
    value_ratio = _ratio(close * volume, value20_prev)

    gap_pct = round((open_ / prev_close - 1) * 100, 2) if prev_close else None
    candle_range = max(high - low, 1.0)
    upper_shadow_pct = round((high - max(open_, close)) / candle_range * 100, 1)
    close_position_pct = round((close - low) / candle_range * 100, 1)
    distance_ma20 = round((close / ma20 - 1) * 100, 2) if ma20 else None

    high_20d_breakout = _is_breakout(df, 20)
    high_60d_breakout = _is_breakout(df, 60)
    trend_state = _trend_state(close, ma5, ma20, ma60, ma120)
    candle_state = _candle_state(change_pct or 0, upper_shadow_pct, close_position_pct, gap_pct)
    score = _score(
        trend_state=trend_state,
        change_pct=change_pct or 0,
        rsi14=rsi14,
        volume_ratio=volume_ratio,
        value_ratio=value_ratio,
        high_20d_breakout=high_20d_breakout,
        high_60d_breakout=high_60d_breakout,
        upper_shadow_pct=upper_shadow_pct,
        close_position_pct=close_position_pct,
        distance_ma20_pct=distance_ma20,
    )
    pattern = _pattern(trend_state, high_20d_breakout, high_60d_breakout,
                       volume_ratio, rsi14, upper_shadow_pct, close_position_pct)
    entry_risk = _entry_risk(score, rsi14, distance_ma20, upper_shadow_pct, gap_pct, change_pct or 0)
    comment = _comment(pattern, entry_risk, trend_state, rsi14, volume_ratio,
                       distance_ma20, upper_shadow_pct, high_60d_breakout)

    return ChartSignal(
        date=date_str,
        ticker=ticker,
        name=stock.get("name", ""),
        market=stock.get("market", ""),
        close=close,
        change_pct=round(float(change_pct or 0), 2),
        volume=volume,
        value_eok=value_eok,
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        ma120=ma120,
        rsi14=rsi14,
        volume_ratio_20d=volume_ratio,
        value_ratio_20d=value_ratio,
        gap_pct=gap_pct,
        upper_shadow_pct=upper_shadow_pct,
        close_position_pct=close_position_pct,
        distance_ma20_pct=distance_ma20,
        high_20d_breakout=high_20d_breakout,
        high_60d_breakout=high_60d_breakout,
        trend_state=trend_state,
        candle_state=candle_state,
        pattern=pattern,
        entry_risk=entry_risk,
        chart_score=score,
        comment=comment,
    )


def analyze_stocks(stocks: Iterable[dict], cache: dict | None = None) -> tuple[list[ChartSignal], list[dict]]:
    results: list[ChartSignal] = []
    errors: list[dict] = []
    for stock in stocks:
        try:
            results.append(analyze_stock(stock, cache=cache))
        except Exception as e:
            errors.append({
                "ticker": stock.get("ticker"),
                "name": stock.get("name"),
                "error": str(e),
            })
    results.sort(key=lambda x: (x.entry_risk, -x.chart_score, -x.value_eok))
    return results, errors


def write_outputs(results: list[ChartSignal], errors: list[dict],
                  output_dir: Path = DEFAULT_OUTPUT_DIR,
                  prefix: str | None = None) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date = results[0].date if results else datetime.now().strftime("%Y%m%d")
    prefix = prefix or f"chart_report_{date}"

    json_path = output_dir / f"{prefix}.json"
    csv_path = output_dir / f"{prefix}.csv"
    html_path = output_dir / f"{prefix}.html"

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(results),
        "errors": errors,
        "results": [asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = list(asdict(results[0]).keys()) if results else list(ChartSignal.__dataclass_fields__.keys())
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))

    html_path.write_text(render_html(results, errors), encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "html": html_path}


def render_html(results: list[ChartSignal], errors: list[dict]) -> str:
    cards = "\n".join(_card_html(r) for r in results)
    error_html = ""
    if errors:
        lis = "".join(
            f"<li>{html.escape(str(e.get('ticker')))} {html.escape(str(e.get('name')))}: "
            f"{html.escape(e.get('error', ''))}</li>"
            for e in errors
        )
        error_html = f"<details><summary>분석 실패 {len(errors)}건</summary><ul>{lis}</ul></details>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Chart Analysis</title>
<style>
  :root {{ --bg:#101214; --fg:#e8eaed; --muted:#9aa0a6; --card:#181b1f; --line:#2a2f36;
           --good:#36c275; --warn:#f2b84b; --bad:#ff6961; --info:#66a6ff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
          font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif; }}
  h1 {{ margin:0 0 4px; font-size:22px; }}
  .meta {{ color:var(--muted); margin-bottom:18px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px; }}
  .row {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
  .name {{ font-weight:700; }}
  .sub {{ color:var(--muted); font-size:12px; }}
  .pct {{ color:#ff7b86; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-weight:700; }}
  .score {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .risk-low {{ color:var(--good); }}
  .risk-medium {{ color:var(--warn); }}
  .risk-high {{ color:var(--bad); }}
  .risk-extreme {{ color:var(--bad); font-weight:700; }}
  .chips span {{ display:inline-block; border:1px solid var(--line); border-radius:4px; padding:1px 6px;
                 margin:6px 4px 0 0; color:var(--muted); font-size:12px; }}
  .comment {{ margin-top:8px; color:#d1d5db; }}
  table {{ width:100%; margin-top:8px; border-collapse:collapse; color:var(--muted); font-size:12px; }}
  td {{ border-top:1px solid var(--line); padding:4px 0; }}
  td:last-child {{ text-align:right; color:var(--fg); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
</style>
</head>
<body>
<h1>급등 종목 차트 분석</h1>
<div class="meta">분석 {len(results)}건 · 실패 {len(errors)}건 · 생성 {html.escape(datetime.now().isoformat(timespec="seconds"))}</div>
{error_html}
<div class="grid">
{cards}
</div>
</body>
</html>
"""


def _card_html(r: ChartSignal) -> str:
    risk_class = {
        "low": "risk-low",
        "medium": "risk-medium",
        "high": "risk-high",
        "extreme": "risk-extreme",
    }.get(r.entry_risk, "risk-medium")
    chips = [
        r.trend_state,
        r.candle_state,
        "20일 신고가" if r.high_20d_breakout else "",
        "60일 신고가" if r.high_60d_breakout else "",
        r.pattern,
    ]
    chips_html = "".join(f"<span>{html.escape(c)}</span>" for c in chips if c)
    return f"""<article class="card">
  <div class="row">
    <div><span class="name">{html.escape(r.name)}</span> <span class="sub">{r.ticker} · {html.escape(r.market)}</span></div>
    <span class="pct">{r.change_pct:+.2f}%</span>
  </div>
  <div class="row" style="margin-top:6px">
    <span class="score">score {r.chart_score}</span>
    <span class="{risk_class}">risk {html.escape(r.entry_risk)}</span>
  </div>
  <div class="chips">{chips_html}</div>
  <div class="comment">{html.escape(r.comment)}</div>
  <table>
    <tr><td>종가 / 거래대금</td><td>{r.close:,} / {r.value_eok:.1f}억</td></tr>
    <tr><td>거래량 배수 / 거래대금 배수</td><td>{_fmt(r.volume_ratio_20d)}x / {_fmt(r.value_ratio_20d)}x</td></tr>
    <tr><td>RSI14 / 20일선 이격</td><td>{_fmt(r.rsi14)} / {_fmt(r.distance_ma20_pct)}%</td></tr>
    <tr><td>갭 / 윗꼬리 / 종가위치</td><td>{_fmt(r.gap_pct)}% / {_fmt(r.upper_shadow_pct)}% / {_fmt(r.close_position_pct)}%</td></tr>
  </table>
</article>"""


def _with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for n in (5, 20, 60, 120):
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    df["RSI14"] = _rsi(df["Close"], 14)
    return df


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _trim_to_target(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df.empty:
        return df
    target = datetime.strptime(date_str, "%Y%m%d").date()
    df = df.copy()
    df = df[df.index.date <= target]
    return df.dropna(subset=["Close"]).copy()


def _is_breakout(df: pd.DataFrame, window: int) -> bool:
    if len(df) <= window:
        return False
    today_high = float(df["High"].iloc[-1])
    prev_high = float(df["High"].iloc[-window - 1:-1].max())
    return today_high >= prev_high


def _trend_state(close: float, ma5: float | None, ma20: float | None,
                 ma60: float | None, ma120: float | None) -> str:
    if ma5 and ma20 and ma60 and ma120 and close > ma5 > ma20 > ma60 > ma120:
        return "strong_uptrend"
    if ma20 and ma60 and close > ma20 > ma60:
        return "uptrend"
    if ma20 and close > ma20:
        return "recovering"
    if ma20 and close < ma20:
        return "below_ma20"
    return "unknown"


def _candle_state(change_pct: float, upper_shadow_pct: float | None,
                  close_position_pct: float | None, gap_pct: float | None) -> str:
    if upper_shadow_pct is not None and upper_shadow_pct >= 45:
        return "long_upper_shadow"
    if close_position_pct is not None and close_position_pct >= 75 and change_pct >= 10:
        return "strong_close"
    if gap_pct is not None and gap_pct >= 8:
        return "gap_up"
    return "normal"


def _pattern(trend_state: str, high_20d: bool, high_60d: bool,
             volume_ratio: float | None, rsi14: float | None,
             upper_shadow_pct: float | None, close_position_pct: float | None) -> str:
    vol = volume_ratio or 0
    if high_60d and vol >= 5 and close_position_pct and close_position_pct >= 70:
        return "volume_breakout"
    if high_20d and trend_state in ("uptrend", "strong_uptrend"):
        return "trend_continuation"
    if rsi14 and rsi14 >= 80 and upper_shadow_pct and upper_shadow_pct >= 35:
        return "overheated_reversal_risk"
    if trend_state == "recovering" and vol >= 3:
        return "ma20_recovery"
    return "momentum_watch"


def _entry_risk(score: int, rsi14: float | None, distance_ma20: float | None,
                upper_shadow_pct: float | None, gap_pct: float | None,
                change_pct: float) -> str:
    heat = 0
    if rsi14 and rsi14 >= 85:
        heat += 2
    elif rsi14 and rsi14 >= 75:
        heat += 1
    if distance_ma20 and distance_ma20 >= 35:
        heat += 2
    elif distance_ma20 and distance_ma20 >= 20:
        heat += 1
    if upper_shadow_pct and upper_shadow_pct >= 45:
        heat += 2
    if gap_pct and gap_pct >= 10:
        heat += 1
    if change_pct >= 20:
        heat += 1

    if heat >= 4:
        return "extreme"
    if heat >= 2 or score < 45:
        return "high"
    if score >= 70:
        return "low"
    return "medium"


def _score(**kwargs) -> int:
    score = 50
    trend = kwargs["trend_state"]
    if trend == "strong_uptrend":
        score += 18
    elif trend == "uptrend":
        score += 12
    elif trend == "recovering":
        score += 5
    elif trend == "below_ma20":
        score -= 12

    if kwargs["high_60d_breakout"]:
        score += 15
    elif kwargs["high_20d_breakout"]:
        score += 8

    volume_ratio = kwargs["volume_ratio"] or 0
    value_ratio = kwargs["value_ratio"] or 0
    if volume_ratio >= 8 or value_ratio >= 8:
        score += 12
    elif volume_ratio >= 4 or value_ratio >= 4:
        score += 8
    elif volume_ratio >= 2:
        score += 4

    rsi = kwargs["rsi14"]
    if rsi and 45 <= rsi <= 72:
        score += 8
    elif rsi and rsi >= 85:
        score -= 12
    elif rsi and rsi >= 78:
        score -= 6

    upper = kwargs["upper_shadow_pct"] or 0
    close_pos = kwargs["close_position_pct"] or 0
    if close_pos >= 75:
        score += 8
    if upper >= 45:
        score -= 15
    elif upper >= 30:
        score -= 7

    distance = kwargs["distance_ma20_pct"]
    if distance and distance >= 35:
        score -= 12
    elif distance and distance >= 20:
        score -= 6

    change_pct = kwargs["change_pct"]
    if change_pct >= 25:
        score -= 6

    return max(0, min(100, int(round(score))))


def _comment(pattern: str, risk: str, trend: str, rsi14: float | None,
             volume_ratio: float | None, distance_ma20: float | None,
             upper_shadow_pct: float | None, high_60d_breakout: bool) -> str:
    parts = []
    if pattern == "volume_breakout":
        parts.append("거래량을 동반한 돌파형입니다.")
    elif pattern == "overheated_reversal_risk":
        parts.append("과열과 윗꼬리가 겹쳐 단기 되돌림 위험이 큽니다.")
    elif pattern == "ma20_recovery":
        parts.append("20일선 회복 구간으로 추세 전환 여부를 관찰할 만합니다.")
    else:
        parts.append("모멘텀은 있으나 추가 확인이 필요한 자리입니다.")

    if high_60d_breakout:
        parts.append("60일 신고가를 건드려 수급 집중 신호가 있습니다.")
    if volume_ratio and volume_ratio >= 5:
        parts.append(f"거래량은 20일 평균 대비 {volume_ratio:.1f}배입니다.")
    if rsi14 and rsi14 >= 80:
        parts.append(f"RSI {rsi14:.1f}로 추격 매수는 부담스럽습니다.")
    if distance_ma20 and distance_ma20 >= 20:
        parts.append(f"20일선 이격 {distance_ma20:.1f}%라 눌림 확인이 유리합니다.")
    if upper_shadow_pct and upper_shadow_pct >= 40:
        parts.append("윗꼬리가 길어 장중 매물 출회를 확인해야 합니다.")
    if risk in ("high", "extreme"):
        parts.append("다음 봉의 저가 이탈 여부를 먼저 보는 편이 안전합니다.")
    elif trend in ("uptrend", "strong_uptrend"):
        parts.append("추세는 양호해 거래량이 유지되는지 확인합니다.")
    return " ".join(parts)


def _ratio(value: float | int | None, base: float | int | None) -> float | None:
    if value is None or base is None or not base:
        return None
    return round(float(value) / float(base), 2)


def _safe_float(value) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)

