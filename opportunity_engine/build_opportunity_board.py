"""
Build an opportunity board centered on article-derived keywords and themes.

This is the "assistant" layer:
  article/news signal -> buy-point keywords -> theme -> strongest chart names.

It uses existing daily report CSV data plus chart_analysis output. It can also
embed small free OHLCV charts fetched through FinanceDataReader.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from chart_analysis.analyzer import fetch_ohlcv


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass
class RankedStock:
    ticker: str
    name: str
    market: str
    change_pct: float
    confidence: str
    news_score: int
    chart_score: int | None
    chart_risk: str
    chart_pattern: str
    volume_ratio_20d: float | None
    rsi14: float | None
    distance_ma20_pct: float | None
    rank_score: int
    verdict: str
    entry_note: str
    reasoning: str
    sparkline_svg: str


@dataclass
class Opportunity:
    date: str
    title: str
    theme: str
    buy_keywords: list[str]
    trigger_types: list[str]
    directness: str
    opportunity_score: int
    assistant_comment: str
    ranked_stocks: list[RankedStock]


def latest_report_csv(reports_dir: Path | None = None) -> Path:
    reports_dir = reports_dir or ROOT / "reports"
    files = sorted(reports_dir.glob("report_*.csv"))
    if not files:
        raise FileNotFoundError(f"No report_*.csv files in {reports_dir}")
    return files[-1]


def infer_date(path: Path) -> str:
    if path.stem.startswith("report_"):
        return path.stem.replace("report_", "")
    raise ValueError(f"Cannot infer date from {path}")


def load_report(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["ticker"] = str(row.get("ticker", "")).zfill(6)
    return rows


def load_chart(date_str: str, path: Path | None = None) -> dict[str, dict]:
    path = path or ROOT / "chart_analysis" / "output" / f"chart_report_{date_str}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["ticker"]).zfill(6): row for row in data.get("results", [])}


def build_opportunities(rows: list[dict], chart_by_ticker: dict[str, dict],
                        with_charts: bool = True) -> list[Opportunity]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        title = opportunity_title(row)
        grouped.setdefault(normalize_key(title), []).append(row)

    opportunities: list[Opportunity] = []
    for group_rows in grouped.values():
        ranked = [
            rank_stock(row, chart_by_ticker.get(row["ticker"]), with_chart=with_charts)
            for row in group_rows
        ]
        ranked.sort(key=lambda s: (-s.rank_score, risk_order(s.chart_risk), -s.news_score))
        keywords = collect_keywords(group_rows)
        triggers = sorted({r.get("trigger_type", "") for r in group_rows if r.get("trigger_type")})
        score = opportunity_score(ranked, keywords)
        directness = directness_label(group_rows)
        theme = dominant_theme(group_rows)
        title = opportunity_title(group_rows[0])
        comment = assistant_comment(title, theme, ranked, keywords)
        opportunities.append(Opportunity(
            date=group_rows[0].get("date", ""),
            title=title,
            theme=theme,
            buy_keywords=keywords,
            trigger_types=triggers,
            directness=directness,
            opportunity_score=score,
            assistant_comment=comment,
            ranked_stocks=ranked,
        ))

    opportunities.sort(key=lambda o: (-o.opportunity_score, -len(o.ranked_stocks)))
    return opportunities


def opportunity_title(row: dict) -> str:
    signal = (row.get("specific_signal") or "").strip()
    if signal and signal.lower() not in {"unknown", "분석 실패", "(미분석)"}:
        return signal
    theme = (row.get("main_theme") or "미분류").strip()
    name = row.get("name", "")
    return f"{theme} / {name} 급등 신호"


def rank_stock(row: dict, chart: dict | None, with_chart: bool) -> RankedStock:
    news_score = score_news(row)
    chart_score = to_int(chart.get("chart_score")) if chart else None
    risk = chart.get("entry_risk", "missing") if chart else "missing"
    risk_penalty = {"low": 0, "medium": 8, "high": 24, "extreme": 42, "missing": 18}.get(risk, 18)
    chart_component = chart_score if chart_score is not None else 42
    rank_score = clamp(news_score * 0.52 + chart_component * 0.48 - risk_penalty)
    verdict = verdict_label(row, chart_score, risk)
    entry = entry_note(verdict, chart)
    svg = ""
    if with_chart:
        svg = make_sparkline(row["ticker"], row.get("date", ""), risk=risk)

    return RankedStock(
        ticker=row["ticker"],
        name=row.get("name", ""),
        market=row.get("market", ""),
        change_pct=to_float(row.get("change_pct")),
        confidence=(row.get("confidence") or "low").lower(),
        news_score=news_score,
        chart_score=chart_score,
        chart_risk=risk,
        chart_pattern=chart.get("pattern", "") if chart else "",
        volume_ratio_20d=to_float_or_none(chart.get("volume_ratio_20d")) if chart else None,
        rsi14=to_float_or_none(chart.get("rsi14")) if chart else None,
        distance_ma20_pct=to_float_or_none(chart.get("distance_ma20_pct")) if chart else None,
        rank_score=rank_score,
        verdict=verdict,
        entry_note=entry,
        reasoning=row.get("reasoning", ""),
        sparkline_svg=svg,
    )


def score_news(row: dict) -> int:
    conf = (row.get("confidence") or "low").lower()
    trigger = (row.get("trigger_type") or "unknown").lower()
    score = {"high": 90, "medium": 66, "low": 28}.get(conf, 28)
    if trigger in {"disclosure", "earnings", "contract", "policy"}:
        score += 6
    elif trigger == "rumor":
        score -= 4
    elif trigger == "unknown":
        score -= 14
    if to_int(row.get("news_count")) >= 15:
        score += 2
    if row.get("specific_signal") and row.get("specific_signal", "").lower() != "unknown":
        score += 3
    return clamp(score)


def verdict_label(row: dict, chart_score: int | None, risk: str) -> str:
    conf = (row.get("confidence") or "low").lower()
    chart = chart_score or 0
    if conf in {"high", "medium"} and chart >= 72 and risk in {"low", "medium"}:
        return "best_candidate"
    if conf in {"high", "medium"} and risk in {"high", "extreme"}:
        return "wait_pullback"
    if conf == "low" and chart >= 72:
        return "chart_only_watch"
    if risk == "missing" and conf == "high":
        return "needs_chart"
    return "pass"


def entry_note(verdict: str, chart: dict | None) -> str:
    if not chart:
        return "차트 데이터가 없어서 수급 확인이 필요합니다."
    rsi = chart.get("rsi14")
    dist = chart.get("distance_ma20_pct")
    vol = chart.get("volume_ratio_20d")
    if verdict == "best_candidate":
        return "뉴스와 차트가 맞습니다. 거래량 유지와 전일 고가 돌파 확인이 핵심입니다."
    if verdict == "wait_pullback":
        bits = ["재료는 좋지만 현재 가격은 부담스럽습니다."]
        if rsi and rsi >= 78:
            bits.append(f"RSI {rsi:.1f}.")
        if dist and dist >= 20:
            bits.append(f"20일선 이격 {dist:.1f}%.")
        bits.append("눌림, 매물 소화, 재돌파를 기다리는 쪽입니다.")
        return " ".join(bits)
    if verdict == "chart_only_watch":
        return "수급은 강하지만 뉴스 직접성이 약합니다. 기사 연결성을 더 확인합니다."
    if vol:
        return f"거래량은 평균 대비 {vol:.1f}배입니다. 다음 봉 방향 확인 전까지 관찰입니다."
    return "우선순위가 낮습니다."


def collect_keywords(rows: list[dict]) -> list[str]:
    seen = set()
    keywords = []
    for row in rows:
        candidates = []
        candidates.extend(split_keywords(row.get("watch_keywords", "")))
        candidates.extend(extract_signal_terms(row.get("specific_signal", "")))
        for kw in candidates:
            kw = kw.strip()
            if not kw or len(kw) < 2:
                continue
            key = normalize_key(kw)
            if key in seen:
                continue
            seen.add(key)
            keywords.append(kw)
    return keywords[:8]


def split_keywords(text: str) -> list[str]:
    return [x.strip(" `") for x in re.split(r"[,/|·]", text or "") if x.strip(" `")]


def extract_signal_terms(text: str) -> list[str]:
    text = text or ""
    terms = []
    for token in re.findall(r"[가-힣A-Za-z0-9]+(?:\s*[가-힣A-Za-z0-9]+){0,3}", text):
        token = token.strip()
        if len(token) >= 4 and token not in {"unknown", "급등", "기대감"}:
            terms.append(token)
    return terms[:4]


def dominant_theme(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        theme = row.get("main_theme") or "미분류"
        counts[theme] = counts.get(theme, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[0][0]


def directness_label(rows: list[dict]) -> str:
    confs = {(r.get("confidence") or "low").lower() for r in rows}
    triggers = {(r.get("trigger_type") or "unknown").lower() for r in rows}
    if "high" in confs and triggers & {"disclosure", "earnings", "contract", "policy"}:
        return "direct"
    if "high" in confs or "medium" in confs:
        return "probable"
    return "weak"


def opportunity_score(ranked: list[RankedStock], keywords: list[str]) -> int:
    if not ranked:
        return 0
    best = ranked[0].rank_score
    breadth = min(len(ranked) * 4, 16)
    kw_bonus = min(len(keywords) * 2, 10)
    return clamp(best + breadth + kw_bonus)


def assistant_comment(title: str, theme: str, ranked: list[RankedStock], keywords: list[str]) -> str:
    if not ranked:
        return "관련 종목을 찾지 못했습니다."
    best = ranked[0]
    names = ", ".join(s.name for s in ranked[:3])
    kw = ", ".join(keywords[:4]) if keywords else title
    if best.verdict == "best_candidate":
        return f"{theme} 테마에서 {kw}가 핵심 매수 포인트입니다. {best.name}이 뉴스 직접성과 차트 강도가 가장 잘 맞습니다."
    if best.verdict == "wait_pullback":
        return f"{theme} 재료는 살아 있지만 {names} 모두 단기 과열 가능성이 있습니다. 바로 추격하기보다 눌림 또는 재돌파 확인이 유리합니다."
    if best.verdict == "chart_only_watch":
        return f"차트는 {best.name}이 강하지만 기사 키워드와의 연결성은 더 확인해야 합니다."
    return f"{theme} 관련 신호입니다. 현재는 {names} 순으로 관찰하되 매수 근거가 충분한지 추가 확인이 필요합니다."


def make_sparkline(ticker: str, date_str: str, risk: str = "medium") -> str:
    try:
        df = fetch_ohlcv(ticker, date_str, lookback_days=160)
        if df.empty or len(df) < 20:
            return ""
        df = df.tail(60).copy()
        return sparkline_svg(df, risk)
    except Exception:
        return ""


def sparkline_svg(df: pd.DataFrame, risk: str) -> str:
    width, height = 300, 118
    pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 22
    prices = df["Close"].astype(float).tolist()
    vols = df["Volume"].astype(float).tolist() if "Volume" in df else [0] * len(prices)
    if len(prices) < 2:
        return ""
    lo, hi = min(prices), max(prices)
    if hi == lo:
        hi = lo + 1
    max_vol = max(vols) or 1
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b - 24

    def x(i: int) -> float:
        return pad_l + (plot_w * i / (len(prices) - 1))

    def y(price: float) -> float:
        return pad_t + (hi - price) / (hi - lo) * plot_h

    points = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(prices))
    vol_bars = []
    base = height - pad_b
    for i, vol in enumerate(vols):
        bar_h = max(1, vol / max_vol * 18)
        vol_bars.append(f'<rect x="{x(i):.1f}" y="{base - bar_h:.1f}" width="2" height="{bar_h:.1f}" rx="1" />')
    color = {"low": "#37c978", "medium": "#6ea8fe", "high": "#f5b84c", "extreme": "#ff6b66"}.get(risk, "#6ea8fe")
    start, end = prices[0], prices[-1]
    ret = (end / start - 1) * 100 if start else 0
    return f'''<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="60일 차트">
  <rect x="0" y="0" width="{width}" height="{height}" rx="8" fill="#11161d"/>
  <g fill="#303843">{''.join(vol_bars)}</g>
  <polyline fill="none" stroke="{color}" stroke-width="2.4" points="{points}"/>
  <line x1="{pad_l}" y1="{y(prices[-1]):.1f}" x2="{width-pad_r}" y2="{y(prices[-1]):.1f}" stroke="{color}" stroke-opacity=".22" stroke-dasharray="3 4"/>
  <text x="10" y="16" fill="#9da7b3" font-size="10">60D</text>
  <text x="{width-10}" y="16" text-anchor="end" fill="{color}" font-size="10">{ret:+.1f}%</text>
</svg>'''


def build_payload(opportunities: list[Opportunity], report_path: Path, chart_count: int) -> dict:
    verdict_counts: dict[str, int] = {}
    for opp in opportunities:
        for stock in opp.ranked_stocks:
            verdict_counts[stock.verdict] = verdict_counts.get(stock.verdict, 0) + 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": opportunities[0].date if opportunities else infer_date(report_path),
        "source_report": str(report_path),
        "opportunity_count": len(opportunities),
        "chart_count": chart_count,
        "verdict_counts": verdict_counts,
        "opportunities": [opportunity_to_dict(o) for o in opportunities],
    }


def opportunity_to_dict(opp: Opportunity) -> dict:
    data = asdict(opp)
    # The SVG is useful in HTML but noisy in JSON consumers. Keep it so the
    # output is fully portable.
    return data


def write_outputs(payload: dict, output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = payload["date"]
    json_path = output_dir / f"opportunities_{date_str}.json"
    html_path = output_dir / f"opportunity_board_{date_str}.html"
    latest_path = output_dir / "opportunity_board.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(payload)
    html_path.write_text(html_text, encoding="utf-8")
    shutil.copyfile(html_path, latest_path)
    return {"json": json_path, "html": html_path, "latest": latest_path}


def render_html(payload: dict) -> str:
    cards = "\n".join(render_opportunity_card(o) for o in payload["opportunities"])
    kpis = render_kpis(payload)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>기사 키워드 기회 보드</title>
<style>
  :root {{
    --bg:#0e1116; --fg:#eef2f7; --muted:#9aa5b1; --panel:#171c24; --panel2:#202734;
    --line:#2d3542; --good:#35c77b; --wait:#f4b64a; --watch:#6ea8fe; --bad:#ff6f68; --cyan:#4ed8d0;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; padding:24px 30px 16px; background:#121720; border-bottom:1px solid var(--line); }}
  h1 {{ margin:0; font-size:23px; }}
  h2 {{ margin:0 0 8px; font-size:17px; letter-spacing:0; }}
  .meta, .muted {{ color:var(--muted); }}
  main {{ padding:22px 30px 40px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:16px; }}
  .kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .kpi b {{ display:block; font-size:22px; }}
  .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }}
  button, input {{ background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; }}
  button {{ cursor:pointer; }}
  button.active {{ background:var(--cyan); color:#061213; border-color:var(--cyan); }}
  input {{ min-width:260px; }}
  .opp {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:14px; }}
  .opp.top {{ border-left:4px solid var(--good); }}
  .opp.wait {{ border-left:4px solid var(--wait); }}
  .row {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; }}
  .score {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-weight:700; }}
  .chips span, .badge {{ display:inline-block; background:var(--panel2); border:1px solid var(--line); border-radius:999px; padding:2px 8px; margin:5px 4px 0 0; font-size:12px; color:#dbe4ee; }}
  .comment {{ margin-top:9px; color:#dce4ed; }}
  .stocks {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:10px; margin-top:12px; }}
  .stock {{ background:#111820; border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .stock.best_candidate {{ border-left:3px solid var(--good); }}
  .stock.wait_pullback {{ border-left:3px solid var(--wait); }}
  .stock.chart_only_watch {{ border-left:3px solid var(--watch); }}
  .stock.pass {{ opacity:.76; }}
  .name {{ font-weight:700; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .pct {{ color:#ff8189; font-weight:700; }}
  .bars {{ display:grid; grid-template-columns:74px 1fr 40px; gap:8px; align-items:center; color:var(--muted); font-size:12px; margin-top:7px; }}
  .bar {{ height:7px; background:#2b3340; border-radius:999px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:linear-gradient(90deg,var(--cyan),var(--good)); }}
  .spark {{ width:100%; height:118px; margin-top:9px; display:block; border:1px solid #26303d; border-radius:8px; }}
  .note {{ margin-top:8px; }}
  .hide {{ display:none; }}
</style>
</head>
<body>
<header>
  <h1>기사 키워드 기회 보드</h1>
  <div class="meta">{esc(payload["date"])} · 기회 {payload["opportunity_count"]}개 · 차트 결합 {payload["chart_count"]}개 · 생성 {esc(payload["generated_at"])}</div>
  <div class="kpis">{kpis}</div>
  <div class="toolbar">
    <button class="active" data-filter="all">전체</button>
    <button data-filter="best_candidate">차트 최강 포함</button>
    <button data-filter="wait_pullback">눌림 대기</button>
    <button data-filter="chart_only_watch">차트만 강함</button>
    <input id="search" placeholder="기사 키워드, 테마, 종목 검색">
  </div>
</header>
<main>
  {cards}
</main>
<script>
let filter = 'all';
let query = '';
const buttons = [...document.querySelectorAll('button[data-filter]')];
buttons.forEach(btn => btn.addEventListener('click', () => {{
  filter = btn.dataset.filter;
  buttons.forEach(b => b.classList.toggle('active', b === btn));
  applyFilters();
}}));
document.getElementById('search').addEventListener('input', e => {{
  query = e.target.value.trim().toLowerCase();
  applyFilters();
}});
function applyFilters() {{
  document.querySelectorAll('[data-verdicts]').forEach(card => {{
    const verdicts = card.dataset.verdicts.split(',');
    const typeOK = filter === 'all' || verdicts.includes(filter);
    const queryOK = !query || card.dataset.search.includes(query);
    card.classList.toggle('hide', !(typeOK && queryOK));
  }});
}}
</script>
</body>
</html>"""


