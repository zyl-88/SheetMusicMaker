# 乐谱制作器 — 前端实施计划（阶段 5-8）

## 摘要

后端（FastAPI，阶段 1-4）已全部完成：7 个 API 端点、6 阶段流水线、线程安全任务存储。前端目前仅有骨架文件（package.json、vite.config.ts、tsconfig、index.html、env.d.ts），无任何 Vue 组件或业务逻辑。本计划覆盖前端全部待建内容：应用入口、API 客户端、状态管理 composable、abcjs 集成、6 个 Vue 组件、设计系统 CSS、App.vue 三态组装，以及最终联调验证。

技术栈：Vue 3.4 + TypeScript + Vite 5 + abcjs 6.2.2。设计方向：印刷乐谱美学（象牙色纸张、Cormorant Garamond 衬线标题、Inter 无衬线 UI、黄铜金点缀）。

---

## 当前状态分析

### 后端已就绪（无需改动）
- **API 端点**：`GET /api/health`、`GET /api/instruments`、`POST /api/transcribe`、`GET /api/tasks/{id}`、`GET /api/tasks/{id}/result`、`GET /api/tasks/{id}/download/{format}`、`DELETE /api/tasks/{id}`
- **任务状态机**：`pending` → `running`（含 stage/progress）→ `completed` | `failed`
- **阶段与进度区间**：`validating` 0-5%、`separating` 5-35%、`transcribing` 35-55%、`analyzing` 55-70%、`quantizing` 70-85%、`exporting` 85-100%
- **阶段中文标签**：校验音频文件 / 分离人声主旋律 / 转录音符 / 分析节拍与调性 / 量化音符时值 / 生成乐谱
- **TaskStatus 响应**：`{task_id, status, stage, progress(int), result(TaskResult|null), error(string|null)}`
- **TaskResult 形状**：`{abc(string), key({tonic, mode}), bpm(float), time_signature(string), instrument_name(string), downloads(dict[str,str])}`
- **关键发现**：`GET /api/tasks/{id}` 在 `status==completed` 时已包含 `result` 字段，无需额外调用 `/result` 端点
- **静态文件挂载**：`/soundfont`（目录存在时自动挂载）、`/`（前端构建产物 `backend/app/static`）
- **Vite 代理**：`/api` 和 `/soundfont` → `http://localhost:8000`

### 前端待建文件清单
| 文件 | 用途 |
|------|------|
| `src/main.ts` | 应用入口，引入字体+样式，挂载 Vue |
| `src/types.ts` | 集中类型定义 + 常量（阶段标签、乐器→GM program 映射） |
| `src/styles/design-system.css` | CSS 设计令牌 + abcjs 主题覆写 |
| `src/api/client.ts` | fetch 封装 + 2 秒轮询 + AbortController |
| `src/composables/useTranscription.ts` | 上传→轮询→结果 状态机 |
| `src/composables/useAbcjs.ts` | renderAbc + SynthController 封装 |
| `src/components/FileUpload.vue` | 拖拽/点击上传 + 前端预校验 |
| `src/components/InstrumentSelect.vue` | 乐器卡片选择 + 音域展示 |
| `src/components/ProgressTracker.vue` | 六阶段步骤 + 线性进度条 |
| `src/components/SheetMusicView.vue` | abcjs 渲染容器 |
| `src/components/PlaybackControls.vue` | SynthController 播放控件 |
| `src/components/DownloadButtons.vue` | 三格式下载 + 复制 ABC |
| `src/App.vue` | 三态 shell + 编排 |

---

## 技术调研结论

### 1. abcjs 6.x API（官方文档确认）
- **渲染**：`abcjs.renderAbc(element, abcString, params)` → 返回 `TuneObject[]`（取 `[0]` 供播放用）。参数 `responsive:"resize"` 自适应宽度、`add_classes:true` 便于 CSS 控制。
- **播放**：`new abcjs.synth.SynthController()` → `.load(selector, cursorControl, visualOptions)` → `.setTune(visualObj, userAction, audioParams)`。
- **AudioContext 手势约束**：`setTune` 的 `userAction` 参数传 `false` 时，音频缓冲延迟到用户点击 play 按钮时才创建。abcjs 的 play 按钮处理器本身在用户点击事件中，因此 `setTune(visualObj, false, audioParams)` 即满足"AudioContext 在用户手势内创建"的要求，**无需手动 `new AudioContext()`**。
- **自托管音色**：`audioParams.soundFontUrl` 指向 `/soundfont/`，abcjs 按需请求 `{soundFontUrl}{instrumentName}-mp3.js`。
- **音色来源**：`https://paulrosen.github.io/midi-js-soundfonts/abcjs/{name}-mp3.js`

