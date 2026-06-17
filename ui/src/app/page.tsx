import Link from "next/link";
import { KPIBanner } from "@/components/KPIBanner";
import { BuyCandidateCard } from "@/components/BuyCandidateCard";
import { PortfolioMini } from "@/components/PortfolioMini";
import {
  loadState,
  loadCumulative,
  loadDDayCloseMap,
  loadChains,
  businessDaysAgo,
} from "@/lib/data";
import { buildBuyCandidates, scoreSignal } from "@/lib/analysis";
import { buildChainOpportunities, RELATION_LABEL } from "@/lib/chains-predict";
import { fetchManyQuotes } from "@/lib/price";
import { formatPct, priceColorClass } from "@/lib/format";
import type { Signal } from "@/lib/types";

export const revalidate = 0;
export const dynamic = "force-dynamic";

export default async function Home() {
  const { signals } = loadState();
  const cumulative = loadCumulative(signals);
  const dDayMap = loadDDayCloseMap();
  const chains = loadChains();
  const { topCandidates: chainCandidates } = buildChainOpportunities(signals, chains);

  const recentSignals: Array<{ ticker: string; signal: Signal }> = [];
  for (const [tic, sig] of Object.entries(signals)) {
    const age = businessDaysAgo(sig.last_seen);
    if (age > 10) continue;
    if (sig.confidence === "low" || sig.trigger_type === "unknown") continue;
    recentSignals.push({ ticker: tic, signal: sig });
  }
  recentSignals.sort((a, b) => {
    const empty = {
      current_price: null,
      d_day_close: null,
      today_open: null,
      today_change_pct: null,
      today_from_open_pct: null,
    };
    return scoreSignal(b.signal, empty).score - scoreSignal(a.signal, empty).score;
  });
  const topTickers = recentSignals.slice(0, 25).map((r) => r.ticker);

  const quotes = await fetchManyQuotes(topTickers);
  const priceMap = new Map();
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
  const buyCandidates = buildBuyCandidates(candidatesSubset, priceMap, 10);

  const firstQuote = Array.from(quotes.values())[0];
  const marketStatus = firstQuote?.market_status === "CLOSE" ? "장마감" : "장중";
  const generatedAt = new Date().toISOString();

  return (
    <>
      <KPIBanner cumulative={cumulative} generatedAt={generatedAt} />

      <main className="container max-w-4xl py-8">
        <PortfolioMini />

        {/* 오르기 전 후보 (밸류체인 전파) — hero */}
        {chainCandidates.length > 0 && (
          <section className="mb-8">
            <div className="mb-2 flex items-baseline justify-between">
              <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
                오르기 전 후보
                <span className="ml-1.5 text-[11px] normal-case text-muted-foreground/70">
                  밸류체인 전파
                </span>
              </h2>
              <Link
                href="/opportunities"
                className="text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                전체 {chainCandidates.length} →
              </Link>
            </div>
            <div className="space-y-1.5">
              {chainCandidates.slice(0, 5).map((c) => (
                <Link
                  key={c.ticker}
                  href={`/signals/${c.ticker}`}
                  className="block rounded-md border border-border px-3 py-2 transition-colors hover:border-foreground/30"
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate">
                      <span className="font-medium">{c.name}</span>
                      <span className="ml-1.5 text-[11px] text-muted-foreground">
                        {c.position} · {c.industry}
                      </span>
                    </span>
                    <span className="shrink-0 tabular text-sm text-emerald-400">{c.score}</span>
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                    <span className="text-rose-400">🔥 {c.leaderName}</span>{" "}
                    <span className={priceColorClass(c.leaderChangePct)}>
                      {formatPct(c.leaderChangePct, { sign: true })}
                    </span>{" "}
                    급등 · {RELATION_LABEL[c.relation]}
                    {c.hasFundamental && " · 본인 호재 보유"}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        )}

        {/* 섹션 헤더 */}
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            오늘 매수 후보
          </h2>
          <div className="flex items-baseline gap-3 text-xs text-muted-foreground">
            <span className="tabular">{buyCandidates.length}건</span>
            <span className="text-muted-foreground/40">·</span>
            <span className={marketStatus === "장중" ? "text-emerald-400" : ""}>
              {marketStatus}
            </span>
          </div>
        </div>

        {/* 매수 후보 리스트 */}
        <div>
          {buyCandidates.map((c, i) => (
            <BuyCandidateCard key={c.ticker} candidate={c} rank={i + 1} />
          ))}
        </div>

        {/* 디스클레이머 */}
        <p className="mt-12 border-t border-border pt-4 text-[11px] leading-relaxed text-muted-foreground">
          이 시스템은 매수 추천이 아닙니다. AI 가 뉴스/차트/공시를 분석한 결과로 관찰 우선순위와
          룰 기반 진입 가이드를 제시합니다. 매매 결정과 결과 책임은 사용자 본인에게 있습니다.
          분할 매수 + 손절가 사전 설정을 권장합니다.
        </p>
      </main>
    </>
  );
}
