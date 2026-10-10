import useSWR, { type SWRConfiguration } from "swr";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

/** All data refreshes every minute; collection runs every 5 minutes. */
export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  return useSWR<T>(path, fetcher, { refreshInterval: 60_000, keepPreviousData: true, ...config });
}

export type Num = number | null;

export interface TokenRow {
  mint: string;
  symbol: string | null;
  name: string | null;
  classification: string | null;
  classification_reason: string | null;
  tracking_status: "active" | "dormant" | "excluded";
  tracking_reason: string | null;
  tracking_changed_at: string | null;
  first_seen_at: string;
  pair_created_at: string | null;
  launchpad: string | null;
  tags: string[] | null;
  primary_pool: string | null;
  age_days: Num;
  observed_at: string | null;
  source_updated_at: string | null;
  price_usd: Num;
  market_cap: Num;
  fdv: Num;
  liquidity_usd: Num;
  holder_count: Num;
  organic_score: Num;
  top_holders_pct: Num;
  volume_24h: Num;
  price_change_24h: Num;
  traders_24h: Num;
  trades_24h: Num;
  buy_share_24h: Num;
  volume_1h: Num;
  price_change_1h: Num;
  buy_share_1h: Num;
  security_status: "ok" | "risky" | "unknown" | null;
  risk_flags: string[] | null;
  top10_pct_adjusted: Num;
  sell_cost_pct: Num;
  quote_error: string | null;
}

export interface TokenList {
  total: number;
  limit: number;
  offset: number;
  items: TokenRow[];
}

export interface WindowStats {
  window: string;
  observed_at: string;
  price_change_pct: Num;
  liquidity_change_pct: Num;
  holder_change_pct: Num;
  volume_usd: Num;
  buy_volume_usd: Num;
  sell_volume_usd: Num;
  organic_buy_volume_usd: Num;
  organic_sell_volume_usd: Num;
  num_buys: Num;
  num_sells: Num;
  num_traders: Num;
  num_net_buyers: Num;
  num_organic_buyers: Num;
}

export interface PoolRow {
  address: string;
  dex_id: string | null;
  quote_symbol: string | null;
  labels: string[] | null;
  pair_created_at: string | null;
  observed_at: string | null;
  liquidity_usd: Num;
  reserve_base: Num;
  reserve_quote: Num;
  price_usd: Num;
  volume_24h: Num;
  buys_24h: Num;
  sells_24h: Num;
  is_primary: boolean;
}

export interface HolderAccount {
  account?: string; // method v1
  token_accounts?: string[]; // method v2: accounts aggregated by owner
  off_curve?: boolean | null;
  owner: string | null;
  owner_program: string | null;
  amount_raw: string;
  pct_of_supply: number;
  excluded: string | null;
}

export interface TokenDetail {
  token: TokenRow;
  windows: WindowStats[];
  pools: PoolRow[];
  security: {
    checked_at: string;
    status: string;
    token_program: string | null;
    mint_authority: string | null;
    freeze_authority: string | null;
    decimals: number | null;
    extensions: string[] | null;
    risk_flags: string[];
  } | null;
  holders: {
    observed_at: string;
    method_version: string;
    top10_pct_raw: Num;
    top10_pct_adjusted: Num;
    excluded_pct: Num;
    accounts: HolderAccount[];
    error: string | null;
  } | null;
  discovery: {
    appearances: number;
    first_at: string | null;
    last_at: string | null;
    best_rank: number | null;
    lists: string[] | null;
  } | null;
  tracking_events: TrackingEvent[];
}

export interface TrackingEvent {
  at: string;
  token_mint?: string;
  symbol?: string | null;
  name?: string | null;
  from_status: string | null;
  to_status: string;
  reason: string;
  classification?: string | null;
}

export interface SeriesPoint {
  t: string;
  price_usd: Num;
  market_cap: Num;
  liquidity_usd: Num;
  holder_count: Num;
  buy_volume_1h: Num;
  sell_volume_1h: Num;
  traders_1h: Num;
  net_buyers_1h: Num;
}

export interface Candle {
  t: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume_usd: number;
  is_complete: boolean;
}

export interface Quote {
  quoted_at: string;
  notional_usd: number;
  ref_price_usd: Num;
  out_usd: Num;
  slippage_pct: Num;
  cost_pct: Num;
  price_impact_pct: Num;
  route_labels: string[] | null;
  error: string | null;
}

export interface JobHealth {
  job: string;
  expected_every_minutes: number;
  runs: number;
  successes: number;
  failures: number;
  last_success_at: string | null;
  minutes_since_success: Num;
  max_gap_minutes: Num;
  gaps: number;
  coverage_pct: number;
  status: "ok" | "degraded" | "stale" | "never_ran";
}

export interface Overview {
  collecting_since: string | null;
  universe: { tracking_status: string; classification: string | null; n: number }[];
  rows: { table: string; n: number }[];
  requests_24h: { job: string; requests: number; runs: number }[];
  jobs: JobHealth[];
  freshness: {
    tracked_active: number;
    tracked_dormant: number;
    excluded: number;
    active_with_fresh_snapshot: number;
    active_stale: number;
    source_lag_p50_minutes: Num;
    source_lag_max_minutes: Num;
    stale_source_values: number;
  };
}

export interface Categories {
  activity: Num;
  momentum: Num;
  liquidity: Num;
  buying: Num;
  risk: Num;
}

export interface WatchlistMemberRow extends Categories {
  mint: string;
  symbol: string | null;
  name: string | null;
  group: string;
  joined_at: string;
  entry_score: number;
  total: Num;
  coverage: Num;
  eligible: boolean | null;
  reasons: string[] | null;
  rank: Num;
  hourly_total: Num;
  prev_hourly_total: Num;
  price_usd: Num;
  market_cap: Num;
  liquidity_usd: Num;
  volume_24h: Num;
}

