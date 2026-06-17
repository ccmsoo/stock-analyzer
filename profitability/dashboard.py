"""수익성 검증 대시보드
======================
``profitability/output/backtest_trades_*.json`` + ``keyword_performance_*.json``
을 결합해 6개 섹션 HTML 대시보드를 생성한다.

섹션
1. 오늘의 관찰 우선 후보 — profitability_score 상위
2. 진입 제외 후보 — exclusion_reasons 노출
3. 전략 백테스트 요약 — 총 거래/적격/평균 수익률/승률/청산사유
4. 최근 누적 성과 좋은 키워드 — promising
5. 최근 누적 성과 약한 키워드 — weak / noisy
6. 데이터 오류/누락 — note 또는 errors

⚠️ 이 대시보드는 매수/매도 추천이 아니며, profitability_score 는 관찰 우선순위다.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


SAFETY_BANNER = (
    "이 대시보드는 매수/매도 추천이 아닙니다. profitability_score 는 관찰 우선순위이며 "
    "수익을 보장하지 않습니다. 백테스트는 과거 데이터 기반이며 미래 성과를 보장하지 않습니다. "
    "실제 거래 전 종이매매로 검증하세요."
)


# ───────────────────────── 데이터 로딩 ─────────────────────────


def load_all_trades(backtest_dir: Path = OUTPUT_DIR) -> list[dict]:
    seen: dict[tuple, dict] = {}
    files = sorted(backtest_dir.glob("backtest_trades_*.json"))
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


def load_all_errors(backtest_dir: Path = OUTPUT_DIR) -> list[dict]:
    out = []
    for p in sorted(backtest_dir.glob("backtest_trades_*.json")):
        if p.name.endswith("_all.json"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in data.get("errors", []):
            out.append({**e, "source": p.name})
    return out


def load_keyword_perf() -> dict:
    files = sorted(OUTPUT_DIR.glob("keyword_performance_*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest_signal_date(trades: list[dict]) -> str:
    dates = {t.get("signal_date", "") for t in trades if t.get("signal_date")}
    return max(dates) if dates else ""


# ───────────────────────── 섹션 ─────────────────────────


def _today_trades(trades: list[dict], signal_date: str) -> list[dict]:
    return [t for t in trades if t.get("signal_date") == signal_date]


def section_top_picks(trades: list[dict], signal_date: str, limit: int = 10) -> list[dict]:
    today = _today_trades(trades, signal_date)
    return sorted(today, key=lambda t: -(t.get("profitability_score") or 0))[:limit]


def section_excluded(trades: list[dict], signal_date: str, limit: int = 12) -> list[dict]:
    today = _today_trades(trades, signal_date)
    excluded = [t for t in today if t.get("exclusion_reasons")]
    excluded.sort(key=lambda t: -len(t.get("exclusion_reasons") or []))
    return excluded[:limit]


def strategy_summary(trades: list[dict]) -> dict:
    eligibles = [t for t in trades if t.get("strategy_eligible") and t.get("strategy_return_pct") is not None]
    out = {
        "total_trades": len(trades),
        "eligible_count": len(eligibles),
        "avg_return_pct": None,
        "win_rate": None,
        "exit_reasons": {},
        "best": None,
        "worst": None,
    }
    if eligibles:
        rets = [t.get("strategy_return_pct") for t in eligibles]
        out["avg_return_pct"] = round(sum(rets) / len(rets), 2)
        out["win_rate"] = round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
        reasons = {}
        for t in eligibles:
            reasons[t.get("strategy_exit_reason")] = reasons.get(t.get("strategy_exit_reason"), 0) + 1
        out["exit_reasons"] = reasons
        best = max(eligibles, key=lambda t: t.get("strategy_return_pct") or 0)
        worst = min(eligibles, key=lambda t: t.get("strategy_return_pct") or 0)
        out["best"] = {
            "name": best.get("name"), "ticker": best.get("ticker"),
            "date": best.get("signal_date"), "return_pct": best.get("strategy_return_pct"),
            "exit_reason": best.get("strategy_exit_reason"),
        }
        out["worst"] = {
            "name": worst.get("name"), "ticker": worst.get("ticker"),
            "date": worst.get("signal_date"), "return_pct": worst.get("strategy_return_pct"),
            "exit_reason": worst.get("strategy_exit_reason"),
        }
    return out


def section_keywords(keyword_perf: dict, label_in: set, sort_key, limit: int = 15) -> list[dict]:
    by_watch = keyword_perf.get("by_watch_keyword", {})
    items = [v for v in by_watch.values() if v.get("quality_label") in label_in]
    items.sort(key=sort_key)
    return items[:limit]


def section_data_issues(trades: list[dict], errors: list[dict], signal_date: str | None,
                        limit: int = 30) -> list[dict]:
    issues: list[dict] = []
    for t in trades:
        if signal_date and t.get("signal_date") != signal_date:
            continue
        if t.get("note") and t.get("note") != "":
            issues.append({"kind": "note", "ticker": t.get("ticker"),
                           "name": t.get("name"), "signal_date": t.get("signal_date"),
                           "detail": t.get("note")})
    for e in errors:
        issues.append({"kind": "error", **e})
    return issues[:limit]


# ───────────────────────── HTML 렌더 ─────────────────────────


def esc(v) -> str:
    if v is None:
        return ""
    return htmllib.escape(str(v))


def fmt_pct(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "-"


def fmt_num(v, decimals: int = 1) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def fmt_score(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{int(v)}"
    except (TypeError, ValueError):
        return "-"


def risk_class(risk: str) -> str:
    return {
        "extreme": "risk-extreme",
        "high": "risk-high",
        "medium": "risk-medium",
        "low": "risk-low",
    }.get((risk or "").lower(), "")


def label_class(lbl: str) -> str:
    return {
        "strong_watch": "lbl-strong",
        "watch": "lbl-watch",
        "neutral": "lbl-neutral",
        "weak": "lbl-weak",
        "avoid": "lbl-avoid",
        "promising": "lbl-promising",
        "noisy": "lbl-noisy",
        "unproven": "lbl-unproven",
    }.get((lbl or "").lower(), "")


CSS = """
:root {
  --bg:#0b0d10; --panel:#151a20; --panel2:#1e2530; --line:#23272e;
  --fg:#e6e6e6; --muted:#8a929b; --cyan:#2dd4bf;
  --green:#22c55e; --yellow:#eab308; --red:#ef4444; --blue:#3b82f6; --purple:#a78bfa;
}
*{box-sizing:border-box;}
body{margin:0;padding:24px 30px;background:var(--bg);color:var(--fg);
     font:14px/1.55 -apple-system,BlinkMacSystemFont,"Pretendard",sans-serif;}
