import { request } from '@/api/client'

/** 影响分析批次与影响项的前端口径，字段与后端 impact 服务保持一致。 */
export type ImpactItem = {
  id: number
  影响批次: string
  批次id: number
  影响类型: '上游节点' | '下游节点' | '字段映射' | '元数据记录' | '重跑批次'
  关联对象: string
  关联模块: string
  关联记录id: number | null
  影响说明: string
  旧值: string
  新值: string
  差异摘要: string
  确认状态: '待确认' | '已确认'
  确认时间: string
  确认备注: string
}

export type ImpactBatch = {
  id: number
  批次编号: string
  节点id: number
  节点编号: string
  节点名称: string
  旧版本: string
  新版本: string
  旧映射: string
  新映射: string
  生效时间: string
  分析指纹: string
  状态: '待处理' | '已完成'
  创建时间: string
  关闭时间: string
  备注: string
  待确认数: number
  已确认数: number
  影响项总数: number
}

export type ImpactResult = {
  batch: ImpactBatch
  items: ImpactItem[]
}

export async function postJson<T>(path: string, body: unknown): Promise<{ status: number; data: T }> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const data = (await response.json()) as T
  return { status: response.status, data }
}

/** 发起影响分析：201 首次建批，200 幂等复用，409 存在未关闭批次的新变更。 */
export async function analyzeNode(nodeId: number, values: Record<string, string>) {
  return postJson<{ ok: boolean; message: string; entry?: ImpactResult }>(
    `/api/impact/nodes/${nodeId}/analyze`,
    { values },
  )
}

export async function confirmImpactItem(itemId: number, remark = '逐项核对无误') {
  return postJson<{ ok: boolean; message: string }>(
    `/api/impact/items/${itemId}/confirm`,
    { values: {}, remark },
  )
}

export async function closeImpactBatch(batchId: number) {
  return postJson<{ ok: boolean; message: string; entry?: ImpactResult }>(
    `/api/impact/batches/${batchId}/close`,
    {},
  )
}

export async function fetchImpactBatch(nodeCode: string): Promise<ImpactBatch[]> {
  const response = await request(`/api/impact/batches?node=${encodeURIComponent(nodeCode)}`)
  if (!response.ok) {
    throw new Error('影响清单读取失败')
  }
  const payload = (await response.json()) as { items: ImpactBatch[] }
  return payload.items ?? []
}

export async function fetchBatchDetail(batchId: number): Promise<ImpactResult> {
  const response = await request(`/api/impact/batches/${batchId}`)
  if (!response.ok) {
    throw new Error('影响批次详情读取失败')
  }
  return (await response.json()) as ImpactResult
}
