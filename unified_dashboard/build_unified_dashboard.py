"""
Build one homepage-style dashboard from the separate analysis outputs.

The underlying engines stay modular, but the user experience becomes one
continuous UI:
  - market overview
  - news x chart candidates
  - article keyword opportunities
  - life/news theme radar
  - links to detailed reports
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def latest_file(pattern: str, base: Path) -> Path | None:
    files = sorted(base.glob(pattern))
    return files[-1] if files else None


def load_json(path: Path | None, default):
    if not path or not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def compute_quiet_signals(state: dict, lookback_days: int = 5, top_n: int = 15) -> list[dict]:
    """최근 N일 high confidence + 펀더멘털 트리거 시그널 중
    D-day 후 가격이 잠잠한 종목 — "시스템은 강한 호재로 봤는데 시장이 아직 반응 안 함".

    조건:
      - confidence == high
      - trigger_type in {disclosure, earnings, contract, policy}
      - D-day 이후 누적 변동 < +10% (덜 움직임)
      - D-day 가 최근 lookback_days 영업일 이내

    반환: 각 종목의 D-day, 트리거, 가격 추세 dict 리스트
    """
    import csv as _csv
    import concurrent.futures as _cf
    try:
        import FinanceDataReader as _fdr
    except ImportError:
        return []

    # 최근 영업일 lookback_days 자 추출
    report_dir = ROOT / "reports"
    csv_files = sorted(report_dir.glob("report_2*.csv"))[-lookback_days:]
    if not csv_files:
        return []

    targets = []
    for p in csv_files:
        try:
            rows = list(_csv.DictReader(open(p, encoding="utf-8-sig")))
        except Exception:
            continue
        d = p.stem.replace("report_", "")
        for r in rows:
            if (r.get("confidence") or "").lower() != "high":
                continue
            if (r.get("trigger_type") or "") not in ("disclosure", "earnings", "contract", "policy"):
                continue
            targets.append({
                "date": d, "ticker": r["ticker"], "name": r["name"],
                "trigger": r["trigger_type"],
                "signal": (r.get("specific_signal") or "")[:80],
                "watch_keywords": [k.strip() for k in (r.get("watch_keywords") or "").split(",") if k.strip()][:4],
            })

    def _check(c):
        try:
            df = _fdr.DataReader(c["ticker"], "2026-04-25", "2026-05-15")
            if len(df) < 3:
                return None
            dates = list(df.index.strftime("%Y%m%d"))
            if c["date"] not in dates:
                return None
            d_idx = dates.index(c["date"])
            d_close = float(df["Close"].iloc[d_idx])
            cur = float(df["Close"].iloc[-1])
            cur_open = float(df["Open"].iloc[-1])
            prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else cur
            accum = (cur / d_close - 1) * 100 if d_close else 0
            today = (cur / prev - 1) * 100 if prev else 0
            from_open = (cur / cur_open - 1) * 100 if cur_open else 0
            return {
                **c,
                "d_close": int(d_close),
                "current_close": int(cur),
                "accum_pct": round(accum, 2),
                "today_chg_pct": round(today, 2),
                "from_open_pct": round(from_open, 2),
            }
        except Exception:
            return None

    results = []
    with _cf.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(_check, targets):
            if r and -10 < r["accum_pct"] < 10:
                results.append(r)

    # 잠잠한 순 정렬 (누적 변동 절댓값 작은 순 — 미스매치 가장 큰 것)
    results.sort(key=lambda r: abs(r["accum_pct"]))
    return results[:top_n]


def build_payload() -> dict:
    combined_path = latest_file("combined_signals_*.json", ROOT / "visual_dashboard" / "output")
    opportunities_path = latest_file("opportunities_*.json", ROOT / "opportunity_engine" / "output")
    life_path = latest_file("life_radar_*.json", ROOT / "life_theme_radar" / "output")
    chart_path = latest_file("chart_report_*.json", ROOT / "chart_analysis" / "output")

    combined = load_json(combined_path, {"signals": [], "signal_counts": {}, "date": ""})
    opportunities = load_json(opportunities_path, {"opportunities": [], "date": ""})
    life = load_json(life_path, {"radar": [], "date": ""})
    chart = load_json(chart_path, {"results": [], "date": ""})

    # state 누적 통계 — "업데이트 됐는지" 사용자가 체감할 수 있게 표시용
    state_path = ROOT / "state" / "signals.json"
    cumulative = {"total_signals": 0, "unique_watch_kw": 0, "unique_deep_kw": 0,
                  "by_confidence": {}, "last_update": "", "covered_dates": []}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sigs = state.get("signals", {})
        cumulative["total_signals"] = len(sigs)
        wkw = set(); dkw = set(); dates = set(); conf = {}
        for s in sigs.values():
            for kw in (s.get("watch_keywords") or []):
                if kw.strip(): wkw.add(kw.strip())
            for cat in ("products", "partners", "places", "events", "people"):
                for kw in (s.get("deep_keywords", {}) or {}).get(cat, []) or []:
                    if kw.strip(): dkw.add(kw.strip())
            if s.get("last_seen"): dates.add(s["last_seen"])
            c = (s.get("confidence") or "").lower() or "none"
            conf[c] = conf.get(c, 0) + 1
        cumulative["unique_watch_kw"] = len(wkw)
        cumulative["unique_deep_kw"] = len(dkw)
        cumulative["by_confidence"] = conf
        cumulative["covered_dates"] = sorted(dates)
        if dates: cumulative["last_update"] = max(dates)
    except Exception:
        pass

    # "시스템 강한 호재 vs 시장 아직 잠잠" 종목 — 매번 빌드 시 갱신
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        quiet_signals = compute_quiet_signals(state, lookback_days=5, top_n=12)
    except Exception:
        quiet_signals = []

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "combined": str(combined_path) if combined_path else "",
            "opportunities": str(opportunities_path) if opportunities_path else "",
            "life": str(life_path) if life_path else "",
            "chart": str(chart_path) if chart_path else "",
        },
        "combined": combined,
        "opportunities": opportunities,
        "life": life,
        "chart": chart,
        "cumulative": cumulative,
        "quiet_signals": quiet_signals,
    }


def write_outputs(payload: dict, output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "unified_dashboard.json"
    html_path = output_dir / "index.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")

    # Convenience copy in reports/, so the existing reports folder can act as
    # the project homepage if the user opens it.
    reports_home = ROOT / "reports" / "home.html"
    shutil.copyfile(html_path, reports_home)
    return {"json": json_path, "html": html_path, "reports_home": reports_home}


def render_html(payload: dict) -> str:
    combined = payload["combined"]
    opportunities = payload["opportunities"]
    life = payload["life"]
    top_signals = combined.get("signals", [])[:12]
    top_opps = opportunities.get("opportunities", [])[:10]
    top_life = life.get("radar", [])[:10]

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Stock Analyzer Home</title>
<style>
  :root {{
    --bg:#0d1117; --fg:#edf2f7; --muted:#9da8b5; --panel:#161b22; --panel2:#202734;
    --line:#2b3442; --green:#35c77b; --blue:#6ea8fe; --yellow:#f4b64a; --red:#ff6f68; --cyan:#54d7d0;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif; }}
  aside {{ position:fixed; inset:0 auto 0 0; width:250px; padding:22px 16px; background:#111720; border-right:1px solid var(--line); }}
  main {{ margin-left:250px; padding:24px 30px 44px; }}
  h1 {{ margin:0 0 4px; font-size:22px; }}
  h2 {{ margin:28px 0 12px; font-size:17px; color:var(--cyan); }}
  h3 {{ margin:0; font-size:15px; }}
  .muted {{ color:var(--muted); }}
  .nav {{ display:flex; flex-direction:column; gap:6px; margin-top:20px; }}
  .nav button,.link {{ width:100%; text-align:left; background:transparent; border:1px solid transparent; color:var(--fg); padding:8px 10px; border-radius:6px; cursor:pointer; text-decoration:none; font:inherit; }}
  .nav button.active,.nav button:hover,.link:hover {{ background:var(--panel2); border-color:var(--line); }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
  .kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  .kpi b {{ display:block; font-size:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:12px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:14px; }}
  .card.good {{ border-left:4px solid var(--green); }}
  .card.wait {{ border-left:4px solid var(--yellow); }}
  .card.watch {{ border-left:4px solid var(--blue); }}
  .card.bad {{ opacity:.76; }}
  .row {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
  .name {{ font-weight:700; }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .pct {{ color:#ff848c; font-weight:700; }}
  .badge {{ display:inline-block; background:var(--panel2); border:1px solid var(--line); border-radius:999px; padding:2px 8px; margin:6px 4px 0 0; font-size:12px; }}
  .signal {{ color:var(--cyan); margin-top:8px; font-weight:600; }}
  .note {{ margin-top:8px; color:#dce5ee; }}
  .section {{ display:none; }}
  .section.active {{ display:block; }}
  input {{ width:100%; background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin-top:14px; }}
  .mini-list {{ display:flex; flex-direction:column; gap:8px; }}
  .list-item {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }}
  a {{ color:var(--cyan); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .hide {{ display:none; }}
  @media (max-width: 900px) {{
    aside {{ position:static; width:auto; border-right:0; border-bottom:1px solid var(--line); }}
    main {{ margin-left:0; }}
    .nav {{ flex-direction:row; flex-wrap:wrap; }}
    .nav button,.link {{ width:auto; }}
  }}
</style>
</head>
<body>
<aside>
  <h1>Stock Analyzer</h1>
  <div class="muted">뉴스, 키워드, 차트가 한 화면에 모이는 홈</div>
  <input id="search" placeholder="검색: 종목/테마/키워드">
  <nav class="nav">
    <button class="active" data-tab="overview">홈</button>
    <button data-tab="signals">뉴스 × 차트</button>
    <button data-tab="opportunities">기회 카드</button>
    <button data-tab="life">생활 테마</button>
    <button data-tab="links">상세 보기</button>
  </nav>
  <div class="muted" style="margin-top:18px;font-size:12px">생성 {esc(payload["generated_at"])}</div>
</aside>
<main>
  <section id="tab-overview" class="section active">
    {render_status_banner(payload)}
    <h2>오늘의 요약</h2>
    {render_kpis(payload)}
    {render_quiet_section(payload)}
    <h2>가장 강한 후보</h2>
    <div class="grid">{''.join(render_signal_card(s) for s in top_signals[:6])}</div>
    <h2>상위 기회 카드</h2>
    <div class="grid">{''.join(render_opp_card(o) for o in top_opps[:4])}</div>
  </section>

  <section id="tab-signals" class="section">
    <h2>뉴스 × 차트 통합 후보</h2>
    <div class="grid">{''.join(render_signal_card(s) for s in top_signals)}</div>
  </section>

  <section id="tab-opportunities" class="section">
    <h2>기사 키워드 기회 카드</h2>
    <div class="grid">{''.join(render_opp_card(o) for o in top_opps)}</div>
  </section>

  <section id="tab-life" class="section">
    <h2>생활 기사 테마 레이더</h2>
    <div class="grid">{''.join(render_life_card(t) for t in top_life)}</div>
  </section>

  <section id="tab-links" class="section">
    <h2>상세 보기</h2>
    <div class="mini-list">
      {render_links()}
    </div>
  </section>
</main>
<script>
const buttons = [...document.querySelectorAll('button[data-tab]')];
buttons.forEach(btn => btn.addEventListener('click', () => {{
  buttons.forEach(b => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
}}));
document.getElementById('search').addEventListener('input', e => {{
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('[data-search]').forEach(el => {{
    el.classList.toggle('hide', q && !el.dataset.search.includes(q));
  }});
}});
</script>
</body>
</html>"""


