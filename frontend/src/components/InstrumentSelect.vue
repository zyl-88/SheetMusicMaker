<script setup lang="ts">
import { computed } from 'vue'
import type { InstrumentInfo } from '../types'
import { FALLBACK_INSTRUMENTS } from '../types'

const props = defineProps<{
  modelValue: string
  instruments: InstrumentInfo[]
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()

// props.instruments 为异步加载，需用 computed 保证响应式
const list = computed<InstrumentInfo[]>(() =>
  props.instruments.length > 0 ? props.instruments : FALLBACK_INSTRUMENTS,
)

// 将通用乐谱分离出来单独展示
const universalItem = computed(() => list.value.find((i) => i.id === 'universal'))
const instrumentList = computed(() => list.value.filter((i) => i.id !== 'universal'))

function select(id: string) {
  if (props.disabled) return
  emit('update:modelValue', id)
}

function formatFreq(hz: number): string {
  return hz >= 1000 ? (hz / 1000).toFixed(1) + ' kHz' : hz.toFixed(0) + ' Hz'
}
</script>

<template>
  <div class="instrument-section">
    <!-- 通用乐谱选项（突出显示） -->
    <div
      v-if="universalItem"
      class="instrument-card universal-card"
      :class="{ selected: modelValue === universalItem.id, disabled: disabled }"
      @click="select(universalItem.id)"
    >
      <div class="inst-name">{{ universalItem.name }}</div>
      <div class="inst-desc">不限制音域，提取完整主旋律</div>
    </div>

    <div class="divider" v-if="universalItem">
      <span>或选择特定乐器</span>
    </div>

    <!-- 特定乐器网格 -->
    <div class="instrument-grid">
      <div
        v-for="inst in instrumentList"
        :key="inst.id"
        class="instrument-card"
        :class="{ selected: modelValue === inst.id, disabled: disabled }"
        @click="select(inst.id)"
      >
        <div class="inst-name">{{ inst.name }}</div>
        <div class="inst-range">
          {{ formatFreq(inst.min_freq) }} – {{ formatFreq(inst.max_freq) }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.instrument-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.universal-card {
  padding: 18px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--paper-card);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  border-left: 3px solid var(--gold-soft);
}

.universal-card:hover:not(.disabled) {
  border-color: var(--gold-soft);
  border-left-color: var(--gold);
}

.universal-card.selected {
  border-color: var(--gold);
  border-left: 3px solid var(--gold);
  border-width: 2px;
  border-left-width: 3px;
  box-shadow: 0 0 0 1px var(--gold-tint);
  padding: 17px;
}

.universal-card.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.inst-desc {
  font-size: 0.8rem;
  color: var(--ink-soft);
  margin-top: 4px;
}

.divider {
  text-align: center;
  position: relative;
  margin: 4px 0;
}

.divider::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--rule);
}

.divider span {
  position: relative;
  background: var(--paper);
  padding: 0 12px;
  font-size: 0.78rem;
  color: var(--ink-soft);
}

.instrument-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.instrument-card {
  padding: 16px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--paper-card);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.instrument-card:hover:not(.disabled) {
  border-color: var(--gold-soft);
}

.instrument-card.selected {
  border-color: var(--gold);
  border-width: 2px;
  box-shadow: 0 0 0 1px var(--gold-tint);
  padding: 15px;
}

.instrument-card.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.inst-name {
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 4px;
}

.inst-range {
  font-size: 0.75rem;
  color: var(--ink-soft);
  letter-spacing: 0.02em;
}

@media (max-width: 640px) {
  .instrument-grid {
    grid-template-columns: 1fr;
  }
}
</style>
