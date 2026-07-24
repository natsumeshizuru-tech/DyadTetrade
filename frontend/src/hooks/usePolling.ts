import { useCallback, useEffect, useState } from 'react'

export function usePolling<T>(loader: () => Promise<T>, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const refresh = useCallback(async () => {
    try { setData(await loader()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '请求失败') }
    finally { setLoading(false) }
  }, [loader])
  useEffect(() => {
    const initial = window.setTimeout(() => { void refresh() }, 0)
    if (!intervalMs || import.meta.env.MODE === 'test') return () => window.clearTimeout(initial)
    const timer = window.setInterval(() => { void refresh() }, intervalMs)
    return () => { window.clearTimeout(initial); window.clearInterval(timer) }
  }, [intervalMs, refresh])
  return { data, error, loading, refresh }
}
