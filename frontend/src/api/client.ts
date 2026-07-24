import axios from 'axios'
import type { ManualQmtOrder, RiskOperatingState } from './openapi.generated'

export type { RiskOperatingState } from './openapi.generated'

export interface ExecutionSnapshot {
  execution_mode: 'disabled' | 'paper' | 'qmt_sim' | 'qmt_live'
  connected: boolean
  subscribed: boolean
  account_healthy: boolean
  account?: string | null
  assets?: {
    cash: string
    available_cash: string
    frozen_cash: string
    positions_value: string
    total_assets: string
  } | null
  positions: Array<Record<string, string | number>>
  orders: Array<Record<string, string | number>>
  trades: Array<Record<string, string | number>>
  kill_switch?: boolean
  cancel_only?: boolean
  no_new_positions?: boolean
  last_heartbeat?: string | null
  last_reconciled_at?: string | null
  last_error?: string | null
  engine_online?: boolean
  engine_pid?: number
  engine_role?: string
  started_at?: string | null
  risk_state?: RiskStateSnapshot
  execution_universe?: ExecutionUniverseRelease | null
}

export interface ExecutionUniverseRelease {
  release_id: string
  trade_date: string
  execution_mode: 'qmt_sim' | 'qmt_live'
  scope: string
  status: 'draft' | 'reviewed' | 'active' | 'revoked'
  symbol_count: number
  snapshot_hash: string
  required_data_date: string
  filters: Record<string, unknown>
  rejection_counts: Record<string, number>
  reviewed_by?: string | null
  approved_by?: string | null
  reviewed_at?: string | null
  activated_at?: string | null
  expires_at?: string | null
}

export interface StrategyExecutionRelease {
  release_id: string
  trade_date: string
  execution_mode: 'qmt_sim' | 'qmt_live'
  strategy_id: string
  strategy_name: string
  strategy_version: string
  universe_release_id: string
  parameters_hash: string
  status: 'draft' | 'active' | 'revoked'
  max_order_notional: string
  max_daily_notional: string
  approved_by?: string | null
  activated_at?: string | null
  expires_at?: string | null
}

export interface StrategyBundleCatalog {
  inbox: string
  bundles: Array<{
    name: string
    strategy_id?: string | null
    strategy_version?: string | null
    approval_status?: string | null
    readable: boolean
  }>
}

export interface StrategyBundleValidation {
  bundle_hash: string
  strategy_id: string
  strategy_version: string
  approval_status: string
  execution_target: 'paper' | 'qmt_sim' | 'qmt_live'
  validated_at: string
  receipt_path: string
  execution_release?: StrategyExecutionRelease
}

export interface RiskStateSnapshot {
  state: RiskOperatingState
  reason: string
  version?: number
  changed_at?: string
  reconciled_at?: string | null
}

export interface ExecutionRiskPolicyValues {
  max_order_notional: string
  max_daily_notional: string
  max_orders_per_minute: number
  max_symbol_position_notional: string
  max_total_position_notional: string
  max_total_position_ratio: string
  max_daily_orders: number
  max_strategy_daily_notional: string
}

export interface ExecutionRiskPolicySnapshot {
  current: ExecutionRiskPolicyValues
  ceiling: ExecutionRiskPolicyValues
  editable: boolean
  requires_restricted_state: boolean
  error?: string | null
}

export interface DownloadTask {
  id: number
  symbols: string[]
  status: string
  total_items: number
  succeeded_items: number
  progress: number
}

export interface QualityIssue {
  id: number
  symbol: string
  check_code: string
  severity: 'warning' | 'error'
  message: string
}

export interface StockPool {
  id: number
  name: string
  pool_type: string
  member_count: number
}

export interface MarketStatus {
  installed: boolean
  connected: boolean
  sector_count: number
  live_trading_enabled: boolean
  error?: string | null
}

