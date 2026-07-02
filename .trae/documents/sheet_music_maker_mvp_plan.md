# 乐谱制作器 MVP 实施计划

## 摘要

构建一个工具型 Web 应用：用户上传混合多乐器音频文件（MP3/WAV/M4A/FLAC，≤10分钟），系统自动分离人声主旋律、转录为音符、检测节拍/调号/拍号，最终生成指定 C 调高音谱号乐器的分谱，支持浏览器内渲染播放，并导出 MIDI、MusicXML、ABC 三种格式。

技术栈：FastAPI (Python) 后端 + Vue 3 前端，CPU 处理，无数据库无账户系统，本地局域网部署。

---

## 当前状态分析

项目目录 `d:\project\SheetMusicMaker` 为空，从零开始构建。

部署环境为 Intel Arc 集成显卡（128MB 显存），不具备 NVIDIA CUDA 加速能力，音频处理全程 CPU 执行。预计 10 分钟音频完整处理约 15 分钟，需异步处理 + 前端轮询进度。

---

## 技术调研发现（源码级验证）

### 1. Demucs 人声分离（关键约束）

- **CPU 性能**：Intel i7 CPU 上 4 分钟歌曲分离约 6 分钟，10 分钟音频预计约 15 分钟。
- **Python API 不支持 `two_stems`**：`demucs/api.py` 源码底部注释 `# two-stem not supported`。`Separator.separate_audio_file()` 返回 `(original, dict_of_all_stems)`，htdemucs 模型返回 4 个 stem（drums/bass/other/vocals）。正确做法：调用后从字典取 `separated["vocals"]`。
- **CPU 优化参数**：`device="cpu"`、`jobs=4`、`segment=7.8`（控制内存峰值）、`shifts=1`。
- **callback 回调**：`Separator` 支持 `callback` 参数，dict 含 `segment_offset`、`state`（start/end）、`audio_length`，可用于实时进度上报。
- **依赖 ffmpeg**：非 WAV 格式需 ffmpeg 解码。
- **首次运行下载模型**（htdemucs ~80MB），需在应用启动时预下载。

### 2. basic-pitch 转录

```python
from basic_pitch.inference import predict, Model
from basic_pitch import ICASSP_2022_MODEL_PATH

model_output, midi_data, note_events = predict(
    audio_path,
    onset_threshold=0.5,
    frame_threshold=0.3,
    minimum_note_length=127.7,
    minimum_frequency=None,   # 可限定最低频率（按乐器音域过滤）
    maximum_frequency=None,   # 可限定最高频率
    multiple_pitch_bends=False,
    melodia_trick=True,
    midi_tempo=120,
)
```

- **note_events 格式**：`List[Tuple[start_time_s, end_time_s, pitch_midi, amplitude, pitch_bend]]`
- **Windows 运行时**：默认安装 ONNX Runtime（CPUExecutionProvider），无需 TensorFlow。
- **频率范围过滤**：`minimum_frequency`/`maximum_frequency` 可直接按乐器音域裁剪结果。
- **模型复用**：用 `Model(path)` 预加载传入 `predict()` 避免重复加载。

### 3. music21 音符量化与导出

- **MusicXML 导出**（`write('musicxml')`）和 **MIDI 导出**（`write('midi')`）完善可靠。
- **ABC 导出支持有限**：music21 的 abcFormat 模块以读取为主，`write('abc')` 输出质量不稳定（复杂节奏、连音等可能丢失）。
- **决策**：music21 负责音符量化与 MusicXML/MIDI 导出；ABC 导出采用专用构建模块从量化后的音符数据直接生成符合 abcjs 规范的 ABC 字符串。

### 4. abcjs 浏览器渲染

- **渲染**：`ABCJS.renderAbc(elementOrId, abcString, options)` 返回 tune 对象数组（播放必需输入）。
- **播放**：`SynthController` 高层播放控件，`load(selector)` → `setTune(visualObj, userAction, audioParams)`。
- **关键约束**：AudioContext 必须在用户手势（点击）事件中创建/恢复，不能自动创建。
- **音色库**：默认从 GitHub 加载 soundfont，局域网部署需自托管 soundfont 文件。

### 5. madmom 节拍检测

- 流水线：`RNNBeatProcessor`（RNN 起拍激活）→ `DBNBeatTrackingProcessor`（动态贝叶斯网络提取节拍时间戳）。
- 速度估计：`TempoEstimationProcessor`。
- 要求输入为 44.1kHz 16-bit WAV。

