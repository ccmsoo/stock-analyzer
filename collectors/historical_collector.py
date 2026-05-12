"""
과거 일자별 TOP 등락 종목 수집
================================
- FDR로 각 종목의 일별 시세를 받아옴 (네이버가 fdr의 기본 소스)
- Naver rate-limit 회피: 캐시-원샷 (한 기간을 한 번에 fetch → pickle → 날짜별 추출)

ETF 제외 필터는 price_collector의 _is_etf 재사용.
"""
import time
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import FinanceDataReader as fdr

from collectors.price_collector import _is_etf, NOISE_KEYWORDS


CACHE_DIR = Path(__file__).parent.parent / "state"


def _get_listing(market: str):
    """KOSPI/KOSDAQ 전체 상장 종목 목록"""
    return fdr.StockListing(market)


def is_krx_open(date_str: str) -> bool:
    """KOSPI 지수(KS11) 데이터 존재 여부로 영업일 판정.
    근로자의날, 어린이날 등 공휴일은 자동으로 False."""
    try:
        d = datetime.strptime(date_str, '%Y%m%d').strftime('%Y-%m-%d')
        df = fdr.DataReader('KS11', d, d)
        return not df.empty
    except Exception:
        return False


def _fetch_one_history(ticker: str, name: str, market: str, start: str, end: str, delay: float = 0.0):
    """한 종목의 [start, end] 일별 시세"""
    if delay:
        time.sleep(delay)
    try:
        df = fdr.DataReader(ticker, start, end)
        if df.empty:
            return ('empty', ticker, name)
        return ('ok', ticker, name, market, df)
    except Exception as e:
        return ('err', ticker, name, str(e)[:60])


def prefetch_period(start_yyyymmdd: str, end_yyyymmdd: str,
                    cache_path: Path | None = None,
                    max_workers: int = 2,
                    resume: bool = True) -> Dict[str, dict]:
    """
    [start, end] 기간 동안 KOSPI/KOSDAQ 전 종목 일별 시세를 한 번에 받아 pickle 캐시.
    이후 각 영업일의 TOP 등락은 이 캐시에서 즉시 추출 가능 (네트워크 호출 0).

    Returns:
        cache dict: {ticker: {'name': str, 'market': str, 'df': DataFrame}}
    """
    if cache_path is None:
        cache_path = CACHE_DIR / f"hist_cache_{start_yyyymmdd}_{end_yyyymmdd}.pkl"
    cache_path.parent.mkdir(exist_ok=True)

    cache: Dict[str, dict] = {}
    if resume and cache_path.exists():
        with open(cache_path, 'rb') as f:
            cache = pickle.load(f)
        print(f"   ↺ 캐시 재개: {len(cache)}종목 기존 보유", flush=True)

    start = datetime.strptime(start_yyyymmdd, '%Y%m%d')
    end = datetime.strptime(end_yyyymmdd, '%Y%m%d')
    # 전일 대비 % 정확히 잡으려고 시작일 7일 전부터 fetch
    fetch_start = (start - timedelta(days=7)).strftime('%Y-%m-%d')
    fetch_end = (end + timedelta(days=1)).strftime('%Y-%m-%d')

    # 종목 목록 모으기 + 필터링
    todo: List[tuple] = []  # (ticker, name, market)
    for market_key in ('KOSPI', 'KOSDAQ'):
        print(f"   - {market_key} 종목 목록...", flush=True)
        listing = fdr.StockListing(market_key)
        listing = listing[~listing['Name'].apply(_is_etf)]
        listing = listing[~listing['Name'].apply(lambda n: any(k in n for k in NOISE_KEYWORDS))]
        for _, row in listing.iterrows():
            t = row['Code']
            if t in cache:
                continue
            todo.append((t, row['Name'], market_key))

    print(f"   - 남은 fetch: {len(todo)}종목 (max_workers={max_workers})", flush=True)
    if not todo:
        return cache

    SAVE_EVERY = 200
    done = 0
    err_count = empty_count = 0
    last_save = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_fetch_one_history, t, n, m, fetch_start, fetch_end): (t, n, m)
            for t, n, m in todo
        }
        for fut in as_completed(futures):
            done += 1
            res = fut.result()
            if res[0] == 'ok':
                _, code, name, market, df = res
                cache[code] = {'name': name, 'market': market, 'df': df}
            elif res[0] == 'empty':
                empty_count += 1
            else:
                err_count += 1
                if err_count <= 3:
                    print(f"     ⚠️ {res[1]} {res[2]}: {res[3]}", flush=True)

            if done % 100 == 0:
                print(f"     ... {done}/{len(todo)}  (저장:{len(cache)} 빈:{empty_count} 오류:{err_count})", flush=True)

            # 중간 저장 (장시간 fetch 중 끊겨도 resume 가능)
            if done % SAVE_EVERY == 0 or time.time() - last_save > 60:
                with open(cache_path, 'wb') as f:
                    pickle.dump(cache, f)
                last_save = time.time()

    with open(cache_path, 'wb') as f:
        pickle.dump(cache, f)
    print(f"   ✓ 캐시 저장: {cache_path.name} ({len(cache)}종목)", flush=True)
    return cache


