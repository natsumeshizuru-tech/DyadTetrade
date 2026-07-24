import { useState } from 'react'
import { Activity, BarChart3, Settings, ShieldCheck } from 'lucide-react'
import { MarketPage } from './pages/MarketPage'
import { StrategyPage } from './pages/StrategyPage'
import { SystemPage } from './pages/SystemPage'
import { TradingPage } from './pages/TradingPage'
import './styles.css'

export type PrimaryPage = 'trading' | 'market' | 'strategy' | 'system'

const navigation = [
  { id: 'trading', label: '交易', icon: Activity },
  { id: 'market', label: '行情', icon: BarChart3 },
  { id: 'strategy', label: '策略', icon: ShieldCheck },
  { id: 'system', label: '系统', icon: Settings },
] satisfies Array<{ id: PrimaryPage; label: string; icon: typeof Activity }>

export default function App() {
  const [page, setPage] = useState<PrimaryPage>('trading')
  return (
    <div className="shell terminal-shell">
      <header className="app-commandbar">
        <button className="brand-mark" onClick={() => setPage('trading')} aria-label="返回交易终端">
          <span>DT</span><strong>DyadTetrade</strong>
        </button>
        <nav className="primary-nav" aria-label="一级导航">
        {navigation.map((item) => (
          <button
            className={page === item.id ? 'active' : ''}
            key={item.id}
            onClick={() => setPage(item.id)}
          >
            <item.icon size={15} strokeWidth={1.8} />
            {item.label}
          </button>
        ))}
        </nav>
        <div className="safety-banner"><ShieldCheck size={14} />实盘受安全门禁保护</div>
      </header>
      <main className="terminal-main">
        {page === 'trading' && <TradingPage />}
        {page === 'market' && <MarketPage />}
        {page === 'strategy' && <StrategyPage />}
        {page === 'system' && <SystemPage />}
      </main>
    </div>
  )
}