### 6. librosa 调性检测

- librosa **无内置调性检测函数**。需用 `librosa.feature.chroma_cqt` 提取色度图，配合 **Krumhansl-Schmuckler 算法**（大/小调轮廓 + Pearson 相关）实现 24 调性识别。

---

## 技术架构

### 数据流

```
上传音频(MP3/WAV/M4A/FLAC)
    │
    ▼
[1] 校验：格式/大小(≤50MB)/时长(≤10min)
    │
    ▼
[2] Demucs 人声分离 (CPU, htdemucs) → vocals.wav (44.1kHz)
    │  (最慢阶段：10min音频≈15min)
    ▼
[3] basic-pitch 转录 → note_events[(start,end,midi,amp,bend)]
    │  (频率范围已按乐器音域过滤)
    ▼
[4] 音频分析：madmom 节拍/BPM + librosa Krumhansl 调性
    │  → {beats, bpm, key, time_signature}
    ▼
[5] music21 音符量化 → music21 Stream（含调号/拍号/速度标记）
    │  (量化到 1/16 网格，音域钳制)
    ▼
[6] 导出：ABC(专用构建器) + MusicXML(music21) + MIDI(music21)
    │
    ▼
返回结果（ABC 即时渲染 + 三格式下载）
```

### 文件结构

```
d:\project\SheetMusicMaker\
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI 应用入口
│   │   ├── config.py                # 配置：路径、限制、模型参数
│   │   ├── schemas.py               # Pydantic 请求/响应模型
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   └── transcribe.py        # 转录 API 端点
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── separator.py         # Demucs 人声分离
│   │   │   ├── transcriber.py       # basic-pitch 转录
│   │   │   ├── analyzer.py          # madmom 节拍 + librosa 调性
│   │   │   ├── quantizer.py         # music21 音符量化
│   │   │   ├── exporter.py          # ABC/MusicXML/MIDI 导出
│   │   │   ├── pipeline.py          # 全流程编排 + 进度上报
│   │   │   └── instruments.py        # C 谱号乐器音域配置
│   │   ├── tasks/
│   │   │   ├── __init__.py
│   │   │   └── store.py             # 内存任务存储（线程安全）
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── audio.py             # 音频校验（格式/大小/时长）
│   ├── requirements.txt
│   └── run.bat                      # Windows 启动脚本
│
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.ts
│       ├── App.vue                   # 单页面三态：上传→处理→结果
│       ├── api/
│       │   └── client.ts            # fetch 封装 + 轮询逻辑
│       ├── components/
│       │   ├── FileUpload.vue       # 拖拽上传 + 校验
│       │   ├── InstrumentSelect.vue # 乐器选择
│       │   ├── ProgressTracker.vue  # 流水线进度展示
│       │   ├── SheetMusicView.vue   # abcjs 渲染容器
│       │   ├── PlaybackControls.vue # abcjs 合成播放控件
│       │   └── DownloadButtons.vue  # 三格式下载
│       └── composables/
│           ├── useAbcjs.ts          # abcjs 渲染+播放封装
│           └── useTranscription.ts  # 上传+轮询+结果编排
│
├── storage/                          # 运行时数据（gitignore）
│   ├── uploads/                      # 原始音频
│   ├── temp/                         # 中间产物（vocals.wav）
│   └── outputs/                      # 最终产物（abc/xml/mid）
│
├── soundfont/                        # 自托管 abcjs 音色库
│
└── README.md
```

---

## 各模块详细设计

### 后端模块

#### `config.py` — 集中配置

核心常量包括：`MAX_FILE_SIZE_MB=50`、`MAX_DURATION_SEC=600`、`ALLOWED_EXTENSIONS={.mp3,.wav,.m4a,.flac}`、`DEMUCS_MODEL="htdemucs"`、`DEMUCS_DEVICE="cpu"`、`DEMUCS_SEGMENT=7`、`DEMUCS_JOBS=4`、`DEMUCS_SHIFTS=1`、`QUANTIZE_DIVISION=4`（量化到 1/16）、`DEFAULT_TIME_SIG="4/4"`。

#### `instruments.py` — C 谱号乐器配置

定义四种乐器的音域（频率 + MIDI 编号），直接传入 basic-pitch 的 `minimum_frequency`/`maximum_frequency`：