### 2. TypeScript 适配
- abcjs 为 CJS 包，`types/index.d.ts` 为 `declare module 'abcjs'`（命名导出）。
- `tsconfig.json` 需增加 `"esModuleInterop": true` 方可 `import abcjs from 'abcjs'`。
- abcjs 播放控件样式表位于包根 `abcjs/abcjs-audio.css`，可 `import 'abcjs/abcjs-audio.css'`。

### 3. 音色→MIDI program 映射（GM 标准）
`flute→73`、`violin→40`、`recorder→74`、`oboe→68`、`acoustic_grand_piano→0`（兜底）。后端 ABC 头部无 `%%MIDI program`，播放音色由前端通过 `audioParams.options.program` 注入，不修改 ABC 字符串（保证下载的 ABC 与渲染一致）。

---

## 设计系统

### 美学方向
印刷乐谱美学：象牙色纸张底色、衬线标题（Cormorant Garamond）、无衬线 UI（Inter）、黄铜/金点缀、发丝边框、克制留白。杜绝渐变/毛玻璃/霓虹。

### 字体（自托管支持局域网离线）
`package.json` devDependencies 增加 `@fontsource/cormorant-garamond` 和 `@fontsource/inter`。在 `main.ts` 引入对应 weight CSS。回退栈：`Georgia, serif` 与 `system-ui`。

### CSS 设计令牌
```css
:root {
  --paper: #fbf6ec;          /* 页面底色 */
  --paper-deep: #f3ead8;     /* 面板/内嵌区 */
  --paper-card: #fffdf7;     /* 乐谱卡片底 */
  --ink: #2a241d;            /* 主文/谱线 */
  --ink-soft: #6f6354;       /* 次要文 */
  --gold: #a8843f;           /* 黄铜主点缀 */
  --gold-soft: #c8a45c;
  --gold-tint: #efe3c8;
  --rule: #e4d8c0;           /* 发丝边框 */
  --ok: #5a7a4e; --err: #a23b2c;
  --font-display: 'Cormorant Garamond', Georgia, 'Times New Roman', serif;
  --font-ui: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --radius: 3px; --radius-lg: 6px;
  --shadow: 0 1px 0 rgba(0,0,0,.03), 0 14px 32px -20px rgba(60,48,30,.4);
  --staff-stroke: #3a342a;
}
```

### 组件视觉规范
- **卡片**：`background: var(--paper-card); border: 1px solid var(--rule); border-top: 2px solid var(--gold); border-radius: var(--radius); box-shadow: var(--shadow)`
- **主按钮**：`background: var(--ink); color: var(--paper)`，hover → `background: var(--gold)`
- **次按钮**：透明描边，hover → 金色边框
- **标签**：`text-transform: uppercase; letter-spacing: .08em; font-size: .72rem; color: var(--ink-soft)`
- **乐谱容器**：白色卡 + 顶部黄铜细线，SVG 谱线用 `color: var(--staff-stroke)`

### abcjs 播放控件主题覆写
```css
.abcjs-inline-audio { background: var(--paper-deep); border: 1px solid var(--rule); border-radius: var(--radius); padding: 8px 14px; }
.abcjs-midi-start, .abcjs-midi-reset, .abcjs-midi-loop { background: var(--gold); color: var(--paper); border: none; border-radius: var(--radius); }
.abcjs-midi-start:hover, .abcjs-midi-reset:hover { background: var(--gold-soft); }
.abcjs-midi-progress-background { background: var(--gold-tint); }
.abcjs-midi-progress, .abcjs-midi-progress-bar { background: var(--gold); }
.abcjs-css-warning { display: none; }
```

