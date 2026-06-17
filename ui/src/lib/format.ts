/**
 * Number / date formatting helpers — 미니멀 디자인용 token map 포함.
 */

export function formatDate(yyyymmdd: string): string {
  if (!yyyymmdd) return "-";
  if (yyyymmdd.length === 8) {
    return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
  }
  return yyyymmdd;
}

export function formatPrice(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("ko-KR");
}

export function formatPct(n: number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = opts.sign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

/** 한국 주식 색 — 빨강(상승) / 파랑(하락). 미니멀 톤다운. */
export function priceColorClass(n: number | null | undefined): string {
  if (n === null || n === undefined) return "text-muted-foreground";
  if (n > 0) return "text-rose-400";
  if (n < 0) return "text-sky-400";
  return "text-muted-foreground";
}

export const TRIGGER_LABELS: Record<string, string> = {
  disclosure: "공시",
  earnings: "실적",
  contract: "계약",
  policy: "정책",
  rumor: "루머",
  technical: "수급",
  unknown: "불명",
};

export const RECOMMENDATION_LABEL: Record<string, string> = {
  strong_buy: "강력 매수",
  buy: "매수",
  watch: "관찰",
  wait: "대기",
  avoid: "회피",
};

/** 미니멀 — 단일 accent 색 (cyan) + 강도만 */
export const RECOMMENDATION_TONE: Record<string, string> = {
  strong_buy: "text-foreground",
  buy: "text-foreground",
  watch: "text-muted-foreground",
  wait: "text-muted-foreground",
  avoid: "text-muted-foreground/70",
};
