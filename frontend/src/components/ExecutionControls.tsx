import { Power, RefreshCw, ShieldAlert } from 'lucide-react'
import type { ExecutionSnapshot, RiskStateSnapshot } from '../api/client'
import { ConfirmAction } from './ConfirmAction'
import { StatusBadge } from './StatusBadge'

interface Props {
  execution: ExecutionSnapshot
  risk: RiskStateSnapshot
  busy: boolean
  onActivate: () => void | Promise<void>
  onReconcile: () => void | Promise<void>
  onHalt: () => void | Promise<void>
}

const modeLabels: Record<ExecutionSnapshot['execution_mode'], string> = {
  disabled: '交易关闭',
  paper: '纸上交易',
  qmt_sim: 'QMT 模拟',
  qmt_live: 'QMT 实盘',
}

export function ExecutionControls({ execution, risk, busy, onActivate, onReconcile, onHalt }: Props) {
  const qmtMode = execution.execution_mode === 'qmt_sim' || execution.execution_mode === 'qmt_live'
  const liveMode = execution.execution_mode === 'qmt_live'
  const accountReady = Boolean(
    execution.engine_online
    && execution.connected
    && execution.subscribed
    && execution.account_healthy
    && risk.state === 'NORMAL',
  )
  const primaryLabel = accountReady
    ? (liveMode ? '实盘运行中' : '模拟运行中')
    : (liveMode ? '开启实盘' : execution.execution_mode === 'qmt_sim' ? '连接模拟' : '交易已关闭')
  const confirmLabel = liveMode ? '确认连接实盘并对账' : '确认连接模拟并对账'

  return <section className="execution-controls" aria-label="交易控制">
    <div className="execution-statuses">
      <StatusBadge label={modeLabels[execution.execution_mode]} ok={qmtMode} />
      <StatusBadge label={execution.connected && execution.subscribed ? '账户在线' : '账户离线'} ok={execution.connected && execution.subscribed} />
      <StatusBadge label={risk.state} ok={risk.state === 'NORMAL'} tone={risk.state === 'HALTED' ? 'danger' : 'warning'} />
    </div>
    <div className="execution-actions">
      <span className="execution-primary">
        <ConfirmAction
          label={primaryLabel}
          confirmLabel={confirmLabel}
          disabled={!qmtMode || !execution.engine_online || accountReady || busy}
          onConfirm={onActivate}
        />
      </span>
      <button className="secondary-control" disabled={!execution.engine_online || busy} onClick={() => void onReconcile()} title="重新核对账户、委托和成交">
        <RefreshCw size={14} />对账
      </button>
      <span className="emergency-control">
        <ConfirmAction
          label="紧急停止"
          confirmLabel="确认停止新委托"
          danger
          disabled={!execution.engine_online || risk.state === 'HALTED' || busy}
          onConfirm={onHalt}
        />
      </span>
    </div>
    {liveMode && !accountReady && <span className="live-gate-note"><ShieldAlert size={13} />需先通过启动脚本的当日实盘门禁</span>}
    {accountReady && <Power className="execution-ready-icon" size={14} aria-hidden="true" />}
  </section>
}
