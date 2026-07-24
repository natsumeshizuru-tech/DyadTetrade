import { useCallback, useMemo, useState } from 'react'
import { useEffect } from 'react'
import { Activity, AlertTriangle, Database, Download, Radio, RefreshCw, ShieldCheck } from 'lucide-react'
import {
  downloadXtData, fetchRealtimeStatus, fetchXtDataCatalog, type RealtimeStatus,
  type SimulatedDailyBar, type XtDataCatalog, type XtDataDownloadResult,
} from '../api/client'
import { formatPrice, formatQuantity, localTime } from '../components/Format'
import { StatusBadge } from '../components/StatusBadge'
import { WorkspaceShell, type WorkspaceTool } from '../components/WorkspaceShell'
import { SubscriptionMonitor } from '../features/market/SubscriptionMonitor'
import { usePolling } from '../hooks/usePolling'

const tools: WorkspaceTool[] = [
  { id: 'overview', label: '实时总览', icon: Activity },
  { id: 'subscriptions', label: '订阅管理', icon: Radio },
  { id: 'data', label: '数据下载', icon: Database },
  { id: 'continuity', label: '连续性', icon: ShieldCheck },
]

export function MarketPage() {
  const loader = useCallback((): Promise<RealtimeStatus> => fetchRealtimeStatus(), [])
  const { data, error, loading, refresh } = usePolling(loader, 3000)
  const [activeTool, setActiveTool] = useState('overview')
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const bars = useMemo(
    () => (data?.intraday_bars ?? []).filter((bar) => !selectedSymbol || bar.symbol === selectedSymbol),
    [data?.intraday_bars, selectedSymbol],
  )
  const symbols = useMemo(
    () => [...new Set((data?.intraday_bars ?? []).map((bar) => bar.symbol))].sort(),
    [data?.intraday_bars],
  )

  const canvas = activeTool === 'subscriptions' ? (
    <div className="workspace-empty-state realtime-workspace-state">
      <Radio size={42} />
      <h3>实时订阅管理</h3>
      <p>订阅命令和最新快照位于左侧栏；历史下载请切换到“数据下载”。</p>
      <small>下载写入仅经过受控 XtData 数据面；研究查询仍由 DyadResearch 只读执行。</small>
    </div>
  ) : activeTool === 'data' ? (
    <XtDataDownloadPanel />
  ) : activeTool === 'continuity' ? (
    <ContinuityPanel status={data} />
  ) : (
    <div className="realtime-market-overview" role="region" aria-label="实时行情总览">
      <div className="workspace-summary-grid">
        <div><span>服务</span><b>{data?.running ? '运行中' : '已停止'}</b></div>
        <div><span>行情新鲜</span><b>{data?.market_fresh ? '是' : '否'}</b></div>
        <div><span>关注标的</span><b>{data?.watchlist_count ?? 0}</b></div>
        <div><span>处理 Tick</span><b>{formatQuantity(data?.ticks_processed, 0)}</b></div>
        <div><span>轮询次数</span><b>{formatQuantity(data?.poll_count, 0)}</b></div>
        <div><span>策略报单</span><b>{data?.orders_sent ?? 0}</b></div>
      </div>
      <header className="workspace-chart-heading">
        <div><strong>进程内实时 Bar</strong><span>仅展示实时服务内存状态</span></div>
        <select aria-label="筛选实时标的" value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value)}>
          <option value="">全部标的</option>
          {symbols.map((symbol) => <option value={symbol} key={symbol}>{symbol}</option>)}
        </select>
      </header>
      <div className="realtime-bars-table">
        <table>
          <thead><tr><th>标的</th><th>时间</th><th>周期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>成交量</th><th>状态</th></tr></thead>
          <tbody>{bars.slice(-80).reverse().map((bar) => <RealtimeBarRow bar={bar} key={`${bar.symbol}-${bar.period}-${bar.timestamp}`} />)}</tbody>
        </table>
        {!bars.length && <p className="empty">尚未收到实时 Bar；可在“订阅管理”启动单标的或沪深全推。</p>}
      </div>
    </div>
  )

  const context = <>
    <div className="context-tabs"><b>实时状态</b><StatusBadge label={data?.market_fresh ? 'fresh' : 'stale'} ok={Boolean(data?.market_fresh)} /></div>
    <dl className="context-list">
      <div><dt>最后接收</dt><dd>{localTime(data?.last_received_at)}</dd></div>
      <div><dt>最后 Tick</dt><dd>{localTime(data?.last_tick_at)}</dd></div>
      <div><dt>行情年龄</dt><dd>{data?.quote_age_seconds == null ? '—' : `${formatQuantity(data.quote_age_seconds, 1)} 秒`}</dd></div>
      <div><dt>轮询间隔</dt><dd>{data?.poll_interval_seconds ?? '—'} 秒</dd></div>
      <div><dt>候选标的</dt><dd>{data?.mainboard_candidate_count ?? 0}</dd></div>
      <div><dt>连续性恢复</dt><dd>{data?.continuity_recovery_required ? '需要' : '正常'}</dd></div>
      <div><dt>共享历史库</dt><dd>受控写入 / Research 只读</dd></div>
    </dl>
    {data?.last_error && <p className="negative"><AlertTriangle size={13} />{data.last_error}</p>}
  </>

  return <WorkspaceShell
    title="行情" eyebrow="MARKET" subtitle="实时订阅、进程内 Bar 与连续性监控"
    tools={tools} activeTool={activeTool} onToolChange={setActiveTool}
    toolbar={<button className="workspace-icon-button" disabled={loading} onClick={() => void refresh()} title="刷新"><RefreshCw size={18} />刷新</button>}
    canvas={canvas} context={context}
    railPanel={activeTool === 'subscriptions' ? <SubscriptionMonitor variant="sidebar" /> : undefined}
    bottomTabs={[]} activeBottomTab="" onBottomTabChange={() => undefined} bottom={null}
    hideBottom error={error}
  />
}

