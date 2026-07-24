import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, PackageCheck, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import {
  fetchLatestExecutionUniverse, fetchStrategyBundles, fetchStrategyExecutionReleases, fetchStrategyMonitor,
  fetchStrategyRuntimeDetail, importStrategyBundle, validateStrategyBundle, type ExecutionUniverseRelease, type StrategyBundleCatalog,
  type StrategyBundleValidation, type StrategyExecutionRelease, type StrategyMonitorRow,
  type StrategyMonitorSnapshot, type StrategyRuntimeDetail,
} from '../api/client'
import { formatQuantity, localTime } from '../components/Format'
import { StatusBadge } from '../components/StatusBadge'
import { WorkspaceShell, type WorkspaceTool } from '../components/WorkspaceShell'
import { usePolling } from '../hooks/usePolling'

interface StrategyData {
  monitor: StrategyMonitorSnapshot
  releases: StrategyExecutionRelease[]
  bundles: StrategyBundleCatalog
  universe: ExecutionUniverseRelease | null
}

const tools: WorkspaceTool[] = [
  { id: 'runtime', label: '运行监控', icon: Activity },
  { id: 'releases', label: '执行发布', icon: PackageCheck },
]

export function StrategyPage() {
  const loader = useCallback(async (): Promise<StrategyData> => {
    const [monitor, releases, bundles, universe] = await Promise.all([
      fetchStrategyMonitor(),
      fetchStrategyExecutionReleases(),
      fetchStrategyBundles(),
      fetchLatestExecutionUniverse(),
    ])
    return { monitor, releases, bundles, universe }
  }, [])
  const { data, error, refresh } = usePolling(loader, 5000)
  const [activeTool, setActiveTool] = useState('runtime')
  const [query, setQuery] = useState('')
  const [healthFilter, setHealthFilter] = useState('all')
  const [modeFilter, setModeFilter] = useState('all')
  const [selectedRuntimeId, setSelectedRuntimeId] = useState('')
  const [runtimeDetail, setRuntimeDetail] = useState<StrategyRuntimeDetail | null>(null)
  const [detailError, setDetailError] = useState('')

  const runtimeRows = useMemo(() => (data?.monitor.strategies ?? []).filter((row) => {
    const normalized = query.trim().toLowerCase()
    const queryMatches = !normalized || `${row.name} ${row.strategy_id}`.toLowerCase().includes(normalized)
    const healthMatches = healthFilter === 'all' || row.health === healthFilter
    const modeMatches = modeFilter === 'all' || row.mode === modeFilter
    return queryMatches && healthMatches && modeMatches
  }), [data?.monitor.strategies, healthFilter, modeFilter, query])
  const effectiveRuntimeId = selectedRuntimeId || runtimeRows[0]?.strategy_id || ''
  const visibleRuntimeDetail = runtimeDetail?.strategy_id === effectiveRuntimeId ? runtimeDetail : null

  useEffect(() => {
    if (activeTool !== 'runtime' || !effectiveRuntimeId) return
    let cancelled = false
    void fetchStrategyRuntimeDetail(effectiveRuntimeId)
      .then((detail) => { if (!cancelled) { setRuntimeDetail(detail); setDetailError('') } })
      .catch((reason: unknown) => {
        if (!cancelled) setDetailError(reason instanceof Error ? reason.message : '策略详情读取失败')
      })
    return () => { cancelled = true }
  }, [activeTool, data?.monitor.timestamp, effectiveRuntimeId])

  const canvas = activeTool === 'releases' ? (
    <ReleasePanel catalog={data?.bundles ?? null} releases={data?.releases ?? []} universe={data?.universe ?? null} onValidated={refresh} />
  ) : (
    <div className="strategy-runtime-layout strategy-page-runtime" aria-label="多策略运行监控" role="region">
      <aside>
        {runtimeRows.map((row) => <RuntimeButton
          active={effectiveRuntimeId === row.strategy_id}
          key={row.strategy_id}
          onClick={() => { setSelectedRuntimeId(row.strategy_id); setRuntimeDetail(null) }}
          row={row}
        />)}
        {!runtimeRows.length && <p className="empty">没有符合筛选条件的策略实例</p>}
      </aside>
      <section>
        {visibleRuntimeDetail ? <RuntimeDetail detail={visibleRuntimeDetail} /> : <p className="empty">选择策略查看心跳、状态、日志、委托、成交和恢复信息。</p>}
      </section>
    </div>
  )

  const context = <>
    <div className="context-tabs"><b>{activeTool === 'runtime' ? '运行详情' : '发布边界'}</b><span>{activeTool === 'runtime' ? visibleRuntimeDetail?.mode ?? '—' : 'Research → Trading'}</span></div>
    {activeTool === 'runtime' ? <dl className="context-list">
      <div><dt>实例</dt><dd>{visibleRuntimeDetail?.strategy_id ?? '—'}</dd></div>
      <div><dt>健康</dt><dd>{visibleRuntimeDetail?.health ?? '—'}</dd></div>
      <div><dt>最后活动</dt><dd>{localTime(visibleRuntimeDetail?.last_activity_at)}</dd></div>
      <div><dt>订单 / 成交</dt><dd>{visibleRuntimeDetail ? `${visibleRuntimeDetail.order_count} / ${visibleRuntimeDetail.trade_count}` : '—'}</dd></div>
      <div><dt>恢复</dt><dd>{visibleRuntimeDetail?.recovery_status ?? '—'}</dd></div>
      <div><dt>实盘直连</dt><dd>禁止</dd></div>
    </dl> : <dl className="context-list">
      <div><dt>研究来源</dt><dd>DyadResearch</dd></div>
      <div><dt>交接协议</dt><dd>StrategyReleaseBundle 1.0</dd></div>
      <div><dt>校验</dt><dd>Schema + SHA-256</dd></div>
      <div><dt>激活</dt><dd>人工门禁</dd></div>
      <div><dt>已登记</dt><dd>{data?.releases.length ?? 0}</dd></div>
    </dl>}
  </>

  const toolbar = activeTool === 'runtime' ? <>
    <label className="workspace-search"><Search size={14} /><input aria-label="搜索策略实例" placeholder="策略名或实例 ID" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    <select aria-label="健康状态筛选" value={healthFilter} onChange={(event) => setHealthFilter(event.target.value)}><option value="all">全部健康状态</option><option value="healthy">健康</option><option value="stale">心跳超时</option><option value="error">异常</option><option value="waiting">等待心跳</option><option value="stopped">已停止</option></select>
    <select aria-label="运行模式筛选" value={modeFilter} onChange={(event) => setModeFilter(event.target.value)}><option value="all">全部模式</option>{Object.keys(data?.monitor.mode_counts ?? {}).map((mode) => <option value={mode} key={mode}>{mode}</option>)}</select>
    <button onClick={() => void refresh()}><RefreshCw size={14} />刷新</button>
  </> : <button onClick={() => void refresh()}><RefreshCw size={14} />刷新发布记录</button>

  const bottom = <div className="workspace-summary-grid">
    <div><span>策略实例</span><b>{data?.monitor.total_count ?? 0}</b></div>
    <div><span>运行</span><b>{data?.monitor.running_count ?? 0}</b></div>
    <div><span>健康</span><b>{data?.monitor.healthy_count ?? 0}</b></div>
    <div><span>心跳超时</span><b>{data?.monitor.stale_count ?? 0}</b></div>
    <div><span>异常</span><b>{data?.monitor.error_count ?? 0}</b></div>
    <div><span>执行发布</span><b>{data?.releases.length ?? 0}</b></div>
  </div>

  return <WorkspaceShell
    title="策略" eyebrow="STRATEGY" subtitle="执行发布与运行监控"
    tools={tools} activeTool={activeTool} onToolChange={setActiveTool}
    toolbar={toolbar} canvas={canvas} context={context}
    bottomTabs={[{ id: 'overview', label: '运行概览', count: data?.monitor.total_count }]}
    activeBottomTab="overview" onBottomTabChange={() => undefined} bottom={bottom}
    error={detailError || error}
  />
}