---

## 各文件详细设计

### 1. `src/main.ts`
应用入口。引入自托管字体（Cormorant Garamond 500/600/700、Inter 400/500/600）、abcjs 播放控件样式 `abcjs/abcjs-audio.css`、设计系统 `styles/design-system.css`，然后 `createApp(App).mount('#app')`。

### 2. `src/types.ts`
集中类型定义，与后端契约对齐：
- `InstrumentInfo`：`{id, name, min_freq, max_freq}`
- `TaskKey`：`{tonic: string, mode: 'major'|'minor'}`
- `TaskResult`：`{abc, key, bpm, time_signature, instrument_name, downloads}`
- `TaskStatus`：`{task_id, status, stage, progress, result, error}`
- `Phase`：`'idle'|'uploading'|'polling'|'completed'|'failed'`
- `INSTRUMENT_PROGRAM` 常量：`{flute:73, violin:40, recorder:74, oboe:68}`
- `STAGES` 常量：六阶段 id + 中文标签 + 进度区间

### 3. `src/api/client.ts`
fetch 封装 + 2 秒轮询。
- `http<T>(url, init)` 通用 fetch，错误提取 `detail` 字段
- `getHealth()`、`getInstruments()`、`submitTranscription(file, instrument)`（FormData，不设 Content-Type）、`getTaskStatus(id)`、`getResult(id)`、`deleteTask(id)`、`downloadUrl(id, format)`
- `pollTask(taskId, onUpdate, signal)`：递归 `setTimeout`（2s 间隔，防止请求堆叠），completed 解析、failed 抛错，AbortSignal 可取消

### 4. `src/composables/useTranscription.ts`
状态编排：`idle` → `uploading`（POST）→ `polling`（GET 每 2s）→ `completed`/`failed`。
- 响应式状态：`phase`、`taskId`、`status`、`stage`、`progress`、`result`（shallowRef）、`error`
- `start(file, instrumentId)`：先 `validateFile`（类型/大小），通过后 `reset(true)` 取消旧轮询 → POST → 轮询
- `reset(silent)`：abort 轮询，非静默时 DELETE 后端任务与文件，清空状态
- `validateFile(file)`：检查扩展名（.mp3/.wav/.m4a/.flac）和大小（≤50MB），返回 null 或错误消息

### 5. `src/composables/useAbcjs.ts`
abcjs 渲染 + 播放封装。
- `renderAbc(el, abc)`：调用 `abcjs.renderAbc`，返回 `visualObj[0]`
- `setupPlayback(audioEl, visualObj, instrumentId)`：创建 `SynthController`，`.load()` 加载控件，`.setTune(visualObj, false, {soundFontUrl:'/soundfont/', options:{program: INSTRUMENT_PROGRAM[instrumentId]}})` —— `userAction=false` 确保 AudioContext 延迟到用户点击 play 时创建
- `dispose()`：释放引用

### 6. `src/components/FileUpload.vue`
拖拽/点击上传 + 前端预校验。
- props：`disabled?: boolean`
- emits：`select(file: File)`、`error(msg: string)`
- `<input type="file" accept=".mp3,.wav,.m4a,.flac" hidden>` + 拖拽区
- change/drop 时调用 `validateFile`，通过则 emit select，否则 emit error
- 展示已选文件名与大小；disabled 时禁用交互

### 7. `src/components/InstrumentSelect.vue`
乐器卡片选择 + 音域展示。
- props：`modelValue: string`、`instruments: InstrumentInfo[]`、`disabled?: boolean`
- emits：`update:modelValue(id: string)`
- 4 张卡片网格，每张显示名称 + 音域范围；选中态黄铜边框
- instruments 为空时用本地兜底 4 乐器数据，保证离线可用

### 8. `src/components/ProgressTracker.vue`
六阶段步骤 + 线性进度条。
- props：`stage: string`、`progress: number`、`status: string`、`error?: string`
- 用 `STAGES` 渲染步骤列表：当前阶段高亮（黄铜点），已完成打勾
- 顶部线性进度条按 `progress%`
- failed 时显示 error 文案 + 重试提示

