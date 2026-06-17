"""
One-command builder for the assistant-style dashboard.

This package does not duplicate the engines. It orchestrates the modules we
already built and publishes a single homepage under stock_assistant/output.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from chart_analysis.analyzer import (
    analyze_stocks,
    latest_report_csv,
    load_report_stocks,
    write_outputs as write_chart_outputs,
)
from life_theme_radar.build_life_radar import (
    build_payload as build_life_payload,
    build_radar,
    write_outputs as write_life_outputs,
)
from opportunity_engine.build_opportunity_board import (
    build_opportunities as make_opportunities,
    build_payload as build_opportunity_payload,
    load_chart as load_opportunity_chart,
    load_report as load_opportunity_report,
    write_outputs as write_opportunity_outputs,
)
from unified_dashboard.build_unified_dashboard import (
    build_payload as build_unified_payload,
    write_outputs as write_unified_outputs,
)
from visual_dashboard.build_dashboard import (
    build_payload as build_visual_payload,
    combine as combine_visual,
    load_chart as load_visual_chart,
    load_report as load_visual_report,
    write_outputs as write_visual_outputs,
)
from profitability.backtest import (
    load_signals_from_report,
    backtest_signals,
    write_outputs as write_backtest_outputs,
    latest_hist_cache,
    load_hist_cache,
)
from profitability.keyword_perf import (
    load_all_trades as load_all_backtest_trades,
    aggregate_all as aggregate_keyword_perf,
    write_outputs as write_keyword_perf_outputs,
)
from profitability.dashboard import (
    load_all_trades as load_all_for_dashboard,
    load_all_errors as load_all_backtest_errors,
    load_keyword_perf,
    latest_signal_date,
    render_html as render_profitability_html,
    write_outputs as write_profitability_outputs,
)
from state_manager import load_state


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def infer_date(report: Path) -> str:
    if report.stem.startswith("report_"):
        return report.stem.replace("report_", "")
    return datetime.now().strftime("%Y%m%d")


def build_chart(report: Path, limit: int | None = None) -> dict:
    stocks = load_report_stocks(report)
    if limit:
        stocks = stocks[:limit]
    print(f"\n[1/6] 차트 분석: {report} ({len(stocks)}종목)")
    results, errors = analyze_stocks(stocks)
    paths = write_chart_outputs(results, errors)
    print(f"   ✓ 성공 {len(results)} / 실패 {len(errors)}")
    return {"paths": {k: str(v) for k, v in paths.items()}, "count": len(results), "errors": errors}


def build_visual(report: Path) -> dict:
    date_str = infer_date(report)
    print(f"\n[2/6] 뉴스 x 차트 통합: {date_str}")
    rows = load_visual_report(report)
    charts = load_visual_chart(date_str)
    combined = combine_visual(rows, charts)
    payload = build_visual_payload(combined, report, len(charts))
    paths = write_visual_outputs(payload)
    print(f"   ✓ 종목 {payload['count']} / 차트 결합 {payload['chart_count']}")
    return {"paths": {k: str(v) for k, v in paths.items()}, "count": payload["count"]}


def build_opportunity_stage(report: Path, no_embedded_charts: bool) -> dict:
    date_str = infer_date(report)
    print(f"\n[3/6] 기사 키워드 기회 보드: {date_str}")
    rows = load_opportunity_report(report)
    charts = load_opportunity_chart(date_str)
    opportunities = make_opportunities(rows, charts, with_charts=not no_embedded_charts)
    payload = build_opportunity_payload(opportunities, report, len(charts))
    paths = write_opportunity_outputs(payload)
    print(f"   ✓ 기회 {payload['opportunity_count']} / 차트 결합 {payload['chart_count']}")
    return {"paths": {k: str(v) for k, v in paths.items()}, "count": payload["opportunity_count"]}


def build_life(date_str: str, sample: bool, no_embedded_charts: bool,
               max_themes: int | None, max_queries: int, max_articles: int,
               max_stocks: int) -> dict:
    print(f"\n[5/6] 생활 기사 테마 레이더: {date_str}")
    radar = build_radar(
        date_str=date_str,
        days=5,
        sample=sample,
        max_themes=max_themes,
        max_queries=max_queries,
        max_articles=max_articles,
        max_stocks=max_stocks,
        with_charts=not no_embedded_charts,
    )
    payload = build_life_payload(radar, date_str, sample)
    paths = write_life_outputs(payload)
    print(f"   ✓ 테마 {payload['theme_count']} / sample={sample}")
    return {"paths": {k: str(v) for k, v in paths.items()}, "count": payload["theme_count"]}


def build_profitability(report: Path) -> dict:
    """[5/6] 수익성 검증 단계 — backtest + keyword_perf + dashboard.

    이 단계는 ``reports/`` 의 모든 영업일 csv 를 입력으로 누적 backtest 한다.
    개별 시그널의 사후 수익률 + 키워드별 통계 + HTML 대시보드 산출.
    """
    date_str = infer_date(report)
    print(f"\n[4/6] 수익성 검증 (backtest + keyword 성과): 누적 ~ {date_str}")

    # 1) 모든 영업일 리포트로 backtest (history 일관성 위해)
    all_signals = []
    for p in sorted(ROOT.glob("reports/report_*.csv")):
        try:
            all_signals.extend(load_signals_from_report(p))
        except Exception as e:
            print(f"   ⚠️  {p.name}: {e}")

    cache: dict = {}
    cache_path = latest_hist_cache()
    if cache_path:
        try:
            cache = load_hist_cache(cache_path)
        except Exception:
            cache = {}

    try:
        state = load_state()
        state_signals = state.get("signals", {})
    except Exception:
        state_signals = {}

    trades, bt_errors = backtest_signals(
        all_signals, cache=cache, state_signals=state_signals
    )
    # 다중 영업일 백테스트라 include_all=True (backtest_trades_all.json/csv 도 저장)
    bt_paths = write_backtest_outputs(trades, bt_errors, suffix=date_str, include_all=True)
    print(f"   ✓ backtest {len(trades)} trade / {len(bt_errors)} 오류 ({bt_paths['json'].name})")

    # 2) 키워드 성과 집계 (모든 backtest 파일 누적)
    trades_acc = load_all_backtest_trades()
    agg = aggregate_keyword_perf(trades_acc)
    kw_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_trades": len(trades_acc),
        "counts": {k: len(v) for k, v in agg.items()},
        **agg,
    }
    kw_paths = write_keyword_perf_outputs(kw_payload, suffix=date_str)
    print(f"   ✓ keyword_perf watch={kw_payload['counts']['by_watch_keyword']} "
          f"signal={kw_payload['counts']['by_specific_signal']} ({kw_paths['json'].name})")

    # 3) HTML 대시보드
    trades_all = load_all_for_dashboard()
    kw_perf = load_keyword_perf()
    errors_all = load_all_backtest_errors()
    sig_date = date_str or latest_signal_date(trades_all)
    html = render_profitability_html(trades_all, kw_perf, errors_all, sig_date)
    dash_paths = write_profitability_outputs(html, sig_date)
    print(f"   ✓ dashboard: {dash_paths['dated'].name}")

    return {
        "paths": {
            "backtest_json": str(bt_paths["json"]),
            "backtest_csv": str(bt_paths["csv"]),
            "keyword_perf": str(kw_paths["json"]),
            "dashboard_dated": str(dash_paths["dated"]),
            "dashboard_latest": str(dash_paths["latest"]),
        },
        "trade_count": len(trades_acc),
        "keyword_count": kw_payload["counts"]["by_watch_keyword"],
        "errors": bt_errors,
    }


def build_home() -> dict:
    print("\n[6/6] 통합 홈 생성")
    payload = build_unified_payload()
    paths = write_unified_outputs(payload, OUTPUT_DIR)
    # Also keep a familiar top-level landing page for the user.
    shutil.copyfile(paths["html"], ROOT / "assistant_home.html")
    print(f"   ✓ 홈: {paths['html']}")
    return {"paths": {k: str(v) for k, v in paths.items()}, "root_home": str(ROOT / "assistant_home.html")}


def write_manifest(report: Path, parts: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report": str(report),
        "home": str(ROOT / "assistant_home.html"),
        "parts": parts,
    }
    path = OUTPUT_DIR / "build_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the full stock assistant UI")
    parser.add_argument("--report", type=Path, help="reports/report_YYYYMMDD.csv. Defaults to latest")
    parser.add_argument("--chart-limit", type=int, help="Only analyze first N report stocks")
    parser.add_argument("--life-sample", action="store_true", help="Use sample articles for life theme radar")
    parser.add_argument("--skip-life", action="store_true", help="Skip refreshing life theme radar")
    parser.add_argument("--no-embedded-charts", action="store_true", help="Skip inline SVG mini charts in boards")
    parser.add_argument("--skip-profitability", action="store_true", help="Skip profitability backtest stage")
    parser.add_argument("--max-life-themes", type=int, default=None)
    parser.add_argument("--max-life-queries", type=int, default=3)
    parser.add_argument("--max-life-articles", type=int, default=4)
    parser.add_argument("--max-life-stocks", type=int, default=6)
    args = parser.parse_args()

    report = args.report or latest_report_csv()
    date_str = infer_date(report)
    parts = {}

    parts["chart"] = build_chart(report, limit=args.chart_limit)
    parts["visual"] = build_visual(report)
    parts["opportunities"] = build_opportunity_stage(report, no_embedded_charts=args.no_embedded_charts)
    if args.skip_profitability:
        print("\n[4/6] 수익성 검증: 스킵")
    else:
        parts["profitability"] = build_profitability(report)
    if args.skip_life:
        print("\n[5/6] 생활 기사 테마 레이더: 스킵")
    else:
        parts["life"] = build_life(
            date_str=datetime.now().strftime("%Y%m%d"),
            sample=args.life_sample,
            no_embedded_charts=args.no_embedded_charts,
            max_themes=args.max_life_themes,
            max_queries=args.max_life_queries,
            max_articles=args.max_life_articles,
            max_stocks=args.max_life_stocks,
        )
    parts["home"] = build_home()
    manifest = write_manifest(report, parts)

    print("\n완료")
    print(f"  - 메인 홈: {ROOT / 'assistant_home.html'}")
    print(f"  - 통합 홈: {OUTPUT_DIR / 'index.html'}")
    print(f"  - manifest: {manifest}")


if __name__ == "__main__":
    main()
