"""
Build a life/news theme radar.

The goal is to catch everyday news themes before or during early market
reaction, then rank related stocks by chart strength.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import FinanceDataReader as fdr

from chart_analysis.analyzer import analyze_stock
from collectors.general_news_collector import search_news
from opportunity_engine.build_opportunity_board import sparkline_svg


ROOT = Path(__file__).resolve().parent.parent
THEME_FILE = Path(__file__).resolve().parent / "theme_dictionary.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass
class DetectedArticle:
    title: str
    link: str
    source: str
    date: str
    query: str
    matched_triggers: list[str]


@dataclass
class RankedThemeStock:
    ticker: str
    name: str
    market: str
    change_pct: float
    close: int
    chart_score: int
    entry_risk: str
    pattern: str
    volume_ratio_20d: float | None
    rsi14: float | None
    distance_ma20_pct: float | None
    rank_score: int
    verdict: str
    comment: str
    chart_svg: str


@dataclass
class ThemeRadar:
    theme_key: str
    theme_name: str
    description: str
    buy_points: list[str]
    triggers: list[str]
    detected_articles: list[DetectedArticle]
    matched_trigger_count: int
    theme_heat_score: int
    ranked_stocks: list[RankedThemeStock]
    assistant_comment: str


def load_theme_dictionary(path: Path = THEME_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_listing_index() -> dict[str, dict]:
    idx = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = fdr.StockListing(market)
        except Exception as e:
            print(f"   ⚠️ {market} 종목 목록 실패: {e}")
            continue
        for _, row in df.iterrows():
            idx[norm(row["Name"])] = {
                "ticker": row["Code"],
                "name": row["Name"],
                "market": market,
                "marcap": int(row.get("Marcap", 0) or 0),
            }
    return idx


def collect_theme_articles(theme: dict, date_str: str, days: int, max_articles: int,
                           max_queries: int) -> list[DetectedArticle]:
    seen_links = set()
    articles: list[DetectedArticle] = []
    triggers = theme.get("triggers", [])
    for query in triggers[:max_queries]:
        found = search_news(query, date_str, days_before=days, max_results=max_articles)
        for article in found:
            link = article.get("link", "")
            if link in seen_links:
                continue
            seen_links.add(link)
            title = article.get("title", "")
            matched = [t for t in triggers if t and t in title]
            if not matched:
                # Query itself is enough to keep the article, but mark it.
                matched = [query]
            articles.append(DetectedArticle(
                title=title,
                link=link,
                source=article.get("source", ""),
                date=article.get("date", ""),
                query=query,
                matched_triggers=matched,
            ))
        time.sleep(0.15)
    return articles[:max_articles * max(1, max_queries)]


def sample_articles(theme: dict, date_str: str) -> list[DetectedArticle]:
    triggers = theme.get("triggers", [])
    if not triggers:
        return []
    return [
        DetectedArticle(
            title=f"{theme['theme_name']} 관련 신호 점검: {triggers[0]} 이슈 부각",
            link="",
            source="sample",
            date=date_str,
            query=triggers[0],
            matched_triggers=triggers[:2],
        )
    ]


def rank_theme_stocks(theme: dict, listing: dict[str, dict], date_str: str,
                      max_stocks: int, with_charts: bool) -> list[RankedThemeStock]:
    ranked: list[RankedThemeStock] = []
    for stock_name in theme.get("stocks", [])[:max_stocks]:
        info = listing.get(norm(stock_name))
        if not info:
            continue
        stock = {
            "date": date_str,
            "ticker": info["ticker"],
            "name": info["name"],
            "market": info["market"],
            "change_pct": 0,
            "close": 0,
            "volume": 0,
        }
        try:
            chart = analyze_stock(stock)
        except Exception as e:
            print(f"      ⚠️ {stock_name} 차트 실패: {e}")
            continue
        penalty = {"low": 0, "medium": 8, "high": 22, "extreme": 38}.get(chart.entry_risk, 12)
        volume_bonus = min(int((chart.volume_ratio_20d or 0) * 3), 18)
        breakout_bonus = 8 if chart.high_60d_breakout else 4 if chart.high_20d_breakout else 0
        rank_score = clamp(chart.chart_score + volume_bonus + breakout_bonus - penalty)
        verdict = stock_verdict(rank_score, chart.entry_risk, chart.chart_score)
        svg = ""
        if with_charts:
            try:
                df = __import__("chart_analysis.analyzer", fromlist=["fetch_ohlcv"]).fetch_ohlcv(info["ticker"], date_str, lookback_days=160).tail(60)
                svg = sparkline_svg(df, chart.entry_risk)
            except Exception:
                svg = ""
        ranked.append(RankedThemeStock(
            ticker=info["ticker"],
            name=info["name"],
            market=info["market"],
            change_pct=chart.change_pct,
            close=chart.close,
            chart_score=chart.chart_score,
            entry_risk=chart.entry_risk,
            pattern=chart.pattern,
            volume_ratio_20d=chart.volume_ratio_20d,
            rsi14=chart.rsi14,
            distance_ma20_pct=chart.distance_ma20_pct,
            rank_score=rank_score,
            verdict=verdict,
            comment=chart.comment,
            chart_svg=svg,
        ))
    ranked.sort(key=lambda s: (-s.rank_score, risk_order(s.entry_risk), -s.chart_score))
    return ranked


def build_radar(date_str: str, days: int, sample: bool, max_themes: int | None,
                max_queries: int, max_articles: int, max_stocks: int,
                with_charts: bool) -> list[ThemeRadar]:
    themes = load_theme_dictionary()
    items = list(themes.items())
    if max_themes:
        items = items[:max_themes]

    print("1️⃣ 종목 인덱스 로드...")
    listing = build_listing_index()
    print(f"   ✓ {len(listing)}종목")

    result: list[ThemeRadar] = []
    for i, (theme_key, theme) in enumerate(items, 1):
        print(f"\n[{i}/{len(items)}] {theme['theme_name']}")
        if sample:
            articles = sample_articles(theme, date_str)
        else:
            articles = collect_theme_articles(theme, date_str, days, max_articles, max_queries)
        matched_triggers = sorted({m for a in articles for m in a.matched_triggers})
        print(f"   - 기사 {len(articles)}건 / 트리거 {len(matched_triggers)}개")
        ranked = rank_theme_stocks(theme, listing, date_str, max_stocks=max_stocks, with_charts=with_charts)
        print(f"   - 차트 랭킹 {len(ranked)}종목")
        heat = theme_heat_score(articles, matched_triggers)
        result.append(ThemeRadar(
            theme_key=theme_key,
            theme_name=theme["theme_name"],
            description=theme.get("description", ""),
            buy_points=theme.get("buy_points", []),
            triggers=theme.get("triggers", []),
            detected_articles=articles,
            matched_trigger_count=len(matched_triggers),
            theme_heat_score=heat,
            ranked_stocks=ranked,
            assistant_comment=assistant_comment(theme["theme_name"], articles, ranked, theme.get("buy_points", [])),
        ))

    result.sort(key=lambda r: (-final_theme_score(r), -r.theme_heat_score))
    return result


def theme_heat_score(articles: list[DetectedArticle], matched_triggers: list[str]) -> int:
    article_score = min(len(articles) * 12, 55)
    trigger_score = min(len(matched_triggers) * 8, 32)
    source_bonus = 8 if any(a.source and a.source != "sample" for a in articles) else 0
    return clamp(article_score + trigger_score + source_bonus)


def final_theme_score(theme: ThemeRadar) -> int:
    best_stock = theme.ranked_stocks[0].rank_score if theme.ranked_stocks else 0
    return clamp(theme.theme_heat_score * 0.45 + best_stock * 0.55)


def stock_verdict(rank_score: int, risk: str, chart_score: int) -> str:
    if rank_score >= 72 and risk in {"low", "medium"}:
        return "early_candidate"
    if chart_score >= 70 and risk in {"high", "extreme"}:
        return "wait_pullback"
    if rank_score >= 58:
        return "watch"
    return "weak"


def assistant_comment(theme_name: str, articles: list[DetectedArticle],
                      ranked: list[RankedThemeStock], buy_points: list[str]) -> str:
    if not articles:
        return f"{theme_name} 관련 기사는 아직 약합니다. 사전 테마로만 관찰합니다."
    if not ranked:
        return f"{theme_name} 뉴스는 감지됐지만 관련 종목 차트 확인이 부족합니다."
    best = ranked[0]
    point = ", ".join(buy_points[:3])
    if best.verdict == "early_candidate":
        return f"{theme_name} 기사 흐름과 {point} 포인트가 감지됐고, {best.name} 차트가 가장 먼저 반응 중입니다."
    if best.verdict == "wait_pullback":
        return f"{theme_name} 이슈는 살아 있지만 {best.name}은 단기 과열입니다. 눌림 또는 재돌파 확인이 우선입니다."
    return f"{theme_name} 이슈가 감지됐습니다. 현재는 {best.name}이 상대적으로 앞서지만 강한 진입 신호는 더 확인해야 합니다."


def build_payload(radar: list[ThemeRadar], date_str: str, sample: bool) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": date_str,
        "sample": sample,
        "theme_count": len(radar),
        "radar": [radar_to_dict(r) for r in radar],
    }


def radar_to_dict(r: ThemeRadar) -> dict:
    data = asdict(r)
    data["final_score"] = final_theme_score(r)
    return data


def write_outputs(payload: dict, output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = payload["date"]
    json_path = output_dir / f"life_radar_{date_str}.json"
    html_path = output_dir / f"life_radar_{date_str}.html"
    latest_path = output_dir / "life_radar.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(payload)
    html_path.write_text(html_text, encoding="utf-8")
    shutil.copyfile(html_path, latest_path)
    return {"json": json_path, "html": html_path, "latest": latest_path}


def render_html(payload: dict) -> str:
    cards = "\n".join(render_theme_card(t) for t in payload["radar"])
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>생활 기사 테마 레이더</title>
<style>
  :root {{
    --bg:#0f1218; --fg:#eef3f8; --muted:#9ea8b5; --panel:#171d26; --panel2:#212936;
    --line:#2d3745; --good:#38ca7e; --wait:#f4b54b; --watch:#6da9ff; --bad:#ff756d; --cyan:#54d7d0;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif; }}
  header {{ position:sticky; top:0; z-index:4; background:#121822; border-bottom:1px solid var(--line); padding:24px 30px 16px; }}
  h1 {{ margin:0; font-size:23px; }}
  h2 {{ margin:0; font-size:18px; }}
  .meta,.muted {{ color:var(--muted); }}
  main {{ padding:22px 30px 40px; }}
  .toolbar {{ display:flex; gap:8px; margin-top:14px; flex-wrap:wrap; }}
  input,button {{ background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:7px 10px; font:inherit; }}
  input {{ min-width:280px; }}
  button {{ cursor:pointer; }}
  button.active {{ background:var(--cyan); color:#061111; border-color:var(--cyan); }}
  .theme {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:14px; }}
  .theme.hot {{ border-left:4px solid var(--good); }}
  .theme.warm {{ border-left:4px solid var(--wait); }}
  .row {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
  .score {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-weight:800; font-size:18px; }}
  .chips span,.badge {{ display:inline-block; background:var(--panel2); border:1px solid var(--line); border-radius:999px; padding:2px 8px; margin:5px 4px 0 0; font-size:12px; }}
  .comment {{ margin-top:9px; color:#dce5ee; }}
  .layout {{ display:grid; grid-template-columns:minmax(280px,.9fr) minmax(360px,1.4fr); gap:12px; margin-top:12px; }}
  .box {{ background:#111821; border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .article {{ margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid var(--line); }}
  .article:last-child {{ margin-bottom:0; padding-bottom:0; border-bottom:0; }}
  a {{ color:var(--cyan); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .stocks {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:10px; }}
  .stock {{ background:#101720; border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .stock.early_candidate {{ border-left:3px solid var(--good); }}
  .stock.wait_pullback {{ border-left:3px solid var(--wait); }}
  .stock.watch {{ border-left:3px solid var(--watch); }}
  .stock.weak {{ opacity:.72; }}
  .name {{ font-weight:700; }}
  .pct {{ color:#ff838b; font-weight:700; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .bars {{ display:grid; grid-template-columns:72px 1fr 40px; gap:8px; align-items:center; color:var(--muted); font-size:12px; margin-top:7px; }}
  .bar {{ height:7px; background:#2b3442; border-radius:999px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:linear-gradient(90deg,var(--cyan),var(--good)); }}
  .spark {{ width:100%; height:118px; margin-top:9px; display:block; border:1px solid #26303d; border-radius:8px; }}
  .hide {{ display:none; }}
  @media (max-width: 880px) {{ .layout {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>생활 기사 테마 레이더</h1>
  <div class="meta">{esc(payload["date"])} · 테마 {payload["theme_count"]}개 · 생성 {esc(payload["generated_at"])}{' · SAMPLE' if payload.get('sample') else ''}</div>
  <div class="toolbar">
    <button class="active" data-filter="all">전체</button>
    <button data-filter="hot">강한 테마</button>
    <button data-filter="early_candidate">초기 후보</button>
    <button data-filter="wait_pullback">눌림 대기</button>
    <input id="search" placeholder="테마, 키워드, 종목, 기사 검색">
  </div>
</header>
<main>{cards}</main>
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
  document.querySelectorAll('[data-tags]').forEach(card => {{
    const tags = card.dataset.tags.split(',');
    const typeOK = filter === 'all' || tags.includes(filter);
    const queryOK = !query || card.dataset.search.includes(query);
    card.classList.toggle('hide', !(typeOK && queryOK));
  }});
}}
</script>
</body>
</html>"""


