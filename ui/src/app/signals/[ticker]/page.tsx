import { notFound } from "next/navigation";
import Link from "next/link";
import { loadChains } from "@/lib/data";
import {
  loadState,
  loadDDayCloseMap,
  businessDaysAgo,
} from "@/lib/data";
import { fetchManyQuotes } from "@/lib/price";
import { fetchHistory } from "@/lib/history";
import { scoreSignal, buildEntryGuide, computeAccum } from "@/lib/analysis";
import {
  formatDate,
  formatPrice,
  formatPct,
  priceColorClass,
  TRIGGER_LABELS,
  RECOMMENDATION_LABEL,
} from "@/lib/format";
import { PriceChart } from "@/components/PriceChart";
import { PortfolioControls } from "@/components/PortfolioControls";

export const revalidate = 0;
export const dynamic = "force-dynamic";

export default async function TickerDetail({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  const { signals } = loadState();
  const sig = signals[ticker];
  if (!sig) return notFound();

  const [quotes, bars, dDayMap, chains] = await Promise.all([
    fetchManyQuotes([ticker]),
    fetchHistory(ticker, 90),
    Promise.resolve(loadDDayCloseMap()),
    Promise.resolve(loadChains()),
  ]);
  const q = quotes.get(ticker);
  const dDay = dDayMap.get(ticker);
  const chainEntry = chains.by_ticker[ticker];

  // 같은 산업의 다른 종목들 (최근 강세 우선)
  const chainPeers = chainEntry?.industry_chain
    ? Object.entries(chains.by_industry[chainEntry.industry_chain] || {})
        .flatMap(([pos, arr]) =>
          arr
            .filter((s) => s.ticker !== ticker)
            .map((s) => ({ ...s, position: pos })),
        )
        .slice(0, 15)
    : [];

  const price = {
    current_price: q?.current_price ?? null,
    d_day_close: dDay?.close ?? null,
    today_open: q?.today_open ?? null,
    today_change_pct: q?.today_change_pct ?? null,
    today_from_open_pct: q?.today_from_open_pct ?? null,
  };

  const { score, recommendation, rationale } = scoreSignal(sig, price);
  const entry = buildEntryGuide(sig, price);
  const accum = computeAccum(sig, price);
  const ageDays = businessDaysAgo(sig.last_seen);

  return (
    <main className="container max-w-4xl py-8">
      <Link href="/" className="text-xs text-muted-foreground hover:text-foreground">
        ← 매수 후보로
      </Link>

      {/* Header */}
      <header className="mt-4 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-border pb-5">
        <div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{sig.name}</h1>
            <span className="text-sm text-muted-foreground tabular">{ticker}</span>
            <span className="text-xs text-muted-foreground">{sig.market}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 text-xs text-muted-foreground">
            <span>{TRIGGER_LABELS[sig.trigger_type] || sig.trigger_type}</span>
            <span className="text-muted-foreground/40">·</span>
            <span>{sig.confidence}</span>
            <span className="text-muted-foreground/40">·</span>
            <span className="tabular">
              {formatDate(sig.last_seen)} (D+{ageDays})
            </span>
            <span className="text-muted-foreground/40">·</span>
            <span>{sig.consecutive_days}일 연속</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-semibold tabular">{formatPrice(price.current_price)}</div>
          <div className="mt-0.5 text-xs">
            <span className={priceColorClass(price.today_change_pct)}>
              전일比 {formatPct(price.today_change_pct, { sign: true })}
            </span>
            <span className="mx-2 text-muted-foreground/40">·</span>
            <span className={priceColorClass(price.today_from_open_pct)}>
              시초比 {formatPct(price.today_from_open_pct, { sign: true })}
            </span>
          </div>
        </div>
      </header>

      {/* Score + 추천 + 진입 가이드 */}
      <section className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="md:col-span-2">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            진입 가이드
          </div>
          <div className="grid grid-cols-4 gap-3 rounded-md border border-border p-3 text-sm">
            <Field label="진입" value={formatPrice(entry.entry_price_suggested)} />
            <Field label="손절" value={formatPrice(entry.stop_loss)} className="text-sky-400" />
            <Field
              label="익절 1"
              value={formatPrice(entry.take_profit_1)}
              className="text-rose-400"
            />
            <Field
              label="익절 2"
              value={formatPrice(entry.take_profit_2)}
              className="text-rose-300"
            />
          </div>
          {entry.risk_reward_ratio !== null && (
            <div className="mt-1 text-[11px] text-muted-foreground">
              R/R <span className="tabular text-foreground">{entry.risk_reward_ratio}</span> ·
              D-day 누적{" "}
              <span className={`tabular ${priceColorClass(accum)}`}>
                {formatPct(accum, { sign: true })}
              </span>
            </div>
          )}
        </div>
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            평가 점수
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-3xl font-semibold tabular">{score}</div>
            <div className="text-xs text-muted-foreground">/ 100</div>
            <div className="mt-2 text-xs">
              {RECOMMENDATION_LABEL[recommendation] || recommendation}
            </div>
          </div>
        </div>
      </section>

      {/* 보유 종목 컨트롤 */}
      <section className="mt-6">
        <PortfolioControls
          ticker={ticker}
          name={sig.name}
          currentPrice={price.current_price}
        />
      </section>

      {/* 가격 차트 */}
      <section className="mt-8">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            최근 90일 가격
          </h2>
          <div className="text-[11px] text-muted-foreground">
            <span className="mr-2">— 종가</span>
            <span className="mr-2 text-zinc-400">··· 진입</span>
            <span className="mr-2 text-sky-400">··· 손절</span>
            <span className="mr-2 text-rose-400">··· 익절</span>
            <span className="text-amber-400">| D-day</span>
          </div>
        </div>
        <PriceChart
          bars={bars}
          signalDate={sig.last_seen}
          entryPrice={entry.entry_price_suggested}
          stopLoss={entry.stop_loss}
          takeProfit={entry.take_profit_1}
        />
      </section>

      {/* 밸류체인 */}
      {chainEntry?.industry_chain && (
        <section className="mt-10">
          <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            밸류체인 위치
          </h2>
          <div className="mt-2 rounded-md border border-border p-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
              <Link
                href={`/chains?industry=${encodeURIComponent(chainEntry.industry_chain)}`}
                className="font-medium hover:underline underline-offset-4"
              >
                {chainEntry.industry_chain}
              </Link>
              <span className="rounded border border-border px-1.5 py-0.5 text-[11px] text-foreground/85">
                {chainEntry.chain_position}
              </span>
              {chainEntry.chain_role && (
                <span className="text-xs text-muted-foreground">{chainEntry.chain_role}</span>
              )}
            </div>
            {(chainEntry.upstream?.length || chainEntry.downstream?.length || chainEntry.peer_chain?.length) && (
              <div className="mt-3 space-y-1.5 text-xs">
                {chainEntry.upstream?.length > 0 && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">상류:</span>
                    <span className="ml-2">{chainEntry.upstream.join(", ")}</span>
                  </div>
                )}
                {chainEntry.downstream?.length > 0 && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">하류:</span>
                    <span className="ml-2">{chainEntry.downstream.join(", ")}</span>
                  </div>
                )}
                {chainEntry.peer_chain?.length > 0 && (
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">동반:</span>
                    <span className="ml-2">{chainEntry.peer_chain.join(", ")}</span>
                  </div>
                )}
              </div>
            )}

            {/* 같은 산업 동반 종목 */}
            {chainPeers.length > 0 && (
              <div className="mt-4 border-t border-border pt-3">
                <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
                  같은 산업 동반 종목 ({chainPeers.length}건)
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-xs">
                  {chainPeers.map((p) => (
                    <Link
                      key={p.ticker}
                      href={`/signals/${p.ticker}`}
                      className="hover:underline underline-offset-4"
                    >
                      <span className="text-muted-foreground">[{p.position}]</span>{" "}
                      <span className="text-foreground/85">{p.name}</span>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* AI 분석 */}
      <section className="mt-10">
        <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
          AI 분석
        </h2>
        <div className="mt-2 space-y-4">
          <Detail label="시그널" value={sig.specific_signal || "—"} />
          <Detail label="추론 근거" value={sig.reasoning || "—"} />
          <Detail label="평가 근거 (자동)" value={rationale} />
          {sig.related_stocks && sig.related_stocks.length > 0 && (
            <Detail
              label="연관 종목"
              value={
                <div className="flex flex-wrap gap-1.5">
                  {sig.related_stocks.map((r, i) => (
                    <span
                      key={i}
                      className="rounded border border-border bg-secondary/30 px-1.5 py-0.5 text-xs"
                    >
                      {r}
                    </span>
                  ))}
                </div>
              }
            />
          )}
          {sig.watch_keywords && sig.watch_keywords.length > 0 && (
            <Detail
              label="watch_keywords"
              value={
                <div className="flex flex-wrap gap-1.5">
                  {sig.watch_keywords.map((kw, i) => (
                    <span
                      key={i}
                      className="rounded border border-border px-1.5 py-0.5 text-xs tabular text-muted-foreground"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              }
            />
          )}
        </div>
      </section>

      {/* History — 며칠 연속 등장 */}
      {sig.history && sig.history.length > 0 && (
        <section className="mt-10">
          <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            등장 이력
          </h2>
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
                <th className="py-1 text-left">일자</th>
                <th className="py-1 text-right">등락률</th>
              </tr>
            </thead>
            <tbody>
              {sig.history.map((h, i) => (
                <tr key={i} className="border-b border-border/40 last:border-b-0">
                  <td className="py-1 text-left tabular">{formatDate(h.date)}</td>
                  <td className={`py-1 text-right tabular ${priceColorClass(h.change_pct)}`}>
                    {formatPct(h.change_pct, { sign: true })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

function Field({
  label,
  value,
  className = "",
}: {
  label: string;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-0.5 font-medium tabular ${className}`}>{value}</div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm leading-relaxed text-foreground/85">{value}</div>
    </div>
  );
}
