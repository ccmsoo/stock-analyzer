/**
 * Backend data types — mirrors stock_analyzer state/JSON layouts.
 */

export type Confidence = "high" | "medium" | "low" | "";
export type TriggerType =
  | "disclosure"
  | "earnings"
  | "contract"
  | "policy"
  | "rumor"
  | "technical"
  | "unknown"
  | "";

export interface SignalHistory {
  date: string; // YYYYMMDD
  change_pct: number;
}

export interface Signal {
  ticker: string;
  name: string;
  market: "KOSPI" | "KOSDAQ" | string;
  first_seen: string;
  last_seen: string;
  consecutive_days: number;

  main_theme: string;
  specific_signal: string;
  trigger_type: TriggerType;
  confidence: Confidence;
  watch_keywords: string[];
  related_stocks: string[];
  reasoning: string;
  reason_unknown_category?: string;

  // optional deep keywords
  deep_keywords?: {
    products?: string[];
    partners?: string[];
    places?: string[];
    events?: string[];
    people?: string[];
  };

  history: SignalHistory[];
  cluster_tag?: string | null;
}

export interface BacktestTrade {
  signal_date: string;
  ticker: string;
  name: string;
  market: string;

  main_theme: string;
  specific_signal: string;
  trigger_type: string;
  confidence: string;
  watch_keywords: string[];

  chart_score: number | null;
  entry_risk: string;
  rsi14: number | null;
  volume_ratio_20d: number | null;
  value_ratio_20d: number | null;
  distance_ma20_pct: number | null;
  upper_shadow_pct: number | null;
  close_position_pct: number | null;
  high_20d_breakout: boolean;
  high_60d_breakout: boolean;

  close_on_signal_date: number | null;
  entry_price_next_open: number | null;
  exit_close_1d: number | null;
  exit_close_3d: number | null;
  exit_close_5d: number | null;
  exit_close_10d: number | null;

  return_1d: number | null;
  return_3d: number | null;
  return_5d: number | null;
  return_10d: number | null;
  max_gain_5d: number | null;
  max_drawdown_5d: number | null;

  strategy_eligible: boolean;
  exclusion_reasons: string[];
  strategy_entry_price: number | null;
  strategy_exit_price: number | null;
  strategy_exit_reason: string;
  strategy_exit_date: string;
  strategy_return_pct: number | null;

  profitability_score: number | null;
  score_label: string;
  score_breakdown: Record<string, unknown>;

  consecutive_days: number;
  note: string;

  reason_unknown_category?: string;
  article_count?: number | null;
  article_origin_dist?: string;
  latest_article_date?: string;
  trigger_lag_candidate?: number | null;
}

export interface BuyCandidate {
  ticker: string;
  name: string;
  market: string;
  signal_date: string;
  signal_age_days: number;

  trigger_type: TriggerType;
  confidence: Confidence;
  specific_signal: string;
  reasoning: string;
  watch_keywords: string[];
  related_stocks: string[];

  // Price snapshot (server-side fetched, cached 5min)
  current_price: number | null;
  d_day_close: number | null;
  accum_pct_from_d_day: number | null;
  today_open: number | null;
  today_change_pct: number | null;
  today_from_open_pct: number | null;

  // Recommendation derived (analysis.ts)
  recommendation: "strong_buy" | "buy" | "watch" | "wait" | "avoid";
  rationale: string;
  entry_price_suggested: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
  take_profit_2: number | null;
  risk_reward_ratio: number | null;

  // Score 0-100
  score: number;
}

export interface PriceQuote {
  ticker: string;
  prev_close: number | null;
  today_open: number | null;
  today_high: number | null;
  today_low: number | null;
  today_close: number | null;
  today_volume: number | null;
  today_change_pct: number | null;
  today_from_open_pct: number | null;
  fetched_at: string;
}

export interface Cumulative {
  total_signals: number;
  unique_watch_kw: number;
  unique_deep_kw: number;
  by_confidence: Record<string, number>;
  last_update: string;
  covered_dates: string[];
}

export interface DataPayload {
  generated_at: string;
  cumulative: Cumulative;
  signals: Record<string, Signal>; // ticker -> Signal
  buy_candidates: BuyCandidate[]; // ranked
  recent_trades?: BacktestTrade[]; // 최근 N개
}
