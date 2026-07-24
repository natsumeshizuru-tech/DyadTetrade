export interface WorkbenchPreferences { symbol: string; period: '1d' | '1m' | '5m' }

const preferences: WorkbenchPreferences = { symbol: '600519.SH', period: '1d' }

export function getWorkbenchPreferences(): WorkbenchPreferences { return preferences }
