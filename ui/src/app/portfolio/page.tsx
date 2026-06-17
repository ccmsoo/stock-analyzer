"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  loadPortfolio,
  savePortfolio,
  type Holding,
} from "@/components/PortfolioControls";
import { TossHoldings } from "@/components/TossHoldings";
import { formatPrice, formatPct, formatDate, priceColorClass } from "@/lib/format";

interface QuoteData {
  ticker: string;
  current_price: number | null;
  today_change_pct: number | null;
  today_from_open_pct: number | null;
}

const fetcher = async (url: string): Promise<QuoteData[]> => {
  const r = await fetch(url, { cache: "no-store" });
  return r.json();
};

export default function PortfolioPage() {
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
  const quoteMap = new Map(quotes?.map((q) => [q.ticker, q]) || []);

  let totalCost = 0;
  let totalValue = 0;
  for (const h of holdings) {
    const q = quoteMap.get(h.ticker);
    totalCost += h.entry_price * h.shares;
    totalValue += (q?.current_price ?? h.entry_price) * h.shares;
  }
  const totalPnl = totalCost > 0 ? (totalValue / totalCost - 1) * 100 : 0;

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <h1 className="text-base font-medium">보유 종목</h1>
      </header>

      {/* 토스 실계좌 (자동 동기화) */}
      <div className="mt-6">
        <TossHoldings />
      </div>

      {/* 수동 추적 (localStorage) */}
      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            수동 추적
          </h2>
          {mounted && holdings.length > 0 && (
            <span className="text-xs text-muted-foreground tabular">{holdings.length}개</span>
          )}
        </div>

        {!mounted ? (
          <p className="text-sm text-muted-foreground">로드 중...</p>
        ) : holdings.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            수동 추적 종목이 없습니다.{" "}
            <Link href="/" className="text-foreground underline-offset-4 hover:underline">
              매수 후보 보기 →
            </Link>
          </p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-x-6 text-sm">
              <Kpi label="투자 원금" value={formatPrice(totalCost)} />
              <Kpi label="현재 평가" value={formatPrice(totalValue)} />
              <Kpi
                label="총 손익"
                value={
                  <span className={priceColorClass(totalPnl)}>
                    {formatPct(totalPnl, { sign: true })}
                  </span>
                }
              />
            </div>

            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
                  <th className="py-2 text-left font-normal">종목</th>
                  <th className="py-2 text-right font-normal">수량</th>
                  <th className="py-2 text-right font-normal">진입가</th>
                  <th className="py-2 text-right font-normal">현재가</th>
                  <th className="py-2 text-right font-normal">손익</th>
                  <th className="py-2 text-right font-normal">평가액</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => {
                  const q = quoteMap.get(h.ticker);
                  const cur = q?.current_price ?? null;
                  const pnl = cur !== null ? (cur / h.entry_price - 1) * 100 : null;
                  const value = cur !== null ? cur * h.shares : h.entry_price * h.shares;
                  return (
                    <tr key={h.ticker} className="border-b border-border/40 last:border-b-0">
                      <td className="py-2">
                        <Link
                          href={`/signals/${h.ticker}`}
                          className="hover:underline underline-offset-4"
                        >
                          {h.name}
                        </Link>
                        <div className="text-[10px] text-muted-foreground tabular">
                          {h.ticker} · {formatDate(h.entry_date.replace(/-/g, ""))} 진입
                        </div>
                      </td>
                      <td className="py-2 text-right tabular">{h.shares}</td>
                      <td className="py-2 text-right tabular">{formatPrice(h.entry_price)}</td>
                      <td className="py-2 text-right tabular">{formatPrice(cur)}</td>
                      <td className={`py-2 text-right font-medium tabular ${priceColorClass(pnl)}`}>
                        {formatPct(pnl, { sign: true })}
                      </td>
                      <td className="py-2 text-right tabular">{formatPrice(value)}</td>
                      <td className="py-2 pl-3 text-right">
                        <button
                          className="text-xs text-muted-foreground hover:text-sky-400"
                          onClick={() => {
                            savePortfolio(holdings.filter((x) => x.ticker !== h.ticker));
                            setHoldings(loadPortfolio());
                          }}
                        >
                          제거
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </section>
    </main>
  );
}

function Kpi({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-base font-medium tabular">{value}</div>
    </div>
  );
}
