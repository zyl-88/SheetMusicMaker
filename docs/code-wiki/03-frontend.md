# 03 · 前端模块与关键组件

> 前端为 Vue 3 + TypeScript + Vite 的 SPA，无路由、无状态库，**单个页面 + 6 个组件 + 2 个 composable + 1 个 API 客户端**。
> 状态机由 `App.vue` 的 `phase` 驱动，三态切换渲染：上传态 → 处理态 → 结果态。

## 1. 应用入口 [src/main.ts](../../frontend/src/main.ts)

- 引入自托管字体 `@fontsource/cormorant-garamond`（标题衬线）与 `@fontsource/inter`（UI 无衬线）——局域网离线可用
- 引入 `abcjs/abcjs-audio.css`（播放控件样式）与 `./styles/design-system.css`
- `createApp(App).mount('#app')`

## 2. 顶层状态机 [src/App.vue](../../frontend/src/App.vue)

**状态**（来自 `useTranscription()` 的 `phase`）：

| phase | 渲染区块 | 说明 |
|-------|----------|------|
| `idle` / `failed` | 上传态 | 文件选择 + 乐器选择 + 开始按钮 |
| `uploading` / `polling` | 处理态 | `ProgressTracker` 阶段进度 |
| `completed` | 结果态 | 元数据条 + 乐谱渲染 + 试听 + 下载 |

**关键逻辑**

- `onMounted`：拉取 `/api/health`（`modelsReady` → 顶栏「模型就绪」徽标）+ 拉取 `/api/instruments`（失败用 `FALLBACK_INSTRUMENTS` 兜底）
- 计算属性：`canSubmit`、`isUploadPhase`、`isProcessing`、`isCompleted`、`keyDisplay`（如 `Am`）
- `startTranscription()`：清空旧 `visualObj` → `transcription.start(file, instrument)`
- `restart()`：`transcription.reset()`（默认非 silent，会调用后端 DELETE 清理）+ 清空本地选择
- `onBeforeUnmount`：`transcription.reset(true)`（silent，仅取消轮询）
- `onRendered(v)`：承接 `SheetMusicView` 渲染出的 `visualObj`，传给 `PlaybackControls`

**模板结构**

```
header（标题 + 模型就绪徽标）
main
 ├─ section 上传态: FileUpload / InstrumentSelect / 开始转录按钮
 ├─ section 处理态: ProgressTracker + 失败重试
 └─ section 结果态: 元数据条(调性/BPM/拍号/乐器) + SheetMusicView
                    + PlaybackControls(有 visualObj 时) + DownloadButtons
footer
```

## 3. 类型与常量 [src/types.ts](../../frontend/src/types.ts)

- 接口：`InstrumentInfo`、`TaskKey`、`TaskResult`、`TaskStatus`
- `Phase`：`'idle' | 'uploading' | 'polling' | 'completed' | 'failed'`
- `INSTRUMENT_PROGRAM`：乐器 id → GM MIDI program（universal=0 钢琴、flute=73、violin=40、recorder=74、oboe=68），供 abcjs 播放音色
- `STAGES`：6 阶段镜像后端 `PROGRESS_RANGES`（id/label/range）
- `FALLBACK_INSTRUMENTS`：API 不可用时的本地兜底数据
- `MAX_FILE_SIZE`（50MB）、`ALLOWED_EXTENSIONS`

## 4. API 客户端 [src/api/client.ts](../../frontend/src/api/client.ts)

| 导出 | 说明 |
|------|------|
| `http<T>(url, init?)` | fetch 封装：非 2xx 时解析 `body.detail` 作为错误消息；204 返回 undefined |
| `getHealth()` | `GET /api/health` |
| `getInstruments()` | `GET /api/instruments` → 返回数组 |
| `submitTranscription(file, instrument)` | `POST /api/transcribe` multipart（不手动设 Content-Type，浏览器自动带 boundary） |
| `getTaskStatus(taskId)` | `GET /api/tasks/{id}` |
| `getResult(taskId)` | `GET /api/tasks/{id}/result` |
| `deleteTask(taskId)` | `DELETE /api/tasks/{id}` |
| `downloadUrl(taskId, format)` | 构造下载链接 |
| `pollTask(taskId, onUpdate, signal?)` | **2 秒轮询**：completed→resolve、failed→reject(Error)、abort→reject(AbortError)；`setTimeout` 递归防请求堆叠 |