function RuntimeButton({ active, onClick, row }: { active: boolean; onClick: () => void; row: StrategyMonitorRow }) {
  return <button className={active ? 'active' : ''} onClick={onClick}>
    <span><Activity size={14} />{row.name}</span>
    <StatusBadge label={row.health} ok={row.health === 'healthy'} />
    <small>{row.mode} · {row.status} · 心跳 {formatQuantity(row.heartbeat_age_seconds, 1)} 秒</small>
  </button>
}

function RuntimeDetail({ detail }: { detail: StrategyRuntimeDetail }) {
  return <>
    <header><div><h3>{detail.name}</h3><span>{detail.strategy_id} · v{detail.version}</span></div><StatusBadge label={detail.health} ok={detail.health === 'healthy'} /></header>
    <div className="strategy-safety-notice"><ShieldCheck size={14} />策略不能直连 XtTrader；所有委托必须经过发布门禁、风控、OMS 和网关。</div>
    <div className="strategy-detail-metrics">
      <span>模式<b>{detail.mode}</b></span><span>状态<b>{detail.status}</b></span>
      <span>最后心跳<b>{localTime(detail.last_heartbeat)}</b></span><span>活动委托<b>{detail.pending_order_count}</b></span>
      <span>成交<b>{detail.trade_count}</b></span><span>风控事件<b>{detail.risk_event_count}</b></span>
    </div>
    <div className="strategy-detail-grid">
      <div><h4>参数</h4><pre>{JSON.stringify(detail.parameters, null, 2)}</pre></div>
      <div><h4>运行状态</h4><pre>{JSON.stringify(detail.state, null, 2)}</pre></div>
      <div><h4>最近审计事件</h4><pre>{detail.audit_events.slice(-20).map((event) => JSON.stringify(event)).join('\n') || '暂无审计事件'}</pre></div>
      <div><h4>执行摘要</h4><p>委托 {detail.order_count} · 成交 {detail.trade_count} · 风控 {detail.risk_event_count}</p><p>进程角色：{detail.process_role}</p></div>
    </div>
    {detail.error && <p className="negative"><AlertTriangle size={13} />{detail.error}</p>}
  </>
}