### 9. `src/components/SheetMusicView.vue`
abcjs 渲染容器，ABC 变化时重渲染。
- props：`abc: string`
- emits：`rendered(visualObj: any)`
- `watch(abc)` → `nextTick` → `abcjs.renderAbc(el, abc, params)` → `emit('rendered', objs[0])`
- 空态：显示"等待乐谱…"占位（衬线斜体，淡墨）

### 10. `src/components/PlaybackControls.vue`
SynthController 播放控件。
- props：`visualObj: any`、`instrumentId: string`
- emits：`error(msg: string)`
- `onMounted` + `watch([visualObj, instrumentId])` → 创建 SynthController + load + setTune
- `setTune(visualObj, false, ...)` 满足 AudioContext 手势约束
- try/catch 包裹，失败时 emit error

### 11. `src/components/DownloadButtons.vue`
三格式下载 + 复制 ABC。
- props：`downloads: Record<string, string>`、`abc?: string`
- emits：`reset()`
- ABC/MusicXML/MIDI 用 `<a :href="url" download>` 直接下载
- 额外"复制 ABC"按钮（`navigator.clipboard.writeText`）
- "重新开始"按钮 emit reset

### 12. `src/App.vue`
三态 shell + 编排。
- 顶部：衬线标题"乐谱制作器" + 副标题 + 健康徽标（GET /api/health）
- 持有 `useTranscription()` + `selectedFile` + `selectedInstrument` + `visualObj`(shallowRef)
- **upload 态**：FileUpload + InstrumentSelect + "开始转录"主按钮（二者皆选才可点）
- **processing 态**：ProgressTracker + 文件名/乐器信息
- **result 态**：SheetMusicView（@rendered 存 visualObj）+ PlaybackControls + 元数据条（调性 `${key.tonic}${key.mode==='minor'?'m':''}`、BPM、拍号、乐器）+ DownloadButtons
- "重新开始"：transcription.reset() + 清空本地选中
- onBeforeUnmount：transcription.reset(true) 防泄漏

---

## 实施步骤

### 阶段 5：前端基础
1. `npm install` 安装现有依赖；增加 `@fontsource/cormorant-garamond`、`@fontsource/inter`
2. 修改 `tsconfig.json` 增加 `"esModuleInterop": true`
3. 创建 `src/types.ts`：类型定义 + 常量
4. 创建 `src/api/client.ts`：fetch 封装 + pollTask 递归轮询
5. 创建 `src/main.ts`：引入字体+样式，挂载 App
6. 创建 `src/composables/useTranscription.ts`：状态机 + validateFile

### 阶段 6：abcjs 集成
7. 创建 `src/composables/useAbcjs.ts`：renderAbc + SynthController 封装
8. 创建 `src/components/SheetMusicView.vue`：ABC 变化时重渲染
9. 创建 `src/components/PlaybackControls.vue`：播放控件 + setTune(false)
10. 下载 soundfont 文件到 `soundfont/`（acoustic_grand_piano/flute/violin/recorder/oboe 的 `-mp3.js`）

### 阶段 7：前端界面组装
11. 创建 `src/styles/design-system.css`：设计令牌 + abcjs 主题覆写
12. 创建 `src/components/FileUpload.vue`：拖拽上传 + 校验
13. 创建 `src/components/InstrumentSelect.vue`：乐器卡片
14. 创建 `src/components/ProgressTracker.vue`：进度展示
15. 创建 `src/components/DownloadButtons.vue`：下载 + 复制
16. 创建 `src/App.vue`：三态 shell + 编排
17. 配置 vite.config.ts 增加 `server.host: true` 便于局域网联调

### 阶段 8：联调与验证
18. `npm run build` 验证类型检查 + 构建产物输出到 backend/app/static
19. 用 30 秒短音频端到端测试：上传 → 轮询 → 渲染 → 播放 → 下载
20. 验证 abcjs 渲染正确（五线谱显示）、播放出声（音色对应乐器）
21. 验证三格式下载（ABC/MusicXML/MIDI 文件可被外部软件打开）
22. 验证失败与重置流程（上传空音频 → 显示错误 → 重新开始）
23. 验证四乐器音色不同、音域过滤生效
24. 局域网测试：另一台机器访问，确认字体与音色均自托管不依赖外网

