/**
 * 밸류체인 전파 예측 엔진 — "오르기 전 후보".
 *
 * 핵심 아이디어 (사용자 인사이트):
 *   한 산업 밸류체인에서 "리더"가 먼저 급등하면,
 *   같은 단계(동반) 또는 인접 단계(전파)의 *아직 안 오른* 종목이 다음 차례.
 *   → 오르기 전에 후보로 제안한다.
 *
 * 입력:
 *   - signals (state/signals.json) : 모멘텀 (최근 변동/신선도/트리거)
 *   - chains  (state/chains.json)  : 산업/체인 단계 분류 (by_ticker)
 *
 * 그룹핑:
 *   chains.json 의 industry_chain 은 gpt-5-mini 가 매우 세분화해서
 *   ("반도체 후공정 패키징" vs "반도체 후공정 검사·계측 장비") 단독 종목이 많다.
 *   coarseIndustry() 로 같은 밸류체인을 중간 단위로 묶는다.
 *   - 매칭되면 coarse 그룹 (반도체 후공정, 로봇, 체외진단 ...)
 *   - 매칭 안 되면 industry_chain 그대로 (정확히 같을 때만 병합 → 과합치기 방지)
 *   - "서비스/기타/테마/수급" 같은 무의미 분류는 종목별 단독 (전파 제외)
 */
import type { Signal, TriggerType } from "./types";
import type { ChainsData, ChainEntry } from "./data";
import { businessDaysAgo } from "./data";
import { formatDate } from "./format";

const STRONG_TRIGGERS = new Set<TriggerType>([
  "disclosure",
  "earnings",
  "contract",
  "policy",
]);

/** 밸류체인 흐름 인덱스 (상류 0 → 하류 5). 지주사/기타 = 흐름 없음 */
const POSITION_FLOW: Record<string, number> = {
  "원소재": 0,
  "장비": 1,
  "부품": 2,
  "조립/제조": 3,
  "최종재": 4,
  "유통/판매": 5,
  "서비스": 5,
};

const LEADER_WINDOW = 7; // 최근 N영업일 이내 급등 = 리더
const LEADER_MIN_PCT = 4; // 최근 변동 +4% 이상 = 급등
const HEAT_THRESHOLD = 22; // 체인 열기 최소치
const CANDIDATE_THRESHOLD = 40; // 후보 점수 최소치

/**
 * 중간 단위(coarse) 산업 그룹 규칙. 순서 중요 — 구체적 규칙을 먼저.
 * 같은 밸류체인을 한 그룹으로 묶어 전파를 계산한다.
 */
const COARSE_RULES: Array<[RegExp, string]> = [
  [/후공정|패키징|본딩|OSAT|반도체.*(테스트|검사|계측)/, "반도체 후공정"],
  [/반도체|웨이퍼|ALD|증착|팹리스|메모리\s*IP|HBM|소부장|소부품/, "반도체 전공정·소재"],
  [/로봇|휴머노이드|감속기|액추에이터|모션/, "로봇"],
  [/진단|POCT|체외진단|검사키트|자가진단|NGS|혈당|유전체.*진단/, "체외진단"],
  [/백신|CDMO|\bCMO\b|위탁생산|항체|바이오의약품|바이오 ?시약|재조합단백/, "백신·CDMO"],
  [/신약|치료제|항암|면역|CAR-?T|유전자치료|알츠하이머|관절염|황반|도네페질/, "바이오 신약"],
  [/건강기능식품|건기식|헬스케어 소매|건강기능/, "건강기능식품"],
  [/전선|케이블|전력|변압기|전기 ?인프라|조명 ?시공/, "전력·전선설비"],
  [/방산|방위|우주|항공|미사일|위성|로켓|MRO/, "방산·우주항공"],
  [/뷰티|화장품|코스메틱/, "K-뷰티·화장품"],
  [/건설|부동산|재개발|재건축|분양|거푸집|폼워크|도시개발|도시재생|터미널/, "부동산·건설"],
  [/콘텐츠|미디어|OTT|영상|엔터|IP 기반/, "콘텐츠·미디어"],
  [/2차전지|배터리|\bBMS\b|양극재|음극재/, "2차전지"],
  [/태양광|폴리실리콘|재생에너지|히트펌프|\bESS\b|LNG|저탄소/, "태양광·에너지"],
  [/특수강|강관|철강|알루미늄|압연/, "철강·금속"],
  [/정밀화학|화학 ?소재|페인트|코팅|염료/, "정밀화학"],
  [/벤처캐피탈|사모펀드|\bVC\b|\bPE\b|지주|투자운용|투자관리|포트폴리오 ?관리/, "VC·지주·투자"],
  [/핀테크|전자결제|결제|자금조달 ?플랫폼/, "핀테크·결제"],
  [/보험|여전|중금리|중·?저신용/, "보험·금융"],
  [/자동차.*(부품|내장|전장|시트)|\bPTC\b|열관리/, "자동차 부품"],
];

