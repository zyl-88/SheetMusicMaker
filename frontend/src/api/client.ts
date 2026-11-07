/** API 客户端：fetch 封装 + 2 秒轮询。 */
import type { InstrumentInfo, TaskStatus, TaskResult } from '../types'

const POLL_INTERVAL = 2000

/** 通用 fetch 封装：自动提取错误 detail。 */
async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...(init?.headers) },
  })
  if (!res.ok) {
    let detail = '请求失败'
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* 响应非 JSON，使用默认消息 */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** GET /api/health */
export function getHealth(): Promise<{ status: string; models_loaded: boolean }> {
  return http('/api/health')
}

/** GET /api/instruments */
export async function getInstruments(): Promise<InstrumentInfo[]> {
  const data = await http<{ instruments: InstrumentInfo[] }>('/api/instruments')
  return data.instruments
}

/** POST /api/transcribe — multipart 表单，不设 Content-Type（浏览器自动带 boundary）。 */
export function submitTranscription(
  file: File,
  instrument: string,
): Promise<{ task_id: string; status: string }> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('instrument', instrument)
  return http('/api/transcribe', { method: 'POST', body: fd })
}

/** GET /api/tasks/{id} */
export function getTaskStatus(taskId: string): Promise<TaskStatus> {
  return http(`/api/tasks/${taskId}`)
}

/** GET /api/tasks/{id}/result */
export function getResult(taskId: string): Promise<TaskResult> {
  return http(`/api/tasks/${taskId}/result`)
}

/** DELETE /api/tasks/{id} */
export function deleteTask(taskId: string): Promise<{ status: string }> {
  return http(`/api/tasks/${taskId}`, { method: 'DELETE' })
}

/** 下载 URL 构造（GET /api/tasks/{id}/download/{format}）。 */
export function downloadUrl(taskId: string, format: string): string {
  return `/api/tasks/${taskId}/download/${format}`
}

/**
 * 2 秒轮询任务状态。
 * - completed → resolve(finalStatus)
 * - failed → reject(Error)
 * - AbortSignal 取消 → reject(AbortError)
 * - 递归 setTimeout 防止请求堆叠
 */
export function pollTask(
  taskId: string,
  onUpdate: (s: TaskStatus) => void,
  signal?: AbortSignal,
): Promise<TaskStatus> {
  return new Promise((resolve, reject) => {
    let stopped = false
    let networkRetries = 0
    const MAX_NETWORK_RETRIES = 3

    const onAbort = () => {
      stopped = true
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })

    const tick = async () => {
      if (stopped) return
      try {
        const s = await getTaskStatus(taskId)
        networkRetries = 0 // 请求成功则重置重试计数
        onUpdate(s)
        if (s.status === 'completed') {
          stopped = true
          resolve(s)
          return
        }
        if (s.status === 'failed') {
          stopped = true
          reject(new Error(s.error || '处理失败'))
          return
        }
      } catch (e) {
        // 仅对网络层错误（TypeError）做有限重试，避免瞬时抖动误判任务失败
        if (e instanceof TypeError && networkRetries < MAX_NETWORK_RETRIES) {
          networkRetries += 1
          setTimeout(tick, POLL_INTERVAL)
          return
        }
        stopped = true
        reject(e)
        return
      }
      if (!stopped) setTimeout(tick, POLL_INTERVAL)
    }

    tick()
  })
}
