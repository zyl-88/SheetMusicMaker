<script setup lang="ts">
import { ref } from 'vue'
import { downloadUrl } from '../api/client'

const props = defineProps<{
  taskId: string
  abc?: string
}>()
const emit = defineEmits<{ reset: [] }>()

const copied = ref(false)

async function copyAbc() {
  if (!props.abc) return
  try {
    await navigator.clipboard.writeText(props.abc)
    copied.value = true
    setTimeout(() => (copied.value = false), 2000)
  } catch {
    /* clipboard 不可用，忽略 */
  }
}

const formats = [
  { id: 'abc', label: 'ABC', desc: '文本乐谱' },
  { id: 'musicxml', label: 'MusicXML', desc: '通用交换' },
  { id: 'midi', label: 'MIDI', desc: '音频播放' },
]
</script>

<template>
  <div class="downloads">
    <div class="label mb-2">下载乐谱</div>
    <div class="download-grid">
      <a
        v-for="fmt in formats"
        :key="fmt.id"
        :href="downloadUrl(taskId, fmt.id)"
        :download="`score.${fmt.id === 'musicxml' ? 'xml' : fmt.id}`"
        class="download-btn"
      >
        <div class="dl-label">{{ fmt.label }}</div>
        <div class="dl-desc">{{ fmt.desc }}</div>
      </a>
    </div>

    <div class="extra-actions">
      <button class="btn btn-secondary" :disabled="!abc" @click="copyAbc">
        {{ copied ? '已复制 ✓' : '复制 ABC' }}
      </button>
      <button class="btn btn-secondary" @click="emit('reset')">重新开始</button>
    </div>
  </div>
</template>

<style scoped>
.downloads {
  width: 100%;
}

.download-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.download-btn {
  display: block;
  padding: 14px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--paper-card);
  text-decoration: none;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.download-btn:hover {
  border-color: var(--gold);
  box-shadow: var(--shadow);
}

.dl-label {
  font-family: var(--font-display);
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 2px;
}

.dl-desc {
  font-size: 0.75rem;
  color: var(--ink-soft);
}

.extra-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
</style>
