/**
 * Backend JSON read helpers.
 *
 * 로컬 개발: stock_analyzer 루트의 파일시스템에서 직접 읽음.
 * 배포(Vercel): 서버리스라 로컬 파일이 없으므로 GitHub raw 에서 fetch.
 *   - process.env.VERCEL 자동 감지, 또는 DATA_RAW_BASE 로 강제(로컬 테스트용)
 *   - cron 이 main 에 push 할 때마다 재배포 없이 최신 데이터 반영
 */
import fs from "fs";
import path from "path";
import type { BacktestTrade, Cumulative, Signal } from "./types";

// 백엔드 경로 — ui/ 폴더에서 한 단계 위가 stock_analyzer 루트
const ROOT = path.resolve(process.cwd(), "..");
const PROFITABILITY_DIR = path.join(ROOT, "profitability", "output");
const REPORTS_DIR = path.join(ROOT, "reports");

// 배포 환경이면 GitHub raw, 로컬이면 빈 문자열(=fs 사용)
const RAW_BASE =
  process.env.DATA_RAW_BASE ||
  (process.env.VERCEL
    ? "https://raw.githubusercontent.com/ccmsoo/stock-analyzer/main"
    : "");

export const USING_REMOTE_DATA = RAW_BASE !== "";

/** 단일 JSON 읽기 — 배포면 raw fetch, 로컬이면 fs */
async function readJson<T>(relPath: string, fallback: T): Promise<T> {
  try {
    if (RAW_BASE) {
      const res = await fetch(`${RAW_BASE}/${relPath}`, { cache: "no-store" });
      if (!res.ok) return fallback;
      return (await res.json()) as T;
    }
    const p = path.join(ROOT, relPath);
    if (!fs.existsSync(p)) return fallback;
    return JSON.parse(fs.readFileSync(p, "utf8")) as T;
  } catch (e) {
    console.error(`readJson ${relPath}:`, e);
    return fallback;
  }
}

function latestFileByPattern(dir: string, prefix: string, suffix: string): string | null {
  try {
    const files = fs
      .readdirSync(dir)
      .filter((f) => f.startsWith(prefix) && f.endsWith(suffix))
      .sort();
    return files.length ? path.join(dir, files[files.length - 1]) : null;
  } catch {
    return null;
  }
}

export async function loadState(): Promise<{ signals: Record<string, Signal>; generated_at: string }> {
  const data = await readJson<{ signals?: Record<string, Signal>; generated_at?: string }>(
    "state/signals.json",
    { signals: {}, generated_at: "" },
  );
  return {
    signals: data.signals || {},
    generated_at: data.generated_at || new Date().toISOString(),
  };
}

export async function loadDashboard(): Promise<{ signals: Record<string, Signal>; generated_at: string }> {
  return readJson("reports/dashboard.json", { signals: {}, generated_at: "" });
}

export interface AlertsData {
  alerts: Array<Record<string, unknown>>;
  generated_at: string;
  new_today: number;
}

export async function loadAlerts(): Promise<AlertsData> {
  const d = await readJson<Record<string, unknown> | Array<Record<string, unknown>>>(
    "reports/alerts.json",
    { alerts: [], generated_at: "", new_today: 0 },
  );
  if (Array.isArray(d)) return { alerts: d, generated_at: "", new_today: 0 };
  return {
    alerts: (d.alerts as Array<Record<string, unknown>>) || [],
    generated_at: (d.generated_at as string) || "",
    new_today: (d.new_today as number) || 0,
  };
}

export async function loadLatestBacktest(): Promise<BacktestTrade[]> {
  if (RAW_BASE) return []; // 배포: 디렉토리 listing 불가 → graceful
  const p = latestFileByPattern(PROFITABILITY_DIR, "backtest_trades_", ".json");
  if (!p) return [];
  const data = JSON.parse(fs.readFileSync(p, "utf8")) as { trades?: BacktestTrade[] } | BacktestTrade[];
  return Array.isArray(data) ? data : data.trades || [];
}

