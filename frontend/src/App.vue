<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, computed } from 'vue'
import type { InstrumentInfo } from './types'
import { FALLBACK_INSTRUMENTS } from './types'
import { getHealth, getInstruments } from './api/client'
import { useTranscription } from './composables/useTranscription'
import FileUpload from './components/FileUpload.vue'
import InstrumentSelect from './components/InstrumentSelect.vue'
import ProgressTracker from './components/ProgressTracker.vue'
import SheetMusicView from './components/SheetMusicView.vue'
import PlaybackControls from './components/PlaybackControls.vue'
import DownloadButtons from './components/DownloadButtons.vue'

const transcription = useTranscription()

const instruments = ref<InstrumentInfo[]>([])
const selectedInstrument = ref('')
const selectedFile = ref<File | null>(null)
const uploadError = ref('')
const modelsReady = ref(false)
const visualObj = shallowRef<any>(null)

const canSubmit = computed(() => !!selectedFile.value && !!selectedInstrument.value)

const isUploadPhase = computed(() => transcription.phase.value === 'idle')
const isFailed = computed(() => transcription.phase.value === 'failed')
const isProcessing = computed(
  () => transcription.phase.value === 'uploading' || transcription.phase.value === 'polling',
)
const isCompleted = computed(() => transcription.phase.value === 'completed')

const keyDisplay = computed(() => {
  const k = transcription.result.value?.key
  if (!k) return ''
  return `${k.tonic}${k.mode === 'minor' ? 'm' : ''}`
})

const warnings = computed(() => transcription.result.value?.warnings ?? [])

onMounted(async () => {
  // 拉取健康状态
  try {
    const h = await getHealth()
    modelsReady.value = h.models_loaded
  } catch {
    /* 后端未启动，忽略 */
  }

  // 拉取乐器列表
  try {
    instruments.value = await getInstruments()
  } catch {
    instruments.value = FALLBACK_INSTRUMENTS
  }
})

onBeforeUnmount(() => {
  transcription.reset(true)
})

function onFileSelect(file: File) {
  selectedFile.value = file
  uploadError.value = ''
}

function onFileError(msg: string) {
  uploadError.value = msg
}

async function startTranscription() {
  if (!selectedFile.value || !selectedInstrument.value) return
  visualObj.value = null
  await transcription.start(selectedFile.value, selectedInstrument.value)
}

function restart() {
  transcription.reset()
  selectedFile.value = null
  selectedInstrument.value = ''
  uploadError.value = ''
  visualObj.value = null
}

function onRendered(v: any) {
  visualObj.value = v
}
</script>

<template>
  <div class="app-shell">
    <!-- 头部 -->
    <header class="app-header">
      <div class="header-inner">
        <div>
          <h1>乐谱制作器</h1>
          <p class="subtitle">上传音频 · 自动生成演奏乐谱</p>
        </div>
        <div class="health-badge" :class="{ ready: modelsReady }">
          <span class="badge-dot"></span>
          {{ modelsReady ? '模型就绪' : '加载中…' }}
        </div>
      </div>
    </header>

    <main class="app-main">
      <!-- 上传态 -->
      <section v-if="isUploadPhase" class="upload-section">
        <div class="card upload-card">
          <div class="label mb-2">第一步 · 选择音频文件</div>
          <FileUpload
            :disabled="false"
            :selected-file="selectedFile"
            @select="onFileSelect"
            @error="onFileError"
          />
          <div v-if="uploadError" class="upload-error text-error mt-1">{{ uploadError }}</div>
        </div>

        <div class="card instrument-card-section">
          <div class="label mb-2">第二步 · 选择乐谱模式</div>
          <InstrumentSelect
            v-model="selectedInstrument"
            :instruments="instruments"
            :disabled="false"
          />
        </div>

        <div class="submit-area">
          <button class="btn btn-primary submit-btn" :disabled="!canSubmit" @click="startTranscription">
            开始转录
          </button>
        </div>
      </section>

      <!-- 处理态 -->
      <section v-else-if="isProcessing" class="processing-section">
        <div class="card">
          <div class="processing-header">
            <h2>正在生成乐谱</h2>
            <div class="processing-meta" v-if="selectedFile">
              <span class="text-soft">{{ selectedFile.name }}</span>
            </div>
          </div>
          <ProgressTracker
            :stage="transcription.stage.value"
            :progress="transcription.progress.value"
            :status="transcription.status.value"
            :error="transcription.error.value"
          />
        </div>
      </section>

      <!-- 失败态 -->
      <section v-else-if="isFailed" class="processing-section">
        <div class="card">
          <div class="processing-header">
            <h2>处理失败</h2>
            <div class="processing-meta text-error" v-if="transcription.error.value">
              {{ transcription.error.value }}
            </div>
          </div>
          <div class="retry-area">
            <button class="btn btn-secondary" @click="restart">重新开始</button>
          </div>
        </div>
      </section>

      <!-- 结果态 -->
      <section v-else-if="isCompleted && transcription.result.value" class="result-section">
        <!-- 置信度提示（仅提示，不影响谱面内容） -->
        <div v-if="warnings.length" class="card notice-card">
          <div class="notice-title">速度检测提示</div>
          <ul class="notice-list">
            <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
          </ul>
          <p class="notice-note">
            乐谱内容（音符、节奏、小节）不受影响，仅速度标记可能有偏差；如需准确速度，可对照原曲手动调整。
          </p>
        </div>

        <!-- 元数据条 -->
        <div class="card metadata-bar">
          <div class="meta-item">
            <div class="label">调性</div>
            <div class="meta-value">{{ keyDisplay }}</div>
          </div>
          <div class="meta-item">
            <div class="label">速度</div>
            <div class="meta-value">{{ transcription.result.value.bpm.toFixed(0) }} BPM</div>
          </div>
          <div class="meta-item">
            <div class="label">拍号</div>
            <div class="meta-value">{{ transcription.result.value.time_signature }}</div>
          </div>
          <div class="meta-item">
            <div class="label">乐器</div>
            <div class="meta-value">{{ transcription.result.value.instrument_name }}</div>
          </div>
        </div>

        <!-- 乐谱渲染 -->
        <div class="card score-card">
          <div class="label mb-2">乐谱</div>
          <SheetMusicView
            :abc="transcription.result.value.abc"
            @rendered="onRendered"
          />
        </div>

        <!-- 播放控件 -->
        <div class="card playback-card" v-if="visualObj">
          <div class="label mb-2">试听</div>
          <PlaybackControls
            :visual-obj="visualObj"
            :instrument-id="selectedInstrument"
          />
          <p class="play-hint text-soft">点击播放按钮试听（需浏览器允许音频播放）</p>
        </div>

        <!-- 下载 -->
        <div class="card download-card">
          <DownloadButtons
            :task-id="transcription.taskId.value"
            :abc="transcription.result.value.abc"
            @reset="restart"
          />
        </div>
      </section>
    </main>

    <footer class="app-footer">
      <span class="text-soft">乐谱制作器 · 本地部署工具</span>
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  border-bottom: 1px solid var(--rule);
  background: var(--paper-card);
}