## 5. 转录状态管理 [src/composables/useTranscription.ts](../../frontend/src/composables/useTranscription.ts)

| 导出 | 说明 |
|------|------|
| `validateFile(file): string \| null` | 扩展名 + 50MB 大小校验，通过返回 null |
| `useTranscription()` | 返回 `{phase, taskId, status, stage, progress, result, error, start, reset}` |

- `start(file, instrumentId)`：校验 → `reset(true)` → phase=`uploading` → 上传 → phase=`polling` → 建 `AbortController` → `pollTask` 更新 status/stage/progress → completed 时取 `finalStatus.result`；AbortError 静默忽略，其余错误写 `error` 并置 `failed`
- `reset(silent=false)`：先 `abort()`；非 silent 且有 taskId 时调用后端 DELETE（失败忽略）；重置全部状态

## 6. abcjs 封装 [src/composables/useAbcjs.ts](../../frontend/src/composables/useAbcjs.ts)

| 函数 | 说明 |
|------|------|
| `renderAbc(el, abc)` | `abcjs.renderAbc`，`responsive:'resize'`、`staffwidth:740`、墨色 `#2a241d`，返回 `visualObjs[0]` |
| `setupPlayback(audioEl, visualObj, instrumentId)` | 懒创建 `SynthController`；`setTune(visualObj, false, {soundFontUrl:'/soundfont/', soundFontVolumeMultiplier:3.0, program})`——`userAction=false` 让音频缓冲延迟到用户点击播放时创建，满足 AudioContext 手势约束 |
| `dispose()` | 释放控制器 |

## 7. 组件一览

### FileUpload.vue — 文件上传
- 拖拽（`dragover/dragleave/drop`）+ 点击触发隐藏 `<input type="file" accept=".mp3,.wav,.m4a,.flac">`
- `handleFile` 调 `validateFile`，通过 emit `select`，失败 emit `error`
- 已选文件显示名称与大小（KB/MB 格式化）

### InstrumentSelect.vue — 乐器选择
- props：`modelValue / instruments / disabled`，v-model 风格 emit
- 「通用乐谱」单独置顶卡片突出显示，其余乐器以 2 列网格展示音域（`formatFreq`：≥1000Hz 显示 kHz）

### ProgressTracker.vue — 阶段进度
- props：`stage / progress / status / error`
- 线性进度条 + 百分比 + 6 阶段列表（`stageState` 返回 `pending/active/done`，done 显示 ✓，active 显示脉冲动画）
- `status === 'failed'` 时显示错误框

### SheetMusicView.vue — 乐谱渲染
- watch `abc` → `nextTick` 后 `abcjs.renderAbc`，emit `rendered(visualObj)`
- 无 abc 时显示「等待乐谱…」占位

### PlaybackControls.vue — 试听控件
- props：`visualObj / instrumentId`；`onMounted` + watch 触发 `load()`
- 懒创建 `SynthController`，`setTune` 使用 `/soundfont/` 音色与 `INSTRUMENT_PROGRAM` 音色编号；异常 emit `error`

### DownloadButtons.vue — 下载
- 三个格式下载链接（ABC/MusicXML/MIDI，`downloadUrl(taskId, format)`）
- `copyAbc`：`navigator.clipboard.writeText` 复制 ABC，2 秒后恢复「已复制 ✓」
- 「重新开始」按钮 emit `reset`

## 8. 设计系统 [src/styles/design-system.css](../../frontend/src/styles/design-system.css)

印刷乐谱美学：象牙纸（`--paper:#fbf6ec`）+ 黄铜金（`--gold:#a8843f`）+ 墨色（`--ink:#2a241d`）；Cormorant Garamond 衬线标题 + Inter UI；发丝边框、克制留白、`--radius:3px`。所有组件样式均引用这些 CSS 变量。

## 9. 构建配置 [vite.config.ts](../../frontend/vite.config.ts)

- dev：端口 5173、`host:true`；代理 `/api` 与 `/soundfont` → `http://localhost:8000`
- build：`outDir: '../backend/app/static'`、`emptyOutDir: true`——构建产物直接进后端静态目录，生产由 FastAPI 同源托管
- `package.json` scripts：`dev` / `build`（先 `vue-tsc --noEmit` 类型检查再 `vite build`）/ `preview`
