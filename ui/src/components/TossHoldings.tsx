"use client";
import useSWR from "swr";
import { formatPct, priceColorClass } from "@/lib/format";
import type { TossHoldings as TossData } from "@/lib/toss";

const fetcher = (url: string) => fetch(url, { cache: "no-store" }).then((r) => r.json());

function money(n: number, cur: string): string {
  if (cur === "USD")
    return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return "₩" + Math.round(n).toLocaleString("ko-KR");
}

function aggAmount(krw: number, usd: number): string {
  const parts: string[] = [];
  if (krw) parts.push(money(krw, "KRW"));
  if (usd) parts.push(money(usd, "USD"));
  return parts.length ? parts.join(" + ") : "—";
}

const flag = (c: string) => (c === "US" ? "🇺🇸" : c === "KR" ? "🇰🇷" : "");

export function TossHoldings() {
  const { data, isLoading } = useSWR<TossData>("/api/toss/holdings", fetcher, {
    refreshInterval: 60_000,
  });

  if (isLoading) {
    return (
      <section className="mb-8">
        <div className="h-4 w-28 animate-pulse rounded bg-secondary" />
        <div className="mt-3 h-16 animate-pulse rounded bg-secondary/40" />
      </section>
    );
  }

  if (!data?.connected) {
    return (
      <section className="mb-8 rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
        토스증권 미연동.{" "}
        <code className="text-[11px]">ui/.env.local</code> 에 TOSS_CLIENT_KEY / TOSS_SECRET_KEY 설정 시 실계좌 자동 동기화.
      </section>
    );
  }

  const agg = data.aggregate;
  const pnlRate = (agg?.pnlRate ?? 0) * 100;
  const dailyRate = (agg?.dailyPnlRate ?? 0) * 100;

  return (
    <section className="mb-8">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
          토스 실계좌
          <span className="ml-1.5 rounded border border-emerald-400/40 px-1.5 py-0.5 text-[10px] normal-case text-emerald-400">
            자동 동기화
          </span>
        </h2>
        <span className="text-xs text-muted-foreground tabular">{data.items.length}종목</span>
      </div>

      {/* 집계 KPI */}
      {agg && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-md border border-border p-4 sm:grid-cols-4">
          <Kpi label="투자 원금" value={aggAmount(agg.purchase.krw, agg.purchase.usd)} />
          <Kpi label="평가액" value={aggAmount(agg.value.krw, agg.value.usd)} />
          <Kpi
            label="총 손익"
            value={
              <span className={priceColorClass(pnlRate)}>
                {formatPct(pnlRate, { sign: true })}
              </span>
            }
            sub={aggAmount(agg.pnlAmount.krw, agg.pnlAmount.usd)}
          />
          <Kpi
            label="오늘"
            value={
              <span className={priceColorClass(dailyRate)}>
                {formatPct(dailyRate, { sign: true })}
              </span>
            }
          />
        </div>
      )}

      {/* 종목별 */}
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="py-2 text-left font-normal">종목</th>
            <th className="py-2 text-right font-normal">수량</th>
            <th className="py-2 text-right font-normal">평단</th>
            <th className="py-2 text-right font-normal">현재가</th>
            <th className="py-2 text-right font-normal">손익</th>
            <th className="py-2 text-right font-normal">평가액</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((it) => {
            const rate = it.pnlRate * 100;
            return (
              <tr key={it.symbol} className="border-b border-border/40 last:border-b-0">
                <td className="py-2">
                  <span className="font-medium">
                    {flag(it.country)} {it.name}
                  </span>
                  <div className="text-[10px] text-muted-foreground tabular">
                    {it.symbol} · {it.currency}
                  </div>
                </td>
                <td className="py-2 text-right tabular">{it.quantity}</td>
                <td className="py-2 text-right tabular">{money(it.avgPrice, it.currency)}</td>
                <td className="py-2 text-right tabular">{money(it.lastPrice, it.currency)}</td>
                <td className={`py-2 text-right font-medium tabular ${priceColorClass(rate)}`}>
                  {formatPct(rate, { sign: true })}
                </td>
                <td className="py-2 text-right tabular">{money(it.value, it.currency)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function Kpi({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-base font-medium tabular">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground tabular">{sub}</div>}
    </div>
  );
}
