import { useCallback, useMemo, useState } from 'react'
import {
  fetchXtDataLatestSnapshots,
  fetchXtDataSubscriptionStatus,
  startXtDataSingleSubscription,
  startXtDataWholeSubscription,
  stopXtDataSubscriptions,
  type XtDataQuoteSnapshot,
} from '../../api/client'
import { ConfirmAction } from '../../components/ConfirmAction'
import { formatQuantity, localTime } from '../../components/Format'
import { StatusBadge } from '../../components/StatusBadge'
import { usePolling } from '../../hooks/usePolling'

const PERIODS = ['tick', '1m', '5m', '15m', '30m', '60m', '1d'] as const

function valueOf(quote: XtDataQuoteSnapshot, ...keys: string[]): string | number {
  for (const key of keys) {
    const value = quote[key]
    if (typeof value === 'number' || typeof value === 'string') return value
  }
  return '—'
}

interface SubscriptionMonitorProps {
  variant?: 'default' | 'sidebar'
}

export function SubscriptionMonitor({ variant = 'default' }: SubscriptionMonitorProps) {
  const [symbolsText, setSymbolsText] = useState('600519.SH')
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>('tick')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const symbols = useMemo(
    () => [...new Set(symbolsText.split(/[\s,，;；]+/).map((item) => item.trim().toUpperCase()).filter(Boolean))],
    [symbolsText],
  )
  const loader = useCallback(async () => {
    const status = await fetchXtDataSubscriptionStatus()
    const latest = await fetchXtDataLatestSnapshots(symbols, 50)
    return { status, latest }
  }, [symbols])
  const { data, error, refresh } = usePolling(loader, 3000)

  const run = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true)
    setMessage('')
    try {
      await operation()
      setMessage(success)
      await refresh()
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : '订阅操作失败')
    } finally {
      setBusy(false)
    }
  }

  const invalidSymbols = symbols.length === 0 || symbols.length > 50
  const rows = Object.entries(data?.latest ?? {})
  return (
    <section className={`panel subscription-monitor ${variant === 'sidebar' ? 'subscription-monitor-sidebar' : ''}`} aria-label="XtData 行情订阅">
      <div className="panel-title">
        <div>
          <h3>XtData 行情订阅</h3>
          <small>单股最多 50 只；沪深全推用于全市场监控</small>
        </div>
        <div className="connection">
          <StatusBadge label={data?.status.active ? '订阅运行中' : '未订阅'} ok={data?.status.active ?? false} />
          <StatusBadge label={data?.status.fresh ? '行情新鲜' : '行情未更新'} ok={data?.status.fresh ?? false} />
        </div>
      </div>
      {(error || message || data?.status.last_error) && (
        <div className={error || data?.status.last_error ? 'alert' : 'notice'} role="status">
          {error || data?.status.last_error || message}
        </div>
      )}
      <div className="subscription-controls">
        <div className="toolbar">
          <label>
            单股代码（逗号分隔）
            <input
              aria-label="单股订阅代码"
              value={symbolsText}
              onChange={(event) => setSymbolsText(event.target.value)}
            />
          </label>
          <label>
            周期
            <select aria-label="单股订阅周期" value={period} onChange={(event) => setPeriod(event.target.value as (typeof PERIODS)[number])}>
              {PERIODS.map((item) => <option value={item} key={item}>{item === '60m' ? '1h' : item}</option>)}
            </select>
          </label>
          <button
            disabled={busy || invalidSymbols}
            onClick={() => void run(() => startXtDataSingleSubscription(symbols, period), `已启动 ${symbols.length} 只证券的 ${period} 订阅`)}
          >
            启动单股订阅
          </button>
          <button
            disabled={busy}
            onClick={() => void run(() => startXtDataWholeSubscription(['SH', 'SZ']), '已启动沪深全市场 Tick 订阅')}
          >
            启动沪深全推
          </button>
          {data?.status.active && (
            <ConfirmAction
              label="停止全部订阅"
              confirmLabel="确认停止订阅"
              disabled={busy}
              onConfirm={() => run(stopXtDataSubscriptions, '全部行情订阅已停止')}
            />
          )}
        </div>
        {symbols.length > 50 && <p className="danger-text">单股订阅最多允许 50 只证券。</p>}
      </div>
      <div className="metric-row subscription-metrics">
        <span><small>活动订阅</small><b>{data?.status.subscription_count ?? 0}</b></span>
        <span><small>最新标的</small><b>{data?.status.latest_symbol_count ?? 0}</b></span>
        <span><small>接收批次 / 标的</small><b>{data?.status.received_batches ?? 0} / {data?.status.received_symbols ?? 0}</b></span>
        <span><small>队列 / 丢弃</small><b>{data?.status.queue_depth ?? 0} / {data?.status.dropped_batches ?? 0}</b></span>
      </div>
      <div className="subscription-detail">
        <div>
          <h4>活动通道</h4>
          {data?.status.subscriptions.length ? data.status.subscriptions.map((subscription, index) => (
            <div className="subscription-channel" key={`${subscription.mode}-${subscription.symbol ?? subscription.markets?.join('-')}-${index}`}>
              <strong>{subscription.mode === 'whole' ? '沪深全推' : subscription.symbol}</strong>
              <span>{subscription.period} · {subscription.mode === 'whole' ? subscription.markets?.join(' / ') : '单股'}</span>
            </div>
          )) : <p className="empty">当前没有活动订阅。</p>}
          <small>最后接收：{localTime(data?.status.last_received_at)} · 延迟 {data?.status.quote_age_seconds == null ? '—' : `${formatQuantity(data.status.quote_age_seconds, 1)} 秒`}</small>
        </div>
        <div>
          <h4>最新快照（所选单股）</h4>
          {rows.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>证券</th><th>最新价</th><th>成交量</th><th>成交额</th><th>接收时间</th></tr></thead>
                <tbody>{rows.map(([symbol, quote]) => (
                  <tr key={symbol}>
                    <td>{symbol}</td>
                    <td>{valueOf(quote, 'lastPrice', 'last_price', 'close')}</td>
                    <td>{valueOf(quote, 'volume')}</td>
                    <td>{valueOf(quote, 'amount')}</td>
                    <td>{localTime(valueOf(quote, 'received_at', 'time', 'timetag'))}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="empty">订阅启动并收到回调后显示最新快照。</p>}
        </div>
      </div>
    </section>
  )
}
