import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

export interface WorkspaceTool {
  id: string
  label: string
  icon: LucideIcon
}

interface WorkspaceShellProps {
  title: string
  eyebrow: string
  subtitle: string
  tools: WorkspaceTool[]
  activeTool: string
  onToolChange: (tool: string) => void
  toolbar: ReactNode
  canvas: ReactNode
  context: ReactNode
  bottomTabs: Array<{ id: string; label: string; count?: number }>
  activeBottomTab: string
  onBottomTabChange: (tab: string) => void
  bottom: ReactNode
  railPanel?: ReactNode
  hideBottom?: boolean
  message?: string
  error?: string
}

export function WorkspaceShell({
  title, eyebrow, subtitle, tools, activeTool, onToolChange, toolbar, canvas, context,
  bottomTabs, activeBottomTab, onBottomTabChange, bottom, railPanel, hideBottom = false, message, error,
}: WorkspaceShellProps) {
  return (
    <section className={`module-workspace${hideBottom ? ' without-bottom' : ''}${error || message ? ' has-message' : ''}`}>
      <header className="module-toolbar">
        <div className="module-title">
          <span>{eyebrow}</span>
          <h2>{title}</h2>
          <small>{subtitle}</small>
        </div>
        <div className="module-toolbar-actions">{toolbar}</div>
      </header>
      {(error || message) && (
        <div className={`module-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}
      <div className={`module-grid${railPanel ? ' has-rail-panel' : ''}`}>
        <div className="module-left-area">
          <aside className="module-rail" aria-label={`${title}工具`}>
            {tools.map((tool) => (
              <button
                aria-label={tool.label}
                className={activeTool === tool.id ? 'active' : ''}
                key={tool.id}
                onClick={() => onToolChange(tool.id)}
                title={tool.label}
              >
                <tool.icon size={20} strokeWidth={1.7} />
                <span>{tool.label}</span>
              </button>
            ))}
          </aside>
          {railPanel && <aside className="module-rail-panel">{railPanel}</aside>}
        </div>
        <main className="module-canvas">{canvas}</main>
        <aside className="module-context">{context}</aside>
      </div>
      {!hideBottom && (
        <section className="module-bottom">
          <nav className="module-bottom-tabs" aria-label={`${title}底部面板`}>
            {bottomTabs.map((tab) => (
              <button
                className={activeBottomTab === tab.id ? 'active' : ''}
                key={tab.id}
                onClick={() => onBottomTabChange(tab.id)}
              >
                {tab.label}{tab.count === undefined ? null : <span>{tab.count}</span>}
              </button>
            ))}
          </nav>
          <div className="module-bottom-content">{bottom}</div>
        </section>
      )}
    </section>
  )
}
