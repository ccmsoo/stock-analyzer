import Link from "next/link";
import { loadState, businessDaysAgo } from "@/lib/data";
import {
  formatDate,
  formatPct,
  priceColorClass,
  TRIGGER_LABELS,
} from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

export default async function SignalsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; confidence?: string; trigger?: string }>;
}) {
  const { signals } = loadState();
  const params = await searchParams;
  const q = (params.q || "").toLowerCase();
  const confFilter = params.confidence || "";
  const trigFilter = params.trigger || "";

  let list = Object.entries(signals).map(([ticker, sig]) => {
    const lastChange = sig.history?.[sig.history.length - 1]?.change_pct ?? null;
    return {
      ticker,
      name: sig.name,
      market: sig.market,
      last_seen: sig.last_seen,
      age: businessDaysAgo(sig.last_seen),
      confidence: sig.confidence,
      trigger_type: sig.trigger_type,
      specific_signal: sig.specific_signal,
      consecutive_days: sig.consecutive_days,
      change_pct: lastChange,
    };
  });

  if (q) {
    list = list.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.ticker.includes(q) ||
        (s.specific_signal || "").toLowerCase().includes(q),
    );
  }
  if (confFilter) list = list.filter((s) => s.confidence === confFilter);
  if (trigFilter) list = list.filter((s) => s.trigger_type === trigFilter);

  // 최신순 → 신뢰도 → 등장일 역순
  list.sort((a, b) => {
    if (a.last_seen !== b.last_seen) return a.last_seen < b.last_seen ? 1 : -1;
    const co = { high: 0, medium: 1, low: 2, "": 3 };
    return (co[a.confidence as keyof typeof co] ?? 9) - (co[b.confidence as keyof typeof co] ?? 9);
  });

  return (
    <main className="container max-w-5xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">전체 시그널</h1>
          <span className="text-xs text-muted-foreground tabular">{list.length}건</span>
        </div>

        {/* 필터 */}
        <form className="mt-3 flex flex-wrap gap-2 text-xs" method="get">
          <input
            type="text"
            name="q"
            placeholder="검색 (종목/티커/시그널)"
            defaultValue={params.q || ""}
            className="rounded border border-border bg-background px-2 py-1 w-56"
          />
          <select
            name="confidence"
            defaultValue={confFilter}
            className="rounded border border-border bg-background px-2 py-1"
          >
            <option value="">신뢰도 (전체)</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
          <select
            name="trigger"
            defaultValue={trigFilter}
            className="rounded border border-border bg-background px-2 py-1"
          >
            <option value="">트리거 (전체)</option>
            <option value="disclosure">공시</option>
            <option value="earnings">실적</option>
            <option value="contract">계약</option>
            <option value="policy">정책</option>
            <option value="rumor">루머</option>
            <option value="technical">수급</option>
          </select>
          <button type="submit" className="rounded border border-border px-3 py-1 hover:bg-secondary">
            적용
          </button>
          {(q || confFilter || trigFilter) && (
            <Link href="/signals" className="rounded px-3 py-1 text-muted-foreground hover:text-foreground">
              초기화
            </Link>
          )}
        </form>
      </header>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
            <th className="py-2 text-left font-normal">종목</th>
            <th className="py-2 text-left font-normal">D-day</th>
            <th className="py-2 text-right font-normal">등락</th>
            <th className="py-2 text-left font-normal">트리거</th>
            <th className="py-2 text-left font-normal w-full">시그널</th>
          </tr>
        </thead>
        <tbody>
          {list.slice(0, 200).map((s) => (
            <tr key={s.ticker} className="border-b border-border/40 last:border-b-0 align-top">
              <td className="py-2">
                <Link
                  href={`/signals/${s.ticker}`}
                  className="font-medium hover:underline underline-offset-4"
                >
                  {s.name}
                </Link>
                <div className="text-[10px] text-muted-foreground tabular">
                  {s.ticker} · {s.market}
                </div>
              </td>
              <td className="py-2 text-xs text-muted-foreground tabular">
                {formatDate(s.last_seen)}
                <div className="text-[10px]">D+{s.age}</div>
              </td>
              <td className={`py-2 text-right tabular ${priceColorClass(s.change_pct)}`}>
                {formatPct(s.change_pct, { sign: true })}
              </td>
              <td className="py-2 text-xs">
                {TRIGGER_LABELS[s.trigger_type] || s.trigger_type}
                <div className="text-[10px] text-muted-foreground">{s.confidence}</div>
              </td>
              <td className="py-2 text-xs text-foreground/80">
                <span className="line-clamp-2 max-w-md">{s.specific_signal || "—"}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {list.length > 200 && (
        <p className="mt-4 text-xs text-muted-foreground">
          상위 200건만 표시 · 필터로 좁혀보세요
        </p>
      )}
    </main>
  );
}
