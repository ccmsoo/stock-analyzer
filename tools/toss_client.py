"""
토스증권 Open API — Python 클라이언트 (시세/캔들).

확인된 엔드포인트:
  POST /oauth2/token           Basic(client_id:secret), grant_type=client_credentials → 24h
  GET  /api/v1/prices?symbols= 현재가 (다중)
  GET  /api/v1/candles?symbol=&interval=1d  일봉 OHLCV(최근 100, 최신순)

키: 환경변수 TOSS_CLIENT_KEY / TOSS_SECRET_KEY (.env)

quick_daily 등에서 네이버 스크래핑 대체/보강용. 키 없으면 graceful None.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request

BASE = "https://openapi.tossinvest.com"

_token: dict | None = None
_lock = threading.Lock()


def _creds() -> tuple[str, str] | None:
    cid = os.environ.get("TOSS_CLIENT_KEY")
    sec = os.environ.get("TOSS_SECRET_KEY")
    if not cid or not sec:
        return None
    return cid, sec


def configured() -> bool:
    return _creds() is not None


def _get_token() -> str | None:
    global _token
    c = _creds()
    if not c:
        return None
    with _lock:
        now = time.time()
        if _token and _token["exp"] > now + 60:
            return _token["token"]
        basic = base64.b64encode(f"{c[0]}:{c[1]}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            f"{BASE}/oauth2/token", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": "Basic " + basic},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read())
            _token = {"token": j["access_token"], "exp": now + j.get("expires_in", 3600)}
            return _token["token"]
        except Exception:
            return None


def _api(path: str, extra: dict | None = None) -> dict | None:
    tok = _get_token()
    if not tok:
        return None
    headers = {"Authorization": "Bearer " + tok, "Accept": "application/json"}
    if extra:
        headers.update(extra)
    req = urllib.request.Request(f"{BASE}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def get_candles(symbol: str, interval: str = "1d") -> list[dict]:
    """일봉 OHLCV (최신순). [{date:'YYYYMMDD', close, open, high, low, volume}]"""
    j = _api(f"/api/v1/candles?symbol={symbol}&interval={interval}")
    candles = (j or {}).get("result", {}).get("candles", [])
    out = []
    for c in candles:
        out.append({
            "date": (c.get("timestamp", "") or "")[:10].replace("-", ""),
            "open": int(_num(c.get("openPrice"))),
            "high": int(_num(c.get("highPrice"))),
            "low": int(_num(c.get("lowPrice"))),
            "close": int(_num(c.get("closePrice"))),
            "volume": int(_num(c.get("volume"))),
        })
    return out


def ohlcv_for_date(ticker: str, date_str: str) -> dict | None:
    """date_str(YYYYMMDD)의 OHLCV + 전일比. quick_daily.fetch_ohlcv 호환 형태."""
    candles = get_candles(ticker)
    if not candles:
        return None
    idx = next((i for i, c in enumerate(candles) if c["date"] == date_str), -1)
    # 최신순이므로 idx+1 이 전일
    if idx == -1 or idx + 1 >= len(candles):
        return None
    cur = candles[idx]
    prev_close = candles[idx + 1]["close"]
    change_pct = ((cur["close"] / prev_close) - 1) * 100 if prev_close else 0
    return {
        "ticker": ticker,
        "close": cur["close"], "open": cur["open"], "high": cur["high"],
        "low": cur["low"], "volume": cur["volume"],
        "change_pct": round(change_pct, 2), "prev_close": prev_close,
    }


def get_prices(symbols: list[str]) -> dict[str, float]:
    """현재가 (다중). {symbol: lastPrice}"""
    if not symbols:
        return {}
    j = _api("/api/v1/prices?symbols=" + ",".join(symbols))
    out = {}
    for p in (j or {}).get("result", []):
        out[p["symbol"]] = _num(p.get("lastPrice"))
    return out


if __name__ == "__main__":
    import sys
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    print("configured:", configured())
    print("삼성 현재가:", get_prices(["005930"]))
    d = sys.argv[1] if len(sys.argv) > 1 else None
    if d:
        print(f"삼성 {d} OHLCV:", ohlcv_for_date("005930", d))
