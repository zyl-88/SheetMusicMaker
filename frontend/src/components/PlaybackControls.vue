<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import abcjs from 'abcjs'
import { INSTRUMENT_PROGRAM } from '../types'

const props = defineProps<{ visualObj: abcjs.TuneObject | null; instrumentId: string }>()
const emit = defineEmits<{ error: [msg: string] }>()

const audioEl = ref<HTMLElement>()
let ctrl: abcjs.SynthObjectController | null = null

function ensureControl() {
  if (!ctrl && audioEl.value) {
    ctrl = new abcjs.synth.SynthController()
    ctrl.load(audioEl.value, null, {
      displayPlay: true,
      displayProgress: true,
      displayRestart: true,
      displayLoop: false,
      displayWarp: false,
    })
  }
}

function load() {
  if (!props.visualObj || !audioEl.value) return
  try {
    ensureControl()
    if (!ctrl) return
    ctrl.setTune(props.visualObj, false, {
      soundFontUrl: '/soundfont/',
      soundFontVolumeMultiplier: 3.0,
      program: INSTRUMENT_PROGRAM[props.instrumentId] ?? 0,
    })
  } catch (e: unknown) {
    emit('error', e instanceof Error ? e.message : '音色加载失败')
  }
}

onMounted(load)
watch(() => [props.visualObj, props.instrumentId], load)
onBeforeUnmount(() => {
  ctrl = null
})
</script>

<template>
  <div ref="audioEl" class="audio-control"></div>
</template>

<style scoped>
.audio-control {
  min-height: 44px;
}
</style>
