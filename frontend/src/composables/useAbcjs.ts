/** abcjs 渲染 + 播放封装。 */
import abcjs from 'abcjs'
import { INSTRUMENT_PROGRAM } from '../types'

export function useAbcjs() {
  let synthControl: abcjs.SynthObjectController | null = null

  /** 渲染 ABC 到指定元素，返回 visualObj 供播放使用。 */
  function renderAbc(el: HTMLElement, abc: string): abcjs.TuneObject | null {
    if (!abc) return null
    const visualObjs = abcjs.renderAbc(el, abc, {
      responsive: 'resize',
      add_classes: true,
      staffwidth: 740,
      paddingleft: 16,
      paddingright: 16,
      paddingtop: 16,
      paddingbottom: 24,
      foregroundColor: '#2a241d',
    })
    return visualObjs[0] ?? null
  }

  /**
   * 设置播放控件。
   * userAction=false：音频缓冲延迟到用户点击 play 时才创建（满足 AudioContext 手势约束）。
   */
  function setupPlayback(
    audioEl: HTMLElement,
    visualObj: abcjs.TuneObject,
    instrumentId: string,
  ) {
    if (!visualObj) return

    if (!synthControl) {
      synthControl = new abcjs.synth.SynthController()
      synthControl.load(audioEl, null, {
        displayPlay: true,
        displayProgress: true,
        displayRestart: true,
        displayLoop: false,
        displayWarp: false,
      })
    }

    synthControl.setTune(visualObj, false, {
      soundFontUrl: '/soundfont/',
      soundFontVolumeMultiplier: 3.0,
      program: INSTRUMENT_PROGRAM[instrumentId] ?? 0,
    })
  }

  function dispose() {
    synthControl = null
  }

  return { renderAbc, setupPlayback, dispose }
}
