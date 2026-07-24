import { useState } from 'react'

export function ConfirmAction({ label, confirmLabel = '再次确认', danger = false, disabled = false, onConfirm }: { label: string; confirmLabel?: string; danger?: boolean; disabled?: boolean; onConfirm: () => void | Promise<void> }) {
  const [armed, setArmed] = useState(false)
  return <button className={danger ? 'danger-button' : ''} disabled={disabled} onClick={() => {
    if (!armed) { setArmed(true); return }
    setArmed(false); void onConfirm()
  }}>{armed ? confirmLabel : label}</button>
}
