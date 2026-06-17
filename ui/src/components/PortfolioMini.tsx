"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { loadPortfolio, type Holding } from "@/components/PortfolioControls";
import { formatPct, priceColorClass } from "@/lib/format";

const fetcher = (url: string) => fetch(url, { cache: "no-store" }).then((r) => r.json());

interface QuoteData {
  ticker: string;
  current_price: number | null;
  today_change_pct: number | null;
}

export function PortfolioMini() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setHoldings(loadPortfolio());
    const onChange = () => setHoldings(loadPortfolio());
    window.addEventListener("portfolio:change", onChange);
    return () => window.removeEventListener("portfolio:change", onChange);
  }, []);

  const tickers = holdings.map((h) => h.ticker).join(",");
  const { data: quotes } = useSWR<QuoteData[]>(
    tickers ? `/api/quotes?tickers=${tickers}` : null,
    fetcher,
    { refreshInterval: 60_000 },
  );
  const qm = new Map(quotes?.map((q) => [q.ticker, q]) || []);

  if (!mounted || holdings.length === 0) return null;

  let totalCost = 0;
  let totalValue = 0;
  for (const h of holdings) {
    const q = qm.get(h.ticker);
    totalCost += h.entry_price * h.shares;
    totalValue += (q?.current_price ?? h.entry_price) * h.shares;
  }
  const totalPnl = totalCost > 0 ? (totalValue / totalCost - 1) * 100 : 0;

  return (
    <div className="mb-8 rounded-md border border-border p-4">
      <div className="flex items-baseline justify-between">
        <Link
          href="/portfolio"
          className="text-xs font-medium uppercase tracking-wider text-muted-foreground hover:text-foreground"
        >
          보유 종목 ({holdings.length})
        </Link>
        <span className={`tabular text-base font-medium ${priceColorClass(totalPnl)}`}>
          {formatPct(totalPnl, { sign: true })}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2 md:grid-cols-3">
        {holdings.slice(0, 6).map((h) => {
          const q = qm.get(h.ticker);
          const pnl =
            q?.current_price !== null && q?.current_price !== undefined
              ? (q.current_price / h.entry_price - 1) * 100
              : null;
          return (
            <Link
              key={h.ticker}
              href={`/signals/${h.ticker}`}
              className="flex items-baseline justify-between text-sm hover:opacity-80"
            >
              <span className="truncate">{h.name}</span>
              <span className={`tabular text-xs font-medium ${priceColorClass(pnl)}`}>
                {formatPct(pnl, { sign: true })}
              </span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
