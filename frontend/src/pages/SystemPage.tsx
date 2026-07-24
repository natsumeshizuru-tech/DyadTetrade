import { useCallback, useState } from 'react'
import { Activity, RefreshCw, Server, ShieldCheck } from 'lucide-react'
import {
  fetchExecutionSnapshot, fetchLatestExecutionUniverse, fetchMarketSession,
  fetchReadiness, fetchStrategyExecutionReleases,
} from '../api/client'
import { localTime } from '../components/Format'
import { StatusBadge } from '../components/StatusBadge'
import { WorkspaceShell, type WorkspaceTool } from '../components/WorkspaceShell'
import { usePolling } from '../hooks/usePolling'

const tools: WorkspaceTool[] = [
  { id: 'runtime', label: '运行状态', icon: Activity },
  { id: 'safety', label: '安全门禁', icon: ShieldCheck },
  { id: 'diagnostics', label: '开发诊断', icon: Server },
]

export function SystemPage() {
  const loader = useCallback(async () => {
    const [readiness, execution, session, universe, strategyReleases] = await Promise.all([
      fetchReadiness(), fetchExecutionSnapshot(), fetchMarketSession(),
      fetchLatestExecutionUniverse(), fetchStrategyExecutionReleases(),
    ])
    return { readiness, execution, session, universe, strategyReleases }
  }, [])
  const { data, error, refresh } = usePolling(loader, 10000)
  const [activeTool, setActiveTool] = useState('runtime')
  const activeReleases = data?.strategyReleases.filter((item) => item.status === 'active') ?? []

  const canvas = activeTool === 'diagnostics' ? (
    <div className="diagnostics-canvas"><h3>开发者诊断</h3><p>原始运行状态只在此区域显示；历史数据诊断请使用 DyadResearch。</p><pre>{JSON.stringify(data, null, 2)}</pre></div>
  ) : (
    <div className="system-matrix">
      <header><strong>{tools.find((tool) => tool.id === activeTool)?.label}</strong><span>{localTime(data?.session.observed_at)}</span></header>
      <div className="system-status-grid">
        <article><span>后端</span><StatusBadge label={data?.readiness.status ?? '未知'} ok={data?.readiness.status === 'ready'} /><small>FastAPI 与交易账本就绪</small></article>
        <article><span>Trading Engine</span><StatusBadge label={data?.execution.engine_online ? '在线' : '离线'} ok={Boolean(data?.execution.engine_online)} /><small>单进程 · {data?.execution.engine_role ?? '未启动'}</small></article>
        <article><span>QMT 账户</span><StatusBadge label={data?.execution.connected && data?.execution.subscribed ? '已订阅' : '未就绪'} ok={Boolean(data?.execution.connected && data?.execution.subscribed)} /><small>账号信息已脱敏</small></article>
        <article><span>市场阶段</span><StatusBadge label={data?.session.phase ?? '未知'} ok={Boolean(data?.session.new_orders_expected)} /><small>{data?.session.operator_action ?? '—'}</small></article>
        <article><span>交易日历</span><StatusBadge label={data?.session.calendar_source ?? '未知'} ok={data?.session.calendar_source !== 'weekday_fallback'} /><small>周末与节假日受日历约束</small></article>
        <article><span>执行模式</span><StatusBadge label={data?.execution.execution_mode ?? '未知'} ok={data?.execution.execution_mode === 'paper' || data?.execution.execution_mode === 'qmt_sim'} /><small>qmt_live 默认拒绝</small></article>
        <article><span>Kill Switch</span><StatusBadge label={data?.execution.kill_switch ? '已触发' : '未触发'} ok={!data?.execution.kill_switch} /><small>风控状态 {data?.execution.risk_state?.state ?? '—'}</small></article>
        <article><span>执行池</span><StatusBadge label={data?.universe?.status ?? '未发布'} ok={data?.universe?.status === 'active'} /><small>{data?.universe?.symbol_count ?? 0} 个标的</small></article>
        <article><span>策略发布</span><StatusBadge label={activeReleases.length ? '已发布' : '未发布'} ok={activeReleases.length > 0} /><small>{activeReleases.length} 个当日有效版本</small></article>
        <article><span>研究数据库</span><StatusBadge label="外部管理" ok /><small>唯一归属 DyadResearch</small></article>
      </div>
    </div>
  )

  const context = <>
    <div className="context-tabs"><b>运行摘要</b><span>{data?.session.phase ?? '—'}</span></div>
    <dl className="context-list">
      <div><dt>运行账本</dt><dd>{data?.readiness.database ?? '—'}</dd></div>
      <div><dt>历史数据归属</dt><dd>{data?.readiness.historical_data_owner ?? 'DyadResearch'}</dd></div>
      <div><dt>MiniQMT</dt><dd>{data?.readiness.miniqmt ?? '—'}</dd></div>
      <div><dt>执行模式</dt><dd>{data?.execution.execution_mode ?? '—'}</dd></div>
      <div><dt>执行池哈希</dt><dd>{data?.universe?.snapshot_hash.slice(0, 12) ?? '—'}</dd></div>
      <div><dt>实盘交易</dt><dd className={data?.readiness.live_trading_enabled ? 'negative' : 'positive'}>{data?.readiness.live_trading_enabled ? '已启用' : '关闭'}</dd></div>
    </dl>
  </>

  return <WorkspaceShell
    title="系统" eyebrow="SYSTEM" subtitle="运行、交易日历与安全门禁"
    tools={tools} activeTool={activeTool} onToolChange={setActiveTool}
    toolbar={<button onClick={() => void refresh()}><RefreshCw size={14} />立即刷新</button>}
    canvas={canvas} context={context}
    bottomTabs={[]} activeBottomTab="" onBottomTabChange={() => undefined} bottom={null}
    hideBottom error={error}
  />
}