def render_theme_card(theme: dict) -> str:
    verdicts = sorted({s["verdict"] for s in theme["ranked_stocks"]})
    tags = verdicts[:]
    if theme["final_score"] >= 72:
        tags.append("hot")
    cls = "hot" if theme["final_score"] >= 72 else "warm" if theme["theme_heat_score"] >= 45 else ""
    search = " ".join([
        theme["theme_name"],
        theme["description"],
        " ".join(theme["buy_points"]),
        " ".join(theme["triggers"]),
        " ".join(a["title"] for a in theme["detected_articles"]),
        " ".join(s["name"] for s in theme["ranked_stocks"]),
    ]).lower()
    chips = "".join(f"<span>{esc(x)}</span>" for x in theme["buy_points"])
    articles = "".join(render_article(a) for a in theme["detected_articles"][:5]) or '<div class="muted">감지 기사 없음</div>'
    stocks = "".join(render_stock(s, i + 1) for i, s in enumerate(theme["ranked_stocks"][:6])) or '<div class="muted">차트 랭킹 없음</div>'
    return f"""<section class="theme {cls}" data-tags="{esc(','.join(tags))}" data-search="{esc(search)}">
  <div class="row">
    <div>
      <h2>{esc(theme["theme_name"])}</h2>
      <div class="muted">{esc(theme["description"])}</div>
    </div>
    <div class="score">{theme["final_score"]}</div>
  </div>
  <div class="chips">{chips}</div>
  <div class="comment">{esc(theme["assistant_comment"])}</div>
  <div class="layout">
    <div class="box">
      <div class="row"><b>감지 기사</b><span class="muted mono">heat {theme["theme_heat_score"]}</span></div>
      {articles}
    </div>
    <div class="box">
      <div class="row"><b>관련 종목 차트 랭킹</b><span class="muted">{len(theme["ranked_stocks"])}종목</span></div>
      <div class="stocks">{stocks}</div>
    </div>
  </div>
</section>"""


