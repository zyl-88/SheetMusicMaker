<script setup lang="ts">
import { computed } from 'vue'
import { STAGES } from '../types'

const props = defineProps<{
  stage: string
  progress: number
  status: string
  error?: string
}>()

const isFailed = computed(() => props.status === 'failed')

function stageState(stageId: string): 'pending' | 'active' | 'done' {
  const idx = STAGES.findIndex((s) => s.id === stageId)
  const currentIdx = STAGES.findIndex((s) => s.id === props.stage)
  if (idx < currentIdx) return 'done'
  if (idx === currentIdx) return 'active'
  return 'pending'
}

const progressStyle = computed(() => ({
  width: `${props.progress}%`,
}))
</script>

<template>
  <div class="progress-tracker">
    <!-- 线性进度条 -->
    <div class="progress-track">
      <div class="progress-fill" :style="progressStyle"></div>
    </div>
    <div class="progress-text">
      <span>{{ progress }}%</span>
      <span v-if="!isFailed" class="text-soft">处理中…</span>
    </div>

    <!-- 阶段列表 -->
    <div class="stages">
      <div
        v-for="s in STAGES"
        :key="s.id"
        class="stage-item"
        :class="stageState(s.id)"
      >
        <div class="stage-dot">
          <span v-if="stageState(s.id) === 'done'" class="check">✓</span>
          <span v-else-if="stageState(s.id) === 'active'" class="pulse"></span>
        </div>
        <div class="stage-label">{{ s.label }}</div>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="isFailed" class="error-box">
      <div class="error-title">处理失败</div>
      <div class="error-msg">{{ error || '未知错误' }}</div>
    </div>
  </div>
</template>

<style scoped>
.progress-tracker {
  width: 100%;
}

.progress-track {
  width: 100%;
  height: 4px;
  background: var(--gold-tint);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--gold);
  border-radius: 2px;
  transition: width 0.5s ease;
}

.progress-text {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 0.85rem;
  color: var(--ink);
}

.stages {
  margin-top: 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.stage-item {
  display: flex;
  align-items: center;
  gap: 12px;
  opacity: 0.4;
  transition: opacity 0.3s;
}

.stage-item.active {
  opacity: 1;
}

.stage-item.done {
  opacity: 0.7;
}

.stage-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--rule);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--paper-card);
}

.stage-item.active .stage-dot {
  border-color: var(--gold);
}

.stage-item.done .stage-dot {
  border-color: var(--ok);
  background: var(--ok);
}

.check {
  color: var(--paper);
  font-size: 0.7rem;
  font-weight: bold;
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--gold);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.7); }
}

.stage-label {
  font-size: 0.9rem;
  color: var(--ink);
}

.stage-item.active .stage-label {
  font-weight: 500;
}

.error-box {
  margin-top: 20px;
  padding: 16px;
  background: rgba(162, 59, 44, 0.06);
  border: 1px solid rgba(162, 59, 44, 0.2);
  border-radius: var(--radius);
}

.error-title {
  font-weight: 600;
  color: var(--err);
  margin-bottom: 4px;
}

.error-msg {
  font-size: 0.85rem;
  color: var(--ink-soft);
}
</style>
