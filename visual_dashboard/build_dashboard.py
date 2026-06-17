"""
Build a static visual dashboard that combines news/AI signals with chart signals.

Inputs:
  - reports/report_YYYYMMDD.csv
  - chart_analysis/output/chart_report_YYYYMMDD.json

Outputs:
  - visual_dashboard/output/combined_signals_YYYYMMDD.json
  - visual_dashboard/output/dashboard_YYYYMMDD.html
  - visual_dashboard/output/dashboard.html
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass
class CombinedSignal:
    date: str
    ticker: str
    name: str
    market: str
    change_pct: float
    status: str
    confidence: str
    main_theme: str
    specific_signal: str
    trigger_type: str
    reasoning: str
    related_stocks: str
    watch_keywords: str
    news_count: int
    news_score: int
    chart_score: int | None
    chart_risk: str
    chart_pattern: str
    chart_comment: str
    volume_ratio_20d: float | None
    value_ratio_20d: float | None
    rsi14: float | None
    distance_ma20_pct: float | None
    high_60d_breakout: bool | None
    combined_score: int
    trade_signal: str
    quadrant: str
    entry_note: str


def latest_report_csv(reports_dir: Path | None = None) -> Path:
    reports_dir = reports_dir or ROOT / "reports"
    files = sorted(reports_dir.glob("report_*.csv"))
    if not files:
        raise FileNotFoundError(f"No report_*.csv files in {reports_dir}")
    return files[-1]


def infer_date_from_report(path: Path) -> str:
    stem = path.stem
    if stem.startswith("report_"):
        return stem.replace("report_", "")
    raise ValueError(f"Cannot infer date from {path}")


def load_report(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row["ticker"] = str(row.get("ticker", "")).zfill(6)
            rows.append(row)
    return rows


def load_chart(date_str: str, path: Path | None = None) -> dict[str, dict]:
    path = path or ROOT / "chart_analysis" / "output" / f"chart_report_{date_str}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["ticker"]).zfill(6): row for row in data.get("results", [])}


def combine(report_rows: list[dict], chart_by_ticker: dict[str, dict]) -> list[CombinedSignal]:
    out = []
    for row in report_rows:
        ticker = row["ticker"]
        chart = chart_by_ticker.get(ticker)
        news_score = score_news(row)
        chart_score = _to_int(chart.get("chart_score")) if chart else None
        risk = chart.get("entry_risk", "missing") if chart else "missing"
        risk_penalty = {"low": 0, "medium": 10, "high": 25, "extreme": 42, "missing": 20}.get(risk, 20)
        chart_component = chart_score if chart_score is not None else 45
        combined_score = clamp(round(news_score * 0.55 + chart_component * 0.45 - risk_penalty))
        trade_signal = classify_trade_signal(row, news_score, chart_score, risk)
        quadrant = classify_quadrant(row, news_score, chart_score, risk)
        entry_note = make_entry_note(row, chart, trade_signal)

        out.append(CombinedSignal(
            date=row.get("date", ""),
            ticker=ticker,
            name=row.get("name", ""),
            market=row.get("market", ""),
            change_pct=_to_float(row.get("change_pct")),
            status=row.get("status", ""),
            confidence=(row.get("confidence", "") or "low").lower(),
            main_theme=row.get("main_theme", ""),
            specific_signal=row.get("specific_signal", ""),
            trigger_type=row.get("trigger_type", ""),
            reasoning=row.get("reasoning", ""),
            related_stocks=row.get("related_stocks", ""),
            watch_keywords=row.get("watch_keywords", ""),
            news_count=_to_int(row.get("news_count")),
            news_score=news_score,
            chart_score=chart_score,
            chart_risk=risk,
            chart_pattern=chart.get("pattern", "") if chart else "",
            chart_comment=chart.get("comment", "") if chart else "차트 분석 데이터가 아직 없습니다.",
            volume_ratio_20d=_to_float_or_none(chart.get("volume_ratio_20d")) if chart else None,
            value_ratio_20d=_to_float_or_none(chart.get("value_ratio_20d")) if chart else None,
            rsi14=_to_float_or_none(chart.get("rsi14")) if chart else None,
            distance_ma20_pct=_to_float_or_none(chart.get("distance_ma20_pct")) if chart else None,
            high_60d_breakout=chart.get("high_60d_breakout") if chart else None,
            combined_score=combined_score,
            trade_signal=trade_signal,
            quadrant=quadrant,
            entry_note=entry_note,
        ))

    out.sort(key=lambda x: (signal_order(x.trade_signal), -x.combined_score, -x.news_score))
    return out


def score_news(row: dict) -> int:
    confidence = (row.get("confidence", "") or "low").lower()
    trigger = (row.get("trigger_type", "") or "unknown").lower()
    status = (row.get("status", "") or "").lower()
    score = {"high": 88, "medium": 64, "low": 28}.get(confidence, 28)
    if trigger in {"disclosure", "earnings", "contract", "policy"}:
        score += 6
    elif trigger in {"rumor", "technical"}:
        score -= 4
    elif trigger == "unknown":
        score -= 12
    if status == "new":
        score += 4
    elif status == "continuation":
        score -= 2
    if _to_int(row.get("news_count")) >= 15:
        score += 2
    return clamp(score)


def classify_trade_signal(row: dict, news_score: int, chart_score: int | None, risk: str) -> str:
    confidence = (row.get("confidence", "") or "low").lower()
    chart = chart_score or 0
    if confidence in {"high", "medium"} and chart >= 72 and risk in {"low", "medium"}:
        return "strong_watch"
    if confidence == "high" and risk in {"high", "extreme"}:
        return "wait_pullback"
    if confidence in {"high", "medium"} and chart >= 65 and risk == "high":
        return "wait_pullback"
    if confidence == "low" and chart >= 72:
        return "theme_watch"
    if risk == "missing" and confidence == "high":
        return "need_chart"
    return "avoid"


def classify_quadrant(row: dict, news_score: int, chart_score: int | None, risk: str) -> str:
    chart = chart_score or 0
    news_strong = news_score >= 68
    chart_strong = chart >= 70 and risk not in {"high", "extreme"}
    chart_hot = risk in {"high", "extreme"}
    if news_strong and chart_strong:
        return "핵심 후보"
    if news_strong and chart_hot:
        return "눌림 대기"
    if not news_strong and chart >= 70:
        return "수급 관찰"
    if risk == "missing":
        return "차트 필요"
    return "제외/보류"


def make_entry_note(row: dict, chart: dict | None, signal: str) -> str:
    if not chart:
        return "차트 분석을 먼저 실행한 뒤 판단합니다."
    risk = chart.get("entry_risk")
    pattern = chart.get("pattern", "")
    rsi = chart.get("rsi14")
    dist = chart.get("distance_ma20_pct")
    vol = chart.get("volume_ratio_20d")
    if signal == "strong_watch":
        return "뉴스와 수급이 함께 맞습니다. 시초 추격보다 거래량 유지와 전일 고가 돌파 여부를 확인합니다."
    if signal == "wait_pullback":
        bits = ["재료는 강하지만 현재 자리는 과열입니다."]
        if rsi and rsi >= 78:
            bits.append(f"RSI {rsi:.1f}.")
        if dist and dist >= 20:
            bits.append(f"20일선 이격 {dist:.1f}%.")
        bits.append("5일선/전일 저가 부근 눌림 확인이 우선입니다.")
        return " ".join(bits)
    if signal == "theme_watch":
        return "차트 수급은 강하지만 뉴스 근거가 약합니다. 테마성 급등인지 추가 뉴스 확인이 필요합니다."
    if risk == "extreme":
        return "상한가/갭/과이격 조합입니다. 신규 진입보다 다음 봉 매물 소화를 먼저 봅니다."
    if pattern:
        return f"{pattern} 패턴입니다. 거래량 {vol or 0:.1f}배 유지 여부를 확인합니다."
    return "신호가 약합니다. 관찰 우선입니다."


def build_payload(combined: list[CombinedSignal], report_path: Path, chart_count: int) -> dict:
    counts = {}
    quadrants = {}
    risks = {}
    for row in combined:
        counts[row.trade_signal] = counts.get(row.trade_signal, 0) + 1
        quadrants[row.quadrant] = quadrants.get(row.quadrant, 0) + 1
        risks[row.chart_risk] = risks.get(row.chart_risk, 0) + 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": combined[0].date if combined else infer_date_from_report(report_path),
        "source_report": str(report_path),
        "count": len(combined),
        "chart_count": chart_count,
        "signal_counts": counts,
        "quadrant_counts": quadrants,
        "risk_counts": risks,
        "signals": [asdict(row) for row in combined],
    }


def write_outputs(payload: dict, output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = payload["date"]
    json_path = output_dir / f"combined_signals_{date_str}.json"
    html_path = output_dir / f"dashboard_{date_str}.html"
    latest_path = output_dir / "dashboard.html"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(payload)
    html_path.write_text(html_text, encoding="utf-8")
    shutil.copyfile(html_path, latest_path)
    return {"json": json_path, "html": html_path, "latest": latest_path}


def render_html(payload: dict) -> str:
    signals = payload["signals"]
    cards = "\n".join(render_card(row) for row in signals)
    matrix = render_matrix(signals)
    table = render_table(signals)
    theme = render_theme_summary(signals)
    data_script = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>뉴스 x 차트 통합 대시보드</title>
<style>
  :root {{
    --bg:#0f1115; --panel:#171a20; --panel2:#1e232b; --fg:#eef1f5; --muted:#9da7b3;
    --line:#2b313b; --good:#37c978; --watch:#6ea8fe; --wait:#f5b84c; --bad:#ff6b66;
    --cyan:#4fd1c5; --purple:#b794f4;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif; }}
  header {{ padding:24px 28px 16px; border-bottom:1px solid var(--line); background:#12151a; position:sticky; top:0; z-index:4; }}
  h1 {{ margin:0; font-size:22px; letter-spacing:0; }}
  h2 {{ font-size:16px; margin:26px 0 12px; color:var(--cyan); }}
  .meta {{ color:var(--muted); margin-top:4px; }}
  main {{ padding:22px 28px 36px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:16px; }}
  .kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .kpi b {{ display:block; font-size:22px; }}
  .kpi span {{ color:var(--muted); font-size:12px; }}
  .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 4px; }}
  button, input {{ background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; }}
  button {{ cursor:pointer; }}
  button.active {{ background:var(--cyan); color:#071012; border-color:var(--cyan); }}
  input {{ min-width:240px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(370px,1fr)); gap:12px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
  .card.strong_watch {{ border-left:4px solid var(--good); }}
  .card.wait_pullback {{ border-left:4px solid var(--wait); }}
  .card.theme_watch {{ border-left:4px solid var(--watch); }}
  .card.need_chart {{ border-left:4px solid var(--purple); }}
  .card.avoid {{ border-left:4px solid var(--bad); opacity:.78; }}
  .row {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
  .name {{ font-weight:700; }}
  .sub, .muted {{ color:var(--muted); }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .pct {{ color:#ff7b86; font-weight:700; }}
  .badge {{ display:inline-block; border:1px solid var(--line); background:var(--panel2); border-radius:999px; padding:2px 8px; font-size:12px; margin:6px 4px 0 0; }}
  .signal {{ color:var(--cyan); margin-top:8px; font-weight:600; }}
  .note {{ margin-top:8px; color:#dce3eb; }}
  .reason {{ margin-top:8px; color:var(--muted); font-size:12px; max-height:58px; overflow:hidden; }}
  .bars {{ display:grid; grid-template-columns:80px 1fr 44px; gap:8px; align-items:center; margin-top:10px; font-size:12px; color:var(--muted); }}
  .bar {{ height:7px; background:#262b34; border-radius:999px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:linear-gradient(90deg,var(--cyan),var(--good)); }}
  .matrix {{ display:grid; grid-template-columns:repeat(2,minmax(260px,1fr)); gap:12px; }}
  .quad {{ min-height:132px; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .quad h3 {{ margin:0 0 8px; font-size:14px; }}
  .pill {{ display:inline-flex; align-items:center; gap:6px; border-radius:6px; background:var(--panel2); border:1px solid var(--line); padding:4px 7px; margin:3px; font-size:12px; }}
  .theme-list {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; }}
  .theme-box {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
  th, td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:13px; }}
  th {{ color:var(--muted); font-size:12px; font-weight:600; }}
  tr:last-child td {{ border-bottom:0; }}
  .hide {{ display:none; }}
</style>
</head>
<body>
<header>
  <h1>뉴스 x 차트 통합 대시보드</h1>
  <div class="meta">{esc(payload["date"])} · 종목 {payload["count"]}개 · 차트 분석 {payload["chart_count"]}개 · 생성 {esc(payload["generated_at"])}</div>
  <div class="kpis">
    {render_kpis(payload)}
  </div>
  <div class="toolbar">
    <button class="active" data-filter="all">전체</button>
    <button data-filter="strong_watch">핵심 후보</button>
    <button data-filter="wait_pullback">눌림 대기</button>
    <button data-filter="theme_watch">수급 관찰</button>
    <button data-filter="need_chart">차트 필요</button>
    <button data-filter="avoid">보류</button>
    <input id="search" placeholder="종목명, 테마, 키워드 검색">
  </div>
</header>
<main>
  <h2>뉴스 × 차트 매트릭스</h2>
  {matrix}
  <h2>테마 요약</h2>
  {theme}
  <h2>종목 카드</h2>
  <section class="grid" id="cards">{cards}</section>
  <h2>점수 테이블</h2>
  {table}
</main>
<script id="payload" type="application/json">{data_script}</script>
<script>
let current = 'all';
let query = '';
const buttons = [...document.querySelectorAll('button[data-filter]')];
buttons.forEach(btn => btn.addEventListener('click', () => {{
  current = btn.dataset.filter;
  buttons.forEach(b => b.classList.toggle('active', b === btn));
  applyFilters();
}}));
document.getElementById('search').addEventListener('input', e => {{
  query = e.target.value.trim().toLowerCase();
  applyFilters();
}});
function applyFilters() {{
  document.querySelectorAll('[data-signal]').forEach(el => {{
    const passType = current === 'all' || el.dataset.signal === current;
    const text = el.dataset.search || el.textContent.toLowerCase();
    el.classList.toggle('hide', !(passType && (!query || text.includes(query))));
  }});
}}
</script>
</body>
</html>
"""