.header-inner {
  max-width: 880px;
  margin: 0 auto;
  padding: 24px 24px 20px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
}

.subtitle {
  color: var(--ink-soft);
  font-size: 0.9rem;
  margin-top: 2px;
}

.health-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: var(--ink-soft);
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--paper);
}

.badge-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--gold-soft);
}

.health-badge.ready .badge-dot {
  background: var(--ok);
}

.app-main {
  flex: 1;
  max-width: 880px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 24px;
}

/* ---- 上传态 ---- */
.upload-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.upload-card,
.instrument-card-section {
  padding: 24px;
}

.submit-area {
  display: flex;
  justify-content: center;
  margin-top: 8px;
}

.submit-btn {
  padding: 14px 40px;
  font-size: 1rem;
}

.upload-error {
  font-size: 0.85rem;
}

/* ---- 处理态 ---- */
.processing-section {
  max-width: 600px;
  margin: 0 auto;
}

.processing-header {
  margin-bottom: 24px;
}

.processing-meta {
  margin-top: 4px;
  font-size: 0.85rem;
}

.retry-area {
  margin-top: 24px;
  text-align: center;
}

/* ---- 结果态 ---- */
.result-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.metadata-bar {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  padding: 20px 24px;
}

/* 提示态用黄铜色，不用红色：这不是失败，只是速度标记存疑 */
.notice-card {
  padding: 18px 24px;
  border-top-color: var(--gold-soft);
  background: var(--paper-card);
}

.notice-title {
  font-family: var(--font-display);
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--gold);
  margin-bottom: 8px;
}

.notice-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.notice-list li {
  font-size: 0.88rem;
  color: var(--ink);
  padding-left: 14px;
  position: relative;
}

.notice-list li::before {
  content: '·';
  position: absolute;
  left: 2px;
  color: var(--gold-soft);
}

.notice-note {
  margin-top: 10px;
  font-size: 0.82rem;
  color: var(--ink-soft);
}

.meta-item {
  text-align: center;
}

.meta-value {
  font-family: var(--font-display);
  font-size: 1.3rem;
  font-weight: 600;
  color: var(--ink);
  margin-top: 2px;
}

.score-card {
  padding: 24px;
}

.playback-card {
  padding: 20px 24px;
}

.play-hint {
  font-size: 0.78rem;
  margin-top: 10px;
}

.download-card {
  padding: 24px;
}

/* ---- 页脚 ---- */
.app-footer {
  text-align: center;
  padding: 20px;
  border-top: 1px solid var(--rule);
  font-size: 0.8rem;
}

/* ---- 响应式 ---- */
@media (max-width: 640px) {
  .metadata-bar {
    grid-template-columns: repeat(2, 1fr);
  }
  .header-inner {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
}
</style>