def render_article(article: dict) -> str:
    title = esc(article["title"])
    link = article.get("link") or ""
    title_html = f'<a href="{esc(link)}" target="_blank" rel="noopener">{title}</a>' if link else title
    triggers = ", ".join(article.get("matched_triggers", []))
    return f"""<div class="article">
  <div>{title_html}</div>
  <div class="muted">{esc(article.get("date", ""))} · {esc(article.get("source", ""))} · {esc(triggers)}</div>
</div>"""


def render_stock(stock: dict, rank: int) -> str:
    return f"""<article class="stock {esc(stock["verdict"])}">
  <div class="row">
    <div><span class="name">{rank}. {esc(stock["name"])}</span> <span class="muted mono">{esc(stock["ticker"])}</span></div>
    <span class="pct mono">{stock["change_pct"]:+.2f}%</span>
  </div>
  <div>
    <span class="badge">{esc(stock["verdict"])}</span>
    <span class="badge">risk {esc(stock["entry_risk"])}</span>
    <span class="badge">{esc(stock["pattern"])}</span>
  </div>
  <div class="bars"><span>차트</span><span class="bar"><i style="width:{stock["chart_score"]}%"></i></span><span class="mono">{stock["chart_score"]}</span></div>
  <div class="bars"><span>랭킹</span><span class="bar"><i style="width:{stock["rank_score"]}%"></i></span><span class="mono">{stock["rank_score"]}</span></div>
  {stock["chart_svg"]}
  <div class="muted">RSI {fmt(stock["rsi14"])} · 거래량 {fmt(stock["volume_ratio_20d"])}x · 20일 이격 {fmt(stock["distance_ma20_pct"])}%</div>
</article>"""