def render_kpis(payload: dict) -> str:
    counts = payload["verdict_counts"]
    items = [
        ("차트 최강 후보", counts.get("best_candidate", 0)),
        ("눌림 대기", counts.get("wait_pullback", 0)),
        ("차트만 강함", counts.get("chart_only_watch", 0)),
        ("차트 필요", counts.get("needs_chart", 0)),
        ("보류", counts.get("pass", 0)),
    ]
    return "".join(f'<div class="kpi"><b>{v}</b><span class="muted">{esc(k)}</span></div>' for k, v in items)


def render_opportunity_card(opp: dict) -> str:
    verdicts = sorted({s["verdict"] for s in opp["ranked_stocks"]})
    best_verdict = opp["ranked_stocks"][0]["verdict"] if opp["ranked_stocks"] else "pass"
    cls = "top" if best_verdict == "best_candidate" else "wait" if best_verdict == "wait_pullback" else ""
    search = " ".join([
        opp["title"], opp["theme"], " ".join(opp["buy_keywords"]),
        " ".join(s["name"] for s in opp["ranked_stocks"]),
    ]).lower()
    stocks = "\n".join(render_stock(s, i + 1) for i, s in enumerate(opp["ranked_stocks"][:6]))
    keywords = "".join(f"<span>{esc(k)}</span>" for k in opp["buy_keywords"])
    triggers = "".join(f"<span>{esc(t)}</span>" for t in opp["trigger_types"])
    return f"""<section class="opp {cls}" data-verdicts="{esc(','.join(verdicts))}" data-search="{esc(search)}">
  <div class="row">
    <div>
      <h2>{esc(opp["title"])}</h2>
      <div class="muted">테마 {esc(opp["theme"])} · 직접성 {esc(opp["directness"])} · 관련 종목 {len(opp["ranked_stocks"])}개</div>
    </div>
    <div class="score">{opp["opportunity_score"]}</div>
  </div>
  <div class="chips">{keywords}{triggers}</div>
  <div class="comment">{esc(opp["assistant_comment"])}</div>
  <div class="stocks">{stocks}</div>
</section>"""


