/** 转录状态管理 composable：上传 → 轮询 → 结果。 */
import { ref, shallowRef } from 'vue'
import type { Phase, TaskResult } from '../types'
import { MAX_FILE_SIZE, ALLOWED_EXTENSIONS } from '../types'
import { submitTranscription, pollTask, deleteTask } from '../api/client'

/** 校验文件：返回 null 表示通过，返回字符串为错误消息。 */
export function validateFile(file: File): string | null {
  const dotIdx = file.name.lastIndexOf('.')
  const ext = dotIdx >= 0 ? file.name.slice(dotIdx).toLowerCase() : ''
  if (!ALLOWED_EXTENSIONS.has(ext)) return '仅支持 MP3 / WAV / M4A / FLAC 格式'
  if (file.size > MAX_FILE_SIZE) return '文件大小超过 50MB 限制'
  return null
}

export function useTranscription() {
  const phase = ref<Phase>('idle')
  const taskId = ref('')
  const status = ref('')
  const stage = ref('')
  const progress = ref(0)
  const result = shallowRef<TaskResult | null>(null)
  const error = ref('')
  let abort: AbortController | null = null

  /** 启动转录流程：上传 → 轮询。 */
  async function start(file: File, instrumentId: string) {
    const validationError = validateFile(file)
    if (validationError) {
      error.value = validationError
      phase.value = 'failed'
      return
    }

    reset(true)
    phase.value = 'uploading'

    try {
      const { task_id } = await submitTranscription(file, instrumentId)
      taskId.value = task_id
      phase.value = 'polling'

      abort = new AbortController()
      const finalStatus = await pollTask(
        task_id,
        (s) => {
          status.value = s.status
          stage.value = s.stage
          progress.value = s.progress
        },
        abort.signal,
      )

      // 轮询响应在 completed 时已包含 result
      result.value = finalStatus.result
      phase.value = 'completed'
    } catch (e: unknown) {
      // AbortError 是主动取消，静默忽略
      if (e instanceof DOMException && e.name === 'AbortError') return
      error.value = e instanceof Error ? e.message : '未知错误'
      phase.value = 'failed'
    }
  }

  /** 重置状态。silent=true 时仅取消轮询不删除后端任务。 */
  function reset(silent = false) {
    abort?.abort()
    abort = null
    if (!silent && taskId.value) {
      deleteTask(taskId.value).catch(() => {})
    }
    phase.value = 'idle'
    taskId.value = ''
    status.value = ''
    stage.value = ''
    progress.value = 0
    result.value = null
    error.value = ''
  }

  return { phase, taskId, status, stage, progress, result, error, start, reset }
}