h1{margin:0 0 4px;font-size:22px;}
h2{margin:28px 0 10px;font-size:17px;color:var(--cyan);border-bottom:1px solid var(--line);padding-bottom:5px;}
h3{margin:4px 0;font-size:14px;}
.muted{color:var(--muted);font-size:13px;}
.banner{background:#3a2a17;border-left:4px solid var(--yellow);padding:10px 14px;
        border-radius:6px;margin:12px 0 24px;font-size:13px;}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0 14px;}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;}
.kpi b{display:block;font-size:24px;color:var(--cyan);}
.kpi small{color:var(--muted);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px;}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;}
.card .row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;}
.name{font-weight:600;}
.ticker{color:var(--muted);font-size:12px;font-family:ui-monospace,monospace;margin-left:6px;}
.score{font-size:22px;font-weight:700;color:var(--cyan);font-family:ui-monospace,monospace;}
.signal{margin:6px 0;color:var(--cyan);font-size:13px;}
.meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;}
.metrics{display:flex;gap:10px;font-size:12px;color:var(--muted);margin-top:6px;font-family:ui-monospace,monospace;flex-wrap:wrap;}
.badge{font-size:11px;background:var(--panel2);border:1px solid var(--line);padding:1px 6px;border-radius:4px;color:var(--muted);}
.exclusions{margin-top:6px;font-size:12px;color:var(--yellow);}
.exclusions code{background:#3a2a17;padding:1px 4px;border-radius:3px;margin:1px 2px;font-size:11px;}
.strategy{margin-top:6px;font-size:12px;border-top:1px solid var(--line);padding-top:6px;color:var(--muted);}
.strategy b{color:var(--fg);}
.kwcard .kwstats{margin-top:6px;display:flex;flex-wrap:wrap;gap:10px;font-size:12px;font-family:ui-monospace,monospace;color:var(--muted);}
.empty{padding:20px;color:var(--muted);font-size:13px;}
.risk-extreme{border-left:4px solid var(--red);}
.risk-high{border-left:4px solid #f59e0b;}
.risk-medium{border-left:4px solid var(--yellow);}
.risk-low{border-left:4px solid var(--green);}
.lbl-strong{background:linear-gradient(180deg,#16321c,var(--panel));}
.lbl-watch{background:linear-gradient(180deg,#1d2a3a,var(--panel));}
.lbl-neutral{opacity:0.85;}
.lbl-weak{opacity:0.65;}
.lbl-avoid{opacity:0.55;}
.lbl-promising{background:linear-gradient(180deg,#16321c,var(--panel));}
.lbl-noisy{background:linear-gradient(180deg,#3a1e20,var(--panel));}
.lbl-unproven{opacity:0.6;}
table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px;}
table th,table td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;}
table th{color:var(--muted);font-weight:500;font-size:12px;}
.return-pos{color:var(--green);}
.return-neg{color:var(--red);}
.exit-tp{color:var(--green);font-weight:600;}
.exit-sl{color:var(--red);font-weight:600;}
.exit-time{color:var(--muted);}
"""


def render_trade_card(t: dict) -> str:
    score = t.get("profitability_score")
    label = (t.get("score_label") or "").lower()
    risk = (t.get("entry_risk") or "").lower()

    notes = []
    if (t.get("rsi14") or 0) >= 80:
        notes.append("RSI 과열")
    if (t.get("distance_ma20_pct") or 0) >= 25:
        notes.append("MA20 이격 과다")
    if (t.get("consecutive_days") or 1) >= 3:
        notes.append(f"{t.get('consecutive_days')}일 연속")
    if (t.get("upper_shadow_pct") or 0) >= 45:
        notes.append("윗꼬리 과다")
    note_html = f'<div class="exclusions">⚠️ {esc(" · ".join(notes))}</div>' if notes else ""

    excl = t.get("exclusion_reasons") or []
    excl_html = ""
    if excl:
        chips = "".join(f"<code>{esc(r)}</code>" for r in excl)
        excl_html = f'<div class="exclusions"><b>제외:</b> {chips}</div>'

    # reason_unknown_category + 진단 메타 (이유 불명 종목용)
    ruc = (t.get("reason_unknown_category") or "").strip()
    unknown_html = ""
    if ruc or (t.get("confidence") or "").lower() == "low" or (t.get("trigger_type") or "").lower() == "unknown":
        origin = t.get("article_origin_dist") or ""
        diag_bits = []
        if t.get("article_count") is not None:
            diag_bits.append(f"기사 {t.get('article_count')}건")
        if origin:
            diag_bits.append(f"origin {esc(str(origin))}")
        if t.get("latest_article_date"):
            diag_bits.append(f"최신기사 {esc(t.get('latest_article_date'))}")
        if t.get("trigger_lag_candidate") not in (None, ""):
            diag_bits.append(f"trigger_lag {esc(t.get('trigger_lag_candidate'))}일")
        diag_text = " · ".join(diag_bits)
        title = f'<b>❓ 이유 불명: <code>{esc(ruc or "uncategorized")}</code></b>'
        unknown_html = (
            f'<div class="exclusions" style="background:rgba(245,158,11,.08); '
            f'border:1px solid rgba(245,158,11,.35); border-radius:6px; padding:4px 6px;">'
            f'{title}'
            + (f'<div style="font-size:11px; color:#a3a3a3; margin-top:2px">{diag_text}</div>' if diag_text else '')
            + '</div>'
        )

    strat_html = ""
    if t.get("strategy_eligible"):
        rp = t.get("strategy_return_pct")
        reason = t.get("strategy_exit_reason", "")
        reason_cls = {"take_profit": "exit-tp", "stop_loss": "exit-sl",
                      "time_exit": "exit-time"}.get(reason, "")
        strat_html = (
            f'<div class="strategy">'
            f'전략: <b class="{reason_cls}">{esc(reason)}</b> '
            f'{fmt_pct(rp)} · 진입 {fmt_num(t.get("strategy_entry_price"))} → '
            f'청산 {fmt_num(t.get("strategy_exit_price"))} '
            f'({esc(t.get("strategy_exit_date") or "-")})'
            f'</div>'
        )

    return f"""
<div class="card {label_class(label)} {risk_class(risk)}">
  <div class="row">
    <div>
      <span class="name">{esc(t.get('name'))}</span>
      <span class="ticker">{esc(t.get('ticker'))} · {esc(t.get('market'))}</span>
    </div>
    <span class="score">{fmt_score(score)}</span>
  </div>
  <div class="signal">🎯 {esc(t.get('specific_signal') or '-')}</div>
  <div class="meta">
    <span class="badge">{esc(t.get('confidence') or '-')}</span>
    <span class="badge">{esc(t.get('trigger_type') or '-')}</span>
    <span class="badge">chart {fmt_score(t.get('chart_score'))}</span>
    <span class="badge {risk_class(risk)}">risk {esc(risk or '-')}</span>
    <span class="badge">{esc(label)}</span>
  </div>
  <div class="metrics">
    <span>RSI {fmt_num(t.get('rsi14'))}</span>
    <span>거래량 {fmt_num(t.get('volume_ratio_20d'))}배</span>
    <span>거래대금 {fmt_num(t.get('value_ratio_20d'))}배</span>
    <span>MA20 {fmt_pct(t.get('distance_ma20_pct'))}</span>
  </div>
  {note_html}
  {excl_html}
  {unknown_html}
  {strat_html}
</div>"""


def render_keyword_card(v: dict) -> str:
    examples = ", ".join(v.get("example_tickers", [])[:4])
    strat_part = ""
    if v.get("avg_strategy_return_pct") is not None:
        strat_part = (
            f"<span>전략 평균 {fmt_pct(v.get('avg_strategy_return_pct'))} "
            f"승률 {fmt_num(v.get('strategy_win_rate'))}% (n={v.get('eligible_count')})</span>"
        )
    return f"""
<div class="card kwcard {label_class(v.get('quality_label'))}">
  <div class="row">
    <h3>🔑 {esc(v.get('keyword'))}</h3>
    <span class="badge">{esc(v.get('quality_label'))}</span>
  </div>
  <div class="kwstats">
    <span>n={v.get('appearances')}</span>
    <span>5d 평균 {fmt_pct(v.get('avg_return_5d'))}</span>
    <span>5d 승률 {fmt_num(v.get('win_rate_5d'))}%</span>
    {strat_part}
  </div>
  <div class="meta"><small>예: {esc(examples)}</small></div>
  <div class="meta"><small>최근: {esc(v.get('latest_seen'))}</small></div>
</div>"""


def render_summary_table(summary: dict) -> str:
    if not summary or summary.get("eligible_count", 0) == 0:
        return '<div class="empty">전략 적격 거래가 없습니다.</div>'
    reasons = summary.get("exit_reasons") or {}
    reasons_html = " · ".join(f"{esc(k)}={v}" for k, v in reasons.items())
    best = summary.get("best") or {}
    worst = summary.get("worst") or {}
    return f"""
<div class="kpis">
  <div class="kpi"><b>{summary.get('total_trades', 0)}</b><small>총 시그널</small></div>
  <div class="kpi"><b>{summary.get('eligible_count', 0)}</b><small>전략 적격</small></div>
  <div class="kpi"><b>{fmt_pct(summary.get('avg_return_pct'))}</b><small>평균 전략 수익률</small></div>
  <div class="kpi"><b>{fmt_num(summary.get('win_rate'))}%</b><small>승률</small></div>
</div>
<div class="muted" style="margin-top:8px">청산 사유: {esc(reasons_html or '-')}</div>
<table>
  <thead><tr><th>최고</th><th>최저</th></tr></thead>
  <tbody><tr>
    <td>{esc(best.get('name', '-'))} ({esc(best.get('ticker','-'))}) {esc(best.get('date','-'))} ·
        <b class="return-pos">{fmt_pct(best.get('return_pct'))}</b> · {esc(best.get('exit_reason','-'))}</td>
    <td>{esc(worst.get('name', '-'))} ({esc(worst.get('ticker','-'))}) {esc(worst.get('date','-'))} ·
        <b class="return-neg">{fmt_pct(worst.get('return_pct'))}</b> · {esc(worst.get('exit_reason','-'))}</td>
  </tr></tbody>
</table>"""


def render_issues_table(issues: list[dict]) -> str:
    if not issues:
        return '<div class="empty">데이터 오류/누락 없음.</div>'
    rows = "".join(
        f"<tr><td>{esc(i.get('kind'))}</td><td>{esc(i.get('signal_date',''))}</td>"
        f"<td>{esc(i.get('ticker',''))}</td><td>{esc(i.get('name',''))}</td>"
        f"<td>{esc(i.get('detail') or i.get('error',''))}</td></tr>"
        for i in issues
    )
    return f"""
<table>
  <thead><tr><th>kind</th><th>date</th><th>ticker</th><th>name</th><th>detail</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""


def render_section(title: str, body: str, intro: str = "") -> str:
    intro_html = f'<p class="muted">{esc(intro)}</p>' if intro else ""
    return f'<section><h2>{esc(title)}</h2>{intro_html}{body}</section>'


def render_html(trades: list[dict], keyword_perf: dict, errors: list[dict],
                signal_date: str) -> str:
    today = _today_trades(trades, signal_date)
    summary = strategy_summary(trades)   # 전체 누적 요약 (모든 영업일)

    top_picks = section_top_picks(trades, signal_date)
    excluded = section_excluded(trades, signal_date)
    promising = section_keywords(
        keyword_perf, {"promising"},
        sort_key=lambda v: -(v.get("avg_return_5d") or 0),
    )
    weak = section_keywords(
        keyword_perf, {"weak", "noisy"},
        sort_key=lambda v: (v.get("avg_return_5d") or 0),
    )
    issues = section_data_issues(trades, errors, signal_date=None)  # 전체 이슈

    kpis = f"""
<div class="kpis">
  <div class="kpi"><b>{len(today)}</b><small>오늘 시그널 ({esc(signal_date)})</small></div>
  <div class="kpi"><b>{sum(1 for t in today if (t.get('profitability_score') or 0) >= 80)}</b><small>strong_watch</small></div>
  <div class="kpi"><b>{sum(1 for t in today if t.get('strategy_eligible'))}</b><small>전략 적격</small></div>
  <div class="kpi"><b>{sum(1 for t in today if t.get('exclusion_reasons'))}</b><small>진입 제외</small></div>
</div>"""

    sections = [
        kpis,
        render_section(
            "1. 오늘의 관찰 우선 후보",
            f'<div class="grid">{"".join(render_trade_card(t) for t in top_picks) or "<div class=empty>없음</div>"}</div>',
            intro="profitability_score 상위. 매수 신호가 아니라 관찰 순위.",
        ),
        render_section(
            "2. 진입 제외 후보 (exclusion_reasons)",
            f'<div class="grid">{"".join(render_trade_card(t) for t in excluded) or "<div class=empty>없음</div>"}</div>',
            intro="룰 기반 전략에서 제외된 종목과 제외 사유. 추격은 위험.",
        ),
        render_section(
            "3. 전략 백테스트 요약 (누적)",
            render_summary_table(summary),
            intro="D+1 시가 진입 · -4% 손절 / +8% 익절 / 5일 보유 가정. 종이매매 검증 필요.",
        ),
        render_section(
            "4. 최근 누적 성과 좋은 키워드 (promising)",
            f'<div class="grid">{"".join(render_keyword_card(v) for v in promising) or "<div class=empty>표본 부족</div>"}</div>',
            intro="3회 이상 등장 + 5일 평균 수익 > 3% + 승률 60%+. 데이터 1~2주 누적된 후 의미.",
        ),
        render_section(
            "5. 최근 성과 약한 키워드 (weak / noisy)",
            f'<div class="grid">{"".join(render_keyword_card(v) for v in weak) or "<div class=empty>없음</div>"}</div>',
            intro="반복 등장에도 5일 평균 손실 또는 승률 40% 미만. 같은 키워드 새 시그널은 신중.",
        ),
        render_section(
            "6. 데이터 오류 · 누락",
            render_issues_table(issues),
            intro="OHLCV 부족, fetch 실패, 향후 거래일 미생성 등.",
        ),
    ]

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>수익성 검증 대시보드 — {esc(signal_date)}</title>
<style>{CSS}</style>
</head>
<body>
<h1>💼 수익성 검증 대시보드</h1>
<div class="muted">기준일: {esc(signal_date)} · 생성: {datetime.now().strftime("%Y-%m-%d %H:%M")} · 누적 trade: {len(trades)}</div>
<div class="banner">{SAFETY_BANNER}</div>
{''.join(sections)}
</body>
</html>"""


# ───────────────────────── 출력 ─────────────────────────


def write_outputs(html: str, signal_date: str) -> dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dated = OUTPUT_DIR / f"profitability_dashboard_{signal_date}.html"
    latest = OUTPUT_DIR / "profitability_dashboard.html"
    dated.write_text(html, encoding="utf-8")
    latest.write_text(html, encoding="utf-8")
    return {"dated": dated, "latest": latest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="기준일 YYYYMMDD (기본: 가장 최근 trade)")
    args = parser.parse_args()

    trades = load_all_trades()
    if not trades:
        print("❌ trade 데이터 없음. profitability.backtest 먼저 실행.")
        return

    errors = load_all_errors()
    keyword_perf = load_keyword_perf()
    if not keyword_perf:
        print("⚠️  keyword_performance_*.json 없음 — keyword_perf 먼저 실행 권장.")

    signal_date = args.date or latest_signal_date(trades)
    print(f"📦 trades {len(trades)} / errors {len(errors)} / 기준일 {signal_date}")

    html = render_html(trades, keyword_perf, errors, signal_date)
    paths = write_outputs(html, signal_date)
    print(f"✅ 저장: {paths['dated'].name} (+ latest)")


if __name__ == "__main__":
    main()
