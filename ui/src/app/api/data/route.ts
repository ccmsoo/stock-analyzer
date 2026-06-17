/**
 * GET /api/data
 *  통합 payload:
 *    - cumulative: 누적 통계
 *    - signals: state 의 모든 signals
 *    - buy_candidates: 점수 + 진입 가이드 포함 TOP 10
 *
 * 가격 fetch 는 매수 후보 대상 종목만 (10-15개) → 빠름.
 * 캐시: in-memory 5분 (price.ts) + revalidate 60초.
 */
import { NextResponse } from "next/server";
import {
  loadState,
  loadCumulative,
  loadDDayCloseMap,
} from "@/lib/data";
import { buildBuyCandidates, scoreSignal } from "@/lib/analysis";
import { fetchManyQuotes } from "@/lib/price";
import { businessDaysAgo } from "@/lib/data";
import type { DataPayload, Signal } from "@/lib/types";

export const revalidate = 60; // ISR 1분
export const dynamic = "force-dynamic";

export async function GET() {
  const { signals } = loadState();
  const cumulative = loadCumulative(signals);
  const dDayMap = loadDDayCloseMap();

  // 매수 후보 candidate 추리기 — 너무 많으면 가격 fetch 부담
  const recentSignals: Array<{ ticker: string; signal: Signal }> = [];
  for (const [tic, sig] of Object.entries(signals)) {
    const age = businessDaysAgo(sig.last_seen);
    if (age > 10) continue;
    if (sig.confidence === "low" || sig.trigger_type === "unknown") continue;
    recentSignals.push({ ticker: tic, signal: sig });
  }

  // 빠른 1차 점수 — 가격 없는 상태로 정렬 후 TOP 25 만 가격 fetch
  recentSignals.sort((a, b) => {
    const scoreA = scoreSignal(a.signal, { current_price: null, d_day_close: null, today_open: null, today_change_pct: null, today_from_open_pct: null }).score;
    const scoreB = scoreSignal(b.signal, { current_price: null, d_day_close: null, today_open: null, today_change_pct: null, today_from_open_pct: null }).score;
    return scoreB - scoreA;
  });
  const topTickers = recentSignals.slice(0, 25).map((r) => r.ticker);

  // 가격 fetch
  const quotes = await fetchManyQuotes(topTickers);

  // priceMap 구성
  const priceMap = new Map<
    string,
    {
      current_price: number | null;
      d_day_close: number | null;
      today_open: number | null;
      today_change_pct: number | null;
      today_from_open_pct: number | null;
    }
  >();
  for (const tic of topTickers) {
    const q = quotes.get(tic);
    const dDay = dDayMap.get(tic);
    priceMap.set(tic, {
      current_price: q?.current_price ?? null,
      d_day_close: dDay?.close ?? null,
      today_open: q?.today_open ?? null,
      today_change_pct: q?.today_change_pct ?? null,
      today_from_open_pct: q?.today_from_open_pct ?? null,
    });
  }

  const candidatesSubset: Record<string, Signal> = {};
  for (const t of topTickers) {
    if (signals[t]) candidatesSubset[t] = signals[t];
  }
  const buy_candidates = buildBuyCandidates(candidatesSubset, priceMap, 10);

  const payload: DataPayload = {
    generated_at: new Date().toISOString(),
    cumulative,
    signals,
    buy_candidates,
  };

  return NextResponse.json(payload, {
    headers: {
      "Cache-Control": "public, s-maxage=60, stale-while-revalidate=120",
    },
  });
}
