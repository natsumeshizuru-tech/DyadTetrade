import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import type { ExecutionSnapshot } from '../api/client'
import { ExecutionControls } from './ExecutionControls'

const liveExecution: ExecutionSnapshot = {
  execution_mode: 'qmt_live',
  connected: false,
  subscribed: false,
  account_healthy: false,
  engine_online: true,
  positions: [],
  orders: [],
  trades: [],
}

test('groups live activation, reconciliation and emergency stop behind explicit controls', () => {
  const onActivate = vi.fn()
  const onReconcile = vi.fn()
  const onHalt = vi.fn()
  render(<ExecutionControls
    execution={liveExecution}
    risk={{ state: 'RECOVERY', reason: 'startup reconciliation required' }}
    busy={false}
    onActivate={onActivate}
    onReconcile={onReconcile}
    onHalt={onHalt}
  />)

  expect(screen.getByRole('region', { name: '交易控制' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '开启实盘' }))
  expect(onActivate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认连接实盘并对账' }))
  expect(onActivate).toHaveBeenCalledOnce()

  fireEvent.click(screen.getByRole('button', { name: '对账' }))
  expect(onReconcile).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: '紧急停止' }))
  fireEvent.click(screen.getByRole('button', { name: '确认停止新委托' }))
  expect(onHalt).toHaveBeenCalledOnce()
})

test('does not pretend the browser can enable a disabled trading engine', () => {
  render(<ExecutionControls
    execution={{ ...liveExecution, execution_mode: 'disabled', engine_online: false }}
    risk={{ state: 'RECOVERY', reason: 'engine offline' }}
    busy={false}
    onActivate={vi.fn()}
    onReconcile={vi.fn()}
    onHalt={vi.fn()}
  />)

  expect(screen.getByRole('button', { name: '交易已关闭' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '对账' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '紧急停止' })).toBeDisabled()
})