export interface XtDataCatalog {
  source: 'xtdata'
  capabilities: {
    connected: boolean
    runtime?: string
    module?: string
    data_dir?: string
    sector_count: number
    available_periods: string[]
    functions: Record<string, boolean>
    error?: string | null
  }
  datasets: Array<{
    value: string
    label: string
    download_api: string
    requires_symbols: boolean
    requires_period: boolean
    supports_shared_import: boolean
    entitlement?: string
    available: boolean
  }>
  periods: Array<{
    value: string
    label: string
    group: string
    entitlement: string
    reported_by_client: boolean
  }>
  shared_import_periods: string[]
  level2_history_downloadable: boolean
  notes: string[]
}

export interface XtDataDownloadRequest {
  dataset: string
  symbols: string[]
  period: string
  start_date: string
  end_date: string
  tables: string[]
  import_to_shared_store: boolean
}

export interface XtDataDownloadResult {
  status: string
  dataset: string
  period?: string | null
  symbols: string[]
  downloaded: boolean
  imported_to_shared_store: boolean
  shared_store: string
  summary: Record<string, unknown>
}

export interface MarketSession {
  phase: 'CLOSED' | 'PREOPEN' | 'TRADING_AM' | 'LUNCH' | 'TRADING_PM' | 'CLOSING' | 'POSTCLOSE'
  observed_at: string
  new_orders_expected: boolean
  operator_action: string
  calendar_source: string
}

export interface MarketBar {
  datetime: string
  bar_start_time?: string
  symbol: string
  open: number | string
  high: number | string
  low: number | string
  close: number | string
  volume: number
  amount?: number | string
  period?: string
  adjust_mode?: string
  source?: string
  is_final?: boolean
  received_at?: string | null
  updated_at?: string | null
  quality_status?: string
}

export interface StitchedCandles {
  symbol: string
  period: string
  adjust_mode: string
  bars: MarketBar[]
  has_gap: boolean
  gaps: string[]
  last_history_time?: string | null
  last_realtime_time?: string | null
  backfill_attempted: boolean
  backfill_error?: string | null
  realtime_error?: string | null
  calendar_error?: string | null
  continuity_verified: boolean
}

export interface RealtimeQuote {
  lastPrice?: number
  volume?: number
  amount?: number
  time?: number | string
  timetag?: number | string
}

export interface XtDataSubscriptionDescriptor {
  mode: 'single' | 'whole'
  symbol?: string
  markets?: string[]
  period: string
}

export interface XtDataSubscriptionSnapshot {
  active: boolean
  subscriptions: XtDataSubscriptionDescriptor[]
  subscription_count: number
  latest_symbol_count: number
  received_batches: number
  received_symbols: number
  dropped_batches: number
  queue_depth: number
  last_received_at?: string | null
  quote_age_seconds?: number | null
  fresh: boolean
  last_error?: string | null
}

export type XtDataQuoteSnapshot = Record<string, string | number | boolean | null>
export type XtDataLatestSnapshots = Record<string, XtDataQuoteSnapshot>

export interface StrategyMetadata {
  name: string
  version: string
  description: string
  parameters_schema: {
    properties?: Record<string, { title?: string; default?: unknown; type?: string }>
  }
  enabled: boolean
}

export interface BacktestTask {
  id: number
  status: string
  progress: number
  strategy_name: string
  strategy_version?: string | null
  symbols: string[]
  result_id?: number | null
  error_message?: string | null
  data_fingerprint?: string | null
}

export interface BacktestResult {
  id: number
  run_id: string
  metrics: Record<string, number>
  reports: Record<string, string | null>
  orders: Array<{
    order_id: string
    symbol: string
    direction: string
    quantity: number
    filled_quantity: number
    status: string
    average_fill_price: string
  }>
  trades: Array<{
    trade_id: string
    symbol: string
    direction: string
    quantity: number
    price: string
    commission: string
    tax: string
    transfer_fee: string
    timestamp: string
  }>
  equity: Array<{
    date: string
    cash: string
    positions_value: string
    total_assets: string
    return_value: number
    drawdown: number
    net_value: number
    benchmark_net_value?: number | null
    relative_net_value?: number | null
  }>
}

export interface FactorResult {
  summary: Record<string, number>
  daily_ic: Array<{ date: string; factor: string; pearson_ic: number; rank_ic: number }>
  group_returns: Array<{ date: string; factor: string; group: number; return: number }>
  turnover: Array<{ date: string; factor: string; turnover: number }>
}

