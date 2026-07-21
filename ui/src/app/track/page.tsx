import { loadTrackRecord } from "@/lib/data";
import type { TRStat } from "@/lib/data";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 0;
export const dynamic = "force-dynamic";

const HOLD_LABEL: Record<string, string> = { "1": "1일", "3": "3일", "5": "5일", "7": "7일" };

export default async function TrackPage() {
  const tr = await loadTrackRecord();

  if (!tr) {
    return (
      <main className="container max-w-4xl py-8">
        <h1 className="text-base font-medium">성적표</h1>
        <p className="mt-6 text-sm text-muted-foreground">
          아직 성적표 데이터가 없습니다. (cron이 track_record.json 을 생성하면 표시됩니다)
        </p>
      </main>
    );
  }

  const typeRows = Object.entries(tr.types)
    .filter(([, v]) => (v.d3?.alpha?.n ?? 0) >= 8)
    .sort((a, b) => (b[1].d7?.alpha?.avg ?? -99) - (a[1].d7?.alpha?.avg ?? -99));

  return (
    <main className="container max-w-4xl py-8">
      <header className="border-b border-border pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-medium">
            성적표
            <span className="ml-2 text-xs text-muted-foreground">포워드 페이퍼트레이드 · 과적합 불가 검증</span>
          </h1>
          <span className="text-xs text-muted-foreground tabular">
            {tr.since ? `${formatDate(tr.since)} 이후` : ""} · 픽 {tr.n_picks} · 촉매이벤트 {tr.n_events}
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          레이더가 매일 뱉은 픽을 기계적으로 채점(다음날 시초 진입 · 손절 −10%).{" "}
          <b className="text-foreground">절대 = 실제 롱온리 수익 · 알파 = 같은 기간 지수 대비</b> — 알파가
          선별력, 절대가 실전입니다.
        </p>
      </header>

      {/* 보유기간별 */}
      <Section title="보유기간별" sub="알파는 보유일과 함께 커지고, 절대는 장세를 따라갑니다">
        <table className="w-full text-xs tabular">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-1.5 text-left font-normal">보유</th>
              <th className="text-right font-normal">픽 절대</th>
              <th className="text-right font-normal">픽 알파</th>
              <th className="text-right font-normal">이벤트(첫날진입) 알파</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(HOLD_LABEL).map((h) => (
              <tr key={h} className="border-b border-border/50">
                <td className="py-1.5">{HOLD_LABEL[h]}</td>
                <td className="text-right"><Stat s={tr.overall[h]?.abs} /></td>
                <td className="text-right"><Stat s={tr.overall[h]?.alpha} /></td>
                <td className="text-right"><Stat s={tr.events_overall[h]?.alpha} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* 주별 알파 */}
      <Section title="주별 흐름" sub="3일 보유 기준 — 알파가 주는 추세면 선별력이 식는 중">
        <table className="w-full text-xs tabular">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-1.5 text-left font-normal">주</th>
              <th className="text-right font-normal">n</th>
              <th className="text-right font-normal">절대</th>
              <th className="text-right font-normal">알파</th>
            </tr>
          </thead>
          <tbody>
            {tr.weekly.map((w) => (
              <tr key={w.week} className="border-b border-border/50">
                <td className="py-1.5">{w.week}</td>
                <td className="text-right text-muted-foreground">{w.abs?.n ?? 0}</td>
                <td className="text-right"><Stat s={w.abs} /></td>
                <td className="text-right"><Stat s={w.alpha} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* 촉매 유형별 */}
      <Section title="촉매 유형별 실적" sub="이벤트 단위(같은 촉매 첫 등장일 진입) — 레이더 카드의 배지와 연결">
        <table className="w-full text-xs tabular">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="py-1.5 text-left font-normal">유형</th>
              <th className="text-right font-normal">이벤트</th>
              <th className="text-right font-normal">D+3 알파</th>
              <th className="text-right font-normal">D+7 알파</th>
            </tr>
          </thead>
          <tbody>
            {typeRows.map(([cat, v]) => (
              <tr key={cat} className="border-b border-border/50">
                <td className="py-1.5">{cat}</td>
                <td className="text-right text-muted-foreground">{v.n_events}</td>
                <td className="text-right"><Stat s={v.d3?.alpha} /></td>
                <td className="text-right"><Stat s={v.d7?.alpha} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* 시나리오 */}
      <Section title="시나리오 생존판" sub="3일 보유 · 매일 재계산 — 표본(n) 작은 칸은 참고만">
        <div className="space-y-2">
          {tr.scenarios.map((s) => (
            <div key={s.name} className="rounded-md border border-border p-3">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm">{s.name}</span>
                <span className="shrink-0 text-xs tabular">
                  알파 <Stat s={s.alpha} />
                </span>
              </div>
              <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[11px] text-muted-foreground">
                <span>{s.desc}</span>
                <span className="shrink-0 tabular">절대 <Stat s={s.abs} muted /></span>
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* 문구 */}
      <Section title="기사 문구별 (이벤트 D+3 알파)" sub="확정형(체결·완료·승인) vs 기대형(추진·검토)">
        <div className="flex flex-wrap gap-4 text-xs tabular">
          {Object.entries(tr.wording).map(([w, p]) => (
            <span key={w}>
              {w} <Stat s={p.alpha} />
            </span>
          ))}
        </div>
      </Section>

      <p className="mt-12 border-t border-border pt-4 text-[11px] leading-relaxed text-muted-foreground">
        {tr.generated_at && <>갱신 {tr.generated_at.replace("T", " ")} · </>}
        표본 4주·단일(하락) 장세 기준 — 시나리오 등수는 표본이 수백 건 쌓일 때까지 확정 규칙이 아닙니다.
        매수 추천 아님.
      </p>
    </main>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="text-sm font-medium uppercase tracking-wider text-muted-foreground">{title}</h2>
      {sub && <p className="mb-2 mt-0.5 text-[11px] text-muted-foreground/70">{sub}</p>}
      {children}
    </section>
  );
}

function Stat({ s, muted }: { s: TRStat | null | undefined; muted?: boolean }) {
  if (!s) return <span className="text-muted-foreground">—</span>;
  const pos = s.avg > 0;
  return (
    <span className={cn(muted ? "text-muted-foreground" : pos ? "text-emerald-400" : "text-red-400")}>
      {s.avg > 0 ? "+" : ""}
      {s.avg.toFixed(1)}%<span className="text-muted-foreground">/{s.win}%</span>
      <span className="ml-1 text-[10px] text-muted-foreground/70">n{s.n}</span>
    </span>
  );
}
