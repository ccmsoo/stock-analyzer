/**
 * 종목 일별 가격 history — Naver Finance siseJson endpoint.
 *  endpoint: https://api.finance.naver.com/siseJson.naver?symbol={ticker}&requestType=1&startTime={YYYYMMDD}&endTime={YYYYMMDD}&timeframe=day
 *  응답: CSV-style 텍스트 (date, open, high, low, close, volume, foreign-ratio)
 */

interface CacheEntry {
  data: DailyBar[];
  fetched_at: number;
}

export interface DailyBar {
  date: string; // YYYYMMDD
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000;
const cache = new Map<string, CacheEntry>();

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  Referer: "https://finance.naver.com/",
};

export async function fetchHistory(
  ticker: string,
  days: number = 60,
): Promise<DailyBar[]> {
  const cacheKey = `${ticker}_${days}`;
  const cached = cache.get(cacheKey);
  if (cached && Date.now() - cached.fetched_at < CACHE_TTL_MS) {
    return cached.data;
  }

  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const fmt = (d: Date) =>
    `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;

  const url = `https://api.finance.naver.com/siseJson.naver?symbol=${ticker}&requestType=1&startTime=${fmt(start)}&endTime=${fmt(end)}&timeframe=day`;

  try {
    const res = await fetch(url, {
      headers: HEADERS,
      signal: AbortSignal.timeout(8000),
      cache: "no-store",
    });
    if (!res.ok) return [];
    const text = await res.text();
    // 응답이 사실상 JS array 형식 텍스트 — eval 대신 정규 파싱
    // 예: [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'], [20260513, 16650, 16900, 16170, 16650, 123456, 0.0], ...]
    const cleaned = text.trim().replace(/[\n\t]/g, "").replace(/'/g, '"');
    let arr: unknown[];
    try {
      arr = JSON.parse(cleaned);
    } catch {
      return [];
    }
    if (!Array.isArray(arr) || arr.length < 2) return [];
    const bars: DailyBar[] = [];
    for (let i = 1; i < arr.length; i++) {
      const row = arr[i] as unknown[];
      if (!Array.isArray(row) || row.length < 6) continue;
      const date = String(row[0]);
      const open = Number(row[1]);
      const high = Number(row[2]);
      const low = Number(row[3]);
      const close = Number(row[4]);
      const volume = Number(row[5]);
      if (!Number.isFinite(close) || !date) continue;
      bars.push({ date, open, high, low, close, volume });
    }
    cache.set(cacheKey, { data: bars, fetched_at: Date.now() });
    return bars;
  } catch (e) {
    console.error(`fetchHistory ${ticker}:`, e);
    return [];
  }
}
