interface Props { label: string; ok?: boolean; tone?: 'normal' | 'warning' | 'danger' }

export function StatusBadge({ label, ok, tone = 'normal' }: Props) {
  const className = ok === true ? 'status online' : ok === false ? 'status danger' : `status ${tone}`
  return <span className={className}>{label}</span>
}
