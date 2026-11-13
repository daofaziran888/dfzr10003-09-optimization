import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import VersionDiffModal from '@/components/VersionDiffModal'

const ENDPOINT = '/api/backfill'
const columns = [
  "批次编号",
  "目标表名",
  "重跑状态",
  "开始日期",
  "结束日期",
  "关联血缘节点",
  "引用节点版本",
  "覆盖模式",
]
const actions = [
  "启动重跑",
  "暂停重跑",
  "归档批次"
]

type Row = Record<string, string | number | null>

export default function BackfillView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [diffBatchId, setDiffBatchId] = useState<number | null>(null)
  const filterFields = useMemo(() => ["批次编号", "目标表名", "重跑状态"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('重跑批次列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史重跑列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '重跑批次总数', value: rows.length },
    { label: '运行中批次', value: rows.filter((row) => row.重跑状态 === '运行中').length },
    { label: '草稿批次', value: rows.filter((row) => row.重跑状态 === '草稿').length },
    { label: '已完成批次', value: rows.filter((row) => row.重跑状态 === '已完成').length },
  ], [rows])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.detail || payload.message || '历史重跑动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '历史重跑操作失败')
    }
  }

  return (
    <section className="page" data-module="backfill">
      <header className="page-head">
        <div>
          <h2>历史重跑管理</h2>
          <p className="page-desc">
            每个补数批次都钉住发起时的血缘节点版本与映射快照；已开始的批次继续由旧版本服务，
            新批次使用新版本，可逐批查看引用版本与当前版本差异。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出历史重跑清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>版本差异</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>
                <button className="link" type="button" onClick={() => setDiffBatchId(Number(row.id))}>
                  查看引用版本差异
                </button>
              </td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无历史重跑数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条历史重跑记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      <VersionDiffModal open={diffBatchId !== null} batchId={diffBatchId} onClose={() => setDiffBatchId(null)} />
    </section>
  )
}