def render_kpis(payload: dict) -> str:
    counts = payload["signal_counts"]
    items = [
        ("핵심 후보", counts.get("strong_watch", 0)),
        ("눌림 대기", counts.get("wait_pullback", 0)),
        ("수급 관찰", counts.get("theme_watch", 0)),
        ("차트 필요", counts.get("need_chart", 0)),
        ("보류", counts.get("avoid", 0)),
    ]
    return "\n".join(f'<div class="kpi"><b>{value}</b><span>{esc(label)}</span></div>' for label, value in items)


def render_matrix(signals: list[dict]) -> str:
    order = ["핵심 후보", "눌림 대기", "수급 관찰", "제외/보류", "차트 필요"]
    labels = {
        "핵심 후보": "뉴스 강함 + 차트 확인",
        "눌림 대기": "뉴스 강함 + 과열",
        "수급 관찰": "차트 강함 + 뉴스 약함",
        "제외/보류": "신호 약함",
        "차트 필요": "뉴스 강함 + 차트 미분석",
    }
    boxes = []
    for q in order:
        rows = [s for s in signals if s["quadrant"] == q]
        if not rows:
            continue
        pills = "".join(
            f'<span class="pill"><b>{esc(s["name"])}</b><span class="muted mono">{s["combined_score"]}</span></span>'
            for s in rows[:12]
        )
        boxes.append(f'<div class="quad"><h3>{esc(q)} <span class="muted">({len(rows)})</span></h3><div class="muted">{esc(labels[q])}</div><div>{pills}</div></div>')
    return f'<section class="matrix">{"".join(boxes)}</section>'