export interface OptimizationResult {
  method: string
  best: {
    parameters: Record<string, number>
    in_sample_score: number
    out_of_sample_score: number
    robust_score: number
  }
  neighborhood_stability: number
  warnings: string[]
  trials: Array<{
    number: number
    parameters: Record<string, number>
    in_sample_score: number
    out_of_sample_score: number
    robust_score: number
  }>
  robustness?: {
    scenarios: Array<{
      scenario: { name: string; commission_multiplier: number; slippage_bps: number; execution_delay_bars: number }
      score: number
    }>
    walk_forward: Array<{ fold: number; test_start: string; test_end: string; score: number }>
    monte_carlo: Record<string, number>
    warnings: string[]
  }
}

export interface PortfolioPlan {
  effective_at: string
  target_weights: Record<string, number>
  target_values: Record<string, number>
  expected_turnover: number
  cash_buffer: number
  industry_exposure: Record<string, number>
  intents: Array<{
    symbol: string
    direction: string
    quantity: number
    target_weight: number
    estimated_value: number
  }>
  sent_to_broker: false
}

export interface Readiness {
  status: string
  database: string
  live_trading_enabled: boolean
  miniqmt: string
  historical_data_owner?: 'DyadResearch'
  paper_reconciled_at?: string | null
  execution?: ExecutionSnapshot
}

export interface MonitorSnapshot {
  type: 'monitor'
  timestamp: string
  live_trading_enabled: boolean
  miniqmt_connected: boolean
  paper_account: PaperSnapshot['account']
  paper_risk: PaperSnapshot['risk']
  paper_reconciled_at?: string | null
  execution?: ExecutionSnapshot
  realtime?: RealtimeStatus
  strategies?: PaperStrategyStatus[]
  account_monitor?: AccountMonitorSnapshot
}

export interface RealtimeFactor {
  symbol: string
  tick_momentum: number
  intraday_return: number
  float_market_cap?: number | null
  momentum_rank: number
  small_cap_rank: number
  composite_score: number
  updated_at?: string | null
}

export interface SimulatedDailyBar {
  symbol: string
  timestamp: string
  open: string
  high: string
  low: string
  close: string
  volume: number
  amount: string
  pre_close?: string | null
  source: string
  period: string
  adjust_mode: 'none'
  is_final: boolean
  received_at?: string | null
  updated_at?: string | null
  quality_status: string
}

export interface RealtimeStatus {
  running: boolean
  watchlist: string[]
  watchlist_count: number
  mainboard_candidate_count: number
  mainboard_candidates: string[]
  poll_interval_seconds: number
  poll_count: number
  ticks_processed: number
  last_received_at?: string | null
  last_tick_at?: string | null
  quote_age_seconds?: number | null
  market_fresh: boolean
  continuity_recovery_required: boolean
  continuity_pending_symbols: string[]
  continuity_backfill_periods: string[]
  continuity_backfill_runs: number
  continuity_last_attempt_at?: string | null
  continuity_last_recovered_at?: string | null
  continuity_failures: Array<{ symbol: string; period: string; error: string }>
  calendar_checked_through?: string | null
  rejected_daily_ticks: Record<string, number>
  rejected_intraday_ticks: Record<string, number>
  bars: SimulatedDailyBar[]
  intraday_bars: SimulatedDailyBar[]
  factors: RealtimeFactor[]
  last_error?: string | null
  orders_sent: 0
}

export interface StrategyMonitorRow {
  strategy_id: string
  name: string
  version: string
  mode: string
  status: string
  health: string
  heartbeat_age_seconds?: number | null
  last_heartbeat?: string | null
  started_at?: string | null
  stopped_at?: string | null
  runtime_seconds?: number | null
  last_activity_at?: string | null
  parameters: Record<string, unknown>
  state: Record<string, unknown>
  order_count: number
  pending_order_count: number
  trade_count: number
  risk_event_count: number
  record_count: number
  process_role: string
  recovery_status: string
  live_order_policy: 'blocked'
  error?: string | null
  can_send_live_orders: false
}

