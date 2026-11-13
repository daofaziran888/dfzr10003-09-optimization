import { useState } from 'react'

import Modal from '@/components/Modal'
import { analyzeNode, type ImpactResult } from '@/api/impact'

type LineageRow = Record<string, string | number | null>

type Props = {
  open: boolean
  row: LineageRow | null
  onClose: () => void
  onAnalyzed: (result: ImpactResult, message: string) => void
}

/** 发布前影响分析入口：填写待生效映射，计算上游/下游/映射/元数据/重跑影响。 */
export default function AnalyzeModal({ open, row, onClose, onAnalyzed }: Props) {
  const [newMapping, setNewMapping] = useState('')
  const [newVersion, setNewVersion] = useState('')
  const [effectiveTime, setEffectiveTime] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  function reset() {
    setNewMapping('')
    setNewVersion('')
    setEffectiveTime('')
    setError('')
  }

  function handleClose() {
    reset()
    onClose()
  }

  async function handleSubmit() {
    if (!row) {
      return
    }
    if (!newMapping.trim()) {
      setError('请填写待生效字段映射（格式：源字段->目标字段，多个用逗号分隔）')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const result = await analyzeNode(Number(row.id), {
        new_mapping: newMapping,
        new_version: newVersion,
        effective_time: effectiveTime,
      })
      if (!result.data.ok || !result.data.entry) {
        throw new Error(result.data.message)
      }
      onAnalyzed(result.data.entry, result.data.message)
      reset()
    } catch (errorLike) {
      const detail = errorLike instanceof Error ? errorLike.message : '影响分析失败'
      setError(detail.includes('Failed to fetch') ? '接口请求失败，请确认后端服务已启动' : detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      title={row ? `发布前影响分析：${row.节点编号}` : '影响分析'}
      onClose={handleClose}
      width={760}
      footer={
        <>
          <button className="btn ghost" type="button" onClick={handleClose}>取消</button>
          <button className="btn primary" type="button" disabled={submitting} onClick={() => void handleSubmit()}>
            {submitting ? '分析中…' : '计算影响范围'}
          </button>
        </>
      }
    >
      {row && (
        <div className="analyze-form">
          <label className="form-block">
            <span>当前生效映射（{row.图谱版本}，旧版本继续服务已开始的批次）</span>
            <textarea readOnly rows={3} value={String(row.字段映射 ?? '')} />
          </label>
          <label className="form-block">
            <span>待生效字段映射 *</span>
            <textarea
              rows={3}
              placeholder="order_id->order_id, order_amt->order_amount_with_tax"
              value={newMapping}
              onChange={(event) => setNewMapping(event.target.value)}
            />
          </label>
          <div className="form-row">
            <label className="form-block">
              <span>新版本号（留空自动递增）</span>
              <input placeholder="如 v1.1" value={newVersion} onChange={(e) => setNewVersion(e.target.value)} />
            </label>
            <label className="form-block">
              <span>生效时间（留空取当前时间）</span>
              <input placeholder="2026-11-20 00:00:00" value={effectiveTime} onChange={(e) => setEffectiveTime(e.target.value)} />
            </label>
          </div>
          <p className="modal-hint">
            分析将沿血缘节点 → 下游表字段 → 元数据目录、以及关联重跑批次两条链路定位影响；
            同节点重复分析幂等，存在未关闭批次时新变更会被拒绝。
          </p>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}
    </Modal>
  )
}
