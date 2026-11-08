# 75c6485 前端播放对齐：独立验证

- 验证时间：2026-11-07
- 待测提交：`75c6485`（`fix(前端): 用 ABC 原文小数回填 tempo，修复页面播放按整数 BPM 漂移`）
- 改动范围：仅 3 个前端文件（`SheetMusicView.vue` +6、`useAbcjs.ts` +5、新增 `utils/abcTempo.ts` 56 行），**未改后端/导出**。
- 对照产物：`c9f5b61` 全曲复测的 `score.abc` / `score.midi`（431 音）。
- 判定：**该修复有效，开发方结论成立。**

## 0. 测量方式与口径（先说明，避免混淆）

未启动浏览器做真实音频回放。改用**页面实际加载的同一份 abcjs（6.6.3）+ 生产 TS 源码**在 Node 中复算：

1. `abcjs.parseOnly(score.abc)` 得到 tune；
2. 调用 `frontend/src/utils/abcTempo.ts` 的 `restoreTempoDecimals(tune, abc)`（用 esbuild 直接编译该生产源文件，不重写逻辑）；
3. 读取 `tune.setUpAudio()` 返回的 `tracks[0]` 中每个 `note` 事件的 `start`/`duration`，按 `240 / commands.tempo` 换算为秒。

`setUpAudio()` 正是浏览器里 `CreateSynth` 播放时调用的同一函数（`abcjs/src/synth/create-synth.js:116`），因此这条时间轴等价于页面播放时间轴；**不是**另写一套 ABC 解析器。音符起点与下载 `score.midi` 按顺序逐对比较（两侧均为 431 音、音高序列完全一致）。

另外做了一组交叉核验：用 abcjs 的 `synth.getMidiFile(tune)` 生成页面 MIDI，再与下载 MIDI 对比（这条路会经过 abcjs 的 ms 取整，误差更大，仅作旁证）。

## 1. 结果：页面播放 vs 下载 MIDI（431 对音符起点）

| 指标 | 修复后（`restoreTempoDecimals`） | 未修复（原生 parseInt） |
|---|---|---|
| 时间轴 tempo（首小节） | 147.66（照文档原值） | 147（截断） |
| 中位差（有符号） | **+0.002ms** | +162.3ms |
| 中位差（绝对值） | **0.002ms** | 162.3ms |
| P90 | **0.005ms** | 302.7ms |
| P99 | 0.008ms | 320.7ms |
| 最大差 | **0.0095ms** | 327.6ms |
| >120ms 计数 | **0/431** | **289/431** |
| 谱末时间（page vs MIDI） | 138.088702 vs 138.088702（<1µs） | +328.5ms |

结论：修复后 431 个起点全部在 **10µs 内**；未修复时中位 162ms、最大 327.6ms，与上一轮按小节累计的 0.329s 一致。

交叉核验（abcjs 生成 MIDI 再对比）：修复后中位 1.35ms、P90 3.0ms、最大 **4.65ms**、>120ms **0/431**；未修复中位 166.9ms、最大 342.1ms、>120ms 290/431。该路径因 abcjs 内部毫秒取整而略大，但仍远低于 120ms 阈值。

## 2. 与开发方自测数字的对照

| 开发方说法 | 本轮实测 | 说明 |
|---|---|---|
| 中位 0.3ms | 0.002ms（synth 时间轴）/ 1.35ms（页面 MIDI 路径） | 口径不同；两种口径都比 0.3ms 更好或同量级 |
| 最大 0.5ms | 0.009ms（synth 时间轴）/ 4.65ms（页面 MIDI 路径） | **未能逐值复现 0.5ms**；两口径分别更小/更大 |
| >120ms 为 0/431 | **0/431** | ✅ 一致 |
| 谱末 138.089 vs MIDI 138.0887 | 138.08870225 vs 138.08870225 | ✅ 一致（开发方的 0.3ms 是三位小数取整造成的表象） |

开发方“中位 0.3ms / 最大 0.5ms”的精确口径我无法复现，但结论（全部对齐全、无 >120ms 漂移、谱末一致）独立成立。

## 3. `useAbcjs.ts` 是否死代码：**属实**

- 全仓前端检索 `useAbcjs`：仅命中定义文件自身，**没有任何组件/入口 import 它**。
- 实际播放路径是 `SheetMusicView.vue`（`afterParsing` 里调用 `restoreTempoDecimals`）→ `emit('rendered', tune)` → `PlaybackControls.vue` 直接 `new abcjs.synth.SynthController()` 并 `setTune(props.visualObj)`。修复通过 `visualObj` 传递到播放端，`useAbcjs.ts` 的 `renderAbc/setupPlayback` 均未参与。
- 影响面判断正确：该文件是死代码（自初始提交 `fd23e6c` 起就未被引用），本次提交只是顺带把修复也写进这份死代码。真正生效的是 `SheetMusicView.vue` 的那 6 行。

## 4. P2 待办（已记录）

- 开发方在 P2 开工前提供**三方一致性基线存档**；我方收到后存档比对。
- 下次全曲复测继续把两项作为常驻检查（**已接入 `retest_song.py`，每次自动跑**）：
  1. 三方一致性：`storage/acceptance/check_export_consistency.py`（自动调用）；
  2. 页面 vs 下载：`storage/acceptance/check_frontend_playback.py`（自动调用，依赖 node/abcjs；失败会在 comparison.md 顶部以 ⚠️ 暴露，不阻断主流程）。
- P2 若改动 tempo 数值，必须复跑并保持：逐小节 tempo 0/93 不一致、音符起点 >120ms 为 0、谱末差异 <1ms。

## 5. 复跑命令

```powershell
# 页面 vs 下载 MIDI（单命令；retest_song.py 下次也会自动调用）
python storage/acceptance/check_frontend_playback.py --run docs/acceptance/retests/song/c9f5b61_20261107-223941_export-tempo-map

# 全曲复测本身（内含三方一致性 + 页面 vs 下载两项）
python storage/acceptance/retest_song.py --label <label>
```