export interface StrategyMonitorSnapshot {
  timestamp: string
  total_count: number
  running_count: number
  healthy_count: number
  stale_count: number
  error_count: number
  stopped_count: number
  waiting_count: number
  mode_counts: Record<string, number>
  strategies: StrategyMonitorRow[]
  realtime: RealtimeStatus
  release_gate: {
    qmt_live_enabled: boolean
    strategy_live_order_policy: 'blocked'
    required_path: string
  }
}

export interface StrategyRuntimeDetail extends StrategyMonitorRow {
  orders: Array<Record<string, string | number | boolean | null>>
  trades: Array<Record<string, string | number | boolean | null>>
  risk_events: Array<Record<string, string | number | boolean | null>>
  audit_events: Array<Record<string, string | number | boolean | null>>
}

export interface DeliveryStatementRecord {
  trade_id: string
  order_id: string
  client_order_id?: string | null
  trade_date: string
  timestamp: string
  symbol: string
  direction: string
  business_type: string
  quantity: number
  price: string
  notional: string
  fees: string
  settlement_amount: string
  strategy_id?: string | null
  execution_mode: string
  account: string
  order_sysid?: string | null
}

export interface DeliveryStatementSnapshot {
  start_date: string
  end_date: string
  records: DeliveryStatementRecord[]
  summary: { trade_count: number; total_notional: string; total_fees: string; net_settlement: string }
  source: string
}

export interface InstrumentSearchResult {
  symbol: string
  name?: string | null
  exchange: string
  asset_type: string
  industry?: string | null
  price_tick: string
  price_decimals: number
  quantity_decimals: number
  lot_size: number
  currency: string
  money_decimals: number
}

export interface AccountMonitorSnapshot {
  connection: Record<string, unknown>
  capital: {
    total_assets?: string
    available_cash?: string
    market_value: string
    cost_value: string
    unrealized_pnl: string
    cash_ratio: number
    gross_exposure: number
    largest_position_weight: number
  }
  positions: Array<Record<string, unknown>>
  orders: Array<Record<string, string | number | boolean>>
  trades: Array<Record<string, unknown>>
  order_statistics: { total: number; active: number; by_status: Record<string, number> }
  trade_statistics: { total: number; notional: string }
  risk_switches: { kill_switch: boolean; cancel_only: boolean; no_new_positions: boolean }
  recent_risk_events?: Array<Record<string, unknown>>
}

export interface PaperSnapshot {
  mode: 'paper'
  account: {
    account_id: string
    cash: string
    available_cash: string
    frozen_cash: string
    positions_value: string
    total_assets: string
  }
  positions: Array<{
    symbol: string
    quantity: number
    available_quantity: number
    average_cost: string
    last_price: string
  }>
  orders: Array<{
    order_id: string
    symbol: string
    direction: string
    quantity: number
    filled_quantity: number
    status: string
    reject_reason?: string | null
  }>
  trades: Array<{
    trade_id: string
    symbol: string
    quantity: number
    price: string
    fees: string
    source: 'paper_match'
    execution_mode: 'paper'
    is_simulated: true
    timestamp: string
  }>
  risk: {
    global_stop: boolean
    cancel_only: boolean
    no_new_positions: boolean
    daily_order_count: number
    daily_turnover: string
    events?: Array<{ code: string; message: string; checked_at: string; details: Record<string, string> }>
  }
  last_reconciled_at?: string | null
}

export interface PaperStrategyStatus {
  strategy_id: string
  name: string
  version: string
  running: boolean
  started_at?: string | null
  stopped_at?: string | null
  last_heartbeat?: string | null
  state: Record<string, unknown>
  mode: 'paper'
  live_order_capability: false
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1',
  timeout: 30_000,
})