def render_status_banner(payload: dict) -> str:
    """상단 상태 배너 — 분석 기준일·누적 통계·마지막 갱신 시각 표시."""
    cum = payload.get("cumulative", {})
    combined = payload["combined"]
    analysis_date = combined.get("date") or "-"
    generated_at = payload.get("generated_at", "")
    # ISO 의 T 를 공백으로 → 보기 쉽게
    gen_disp = generated_at.replace("T", " ") if generated_at else ""
    total = cum.get("total_signals", 0)
    wkw = cum.get("unique_watch_kw", 0)
    dkw = cum.get("unique_deep_kw", 0)
    by_conf = cum.get("by_confidence", {})
    high = by_conf.get("high", 0); med = by_conf.get("medium", 0); low = by_conf.get("low", 0)
    dates = cum.get("covered_dates", [])
    date_span = f'{dates[0]} ~ {dates[-1]}' if dates else '-'
    return f'''
<div style="background:linear-gradient(135deg,#1a2331,#162028); border:1px solid var(--line);
            border-radius:12px; padding:16px 20px; margin-bottom:20px;">
  <div style="display:flex; flex-wrap:wrap; gap:8px 24px; align-items:baseline;">
    <h2 style="margin:0; color:var(--cyan); font-size:18px;">📊 분석 기준: <span style="color:var(--fg)">{esc(analysis_date)}</span></h2>
    <span class="muted" style="font-size:12px;">마지막 빌드: {esc(gen_disp)}</span>
  </div>
  <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:6px 16px; font-size:13px;">
    <span>📦 누적 시그널 <b style="color:var(--cyan);">{total}</b>종목</span>
    <span>🏷 watch_keywords <b style="color:var(--cyan);">{wkw}</b>개</span>
    <span>🔍 deep_keywords <b style="color:var(--cyan);">{dkw}</b>개</span>
    <span>🟢 high <b>{high}</b> · 🟡 medium <b>{med}</b> · 🔴 low <b>{low}</b></span>
    <span class="muted">커버 일자: {esc(date_span)} ({len(dates)}일)</span>
  </div>
</div>'''