def norm(name: str) -> str:
    return re.sub(r"[\s\(\)\[\]]+", "", name or "").upper()


def risk_order(risk: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "extreme": 3}.get(risk, 9)


def clamp(value: float | int) -> int:
    return max(0, min(100, int(round(value))))


def fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def default_date() -> str:
    return datetime.now().strftime("%Y%m%d")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build life/news theme radar")
    parser.add_argument("--date", default=default_date(), help="YYYYMMDD")
    parser.add_argument("--days", type=int, default=5, help="뉴스 검색 기간")
    parser.add_argument("--sample", action="store_true", help="뉴스 검색 없이 샘플 기사로 빠른 생성")
    parser.add_argument("--max-themes", type=int, default=None, help="처리할 테마 수 제한")
    parser.add_argument("--max-queries", type=int, default=3, help="테마별 검색 키워드 수")
    parser.add_argument("--max-articles", type=int, default=5, help="검색 키워드별 최대 기사 수")
    parser.add_argument("--max-stocks", type=int, default=8, help="테마별 차트 분석 종목 수")
    parser.add_argument("--no-charts", action="store_true", help="HTML 미니 차트 생략")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    radar = build_radar(
        date_str=args.date,
        days=args.days,
        sample=args.sample,
        max_themes=args.max_themes,
        max_queries=args.max_queries,
        max_articles=args.max_articles,
        max_stocks=args.max_stocks,
        with_charts=not args.no_charts,
    )
    payload = build_payload(radar, args.date, args.sample)
    paths = write_outputs(payload, args.output_dir)
    print("\n생활 테마 레이더 생성 완료")
    print(f"  - 날짜: {args.date} / 테마: {payload['theme_count']}")
    for key, path in paths.items():
        print(f"  - {key}: {path}")


if __name__ == "__main__":
    main()

