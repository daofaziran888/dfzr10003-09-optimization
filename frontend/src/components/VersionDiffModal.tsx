import { useEffect, useState } from 'react'

import Modal from '@/components/Modal'
import { request } from '@/api/client'

type MappingChange = {
  源字段: string
  旧目标字段: string
  新目标字段: string
  变化类型: string
}

type VersionDiff = {
  批次编号: string
  关联血缘节点: string
  引用节点版本: string
  引用映射: string
  当前生效版本: string
  当前生效映射: string
  是否落后: boolean
  映射差异: MappingChange[]
}

type Props = {
  open: boolean
  batchId: number | null
  onClose: () => void
}

/** 历史重跑页：指出批次引用的节点版本，以及与当前新版本的映射差异。 */
export default function VersionDiffModal({ open, batchId, onClose }: Props) {
  const [diff, setDiff] = useState<VersionDiff | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || batchId === null) {
      return
    }
    let cancelled = false
    setError('')
    request(`/api/backfill/${batchId}/version-diff`)
      .then(async (response) => {
        const payload = await response.json()
        if (!response.ok) {
          throw new Error(payload.detail || '版本差异读取失败')
        }
        if (!cancelled) {
          setDiff(payload as VersionDiff)
        }
      })
      .catch((errorLike: unknown) => {
        if (!cancelled) {
          setError(errorLike instanceof Error ? errorLike.message : '版本差异读取失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [open, batchId])

  return (
    <Modal
      open={open}
      title={diff ? `引用版本差异：${diff.批次编号}` : '引用版本差异'}
      onClose={onClose}
      width={860}
      footer={<span className="modal-hint">已开始的批次继续由旧版本服务，差异仅用于排查补数口径</span>}
    >
      {error && <p className="error-text">{error}</p>}
      {diff && (
        <div className="diff-panel">
          <section className="impact-summary">
            <div><span>关联节点</span><strong>{diff.关联血缘节点 || '—'}</strong></div>
            <div>
              <span>引用版本</span>
              <strong>{diff.引用节点版本 || '—'}</strong>
            </div>
            <div>
              <span>当前生效版本</span>
              <strong>{diff.当前生效版本 || '—'}</strong>
            </div>
            <div>
              <span>版本状态</span>
              <strong>
                <span className={`tag ${diff.是否落后 ? 'warn' : 'ok'}`}>
                  {diff.是否落后 ? '版本落后' : '与生效版本一致'}
                </span>
              </strong>
            </div>
          </section>
          <section className="impact-group">
            <h4>批次引用映射（补数实际使用）</h4>
            <pre className="mapping-snapshot">{diff.引用映射 || '—'}</pre>
          </section>
          <section className="impact-group">
            <h4>当前生效映射</h4>
            <pre className="mapping-snapshot">{diff.当前生效映射 || '—'}</pre>
          </section>
          <section className="impact-group">
            <h4>新版本差异（{diff.映射差异.length} 项）</h4>
            {diff.映射差异.length === 0 ? (
              <p className="empty-state">两版映射一致，没有差异</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr><th>源字段</th><th>引用版本目标</th><th>新版本目标</th><th>变化类型</th></tr>
                </thead>
                <tbody>
                  {diff.映射差异.map((change) => (
                    <tr key={change.源字段}>
                      <td>{change.源字段}</td>
                      <td><del className="diff-old">{change.旧目标字段 || '—'}</del></td>
                      <td><ins className="diff-new">{change.新目标字段 || '—'}</ins></td>
                      <td><span className="tag info">{change.变化类型}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </Modal>
  )
}
