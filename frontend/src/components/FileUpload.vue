<script setup lang="ts">
import { ref } from 'vue'
import { validateFile } from '../composables/useTranscription'

const props = defineProps<{ disabled?: boolean; selectedFile?: File | null }>()
const emit = defineEmits<{ select: [file: File]; error: [msg: string] }>()

const input = ref<HTMLInputElement>()
const isDragging = ref(false)

function handleFile(file: File) {
  const err = validateFile(file)
  if (err) {
    emit('error', err)
    return
  }
  emit('select', file)
}

function onInputChange(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files && target.files[0]) {
    handleFile(target.files[0])
  }
}

function onDrop(e: DragEvent) {
  isDragging.value = false
  if (e.dataTransfer?.files && e.dataTransfer.files[0]) {
    handleFile(e.dataTransfer.files[0])
  }
}

function onDragOver() {
  isDragging.value = true
}

function onDragLeave() {
  isDragging.value = false
}

function openPicker() {
  if (props.disabled) return
  input.value?.click()
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
</script>

<template>
  <div class="upload-zone-wrap">
    <div
      class="upload-zone"
      :class="{ dragging: isDragging, disabled: disabled }"
      @click="openPicker"
      @drop.prevent="onDrop"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
    >
      <input
        ref="input"
        type="file"
        accept=".mp3,.wav,.m4a,.flac"
        hidden
        :disabled="disabled"
        @change="onInputChange"
      />
      <template v-if="selectedFile">
        <div class="file-info">
          <span class="file-icon">♪</span>
          <div>
            <div class="file-name">{{ selectedFile.name }}</div>
            <div class="file-size">{{ formatSize(selectedFile.size) }}</div>
          </div>
        </div>
      </template>
      <template v-else>
        <div class="upload-prompt">
          <div class="upload-icon">♪</div>
          <div class="upload-text">拖拽音频文件到此处</div>
          <div class="upload-hint">或点击选择文件 · MP3 / WAV / M4A / FLAC · ≤50MB</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.upload-zone {
  border: 2px dashed var(--rule);
  border-radius: var(--radius-lg);
  padding: 40px 24px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
  background: var(--paper-card);
}

.upload-zone:hover:not(.disabled) {
  border-color: var(--gold);
  background: var(--gold-tint);
}

.upload-zone.dragging {
  border-color: var(--gold);
  background: var(--gold-tint);
}

.upload-zone.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.upload-icon {
  font-size: 2.5rem;
  color: var(--gold);
  font-family: var(--font-display);
  margin-bottom: 12px;
}

.upload-text {
  font-size: 1rem;
  color: var(--ink);
  margin-bottom: 4px;
}

.upload-hint {
  font-size: 0.8rem;
  color: var(--ink-soft);
}

.file-info {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.file-icon {
  font-size: 1.8rem;
  color: var(--gold);
}

.file-name {
  font-weight: 500;
  color: var(--ink);
}

.file-size {
  font-size: 0.8rem;
  color: var(--ink-soft);
}
</style>
