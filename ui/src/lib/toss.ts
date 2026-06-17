/**
 * 토스증권 Open API 클라이언트 — 서버 전용.
 *  ⚠️ 클라이언트 컴포넌트에서 import 금지 (TOSS_SECRET_KEY 노출 위험).
 *  라우트 핸들러(app/api/*)나 서버 컴포넌트에서만 사용.
 *
 * 확인된 엔드포인트 (실측):
 *  - POST /oauth2/token            Basic(client_id:secret), grant_type=client_credentials → 24h 토큰
 *  - GET  /api/v1/accounts         계좌 목록 (accountNo, accountSeq)
 *  - GET  /api/v1/holdings         헤더 X-Tossinvest-Account: {accountSeq} → 집계+종목별 손익
 *  - GET  /api/v1/prices?symbols=  현재가 (다중, 콤마구분)
 *  - GET  /api/v1/candles?symbol=&interval=1d  일봉 OHLCV(최근 100)
 */
const BASE = "https://openapi.tossinvest.com";

function creds(): { id: string; secret: string } | null {
  const id = process.env.TOSS_CLIENT_KEY;
  const secret = process.env.TOSS_SECRET_KEY;
  if (!id || !secret) return null;
  return { id, secret };
}

export function tossConfigured(): boolean {
  return creds() !== null;
}

let _token: { token: string; exp: number } | null = null;

async function getToken(): Promise<string | null> {
  const c = creds();
  if (!c) return null;
  const now = Date.now();
  if (_token && _token.exp > now + 60_000) return _token.token;
  const basic = Buffer.from(`${c.id}:${c.secret}`).toString("base64");
  try {
    const res = await fetch(`${BASE}/oauth2/token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Authorization: `Basic ${basic}`,
      },
      body: "grant_type=client_credentials",
      cache: "no-store",
    });
    if (!res.ok) return null;
    const j = await res.json();
    _token = { token: j.access_token, exp: now + (j.expires_in ?? 3600) * 1000 };
    return _token.token;
  } catch {
    return null;
  }
}

async function api(path: string, extra?: Record<string, string>): Promise<any | null> {
  const tok = await getToken();
  if (!tok) return null;
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { Authorization: `Bearer ${tok}`, Accept: "application/json", ...(extra || {}) },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

let _accountSeq: string | null = null;
async function accountSeq(): Promise<string | null> {
  if (_accountSeq) return _accountSeq;
  const j = await api("/api/v1/accounts");
  const a = j?.result?.[0];
  if (!a) return null;
  _accountSeq = String(a.accountSeq);
  return _accountSeq;
}

const num = (x: unknown): number => (x == null ? 0 : Number(x));

export interface TossHolding {
  symbol: string;
  name: string;
  country: string;
  currency: string;
  quantity: number;
  lastPrice: number;
  avgPrice: number;
  value: number;
  pnlAmount: number;
  pnlRate: number;
  dailyPnlRate: number;
}

export interface TossHoldings {
  connected: boolean;
  aggregate?: {
    purchase: { krw: number; usd: number };
    value: { krw: number; usd: number };
    pnlAmount: { krw: number; usd: number };
    pnlRate: number;
    dailyPnlRate: number;
  };
  items: TossHolding[];
}

export async function getHoldings(): Promise<TossHoldings> {
  if (!tossConfigured()) return { connected: false, items: [] };
  const seq = await accountSeq();
  if (!seq) return { connected: false, items: [] };
  const j = await api("/api/v1/holdings", { "X-Tossinvest-Account": seq });
  const r = j?.result;
  if (!r) return { connected: false, items: [] };
  return {
    connected: true,
    aggregate: {
      purchase: { krw: num(r.totalPurchaseAmount?.krw), usd: num(r.totalPurchaseAmount?.usd) },
      value: { krw: num(r.marketValue?.amount?.krw), usd: num(r.marketValue?.amount?.usd) },
      pnlAmount: { krw: num(r.profitLoss?.amount?.krw), usd: num(r.profitLoss?.amount?.usd) },
      pnlRate: num(r.profitLoss?.rate),
      dailyPnlRate: num(r.dailyProfitLoss?.rate),
    },
    items: (r.items || []).map((it: any) => ({
      symbol: it.symbol,
      name: it.name || it.symbol,
      country: it.marketCountry || "",
      currency: it.currency || "",
      quantity: num(it.quantity),
      lastPrice: num(it.lastPrice),
      avgPrice: num(it.averagePurchasePrice),
      value: num(it.marketValue?.amount),
      pnlAmount: num(it.profitLoss?.amount),
      pnlRate: num(it.profitLoss?.rate),
      dailyPnlRate: num(it.dailyProfitLoss?.rate),
    })),
  };
}

/** 현재가 (다중 심볼) → { symbol: lastPrice } */
export async function getPrices(symbols: string[]): Promise<Record<string, number>> {
  if (!symbols.length || !tossConfigured()) return {};
  const j = await api(`/api/v1/prices?symbols=${symbols.join(",")}`);
  const out: Record<string, number> = {};
  for (const p of j?.result || []) out[p.symbol] = num(p.lastPrice);
  return out;
}

export interface TossCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** 일봉 OHLCV (최근→과거, 최대 100) */
export async function getCandles(symbol: string, interval = "1d"): Promise<TossCandle[]> {
  if (!tossConfigured()) return [];
  const j = await api(`/api/v1/candles?symbol=${symbol}&interval=${interval}`);
  const candles = j?.result?.candles || [];
  return candles.map((c: any) => ({
    date: (c.timestamp || "").slice(0, 10),
    open: num(c.openPrice),
    high: num(c.highPrice),
    low: num(c.lowPrice),
    close: num(c.closePrice),
    volume: num(c.volume),
  }));
}