def render_stock(stock: dict, rank: int) -> str:
    chart_score = stock["chart_score"] if stock["chart_score"] is not None else "-"
    return f"""<article class="stock {esc(stock["verdict"])}">
  <div class="row">
    <div><span class="name">{rank}. {esc(stock["name"])}</span> <span class="muted mono">{esc(stock["ticker"])} · {esc(stock["market"])}</span></div>
    <span class="pct mono">{stock["change_pct"]:+.2f}%</span>
  </div>
  <div>
    <span class="badge">{esc(stock["verdict"])}</span>
    <span class="badge">risk {esc(stock["chart_risk"])}</span>
    <span class="badge">{esc(stock["confidence"])}</span>
  </div>
  <div class="bars"><span>뉴스</span><span class="bar"><i style="width:{stock["news_score"]}%"></i></span><span class="mono">{stock["news_score"]}</span></div>
  <div class="bars"><span>차트</span><span class="bar"><i style="width:{chart_score if isinstance(chart_score, int) else 0}%"></i></span><span class="mono">{chart_score}</span></div>
  <div class="bars"><span>랭킹</span><span class="bar"><i style="width:{stock["rank_score"]}%"></i></span><span class="mono">{stock["rank_score"]}</span></div>
  {stock["sparkline_svg"]}
  <div class="note">{esc(stock["entry_note"])}</div>
  <div class="muted">패턴 {esc(stock["chart_pattern"] or "-")} · RSI {fmt(stock["rsi14"])} · 거래량 {fmt(stock["volume_ratio_20d"])}x · 20일 이격 {fmt(stock["distance_ma20_pct"])}%</div>
</article>"""


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def risk_order(risk: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "extreme": 3, "missing": 4}.get(risk, 9)


def clamp(value: float | int) -> int:
    return max(0, min(100, int(round(value))))


def to_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def to_float(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def to_float_or_none(value) -> float | None:
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
    parser = argparse.ArgumentParser(description="Build article keyword opportunity board")
    parser.add_argument("--report", type=Path, help="reports/report_YYYYMMDD.csv. Defaults to latest")
    parser.add_argument("--chart", type=Path, help="chart_report_YYYYMMDD.json. Defaults by report date")
    parser.add_argument("--no-charts", action="store_true", help="Do not embed free OHLCV mini charts")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    report = args.report or latest_report_csv()
    date_str = infer_date(report)
    rows = load_report(report)
    chart_by_ticker = load_chart(date_str, args.chart)
    opportunities = build_opportunities(rows, chart_by_ticker, with_charts=not args.no_charts)
    payload = build_payload(opportunities, report, len(chart_by_ticker))
    paths = write_outputs(payload, args.output_dir)

    print(f"기회 보드 생성 완료: {date_str}")
    print(f"  - 기회: {payload['opportunity_count']} / 차트 결합: {payload['chart_count']}")
    for key, path in paths.items():
        print(f"  - {key}: {path}")


if __name__ == "__main__":
    main()