| 乐器 | 最低音 | 最高音 | MIDI 范围 |
|------|--------|--------|-----------|
| 长笛 Flute | C4 (261.63 Hz) | D7 (2349.32 Hz) | 60–98 |
| 小提琴 Violin | G3 (196.00 Hz) | E7 (2637.02 Hz) | 55–100 |
| 竖笛 Recorder | C4 (261.63 Hz) | D7 (2349.32 Hz) | 60–98 |
| 双簧管 Oboe | Bb3 (233.08 Hz) | G6 (1567.98 Hz) | 58–91 |

均为 C 调非移调乐器，统一使用高音谱号。

#### `separator.py` — Demucs 人声分离

封装 `demucs.api.Separator`，配置 CPU 参数。调用 `separate_audio_file()` 后从返回字典取 `separated["vocals"]`（Python API 不支持 `two_stems`）。通过 `callback` 参数实现分段进度上报。输出 vocals tensor + 采样率，保存为 44.1kHz WAV。

#### `transcriber.py` — basic-pitch 转录

预加载 `Model(ICASSP_2022_MODEL_PATH)` 复用。调用 `predict()` 时传入乐器音域的 `minimum_frequency`/`maximum_frequency`，在转录阶段即过滤超出音域的音符。返回 `note_events` 和 `midi_data`。

#### `analyzer.py` — 节拍 + 调性检测

- **节拍/速度**：`RNNBeatProcessor` → `DBNBeatTrackingProcessor` 提取节拍时间戳；`TempoEstimationProcessor` 估计 BPM。
- **调性**：`librosa.feature.chroma_cqt` 提取色度图 → Krumhansl-Schmuckler 算法（12 大调 + 12 小调轮廓，Pearson 相关）识别 24 调性。
- **拍号**：基于节拍间隔规律推断，默认 4/4。

#### `quantizer.py` — music21 音符量化

将 `note_events` 的秒级时间转为音乐时值：用 BPM 计算每拍秒数，将 onset/offset 量化到 1/16 网格。构建 music21 Stream，设置调号、拍号、速度标记。对超出乐器音域的音符进行钳制。调用 `stream.quantize()` 清理对齐。

#### `exporter.py` — 三格式导出

- **MusicXML**：`stream.write("musicxml")` — music21 原生支持，可靠。
- **MIDI**：`stream.write("midi")` — music21 原生支持，可靠。
- **ABC**：专用 ABC 构建器，从量化后的 Stream 遍历音符，MIDI 音高转 ABC 音名（C/D/E/F/G/A/B + 八度记号），quarterLength 转 ABC 时值符号，插入小节线，头部写 `X:1`/`T:标题`/`M:拍号`/`L:1/8`/`Q:1/4=BPM`/`K:调性`。保证 abcjs 完美渲染。

#### `pipeline.py` — 全流程编排

串联 6 个阶段（validating → separating → transcribing → analyzing → quantizing → exporting），通过 `progress_callback` 回写 TaskStore 进度。各阶段进度占比：校验 0-5%、分离 5-35%、转录 35-55%、分析 55-70%、量化 70-85%、导出 85-100%。

#### `tasks/store.py` — 内存任务存储

线程安全的字典存储，支持 `create`/`update`/`complete`/`fail`/`get` 操作。任务状态：pending → running（含 stage/progress）→ completed/failed。单任务串行执行（信号量限制），新任务排队。

### 前端模块

#### `api/client.ts` — API 封装与轮询

封装 `submitTranscription`（POST multipart）、`pollTask`（GET 状态）、`getResult`（GET 结果）、`downloadFile`（GET 文件流）。轮询间隔 2 秒，遇到 completed/failed 停止。

#### `composables/useAbcjs.ts` — abcjs 集成

封装 `renderAbc()` 渲染和 `SynthController` 播放。关键：AudioContext 在用户点击播放按钮时创建/恢复。配置 `soundFontUrl` 指向自托管 `/soundfont/`。

#### `composables/useTranscription.ts` — 状态编排

管理上传 → 轮询 → 结果获取的完整流程，暴露 `status`/`stage`/`progress`/`result`/`error` 响应式状态。

#### 组件

- **FileUpload.vue**：拖拽/点击上传，前端预校验类型/大小，`accept=".mp3,.wav,.m4a,.flac"`
- **InstrumentSelect.vue**：下拉选择 4 种乐器，展示音域范围
- **ProgressTracker.vue**：进度条 + 阶段文案（人声分离中.../转录中.../分析中...）
- **SheetMusicView.vue**：`<div id="paper">` 容器，ABC 变化时重新渲染
- **PlaybackControls.vue**：`<div id="audio-control">` 容器，绑定 SynthController
- **DownloadButtons.vue**：三格式下载按钮，指向 download 端点

