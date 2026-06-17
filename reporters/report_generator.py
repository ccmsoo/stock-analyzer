"""
리포트 생성 모듈
================
- Markdown 일일 리포트 (사람이 읽기 쉬운 요약)
- CSV 누적 (백테스트/통계)
- HTML 일일 리포트 (테마별 그룹뷰 포함)
- 상태(state)를 함께 받아 대시보드 데이터(JSON)도 갱신
"""
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


REASON_UNKNOWN_LABELS = {
    'no_news_in_window': '윈도우 내 뉴스 없음',
    'headline_only_generic': '제목만 일반 시황·랭킹',
    'lagging_article': '기사 날짜 지연(D-2 이전)',
    'weak_name_link': '종목명-기사 연결 약함',
    'theme_only_supply': '수급/테마성만',
    'data_missing': '수집/본문 누락',
    'other': '기타',
    '': '',
}


def _article_diagnostics(articles: list, date_str: str) -> dict:
    """기사 묶음의 진단 메타 (article_count / origin_dist / latest_date / trigger_lag)."""
    origin_dist: dict[str, int] = {}
    latest_dt = None
    body_count = 0
    for a in articles:
        origin_dist[a.get('origin', '?')] = origin_dist.get(a.get('origin', '?'), 0) + 1
        if len((a.get('body') or '').strip()) >= 100:
            body_count += 1
        try:
            dt = datetime.strptime(a.get('date', ''), '%Y.%m.%d %H:%M')
        except Exception:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
    trigger_lag = None
    try:
        if latest_dt:
            trigger_lag = (datetime.strptime(date_str, '%Y%m%d') - latest_dt).days
    except Exception:
        pass
    return {
        'article_count': len(articles),
        'body_count': body_count,
        'origin_dist': origin_dist,
        'latest_article_date': latest_dt.strftime('%Y-%m-%d %H:%M') if latest_dt else '',
        'trigger_lag_candidate': trigger_lag if trigger_lag is not None else '',
    }


SECTION_TITLES = {
    'new': '🆕 신규 시그널 (오늘 매칭됨)',
    'continuation': '🔁 연속 상승 (이전 시그널 유지)',
    'unclear': '❓ 이유 불명 (원인 미상 — 추적 필요)',
}


def _stock_md(stock, articles, analysis, date_str=''):
    conf = (analysis.get('confidence') or 'low').lower()
    emoji = {'high': '🟢', 'medium': '🟡', 'low': '🔴'}.get(conf, '⚪')
    md = f"""
### [{stock['ticker']}] {stock['name']}  `{stock['change_pct']:+.2f}%`  {emoji}

- **시장**: {stock['market']} | **종가**: {stock['close']:,}원 | **거래량**: {stock['volume']:,}주
- **1차 테마**: {analysis.get('main_theme', '-')}
- **🎯 지엽적 시그널**: **{analysis.get('specific_signal', '-')}**
- **트리거 유형**: `{analysis.get('trigger_type', '-')}`
"""
    if analysis.get('trigger_date'):
        md += f"- **트리거 일자**: {analysis['trigger_date']} (D-{analysis.get('trigger_lag_days', '?')}일)\n"
    if analysis.get('reason_unknown_category'):
        ruc = analysis['reason_unknown_category']
        md += f"- **이유 불명 카테고리**: `{ruc}` — {REASON_UNKNOWN_LABELS.get(ruc, '')}\n"
        diag = _article_diagnostics(articles, date_str)
        md += (
            f"- **수집 진단**: 기사 {diag['article_count']}건 · 본문 {diag['body_count']}건 · "
            f"origin {diag['origin_dist']} · "
            f"최신기사 {diag['latest_article_date'] or '없음'} · "
            f"trigger_lag 후보 {diag['trigger_lag_candidate']}일\n"
        )
    md += (
        f"- **추정 근거**: {analysis.get('reasoning', '-')}\n"
        f"- **연관 종목**: {', '.join(analysis.get('related_stocks', [])) or '-'}\n"
        f"- **추적 키워드**: {', '.join(f'`{k}`' for k in analysis.get('watch_keywords', []))}\n\n"
        f"<details><summary>📰 관련 뉴스 ({len(articles)}건)</summary>\n\n"
    )
    for a in articles:
        origin = a.get('origin', 'stock_news')
        md += f"- [{a.get('date','')}] [{a['title']}]({a['link']}) — *{a.get('source','')}* `{origin}`\n"
    md += "\n</details>\n"
    return md


def _all_stocks(movers):
    return list(movers.get('kospi_up', [])) + list(movers.get('kosdaq_up', []))