export async function fetchMarketSession(): Promise<MarketSession> {
  return (await api.get<MarketSession>('/monitoring/market-session')).data
}
export async function fetchXtDataCatalog(): Promise<XtDataCatalog> {
  return (await api.get<XtDataCatalog>('/market/xtdata/catalog')).data
}
export async function downloadXtData(
  payload: XtDataDownloadRequest,
): Promise<XtDataDownloadResult> {
  return (await api.post<XtDataDownloadResult>('/market/xtdata/download', payload)).data
}
export async function fetchXtDataSubscriptionStatus(): Promise<XtDataSubscriptionSnapshot> {
  return (await api.get<XtDataSubscriptionSnapshot>('/realtime/subscriptions/status')).data
}
export async function startXtDataSingleSubscription(
  symbols: string[],
  period = 'tick',
): Promise<XtDataSubscriptionSnapshot> {
  return (await api.post<XtDataSubscriptionSnapshot>('/realtime/subscriptions/single', { symbols, period })).data
}
export async function startXtDataWholeSubscription(
  markets: Array<'SH' | 'SZ'> = ['SH', 'SZ'],
): Promise<XtDataSubscriptionSnapshot> {
  return (await api.post<XtDataSubscriptionSnapshot>('/realtime/subscriptions/whole', { markets })).data
}
export async function stopXtDataSubscriptions(): Promise<XtDataSubscriptionSnapshot> {
  return (await api.delete<XtDataSubscriptionSnapshot>('/realtime/subscriptions')).data
}
export async function fetchXtDataLatestSnapshots(
  symbols: string[] = [],
  limit = 50,
): Promise<XtDataLatestSnapshots> {
  return (await api.get<XtDataLatestSnapshots>('/realtime/subscriptions/latest', {
    params: { symbols: symbols.join(','), limit },
  })).data
}
export async function fetchReadiness(): Promise<Readiness> {
  return (await api.get<Readiness>('/ready')).data
}
export function monitorWebSocketUrl(): string {
  const configured = import.meta.env.VITE_WS_BASE_URL as string | undefined
  if (configured) return `${configured.replace(/\/$/, '')}/api/v1/ws/monitor`
  const base = String(api.defaults.baseURL)
  if (/^https?:\/\//.test(base)) return `${base.replace(/^http/, 'ws')}/ws/monitor`
  const origin = window.location.origin.replace(/^http/, 'ws')
  return `${origin}${base.startsWith('/') ? base : `/${base}`}/ws/monitor`
}
export async function fetchPaperSnapshot(): Promise<PaperSnapshot> {
  return (await api.get<PaperSnapshot>('/paper/snapshot')).data
}
export async function pushPaperBar(bar: object): Promise<PaperSnapshot> {
  return (await api.post<PaperSnapshot>('/paper/market/bars', [bar])).data
}
export async function createPaperOrder(payload: object): Promise<void> {
  await api.post('/paper/orders', payload)
}
export async function fetchPaperStrategies(): Promise<PaperStrategyStatus[]> {
  return (await api.get<PaperStrategyStatus[]>('/paper/strategies')).data
}
export async function startPaperStrategy(): Promise<PaperStrategyStatus> {
  return (await api.post<PaperStrategyStatus>('/paper/strategies/start', {
    strategy_name: 'buy_and_hold', strategy_id: 'frontend-paper-buy-hold',
    parameters: { symbol: '510300.SH', quantity: 100 },
  })).data
}
export async function stopPaperStrategy(strategyId: string): Promise<PaperStrategyStatus> {
  return (await api.post<PaperStrategyStatus>(`/paper/strategies/${strategyId}/stop`)).data
}

export async function fetchExecutionSnapshot(): Promise<ExecutionSnapshot> {
  return (await api.get<ExecutionSnapshot>('/execution/status')).data
}
export async function connectExecution(): Promise<ExecutionSnapshot> {
  return (await api.post<ExecutionSnapshot>('/execution/connect')).data
}
export async function fetchLatestExecutionUniverse(): Promise<ExecutionUniverseRelease | null> {
  return (await api.get<ExecutionUniverseRelease | null>('/execution/universe/releases/latest')).data
}
export async function fetchStrategyExecutionReleases(): Promise<StrategyExecutionRelease[]> {
  return (await api.get<StrategyExecutionRelease[]>('/execution/strategy-releases')).data
}
export async function fetchStrategyBundles(): Promise<StrategyBundleCatalog> {
  return (await api.get<StrategyBundleCatalog>('/strategy-bundles')).data
}
export async function validateStrategyBundle(
  bundleName: string,
  executionTarget: 'paper' | 'qmt_sim' | 'qmt_live',
): Promise<StrategyBundleValidation> {
  return (await api.post<StrategyBundleValidation>('/strategy-bundles/validate', {
    bundle_name: bundleName,
    execution_target: executionTarget,
    actor: 'frontend-operator',
  })).data
}
export async function importStrategyBundle(
  bundleName: string,
  executionTarget: 'qmt_sim' | 'qmt_live',
  universeReleaseId: string,
): Promise<StrategyBundleValidation> {
  return (await api.post<StrategyBundleValidation>('/strategy-bundles/import', {
    bundle_name: bundleName,
    execution_target: executionTarget,
    universe_release_id: universeReleaseId,
    actor: 'frontend-operator',
  })).data
}
export async function submitQmtOrder(payload: ManualQmtOrder): Promise<Record<string, unknown>> {
  return (await api.post<Record<string, unknown>>('/execution/orders', payload)).data
}
export async function cancelQmtOrder(orderId: number, live = false): Promise<Record<string, unknown>> {
  return (await api.post<Record<string, unknown>>(`/execution/orders/${orderId}/cancel`, null, {
    params: live ? { confirm_live_cancel: true } : { confirm_simulated_order: true },
  })).data
}
export async function updateExecutionSwitches(payload: object): Promise<ExecutionSnapshot> {
  return (await api.put<ExecutionSnapshot>('/execution/switches', payload)).data
}
export async function updateExecutionRiskState(state: RiskOperatingState, reason: string): Promise<ExecutionSnapshot> {
  return (await api.put<ExecutionSnapshot>('/execution/risk-state', { state, reason, actor: 'frontend-operator' })).data
}
export async function fetchExecutionRiskPolicy(): Promise<ExecutionRiskPolicySnapshot> {
  return (await api.get<ExecutionRiskPolicySnapshot>('/execution/risk-policy')).data
}
export async function updateExecutionRiskPolicy(
  payload: ExecutionRiskPolicyValues & { actor: string },
): Promise<ExecutionSnapshot> {
  return (await api.put<ExecutionSnapshot>('/execution/risk-policy', payload)).data
}
export async function reconcileExecution(): Promise<ExecutionSnapshot> {
  return (await api.post<ExecutionSnapshot>('/execution/reconcile')).data
}
export async function fetchRealtimeStatus(): Promise<RealtimeStatus> {
  return (await api.get<RealtimeStatus>('/realtime/status')).data
}
export async function fetchStrategyMonitor(): Promise<StrategyMonitorSnapshot> {
  return (await api.get<StrategyMonitorSnapshot>('/monitoring/strategies')).data
}
export async function fetchStrategyRuntimeDetail(strategyId: string): Promise<StrategyRuntimeDetail> {
  return (await api.get<StrategyRuntimeDetail>(`/monitoring/strategies/${encodeURIComponent(strategyId)}`)).data
}
export async function fetchAccountMonitor(): Promise<AccountMonitorSnapshot> {
  return (await api.get<AccountMonitorSnapshot>('/monitoring/account')).data
}
export async function fetchDeliveryStatements(
  startDate: string,
  endDate: string,
  symbol = '',
  strategyId = '',
): Promise<DeliveryStatementSnapshot> {
  return (await api.get<DeliveryStatementSnapshot>('/monitoring/delivery-statements', {
    params: { start_date: startDate, end_date: endDate, symbol: symbol || undefined, strategy_id: strategyId || undefined },
  })).data
}
export function deliveryStatementExportUrl(startDate: string, endDate: string, symbol = ''): string {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate })
  if (symbol) params.set('symbol', symbol)
  return `${String(api.defaults.baseURL)}/monitoring/delivery-statements/export.csv?${params.toString()}`
}
export async function refreshSmallCapCandidates(limit = 50): Promise<RealtimeStatus> {
  await api.post('/realtime/mainboard-small-cap/refresh', null, { params: { limit } })
  return fetchRealtimeStatus()
}
export async function startTickMomentumSmallCap(): Promise<PaperStrategyStatus> {
  return (await api.post<PaperStrategyStatus>('/realtime/strategies/tick-momentum-small-cap/start')).data
}
