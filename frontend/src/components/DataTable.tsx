import type { ReactNode } from 'react'

export interface Column<T> { key: string; title: string; render: (row: T) => ReactNode }

export function DataTable<T>({ rows, columns, empty = '暂无数据' }: { rows: T[]; columns: Array<Column<T>>; empty?: string }) {
  if (!rows.length) return <p className="empty">{empty}</p>
  return <div className="table-wrap"><table><thead><tr>{columns.map((column) => <th key={column.key}>{column.title}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}</tr>)}</tbody></table></div>
}
