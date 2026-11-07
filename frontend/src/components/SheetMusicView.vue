<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import abcjs from 'abcjs'
import { restoreTempoDecimals } from '../utils/abcTempo'

const props = defineProps<{ abc: string }>()
const emit = defineEmits<{ rendered: [v: abcjs.TuneObject] }>()

const el = ref<HTMLElement>()

watch(
  () => props.abc,
  async (v) => {
    await nextTick()
    if (!el.value || !v) return
    const objs = abcjs.renderAbc(el.value, v, {
      responsive: 'resize',
      add_classes: true,
      staffwidth: 740,
      paddingleft: 16,
      paddingright: 16,
      paddingtop: 16,
      paddingbottom: 24,
      foregroundColor: '#2a241d',
      // abcjs 用 parseInt 解析 Q:，回填原文小数，避免播放按整数 BPM 累积漂移
      afterParsing: (tune: abcjs.TuneObject) => {
        restoreTempoDecimals(tune, v)
        return tune
      },
    })
    emit('rendered', objs[0])
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="abc" ref="el" class="sheet-paper"></div>
  <div v-else class="sheet-placeholder">
    <span>等待乐谱…</span>
  </div>
</template>

<style scoped>
.sheet-paper {
  width: 100%;
  overflow-x: auto;
}
.sheet-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  color: var(--ink-soft);
  font-family: var(--font-display);
  font-style: italic;
  font-size: 1.2rem;
}
</style>