#### Vite 配置

`server.proxy` 将 `/api` 代理到 FastAPI（`http://localhost:8000`）。部署时 FastAPI 挂载前端静态文件。

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 + 模型就绪状态 |
| GET | `/api/instruments` | 乐器列表 |
| POST | `/api/transcribe` | 提交转录任务（multipart: file + instrument），返回 task_id |
| GET | `/api/tasks/{task_id}` | 轮询任务状态（status/stage/progress/result/error） |
| GET | `/api/tasks/{task_id}/result` | 获取完整结果（ABC 字符串 + 元数据 + 下载链接） |
| GET | `/api/tasks/{task_id}/download/{format}` | 下载文件（format∈{abc,musicxml,midi}） |
| DELETE | `/api/tasks/{task_id}` | 清理任务 + 临时文件 |

POST 立即返回 task_id，后台线程执行 pipeline。前端 2 秒间隔轮询状态。ABC 字符串随结果直接返回（前端即时渲染），文件下载走单独端点。

---

## 实施步骤

### 阶段 1：项目骨架与环境

1. 创建目录结构（backend/frontend/storage/soundfont）
2. 编写 `backend/requirements.txt`：fastapi、uvicorn[standard]、python-multipart、demucs、basic-pitch、madmom、librosa、music21、numpy、soundfile
3. 编写 `frontend/package.json`：vue@3、abcjs@^6、vite、typescript
4. 配置 Vite 代理 + FastAPI CORS
5. 编写 `README.md`，明确列出系统依赖（Python 3.9+、Node.js 18+、ffmpeg）

### 阶段 2：后端基础

6. 实现 `config.py`：集中配置常量
7. 实现 `instruments.py`：4 种 C 谱号乐器音域配置
8. 实现 `utils/audio.py`：用 ffprobe 校验时长/大小/扩展名
9. 实现 `tasks/store.py`：线程安全内存任务存储
10. 实现 `schemas.py`：Pydantic 请求/响应模型
11. 搭建 `main.py`：FastAPI 应用、CORS、生命周期（预下载 Demucs 模型）、路由注册

### 阶段 3：流水线各服务

12. 实现 `separator.py`：Demucs Separator 封装，callback 进度上报，取 vocals 键
13. 实现 `transcriber.py`：basic-pitch predict 封装，乐器音域频率过滤
14. 实现 `analyzer.py`：madmom 节拍/BPM + librosa Krumhansl-Schmuckler 调性
15. 实现 `quantizer.py`：note_events → music21 量化 Stream（1/16 网格，音域钳制）
16. 实现 `exporter.py`：MusicXML/MIDI（music21 写入）+ ABC（专用构建器）

### 阶段 4：流水线编排与 API

17. 实现 `pipeline.py`：串联 6 阶段，progress_callback 回写 TaskStore
18. 实现 `routers/transcribe.py`：7 个端点
19. POST 端点用 ThreadPoolExecutor 异步执行 pipeline
20. 下载端点用 FileResponse

### 阶段 5：前端基础

21. 初始化 Vite + Vue 3 + TypeScript 项目
22. 实现 `api/client.ts`：fetch 封装 + 2 秒轮询
23. 实现 `useTranscription.ts`：上传 → 轮询 → 结果状态管理

### 阶段 6：abcjs 集成

24. 实现 `useAbcjs.ts`：renderAbc + SynthController 封装
25. 实现 `SheetMusicView.vue`：ABC 变化时重新渲染
26. 实现 `PlaybackControls.vue`：播放控件 + 用户手势创建 AudioContext
27. 下载 soundfont 文件到 `/soundfont/` 供局域网离线播放

### 阶段 7：前端界面组装

28. 实现 `FileUpload.vue` + `InstrumentSelect.vue` + `ProgressTracker.vue` + `DownloadButtons.vue`
29. 组装 `App.vue`：单页面三态切换（上传态 → 处理态 → 结果态）
30. 配置 FastAPI 挂载前端构建产物

### 阶段 8：联调与验证

31. 用 30 秒短音频端到端测试
32. 验证 abcjs 渲染 + 播放 + 三格式下载
33. 用 10 分钟音频测试性能与超时
34. 验证四种乐器的音域过滤效果