const JUNK_INDUSTRY = /^(서비스|기타|장비|부품|최종재|원소재|조립\/제조|유통\/판매|지주사)$|테마|수급|소형주|저유동성|리스크/;

export function coarseIndustry(ind: string): string | null {
  for (const [re, name] of COARSE_RULES) {
    if (re.test(ind)) return name;
  }
  return null;
}

/** 표시용 coarse 그룹명: 매칭되면 coarse, 쓰레기/빈값이면 "기타", 그 외 원시 산업 그대로 */
export function coarseGroup(ind: string): string {
  const c = coarseIndustry(ind);
  if (c) return c;
  if (!ind || JUNK_INDUSTRY.test(ind)) return "기타";
  return ind;
}

/** 전파 그룹 키 + 표시 라벨 */
function groupOf(entry: ChainEntry): { key: string; label: string } {
  const ind = entry.industry_chain || "";
  const c = coarseIndustry(ind);
  if (c) return { key: c, label: c };
  if (JUNK_INDUSTRY.test(ind) || !ind) return { key: `__solo__${entry.ticker}`, label: ind || "기타" };
  return { key: ind, label: ind }; // 정확히 같을 때만 병합
}

export type RelationKind = "same_stage" | "adjacent" | "same_chain";

export const RELATION_LABEL: Record<RelationKind, string> = {
  same_stage: "동일 단계 동반",
  adjacent: "인접 단계 전파",
  same_chain: "같은 체인",
};

export interface ChainLeader {
  ticker: string;
  name: string;
  position: string;
  specificIndustry: string;
  changePct: number | null;
  age: number;
  confidence: string;
  trigger: TriggerType;
  specificSignal: string;
}

export interface ChainCandidate {
  ticker: string;
  name: string;
  industry: string; // coarse 그룹
  specificIndustry: string; // 세부 분류
  position: string;
  role: string;
  score: number;
  relation: RelationKind;
  reason: string;

  ownConfidence: string;
  ownTrigger: TriggerType;
  ownAge: number | null;
  recentChangePct: number | null;
  hasFundamental: boolean;

  leaderTicker: string;
  leaderName: string;
  leaderPosition: string;
  leaderChangePct: number | null;
  leaderDate: string;
}

export interface ChainOpportunity {
  industry: string; // coarse 그룹
  heat: number;
  positionsMoved: string[];
  leaders: ChainLeader[];
  candidates: ChainCandidate[];
}

export interface ChainOppResult {
  opportunities: ChainOpportunity[];
  topCandidates: ChainCandidate[];
  generatedFromDate: string;
}

interface Momentum {
  ticker: string;
  name: string;
  sig: Signal;
  age: number;
  change: number | null;
  confidence: string;
  trigger: TriggerType;
  isLeader: boolean;
}

function latestChange(sig: Signal): number | null {
  const h = sig.history;
  if (!h || !h.length) return null;
  return h[h.length - 1]?.change_pct ?? null;
}