function XtDataDownloadPanel() {
  const [catalog, setCatalog] = useState<XtDataCatalog | null>(null)
  const [dataset, setDataset] = useState('history')
  const [symbols, setSymbols] = useState('000001.SZ')
  const [period, setPeriod] = useState('1d')
  const [startDate, setStartDate] = useState('20240101')
  const [endDate, setEndDate] = useState('')
  const [tables, setTables] = useState('Balance,Income,CashFlow,Capital')
  const [shouldImport, setShouldImport] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<XtDataDownloadResult | null>(null)

  useEffect(() => {
    void fetchXtDataCatalog().then(setCatalog).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : '读取 XtData 能力失败')
    })
  }, [])

  const selected = catalog?.datasets.find((item) => item.value === dataset)
  const run = async () => {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      setResult(await downloadXtData({
        dataset,
        symbols: symbols.split(/[\s,;]+/).filter(Boolean),
        period,
        start_date: startDate,
        end_date: endDate,
        tables: tables.split(/[\s,;]+/).filter(Boolean),
        import_to_shared_store: shouldImport && Boolean(selected?.supports_shared_import),
      }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'XtData 下载失败')
    } finally {
      setBusy(false)
    }
  }

  return <div className="xtdata-workspace">
    <header className="workspace-chart-heading">
      <div><strong>XtData 下载与共享导入</strong><span>MiniQMT 缓存 → DyadResearch / DyadTetrade 共用 SQLite</span></div>
      <StatusBadge label={catalog?.capabilities.connected ? 'MiniQMT 已连接' : 'MiniQMT 未连接'} ok={Boolean(catalog?.capabilities.connected)} />
    </header>
    <div className="xtdata-form-grid">
      <label>数据类别<select value={dataset} onChange={(event) => setDataset(event.target.value)}>{catalog?.datasets.map((item) => <option key={item.value} value={item.value} disabled={!item.available}>{item.label}{item.available ? '' : '（当前版本无接口）'}</option>)}</select></label>
      {selected?.requires_symbols && <label>合约代码<input value={symbols} onChange={(event) => setSymbols(event.target.value.toUpperCase())} placeholder="000001.SZ, 510300.SH" /></label>}
      {selected?.requires_period && <label>周期<select value={period} onChange={(event) => setPeriod(event.target.value)}>{catalog?.periods.map((item) => <option key={item.value} value={item.value}>{item.group} · {item.label}{item.reported_by_client ? '' : ' *'}</option>)}</select></label>}
      {(dataset === 'history' || dataset === 'financial') && <><label>开始日期<input value={startDate} onChange={(event) => setStartDate(event.target.value.replace(/\D/g, '').slice(0, 8))} /></label><label>结束日期<input value={endDate} onChange={(event) => setEndDate(event.target.value.replace(/\D/g, '').slice(0, 8))} placeholder="留空为今天" /></label></>}
      {dataset === 'financial' && <label>财务表<input value={tables} onChange={(event) => setTables(event.target.value)} /></label>}
      <label className="xtdata-check"><input type="checkbox" checked={shouldImport} disabled={!selected?.supports_shared_import} onChange={(event) => setShouldImport(event.target.checked)} /> 导入共享 SQLite</label>
      <button type="button" className="xtdata-run" disabled={busy || !selected?.available} onClick={() => void run()}><Download size={17} />{busy ? '下载导入中…' : '执行下载 / 导入'}</button>
    </div>
    {error && <p className="xtdata-error"><AlertTriangle size={15} />{error}</p>}
    <div className="xtdata-result-grid">
      <section><h3>客户端能力</h3><dl className="context-list"><div><dt>动态上报周期</dt><dd>{catalog?.capabilities.available_periods.length ?? 0}</dd></div><div><dt>板块数量</dt><dd>{catalog?.capabilities.sector_count ?? 0}</dd></div><div><dt>共享导入</dt><dd>{catalog?.shared_import_periods.join(' / ') || '—'}</dd></div><div><dt>Level2 历史</dt><dd>不支持跨日下载</dd></div></dl></section>
      <section><h3>执行结果</h3><pre>{result ? JSON.stringify(result, null, 2) : '等待执行；未导入的特色周期仍保存在 MiniQMT 本地缓存。'}</pre></section>
    </div>
  </div>
}

