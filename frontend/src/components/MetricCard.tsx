import type { ReactNode } from 'react'

export function MetricCard({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <article className="metric-card"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>
}