def render_theme_summary(signals: list[dict]) -> str:
    themes = {}
    for s in signals:
        key = s.get("main_theme") or "미분류"
        item = themes.setdefault(key, {"count": 0, "avg": 0, "names": []})
        item["count"] += 1
        item["avg"] += s["combined_score"]
        item["names"].append(s["name"])
    blocks = []
    for theme, item in sorted(themes.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["avg"])):
        avg = round(item["avg"] / item["count"])
        names = ", ".join(item["names"][:6])
        blocks.append(f'<div class="theme-box"><div class="row"><b>{esc(theme)}</b><span class="mono">{avg}</span></div><div class="muted">{item["count"]}종목 · {esc(names)}</div></div>')
    return f'<section class="theme-list">{"".join(blocks)}</section>'


def render_card(row: dict) -> str:
    search = " ".join(str(row.get(k, "")) for k in (
        "name", "ticker", "main_theme", "specific_signal", "watch_keywords", "trade_signal", "quadrant"
    )).lower()
    chart_score = row["chart_score"] if row["chart_score"] is not None else "-"
    rsi = fmt(row["rsi14"])
    vol = fmt(row["volume_ratio_20d"])
    dist = fmt(row["distance_ma20_pct"])
    return f"""<article class="card {esc(row["trade_signal"])}" data-signal="{esc(row["trade_signal"])}" data-search="{esc(search)}">
  <div class="row">
    <div><span class="name">{esc(row["name"])}</span> <span class="sub mono">{esc(row["ticker"])} · {esc(row["market"])}</span></div>
    <span class="pct mono">{row["change_pct"]:+.2f}%</span>
  </div>
  <div>
    <span class="badge">{esc(row["trade_signal"])}</span>
    <span class="badge">{esc(row["quadrant"])}</span>
    <span class="badge">risk {esc(row["chart_risk"])}</span>
    <span class="badge">{esc(row["confidence"])}</span>
  </div>
  <div class="signal">{esc(row["specific_signal"] or row["main_theme"])}</div>
  <div class="bars"><span>뉴스</span><span class="bar"><i style="width:{row["news_score"]}%"></i></span><span class="mono">{row["news_score"]}</span></div>
  <div class="bars"><span>차트</span><span class="bar"><i style="width:{chart_score if isinstance(chart_score, int) else 0}%"></i></span><span class="mono">{chart_score}</span></div>
  <div class="bars"><span>종합</span><span class="bar"><i style="width:{row["combined_score"]}%"></i></span><span class="mono">{row["combined_score"]}</span></div>
  <div class="note">{esc(row["entry_note"])}</div>
  <div class="muted">패턴 {esc(row["chart_pattern"] or "-")} · RSI {rsi} · 거래량 {vol}x · 20일 이격 {dist}%</div>
  <div class="reason">{esc(row["reasoning"])}</div>
</article>"""


