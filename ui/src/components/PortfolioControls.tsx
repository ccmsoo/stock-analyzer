"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const STORAGE_KEY = "stock_analyzer_portfolio_v1";

export interface Holding {
  ticker: string;
  name: string;
  entry_price: number;
  shares: number;
  entry_date: string; // ISO date string
}

export function loadPortfolio(): Holding[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Holding[]) : [];
  } catch {
    return [];
  }
}

export function savePortfolio(holdings: Holding[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(holdings));
  window.dispatchEvent(new Event("portfolio:change"));
}

interface Props {
  ticker: string;
  name: string;
  currentPrice: number | null;
}

export function PortfolioControls({ ticker, name, currentPrice }: Props) {
  const [holding, setHolding] = useState<Holding | null>(null);
  const [entryPrice, setEntryPrice] = useState<string>(currentPrice?.toString() || "");
  const [shares, setShares] = useState<string>("1");
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const p = loadPortfolio();
    const h = p.find((x) => x.ticker === ticker);
    if (h) setHolding(h);
    if (currentPrice && !entryPrice) setEntryPrice(currentPrice.toString());
  }, [ticker, currentPrice, entryPrice]);

  const handleAdd = () => {
    const ep = parseFloat(entryPrice);
    const s = parseInt(shares, 10);
    if (!Number.isFinite(ep) || !Number.isFinite(s) || ep <= 0 || s <= 0) return;
    const next: Holding = {
      ticker,
      name,
      entry_price: ep,
      shares: s,
      entry_date: new Date().toISOString().slice(0, 10),
    };
    const p = loadPortfolio().filter((x) => x.ticker !== ticker);
    p.push(next);
    savePortfolio(p);
    setHolding(next);
    setEditing(false);
  };

  const handleRemove = () => {
    const p = loadPortfolio().filter((x) => x.ticker !== ticker);
    savePortfolio(p);
    setHolding(null);
    setEditing(false);
  };

  if (holding && !editing) {
    const pnl =
      currentPrice !== null
        ? ((currentPrice / holding.entry_price - 1) * 100).toFixed(2)
        : null;
    return (
      <div className="rounded-md border border-border p-3">
        <div className="flex items-baseline justify-between text-xs">
          <span className="uppercase tracking-wider text-muted-foreground">보유 중</span>
          <div className="flex gap-2">
            <button
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setEditing(true)}
            >
              수정
            </button>
            <button
              className="text-xs text-sky-400 hover:text-sky-300"
              onClick={handleRemove}
            >
              제거
            </button>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-x-4 text-sm tabular">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">진입가</div>
            <div className="font-medium">{holding.entry_price.toLocaleString()}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">수량</div>
            <div className="font-medium">{holding.shares}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">평가손익</div>
            <div
              className={
                pnl === null
                  ? "text-muted-foreground"
                  : parseFloat(pnl) > 0
                    ? "text-rose-400 font-medium"
                    : parseFloat(pnl) < 0
                      ? "text-sky-400 font-medium"
                      : "text-muted-foreground"
              }
            >
              {pnl === null ? "—" : `${parseFloat(pnl) > 0 ? "+" : ""}${pnl}%`}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">
        {editing ? "보유 종목 수정" : "보유 종목 추가"}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          type="number"
          placeholder="진입가"
          value={entryPrice}
          onChange={(e) => setEntryPrice(e.target.value)}
          className="w-32 rounded border border-border bg-background px-2 py-1 text-sm tabular"
        />
        <input
          type="number"
          placeholder="수량"
          value={shares}
          onChange={(e) => setShares(e.target.value)}
          className="w-20 rounded border border-border bg-background px-2 py-1 text-sm tabular"
        />
        <Button onClick={handleAdd} size="sm" className="h-7 text-xs">
          {editing ? "저장" : "추가"}
        </Button>
        {editing && (
          <button
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setEditing(false)}
          >
            취소
          </button>
        )}
      </div>
    </div>
  );
}