def render_quiet_section(payload: dict) -> str:
    """시스템 high signal vs 시장 잠잠 — 메인 화면 핵심 섹션."""
    quiet = payload.get("quiet_signals", [])
    if not quiet:
        return ''

    cards = []
    trigger_color = {
        "earnings": "#54d7d0", "disclosure": "#6ea8fe",
        "contract": "#35c77b", "policy": "#f4b64a",
    }
    for r in quiet:
        accum = r.get("accum_pct", 0)
        today = r.get("today_chg_pct", 0)
        fo = r.get("from_open_pct", 0)
        trig_color = trigger_color.get(r.get("trigger", ""), "#9da8b5")
        # 매력도 — 누적 변동 작고 + 오늘 시초比 + 면 ✨
        if abs(accum) < 5 and fo > -2:
            flag = '✨'
            badge_color = '#35c77b'
        elif accum > 0:
            flag = '🟢'
            badge_color = '#6ea8fe'
        else:
            flag = '🟡'
            badge_color = '#f4b64a'
        kws = ''.join(f'<span class="badge">{esc(k)}</span>' for k in r.get("watch_keywords", [])[:3])
        # 가격 변화 색
        accum_c = '#35c77b' if accum > 0 else '#ff6f68'
        today_c = '#35c77b' if today > 0 else '#ff6f68'
        cards.append(f"""
<article class="card" style="border-left:3px solid {badge_color};">
  <div class="row">
    <div>
      <span class="name">{flag} {esc(r["name"])}</span>
      <span class="muted mono">{esc(r["ticker"])} · D{r["date"][-4:]}</span>
    </div>
    <span class="mono" style="font-weight:700; color:{accum_c};">{accum:+.1f}%</span>
  </div>
  <div style="margin-top:4px;">
    <span class="badge" style="border-color:{trig_color}; color:{trig_color}">{esc(r.get("trigger",""))}</span>
    <span class="badge">D날 종가 {r.get("d_close",0):,}원 → 현재 {r.get("current_close",0):,}원</span>
    <span class="badge">오늘 <span style="color:{today_c}">{today:+.1f}%</span> · 시초比 {fo:+.1f}%</span>
  </div>
  <div class="signal" style="margin-top:6px;">{esc(r.get("signal",""))}</div>
  <div style="margin-top:6px;">{kws}</div>
</article>""")

    return f"""
<h2 style="color:#f4b64a;">🎯 호재는 잡혔지만 시장이 아직 잠잠 <span class="muted" style="font-size:12px;">— 최근 5영업일 high+펀더멘털 시그널 중 D-day 이후 ±10% 이내</span></h2>
<div style="background:rgba(244,182,74,0.05); border:1px solid rgba(244,182,74,0.25); border-radius:8px; padding:12px; margin-bottom:14px;">
  <div class="muted" style="font-size:12px; margin-bottom:8px;">
    시스템이 명확한 호재(실적/공시/수주/정책)로 high confidence 잡았으나 시장이 D-day 이후에도 충분히 반영하지 않은 종목.
    ✨ = 가장 잠잠 + 매수세 살아있음, 🟢 = 약상승, 🟡 = 약하락 (저가 매수 기회 가능).
  </div>
  <div class="grid">{''.join(cards)}</div>
</div>"""


