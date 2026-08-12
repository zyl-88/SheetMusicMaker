# 04 · 依赖关系

## 1. 模块依赖图（后端）

```
routers/transcribe.py
  ├── app.config              (UPLOADS_DIR, MAX_CONCURRENT_TASKS)
  ├── app.services.instruments (INSTRUMENTS 校验)
  ├── app.services.pipeline    (run_pipeline, cleanup_task_files)
  └── app.tasks.store          (task_store)

services/pipeline.py
  ├── app.config
  ├── app.services.instruments (get_instrument)
  ├── app.tasks.store          (task_store + 阶段常量)
  ├── app.utils.audio          (validate_audio_file)      ← 阶段1
  ├── app.services.separator   (VocalSeparator)           ← 阶段2
  ├── app.services.transcriber (NoteTranscriber)          ← 阶段3
  ├── app.services.analyzer    (AudioAnalyzer)            ← 阶段4
  ├── app.services.quantizer   (NoteQuantizer)            ← 阶段5
  └── app.services.exporter    (ScoreExporter)            ← 阶段6

services/exporter.py ──→ services/analyzer.NOTE_NAMES（复用音名表）
services/analyzer.py ──→ numpy / librosa / madmom（可选）
services/separator.py ──→ numpy / soundfile / torch / torchaudio / demucs / librosa
services/transcriber.py ─→ basic_pitch
services/quantizer.py ──→ music21
```

要点：
- **单向依赖**：router → pipeline → services，无循环引用。
- **懒加载依赖**：pipeline 内的 `_get_*()` 在函数内 import，避免服务间强耦合与启动耗时。
- **跨服务常量复用**：`exporter` 复用 `analyzer.NOTE_NAMES`；前端 `STAGES` 镜像后端 `PROGRESS_RANGES`（手工同步，改动需两端一致）。

## 2. 模块依赖图（前端）

```
main.ts
 └─ App.vue
     ├─ api/client.ts          (http 封装)
     ├─ composables/useTranscription.ts
     │    └─ api/client.ts, types.ts
     ├─ composables/useAbcjs.ts
     │    └─ abcjs, types.ts
     ├─ components/FileUpload.vue ── useTranscription.validateFile
     ├─ components/InstrumentSelect.vue ── types.ts
     ├─ components/ProgressTracker.vue ── types.ts (STAGES)
     ├─ components/SheetMusicView.vue ── abcjs
     ├─ components/PlaybackControls.vue ── abcjs, types.ts
     └─ components/DownloadButtons.vue ── api/client.downloadUrl
```

## 3. 前后端 API 契约

| 前端函数 | 方法 & 路径 | 请求 | 响应关键字段 |
|----------|-------------|------|--------------|
| `getHealth` | GET `/api/health` | - | `{status, models_loaded}` |
| `getInstruments` | GET `/api/instruments` | - | `{instruments:[{id,name,min_freq,max_freq}]}` |
| `submitTranscription` | POST `/api/transcribe` | multipart：`file`+`instrument` | `{task_id, status:"pending"}` |
| `getTaskStatus` | GET `/api/tasks/{id}` | - | `{task_id,status,stage,progress,result,error}` |
| `getResult` | GET `/api/tasks/{id}/result` | - | `{abc,key,bpm,time_signature,instrument_name,downloads}` |
| `downloadUrl` | GET `/api/tasks/{id}/download/{format}` | - | 文件（abc:text/plain, musicxml:application/xml, midi:audio/midi） |
| `deleteTask` | DELETE `/api/tasks/{id}` | - | `{status:"deleted"}` |

**任务状态机**：`pending → running → completed | failed`；前端 phase：`idle → uploading → polling → completed | failed`。

## 4. 第三方依赖清单

### 后端（requirements.txt）

