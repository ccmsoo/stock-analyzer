/**
 * 가격 fetch — Naver Finance polling endpoint (배치 지원).
 *  endpoint: https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:<ticker1>,<ticker2>,...
 *  필드: nv (현재가), sv/pcv (전일 종가), ov (시초), hv (고가), lv (저가),
 *        cr (전일比 %), aq (거래량), ms (CLOSE|OPEN), mt (시장 구분)
 *
 * 단일 호출로 최대 ~50 종목까지 가능. In-memory cache 1분.
 */

interface NaverData {
  cd: string;       // ticker
  nm: string;       // 종목명
  sv: number;       // 전일 종가
  nv: number;       // 현재가 (장종료 후엔 종가)
  cv: number;       // 변동가
  cr: number;       // 변동률 (%)
  ov: number;       // 시초가
  hv: number;       // 고가
  lv: number;       // 저가
  aq: number;       // 거래량
  aa?: number;      // 거래대금
  pcv: number;      // 전일 종가
  ms: string;       // CLOSE | OPEN
  mt: string;       // 1=KOSPI, 2=KOSDAQ
}

interface NaverResponse {
  resultCode: string;
  result?: {
    pollingInterval: number;
    areas?: Array<{ name: string; datas: NaverData[] }>;
  };
}

export interface PriceSnapshot {
  ticker: string;
  prev_close: number | null;
  today_open: number | null;
  today_high: number | null;
  today_low: number | null;
  current_price: number | null;
  today_volume: number | null;
  today_change_pct: number | null;
  today_from_open_pct: number | null;
  market_status: string;
  fetched_at: string;
}

interface CacheEntry {
  data: PriceSnapshot;
  fetched_at: number;
}

const CACHE_TTL_MS = 60_000; // 1분
const cache = new Map<string, CacheEntry>();

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  Referer: "https://m.stock.naver.com/",
  Accept: "application/json,text/plain,*/*",
};

/** Batch fetch — 한 번 호출로 최대 50종목. cache miss 만 호출. */
export async function fetchManyQuotes(
  tickers: string[],
): Promise<Map<string, PriceSnapshot>> {
  const out = new Map<string, PriceSnapshot>();
  const now = Date.now();

  // cache hit 먼저 채우기
  const missing: string[] = [];
  for (const t of tickers) {
    const c = cache.get(t);
    if (c && now - c.fetched_at < CACHE_TTL_MS) {
      out.set(t, c.data);
    } else {
      missing.push(t);
    }
  }
  if (missing.length === 0) return out;

  // 한 번에 30종목씩 (네이버 응답 사이즈 제한 회피)
  const CHUNK = 30;
  for (let i = 0; i < missing.length; i += CHUNK) {
    const batch = missing.slice(i, i + CHUNK);
    const query = `SERVICE_ITEM:${batch.join(",")}`;
    const url = `https://polling.finance.naver.com/api/realtime?query=${encodeURIComponent(query)}`;
    try {
      const res = await fetch(url, {
        headers: HEADERS,
        signal: AbortSignal.timeout(8000),
        cache: "no-store",
      });
      if (!res.ok) continue;
      const json = (await res.json()) as NaverResponse;
      const datas = json?.result?.areas?.[0]?.datas || [];
      for (const d of datas) {
        const tic = d.cd;
        const snap: PriceSnapshot = {
          ticker: tic,
          prev_close: numOr(d.pcv ?? d.sv),
          today_open: numOr(d.ov),
          today_high: numOr(d.hv),
          today_low: numOr(d.lv),
          current_price: numOr(d.nv),
          today_volume: numOr(d.aq),
          today_change_pct: numOr(d.cr),
          today_from_open_pct:
            d.nv && d.ov && d.ov > 0
              ? Number((((d.nv / d.ov) - 1) * 100).toFixed(2))
              : null,
          market_status: d.ms || "",
          fetched_at: new Date().toISOString(),
        };
        out.set(tic, snap);
        cache.set(tic, { data: snap, fetched_at: now });
      }
    } catch (e) {
      console.error(`fetchManyQuotes batch ${i}-${i + CHUNK}:`, e);
    }
  }
  return out;
}

function numOr(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "string" ? parseFloat(v.replace(/,/g, "")) : Number(v);
  return Number.isFinite(n) ? n : null;
}
