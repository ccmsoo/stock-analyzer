import Link from "next/link";
import { loadRadar, loadTrackRecord } from "@/lib/data";
import type { RadarCandidate, TrackRecord } from "@/lib/data";
import { formatPrice, formatPct, formatDate, priceColorClass } from "@/lib/format";

export const revalidate = 0;
export const dynamic = "force-dynamic";

export default async function RadarPage() {
  const [radar, track] = await Promise.all([loadRadar(), loadTrackRecord()]);
  const cands = radar.candidates || [];
  const has = cands.length > 0;

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            오르기 전 레이더
            <span className="ml-2 text-xs text-muted-foreground">촉매 선진입 · 단기 스윙</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {radar.date ? formatDate(radar.date) : "—"}
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          매일 장전 자동 스캔(검증: 촉매≥6 → 급등 정밀도 ~70%). <b className="text-foreground">3일 보유 · 넓은 손절(−10%) · 추격 금지.</b>{" "}
          텔레그램으로도 발송됩니다.
        </p>
      </header>

      {!has ? (
        <p className="mt-10 text-center text-sm text-muted-foreground">
          아직 후보가 없습니다. (장전 cron 실행 전이거나 오늘 강한 촉매 없음)
        </p>
      ) : (
        <>
          {/* 촉매 선진입 후보 */}
          <section className="mt-6">
            <h2 className="mb-2 text-sm font-medium uppercase tracking-wider text-muted-foreground">
              촉매 후보 · 아직 안 오름
              <span className="ml-2 text-xs normal-case text-muted-foreground/70">{cands.length}</span>
            </h2>
            {cands.length === 0 ? (
              <p className="text-sm text-muted-foreground">오늘 강한 촉매 후보 없음.</p>
            ) : (
              <div className="space-y-2">
                {cands.slice(0, 20).map((c, i) => (
                  <CatalystRow key={c.ticker} c={c} rank={i + 1} track={track} />
                ))}
              </div>
            )}
          </section>

        </>
      )}

      <p className="mt-12 border-t border-border pt-4 text-[11px] leading-relaxed text-muted-foreground">
        매수 추천 아님. 백테스트는 균형표본 기준이라 실전 정밀도는 더 낮을 수 있음. 분할 매수 + 손절 사전 설정 권장.
      </p>
    </main>
  );
}

function CatalystRow({ c, rank, track }: { c: RadarCandidate; rank: number; track: TrackRecord | null }) {
  const t = c.cat ? track?.types?.[c.cat] : undefined;
  const d7 = t?.d7?.alpha;
  return (
    <Link
      href={`/signals/${c.ticker}`}
      className="block rounded-md border border-border p-3 transition-colors hover:border-foreground/30"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-baseline gap-2 truncate">
          <span className="w-5 shrink-0 text-xs text-muted-foreground tabular">{rank}</span>
          <span className="truncate font-medium">{c.name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground tabular">{c.ticker}</span>
          {c.cat && (
            <span className="shrink-0 rounded border border-border px-1 text-[10px] text-muted-foreground">
              {c.cat}
              {c.wording === "확정형" && <span className="text-emerald-400"> 확정</span>}
            </span>
          )}
          {c.stale_catalyst && (
            <span className="shrink-0 rounded border border-red-400/40 px-1 text-[10px] text-red-400">급등후</span>
          )}
          {!c.chart_ok && !c.stale_catalyst && (
            <span className="shrink-0 rounded border border-amber-400/40 px-1 text-[10px] text-amber-400">과열</span>
          )}
        </span>
        <span className="shrink-0 text-sm font-medium tabular text-emerald-400">촉매 {c.score}</span>
      </div>
      <div className="mt-1 truncate pl-7 text-[11px] text-muted-foreground">
        {c.keyword} · {c.reason}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 pl-7 text-[11px] tabular">
        {c.today != null && (
          <span>오늘 <span className={priceColorClass(c.today)}>{formatPct(c.today, { sign: true })}</span></span>
        )}
        {c.chg5 != null && <span className="text-muted-foreground">5일 {formatPct(c.chg5, { sign: true })}</span>}
        {c.from_high != null && <span className="text-muted-foreground">고점 {formatPct(c.from_high, { sign: true })}</span>}
        {c.price != null && <span className="text-muted-foreground">{formatPrice(c.price)}원</span>}
        {d7 && (
          <span className="text-muted-foreground/80">
            이 유형 과거 {t!.n_events}건 · D+7 알파{" "}
            <span className={d7.avg > 0 ? "text-emerald-400" : "text-red-400"}>
              {d7.avg > 0 ? "+" : ""}{d7.avg.toFixed(1)}%/{d7.win}%
            </span>
          </span>
        )}
      </div>
    </Link>
  );
}