def top_movers_from_cache(cache: Dict[str, dict], date_str: str, top_n: int = 10) -> Dict[str, list]:
    """이미 prefetch한 캐시에서 특정 영업일의 TOP 등락 종목 추출 (네트워크 0)"""
    target_date_norm = datetime.strptime(date_str, '%Y%m%d').date()
    by_market = {'KOSPI': [], 'KOSDAQ': []}
    for ticker, info in cache.items():
        df = info['df']
        rows = df[df.index.date == target_date_norm]
        if rows.empty:
            continue
        row = rows.iloc[0]
        volume = int(row.get('Volume', 0) or 0)
        if volume <= 0:
            continue
        try:
            change_pct = float(row['Change']) * 100
            close = int(row['Close'])
        except (KeyError, ValueError, TypeError):
            continue
        by_market[info['market']].append({
            'ticker': ticker,
            'name': info['name'],
            'close': close,
            'change_pct': round(change_pct, 2),
            'volume': volume,
            'market': info['market'],
        })

    by_market['KOSPI'].sort(key=lambda x: x['change_pct'], reverse=True)
    by_market['KOSDAQ'].sort(key=lambda x: x['change_pct'], reverse=True)
    return {
        'kospi_up': by_market['KOSPI'][:top_n],
        'kosdaq_up': by_market['KOSDAQ'][:top_n],
    }


def get_top_movers_historical(date_str: str, top_n: int = 10, max_workers: int = 5) -> Dict[str, list]:
    """
    과거 특정 영업일의 KOSPI/KOSDAQ 등락률 TOP N (ETF/우선주/스팩/리츠 제외).

    Args:
        date_str: YYYYMMDD
        top_n: 마켓별 TOP N
        max_workers: 동시 fetch 스레드 수

    Returns:
        {'kospi_up': [...], 'kosdaq_up': [...]}  - price_collector와 동일 형식
    """
    target = datetime.strptime(date_str, '%Y%m%d')
    # 시세 fetch 범위는 +/- 5일 (전일대비 % 정확히 잡기 위해)
    start = (target - timedelta(days=10)).strftime('%Y-%m-%d')
    end = (target + timedelta(days=1)).strftime('%Y-%m-%d')
    target_date_norm = target.date()

    result = {'kospi_up': [], 'kosdaq_up': []}

    for market_key, market_label in [('KOSPI', 'kospi_up'), ('KOSDAQ', 'kosdaq_up')]:
        print(f"   - {market_key} 전체 종목 목록 조회...")
        listing = _get_listing(market_key)
        # ETF/노이즈 사전 필터
        listing = listing[~listing['Name'].apply(_is_etf)]
        listing = listing[~listing['Name'].apply(lambda n: any(k in n for k in NOISE_KEYWORDS))]
        tickers = listing[['Code', 'Name']].values.tolist()
        print(f"     ({len(tickers)}종목, 일별 시세 fetch 중...)")

        movers = []
        err_count = empty_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_fetch_one_history, code, name, market_key, start, end): (code, name)
                for code, name in tickers
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 100 == 0:
                    print(f"     ... {done}/{len(tickers)}  (수집:{len(movers)} 빈:{empty_count} 오류:{err_count})", flush=True)
                res = fut.result()
                if res[0] == 'err':
                    err_count += 1
                    if err_count <= 3:
                        print(f"       ⚠️ {res[1]} {res[2]}: {res[3]}", flush=True)
                    continue
                if res[0] == 'empty':
                    empty_count += 1
                    continue
                _, code, name, market, df = res
                day_row = df[df.index.date == target_date_norm]
                if day_row.empty:
                    empty_count += 1
                    continue
                row = day_row.iloc[0]
                change_pct = float(row['Change']) * 100
                close = int(row['Close'])
                volume = int(row['Volume'])
                if volume <= 0:
                    continue
                movers.append({
                    'ticker': code,
                    'name': name,
                    'close': close,
                    'change_pct': round(change_pct, 2),
                    'volume': volume,
                    'market': market,
                })

        movers.sort(key=lambda x: x['change_pct'], reverse=True)
        result[market_label] = movers[:top_n]
        print(f"     ✓ {market_key} 상승 TOP {top_n}: {[m['name'] for m in result[market_label][:3]]} ...")
        time.sleep(0.5)

    return result


def krx_business_days(start_yyyymmdd: str, end_yyyymmdd: str) -> List[str]:
    """KS11 인덱스 일별 시세를 한 번에 받아서 실제 영업일만 추림 (공휴일 자동 제외)"""
    start = datetime.strptime(start_yyyymmdd, '%Y%m%d').strftime('%Y-%m-%d')
    end = datetime.strptime(end_yyyymmdd, '%Y%m%d').strftime('%Y-%m-%d')
    try:
        df = fdr.DataReader('KS11', start, end)
        return [d.strftime('%Y%m%d') for d in df.index]
    except Exception:
        # 폴백: 주말만 제외
        s = datetime.strptime(start_yyyymmdd, '%Y%m%d')
        e = datetime.strptime(end_yyyymmdd, '%Y%m%d')
        days = []
        d = s
        while d <= e:
            if d.weekday() < 5:
                days.append(d.strftime('%Y%m%d'))
            d += timedelta(days=1)
        return days


if __name__ == "__main__":
    # 단독 검증: 2026-05-08 TOP 5
    movers = get_top_movers_historical('20260508', top_n=5, max_workers=10)
    print("\nKOSPI 상승 TOP 5:")
    for m in movers['kospi_up']:
        print(f"  [{m['ticker']}] {m['name']:20s} {m['change_pct']:+6.2f}%")
    print("\nKOSDAQ 상승 TOP 5:")
    for m in movers['kosdaq_up']:
        print(f"  [{m['ticker']}] {m['name']:20s} {m['change_pct']:+6.2f}%")