function momentumOf(ticker: string, name: string, sig: Signal): Momentum {
  const age = businessDaysAgo(sig.last_seen);
  const change = latestChange(sig);
  const isLeader =
    age <= LEADER_WINDOW && change !== null && change >= LEADER_MIN_PCT;
  return {
    ticker,
    name,
    sig,
    age,
    change,
    confidence: (sig.confidence || "").toLowerCase(),
    trigger: sig.trigger_type,
    isLeader,
  };
}

function chainHeat(leaders: Momentum[]): number {
  if (!leaders.length) return 0;
  const breadth = Math.min(leaders.length, 4) * 12; // 최대 48
  const minAge = Math.min(...leaders.map((l) => l.age));
  const recency =
    minAge === 0 ? 25 : minAge === 1 ? 20 : minAge <= 3 ? 12 : minAge <= 5 ? 6 : 2;
  const maxChange = Math.max(...leaders.map((l) => l.change ?? 0));
  const strength =
    maxChange >= 15 ? 20 : maxChange >= 8 ? 14 : maxChange >= 4 ? 8 : 3;
  const hasHigh = leaders.some((l) => l.confidence === "high") ? 7 : 0;
  return Math.min(100, breadth + recency + strength + hasHigh);
}

function relation(
  candPos: string,
  leaderPos: string,
): { kind: RelationKind; bonus: number } {
  const ci = POSITION_FLOW[candPos];
  const li = POSITION_FLOW[leaderPos];
  if (ci === undefined || li === undefined) return { kind: "same_chain", bonus: 8 };
  const d = Math.abs(ci - li);
  // 백테스트(토스 캔들): 인접 단계 전파력이 동일 단계와 비슷(인접 +0.2% ≈ 동일 -1.1%),
  // 먼 단계(far)는 -3.5%로 확실히 약함 → 인접 가점을 동일에 가깝게 상향.
  if (d === 0) return { kind: "same_stage", bonus: 26 };
  if (d === 1) return { kind: "adjacent", bonus: 24 };
  return { kind: "same_chain", bonus: 8 };
}

