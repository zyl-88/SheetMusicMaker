/**
 * abcjs 把 `Q:1/4=166.71` 一律按 `parseInt` 解析（tempo.bpm = 166），
 * 页面播放按整数 BPM 累积，93 小节会慢 0.3s 以上，与下载的 MusicXML/MIDI 脱节。
 * 这里在渲染前用 ABC 原文的 2 位小数回填 tempo 元素，导出文本保持真实值不变。
 */

import type { TuneObject, VoiceItemTempo } from 'abcjs'

const Q_LINE = /^[ \t]*Q[ \t]*:/
const NUMBER = /[0-9]+(?:\.[0-9]+)?/

/** 按文件顺序读取所有 Q 字段的原始数值（表头 Q 在前，正文变更行在后）。 */
export function extractTempoValues(abc: string): number[] {
  const values: number[] = []
  for (const line of abc.split(/\r?\n/)) {
    if (!Q_LINE.test(line)) continue
    const body = line.slice(line.indexOf(':') + 1)
    // Q:1/4=166.71 / Q:C=120 取最后一个 = 之后；Q:120 直接取数字
    const eq = body.lastIndexOf('=')
    const source = eq >= 0 ? body.slice(eq + 1) : body
    const m = source.match(NUMBER)
    if (m) values.push(Number.parseFloat(m[0]))
  }
  return values
}

/** 把 ABC 原文的精确速度回填到 abcjs 的 tempo 元素（就地修改 tune）。 */
export function restoreTempoDecimals(tune: TuneObject, abc: string): void {
  const values = extractTempoValues(abc)
  if (!values.length) return

  let i = 0
  const header = tune.metaText?.tempo
  if (header) {
    const v = values[i]
    if (Number.isFinite(v)) header.bpm = v
    i += 1
  }

  const elems: VoiceItemTempo[] = []
  for (const line of tune.lines ?? []) {
    for (const staff of line.staff ?? []) {
      for (const voice of staff.voices ?? []) {
        for (const el of voice) {
          if (el.el_type === 'tempo') elems.push(el)
        }
      }
    }
  }
  for (const el of elems) {
    if (i >= values.length) break
    const v = values[i]
    i += 1
    if (Number.isFinite(v)) el.bpm = v
  }
}
