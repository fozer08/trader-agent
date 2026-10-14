export interface Metadata {
  instance_id: string;
  server_time: string;
  timezone: string;
  source: string;
  delay_minutes: number;
  scheduled_open: boolean | null;
  session_date: string;
  day_kind: 'normal' | 'half_day' | 'closed' | 'unknown';
  reason: string | null;
  calendar_years: number[];
  session_start: string | null;
  session_end: string | null;
  daily_close: string | null;
  next_open: string | null;
  watchlist_count: number;
}
export type Horizon = 'intraday' | 'swing' | 'position';
export type RiskLevel = 'low' | 'moderate' | 'high';
export interface ConversationPreferences {
  horizon: Horizon | null;
  risk_level: RiskLevel | null;
}
export interface DailyAnalysis {
  horizon: Horizon;
  as_of: string;
  price_vs_ema: 'above' | 'below' | 'equal' | null;
  ema_alignment: 'bullish' | 'bearish' | 'flat' | null;
  ema_fast: number | null;
  ema_slow: number | null;
  ema_fast_period: number;
  ema_slow_period: number;
  rsi: number | null;
  atr: number | null;
  atr_pct: number | null;
  relative_volume: number | null;
  return_5_pct: number | null;
  return_20_pct: number | null;
  return_60_pct: number | null;
  unavailable: Record<string, string>;
  levels: {
    last_high: number | null;
    last_low: number | null;
    last_close: number;
    rolling_5_high: number | null;
    rolling_5_low: number | null;
    rolling_20_high: number | null;
    rolling_20_low: number | null;
    rolling_60_high: number | null;
    rolling_60_low: number | null;
  };
}
export interface Stock {
  symbol: string;
  name: string;
}
export interface Stocklist {
  items: Stock[];
}
export interface Quote extends Stock {
  pending: boolean;
  error?: string;
  price?: number | null;
  change_pct?: number | null;
  as_of?: string | null;
  session_date?: string | null;
  price_kind?: 'delayed' | 'close' | 'unavailable';
  horizon?: Horizon;
  daily?: DailyAnalysis;
  delay_minutes?: number;
  daily_stale?: boolean | null;
  session_error?: string | null;
  session_open?: boolean;
  session_age_minutes?: number | null;
  session?: {
    as_of: string;
    observed_open: number;
    observed_high: number;
    observed_low: number;
    price: number;
    ohlc_kind: 'reported' | 'range_only' | 'sampled' | 'close_only';
  } | null;
}
export interface Watchlist {
  items: Quote[];
  refreshing: boolean;
  updated_at: string | null;
}
export interface SymbolDetail extends Quote {
  daily: DailyAnalysis;
}
export interface Bars {
  symbol: string;
  timeframe: string;
  chart_type: 'candlestick' | 'line';
  bars: {
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number | null;
  }[];
}
export type PortfolioTab = 'recommended' | 'open' | 'closed' | 'performance';
export type PositionSource = 'manual' | 'system';
export interface Recommendation {
  id: string;
  symbol: string;
  entry_min: number;
  entry_max: number;
  stop_loss: number;
  target: number;
  horizon: Horizon;
  risk_level: RiskLevel | null;
  rationale: string;
  producer: 'assistant' | 'model';
  producer_version: string;
  created_at: string;
  expires_at: string;
  data_as_of: string;
  active: boolean;
  position_id: string | null;
}
export interface Position {
  id: string;
  source: PositionSource;
  producer: string | null;
  recommendation_id: string | null;
  initial_plan: {
    stop_loss?: number | null;
    target?: number | null;
    horizon?: Horizon;
    note?: string;
    rationale?: string;
    entry_min?: number;
    entry_max?: number;
    expires_at?: string;
  };
  plan_changed: boolean;
  created_at: string;
  closed_at: string | null;
  symbol: string;
  horizon: Horizon;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  opened_at: string | null;
  hold_days: number | null;
  realized_pnl: number;
  unrealized_pnl?: number;
  unrealized_pnl_pct?: number;
  stop_loss: number | null;
  target: number | null;
  note: string;
  distance_to_stop_pct?: number;
  distance_to_target_pct?: number;
  as_of: string | null;
  price_kind: 'delayed' | 'close' | 'unavailable';
}
export interface Portfolio {
  positions: Position[];
  summary: {
    total_cost: number;
    market_value: number | null;
    unrealized_pnl: number | null;
    unrealized_pnl_pct: number | null;
    realized_pnl: number;
    missing_prices: number;
    missing_stops: number;
    breached_stops: number;
    planned_downside_to_stops: number | null;
    largest_position_weight_pct: number | null;
  };
}
export interface ClosedPosition {
  id: string;
  source: PositionSource;
  producer: string | null;
  horizon: Horizon;
  recommendation_id: string | null;
  symbol: string;
  opened_at: string | null;
  closed_at: string;
  hold_days: number | null;
  quantity: number;
  realized_pnl: number;
  realized_pnl_pct: number;
  avg_buy_cost: number;
  avg_sell_price: number;
}
export interface Transaction {
  id: number;
  kind: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  created_at: string;
}
export interface Digest {
  date: string;
  sources: string[];
  item_count: number;
  summary: string;
}
export interface News {
  digests: Digest[];
}
export type ChatEvent =
  | { type: 'text'; content: string }
  | { type: 'tool'; name: string }
  | { type: 'recommendation'; recommendation_id: string }
  | { type: 'error'; message: string };

export interface Performance {
  realized_pnl: number;
  released_cost: number;
  realized_return_pct: number | null;
  closed_count: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number | null;
  average_hold_days: number | null;
  known_hold_count: number;
  current: Portfolio['summary'];
}