def render_table(signals: list[dict]) -> str:
    rows = []
    for s in signals:
        rows.append(f"""<tr data-signal="{esc(s["trade_signal"])}" data-search="{esc((s["name"] + s["main_theme"] + s["specific_signal"]).lower())}">
  <td><b>{esc(s["name"])}</b><div class="muted mono">{esc(s["ticker"])}</div></td>
  <td>{esc(s["trade_signal"])}<div class="muted">{esc(s["quadrant"])}</div></td>
  <td class="mono">{s["combined_score"]}</td>
  <td class="mono">{s["news_score"]}</td>
  <td class="mono">{s["chart_score"] if s["chart_score"] is not None else "-"}</td>
  <td>{esc(s["chart_risk"])}</td>
  <td>{esc(s["main_theme"])}</td>
  <td>{esc(s["specific_signal"])}</td>
</tr>""")
    return f"""<table>
  <thead><tr><th>종목</th><th>판정</th><th>종합</th><th>뉴스</th><th>차트</th><th>리스크</th><th>테마</th><th>시그널</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""


def signal_order(signal: str) -> int:
    return {
        "strong_watch": 0,
        "wait_pullback": 1,
        "theme_watch": 2,
        "need_chart": 3,
        "avoid": 4,
    }.get(signal, 9)


def clamp(value: int | float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _to_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _to_float_or_none(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build visual dashboard")
    parser.add_argument("--report", type=Path, help="reports/report_YYYYMMDD.csv. Defaults to latest")
    parser.add_argument("--chart", type=Path, help="chart_report_YYYYMMDD.json. Defaults by report date")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    report = args.report or latest_report_csv()
    date_str = infer_date_from_report(report)
    report_rows = load_report(report)
    chart_by_ticker = load_chart(date_str, args.chart)
    combined = combine(report_rows, chart_by_ticker)
    payload = build_payload(combined, report, len(chart_by_ticker))
    paths = write_outputs(payload, args.output_dir)

    print(f"대시보드 생성 완료: {date_str}")
    print(f"  - 종목: {payload['count']} / 차트 결합: {payload['chart_count']}")
    for key, path in paths.items():
        print(f"  - {key}: {path}")


if __name__ == "__main__":
    main()
