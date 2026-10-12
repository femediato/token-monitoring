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

export interface BacktestMetrics {
  start: string;
  end: string;
  hours: number;
  trades: number;
  net_pnl_usd: number;
  net_return_pct: Num;
  win_rate_pct: Num;
  profit_factor: Num;
  gross_profit_usd: number;
  gross_loss_usd: number;
  expectancy_usd: Num;
  expectancy_pct: Num;
  max_drawdown_pct: number;
  sharpe: Num;
  sortino: Num;
  return_days: number;
  avg_cost_pct: Num;
  costs_usd: number;
  exposure_pct: Num;
  exits: Record<string, number>;
  by_strategy: Record<string, { trades: number; pnl_usd: number }>;
}

export interface BacktestTrade {
  position: number;
  strategy: string;
  mint: string;
  entry_at: string;
  exit_at: string;
  entry_price: number;
  exit_price: number;
  exit_fill_price: number;
  size_usd: number;
  qty: number;
  cost_pct: number;
  proceeds_usd: number;
  pnl_usd: number;
  return_pct: number;
  reason: string;
}

export interface BacktestRunResult {
  universe: string;
  label: string;
  scenario: string;
  decision_digest: string;
  decisions: Record<string, number>;
  rejections: Record<string, number>;
  metrics: Record<string, BacktestMetrics>;
  members_avg: number;
  candle_coverage_pct: Num;
  equity: [string, number, number][];
  trades: BacktestTrade[];
  open_positions: { mint: string; strategy: string; entry_at: string }[];
}

export interface ForwardRow {
  n: number;
  missing: number;
  mean_pct: Num;
  median_pct: Num;
  hit_rate_pct: Num;
  net_mean_pct: Num;
  net_n: number;
  uplift_pct: Num;
}

export interface BacktestGate {
  gate: string;
  value: Num;
  threshold: string;
  status: "pass" | "fail" | "insufficient_data";
  trades: number;
  min_trades: number;
}

export interface BacktestData {
  tokens: number;
  evaluations: number;
  candles: number;
  first_evaluation_at: string | null;
  input_digest: string;
  days_available: number;
  days_target: number;
  sufficient: boolean;
}

export interface ExecutionScenario {
  name: string;
  entry_delay_minutes: number;
  cost_multiplier: number;
  stop_slippage_pct: number;
  swap_failure_pct: number;
}

export interface BacktestReport {
  id: number;
  created_at: string;
  symbols: Record<string, string | null>;
  version: string;
  strategy_version: string;
  model_version: string;
  window: { start: string; end: string; simulated_until: string; sealed: boolean; days: number };
  splits: { name: string; start: string; end: string }[];
  data: BacktestData;
  params: {
    strategy: Record<string, unknown>;
    backtest: { candle_latency_minutes: number; scenarios: Record<string, ExecutionScenario>; universe: Record<string, unknown> } & Record<string, unknown>;
    overrides: Record<string, unknown>;
  };
  runs: BacktestRunResult[];
  forward: Record<string, Record<string, Record<string, ForwardRow>>>;
  gates: BacktestGate[];
  result_digest: string;
}

export interface BacktestListItem {
  id: number;
  created_at: string;
  version: string;
  strategy_version: string;
  model_version: string;
  window_start: string;
  window_end: string;
  sealed: boolean;
  overrides: Record<string, unknown>;
  input_digest: string;
  result_digest: string;
  data: BacktestData;
  gates: BacktestGate[];
  selection: BacktestMetrics | null;
}

export interface BacktestList {
  version: string;
  universes: Record<string, string>;
  scenarios: string[];
  items: BacktestListItem[];
}

export interface PaperPosition {
  id: number;
  status: "pending" | "open" | "closed" | "cancelled";
  strategy: string;
  token_mint: string;
  strategy_version: string;
  paper_version: string;
  signal_at: string;
  signal_bar: string;
  entry_ref: number;
  stop: number;
  target: number;
  max_hold_hours: number;
  quoted_cost_pct: number;
  size_usd: number;
  planned_risk_usd: number;
  sizing_binding: string;
  entry_at: string | null;
  entry_price: Num;
  entry_cost_pct: Num;
  qty: Num;
  exit_by: string | null;
  last_price: Num;
  last_price_at: string | null;
  pending_exit: string | null;
  exit_attempts: number;
  exit_at: string | null;
  exit_reason: string | null;
  exit_trigger_price: Num;
  exit_price: Num;
  exit_cost_pct: Num;
  proceeds_usd: Num;
  pnl_usd: Num;
  cancel_reason: string | null;
}

export interface PaperEvent {
  id: number;
  position_id: number | null;
  at: string;
  kind: string;
  strategy: string | null;
  token_mint: string | null;
  signal_bar: string | null;
  reason: string | null;
  data: Record<string, unknown>;
}

export interface PaperFill {
  id: number;
  side: "buy" | "sell";
  decided_at: string;
  quoted_at: string | null;
  usd: Num;
  qty: Num;
  ref_price: Num;
  fill_price: Num;
  cost_pct: Num;
  fee_usd: Num;
  price_impact_pct: Num;
  route_labels: string[] | null;
  trigger_price: Num;
  raw_ids: number[] | null;
  error: string | null;
  model_price: Num;
  model_note: string | null;
  shortfall_vs_model_pct: Num;
}

export interface PaperSide {
  attempts: number;
  failed: number;
  failure_pct: Num;
  cost_pct: Num;
  quoted_cost_pct: Num;
  price_impact_pct: Num;
  model_n: number;
  shortfall_vs_model_pct: Num;
}

