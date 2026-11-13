import { useCallback, useEffect, useState } from 'react'

import Modal from '@/components/Modal'
import {
  closeImpactBatch,
  confirmImpactItem,
  fetchBatchDetail,
  type ImpactBatch,
  type ImpactItem,
} from '@/api/impact'

const TYPE_ORDER = ['上游节点', '下游节点', '字段映射', '元数据记录', '重跑批次']
const TYPE_LABEL: Record<string, string> = {
  上游节点: '上游节点影响',
  下游节点: '下游节点影响',
  字段映射: '字段映射变化',
  元数据记录: '元数据目录待复核',
  重跑批次: '历史重跑引用差异',
}

type Props = {
  open: boolean
  batch: ImpactBatch | null
  onClose: () => void
  onChanged: () => void
}

/** 影响清单视图：逐项展示待确认范围，未全部确认前关闭按钮保持禁用。 */
export default function ImpactBatchModal({ open, batch, onClose, onChanged }: Props) {
  const [items, setItems] = useState<ImpactItem[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [acting, setActing] = useState(false)

  const load = useCallback(async () => {
    if (!batch) {
      return
    }
    setLoading(true)
    setMessage('')
    try {
      const detail = await fetchBatchDetail(batch.id)
      setItems(detail.items ?? [])
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '影响清单读取失败')
    } finally {
      setLoading(false)
    }
  }, [batch])

  useEffect(() => {
    if (open) {
      void load()
    }
  }, [open, load])

  const pendingCount = items.filter((item) => item.确认状态 !== '已确认').length

  async function handleConfirm(item: ImpactItem) {
    setActing(true)
    setMessage('')
    try {
      const result = await confirmImpactItem(item.id)
      if (!result.data.ok) {
        throw new Error(result.data.message)
      }
      await load()
      onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '确认失败')
    } finally {
      setActing(false)
    }
  }

  async function handleClose() {
    if (!batch) {
      return
    }
    setActing(true)
    setMessage('')
    try {
      const result = await closeImpactBatch(batch.id)
      if (!result.data.ok) {
        throw new Error(result.data.message)
      }
      setMessage(result.data.message)
      await load()
      onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '批次关闭失败')
    } finally {
      setActing(false)
    }
  }

  const grouped = TYPE_ORDER.map((type) => ({
    type,
    rows: items.filter((item) => item.影响类型 === type),
  })).filter((group) => group.rows.length > 0)

  return (
    <Modal
      open={open}
      title={batch ? `影响清单 ${batch.批次编号}（${batch.节点编号}）` : '影响清单'}
      onClose={onClose}
      width={1040}
      footer={
        <>
          <span className="modal-hint">
            待确认 {pendingCount} 项 / 共 {items.length} 项；未确认的影响项不能关闭批次
          </span>
          <button
            className="btn primary"
            type="button"
            disabled={acting || pendingCount > 0}
            onClick={() => void handleClose()}
            title={pendingCount > 0 ? '仍有影响项未确认' : '确认后新版本在血缘、元数据、重跑三面同步落版'}
          >
            全部确认并关闭批次
          </button>
        </>
      }
    >
      {batch && (
        <section className="impact-summary">
          <div><span>节点</span><strong>{batch.节点名称}</strong></div>
          <div><span>版本变化</span><strong>{batch.旧版本} → {batch.新版本}</strong></div>
          <div><span>批次状态</span><strong className={batch.状态 === '待处理' ? 'tag warn' : 'tag ok'}>{batch.状态}</strong></div>
          <div><span>生效时间</span><strong>{batch.生效时间}</strong></div>
        </section>
      )}
      {loading && <p className="empty-state">清单加载中…</p>}
      {!loading && grouped.map((group) => (
        <section key={group.type} className="impact-group">
          <h4>
            {TYPE_LABEL[group.type]}
            <em>{group.rows.filter((row) => row.确认状态 === '已确认').length}/{group.rows.length} 已确认</em>
          </h4>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 180 }}>关联对象</th>
                <th>影响说明</th>
                <th style={{ width: 220 }}>旧值 → 新值 / 差异</th>
                <th style={{ width: 110 }}>确认状态</th>
                <th style={{ width: 90 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.map((item) => (
                <tr key={item.id} className={item.确认状态 === '已确认' ? 'row-confirmed' : ''}>
                  <td>{item.关联对象}</td>
                  <td>{item.影响说明}</td>
                  <td>
                    {item.差异摘要 || (
                      <span>
                        <del className="diff-old">{item.旧值 || '—'}</del>
                        {' → '}
                        <ins className="diff-new">{item.新值 || '—'}</ins>
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`tag ${item.确认状态 === '已确认' ? 'ok' : 'warn'}`}>
                      {item.确认状态}
                    </span>
                  </td>
                  <td>
                    {item.确认状态 === '已确认' ? (
                      <span className="muted">已闭环</span>
                    ) : (
                      <button className="link" type="button" disabled={acting} onClick={() => void handleConfirm(item)}>
                        确认
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      {!loading && items.length === 0 && <p className="empty-state">该批次暂无影响项</p>}
      {message && <p className="error-text impact-message">{message}</p>}
    </Modal>
  )
}
