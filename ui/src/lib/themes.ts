/**
 * 테마 클러스터링 — 시그널을 뉴스 내러티브(테마) 단위로 묶는다.
 *
 * main_theme(AI 추출)는 혼합 단위 + "수급/*"(테마 아님)가 섞임.
 * coarseTheme()로 정규화: 진짜 테마는 묶고, 수급/지수성은 "테마 없음(단독)"으로.
 *
 * 밸류체인(/chains, 산업·단계 구조)과 다름 — 여긴 "지금 뜨거운 내러티브".
 */
import type { Signal, TriggerType } from "./types";
import { businessDaysAgo } from "./data";

const THEME_RULES: Array<[RegExp, string]> = [
  [/후공정|패키징|HBM|소부장|소부품|웨이퍼|파운드리|D램|낸드|반도체|팹리스/, "반도체"],
  [/로봇|휴머노이드|감속기|액추에이터|피지컬\s*AI|자동화/, "로봇·AI"],
  [/바이오|제약|신약|임상|항암|면역|치료제|진단|백신|CDMO|FDA|항체|세포/, "바이오·제약"],
  [/2차전지|배터리|양극재|음극재|전해질|리튬/, "2차전지"],
  [/방산|방위|우주|항공|미사일|위성|로켓|MRO/, "방산·우주항공"],
  [/전력|전선|변압기|원전|SMR|태양광|ESS|수소|에너지/, "전력·에너지"],
  [/뷰티|화장품|코스메틱|ODM/, "K-뷰티"],
  [/조선|해운|선박|기자재/, "조선·해운"],
  [/콘텐츠|엔터|미디어|OTT|영상|게임|드라마/, "콘텐츠·엔터"],
  [/부동산|건설|재개발|재건축|분양|시니어주택|플랜트/, "부동산·건설"],
  [/정책|지정학|중동|이란|재건|관세|국책|과제/, "정책·지정학"],
  [/철강|강관|특수강|금속|알루미늄|구리/, "철강·금속"],
  [/자동차|전장|타이어|모빌리티/, "자동차"],
  [/핀테크|결제|은행|보험|증권|카드|금융/, "금융"],
];
const NOISE = /수급|리밸런싱|주주환원|기업행동|ETF|지수|외국인|기관|공모|저유동|변동성/;

export function coarseTheme(mainTheme: string, specific?: string): string | null {
  const txt = `${mainTheme || ""} ${specific || ""}`;
  for (const [re, name] of THEME_RULES) if (re.test(txt)) return name;
  if (!mainTheme || NOISE.test(mainTheme)) return null; // 수급/지수성 = 테마 없음
  return mainTheme.trim() || null;
}

export interface ThemeStock {
  ticker: string;
  name: string;
  theme: string;
  specificTheme: string;
  change: number | null;
  age: number;
  confidence: string;
  trigger: TriggerType;
  specific: string;
}

export interface ThemeGroup {
  theme: string;
  heat: number;
  recentStrong: number; // 최근(≤3일) 강세 수
  stocks: ThemeStock[];
}

export interface ThemesResult {
  hot: ThemeGroup[];
  standalone: ThemeStock[];
  total: number;
}

function latestChange(s: Signal): number | null {
  const h = s.history;
  return h && h.length ? h[h.length - 1]?.change_pct ?? null : null;
}

export function buildThemes(signals: Record<string, Signal>, maxAge = 10): ThemesResult {
  const byTheme = new Map<string, ThemeStock[]>();
  const standalone: ThemeStock[] = [];
  let total = 0;

  for (const [t, s] of Object.entries(signals)) {
    const age = businessDaysAgo(s.last_seen);
    if (age > maxAge) continue;
    if (s.confidence === "low" || s.trigger_type === "unknown") continue;
    total++;
    const stock: ThemeStock = {
      ticker: t,
      name: s.name,
      theme: "",
      specificTheme: s.main_theme || "",
      change: latestChange(s),
      age,
      confidence: (s.confidence || "").toLowerCase(),
      trigger: s.trigger_type,
      specific: s.specific_signal || "",
    };
    const th = coarseTheme(s.main_theme, s.specific_signal);
    if (!th) {
      standalone.push(stock);
      continue;
    }
    stock.theme = th;
    if (!byTheme.has(th)) byTheme.set(th, []);
    byTheme.get(th)!.push(stock);
  }

  const hot: ThemeGroup[] = [];
  for (const [theme, stocks] of byTheme) {
    if (stocks.length < 2) {
      standalone.push(...stocks); // 단독 테마는 standalone 로
      continue;
    }
    const minAge = Math.min(...stocks.map((s) => s.age));
    const maxChg = Math.max(...stocks.map((s) => s.change ?? 0));
    const recentStrong = stocks.filter((s) => s.age <= 3 && (s.change ?? 0) >= 4).length;
    const heat = Math.min(
      100,
      Math.min(stocks.length, 6) * 9 +
        (minAge === 0 ? 25 : minAge <= 1 ? 18 : minAge <= 3 ? 10 : 3) +
        (maxChg >= 15 ? 20 : maxChg >= 8 ? 12 : 5),
    );
    stocks.sort((a, b) => (b.change ?? 0) - (a.change ?? 0));
    hot.push({ theme, heat, recentStrong, stocks });
  }
  hot.sort((a, b) => b.heat - a.heat);
  standalone.sort((a, b) => (b.change ?? 0) - (a.change ?? 0));
  return { hot, standalone, total };
}