---

## 假设与决策

### 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 输入类型 | 音频文件（非 MIDI） | 用户需求明确要求从音乐文件生成 |
| 音轨类型 | 混合多乐器 → 分离人声 | 最实用的实际场景 |
| 输出形式 | 单乐器分谱 | 聚焦核心功能，降低复杂度 |
| 编辑功能 | 无（MVP） | 先跑通全流程，编辑留待 v2 |
| 后端框架 | FastAPI | 音频处理库全在 Python 生态 |
| 乐器范围 | 高音谱号 C 调 | 无移调复杂度，渲染最简 |
| 节拍/调号 | 全部自动检测 | 无需用户干预，全自动体验 |
| 导出格式 | MIDI + MusicXML + ABC | 覆盖播放/交换/渲染三场景 |
| 用户系统 | 无账户工具型 | MVP 最简，无需数据库 |
| 音频限制 | 常见格式，≤10min，≤50MB | 平衡可用性与 CPU 处理时间 |

### 关键技术决策

1. **Demucs vocals 提取**：Python API 不支持 `two_stems`，改为分离全部声源后取 `vocals` 键，无性能损失。
2. **ABC 导出**：music21 的 ABC 导出不可靠，改用专用构建器从量化数据直接生成 abcjs 兼容 ABC 字符串。
3. **调性检测**：librosa 无内置函数，自行实现 Krumhansl-Schmuckler 算法（chroma_cqt + 24 调轮廓 + Pearson 相关）。
4. **abcjs AudioContext**：必须在用户点击事件中创建/恢复，不自动播放。
5. **abcjs soundfont**：局域网部署需自托管，指向 `/soundfont/`。
6. **basic-pitch 频率过滤**：直接传入乐器音域的 min/max frequency，在转录阶段即过滤超域音符。

### 系统依赖

- Python 3.9+（basic-pitch 要求）
- Node.js 18+
- ffmpeg + ffprobe（音频解码/校验）
- 无需 GPU、无需数据库

---

## 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Demucs CPU 极慢（10min音频≈15min） | 等待体验差 | 前端显示预计耗时，callback 细粒度进度；建议先用短音频测试 |
| Demucs 首次运行下载模型（~80MB） | 首次任务失败 | 应用启动时预下载；`/api/health` 报告模型就绪状态 |
| madmom 需 44.1kHz WAV | 格式不匹配 | 分离后统一存为 44.1kHz WAV |
| abcjs AudioContext 需用户手势 | 自动播放失败 | 播放按钮 onclick 中创建 AudioContext |
| abcjs soundfont 需联网 | 局域网无声 | 自托管 soundfont 至 `/soundfont/` |
| music21 ABC 导出质量差 | 渲染异常 | 专用 ABC 构建器替代 |
| 长任务内存堆积 | OOM | 每阶段清理中间文件；Demucs segment=7 控制峰值 |
| 任务并发竞争 CPU | 互相拖慢 | 信号量限制同时 1 个 pipeline |
| ffprobe/ffmpeg 未安装 | 音频读取失败 | README 列出依赖；启动时检测提示 |

---

## 验证步骤

1. **环境验证**：确认 Python 3.9+、Node.js 18+、ffmpeg/ffprobe 已安装并可调用
2. **后端单元验证**：
   - Demucs 分离：用 30 秒音频测试，确认 vocals.wav 生成正确
   - basic-pitch 转录：确认 note_events 非空，音高在合理范围
   - madmom 节拍：确认 BPM 检测合理（60-180 范围）
   - librosa 调性：确认返回有效的 tonic + mode
   - music21 量化：确认 Stream 含正确调号/拍号/音符
   - ABC 导出：确认 abcjs 能渲染生成的 ABC 字符串
3. **API 端点验证**：用 curl/Postman 测试全部 7 个端点
4. **前端验证**：
   - 上传流程：拖拽上传、格式校验
   - 轮询进度：2 秒间隔更新，阶段文案正确
   - 乐谱渲染：abcjs 正确显示五线谱
   - 播放功能：点击播放按钮能听到旋律
   - 下载功能：三格式文件可正确下载
5. **端到端验证**：用 3 分钟流行歌曲测试完整流程，人工检查转录质量
6. **性能验证**：用 10 分钟音频测试，确认不超时不 OOM
7. **四乐器验证**：对同一音频分别选 4 种乐器，确认音域过滤生效