def render_kpis(payload: dict) -> str:
    combined = payload["combined"]
    opps = payload["opportunities"]
    life = payload["life"]
    counts = combined.get("signal_counts", {})
    items = [
        ("핵심 후보", counts.get("strong_watch", 0)),
        ("눌림 대기", counts.get("wait_pullback", 0)),
        ("기회 카드", opps.get("opportunity_count", len(opps.get("opportunities", [])))),
        ("생활 테마", life.get("theme_count", len(life.get("radar", [])))),
        ("차트 결합", combined.get("chart_count", 0)),
    ]
    return '<div class="kpis">' + ''.join(
        f'<div class="kpi"><b>{v}</b><span class="muted">{esc(k)}</span></div>'
        for k, v in items
    ) + '</div>'


def render_signal_card(s: dict) -> str:
    signal = s.get("trade_signal", "")
    cls = "good" if signal == "strong_watch" else "wait" if signal == "wait_pullback" else "watch" if signal == "theme_watch" else "bad"
    search = " ".join(str(s.get(k, "")) for k in ["name", "ticker", "main_theme", "specific_signal", "watch_keywords", "trade_signal"]).lower()
    return f"""<article class="card {cls}" data-search="{esc(search)}">
  <div class="row"><div><span class="name">{esc(s.get("name",""))}</span> <span class="muted mono">{esc(s.get("ticker",""))}</span></div><span class="pct mono">{to_float(s.get("change_pct")):+.2f}%</span></div>
  <span class="badge">{esc(signal)}</span><span class="badge">risk {esc(s.get("chart_risk",""))}</span><span class="badge">{esc(s.get("confidence",""))}</span>
  <div class="signal">{esc(s.get("specific_signal") or s.get("main_theme",""))}</div>
  <div class="note">{esc(s.get("entry_note",""))}</div>
  <div class="muted mono">종합 {s.get("combined_score","-")} · 뉴스 {s.get("news_score","-")} · 차트 {s.get("chart_score","-")}</div>
</article>"""


