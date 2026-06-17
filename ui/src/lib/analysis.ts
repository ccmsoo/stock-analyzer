/**
 * Buy candidate scoring & entry guide derivation.
 *
 * Input: signal (from state) + current price snapshot.
 * Output: BuyCandidate with recommendation + entry/stop/take prices.
 *
 * 추천 로직:
 * - 펀더멘털 trigger (disclosure/earnings/contract/policy) + high/medium confidence 가 기본
 * - signal_age_days (D-day 이후) 가 너무 오래면 신선도 ↓
 * - 누적 변동 (accum from D-day) 가 너무 크면 과열, 너무 마이너스면 추세 무너짐
 * - 오늘 시초比 + 가 매수세 살아있는 강한 신호
 */
import type { BuyCandidate, Confidence, Signal, TriggerType } from "./types";
import { businessDaysAgo } from "./data";

const STRONG_TRIGGERS = new Set<TriggerType>([
  "disclosure",
  "earnings",
  "contract",
  "policy",
]);

interface PriceLike {
  current_price: number | null;
  d_day_close: number | null;
  today_open: number | null;
  today_change_pct: number | null;
  today_from_open_pct: number | null;
}

export function scoreSignal(
  sig: Signal,
  price: PriceLike,
): { score: number; recommendation: BuyCandidate["recommendation"]; rationale: string } {
  let score = 50; // base
  const reasons: string[] = [];

  // 1) 신뢰도
  if (sig.confidence === "high") {
    score += 15;
    reasons.push("high confidence");
  } else if (sig.confidence === "medium") {
    score += 8;
  } else if (sig.confidence === "low") {
    score -= 20;
    reasons.push("low confidence");
  }

  // 2) 트리거 종류
  if (STRONG_TRIGGERS.has(sig.trigger_type)) {
    score += 12;
    reasons.push(`${sig.trigger_type} 펀더멘털`);
  } else if (sig.trigger_type === "rumor" || sig.trigger_type === "technical") {
    score -= 5;
    reasons.push("단기 수급/루머");
  } else if (sig.trigger_type === "unknown") {
    score -= 15;
  }

  // 3) 신선도 — 시그널 후 며칠 지났나
  const age = businessDaysAgo(sig.last_seen);
  if (age === 0) {
    score += 10;
    reasons.push("오늘 시그널 (D+0)");
  } else if (age === 1) {
    score += 8;
    reasons.push("어제 시그널 (D+1, 진입 적기)");
  } else if (age <= 3) {
    score += 5;
  } else if (age <= 5) {
    score += 0;
  } else if (age <= 10) {
    score -= 5;
    reasons.push(`시그널 후 ${age}일 (신선도 ↓)`);
  } else {
    score -= 15;
    reasons.push(`시그널 후 ${age}일 (오래됨)`);
  }

  // 4) 누적 변동 (D-day 종가 대비)
  const accum = computeAccum(sig, price);
  if (accum !== null) {
    if (accum < -10) {
      score -= 15;
      reasons.push(`D-day 후 ${accum.toFixed(1)}% (추세 무너짐)`);
    } else if (accum < -5) {
      score -= 5;
    } else if (accum < 5) {
      score += 10;
      reasons.push(`D-day 후 ${accum.toFixed(1)}% (호재 vs 가격 미스매치)`);
    } else if (accum < 15) {
      score += 5;
    } else if (accum < 30) {
      score -= 5;
      reasons.push(`이미 +${accum.toFixed(0)}% 상승 (과열 주의)`);
    } else {
      score -= 15;
      reasons.push(`이미 +${accum.toFixed(0)}% 폭등 (추격 위험)`);
    }
  }

  // 5) 오늘 매수세 (시초比 + 면 좋음)
  if (price.today_from_open_pct !== null) {
    if (price.today_from_open_pct > 5) {
      score += 8;
      reasons.push(`오늘 시초比 +${price.today_from_open_pct.toFixed(1)}% (매수세 강함)`);
    } else if (price.today_from_open_pct > 0) {
      score += 4;
    } else if (price.today_from_open_pct < -5) {
      score -= 5;
      reasons.push(`오늘 시초比 ${price.today_from_open_pct.toFixed(1)}% (매도세)`);
    }
  }

  // clamp
  score = Math.max(0, Math.min(100, Math.round(score)));

  let recommendation: BuyCandidate["recommendation"];
  if (score >= 80) recommendation = "strong_buy";
  else if (score >= 65) recommendation = "buy";
  else if (score >= 50) recommendation = "watch";
  else if (score >= 35) recommendation = "wait";
  else recommendation = "avoid";

  return { score, recommendation, rationale: reasons.join(" · ") };
}

