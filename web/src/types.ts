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
export interface Indicators {
  ema_trend: string | null;
  ema_alignment: string | null;
  ema_fast?: number | null;
  ema_slow?: number | null;
  rsi: number | null;
  rsi_divergence: string | null;
  macd_histogram: number | null;
  atr: number | null;
  bb_width: number | null;
  bb_pct_b: number | null;
  relative_volume?: number | null;
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
  daily?: Indicators;
  pulse?: { relative_volume: number | null; high: number; low: number } | null;
}
export interface Watchlist {
  items: Quote[];
  refreshing: boolean;
  updated_at: string | null;
}
export interface SymbolDetail extends Quote {
  levels: {
    prev_high: number;
    prev_low: number;
    prev_close: number;
    weekly_high: number;
    weekly_low: number;
    relative_volume: number | null;
    pivot: { pp: number; s1: number; s2: number; r1: number; r2: number };
    candle: { name: string; bullish: boolean } | null;
  };
  intraday: { m5: Indicators | null; m15: Indicators | null };
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
export interface Position {
  symbol: string;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  opened_at: string;
  hold_days: number;
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
  };
}
export interface ClosedPosition {
  symbol: string;
  opened_at: string;
  closed_at: string;
  hold_days: number;
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
  | { type: 'error'; message: string };
