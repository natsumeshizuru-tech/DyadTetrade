import { useCallback, useEffect, useState } from 'react'
import {
  Activity, Download, Landmark, RefreshCw, Search, ShieldCheck, TrendingUp, WalletCards,
} from 'lucide-react'
import {
  cancelQmtOrder, connectExecution, deliveryStatementExportUrl, fetchAccountMonitor,
  fetchDeliveryStatements, fetchExecutionSnapshot, fetchRealtimeStatus, fetchStrategyMonitor,
  fetchStrategyRuntimeDetail, fetchExecutionRiskPolicy, reconcileExecution,
  updateExecutionRiskPolicy, updateExecutionRiskState,
  type AccountMonitorSnapshot, type DeliveryStatementSnapshot, type ExecutionSnapshot,
  type ExecutionRiskPolicySnapshot, type ExecutionRiskPolicyValues, type RealtimeStatus,
  type StrategyMonitorRow, type StrategyMonitorSnapshot,
  type StrategyRuntimeDetail,
} from '../api/client'
import { ConfirmAction } from '../components/ConfirmAction'
import { DataTable, type Column } from '../components/DataTable'
import { ExecutionControls } from '../components/ExecutionControls'
import { formatAverageCost, formatMoney, formatPercent, formatPrice, formatQuantity, localTime } from '../components/Format'
import { StatusBadge } from '../components/StatusBadge'
import { usePolling } from '../hooks/usePolling'

interface CockpitData {
  execution: ExecutionSnapshot
  account: AccountMonitorSnapshot
  realtime: RealtimeStatus
  strategies: StrategyMonitorSnapshot
  riskPolicy: ExecutionRiskPolicySnapshot
}

type GenericRow = Record<string, unknown>
type DetailPanel = 'positions' | 'orders' | 'trades' | 'delivery' | 'strategies' | 'risk'

const display = (row: GenericRow, key: string): string => String(row[key] ?? '—')
const numberValue = (row: GenericRow, key: string): number => Number(row[key] ?? 0)
const isoDay = (date: Date): string => date.toISOString().slice(0, 10)
const DEFAULT_DELIVERY_END = isoDay(new Date())
const DEFAULT_DELIVERY_START = isoDay(new Date(new Date().getTime() - 30 * 86400000))

