import Link from "next/link";
import { loadState, loadChains } from "@/lib/data";
import { buildChainOpportunities, RELATION_LABEL } from "@/lib/chains-predict";
import type { ChainCandidate, ChainOpportunity } from "@/lib/chains-predict";
import { buildEntryGuide } from "@/lib/analysis";
import { fetchManyQuotes } from "@/lib/price";
import {
  formatPrice,
  formatPct,
  formatDate,
  priceColorClass,
  TRIGGER_LABELS,
} from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

const POSITION_COLOR: Record<string, string> = {
  "원소재": "text-amber-400 border-amber-400/30",
  "장비": "text-cyan-400 border-cyan-400/30",
  "부품": "text-emerald-400 border-emerald-400/30",
  "조립/제조": "text-blue-400 border-blue-400/30",
  "유통/판매": "text-purple-400 border-purple-400/30",
  "서비스": "text-fuchsia-400 border-fuchsia-400/30",
  "최종재": "text-rose-400 border-rose-400/30",
  "지주사": "text-slate-400 border-slate-400/30",
  "기타": "text-zinc-500 border-zinc-500/30",
};

const RELATION_TONE: Record<string, string> = {
  same_stage: "text-emerald-400 border-emerald-400/40",
  adjacent: "text-cyan-400 border-cyan-400/40",
  same_chain: "text-muted-foreground border-border",
};

export default async function OpportunitiesPage() {
  const { signals } = await loadState();
  const chains = await loadChains();
  const { opportunities, topCandidates, generatedFromDate } =
    buildChainOpportunities(signals, chains);

  // 상위 후보 시세 fetch → 진입 가이드
  const topForGuide = topCandidates.slice(0, 12);
  const quotes = await fetchManyQuotes(topForGuide.map((c) => c.ticker));
  const guideMap = new Map<
    string,
    {
      current: number | null;
      todayPct: number | null;
      entry: number | null;
      stop: number | null;
      tp1: number | null;
      tp2: number | null;
    }
  >();
  for (const c of topForGuide) {
    const q = quotes.get(c.ticker);
    const sig = signals[c.ticker];
    const price = {
      current_price: q?.current_price ?? null,
      d_day_close: null,
      today_open: q?.today_open ?? null,
      today_change_pct: q?.today_change_pct ?? null,
      today_from_open_pct: q?.today_from_open_pct ?? null,
    };
    const g = sig
      ? buildEntryGuide(sig, price)
      : { entry_price_suggested: null, stop_loss: null, take_profit_1: null, take_profit_2: null, risk_reward_ratio: null };
    guideMap.set(c.ticker, {
      current: q?.current_price ?? null,
      todayPct: q?.today_change_pct ?? null,
      entry: g.entry_price_suggested,
      stop: g.stop_loss,
      tp1: g.take_profit_1,
      tp2: g.take_profit_2,
    });
  }

  const marketStatus =
    Array.from(quotes.values())[0]?.market_status === "OPEN" ? "장중" : "장마감";

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            오르기 전 후보
            <span className="ml-2 text-xs text-muted-foreground">밸류체인 전파 예측</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {generatedFromDate ? formatDate(generatedFromDate) : "—"} · {marketStatus}
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          한 산업 체인에서 <span className="text-rose-400">리더가 먼저 급등</span>하면, 같은 단계(동반)·
          인접 단계(전파)의 <span className="text-emerald-400">아직 안 오른 종목</span>이 다음 차례일 확률이 높습니다.
          그 후보를 <b className="text-foreground">오르기 전에</b> 골라냅니다.
        </p>
        <div className="mt-2 flex gap-4 text-xs text-muted-foreground tabular">
          <span>후보 {topCandidates.length}</span>
          <span>뜨거운 체인 {opportunities.length}</span>
        </div>
      </header>

      {topCandidates.length === 0 ? (
        <div className="mt-10 text-center text-sm text-muted-foreground">
          <p>지금은 전파 후보가 없습니다.</p>
          <p className="mt-2 text-xs">
            체인에 최근({"≤"}7영업일) 급등 리더가 있어야 후보가 잡힙니다. 데이터가 쌓이면 자동으로 채워집니다.
          </p>
        </div>
      ) : (
        <>
          {/* TOP 후보 — 진입 가이드 */}
          <section className="mt-6">
            <h2 className="mb-2 text-sm font-medium uppercase tracking-wider text-muted-foreground">
              TOP 후보 · 진입 가이드
            </h2>
            <div className="space-y-2">
              {topForGuide.map((c, i) => (
                <TopCandidateRow
                  key={c.ticker}
                  c={c}
                  rank={i + 1}
                  guide={guideMap.get(c.ticker)}
                />
              ))}
            </div>
          </section>

          {/* 뜨거운 체인별 */}
          <section className="mt-10">
            <h2 className="mb-2 text-sm font-medium uppercase tracking-wider text-muted-foreground">
              뜨거운 체인 — 리더 → 다음 후보
            </h2>
            <div className="space-y-4">
              {opportunities.map((o) => (
                <HotChainBlock key={o.industry} o={o} />
              ))}
            </div>
          </section>
        </>
      )}

      <p className="mt-12 border-t border-border pt-4 text-[11px] leading-relaxed text-muted-foreground">
        밸류체인 전파는 확률적 경향이지 보장이 아닙니다. 리더 급등이 테마 전체의 펀더멘털이 아니라 개별
        이슈일 수 있으니, 후보의 본인 재료(공시/실적)를 반드시 함께 확인하세요. 분할 매수 + 손절가 사전 설정 권장.
      </p>
    </main>
  );
}

function ScorePill({ score }: { score: number }) {
  const tone =
    score >= 75 ? "text-emerald-400" : score >= 60 ? "text-cyan-400" : "text-muted-foreground";
  return <span className={`tabular text-sm font-medium ${tone}`}>{score}</span>;
}

function PositionTag({ pos }: { pos: string }) {
  const color = POSITION_COLOR[pos] || POSITION_COLOR["기타"];
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${color}`}>{pos}</span>
  );
}

function TopCandidateRow({
  c,
  rank,
  guide,
}: {
  c: ChainCandidate;
  rank: number;
  guide?: {
    current: number | null;
    todayPct: number | null;
    entry: number | null;
    stop: number | null;
    tp1: number | null;
    tp2: number | null;
  };
}) {
  return (
    <Link
      href={`/signals/${c.ticker}`}
      className="block rounded-md border border-border p-3 transition-colors hover:border-foreground/30"
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-2 truncate">
          <span className="w-5 shrink-0 text-xs text-muted-foreground tabular">{rank}</span>
          <span className="truncate font-medium">{c.name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground tabular">{c.ticker}</span>
          <PositionTag pos={c.position} />
        </div>
        <ScorePill score={c.score} />
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 pl-7 text-[11px]">
        <span className="text-muted-foreground">{c.industry}</span>
        <span
          className={`rounded border px-1.5 py-0.5 ${RELATION_TONE[c.relation]}`}
        >
          {RELATION_LABEL[c.relation]}
        </span>
        {c.hasFundamental && (
          <span className="rounded border border-amber-400/40 px-1.5 py-0.5 text-amber-400">
            본인 {TRIGGER_LABELS[c.ownTrigger] || c.ownTrigger} 호재
          </span>
        )}
      </div>

      {/* 트리거(리더) */}
      <div className="mt-1.5 pl-7 text-[11px] text-muted-foreground">
        <span className="text-rose-400">🔥 {c.leaderName}</span> {formatDate(c.leaderDate)}{" "}
        <span className={priceColorClass(c.leaderChangePct)}>
          {formatPct(c.leaderChangePct, { sign: true })}
        </span>{" "}
        급등 → {c.name} {c.recentChangePct !== null ? (
          <span className={priceColorClass(c.recentChangePct)}>
            {formatPct(c.recentChangePct, { sign: true })}
          </span>
        ) : "조용"}
      </div>

      {/* 진입 가이드 */}
      {guide?.current != null && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 pl-7 text-[11px] sm:grid-cols-4">
          <Cell label="현재" value={formatPrice(guide.current)} tone={priceColorClass(guide.todayPct)} />
          <Cell label="진입" value={formatPrice(guide.entry)} />
          <Cell label="손절" value={formatPrice(guide.stop)} tone="text-sky-400" />
          <Cell label="익절" value={formatPrice(guide.tp1)} tone="text-rose-400" />
        </div>
      )}
    </Link>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`tabular ${tone || "text-foreground"}`}>{value}</div>
    </div>
  );
}

function HotChainBlock({ o }: { o: ChainOpportunity }) {
  return (
    <section className="rounded-md border border-border p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium">{o.industry}</h3>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
            <div
              className="h-full bg-rose-400/70"
              style={{ width: `${o.heat}%` }}
            />
          </div>
          <span className="text-[11px] text-rose-400 tabular">열기 {o.heat}</span>
        </div>
      </div>

      {/* 이미 오른 리더 */}
      <div className="mt-2">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          이미 오름 (리더)
        </div>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {o.leaders.map((l) => (
            <Link
              key={l.ticker}
              href={`/signals/${l.ticker}`}
              className="flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] hover:border-foreground/30"
            >
              <PositionTag pos={l.position} />
              <span>{l.name}</span>
              <span className={priceColorClass(l.changePct)}>
                {formatPct(l.changePct, { sign: true })}
              </span>
            </Link>
          ))}
        </div>
      </div>

      {/* 다음 후보 */}
      <div className="mt-3">
        <div className="text-[10px] uppercase tracking-wider text-emerald-400">
          다음 후보 (오르기 전)
        </div>
        <ul className="mt-1 space-y-1">
          {o.candidates.slice(0, 6).map((c) => (
            <li key={c.ticker}>
              <Link
                href={`/signals/${c.ticker}`}
                className="flex items-baseline justify-between gap-2 text-sm hover:underline underline-offset-4"
              >
                <span className="flex items-baseline gap-1.5 truncate">
                  <PositionTag pos={c.position} />
                  <span className="truncate">{c.name}</span>
                  <span
                    className={`rounded border px-1 text-[10px] ${RELATION_TONE[c.relation]}`}
                  >
                    {RELATION_LABEL[c.relation]}
                  </span>
                </span>
                <ScorePill score={c.score} />
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