export function buildChainOpportunities(
  signals: Record<string, Signal>,
  chains: ChainsData,
): ChainOppResult {
  // 1) by_ticker → coarse 그룹으로 재편
  const groups = new Map<
    string,
    { label: string; members: Array<{ entry: ChainEntry; mom: Momentum }> }
  >();
  for (const [ticker, entry] of Object.entries(chains.by_ticker || {})) {
    const sig = signals[ticker];
    if (!sig) continue;
    const g = groupOf(entry);
    const mom = momentumOf(ticker, entry.name || sig.name, sig);
    if (!groups.has(g.key)) groups.set(g.key, { label: g.label, members: [] });
    groups.get(g.key)!.members.push({ entry, mom });
  }

  const opportunities: ChainOpportunity[] = [];

  for (const { label, members } of groups.values()) {
    if (members.length < 2) continue; // 2종목 미만이면 전파 의미 없음

    const leaders = members.filter((m) => m.mom.isLeader);
    if (!leaders.length) continue;

    const heat = chainHeat(leaders.map((l) => l.mom));
    if (heat < HEAT_THRESHOLD) continue;

    const positionsMoved = Array.from(
      new Set(leaders.map((l) => l.entry.chain_position).filter(Boolean)),
    );
    const heatBase = heat * 0.4; // 최대 40

    const candidates: ChainCandidate[] = [];
    for (const m of members) {
      if (m.mom.isLeader) continue;
      const candPos = m.entry.chain_position || "기타";

      let best: { kind: RelationKind; bonus: number; leader: Momentum; lpos: string } = {
        kind: "same_chain",
        bonus: 8,
        leader: leaders[0].mom,
        lpos: leaders[0].entry.chain_position || "기타",
      };
      for (const l of leaders) {
        const lpos = l.entry.chain_position || "기타";
        const r = relation(candPos, lpos);
        if (r.bonus > best.bonus) best = { ...r, leader: l.mom, lpos };
      }

      const hasFundamental =
        STRONG_TRIGGERS.has(m.mom.trigger) &&
        (m.mom.confidence === "high" || m.mom.confidence === "medium");
      const change = m.mom.change;
      const isQuiet = change === null || change < LEADER_MIN_PCT;

      const ownSignalBonus = hasFundamental ? (isQuiet ? 20 : 6) : 0;
      const quietBonus =
        change === null ? 6 : change < 0 ? 8 : change < 3 ? 6 : change < 8 ? 0 : -12;
      const la = best.leader.age;
      const freshness = la === 0 ? 10 : la === 1 ? 7 : la <= 3 ? 4 : 0;

      // 백테스트 검증(토스 캔들, 5/1~6/13): 리더 급등 강도가 클수록 동료 전파 alpha↑
      // (리더 ≥12% → D+5 +2.5% vs ≥4% → +1.1%). 강한 트리거에 가점.
      const lchg = best.leader.change ?? 0;
      const leaderStrength = lchg >= 12 ? 12 : lchg >= 8 ? 7 : lchg >= 5 ? 3 : 0;

      const score = Math.max(
        0,
        Math.min(
          100,
          Math.round(heatBase + best.bonus + ownSignalBonus + quietBonus + freshness + leaderStrength),
        ),
      );
      if (score < CANDIDATE_THRESHOLD) continue;

      const leaderDate = best.leader.sig.last_seen;
      const reasonBits: string[] = [
        `${best.leader.name} ${formatDate(leaderDate)} ${
          best.leader.change !== null
            ? (best.leader.change > 0 ? "+" : "") + best.leader.change.toFixed(1) + "%"
            : "급등"
        }`,
        RELATION_LABEL[best.kind],
      ];
      if (isQuiet) reasonBits.push("아직 조용");
      if (hasFundamental && isQuiet) reasonBits.push("본인 호재 보유");

      candidates.push({
        ticker: m.mom.ticker,
        name: m.mom.name,
        industry: label,
        specificIndustry: m.entry.industry_chain || "",
        position: candPos,
        role: m.entry.chain_role || "",
        score,
        relation: best.kind,
        reason: reasonBits.join(" · "),
        ownConfidence: m.mom.confidence,
        ownTrigger: m.mom.trigger,
        ownAge: Number.isFinite(m.mom.age) ? m.mom.age : null,
        recentChangePct: change,
        hasFundamental,
        leaderTicker: best.leader.ticker,
        leaderName: best.leader.name,
        leaderPosition: best.lpos,
        leaderChangePct: best.leader.change,
        leaderDate,
      });
    }
    if (!candidates.length) continue;
    candidates.sort((a, b) => b.score - a.score);

    const leaderList: ChainLeader[] = leaders
      .map((l) => ({
        ticker: l.mom.ticker,
        name: l.mom.name,
        position: l.entry.chain_position || "기타",
        specificIndustry: l.entry.industry_chain || "",
        changePct: l.mom.change,
        age: l.mom.age,
        confidence: l.mom.confidence,
        trigger: l.mom.trigger,
        specificSignal: l.mom.sig.specific_signal || "",
      }))
      .sort((a, b) => (b.changePct ?? 0) - (a.changePct ?? 0));

    opportunities.push({
      industry: label,
      heat,
      positionsMoved,
      leaders: leaderList,
      candidates,
    });
  }

  opportunities.sort((a, b) => b.heat - a.heat);

  const topCandidates = opportunities
    .flatMap((o) => o.candidates)
    .sort((a, b) => b.score - a.score);

  let latest = "";
  for (const o of opportunities) {
    for (const l of o.leaders) {
      const d = signals[l.ticker]?.last_seen || "";
      if (d > latest) latest = d;
    }
  }

  return { opportunities, topCandidates, generatedFromDate: latest };
}
