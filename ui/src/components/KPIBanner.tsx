import type { Cumulative } from "@/lib/types";
import { formatDate } from "@/lib/format";

interface Props {
  cumulative: Cumulative;
  generatedAt: string;
}

export function KPIBanner({ cumulative, generatedAt }: Props) {
  const { total_signals, unique_watch_kw, by_confidence, covered_dates, last_update } =
    cumulative;
  const high = by_confidence.high || 0;
  const med = by_confidence.medium || 0;
  const low = by_confidence.low || 0;

  const dateSpan =
    covered_dates.length > 0
      ? `${formatDate(covered_dates[0])} → ${formatDate(covered_dates[covered_dates.length - 1])}`
      : "—";

  const syncStr = generatedAt
    ? new Date(generatedAt).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

  return (
    <header className="border-b border-border">
      <div className="container py-5">
        {/* 타이틀 + 메타 */}
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <div className="flex items-baseline gap-3">
            <h1 className="text-base font-medium tracking-tight">Stock Analyzer</h1>
            <span className="text-xs text-muted-foreground">한국 주식 시그널 + 진입 가이드</span>
          </div>
          <div className="flex items-baseline gap-3 text-xs text-muted-foreground tabular">
            <span>{formatDate(last_update)}</span>
            <span className="text-muted-foreground/40">·</span>
            <span>{syncStr}</span>
          </div>
        </div>

        {/* KPI — 단일 라인, 박스 없음 */}
        <div className="mt-4 flex flex-wrap items-baseline gap-x-8 gap-y-2 text-sm">
          <Kpi label="시그널" value={total_signals.toLocaleString()} unit="종목" />
          <Kpi label="키워드" value={unique_watch_kw.toLocaleString()} unit="개" />
          <Kpi
            label="신뢰도"
            value={
              <>
                <span className="text-foreground">{high}</span>
                <span className="mx-1 text-muted-foreground/40">/</span>
                <span className="text-muted-foreground">{med}</span>
                <span className="mx-1 text-muted-foreground/40">/</span>
                <span className="text-muted-foreground/60">{low}</span>
              </>
            }
            unit="H · M · L"
          />
          <Kpi label="기간" value={dateSpan} unit={`${covered_dates.length}일`} />
        </div>
      </div>
    </header>
  );
}

function Kpi({
  label,
  value,
  unit,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="font-medium tabular">{value}</span>
      {unit && <span className="text-xs text-muted-foreground">{unit}</span>}
    </div>
  );
}