function RealtimeBarRow({ bar }: { bar: SimulatedDailyBar }) {
  return <tr>
    <td>{bar.symbol}</td><td>{localTime(bar.timestamp)}</td><td>{bar.period}</td>
    <td>{formatPrice(bar.open)}</td><td>{formatPrice(bar.high)}</td><td>{formatPrice(bar.low)}</td><td>{formatPrice(bar.close)}</td>
    <td>{formatQuantity(bar.volume, 0)}</td><td>{bar.is_final ? 'final' : 'forming'}</td>
  </tr>
}

function ContinuityPanel({ status }: { status: RealtimeStatus | null }) {
  return <div className="continuity-panel">
    <div className="workspace-summary-grid">
      <div><span>恢复状态</span><b>{status?.continuity_recovery_required ? '待恢复' : '正常'}</b></div>
      <div><span>待恢复标的</span><b>{status?.continuity_pending_symbols.length ?? 0}</b></div>
      <div><span>回补运行</span><b>{status?.continuity_backfill_runs ?? 0}</b></div>
      <div><span>失败项</span><b>{status?.continuity_failures.length ?? 0}</b></div>
      <div><span>日历检查至</span><b>{status?.calendar_checked_through ?? '—'}</b></div>
      <div><span>最后恢复</span><b>{localTime(status?.continuity_last_recovered_at)}</b></div>
    </div>
    <section className="strategy-safety-notice"><ShieldCheck size={14} />连续性检查只消费 DyadResearch 的只读历史契约；历史写入仅允许通过“数据下载”的受控 XtData 导入。</section>
    {(status?.continuity_failures ?? []).map((failure) => <p className="negative" key={`${failure.symbol}-${failure.period}`}><AlertTriangle size={13} />{failure.symbol} · {failure.period} · {failure.error}</p>)}
    {!status?.continuity_failures.length && <p className="empty">没有连续性失败。</p>}
  </div>
}
