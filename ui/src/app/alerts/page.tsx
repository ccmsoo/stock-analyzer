import Link from "next/link";
import path from "path";
import fs from "fs";
import { formatDate, priceColorClass, formatPct, formatPrice } from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

interface Alert {
  ticker: string;
  name: string;
  sosok: string;
  report_name: string;
  submitter: string;
  submitted_at: string;
  time: string;
  dart_url: string;
  rcp_no: string;
  category: string;
  score: number;
  current_price: number | null;
  today_change_pct: number | null;
  signal_last_seen: string;
  signal_confidence: string;
  signal_main_theme: string;
  matched_at: string;
}

const CATEGORY_COLOR: Record<string, string> = {
  "수주/계약": "text-emerald-400 border-emerald-400/30",
  "M&A/경영권": "text-fuchsia-400 border-fuchsia-400/30",
  "실적": "text-cyan-400 border-cyan-400/30",
  "자본정책_호재": "text-blue-400 border-blue-400/30",
  "투자/시설": "text-amber-400 border-amber-400/30",
  "신약/임상": "text-rose-400 border-rose-400/30",
};

function loadAlerts(): { alerts: Alert[]; generated_at: string; new_today: number } {
  const p = path.resolve(process.cwd(), "..", "reports", "alerts.json");
  try {
    if (!fs.existsSync(p)) return { alerts: [], generated_at: "", new_today: 0 };
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    return {
      alerts: data.alerts || [],
      generated_at: data.generated_at || "",
      new_today: data.new_today || 0,
    };
  } catch {
    return { alerts: [], generated_at: "", new_today: 0 };
  }
}

export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const params = await searchParams;
  const { alerts, generated_at, new_today } = loadAlerts();
  const catFilter = params.category || "";

  let list = alerts;
  if (catFilter) list = list.filter((a) => a.category === catFilter);

  // 카테고리별 개수
  const catCounts: Record<string, number> = {};
  for (const a of alerts) {
    catCounts[a.category] = (catCounts[a.category] || 0) + 1;
  }

  const gen = generated_at
    ? new Date(generated_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
    : "—";

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            DART 공시 알림 <span className="ml-2 text-xs text-muted-foreground">"오르기 전 시그널"</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {alerts.length}건 · 마지막 동기화 {gen}
          </span>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          우리 시그널 종목 중 최근 DART 공시에서 호재(수주·실적·M&A 등) 발생 — 가격이 아직 안 움직였으면 진입 후보.
        </p>

        {/* 카테고리 필터 */}
        <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
          <FilterChip href="/alerts" active={!catFilter}>
            전체 ({alerts.length})
          </FilterChip>
          {Object.entries(catCounts).map(([cat, n]) => (
            <FilterChip
              key={cat}
              href={`/alerts?category=${encodeURIComponent(cat)}`}
              active={catFilter === cat}
            >
              {cat} ({n})
            </FilterChip>
          ))}
        </div>
      </header>

      <div className="mt-2">
        {list.length === 0 && (
          <p className="mt-8 text-center text-sm text-muted-foreground">
            매칭된 공시 없음. <code className="text-xs">python -m tools.dart_monitor</code> 실행 권장.
          </p>
        )}
        {list.map((a) => (
          <AlertRow key={`${a.ticker}-${a.rcp_no}`} alert={a} />
        ))}
      </div>
    </main>
  );
}

function AlertRow({ alert: a }: { alert: Alert }) {
  const catColor = CATEGORY_COLOR[a.category] || "text-muted-foreground border-border";
  return (
    <article className="border-b border-border py-4 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2 min-w-0">
          <Link
            href={`/signals/${a.ticker}`}
            className="font-medium hover:underline underline-offset-4"
          >
            {a.name}
          </Link>
          <span className="text-xs text-muted-foreground tabular">{a.ticker}</span>
          {a.sosok && <span className="text-[10px] text-muted-foreground">{a.sosok}</span>}
        </div>
        <div className="flex items-baseline gap-3 shrink-0">
          <span className="tabular text-sm">{formatPrice(a.current_price)}</span>
          <span className={`tabular text-xs font-medium ${priceColorClass(a.today_change_pct)}`}>
            {formatPct(a.today_change_pct, { sign: true })}
          </span>
        </div>
      </div>

      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
        <span className={`rounded border px-1.5 py-0.5 ${catColor}`}>{a.category}</span>
        <span className="text-foreground/85">{a.report_name}</span>
      </div>

      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 text-[11px] text-muted-foreground">
        <span className="tabular">{a.submitted_at} {a.time}</span>
        <span>·</span>
        <span>제출 {a.submitter}</span>
        {a.signal_main_theme && (
          <>
            <span>·</span>
            <span>시그널 테마: {a.signal_main_theme}</span>
          </>
        )}
        {a.dart_url && (
          <>
            <span>·</span>
            <a
              href={a.dart_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-foreground/70 underline-offset-4 hover:underline"
            >
              공시 원문 →
            </a>
          </>
        )}
      </div>
    </article>
  );
}

function FilterChip({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`rounded border px-2 py-0.5 transition-colors ${
        active
          ? "border-foreground bg-foreground text-background"
          : "border-border text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </Link>
  );
}