export function TradingPage() {
  const loader = useCallback(async (): Promise<CockpitData> => {
    const [execution, account, realtime, strategies, riskPolicy] = await Promise.all([
      fetchExecutionSnapshot(), fetchAccountMonitor(), fetchRealtimeStatus(), fetchStrategyMonitor(), fetchExecutionRiskPolicy(),
    ])
    return { execution, account, realtime, strategies, riskPolicy }
  }, [])
  const { data, error, loading, refresh } = usePolling(loader, 2000)
  const [activePanel, setActivePanel] = useState<DetailPanel>('positions')
  const [message, setMessage] = useState('')
  const [controlBusy, setControlBusy] = useState(false)
  const [delivery, setDelivery] = useState<DeliveryStatementSnapshot | null>(null)
  const [deliveryStart, setDeliveryStart] = useState(DEFAULT_DELIVERY_START)
  const [deliveryEnd, setDeliveryEnd] = useState(DEFAULT_DELIVERY_END)
  const [strategyDetail, setStrategyDetail] = useState<StrategyRuntimeDetail | null>(null)

  useEffect(() => {
    if (activePanel !== 'delivery') return
    void fetchDeliveryStatements(deliveryStart, deliveryEnd)
      .then(setDelivery)
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : '交割单查询失败'))
  }, [activePanel, deliveryEnd, deliveryStart])

  if (loading && !data) return <p className="empty terminal-loading">正在读取真实账户、持仓与策略状态…</p>
  if (!data) return <div className="alert">交易驾驶舱不可用：{error}</div>

  const { execution, account, realtime, strategies } = data
  const risk = execution.risk_state ?? { state: 'RECOVERY', reason: '交易引擎未返回状态' }
  const positionRows = execution.positions as GenericRow[]
  const orderRows = execution.orders as GenericRow[]
  const tradeRows = execution.trades as GenericRow[]
  const activeOrders = execution.orders.filter((row) => !['FILLED', 'CANCELLED', 'REJECTED'].includes(String(row.status ?? '')))
  const availableCash = execution.assets?.available_cash ?? account.capital.available_cash
  const frozenCash = execution.assets?.frozen_cash ?? '0'
  const marketValue = account.capital.market_value
  const reportedTotalAssets = Number(execution.assets?.total_assets ?? account.capital.total_assets ?? 0)
  const calculatedTotalAssets = Number(availableCash ?? 0) + Number(marketValue ?? 0)
  const assetFallback = reportedTotalAssets <= 0 && calculatedTotalAssets > 0
  const totalAssets = assetFallback ? calculatedTotalAssets : reportedTotalAssets
  const unrealizedPnl = account.capital.unrealized_pnl
  const pnlPositive = Number(unrealizedPnl) >= 0
  const accountReady = Boolean(execution.engine_online && execution.connected && execution.subscribed && execution.account_healthy)
  const grossExposure = totalAssets > 0 ? Number(marketValue) / totalAssets : account.capital.gross_exposure
  const cashRatio = totalAssets > 0 ? Number(availableCash) / totalAssets : account.capital.cash_ratio
  const largestPositionValue = Math.max(0, ...account.positions.map((row) => Number(row.market_value ?? 0)))
  const largestPositionWeight = totalAssets > 0 ? largestPositionValue / totalAssets : account.capital.largest_position_weight

  const runControlAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setControlBusy(true)
    try {
      await action()
      setMessage(successMessage)
      await refresh()
    } catch (reasonValue) {
      setMessage(reasonValue instanceof Error ? reasonValue.message : '交易控制操作失败')
    } finally {
      setControlBusy(false)
    }
  }

  const openStrategy = async (row: StrategyMonitorRow) => {
    setActivePanel('strategies')
    try { setStrategyDetail(await fetchStrategyRuntimeDetail(row.strategy_id)) }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : '策略详情加载失败') }
  }

  const saveRiskPolicy = async (riskDraft: ExecutionRiskPolicyValues) => {
    await runControlAction(
      () => updateExecutionRiskPolicy({ ...riskDraft, actor: 'frontend-operator' }),
      '风控限额已更新并写入审计记录。',
    )
  }

  const positionColumns: Array<Column<GenericRow>> = [
    { key: 'symbol', title: '标的', render: (row) => display(row, 'symbol') },
    { key: 'quantity', title: '持仓数量', render: (row) => formatQuantity(row.quantity) },
    { key: 'available', title: '可用数量', render: (row) => formatQuantity(row.available_quantity) },
    { key: 'cost', title: '持仓成本', render: (row) => formatAverageCost(row.average_cost) },
    { key: 'last', title: '最新价', render: (row) => formatPrice(row.last_price) },
    { key: 'value', title: '持仓市值', render: (row) => formatMoney(numberValue(row, 'market_value')) },
  ]
  const orderColumns: Array<Column<GenericRow>> = [
    { key: 'time', title: '更新时间', render: (row) => localTime(row.updated_at) },
    { key: 'symbol', title: '标的', render: (row) => display(row, 'symbol') },
    { key: 'direction', title: '方向', render: (row) => display(row, 'direction') },
    { key: 'status', title: '状态', render: (row) => display(row, 'status') },
    { key: 'quantity', title: '委托 / 已成', render: (row) => `${display(row, 'quantity')} / ${display(row, 'filled_quantity')}` },
    { key: 'action', title: '操作', render: (row) => row.qmt_order_id ? <ConfirmAction label="撤单" onConfirm={async () => { await cancelQmtOrder(Number(row.qmt_order_id)); await refresh() }} /> : '—' },
  ]
  const tradeColumns: Array<Column<GenericRow>> = [
    { key: 'time', title: '成交时间', render: (row) => localTime(row.timestamp) },
    { key: 'symbol', title: '标的', render: (row) => display(row, 'symbol') },
    { key: 'direction', title: '方向', render: (row) => display(row, 'direction') },
    { key: 'quantity', title: '数量', render: (row) => display(row, 'quantity') },
    { key: 'price', title: '成交价', render: (row) => formatPrice(row.price) },
  ]
  const deliveryColumns: Array<Column<DeliveryStatementSnapshot['records'][number]>> = [
    { key: 'time', title: '成交时间', render: (row) => localTime(row.timestamp) },
    { key: 'symbol', title: '证券', render: (row) => row.symbol },
    { key: 'business', title: '业务', render: (row) => row.business_type },
    { key: 'quantity', title: '数量', render: (row) => row.quantity },
    { key: 'price', title: '成交价', render: (row) => formatPrice(row.price) },
    { key: 'notional', title: '成交金额', render: (row) => formatMoney(row.notional) },
    { key: 'fees', title: '费用', render: (row) => formatMoney(row.fees) },
    { key: 'settlement', title: '清算金额', render: (row) => <span className={Number(row.settlement_amount) >= 0 ? 'positive' : 'negative'}>{formatMoney(row.settlement_amount)}</span> },
    { key: 'mode', title: '来源', render: (row) => row.execution_mode },
  ]

  return <section className="trading-overview" aria-label="DyadTetrade 交易终端">
    <header className="cockpit-header">
      <div className="cockpit-heading">
        <span>LIVE OPERATIONS</span>
        <div><h1>交易驾驶舱</h1><small>真实账户资金、持仓、策略与风控总览</small></div>
      </div>
      <div className="cockpit-actions">
        <button className="cockpit-refresh" disabled={loading} onClick={() => void refresh()} title="刷新账户总览"><RefreshCw size={18} />刷新</button>
        <ExecutionControls
          execution={execution}
          risk={risk}
          busy={controlBusy}
          onActivate={() => runControlAction(connectExecution, execution.execution_mode === 'qmt_live' ? '实盘账户已连接并完成对账。' : '模拟账户已连接并完成对账。')}
          onReconcile={() => runControlAction(reconcileExecution, '账户、委托与成交已完成对账。')}
          onHalt={() => runControlAction(() => updateExecutionRiskState('HALTED', 'operator activated Kill Switch'), 'Kill Switch 已触发，新委托已停止。')}
        />
      </div>
    </header>
    {(error || message) && <div className={error ? 'terminal-message error' : 'terminal-message'}>{error || message}<button onClick={() => setMessage('')}>关闭</button></div>}

    <main className="cockpit-scroll">
      <section className="account-overview-card" aria-label="账户资金总览">
        <div className="section-heading">
          <div><span>QMT ACCOUNT</span><h2>账户 {execution.account ?? '—'}</h2></div>
          <div className="account-badges"><StatusBadge label={execution.execution_mode === 'qmt_live' ? '实盘流程' : execution.execution_mode} ok={execution.execution_mode === 'qmt_live'} /><StatusBadge label={accountReady ? '账户在线' : '账户未就绪'} ok={accountReady} /></div>
        </div>
        <div className="account-metric-grid">
          <article className="primary-metric"><WalletCards /><span>总资产</span><strong>{formatMoney(totalAssets)}</strong><small>{assetFallback ? '券商总资产为 0，按持仓市值 + 可用资金估算' : '账户资产实时快照'}</small></article>
          <article><Landmark /><span>可用资金</span><strong>{formatMoney(availableCash)}</strong><small>冻结 {formatMoney(frozenCash)}</small></article>
          <article><TrendingUp /><span>持仓市值</span><strong>{formatMoney(marketValue)}</strong><small>{positionRows.length} 个持仓标的</small></article>
          <article className={pnlPositive ? 'metric-positive' : 'metric-negative'}><Activity /><span>持仓浮盈亏</span><strong>{formatMoney(unrealizedPnl)}</strong><small>成本 {formatMoney(account.capital.cost_value)}</small></article>
          <article><ShieldCheck /><span>总敞口</span><strong>{formatPercent(grossExposure)}</strong><small>最大单仓 {formatPercent(largestPositionWeight)}</small></article>
          <article><WalletCards /><span>现金占比</span><strong>{formatPercent(cashRatio)}</strong><small>仅使用账户真实可用资金</small></article>
        </div>
      </section>

      <section className="operations-grid">
        <article className="operations-card">
          <div className="section-heading compact"><div><span>EXECUTION</span><h2>交易与风控状态</h2></div><StatusBadge label={risk.state} ok={risk.state === 'NORMAL'} tone={risk.state === 'HALTED' ? 'danger' : 'warning'} /></div>
          <dl className="operations-status-list">
            <div><dt>交易引擎</dt><dd className={execution.engine_online ? 'positive' : 'negative'}>{execution.engine_online ? '运行中' : '离线'}</dd></div>
            <div><dt>QMT 连接 / 订阅</dt><dd>{execution.connected ? '已连接' : '未连接'} / {execution.subscribed ? '已订阅' : '未订阅'}</dd></div>
            <div><dt>行情状态</dt><dd className={realtime.market_fresh ? 'positive' : 'negative'}>{realtime.market_fresh ? '实时' : '陈旧'} · {realtime.quote_age_seconds == null ? '—' : `${formatQuantity(realtime.quote_age_seconds, 1)} 秒`}</dd></div>
            <div><dt>活动委托</dt><dd>{activeOrders.length} 笔</dd></div>
            <div><dt>最后对账</dt><dd>{localTime(execution.last_reconciled_at)}</dd></div>
            <div><dt>执行股票池</dt><dd>{execution.execution_universe?.symbol_count ?? '—'} 只</dd></div>
          </dl>
          <p className="risk-reason">{risk.reason || '所有交易风控检查正常。'}</p>
        </article>

        <article className="operations-card strategy-overview-card">
          <div className="section-heading compact"><div><span>STRATEGIES</span><h2>策略运行概况</h2></div><StatusBadge label={`${strategies.running_count} 个运行中`} ok={strategies.running_count > 0 && strategies.error_count === 0} /></div>
          <div className="strategy-summary-metrics">
            <span><small>策略总数</small><b>{strategies.total_count}</b></span>
            <span><small>健康运行</small><b>{strategies.healthy_count}</b></span>
            <span><small>等待 / 停止</small><b>{strategies.waiting_count} / {strategies.stopped_count}</b></span>
            <span><small>异常</small><b className={strategies.error_count ? 'negative' : ''}>{strategies.error_count}</b></span>
          </div>
          <div className="strategy-overview-list">
            {strategies.strategies.slice(0, 5).map((row) => <button key={row.strategy_id} onClick={() => void openStrategy(row)}><span><Activity size={17} /><b>{row.name}</b><small>{row.mode} · {row.status}</small></span><span><StatusBadge label={row.health} ok={row.health === 'healthy'} /><small>心跳 {localTime(row.last_heartbeat)}</small></span></button>)}
            {!strategies.strategies.length && <p className="empty">当前没有策略运行实例。</p>}
          </div>
        </article>
      </section>

      <section className="portfolio-card">
        <div className="portfolio-tabs" role="tablist" aria-label="账户交易明细">
          <button className={activePanel === 'positions' ? 'active' : ''} onClick={() => setActivePanel('positions')}>持仓 <span>{positionRows.length}</span></button>
          <button className={activePanel === 'orders' ? 'active' : ''} onClick={() => setActivePanel('orders')}>委托 <span>{activeOrders.length}</span></button>
          <button className={activePanel === 'trades' ? 'active' : ''} onClick={() => setActivePanel('trades')}>成交 <span>{tradeRows.length}</span></button>
          <button className={activePanel === 'delivery' ? 'active' : ''} onClick={() => setActivePanel('delivery')}>交割单</button>
          <button className={activePanel === 'strategies' ? 'active' : ''} onClick={() => setActivePanel('strategies')}>策略详情 <span>{strategies.running_count}</span></button>
          <button className={activePanel === 'risk' ? 'active' : ''} onClick={() => setActivePanel('risk')}>风控参数</button>
        </div>
        <div className="portfolio-content">
          {activePanel === 'positions' && <DataTable rows={positionRows} columns={positionColumns} empty="当前账户无持仓" />}
          {activePanel === 'orders' && <DataTable rows={orderRows.slice().reverse()} columns={orderColumns} empty="当前无委托" />}
          {activePanel === 'trades' && <DataTable rows={tradeRows.slice().reverse()} columns={tradeColumns} empty="当前无成交" />}
          {activePanel === 'delivery' && <div className="delivery-panel"><div className="delivery-toolbar"><label>开始日期<input type="date" value={deliveryStart} onChange={(event) => setDeliveryStart(event.target.value)} /></label><label>结束日期<input type="date" value={deliveryEnd} onChange={(event) => setDeliveryEnd(event.target.value)} /></label><button onClick={() => void fetchDeliveryStatements(deliveryStart, deliveryEnd).then(setDelivery)}><Search size={17} />查询</button><a className="button-link" href={deliveryStatementExportUrl(deliveryStart, deliveryEnd)}><Download size={17} />导出 CSV</a><span>交割 {delivery?.summary.trade_count ?? 0} · 费用 {formatMoney(delivery?.summary.total_fees ?? 0)} · 净清算 {formatMoney(delivery?.summary.net_settlement ?? 0)}</span></div><DataTable rows={delivery?.records ?? []} columns={deliveryColumns} empty="券商未返回交割事实；Paper、回测和普通成交不会显示在这里" /></div>}
          {activePanel === 'strategies' && <div className="strategy-runtime-layout overview-runtime-layout"><aside>{strategies.strategies.map((row) => <button key={row.strategy_id} className={strategyDetail?.strategy_id === row.strategy_id ? 'active' : ''} onClick={() => void openStrategy(row)}><span><Activity size={17} />{row.name}</span><StatusBadge label={row.health} ok={row.health === 'healthy'} /><small>{row.mode} · 心跳 {localTime(row.last_heartbeat)}</small></button>)}{!strategies.strategies.length && <p className="empty">暂无策略运行实例</p>}</aside><section>{strategyDetail ? <><header><div><h3>{strategyDetail.name}</h3><span>{strategyDetail.strategy_id} · v{strategyDetail.version}</span></div><StatusBadge label={strategyDetail.health} ok={strategyDetail.health === 'healthy'} /></header><div className="strategy-detail-metrics"><span>模式<b>{strategyDetail.mode}</b></span><span>状态<b>{strategyDetail.status}</b></span><span>心跳年龄<b>{formatQuantity(strategyDetail.heartbeat_age_seconds, 1)} 秒</b></span><span>实盘直连<b>禁止</b></span></div><div className="strategy-detail-grid"><div><h4>参数</h4><pre>{JSON.stringify(strategyDetail.parameters, null, 2)}</pre></div><div><h4>状态</h4><pre>{JSON.stringify(strategyDetail.state, null, 2)}</pre></div><div><h4>最近审计事件</h4><pre>{strategyDetail.audit_events.slice(-20).map((event) => JSON.stringify(event)).join('\n') || '暂无审计事件'}</pre></div><div><h4>执行统计</h4><p>委托 {strategyDetail.orders.length} · 成交 {strategyDetail.trades.length} · 风控事件 {strategyDetail.risk_events.length}</p></div></div></> : <p className="empty">选择策略查看心跳、参数、状态、审计、委托、成交和风控事件。</p>}</section></div>}
          {activePanel === 'risk' && <RiskPolicyPanel snapshot={data.riskPolicy} onSave={(value) => void saveRiskPolicy(value)} busy={controlBusy} state={risk.state} />}
        </div>
      </section>
    </main>
  </section>
}