export function computeAccum(sig: Signal, price: PriceLike): number | null {
  if (price.current_price === null || price.d_day_close === null) return null;
  if (price.d_day_close === 0) return null;
  return (price.current_price / price.d_day_close - 1) * 100;
}

/**
 * 진입가/손절/익절 가이드 — 룰 기반
 *  - 진입가: 현재가가 좋은 위치인지 + 약간의 조정 권장
 *  - 손절: 진입가 -5% ~ -7% (trigger 강도 따라)
 *  - 익절 1차: 진입가 +10~15%
 *  - 익절 2차: 진입가 +20~30%
 */
export function buildEntryGuide(
  sig: Signal,
  price: PriceLike,
): {
  entry_price_suggested: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
  take_profit_2: number | null;
  risk_reward_ratio: number | null;
} {
  if (price.current_price === null) {
    return {
      entry_price_suggested: null,
      stop_loss: null,
      take_profit_1: null,
      take_profit_2: null,
      risk_reward_ratio: null,
    };
  }

  const cur = price.current_price;
  const accum = computeAccum(sig, price) ?? 0;
  const isStrong = STRONG_TRIGGERS.has(sig.trigger_type) && sig.confidence === "high";

  // 진입가: 과열 시 현재가 -3%, 보통 현재가, 미스매치 시 현재가
  let entry: number;
  if (accum > 20) {
    entry = Math.round(cur * 0.97); // 과열 → 3% 조정 후 진입
  } else {
    entry = Math.round(cur);
  }

  // 손절 — 펀더멘털 강하면 -7%, 약하면 -5%
  const stopPct = isStrong ? 0.93 : 0.95;
  const stop = Math.round(entry * stopPct);

  // 익절
  const tp1Pct = isStrong ? 1.15 : 1.10;
  const tp2Pct = isStrong ? 1.30 : 1.20;
  const tp1 = Math.round(entry * tp1Pct);
  const tp2 = Math.round(entry * tp2Pct);

  const riskReward =
    entry > stop && tp1 > entry
      ? Number(((tp1 - entry) / (entry - stop)).toFixed(2))
      : null;

  return {
    entry_price_suggested: entry,
    stop_loss: stop,
    take_profit_1: tp1,
    take_profit_2: tp2,
    risk_reward_ratio: riskReward,
  };
}

/** signals + price quotes → 매수 후보 리스트 (score 정렬, top N) */
export function buildBuyCandidates(
  signals: Record<string, Signal>,
  priceMap: Map<string, PriceLike>,
  topN: number = 10,
): BuyCandidate[] {
  const out: BuyCandidate[] = [];

  for (const [ticker, sig] of Object.entries(signals)) {
    // 너무 오래된 시그널은 제외 (10영업일)
    const age = businessDaysAgo(sig.last_seen);
    if (age > 10) continue;
    // low/unknown 은 제외
    if (sig.confidence === "low" || sig.trigger_type === "unknown") continue;

    const price = priceMap.get(ticker) || {
      current_price: null,
      d_day_close: null,
      today_open: null,
      today_change_pct: null,
      today_from_open_pct: null,
    };

    const accum = computeAccum(sig, price);
    const { score, recommendation, rationale } = scoreSignal(sig, price);
    const entry = buildEntryGuide(sig, price);

    out.push({
      ticker,
      name: sig.name,
      market: sig.market,
      signal_date: sig.last_seen,
      signal_age_days: age,

      trigger_type: sig.trigger_type,
      confidence: sig.confidence as Confidence,
      specific_signal: sig.specific_signal,
      reasoning: sig.reasoning,
      watch_keywords: sig.watch_keywords || [],
      related_stocks: sig.related_stocks || [],

      current_price: price.current_price,
      d_day_close: price.d_day_close,
      accum_pct_from_d_day: accum !== null ? Number(accum.toFixed(2)) : null,
      today_open: price.today_open,
      today_change_pct: price.today_change_pct,
      today_from_open_pct: price.today_from_open_pct,

      recommendation,
      rationale,
      ...entry,
      score,
    });
  }

  // score desc
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, topN);
}
