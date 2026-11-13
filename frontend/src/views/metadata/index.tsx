import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/metadata'
const columns = [
  "记录编号",
  "表名称",
  "字段名称",
  "字段类型",
  "业务含义",
  "负责人",
  "复核状态",
  "影响批次",
]

type Row = Record<string, string | number | null>

export default function MetadataView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [reviewOnly, setReviewOnly] = useState(false)
  const filterFields = useMemo(() => ["记录编号", "表名称", "字段名称"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const params = new URLSearchParams(filters)
    if (reviewOnly) {
      params.set('review_status', '待复核')
    }
    try {
      const response = await request(`${ENDPOINT}?${params.toString()}`)
      if (!response.ok) {
        throw new Error('元数据记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据目录列表读取失败')
    }
  }, [filters, reviewOnly])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '元数据记录总数', value: rows.length },
    { label: '待复核记录', value: rows.filter((row) => row.复核状态 === '待复核').length },
    { label: '已复核记录', value: rows.filter((row) => row.复核状态 === '已复核').length },
  ], [rows])

  return (
    <section className="page" data-module="metadata">
      <header className="page-head">
        <div>
          <h2>元数据目录管理</h2>
          <p className="page-desc">
            血缘字段映射变更后，沿「节点下游表 → 字段」依赖路径命中的目录记录自动进入待复核，
            影响项逐项确认并关闭批次后才回到已复核，未复核期间旧业务含义持续挂账。
          </p>
        </div>
        <div className="page-actions">
          <button
            className={`btn ${reviewOnly ? 'primary' : ''}`}
            type="button"
            onClick={() => setReviewOnly((prev) => !prev)}
          >
            {reviewOnly ? '显示全部记录' : '只看待复核清单'}
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出元数据目录清单
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
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row.复核状态 === '待复核' ? 'row-review-pending' : ''}>
              {columns.map((column) => (
                <td key={column}>
                  {column === '复核状态' ? (
                    row[column]
                      ? <span className={`tag ${row[column] === '待复核' ? 'warn' : 'ok'}`}>{String(row[column])}</span>
                      : <span className="muted">—</span>
                  ) : (
                    row[column] ?? '—'
                  )}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length}>
                {reviewOnly ? '暂无待复核的元数据记录' : '暂无元数据目录数据'}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条元数据目录记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