| 包 | 版本约束 | 用途 |
|----|----------|------|
| fastapi | >=0.104.0 | Web 框架 |
| uvicorn[standard] | >=0.24.0 | ASGI 服务器 |
| python-multipart | >=0.0.6 | multipart 表单解析 |
| demucs | >=4.0.0 | 人声分离（htdemucs） |
| basic-pitch | >=0.2.0 | 音符转录 |
| madmom | >=0.16.1 | 节拍/BPM 检测（numpy 2.x 下可能不可用） |
| librosa | >=0.10.0 | 重采样/调性检测/降级节拍检测 |
| music21 | >=9.1.0 | 音乐建模与 MusicXML/MIDI 导出 |
| numpy | >=1.24.0 | 数值计算 |
| soundfile | >=0.12.0 | 音频读写/时长读取 |

间接依赖（代码中直接 import）：`torch`、`torchaudio`（separator 加载/回退）、`shutil/os/json/subprocess`（标准库）。

### 前端（package.json）

| 包 | 版本 | 用途 |
|----|------|------|
| vue | ^3.4.0 | 框架 |
| abcjs | ^6.2.2 | 乐谱渲染 + Web Audio 播放 |
| @fontsource/cormorant-garamond | ^5.2.11 | 标题字体（自托管） |
| @fontsource/inter | ^5.2.8 | UI 字体（自托管） |
| vite | ^5.0.0 | 构建工具（devDependency） |
| @vitejs/plugin-vue | ^5.0.0 | Vue 插件（devDependency） |
| typescript | ^5.3.0 | 类型系统（devDependency） |
| vue-tsc | ^2.0.0 | 类型检查（devDependency） |

### 系统依赖

- Python 3.9+、Node.js 18+
- **ffmpeg + ffprobe**（音频解码/时长校验；M4A 依赖；`get_duration` 的 soundfile 失败回退）

## 5. 数据流（一次完整任务）

```
[前端] File 上传 ──multipart──▶ [router] 落盘 storage/uploads/
  ──▶ task_store.create() ──▶ ThreadPoolExecutor.submit(run_pipeline)
  ──▶ [pipeline]
       validating ──▶ storage/temp/{id}/input.ext
       separating ──▶ vocals.wav（能量比<20% 则改用原音频）
       transcribing ──▶ note_events[(start,end,midi,amp,bend)]
       analyzing ──▶ {bpm, key, key_abc, time_signature}
       quantizing ──▶ music21 Stream
       exporting ──▶ ABC 字符串 + {title}.musicxml + {title}.mid（写入 storage/outputs/{id}/）
  ──▶ task_store.complete(result)
[前端] 轮询 GET /api/tasks/{id} ◀── progress/stage
[前端] GET /api/tasks/{id}/result ──▶ abcjs 渲染 ──▶ soundfont 试听 ──▶ 下载
[清理] DELETE /api/tasks/{id} ──▶ 删除 temp/{id}、outputs/{id}、uploads/{原文件}
```

## 6. 存储布局

```
storage/                      # 运行时可写（开发=仓库根，EXE=EXE 同级）
├── uploads/                  # 上传的原始音频（DELETE 时清理）
├── temp/{task_id}/           # 流水线临时文件（input.ext、vocals.wav，完成后清理）
└── outputs/{task_id}/        # {title}.abc / {title}.musicxml / {title}.mid
```

其他路径：`soundfont/`（音色库，.gitignore 排除）、`backend/app/static/`（前端构建产物）、`torch_hub/`（Demucs 模型缓存，.gitignore 排除）。

## 7. 注意事项（易踩坑点）

1. **STAGES 双端同步**：前端 `types.ts STAGES` 与后端 `pipeline.PROGRESS_RANGES` 需保持一致。
2. **madmom 兼容性**：numpy 2.x 下 madmom 可能无法导入，analyzer 已内置 librosa 降级。
3. **Windows 多进程**：Demucs `num_workers=0`、EXE 入口 `freeze_support()` 都是规避 spawn 问题。
4. **`_file_paths` 内部字段**：对外接口已过滤，新增字段时勿直接暴露服务器磁盘路径。
5. **音色库缺失**：`/soundfont` 目录不存在时 main.py 跳过挂载，试听会失败——本地部署需确保音色库在位（.gitignore 已排除，需手动放置）。
