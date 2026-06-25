import Link from "next/link";
import { loadState } from "@/lib/data";
import { buildThemes } from "@/lib/themes";
import type { ThemeGroup } from "@/lib/themes";
import { formatPct, priceColorClass, TRIGGER_LABELS } from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

export default async function ThemesPage() {
  const { signals } = await loadState();
  const { hot, standalone, total } = buildThemes(signals);

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            테마 정리
            <span className="ml-2 text-xs text-muted-foreground">지금 묶이는 뉴스 테마</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {hot.length}테마 · {total}종목
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          최근(≤10영업일) 시그널을 <b className="text-foreground">뉴스 테마</b>로 묶음. 여러 종목이 한 테마에 모이면
          그 테마가 살아있다는 신호. 수급·지수성처럼 테마가 없는 건 아래 <span className="text-muted-foreground">단독</span>으로.
        </p>
      </header>

      {hot.length === 0 ? (
        <p className="mt-10 text-center text-sm text-muted-foreground">묶이는 테마가 없습니다.</p>
      ) : (
        <section className="mt-6 space-y-4">
          {hot.map((g) => (
            <ThemeCard key={g.theme} g={g} />
          ))}
        </section>
      )}

      {standalone.length > 0 && (
        <section className="mt-10">
          <h2 className="mb-2 text-sm font-medium uppercase tracking-wider text-muted-foreground">
            테마 없음 · 단독 <span className="ml-1 normal-case text-[11px] text-muted-foreground/70">(수급·개별 이슈)</span>
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {standalone.slice(0, 40).map((s) => (
              <Link
                key={s.ticker}
                href={`/signals/${s.ticker}`}
                className="flex items-baseline gap-1.5 rounded border border-border px-2 py-1 text-xs transition-colors hover:border-foreground/30"
              >
                <span>{s.name}</span>
                {s.change !== null && (
                  <span className={`tabular ${priceColorClass(s.change)}`}>
                    {formatPct(s.change, { sign: true })}
                  </span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function ThemeCard({ g }: { g: ThemeGroup }) {
  return (
    <section className="rounded-md border border-border p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-medium">
          {g.theme}
          <span className="ml-2 text-xs text-muted-foreground tabular">{g.stocks.length}종목</span>
        </h3>
        <div className="flex items-center gap-2">
          {g.recentStrong > 0 && (
            <span className="text-[11px] text-emerald-400">최근 강세 {g.recentStrong}</span>
          )}
          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
            <div className="h-full bg-rose-400/70" style={{ width: `${g.heat}%` }} />
          </div>
          <span className="text-[11px] text-rose-400 tabular">열기 {g.heat}</span>
        </div>
      </div>

      <ul className="mt-2 space-y-1">
        {g.stocks.slice(0, 8).map((s) => (
          <li key={s.ticker}>
            <Link
              href={`/signals/${s.ticker}`}
              className="flex items-baseline justify-between gap-2 text-sm hover:underline underline-offset-4"
            >
              <span className="flex items-baseline gap-2 truncate">
                <span className="truncate">{s.name}</span>
                <span className="shrink-0 rounded border border-border px-1 text-[10px] text-muted-foreground">
                  {TRIGGER_LABELS[s.trigger] || s.trigger}
                </span>
                {s.age <= 3 && (
                  <span className="shrink-0 text-[10px] text-emerald-400 tabular">D+{s.age}</span>
                )}
              </span>
              {s.change !== null && (
                <span className={`shrink-0 tabular text-sm ${priceColorClass(s.change)}`}>
                  {formatPct(s.change, { sign: true })}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
