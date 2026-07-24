const numberValue = (value: unknown): number => Number(value ?? Number.NaN)

const decimalsFromTick = (tick: string | number | undefined): number => {
  if (tick == null) return 2
  const text = String(tick).replace(/0+$/, '')
  const point = text.indexOf('.')
  return point < 0 ? 0 : text.length - point - 1
}

const fixedNumber = (value: unknown, decimals: number): string => {
  const number = numberValue(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
        useGrouping: false,
      }).format(number)
    : '—'
}

export function formatPrice(value: unknown, priceTick: string | number = '0.01'): string {
  return fixedNumber(value, decimalsFromTick(priceTick))
}

export function formatAverageCost(value: unknown, priceDecimals = 2): string {
  return fixedNumber(value, Math.max(priceDecimals, 4))
}

export function formatMoney(value: unknown, currency = 'CNY', decimals = 2): string {
  const number = numberValue(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', {
        style: 'currency', currency, minimumFractionDigits: decimals, maximumFractionDigits: decimals,
      }).format(number)
    : '—'
}

export function formatQuantity(value: unknown, decimals = 0): string {
  const number = numberValue(value)
  return Number.isFinite(number)
    ? new Intl.NumberFormat('zh-CN', {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals,
      }).format(number)
    : '—'
}

export function formatPercent(value: unknown, decimals = 2): string {
  const number = numberValue(value)
  return Number.isFinite(number) ? `${fixedNumber(number * 100, decimals)}%` : '—'
}

export const money = formatMoney
export const percent = formatPercent

export function localTime(value: unknown): string {
  if (!value) return '—'
  const numeric = typeof value === 'number' || /^\d{10,16}$/.test(String(value)) ? Number(value) : null
  const epoch = numeric == null ? null : numeric < 1_000_000_000_000 ? numeric * 1000 : numeric
  const date = epoch == null ? new Date(String(value)) : new Date(epoch)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}