def render_opp_card(o: dict) -> str:
    stocks = o.get("ranked_stocks", [])
    best = stocks[0] if stocks else {}
    verdict = best.get("verdict", "pass")
    cls = "good" if verdict == "best_candidate" else "wait" if verdict == "wait_pullback" else "watch"
    search = " ".join([o.get("title", ""), o.get("theme", ""), " ".join(o.get("buy_keywords", [])), " ".join(s.get("name","") for s in stocks)]).lower()
    stock_line = ", ".join(f'{s.get("name")} {s.get("rank_score")}' for s in stocks[:3])
    return f"""<article class="card {cls}" data-search="{esc(search)}">
  <div class="row"><h3>{esc(o.get("title",""))}</h3><span class="mono">{o.get("opportunity_score","-")}</span></div>
  <span class="badge">{esc(o.get("theme",""))}</span><span class="badge">{esc(o.get("directness",""))}</span>
  <div class="signal">{esc(", ".join(o.get("buy_keywords", [])[:4]))}</div>
  <div class="note">{esc(o.get("assistant_comment",""))}</div>
  <div class="muted">상위: {esc(stock_line)}</div>
</article>"""


def render_life_card(t: dict) -> str:
    stocks = t.get("ranked_stocks", [])
    best = stocks[0] if stocks else {}
    verdict = best.get("verdict", "weak")
    cls = "good" if verdict == "early_candidate" else "wait" if verdict == "wait_pullback" else "watch"
    articles = len(t.get("detected_articles", []))
    search = " ".join([t.get("theme_name", ""), t.get("description", ""), " ".join(t.get("buy_points", [])), " ".join(s.get("name","") for s in stocks)]).lower()
    return f"""<article class="card {cls}" data-search="{esc(search)}">
  <div class="row"><h3>{esc(t.get("theme_name",""))}</h3><span class="mono">{t.get("final_score","-")}</span></div>
  <span class="badge">기사 {articles}</span><span class="badge">heat {t.get("theme_heat_score","-")}</span>
  <div class="signal">{esc(", ".join(t.get("buy_points", [])[:4]))}</div>
  <div class="note">{esc(t.get("assistant_comment",""))}</div>
  <div class="muted">선두: {esc(best.get("name", "-"))} · {best.get("rank_score", "-")} · {esc(verdict)}</div>
</article>"""


def render_links() -> str:
    links = [
        ("💼 수익성 검증 대시보드 (NEW)", "../profitability/output/profitability_dashboard.html"),
        ("뉴스 × 차트 통합 대시보드", "../visual_dashboard/output/dashboard.html"),
        ("기사 키워드 기회 보드", "../opportunity_engine/output/opportunity_board.html"),
        ("생활 기사 테마 레이더", "../life_theme_radar/output/life_radar.html"),
        ("차트 분석 리포트", "../chart_analysis/output/chart_report_20260514.html"),
        ("기존 누적 시그널 대시보드", "../reports/index.html"),
    ]
    return ''.join(
        f'<div class="list-item"><a href="{esc(href)}">{esc(label)}</a><div class="muted">{esc(href)}</div></div>'
        for label, href in links
    )


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build unified stock analyzer homepage")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    payload = build_payload()
    paths = write_outputs(payload, args.output_dir)
    print("통합 홈 대시보드 생성 완료")
    for key, path in paths.items():
        print(f"  - {key}: {path}")


if __name__ == "__main__":
    main()

