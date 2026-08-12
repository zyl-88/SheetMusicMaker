# 01 · 整体架构

## 1. 架构总览

项目采用 **前后端分离的模块化单体（Modular Monolith）** 架构，单仓库双端：

- **前端**：Vue 3 + TypeScript + Vite + abcjs，构建产物输出到后端 `backend/app/static`，生产环境由 FastAPI 直接托管，实现「一个服务、一个端口、局域网可访问」。
- **后端**：Python FastAPI，负责音频处理全流程（Demucs / basic-pitch / madmom / librosa / music21），通过**内存任务存储 + 线程池**实现异步任务与进度上报。
- **部署形态**：CPU 本地处理，可开发模式运行，也可用 PyInstaller 打包为 Windows EXE 双击运行。

```
┌─────────────────────────────────────────────────────────────┐
│                        浏览器 (用户)                          │
│   上传音频 / 轮询进度 / 查看乐谱 / 试听 / 下载 ABC·MusicXML·MIDI │
└───────────────┬───────────────────────────────┬─────────────┘
                │                               │
     开发: http://localhost:5173       生产: http://localhost:8000
      │ Vite Dev Server (proxy /api)     │ FastAPI StaticFiles 托管
      ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI 应用 (app.main)                   │
│  lifespan 预加载模型 │ CORS │ /api/* 路由 │ /soundfont │ 静态前端  │
├─────────────────────────────────────────────────────────────┤
│  routers/transcribe  API 层（提交/轮询/下载/清理）             │
│  services/pipeline    流水线编排（6 阶段 + 进度）              │
│  services/*           领域服务（分离/转录/分析/量化/导出/乐器） │
│  tasks/store          内存任务存储（线程安全）                 │
│  utils/audio          音频校验                                │
│  ThreadPoolExecutor(max_workers=1)  ← 异步执行 pipeline       │
└─────────────────────────────────────────────────────────────┘
```

## 2. 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 后端 Web | FastAPI + Uvicorn + python-multipart | REST API、异步、multipart 上传 |
| 人声分离 | Demucs 4.x（`htdemucs` 模型，CPU） | 从混合音频提取 vocals 轨 |
| 音符转录 | Spotify basic-pitch | 音频 → 音符事件序列 |
| 节拍/调性 | madmom（节拍追踪）→ 降级 librosa；librosa chroma_cqt + Krumhansl-Schmuckler | BPM / 调性检测 |
| 音乐建模 | music21 | 音符量化 → Stream → MusicXML/MIDI 导出 |
| ABC 生成 | 自研 ABC 构建器（exporter.py） | 生成 abcjs 兼容字符串 |
| 前端框架 | Vue 3（Composition API, `<script setup>`）+ TypeScript | SPA |
| 乐谱渲染/播放 | abcjs 6.x | 浏览器渲染乐谱 + Web Audio 试听 |
| 构建 | Vite 5 + vue-tsc | 开发服务器、类型检查、构建 |
| 打包 | PyInstaller（onedir） | Windows EXE 桌面化 |

## 3. 目录结构

```
SheetMusicMaker/
├── backend/                      # Python FastAPI 后端
│   ├── run.py                    # EXE 启动入口（双击运行）
│   ├── requirements.txt          # Python 依赖
│   └── app/
│       ├── main.py               # 应用工厂 / 生命周期 / 中间件 / 静态托管
│       ├── config.py             # 集中配置（路径/限制/模型参数）
│       ├── schemas.py            # Pydantic 响应模型
│       ├── routers/
│       │   └── transcribe.py     # 转录 API 路由
│       ├── services/
│       │   ├── pipeline.py       # 6 阶段流水线编排（核心）
│       │   ├── separator.py      # Demucs 人声分离
│       │   ├── transcriber.py    # basic-pitch 转录 + 后处理
│       │   ├── analyzer.py       # 节拍/速度/调性分析
│       │   ├── quantizer.py      # music21 量化
│       │   ├── exporter.py       # ABC/MusicXML/MIDI 导出
│       │   └── instruments.py    # 乐器音域配置
│       ├── tasks/
│       │   └── store.py          # 线程安全内存任务存储
│       ├── utils/
│       │   └── audio.py          # 音频文件校验
│       └── static/               # 前端构建产物（npm run build 输出）
├── frontend/                     # Vue 3 前端
│   ├── vite.config.ts            # 代理 + 构建输出到后端 static
│   ├── package.json
│   └── src/
│       ├── main.ts               # 应用入口
│       ├── App.vue               # 顶层组件（三态切换）
│       ├── types.ts              # 类型 + 常量 + 兜底数据
│       ├── api/client.ts         # fetch 封装 + 轮询
│       ├── composables/
│       │   ├── useTranscription.ts  # 上传→轮询→结果 状态管理
│       │   └── useAbcjs.ts          # abcjs 渲染 + 播放封装
│       ├── components/
│       │   ├── FileUpload.vue       # 拖拽/点击上传
│       │   ├── InstrumentSelect.vue # 乐器选择卡片
│       │   ├── ProgressTracker.vue  # 阶段进度条
│       │   ├── SheetMusicView.vue   # 乐谱渲染
│       │   ├── PlaybackControls.vue # 试听控件
│       │   └── DownloadButtons.vue  # 下载 + 复制 ABC
│       └── styles/design-system.css # 设计系统（印刷乐谱美学）
└── docs/code-wiki/               # 本文档
```

