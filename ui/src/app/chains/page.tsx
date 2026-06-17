import Link from "next/link";
import { loadChains, loadState, businessDaysAgo } from "@/lib/data";
import { coarseGroup } from "@/lib/chains-predict";
import { formatPct, priceColorClass } from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

const POSITION_ORDER = [
  "원소재",
  "장비",
  "부품",
  "조립/제조",
  "최종재",
  "유통/판매",
  "서비스",
  "지주사",
  "기타",
];

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

export default async function ChainsPage({
  searchParams,
}: {
  searchParams: Promise<{ industry?: string; q?: string }>;
}) {
  const params = await searchParams;
  const chains = await loadChains();
  const { signals } = await loadState();

  const filterIndustry = params.industry || "";
  const q = (params.q || "").toLowerCase();

  // by_ticker → coarse 그룹핑 (엔진과 동일). 원시 industry_chain 은 너무 세분화돼서
  // "반도체 후공정 패키징" + "검사·계측 장비" 등을 "반도체 후공정" 으로 묶는다.
  const coarseByIndustry: Record<string, Record<string, PositionStock[]>> = {};
  for (const [ticker, e] of Object.entries(chains.by_ticker)) {
    const g = coarseGroup(e.industry_chain || "");
    const pos = e.chain_position || "기타";
    (coarseByIndustry[g] ??= {})[pos] ??= [];
    coarseByIndustry[g][pos].push({ ticker, name: e.name, role: e.chain_role || "" });
  }
  const sizeOf = (p: Record<string, PositionStock[]>) =>
    Object.values(p).reduce((s, x) => s + x.length, 0);

  const allGroups = Object.entries(coarseByIndustry).sort(
    ([, a], [, b]) => sizeOf(b) - sizeOf(a),
  );
  // 2종목 이상만 "체인"으로 표시 (단독은 전파 의미 없음)
  const industries = allGroups.filter(([, p]) => sizeOf(p) >= 2);
  const singletonCount = allGroups.length - industries.length;

  const filtered = filterIndustry
    ? industries.filter(([name]) => name === filterIndustry)
    : industries;

  return (
    <main className="container max-w-5xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            밸류체인 맵 <span className="ml-2 text-xs text-muted-foreground">"오늘 누가 오르면 내일 누가 오르나"</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {chains.total}종목 · {industries.length}체인
            {singletonCount > 0 && ` · 단독 ${singletonCount}`}
          </span>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          시그널 종목을 산업/체인 단계로 분류 (세부 분류를 테마 단위로 묶음). 한 종목 시그널 → 같은 체인 같은 단계 종목 동반 강세 가능성.
        </p>

        {/* 산업 필터 칩 */}
        <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
          <Chip href="/chains" active={!filterIndustry}>
            전체
          </Chip>
          {industries.slice(0, 15).map(([name, positions]) => {
            const total = Object.values(positions).reduce((s, x) => s + x.length, 0);
            return (
              <Chip
                key={name}
                href={`/chains?industry=${encodeURIComponent(name)}`}
                active={filterIndustry === name}
              >
                {name} ({total})
              </Chip>
            );
          })}
        </div>
      </header>

      <div className="mt-2 space-y-6">
        {filtered.length === 0 && (
          <p className="mt-8 text-center text-sm text-muted-foreground">
            체인 맵이 비어있습니다. <code className="text-xs">python -m tools.build_value_chains</code>
          </p>
        )}
        {filtered.map(([industry, positions]) => (
          <IndustryBlock
            key={industry}
            industry={industry}
            positions={positions}
            signals={signals}
            q={q}
          />
        ))}
      </div>
    </main>
  );
}

interface PositionStock {
  ticker: string;
  name: string;
  role: string;
}

function IndustryBlock({
  industry,
  positions,
  signals,
  q,
}: {
  industry: string;
  positions: Record<string, PositionStock[]>;
  signals: Record<string, import("@/lib/types").Signal>;
  q: string;
}) {
  const totalStocks = Object.values(positions).reduce((s, x) => s + x.length, 0);
  if (q) {
    const matches = Object.values(positions).some((arr) =>
      arr.some((s) => s.name.toLowerCase().includes(q) || s.ticker.includes(q)),
    );
    if (!matches) return null;
  }

  // 최근 강세 종목 수 (D+5 이내 시그널)
  const recentStrong = Object.values(positions)
    .flat()
    .filter((s) => {
      const sig = signals[s.ticker];
      if (!sig) return false;
      const age = businessDaysAgo(sig.last_seen);
      return age <= 5 && sig.confidence !== "low";
    }).length;

  return (
    <section className="border border-border rounded-md p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-base font-medium">
          {industry}
          <span className="ml-2 text-xs text-muted-foreground tabular">{totalStocks}종목</span>
        </h2>
        {recentStrong > 0 && (
          <span className="text-xs text-emerald-400">
            최근 강세 {recentStrong}건
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {POSITION_ORDER.filter((p) => positions[p]?.length).map((pos) => {
          const stocks = positions[pos];
          const color = POSITION_COLOR[pos] || POSITION_COLOR["기타"];
          return (
            <div key={pos}>
              <div className={`text-xs uppercase tracking-wider px-2 py-0.5 inline-block rounded border ${color} mb-1.5`}>
                {pos}
              </div>
              <ul className="space-y-1">
                {stocks.map((s) => {
                  const sig = signals[s.ticker];
                  const age = sig ? businessDaysAgo(sig.last_seen) : null;
                  const lastChange = sig?.history?.[sig.history.length - 1]?.change_pct ?? null;
                  return (
                    <li key={s.ticker} className="text-sm flex items-baseline justify-between gap-2">
                      <Link
                        href={`/signals/${s.ticker}`}
                        className="truncate hover:underline underline-offset-4"
                      >
                        {s.name}
                      </Link>
                      <div className="flex items-baseline gap-2 shrink-0 text-xs">
                        {age !== null && age <= 5 && (
                          <span className="text-emerald-400 tabular">D+{age}</span>
                        )}
                        {lastChange !== null && (
                          <span className={`tabular ${priceColorClass(lastChange)}`}>
                            {formatPct(lastChange, { sign: true })}
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Chip({
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