export interface Watchlist {
  model_version: string;
  evaluated_at: string | null;
  hourly_evaluated_at: string | null;
  size: number;
  entry_score: number;
  retain_score: number;
  members: WatchlistMemberRow[];
}

export interface WatchlistEventRow {
  at: string;
  mint: string;
  symbol: string | null;
  action: "add" | "remove";
  group: string | null;
  reason: string;
  score: Num;
}

export interface EvaluationRow extends Categories {
  mint: string;
  symbol: string | null;
  name: string | null;
  eligible: boolean;
  critical: boolean;
  reasons: string[];
  total: Num;
  coverage: Num;
  group: string | null;
  rank: Num;
  market_cap: Num;
  liquidity_usd: Num;
  volume_24h: Num;
  member: boolean;
}

export interface Evaluations {
  evaluated_at: string | null;
  counts: { evaluated?: number; eligible?: number; critical?: number; above_entry?: number };
  reasons: { reason: string; n: number }[];
  items: EvaluationRow[];
}

export interface TokenScores {
  model_version: string;
  latest:
    | (Categories & {
        evaluated_at: string;
        eligible: boolean;
        critical: boolean;
        reasons: string[];
        features: Record<string, unknown>;
        components: Record<string, Record<string, number | null>> | null;
        total: Num;
        coverage: Num;
        group: string | null;
        rank: Num;
      })
    | null;
  history: (Categories & { t: string; eligible: boolean; total: Num; coverage: Num; rank: Num; hourly: boolean })[];
  watchlist_events: { at: string; action: string; group: string | null; reason: string; score: Num }[];
}

export interface ModelInfo {
  version: string;
  category_weights: Record<string, number>;
  components: Record<string, { name: string; weight: number }[]>;
  reason_codes: Record<string, string>;
}

export interface InfraCandidates {
  tokens_analysed: number;
  candidates: { owner: string; tokens: number; avg_pct: number; max_pct: number; excluded: boolean }[];
}

export interface EarlyBoard {
  model_version: string;
  evaluated_at: string | null;
  signals: string[];
  alert_min_score: number;
  states: Record<string, number>;
  items: {
    mint: string;
    symbol: string | null;
    name: string | null;
    score: number;
    state: string;
    signals: Record<string, boolean | null>;
    eligible: boolean;
    price_usd: Num;
    ret_1h_pct: Num;
    market_cap: Num;
    liquidity_usd: Num;
    volume_1h: Num;
  }[];
}

export interface EarlyAlertRow {
  id: number;
  at: string;
  mint: string;
  symbol: string | null;
  score: number;
  state: string;
  signals: Record<string, boolean | null>;
  price_usd: Num;
  pre_return_1h_pct: Num;
  pre_return_4h_pct: Num;
  entry_cost_pct: Num;
  entry_error: string | null;
  ret_1h_pct: Num;
  ret_4h_pct: Num;
  ret_12h_pct: Num;
  ret_24h_pct: Num;
  mfe_24h_pct: Num;
  mae_24h_pct: Num;
  missing: string[] | null;
  controls: number;
  control_ret_24h_pct: Num;
}

export interface Stat {
  n: number;
  mean: Num;
  median: Num;
  win_rate: Num;
  se: Num;
  ci95: [number, number] | null;
}

export interface EarlyReport {
  model_version: string;
  alerts: number;
  with_controls: number;
  horizons: Record<string, { alerts: Stat; controls: Stat; uplift: Stat }>;
  excursions: Record<string, Record<string, Stat>>;
  pre_alert: Record<string, Stat>;
  entry: { checked: number; available: number; cost: Stat };
}

export type Check = boolean | null;

export interface StrategySignalRow {
  mint: string;
  symbol: string | null;
  strategy: "momentum" | "pullback" | "range";
  setup: boolean;
  checks: Record<string, Check>;
  failed: string[];
  values: Record<string, number | null>;
  last_candle_at: string | null;
  entry_ref: Num;
  stop: Num;
  target: Num;
  max_hold_hours: Num;
  cost_pct: Num;
}

export interface StrategySignals {
  version: string;
  evaluated_at: string | null;
  items: StrategySignalRow[];
}

export interface StrategySetups {
  days: number;
  totals: { strategy: string; evaluated: number; setups: number }[];
  blockers: { strategy: string; check: string; n: number }[];
  items: (Omit<StrategySignalRow, "checks" | "failed" | "setup" | "last_candle_at"> & { at: string })[];
}

interface ExitParams {
  take_profit_pct: number;
  stop_loss_pct: number;
  max_hold_hours: number;
}

export interface StrategyParams {
  version: string;
  params: {
    enabled: string[];
    momentum: { min_relative_volume: number; min_buying_score: number; exit: ExitParams };
    pullback: { ema_fast: number; ema_slow: number; high_lookback_candles: number; min_retrace_pct: number; max_retrace_pct: number; exit: ExitParams };
    range: {
      window_candles: number;
      min_support_tests: number;
      min_resistance_tests: number;
      min_reward_risk: number;
      stop_buffer_pct: number;
      max_hold_hours: number;
    };
    risk: {
      starting_equity_usd: number;
      risk_per_trade_pct: number;
      max_positions: number;
      max_open_risk_pct: number;
      daily_loss_stop_pct: number;
      max_quote_age_minutes: number;
      max_cost_pct: number;
      quoted_size_usd: number;
      max_liquidity_share_pct: number;
    };
    data: { max_candle_age_minutes: number; max_evaluation_age_minutes: number };
  };
}