## 4. 一次完整请求的生命周期

1. 用户在 [App.vue](../../frontend/src/App.vue) 选择音频文件 + 乐器，点击「开始转录」。
2. [useTranscription.ts](../../frontend/src/composables/useTranscription.ts) 校验文件 → `POST /api/transcribe`（multipart）上传。
3. 后端 [transcribe.py](../../backend/app/routers/transcribe.py) 落盘文件到 `storage/uploads/`，`task_store.create()` 创建任务，`ThreadPoolExecutor.submit(run_pipeline)` 异步执行，立即返回 `task_id`。
4. 前端进入 **polling** 阶段，每 2 秒轮询 `GET /api/tasks/{id}` 获取 stage/progress。
5. 后端 pipeline 依次执行 6 个阶段，每阶段通过 `task_store.update()` 上报进度。
6. 完成后前端 `GET /api/tasks/{id}/result`（或轮询响应中直接携带），用 abcjs 渲染乐谱、加载音色试听、提供三种格式下载。
7. 用户点击「重新开始」时前端调用 `DELETE /api/tasks/{id}` 清理后端文件与记录。

## 5. 核心流水线（6 阶段）

定义于 [pipeline.py](../../backend/app/services/pipeline.py#L31-L38)：

| 阶段 (stage) | 进度区间 | 服务 | 说明 |
|--------------|----------|------|------|
| validating | 0–5% | [utils/audio.py](../../backend/app/utils/audio.py) | 校验扩展名/大小/时长 |
| separating | 5–35% | [separator.py](../../backend/app/services/separator.py) | Demucs 提取 vocals，并按能量比智能选择转录源（非人声音乐回退原始音频） |
| transcribing | 35–55% | [transcriber.py](../../backend/app/services/transcriber.py) | basic-pitch 转录 + 频率范围过滤 + 噪声过滤 + 旋律提取 + 音符合并 |
| analyzing | 55–70% | [analyzer.py](../../backend/app/services/analyzer.py) | madmom 节拍/BPM（降级 librosa）+ 调性检测（Krumhansl-Schmuckler）+ 拍号推断 |
| quantizing | 70–85% | [quantizer.py](../../backend/app/services/quantizer.py) | 秒级事件 → music21 Stream，量化到 1/16 网格，设置调号/拍号/速度，音域钳制 |
| exporting | 85–100% | [exporter.py](../../backend/app/services/exporter.py) | 自研 ABC 构建器 + music21 导出 MusicXML / MIDI |

各服务采用**懒加载单例**（`pipeline.py` 中的 `_get_separator()` 等），首次使用时才加载模型，避免启动阻塞。

## 6. 关键设计决策

| 决策 | 理由（来自代码注释） |
|------|----------------------|
| 线程池 `max_workers=1` | CPU 受限，同时只跑一个 pipeline（[config.py](../../backend/app/config.py#L63)） |
| 内存任务存储而非数据库 | 本地单机工具，无需持久化；任务生命周期短 |
| 智能源选择（vocals 能量比阈值 0.20） | 钢琴/器乐独奏会被 Demucs 归入 `other` 而非 `vocals`，回退原始音频转录（[pipeline.py](../../backend/app/services/pipeline.py#L147-L170)） |
| basic-pitch 后处理流水线 | 过滤次低频噪声/过短/过弱音符；按振幅非最大抑制剔除泛音；合并同音高连续音符（[transcriber.py](../../backend/app/services/transcriber.py#L81-L204)） |
| madmom → librosa 降级 | numpy 2.x 与 madmom 不兼容时降级（[analyzer.py](../../backend/app/services/analyzer.py#L64-L74)） |
| Demucs `num_workers=0` | 避免 Windows multiprocessing spawn 问题（[separator.py](../../backend/app/services/separator.py#L124)） |
| 音色库自托管（soundfont） | 局域网离线可用；`userAction=false` 延迟加载满足 AudioContext 手势约束（[useAbcjs.ts](../../frontend/src/composables/useAbcjs.ts#L28-L51)） |
| 路径双环境（开发 vs PyInstaller frozen） | [config.py](../../backend/app/config.py#L8-L30) 区分只读资源目录与可写存储目录 |
