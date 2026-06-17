"""
CLI for standalone chart analysis.

Examples:
  ./venv/bin/python -m chart_analysis.run_chart_analysis
  ./venv/bin/python -m chart_analysis.run_chart_analysis --report reports/report_20260504.csv --cache state/hist_cache_20260504_20260511.pkl
  ./venv/bin/python -m chart_analysis.run_chart_analysis --top 10
"""
from __future__ import annotations

import argparse
from pathlib import Path

from collectors.price_collector import get_latest_business_day, get_top_movers

from .analyzer import (
    DEFAULT_OUTPUT_DIR,
    analyze_stocks,
    latest_report_csv,
    load_cache,
    load_report_stocks,
    write_outputs,
)


def _current_top_stocks(top_n: int, date_str: str | None) -> list[dict]:
    date_str = date_str or get_latest_business_day()
    movers = get_top_movers(date_str, top_n=top_n)
    stocks = []
    for stock in movers.get("kospi_up", []) + movers.get("kosdaq_up", []):
        row = dict(stock)
        row["date"] = date_str
        stocks.append(row)
    return stocks


def main() -> None:
    parser = argparse.ArgumentParser(description="급등 종목 차트 분석")
    parser.add_argument("--report", type=Path, help="분석할 report_YYYYMMDD.csv. 생략하면 최신 리포트 사용")
    parser.add_argument("--cache", type=Path, help="historical_collector pickle cache. 네트워크 없이 과거 데이터 분석 가능")
    parser.add_argument("--date", help="현재 TOP 수집 또는 리포트 날짜 보정용 YYYYMMDD")
    parser.add_argument("--top", type=int, help="리포트 대신 현재 등락률 TOP N을 수집해서 분석")
    parser.add_argument("--limit", type=int, help="앞에서 N개만 분석")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    cache = load_cache(args.cache) if args.cache else None

    if args.top:
        stocks = _current_top_stocks(args.top, args.date)
        source = f"current top {args.top}"
    else:
        report = args.report or latest_report_csv()
        stocks = load_report_stocks(report)
        source = str(report)
        if args.date:
            for stock in stocks:
                stock["date"] = args.date

    if args.limit:
        stocks = stocks[:args.limit]

    print(f"차트 분석 시작: {source} / {len(stocks)}종목")
    results, errors = analyze_stocks(stocks, cache=cache)
    paths = write_outputs(results, errors, output_dir=args.output_dir)

    print(f"완료: 성공 {len(results)} / 실패 {len(errors)}")
    for kind, path in paths.items():
        print(f"  - {kind}: {path}")


if __name__ == "__main__":
    main()