export function loadCumulative(signals: Record<string, Signal>): Cumulative {
  const wkw = new Set<string>();
  const dkw = new Set<string>();
  const dates = new Set<string>();
  const by_confidence: Record<string, number> = {};

  for (const s of Object.values(signals)) {
    (s.watch_keywords || []).forEach((kw) => {
      if (kw?.trim()) wkw.add(kw.trim());
    });
    const deep = s.deep_keywords || {};
    (["products", "partners", "places", "events", "people"] as const).forEach((cat) => {
      (deep[cat] || []).forEach((kw) => {
        if (kw?.trim()) dkw.add(kw.trim());
      });
    });
    if (s.last_seen) dates.add(s.last_seen);
    const c = (s.confidence || "none").toLowerCase();
    by_confidence[c] = (by_confidence[c] || 0) + 1;
  }

  const sorted_dates = Array.from(dates).sort();
  return {
    total_signals: Object.keys(signals).length,
    unique_watch_kw: wkw.size,
    unique_deep_kw: dkw.size,
    by_confidence,
    last_update: sorted_dates.length ? sorted_dates[sorted_dates.length - 1] : "",
    covered_dates: sorted_dates,
  };
}

/** 영업일 차이 (단순화: 달력일 기준, 주말 제외) */
export function businessDaysAgo(yyyymmdd: string): number {
  if (!yyyymmdd || yyyymmdd.length !== 8) return 999;
  const y = +yyyymmdd.slice(0, 4);
  const m = +yyyymmdd.slice(4, 6) - 1;
  const d = +yyyymmdd.slice(6, 8);
  const sigDate = new Date(y, m, d);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  let count = 0;
  const cur = new Date(sigDate);
  while (cur < now) {
    cur.setDate(cur.getDate() + 1);
    const wd = cur.getDay();
    if (wd !== 0 && wd !== 6) count++;
  }
  return count;
}

/** 모든 backtest_trades_*.json 을 합쳐서 ticker -> 가장 최근 close_on_signal_date 맵 */
export async function loadDDayCloseMap(): Promise<Map<string, { signal_date: string; close: number }>> {
  const out = new Map<string, { signal_date: string; close: number }>();
  if (RAW_BASE) return out; // 배포: 디렉토리 listing 불가 → graceful (accum% 미표시)
  try {
    const files = fs
      .readdirSync(PROFITABILITY_DIR)
      .filter((f) => f.startsWith("backtest_trades_") && f.endsWith(".json"))
      .sort();
    for (const f of files) {
      const p = path.join(PROFITABILITY_DIR, f);
      const data = JSON.parse(fs.readFileSync(p, "utf8")) as { trades?: BacktestTrade[] } | BacktestTrade[];
      const trades = Array.isArray(data) ? data : data.trades || [];
      for (const t of trades) {
        if (!t.ticker || t.close_on_signal_date === null || t.close_on_signal_date === undefined)
          continue;
        const existing = out.get(t.ticker);
        if (!existing || (t.signal_date && t.signal_date > existing.signal_date)) {
          out.set(t.ticker, { signal_date: t.signal_date, close: t.close_on_signal_date });
        }
      }
    }
  } catch (e) {
    console.error("loadDDayCloseMap:", e);
  }
  return out;
}

/** state/chains.json 로드 (밸류체인 맵) */
export interface ChainEntry {
  ticker: string;
  name: string;
  industry_chain: string;
  chain_position: string;
  chain_role: string;
  upstream: string[];
  downstream: string[];
  peer_chain: string[];
}

export interface ChainsData {
  generated_at: string;
  total: number;
  by_ticker: Record<string, ChainEntry>;
  by_industry: Record<string, Record<string, Array<{ ticker: string; name: string; role: string }>>>;
}

export async function loadChains(): Promise<ChainsData> {
  return readJson<ChainsData>("state/chains.json", {
    generated_at: "",
    total: 0,
    by_ticker: {},
    by_industry: {},
  });
}

/** reports/presurge_radar.json — 오르기 전 촉매 레이더 (cron 생성) */
export interface RadarCandidate {
  ticker: string;
  name: string;
  market: string;
  score: number;
  keyword: string;
  reason: string;
  today: number | null;
  chg5: number | null;
  from_high: number | null;
  chart_ok: boolean;
  price: number | null;
}
export interface RadarRerise {
  ticker: string;
  name: string;
  dip: number;
  from_ma: number;
  price: number | null;
}
export interface RadarData {
  date: string;
  generated_at: string;
  candidates: RadarCandidate[];
  rerise: RadarRerise[];
}

export async function loadRadar(): Promise<RadarData> {
  return readJson<RadarData>("reports/presurge_radar.json", {
    date: "",
    generated_at: "",
    candidates: [],
    rerise: [],
  });
}

export { ROOT, REPORTS_DIR };
