import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import { fetchImpactBatch, type ImpactBatch, type ImpactResult } from '@/api/impact'
import AnalyzeModal from '@/components/AnalyzeModal'
import ImpactBatchModal from '@/components/ImpactBatchModal'

const ENDPOINT = '/api/lineage'
const columns = [
  "节点编号",
  "节点名称",
  "节点类型",
  "上游节点",
  "下游节点",
  "字段映射",
  "图谱版本",
  "生效时间",
]
const actions = [
  "提交审批",
  "发布节点",
  "废弃节点"
]

type Row = Record<string, string | number | null>

export default function LineageView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [analyzeTarget, setAnalyzeTarget] = useState<Row | null>(null)
  const [impactBatches, setImpactBatches] = useState<Record<string, ImpactBatch[]>>({})
  const [openedBatch, setOpenedBatch] = useState<ImpactBatch | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('血缘节点列表读取失败')
      }
      const payload = await response.json()
      const items = (payload.items ?? []) as Row[]
      setRows(items)
      setTotal(payload.total ?? 0)
      await loadBatchMap(items)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '血缘图谱列表读取失败')
    }
  }, [filters])

  const loadBatchMap = useCallback(async (items: Row[]) => {
    const entries = await Promise.all(
      items.map(async (row) => {
        const code = String(row.节点编号 ?? '')
        if (!code) {
          return [code, []] as const
        }
        try {
          const batches = await fetchImpactBatch(code)
          return [code, batches] as const
        } catch {
          return [code, []] as const
        }
      }),
    )
    setImpactBatches(Object.fromEntries(entries))
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '血缘节点总数', value: rows.length },
    { label: '已生效节点', value: rows.filter((row) => row.status === '已生效').length },
    { label: '待审批节点', value: rows.filter((row) => row.status === '待审批').length },
    { label: '待处理影响批次', value: Object.values(impactBatches)
      .flat()
      .filter((batch) => batch.状态 === '待处理').length },
  ], [rows, impactBatches])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.detail || payload.message || '血缘图谱动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '血缘图谱操作失败')
    }
  }

  function handleAnalyzed(result: ImpactResult, message: string) {
    setAnalyzeTarget(null)
    setOpenedBatch(result.batch)
    setErrorMessage(message)
    void reload()
  }

  function openLatestBatch(row: Row) {
    const code = String(row.节点编号 ?? '')
    const batches = impactBatches[code] ?? []
    if (batches.length === 0) {
      setErrorMessage('该节点尚未发起过影响分析')
      return
    }
    setOpenedBatch(batches[0])
  }

  return (
    <section className="page" data-module="lineage">
      <header className="page-head">
        <div>
          <h2>血缘图谱管理</h2>
          <p className="page-desc">
            维护血缘节点的依赖与字段映射；发布前先做影响分析，逐项确认上游、下游、映射、元数据与重跑批次后才能落版。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出血缘图谱清单
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
            <th>影响分析</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const code = String(row.节点编号 ?? '')
            const nodeBatches = impactBatches[code] ?? []
            const openBatch = nodeBatches.find((batch) => batch.状态 === '待处理')
            return (
              <tr key={String(row.id)}>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>
                  <div className="impact-cell">
                    {openBatch ? (
                      <span className="tag warn">待处理批次 {openBatch.批次编号}</span>
                    ) : nodeBatches.length > 0 ? (
                      <span className="tag ok">已闭环 {nodeBatches[0].批次编号}</span>
                    ) : (
                      <span className="muted">未分析</span>
                    )}
                    {row.待生效映射 && (
                      <span className="tag info" title={`待生效：${String(row.待生效映射)}`}>
                        待生效 {String(row.待生效版本 ?? '')}
                      </span>
                    )}
                    <span className="row-actions">
                      <button className="link" type="button" onClick={() => setAnalyzeTarget(row)}>
                        影响分析
                      </button>
                      <button className="link" type="button" onClick={() => openLatestBatch(row)}>
                        影响清单
                      </button>
                    </span>
                  </div>
                </td>
                <td className="row-actions">
                  {actions.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无血缘图谱数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条血缘图谱记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      <AnalyzeModal
        open={analyzeTarget !== null}
        row={analyzeTarget}
        onClose={() => setAnalyzeTarget(null)}
        onAnalyzed={handleAnalyzed}
      />
      <ImpactBatchModal
        open={openedBatch !== null}
        batch={openedBatch}
        onClose={() => setOpenedBatch(null)}
        onChanged={() => void reload()}
      />
    </section>
  )
}