const RISK_POLICY_FIELDS: Array<{ key: keyof ExecutionRiskPolicyValues; label: string; step: string }> = [
  { key: 'max_order_notional', label: '单笔名义金额', step: '100' },
  { key: 'max_daily_notional', label: '账户单日名义金额', step: '100' },
  { key: 'max_orders_per_minute', label: '每分钟委托数', step: '1' },
  { key: 'max_symbol_position_notional', label: '单标的持仓金额', step: '100' },
  { key: 'max_total_position_notional', label: '总持仓金额', step: '100' },
  { key: 'max_total_position_ratio', label: '总持仓比例', step: '0.01' },
  { key: 'max_daily_orders', label: '每日委托数', step: '1' },
  { key: 'max_strategy_daily_notional', label: '单策略日限额', step: '100' },
]

function RiskPolicyPanel({ snapshot, onSave, busy, state }: {
  snapshot: ExecutionRiskPolicySnapshot
  onSave: (value: ExecutionRiskPolicyValues) => void
  busy: boolean
  state: string
}) {
  const [draft, setDraft] = useState<ExecutionRiskPolicyValues>(snapshot.current)
  const restricted = state === 'HALTED' || state === 'RECOVERY'
  return <div className="risk-policy-panel">
    <header><div><ShieldCheck size={20} /><span><b>运行时风控限额</b><small>只允许在启动配置上限内调整；修改会持久化并写入审计。</small></span></div><StatusBadge label={restricted ? `${state} · 可编辑` : `${state} · 只读`} ok={restricted} tone={restricted ? 'warning' : undefined} /></header>
    {snapshot.error && <p className="risk-policy-warning">交易引擎不可用：{snapshot.error}</p>}
    <div className="risk-policy-grid">{RISK_POLICY_FIELDS.map((field) => <label key={field.key}><span>{field.label}</span><input type="number" min="0" step={field.step} value={draft[field.key]} disabled={!snapshot.editable || !restricted || busy} onChange={(event) => setDraft({ ...draft, [field.key]: field.key === 'max_orders_per_minute' || field.key === 'max_daily_orders' ? Number(event.target.value) : event.target.value })} /><small>启动上限 {snapshot.ceiling[field.key]}</small></label>)}</div>
    <div className="risk-policy-actions"><p>若当前为 NORMAL，请先触发 Kill Switch 或进入 RECOVERY，再保存新限额。</p><button disabled={!snapshot.editable || !restricted || busy} onClick={() => onSave(draft)}>{busy ? '保存中…' : '保存风控参数'}</button></div>
  </div>
}
