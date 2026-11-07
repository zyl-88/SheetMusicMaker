/** 乐器信息（GET /api/instruments 返回的单项）。 */
export interface InstrumentInfo {
  id: string
  name: string
  min_freq: number
  max_freq: number
}

/** 调性检测结果。 */
export interface TaskKey {
  tonic: string
  mode: 'major' | 'minor'
}

/** 任务完成后的结果。 */
export interface TaskResult {
  abc: string
  key: TaskKey
  bpm: number
  time_signature: string
  instrument_name: string
  downloads: Record<string, string>
  /** 后端分析阶段的置信度提示（如 BPM 疑似倍速/半速）；仅提示，不影响谱面内容。 */
  warnings?: string[]
}

/** 任务状态响应（GET /api/tasks/{id}）。 */
export interface TaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  result: TaskResult | null
  error: string | null
}

/** 前端状态机阶段。 */
export type Phase = 'idle' | 'uploading' | 'polling' | 'completed' | 'failed'

/** 乐器 id → GM MIDI program 编号（供 abcjs 播放音色）。 */
export const INSTRUMENT_PROGRAM: Record<string, number> = {
  universal: 0,   // 钢琴
  flute: 73,
  violin: 40,
  recorder: 74,
  oboe: 68,
}

/** 流水线阶段定义（镜像后端 STAGE_LABELS + 进度区间）。 */
export const STAGES: { id: string; label: string; range: [number, number] }[] = [
  { id: 'validating', label: '校验音频文件', range: [0, 5] },
  { id: 'separating', label: '分离人声主旋律', range: [5, 35] },
  { id: 'transcribing', label: '转录音符', range: [35, 55] },
  { id: 'analyzing', label: '分析节拍与调性', range: [55, 70] },
  { id: 'quantizing', label: '量化音符时值', range: [70, 85] },
  { id: 'exporting', label: '生成乐谱', range: [85, 100] },
]

/** 本地兜底乐器数据（API 不可用时使用）。 */
export const FALLBACK_INSTRUMENTS: InstrumentInfo[] = [
  { id: 'universal', name: '通用乐谱 Universal', min_freq: 27.5, max_freq: 4186.01 },
  { id: 'flute', name: '长笛 Flute', min_freq: 261.63, max_freq: 2349.32 },
  { id: 'violin', name: '小提琴 Violin', min_freq: 196.0, max_freq: 2637.02 },
  { id: 'recorder', name: '竖笛 Recorder', min_freq: 261.63, max_freq: 2349.32 },
  { id: 'oboe', name: '双簧管 Oboe', min_freq: 233.08, max_freq: 1567.98 },
]

/** 音频限制常量（镜像后端 config.py）。 */
export const MAX_FILE_SIZE = 50 * 1024 * 1024
export const ALLOWED_EXTENSIONS = new Set(['.mp3', '.wav', '.m4a', '.flac'])