def _bucket_by_status(stocks, status_map):
    """status별로 종목을 나눔. status_map[t] = 'new'|'continuation'|'unclear'"""
    buckets = {'new': [], 'continuation': [], 'unclear': []}
    for s in stocks:
        buckets.get(status_map.get(s['ticker'], 'new'), buckets['new']).append(s)
    return buckets


def _group_by_theme(stocks, analysis):
    """specific_signal이 같은 종목끼리 묶음. specific_signal 없으면 main_theme로 fallback."""
    groups = defaultdict(list)
    for s in stocks:
        a = analysis.get(s['ticker'], {})
        key = (a.get('specific_signal') or a.get('main_theme') or '미분류').strip()
        groups[key].append(s)
    # 멤버 많은 그룹 먼저
    return sorted(groups.items(), key=lambda kv: -len(kv[1]))


# ─────────────────────── HTML ───────────────────────

HTML_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>일일 분석 — {date_str}</title>
<style>
  :root {{ --bg:#0b0d10; --fg:#e6e6e6; --muted:#8a929b; --card:#151a20; --border:#23272e;
           --hi:#2dd4bf; --new:#3b82f6; --cont:#a78bfa; --unclear:#f59e0b;
           --conf-high:#22c55e; --conf-med:#eab308; --conf-low:#ef4444; }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--fg); font: 14px/1.55 -apple-system, "Pretendard", sans-serif; margin: 0; padding: 24px 32px; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  h2 {{ margin: 28px 0 12px; font-size: 16px; color: var(--hi); border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  h3 {{ margin: 16px 0 8px; font-size: 14px; color: var(--muted); }}
  .meta {{ color: var(--muted); margin-bottom: 18px; }}
  nav.tabs {{ display:flex; gap:6px; margin: 16px 0 10px; }}
  nav.tabs button {{ background: var(--card); color: var(--fg); border: 1px solid var(--border); padding: 6px 12px; cursor: pointer; border-radius: 6px; font-size: 13px; }}
  nav.tabs button.active {{ background: var(--hi); color:#000; border-color: var(--hi); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }}
  .card.new {{ border-left: 3px solid var(--new); }}
  .card.continuation {{ border-left: 3px solid var(--cont); }}
  .card.unclear {{ border-left: 3px solid var(--unclear); }}
  .row {{ display:flex; justify-content:space-between; align-items:baseline; gap: 8px; }}
  .name {{ font-weight: 600; }}
  .ticker {{ color: var(--muted); font-size: 12px; font-family: ui-monospace, monospace; }}
  .pct {{ font-family: ui-monospace, monospace; font-weight: 600; color: #fb7185; }}
  .signal {{ margin: 6px 0; color: var(--hi); }}
  .keywords span {{ display: inline-block; background:#1e2530; border:1px solid var(--border); border-radius: 4px; padding: 1px 6px; margin: 2px 4px 0 0; font-size: 12px; color: var(--muted); font-family: ui-monospace, monospace; }}
  .conf {{ font-size: 11px; padding: 1px 6px; border-radius: 999px; font-weight: 600; }}
  .conf.high {{ background: rgba(34,197,94,.15); color: var(--conf-high); }}
  .conf.medium {{ background: rgba(234,179,8,.15); color: var(--conf-med); }}
  .conf.low {{ background: rgba(239,68,68,.15); color: var(--conf-low); }}
  details summary {{ cursor:pointer; color: var(--muted); font-size: 12px; margin-top: 8px; }}
  details ul {{ padding-left: 16px; margin: 6px 0 0; }}
  details a {{ color: var(--fg); }}
  .reasoning {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
  .theme-group {{ margin: 18px 0; }}
  .theme-title {{ font-weight: 600; color: var(--fg); margin-bottom: 6px; font-size: 14px; }}
  .theme-title small {{ color: var(--muted); font-weight: 400; }}
  .section {{ display: none; }}
  .section.active {{ display: block; }}
</style>
</head>
<body>
<h1>📊 {date_str} 일일 분석</h1>
<div class="meta">
  KOSPI 상승 {n_kospi} · KOSDAQ 상승 {n_kosdaq} · 신규 {n_new} · 연속 {n_cont} · 불명 {n_unclear}
</div>
<nav class="tabs">
  <button class="active" data-tab="status">상태별</button>
  <button data-tab="theme">테마별</button>
  <button data-tab="all">전체</button>
</nav>

<section id="tab-status" class="section active">
{status_sections}
</section>

<section id="tab-theme" class="section">
<h2>🎯 테마별 그룹</h2>
{theme_groups}
</section>

<section id="tab-all" class="section">
<h2>🧾 전체 {n_total}개</h2>
<div class="grid">
{all_cards}
</div>
</section>

<script>
document.querySelectorAll('nav.tabs button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('nav.tabs button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('tab-' + b.dataset.tab).classList.add('active');
}}));
</script>
</body>
</html>
"""


def _card_html(stock, articles, analysis, status, date_str=''):
    conf = (analysis.get('confidence') or 'low').lower()
    signal = analysis.get('specific_signal') or '—'
    theme = analysis.get('main_theme') or '미분류'
    kws = ''.join(f'<span>{k}</span>' for k in analysis.get('watch_keywords', []))
    news_items = ''.join(
        f'<li><a href="{a["link"]}" target="_blank" rel="noopener">{a["title"]}</a> '
        f'<small style="color:var(--muted)">[{a.get("date","")}] {a.get("source","")} · {a.get("origin","")}</small></li>'
        for a in articles
    )
    trig_line = ''
    if analysis.get('trigger_date'):
        trig_line = f'<div class="reasoning">📅 트리거: {analysis["trigger_date"]} (D-{analysis.get("trigger_lag_days","?")}일)</div>'

    unknown_block = ''
    ruc = analysis.get('reason_unknown_category') or ''
    if ruc:
        diag = _article_diagnostics(articles, date_str)
        origin_str = ', '.join(f'{k}:{v}' for k, v in diag['origin_dist'].items()) or '(없음)'
        unknown_block = (
            f'<div class="unknown-diag" style="margin-top:6px; padding:6px 8px; '
            f'background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.35); '
            f'border-radius:6px; font-size:12px; color:var(--muted)">'
            f'<div style="color:#f59e0b; font-weight:600">❓ 이유 불명: <code>{ruc}</code> '
            f'— {REASON_UNKNOWN_LABELS.get(ruc, "")}</div>'
            f'<div>기사 {diag["article_count"]}건 · 본문 {diag["body_count"]}건 · origin {origin_str} · '
            f'최신기사 {diag["latest_article_date"] or "없음"} · '
            f'trigger_lag 후보 {diag["trigger_lag_candidate"]}일</div>'
            f'</div>'
        )

    return f'''<div class="card {status}">
  <div class="row">
    <div><span class="name">{stock["name"]}</span> <span class="ticker">{stock["ticker"]} · {stock["market"]}</span></div>
    <span class="pct">{stock["change_pct"]:+.2f}%</span>
  </div>
  <div class="row" style="margin-top:4px">
    <span style="color:var(--muted); font-size:12px">{theme}</span>
    <span class="conf {conf}">{conf}</span>
  </div>
  <div class="signal">🎯 {signal}</div>
  <div class="reasoning">{analysis.get("reasoning","")}</div>
  {trig_line}
  {unknown_block}
  <div class="keywords">{kws}</div>
  <details><summary>📰 관련 뉴스 {len(articles)}건</summary><ul>{news_items}</ul></details>
</div>'''


def _render_html(date_str, movers, news_data, analysis, status_map):
    all_stocks = _all_stocks(movers)
    buckets = _bucket_by_status(all_stocks, status_map)

    def cards_for(stocks):
        return '\n'.join(
            _card_html(s, news_data.get(s['ticker'], []), analysis.get(s['ticker'], {}), status_map.get(s['ticker'], 'new'), date_str=date_str)
            for s in stocks
        )

    status_sections_html = ''
    for status in ('new', 'continuation', 'unclear'):
        stocks = buckets[status]
        if not stocks:
            continue
        status_sections_html += f'<h2>{SECTION_TITLES[status]} <small style="color:var(--muted)">({len(stocks)})</small></h2>\n'
        status_sections_html += f'<div class="grid">{cards_for(stocks)}</div>\n'

    theme_groups_html = ''
    for theme, stocks in _group_by_theme(all_stocks, analysis):
        theme_groups_html += (
            f'<div class="theme-group">'
            f'<div class="theme-title">{theme} <small>({len(stocks)}종목)</small></div>'
            f'<div class="grid">{cards_for(stocks)}</div>'
            f'</div>\n'
        )

    return HTML_TEMPLATE.format(
        date_str=date_str,
        n_kospi=len(movers.get('kospi_up', [])),
        n_kosdaq=len(movers.get('kosdaq_up', [])),
        n_new=len(buckets['new']),
        n_cont=len(buckets['continuation']),
        n_unclear=len(buckets['unclear']),
        n_total=len(all_stocks),
        status_sections=status_sections_html,
        theme_groups=theme_groups_html,
        all_cards=cards_for(all_stocks),
    )


# ─────────────────────── Public ───────────────────────

def generate_report(date_str, movers, news_data, analysis, status_map, output_dir, state=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    md_path = output_dir / f"report_{date_str}.md"
    csv_path = output_dir / f"report_{date_str}.csv"
    html_path = output_dir / f"report_{date_str}.html"

    all_stocks = _all_stocks(movers)
    buckets = _bucket_by_status(all_stocks, status_map)

    # ── Markdown ──
    md = f"# 📊 한국 주식 일일 분석 리포트\n\n**기준일**: {date_str}\n\n"
    md += "> 🟢 high · 🟡 medium · 🔴 low (분석 신뢰도)\n\n---\n\n"

    # 이유 불명 카테고리 분포 (요약)
    unclear_stocks = buckets.get('unclear', [])
    if unclear_stocks:
        from collections import Counter
        ruc_counter = Counter(
            (analysis.get(s['ticker'], {}).get('reason_unknown_category') or 'uncategorized')
            for s in unclear_stocks
        )
        md += "\n## ❓ 이유 불명 카테고리 분포\n\n| 카테고리 | 건수 |\n|---|---:|\n"
        for k, v in ruc_counter.most_common():
            label = REASON_UNKNOWN_LABELS.get(k, k)
            md += f"| `{k}` ({label}) | {v} |\n"
        md += "\n"

    for status in ('new', 'continuation', 'unclear'):
        stocks = buckets[status]
        if not stocks:
            continue
        md += f"\n## {SECTION_TITLES[status]} ({len(stocks)})\n"
        for s in stocks:
            md += _stock_md(s, news_data.get(s['ticker'], []), analysis.get(s['ticker'], {}), date_str=date_str)

    # 테마 그룹 추가
    md += "\n\n---\n\n## 🎯 테마별 그룹\n"
    for theme, stocks in _group_by_theme(all_stocks, analysis):
        md += f"\n### {theme} ({len(stocks)}종목)\n"
        for s in stocks:
            md += f"- [{s['ticker']}] **{s['name']}** `{s['change_pct']:+.2f}%`  ·  {analysis.get(s['ticker'], {}).get('confidence','-')}\n"

    md_path.write_text(md, encoding='utf-8')

    # ── CSV ──
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow([
            'date', 'market', 'ticker', 'name', 'close', 'change_pct', 'volume',
            'status', 'main_theme', 'specific_signal', 'trigger_type', 'trigger_date', 'trigger_lag_days',
            'confidence', 'reasoning', 'related_stocks', 'watch_keywords', 'news_count',
            'body_count', 'reason_unknown_category', 'article_origin_dist', 'latest_article_date', 'trigger_lag_candidate',
        ])
        for s in all_stocks:
            a = analysis.get(s['ticker'], {})
            arts = news_data.get(s['ticker'], [])
            diag = _article_diagnostics(arts, date_str)
            writer.writerow([
                date_str, s['market'], s['ticker'], s['name'],
                s['close'], s['change_pct'], s['volume'],
                status_map.get(s['ticker'], ''),
                a.get('main_theme', ''), a.get('specific_signal', ''),
                a.get('trigger_type', ''), a.get('trigger_date', ''), a.get('trigger_lag_days', ''),
                a.get('confidence', ''),
                a.get('reasoning', ''),
                ', '.join(a.get('related_stocks', [])),
                ', '.join(a.get('watch_keywords', [])),
                len(arts),
                diag['body_count'],
                a.get('reason_unknown_category', ''),
                '; '.join(f'{k}:{v}' for k, v in diag['origin_dist'].items()),
                diag['latest_article_date'],
                diag['trigger_lag_candidate'],
            ])

    # ── HTML 일일 페이지 ──
    html_path.write_text(_render_html(date_str, movers, news_data, analysis, status_map), encoding='utf-8')

    # ── 대시보드용 JSON (state 전체 + 일일 요약 머지) ──
    if state is not None:
        _write_dashboard_data(output_dir, state)

    print(f"   - Markdown: {md_path}")
    print(f"   - CSV: {csv_path}")
    print(f"   - HTML: {html_path}")
    return md_path


def _write_dashboard_data(output_dir, state):
    """대시보드(index.html)가 fetch할 누적 데이터"""
    data = {
        "generated_at": __import__('datetime').datetime.now().isoformat(timespec='seconds'),
        "signals": state.get("signals", {}),
    }
    (output_dir / "dashboard.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )
