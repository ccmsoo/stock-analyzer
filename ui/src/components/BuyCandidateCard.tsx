import Link from "next/link";
import type { BuyCandidate } from "@/lib/types";
import {
  formatPrice,
  formatPct,
  priceColorClass,
  TRIGGER_LABELS,
  RECOMMENDATION_LABEL,
  formatDate,
} from "@/lib/format";

interface Props {
  candidate: BuyCandidate;
  rank: number;
}

export function BuyCandidateCard({ candidate: c, rank }: Props) {
  return (
    <article className="group border-b border-border py-6 last:border-b-0">
      {/* Row 1: 순위 · 종목명 · 점수 */}
      <div className="flex items-baseline justify-between gap-4">
        <Link
          href={`/signals/${c.ticker}`}
          className="flex items-baseline gap-3 min-w-0 hover:opacity-80"
        >
          <span className="text-xs text-muted-foreground tabular w-5 shrink-0">
            {String(rank).padStart(2, "0")}
          </span>
          <h3 className="truncate text-base font-medium tracking-tight underline-offset-4 group-hover:underline">
            {c.name}
          </h3>
          <span className="text-xs text-muted-foreground tabular shrink-0">{c.ticker}</span>
        </Link>
        <div className="flex items-baseline gap-3 shrink-0">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {RECOMMENDATION_LABEL[c.recommendation] || c.recommendation}
          </span>
          <span className="font-medium tabular text-lg">{c.score}</span>
        </div>
      </div>

      {/* Row 2: 메타 — 시장 · D-day · 트리거 · 신뢰도 */}
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 pl-8 text-xs text-muted-foreground">
        <span>{c.market}</span>
        <Dot />
        <span className="tabular">
          {formatDate(c.signal_date)} (D+{c.signal_age_days})
        </span>
        <Dot />
        <span className="text-foreground/70">
          {TRIGGER_LABELS[c.trigger_type] || c.trigger_type}
          <span className="ml-1 text-muted-foreground">· {c.confidence}</span>
        </span>
      </div>

      {/* Row 3: 시그널 한 줄 */}
      {c.specific_signal && (
        <p className="mt-3 pl-8 text-sm leading-relaxed text-foreground/85 line-clamp-2">
          {c.specific_signal}
        </p>
      )}

      {/* Row 4: 가격 / 변동 / 진입 가이드 — 단일 그리드 */}
      <div className="mt-4 ml-8 grid grid-cols-2 gap-x-8 gap-y-3 md:grid-cols-4">
        <Field
          label="현재가"
          value={<span className="tabular">{formatPrice(c.current_price)}</span>}
        />
        <Field
          label="시초比 · 누적"
          value={
            <span className="tabular">
              <span className={priceColorClass(c.today_from_open_pct)}>
                {formatPct(c.today_from_open_pct, { sign: true })}
              </span>
              <span className="mx-1 text-muted-foreground/50">·</span>
              <span className={priceColorClass(c.accum_pct_from_d_day)}>
                {formatPct(c.accum_pct_from_d_day, { sign: true })}
              </span>
            </span>
          }
        />
        <Field
          label="진입 → 손절"
          value={
            <span className="tabular text-sm">
              {formatPrice(c.entry_price_suggested)}
              <span className="mx-1 text-muted-foreground/50">→</span>
              <span className="text-sky-400">{formatPrice(c.stop_loss)}</span>
            </span>
          }
        />
        <Field
          label="익절 1 / 2"
          value={
            <span className="tabular text-sm">
              <span className="text-rose-400">{formatPrice(c.take_profit_1)}</span>
              <span className="mx-1 text-muted-foreground/50">/</span>
              <span className="text-rose-400/70">{formatPrice(c.take_profit_2)}</span>
              {c.risk_reward_ratio && (
                <span className="ml-2 text-[11px] text-muted-foreground">
                  R/R {c.risk_reward_ratio}
                </span>
              )}
            </span>
          }
        />
      </div>

      {/* Row 5: 근거 (작게) */}
      {c.rationale && (
        <p className="mt-3 pl-8 text-[11px] leading-relaxed text-muted-foreground">
          {c.rationale}
        </p>
      )}
    </article>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-medium">{value}</div>
    </div>
  );
}

function Dot() {
  return <span className="text-muted-foreground/40">·</span>;
}
