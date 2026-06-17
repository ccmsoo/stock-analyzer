import Link from "next/link";
import { loadState } from "@/lib/data";

export const revalidate = 0;
export const dynamic = "force-dynamic";

interface KeywordRow {
  keyword: string;
  count: number;
  tickers: Array<{ ticker: string; name: string; confidence: string; last_seen: string }>;
}

export default async function KeywordsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { signals } = await loadState();
  const params = await searchParams;
  const q = (params.q || "").toLowerCase();

  const map = new Map<string, KeywordRow>();
  for (const [ticker, sig] of Object.entries(signals)) {
    for (const kw of sig.watch_keywords || []) {
      const trimmed = kw.trim();
      if (!trimmed) continue;
      const existing = map.get(trimmed) || { keyword: trimmed, count: 0, tickers: [] };
      existing.count++;
      existing.tickers.push({
        ticker,
        name: sig.name,
        confidence: sig.confidence,
        last_seen: sig.last_seen,
      });
      map.set(trimmed, existing);
    }
  }

  let rows = Array.from(map.values()).sort((a, b) => b.count - a.count);
  if (q) rows = rows.filter((r) => r.keyword.toLowerCase().includes(q));

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">키워드 라이브러리</h1>
          <span className="text-xs text-muted-foreground tabular">{rows.length}개</span>
        </div>
        <form className="mt-3 flex gap-2 text-xs" method="get">
          <input
            type="text"
            name="q"
            placeholder="검색"
            defaultValue={params.q || ""}
            className="rounded border border-border bg-background px-2 py-1 w-56"
          />
          <button type="submit" className="rounded border border-border px-3 py-1 hover:bg-secondary">
            검색
          </button>
        </form>
      </header>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="py-2 text-left font-normal">키워드</th>
            <th className="py-2 text-right font-normal w-20">등장</th>
            <th className="py-2 text-left font-normal">대표 종목 (high/medium)</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 80).map((r) => {
            const hi = r.tickers.filter((t) => t.confidence === "high" || t.confidence === "medium").slice(0, 5);
            return (
              <tr key={r.keyword} className="border-b border-border/40 align-top last:border-b-0">
                <td className="py-2 font-medium">{r.keyword}</td>
                <td className="py-2 text-right tabular text-muted-foreground">{r.count}</td>
                <td className="py-2 text-xs">
                  <div className="flex flex-wrap gap-x-2 gap-y-1">
                    {hi.length > 0
                      ? hi.map((t) => (
                          <Link
                            key={t.ticker}
                            href={`/signals/${t.ticker}`}
                            className="text-foreground/80 hover:text-foreground hover:underline underline-offset-4"
                          >
                            {t.name}
                          </Link>
                        ))
                      : <span className="text-muted-foreground">—</span>}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length > 80 && (
        <p className="mt-4 text-xs text-muted-foreground">상위 80건만 표시</p>
      )}
    </main>
  );
}