export interface PaperTrade {
  position: number;
  strategy: string;
  mint: string;
  entry_at: string;
  exit_at: string;
  entry_price: number;
  exit_price: number;
  exit_fill_price: Num;
  size_usd: number;
  qty: number;
  proceeds_usd: number;
  pnl_usd: number;
  return_pct: Num;
  reason: string;
  entry_cost_pct: Num;
  exit_cost_pct: Num;
}

type Assumptions = { cost_multiplier: Num; stop_slippage_pct: Num; swap_failure_pct: Num };

export interface PaperReport {
  version: string;
  account: null | {
    name: string;
    created_at: string;
    starting_equity_usd: number;
    cash_usd: number;
    equity_usd: number;
    positions_usd: number;
    params: Record<string, unknown>;
  };
  running_since: string;
  days: number;
  positions: Record<string, number>;
  metrics: BacktestMetrics;
  execution: {
    buy: PaperSide;
    sell: PaperSide;
    stop_slippage_pct: Num;
    stops: number;
    vs_backtest: { assumed: Assumptions; observed: Assumptions };
  };
  progress: { days: number; days_required: number; trades: number; trades_required: number };
  trades: PaperTrade[];
  reconciliation: { ok: boolean; positions: number; cash_from_fills_usd: number; cash_usd: number; issues: { position: number | null; issue: string }[] };
  open: PaperPosition[];
  exposure: Exposure;
  equity: { at: string; equity_usd: number; cash_usd: number; positions_usd: number; open_positions: number }[];
  events: PaperEvent[];
  symbols: Record<string, string | null>;
}

export interface PaperPositionDetail {
  position: PaperPosition;
  symbol: string | null;
  events: PaperEvent[];
  fills: PaperFill[];
  issues: string[];
}

export interface Exposure {
  invested_usd: number;
  invested_pct: Num;
  positions: number;
  max_positions: number;
  planned_risk_usd: number;
  planned_risk_pct: Num;
  max_open_risk_pct: number;
  risk_to_stop_usd: number;
  by_strategy: Record<string, number>;
  items: { position: number; strategy: string; mint: string; value_usd: number; pct_of_equity: Num; planned_risk_usd: number; risk_to_stop_usd: number }[];
}

// ---------------------------------------------------------------- analytics (Phase 8)

export interface SignalOutcome {
  status: "waiting" | "open" | "target" | "stop" | "time" | "no_data";
  exit_reason: string | null;
  entry_at: string | null;
  entry_price: Num;
  exit_at: string | null;
  exit_price: Num;
  gross_pct: Num;
  net_pct: Num;
  best_pct: Num;
  worst_pct: Num;
}

export interface SignalRow {
  id: number;
  at: string;
  strategy: string;
  mint: string;
  symbol: string | null;
  signal_bar: string;
  position_id: number | null;
  reject_reason: string | null;
  decision: "taken" | "rejected" | "cancelled";
  status: string | null;
  entry_at: string | null;
  entry_price: Num;
  exit_at: string | null;
  exit_price: Num;
  exit_reason: string | null;
  pnl_usd: Num;
  size_usd: Num;
  last_price: Num;
  qty: Num;
  cancel_reason: string | null;
  entry_ref: number;
  stop: number;
  target: number;
  max_hold_hours: number;
  cost_pct: number;
  paper_return_pct: Num;
  outcome: SignalOutcome;
}

export interface AccuracyStats {
  signals: number;
  resolved: number;
  status: Record<string, number>;
  target_hit_pct: Num;
  positive_pct: Num;
  mean_net_pct: Num;
  median_net_pct: Num;
  paper_n: number;
  paper_mean_return_pct: Num;
  ideal_mean_net_pct: Num;
}

export interface SignalsReport {
  version: string;
  days: number;
  accuracy: Record<string, { all: AccuracyStats; taken: AccuracyStats; rejected: AccuracyStats }>;
  items: SignalRow[];
}

export interface ChartData {
  mint: string;
  symbol: string | null;
  pool: string | null;
  start: string;
  end: string;
  candles: { t: string; open: number; high: number; low: number; close: number; volume_usd: number }[];
  signals: SignalRow[];
}

export interface StrategyStats {
  trades: number;
  wins: number;
  win_rate_pct: Num;
  net_pnl_usd: number;
  profit_factor: Num;
  expectancy_usd: Num;
  expectancy_pct: Num;
  best_pct: Num;
  worst_pct: Num;
  avg_hold_hours: Num;
  exits: Record<string, number>;
  pnl_before_costs_usd: number;
  costs: {
    total_usd: number;
    entry_usd: number;
    exit_usd: number;
    fees_usd: number;
    stop_overshoot_usd: number;
    volume_usd: number;
    pct_of_volume: Num;
    per_trade_usd: Num;
  };
}

export interface DailyRow {
  day: string;
  trades: number;
  wins: number;
  pnl_usd: number;
  costs_usd: number;
  equity_usd: Num;
  return_pct: Num;
}

export interface Performance {
  version: string;
  paper_version: string;
  account: PaperReport["account"];
  running_since: string;
  progress: PaperReport["progress"];
  metrics: BacktestMetrics;
  execution: PaperReport["execution"];
  by_strategy: Record<string, StrategyStats>;
  daily: DailyRow[];
  equity: { at: string; equity_usd: number; positions_usd: number; open_positions: number }[];
}