function ReleasePanel({ catalog, releases, universe, onValidated }: { catalog: StrategyBundleCatalog | null; releases: StrategyExecutionRelease[]; universe: ExecutionUniverseRelease | null; onValidated: () => Promise<void> }) {
  const [bundleName, setBundleName] = useState('')
  const [target, setTarget] = useState<'paper' | 'qmt_sim' | 'qmt_live'>('paper')
  const [validation, setValidation] = useState<StrategyBundleValidation | null>(null)
  const [validationError, setValidationError] = useState('')
  const [busy, setBusy] = useState(false)
  const validate = async () => {
    setBusy(true); setValidationError('')
    try {
      setValidation(await validateStrategyBundle(bundleName, target))
      await onValidated()
    } catch (reason) {
      setValidationError(reason instanceof Error ? reason.message : '发布包校验失败')
    } finally { setBusy(false) }
  }
  const register = async () => {
    if (target === 'paper' || !universe) return
    setBusy(true); setValidationError('')
    try {
      setValidation(await importStrategyBundle(bundleName, target, universe.release_id))
      await onValidated()
    } catch (reason) {
      setValidationError(reason instanceof Error ? reason.message : '执行发布登记失败')
    } finally { setBusy(false) }
  }
  return <div className="strategy-release-panel">
    <section className="strategy-safety-notice"><PackageCheck size={14} />这里只展示经交易侧登记的执行发布；策略编写、因子研究、回测与优化请在 DyadResearch 完成。</section>
    <section className="release-import-controls">
      <label><span>发布包</span><input list="strategy-bundle-options" value={bundleName} onChange={(event) => setBundleName(event.target.value)} placeholder="inbox 中的目录名" /></label>
      <datalist id="strategy-bundle-options">{(catalog?.bundles ?? []).map((bundle) => <option value={bundle.name} key={bundle.name}>{bundle.strategy_id} {bundle.strategy_version}</option>)}</datalist>
      <label><span>目标</span><select value={target} onChange={(event) => setTarget(event.target.value as typeof target)}><option value="paper">paper</option><option value="qmt_sim">qmt_sim</option><option value="qmt_live">qmt_live</option></select></label>
      <button disabled={busy || !bundleName} onClick={() => void validate()}><ShieldCheck size={14} />{busy ? '校验中' : '校验并留痕'}</button>
      <button disabled={busy || !bundleName || target === 'paper' || universe?.status !== 'active'} onClick={() => void register()}><PackageCheck size={14} />登记待激活发布</button>
      <small>Inbox: {catalog?.inbox ?? '—'}</small>
      <small>执行股票池: {universe ? `${universe.release_id} · ${universe.status}` : '尚未创建'}</small>
    </section>
    {validation && <p className="positive">已验证 {validation.strategy_id} v{validation.strategy_version} · {validation.execution_target} · {validation.bundle_hash.slice(0, 12)}</p>}
    {validation?.execution_release && <p className="positive">已登记草稿 {validation.execution_release.release_id}；仍需交易端人工激活。</p>}
    {validationError && <p className="negative"><AlertTriangle size={13} />{validationError}</p>}
    <div className="realtime-bars-table"><table>
      <thead><tr><th>发布 ID</th><th>策略</th><th>版本</th><th>模式</th><th>状态</th><th>股票池发布</th><th>单笔上限</th><th>日上限</th><th>激活时间</th></tr></thead>
      <tbody>{releases.map((release) => <tr key={release.release_id}>
        <td>{release.release_id}</td><td>{release.strategy_name}</td><td>{release.strategy_version}</td>
        <td>{release.execution_mode}</td><td>{release.status}</td><td>{release.universe_release_id}</td>
        <td>{release.max_order_notional}</td><td>{release.max_daily_notional}</td><td>{localTime(release.activated_at)}</td>
      </tr>)}</tbody>
    </table>{!releases.length && <p className="empty">暂无执行发布。请先从 DyadResearch 导出并审阅 StrategyReleaseBundle。</p>}</div>
  </div>
}
