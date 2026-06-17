/**
 * Backend JSON read helpers.
 *
 * Backend stays as-is (state/signals.json, reports/dashboard.json,
 * profitability/output/backtest_trades_*.json). This module just reads
 * them into normalized TypeScript shapes the UI can consume.
 */
import fs from "fs";
import path from "path";
import type {
  BacktestTrade,
  Cumulative,
  Signal,
} from "./types";

// 백엔드 경로 — ui/ 폴더에서 한 단계 위가 stock_analyzer 루트
const ROOT = path.resolve(process.cwd(), "..");

const STATE_PATH = path.join(ROOT, "state", "signals.json");
const DASHBOARD_PATH = path.join(ROOT, "reports", "dashboard.json");
const PROFITABILITY_DIR = path.join(ROOT, "profitability", "output");
const REPORTS_DIR = path.join(ROOT, "reports");

function safeReadJson<T>(p: string, fallback: T): T {
  try {
    if (!fs.existsSync(p)) return fallback;
    const txt = fs.readFileSync(p, "utf8");
    return JSON.parse(txt) as T;
  } catch (e) {
    console.error(`safeReadJson ${p}:`, e);
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

export function loadState(): { signals: Record<string, Signal>; generated_at: string } {
  const data = safeReadJson<{ signals: Record<string, Signal>; generated_at?: string }>(
    STATE_PATH,
    { signals: {}, generated_at: "" },
  );
  return {
    signals: data.signals || {},
    generated_at: data.generated_at || new Date().toISOString(),
  };
}

export function loadDashboard(): { signals: Record<string, Signal>; generated_at: string } {
  return safeReadJson(DASHBOARD_PATH, { signals: {}, generated_at: "" });
}

export function loadLatestBacktest(): BacktestTrade[] {
  const p = latestFileByPattern(PROFITABILITY_DIR, "backtest_trades_", ".json");
  if (!p) return [];
  const data = safeReadJson<{ trades?: BacktestTrade[] } | BacktestTrade[]>(p, []);
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
export function loadDDayCloseMap(): Map<string, { signal_date: string; close: number }> {
  const out = new Map<string, { signal_date: string; close: number }>();
  try {
    const files = fs
      .readdirSync(PROFITABILITY_DIR)
      .filter((f) => f.startsWith("backtest_trades_") && f.endsWith(".json"))
      .sort(); // 오래된 것부터 → 최신 것이 덮어쓰게
    for (const f of files) {
      const p = path.join(PROFITABILITY_DIR, f);
      const data = safeReadJson<{ trades?: BacktestTrade[] } | BacktestTrade[]>(p, []);
      const trades = Array.isArray(data) ? data : data.trades || [];
      for (const t of trades) {
        if (!t.ticker || t.close_on_signal_date === null || t.close_on_signal_date === undefined)
          continue;
        const existing = out.get(t.ticker);
        // 최신 signal_date 우선
        if (!existing || (t.signal_date && t.signal_date > existing.signal_date)) {
          out.set(t.ticker, {
            signal_date: t.signal_date,
            close: t.close_on_signal_date,
          });
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

const CHAINS_PATH = path.join(ROOT, "state", "chains.json");

export function loadChains(): ChainsData {
  const fallback: ChainsData = { generated_at: "", total: 0, by_ticker: {}, by_industry: {} };
  return safeReadJson<ChainsData>(CHAINS_PATH, fallback);
}

export { ROOT, REPORTS_DIR };