---

## 假设与决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 轮询方式 | 递归 setTimeout 2s | 防止请求堆叠；与后端 POLL_INTERVAL_MS 一致 |
| 结果获取 | 直接从轮询响应读 result | GET /tasks/{id} 在 completed 时已含 result，无需额外请求 |
| AudioContext 创建 | setTune(visualObj, false, ...) | userAction=false 让 abcjs 在用户点击 play 时创建 AudioContext |
| 音色注入 | audioParams.options.program | 不修改 ABC 字符串，保证下载文件与渲染一致 |
| 音色托管 | 自托管 /soundfont/ | 局域网离线部署不依赖 GitHub |
| 字体托管 | @fontsource 自托管 | 局域网离线不依赖 Google Fonts |
| result 响应式 | shallowRef | 大对象无需深响应，避免性能开销 |
| 乐器数据 | API 拉取 + 本地兜底 | API 不可用时仍可选乐器 |
| 状态取消 | AbortController | reset/卸载时取消轮询，防止泄漏 |

### 关键约束
1. **AudioContext 手势**：`setTune` 第二参数传 `false`，不在挂载/渲染时创建 AudioContext。iOS Safari 若 suspended，在容器加一次性 `pointerdown` 调 `activeAudioContext.resume()`（仍属手势内）。
2. **Soundfont 自托管**：需下载 5 个 `-mp3.js` 文件到 `soundfont/` 目录，后端 `main.py` 在目录存在时自动挂载 `/soundfont`。
3. **esModuleInterop**：abcjs 为 CJS，tsconfig 必须加 `esModuleInterop: true` 才能通过类型检查。
4. **前端校验**：上传前校验扩展名（.mp3/.wav/.m4a/.flac）和大小（≤50MB），不通过直接提示。
5. **单任务串行**：后端 `MAX_CONCURRENT_TASKS=1`，前端只管一个 task，重置即清理。

---

## 风险与对策

| 风险 | 对策 |
|------|------|
| `import abcjs from 'abcjs'` 类型报错 | tsconfig 加 `esModuleInterop: true` |
| abcjs `load()` 某些版本只收 selector 字符串 | PlaybackControls 的 div 固定 id，必要时传 `'#audio-control'` |
| iOS AudioContext suspended | 容器加一次性 pointerdown 调 resume（手势内） |
| 音色文件名/格式不符 | 先 curl 验证 `/soundfont/flute-mp3.js` 返回 200 |
| 轮询请求堆叠/泄漏 | 递归 setTimeout + AbortController + 卸载 reset |
| 后端 result 含内部 `_file_paths` | 前端只读 6 个业务字段，忽略其余 |
| 长任务（10min≈15min）体验差 | ProgressTracker 按阶段区间显示细分进度 |

---

## 验证步骤

1. **类型/构建**：`npm run build`（`vue-tsc -b && vite build`）无类型错误，产物输出到 `backend/app/static`
2. **dev 代理**：`npm run dev`，浏览器 Network 中 `/api/*` 与 `/soundfont/*` 均 200
3. **上传校验**：拖入 `.txt`/`>50MB` 文件 → 前端拦截并提示；`.mp3` 等通过
4. **轮询**：提交后每 2s 更新阶段与进度条，阶段文案与后端一致，完成即进入结果态
5. **乐谱渲染**：SheetMusicView 内出现 abcjs SVG 五线谱，谱线为暖墨色，布局自适应
6. **播放（手势）**：首次点击 play 能出声且音色对应所选乐器；DevTools 中 AudioContext 由用户点击触发创建
7. **下载**：ABC / MusicXML / MIDI 三个链接均能下载且文件可被外部软件打开
8. **失败与重置**：人为触发失败 → 展示 error → 点"重新开始"→ 状态清空且可再次提交
9. **健康检查**：启动时徽标反映 `models_loaded`
10. **四乐器**：同一音频分别选 4 种乐器，播放音色不同、音域过滤生效
11. **局域网**：另一台机器访问 `http://<本机IP>:8000/`，渲染/播放/下载全程不依赖外网
