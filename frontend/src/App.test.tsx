import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import App from './App'

const { execution, realtime, subscription, startWholeSubscription, stopSubscriptions } = vi.hoisted(() => ({
  execution: {
    execution_mode: 'disabled', connected: false, subscribed: false, account_healthy: false,
    engine_online: false, positions: [], orders: [], trades: [],
    risk_state: { state: 'RECOVERY', reason: 'engine unavailable' },
  },
  realtime: {
    running: true, watchlist: [], watchlist_count: 0, mainboard_candidate_count: 0,
    mainboard_candidates: [], poll_interval_seconds: 2, poll_count: 1, ticks_processed: 0,
    market_fresh: false, bars: [], intraday_bars: [], factors: [], orders_sent: 0,
  },
  subscription: {
    active: true, subscriptions: [{ mode: 'single', symbol: '600519.SH', period: 'tick' }],
    subscription_count: 1, latest_symbol_count: 1, received_batches: 2, received_symbols: 2,
    dropped_batches: 0, queue_depth: 0, last_received_at: '2026-07-13T01:00:00Z',
    quote_age_seconds: 1, fresh: true, last_error: null,
  },
  startWholeSubscription: vi.fn().mockResolvedValue(undefined),
  stopSubscriptions: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('./api/client', () => ({
  fetchExecutionSnapshot: vi.fn().mockResolvedValue(execution),
  fetchExecutionRiskPolicy: vi.fn().mockResolvedValue({
    current: {
      max_order_notional: '5000', max_daily_notional: '25000', max_orders_per_minute: 3,
      max_symbol_position_notional: '15000', max_total_position_notional: '80000',
      max_total_position_ratio: '0.70', max_daily_orders: 50,
      max_strategy_daily_notional: '10000',
    },
    ceiling: {
      max_order_notional: '10000', max_daily_notional: '50000', max_orders_per_minute: 5,
      max_symbol_position_notional: '20000', max_total_position_notional: '100000',
      max_total_position_ratio: '0.80', max_daily_orders: 100,
      max_strategy_daily_notional: '20000',
    },
    editable: true, requires_restricted_state: true, error: null,
  }),
  updateExecutionRiskPolicy: vi.fn().mockResolvedValue(execution),
  fetchAccountMonitor: vi.fn().mockResolvedValue({
    connection: {}, capital: { total_assets: '1000000', available_cash: '1000000', market_value: '0', cost_value: '0', unrealized_pnl: '0', cash_ratio: 1, gross_exposure: 0, largest_position_weight: 0 },
    positions: [], orders: [], trades: [], order_statistics: { total: 0, active: 0, by_status: {} }, trade_statistics: { total: 0, notional: '0' }, risk_switches: { kill_switch: false, cancel_only: false, no_new_positions: false },
  }),
  fetchRealtimeStatus: vi.fn().mockResolvedValue(realtime),
  fetchStrategyMonitor: vi.fn().mockResolvedValue({
    timestamp: '2026-07-13T00:00:00Z', total_count: 2, running_count: 1,
    healthy_count: 1, stale_count: 0, error_count: 0, stopped_count: 1, waiting_count: 0,
    mode_counts: { paper: 2 },
    strategies: [
      { strategy_id: 'alpha-paper', name: 'alpha_rotation', version: '1.0.0', mode: 'paper', status: 'running', health: 'healthy', heartbeat_age_seconds: 2, last_heartbeat: '2026-07-13T00:00:00Z', parameters: {}, state: {}, order_count: 1, pending_order_count: 1, trade_count: 0, risk_event_count: 0, record_count: 0, process_role: 'paper-strategy-runner', recovery_status: 'attached', live_order_policy: 'blocked', can_send_live_orders: false },
      { strategy_id: 'beta-paper', name: 'beta_momentum', version: '1.0.0', mode: 'paper', status: 'stopped', health: 'stopped', heartbeat_age_seconds: 20, last_heartbeat: '2026-07-13T00:00:00Z', parameters: {}, state: {}, order_count: 0, pending_order_count: 0, trade_count: 0, risk_event_count: 0, record_count: 0, process_role: 'paper-strategy-runner', recovery_status: 'not_required', live_order_policy: 'blocked', can_send_live_orders: false },
    ],
    realtime,
    release_gate: { qmt_live_enabled: false, strategy_live_order_policy: 'blocked', required_path: 'strategy -> order intent -> risk -> OMS -> gateway' },
  }),
  fetchStrategyExecutionReleases: vi.fn().mockResolvedValue([]),
  fetchLatestExecutionUniverse: vi.fn().mockResolvedValue(null),
  fetchStrategyBundles: vi.fn().mockResolvedValue({ inbox: 'data/strategy_bundles/inbox', bundles: [] }),
  importStrategyBundle: vi.fn(),
  validateStrategyBundle: vi.fn(),
  fetchReadiness: vi.fn().mockResolvedValue({ status: 'ready', database: 'ok', live_trading_enabled: false, miniqmt: 'connected', backtest_tasks: {} }),
  fetchDeliveryStatements: vi.fn().mockResolvedValue({ start_date: '2026-06-13', end_date: '2026-07-13', records: [], summary: { trade_count: 0, total_notional: '0', total_fees: '0', net_settlement: '0' }, source: 'broker_settlements_only' }),
  deliveryStatementExportUrl: vi.fn().mockReturnValue('/api/v1/monitoring/delivery-statements/export.csv'),
  fetchStrategyRuntimeDetail: vi.fn().mockImplementation(async (strategyId: string) => ({ strategy_id: strategyId, name: strategyId === 'alpha-paper' ? 'alpha_rotation' : 'beta_momentum', version: '1.0.0', mode: 'paper', status: strategyId === 'alpha-paper' ? 'running' : 'stopped', health: strategyId === 'alpha-paper' ? 'healthy' : 'stopped', heartbeat_age_seconds: 2, last_heartbeat: '2026-07-13T00:00:00Z', runtime_seconds: 120, last_activity_at: '2026-07-13T00:00:00Z', parameters: {}, state: {}, order_count: 1, pending_order_count: 1, trade_count: 0, risk_event_count: 0, record_count: 0, process_role: 'paper-strategy-runner', recovery_status: strategyId === 'alpha-paper' ? 'attached' : 'not_required', live_order_policy: 'blocked', can_send_live_orders: false, orders: [], trades: [], risk_events: [], audit_events: [] })),
  fetchXtDataSubscriptionStatus: vi.fn().mockResolvedValue(subscription),
  fetchXtDataLatestSnapshots: vi.fn().mockResolvedValue({ '600519.SH': { lastPrice: 1688, volume: 10, amount: 16880, received_at: '2026-07-13T01:00:00Z' } }),
  startXtDataSingleSubscription: vi.fn().mockResolvedValue(subscription),
  startXtDataWholeSubscription: startWholeSubscription,
  stopXtDataSubscriptions: stopSubscriptions,
  cancelQmtOrder: vi.fn(), connectExecution: vi.fn(), reconcileExecution: vi.fn(), submitQmtOrder: vi.fn(), updateExecutionRiskState: vi.fn(),
}))

test('uses four trading-only primary entries and trading cockpit is the default', async () => {
  render(<App />)
  expect(await screen.findByText('交易驾驶舱')).toBeInTheDocument()
  const navigation = screen.getByRole('navigation', { name: '一级导航' })
  expect(navigation.querySelectorAll('button')).toHaveLength(4)
  expect(screen.queryByRole('button', { name: '研究' })).not.toBeInTheDocument()
  expect(screen.queryByText('QMT 模拟柜台手工委托')).not.toBeInTheDocument()
  expect(screen.getByText('实盘受安全门禁保护')).toBeInTheDocument()
})

test('navigates market strategy and system without research pages', async () => {
  render(<App />)
  await screen.findByText('交易驾驶舱')
  fireEvent.click(screen.getByRole('button', { name: '行情' }))
  expect(await screen.findByRole('heading', { name: '行情' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '策略' }))
  expect(await screen.findByRole('heading', { name: '策略' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '系统' }))
  await waitFor(() => expect(screen.getByRole('heading', { name: '系统' })).toBeInTheDocument())
})

test('strategy page monitors multiple runtimes and keeps live orders blocked', async () => {
  render(<App />)
  await screen.findByText('交易驾驶舱')
  fireEvent.click(screen.getByRole('button', { name: '策略' }))
  expect(await screen.findByRole('region', { name: '多策略运行监控' })).toBeInTheDocument()
  expect((await screen.findAllByText('alpha_rotation')).length).toBeGreaterThan(0)
  expect(await screen.findByText('beta_momentum')).toBeInTheDocument()
  expect(await screen.findByText(/策略不能直连 XtTrader/)).toBeInTheDocument()
  expect(screen.getByText('实盘直连')).toBeInTheDocument()
  expect(screen.getAllByText('禁止').length).toBeGreaterThan(0)
})

test('controls XtData single and whole-market subscriptions from market page', async () => {
  render(<App />)
  await screen.findByText('交易驾驶舱')
  fireEvent.click(screen.getByRole('button', { name: '行情' }))
  fireEvent.click(await screen.findByRole('button', { name: '订阅管理' }))
  expect(await screen.findByRole('region', { name: 'XtData 行情订阅' })).toBeInTheDocument()
  expect(await screen.findByText('订阅运行中')).toBeInTheDocument()
  expect(screen.getByText('600519.SH', { selector: 'td' })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '启动沪深全推' }))
  await waitFor(() => expect(startWholeSubscription).toHaveBeenCalledWith(['SH', 'SZ']))

  fireEvent.click(screen.getByRole('button', { name: '停止全部订阅' }))
  fireEvent.click(screen.getByRole('button', { name: '确认停止订阅' }))
  await waitFor(() => expect(stopSubscriptions).toHaveBeenCalledOnce())
})

test('trading cockpit exposes account overview, delivery statements and strategy runtime panels without a discretionary chart', async () => {
  render(<App />)
  expect(await screen.findByLabelText('DyadTetrade 交易终端')).toBeInTheDocument()
  expect(screen.queryByLabelText('图表工具')).not.toBeInTheDocument()
  expect(screen.getByRole('region', { name: '交易控制' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '账户资金总览' })).toBeInTheDocument()
  expect(screen.getByText('可用资金')).toBeInTheDocument()
  expect(screen.getByText('持仓浮盈亏')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '交易已关闭' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: '告警' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '回放' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '日线' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '交割单' }))
  expect(await screen.findByRole('button', { name: '查询' })).toBeInTheDocument()
  expect(screen.getByText('导出 CSV')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /策略详情/ }))
  expect((await screen.findAllByText('alpha_rotation')).length).toBeGreaterThan(0)
})
