# 02 · 后端模块与关键类

> 后端为 Python FastAPI，按包结构划分为：入口、配置、模型、路由、服务、任务存储、工具。
> 建议阅读顺序：`main.py → config.py → routers/transcribe.py → services/pipeline.py → 各领域服务`。

## 1. 应用入口 [app/main.py](../../backend/app/main.py)

FastAPI 应用工厂，负责组装。

| 元素 | 说明 |
|------|------|
| `lifespan(app)` | 启动时预加载 basic-pitch 与 Demucs 模型（失败仅告警，首次使用时再加载）；退出时打日志 |
| `models_loaded` | 全局标志，供 `/api/health` 返回模型就绪状态 |
| `app = FastAPI(...)` | 标题「乐谱制作器」，版本 1.0.0 |
| CORSMiddleware | `allow_origins=["*"]`，全开放（本地局域网工具） |
| 路由注册 | `include_router(transcribe.router, prefix="/api")` |
| `GET /api/health` | 健康检查 + 模型就绪状态 |
| `GET /api/instruments` | 返回 `{instruments: [...]}`（转调 `services.instruments.list_instruments`） |
| 静态挂载 | `SOUNDFONT_DIR` 存在则挂载 `/soundfont`（abcjs 播放音色）；`STATIC_DIR` 存在则挂载 `/`（生产托管前端） |

## 2. 集中配置 [app/config.py](../../backend/app/config.py)

所有可调参数集中于此，无环境变量，改代码即生效。

**路径（开发 vs PyInstaller frozen 双环境）**

| 常量 | 开发环境 | 打包环境 |
|------|----------|----------|
| `SOUNDFONT_DIR` | 仓库根/soundfont | `sys._MEIPASS/soundfont` |
| `STATIC_DIR` | `app/static` | `sys._MEIPASS/app/static` |
| `STORAGE_DIR` | 仓库根/storage | EXE 同级/storage |
| `UPLOADS_DIR / TEMP_DIR / OUTPUTS_DIR` | storage 下三个子目录 | 同左（可写） |

**核心参数分组**

- 音频限制：`MAX_FILE_SIZE_MB=50`、`MAX_DURATION_SEC=600`、`ALLOWED_EXTENSIONS={mp3,wav,m4a,flac}`
- Demucs：`DEMUCS_MODEL="htdemucs"`、`DEMUCS_DEVICE="cpu"`、`SEGMENT=7`、`JOBS=4`、`SHIFTS=1`
- basic-pitch：`ONSET_THRESHOLD=0.5`、`FRAME_THRESHOLD=0.3`、`MIN_NOTE_LENGTH=100ms`
- 音符后处理：`MIN_NOTE_AMPLITUDE=0.1`、`MIN_MIDI_PITCH=36`、`MERGE_GAP_THRESHOLD=0.08s`
- 人声回退：`DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD=0.20`
- 量化：`QUANTIZE_DIVISION=4`（1/16 音符网格）、`DEFAULT_TIME_SIG="4/4"`
- 任务：`MAX_CONCURRENT_TASKS=1`、`POLL_INTERVAL_MS=2000`
- 输出：`OUTPUT_SAMPLE_RATE=44100`、`OUTPUT_AUDIO_FORMAT="wav"`

## 3. 响应模型 [app/schemas.py](../../backend/app/schemas.py)

Pydantic 模型：`HealthResponse`、`InstrumentInfo`、`InstrumentsResponse`、`TranscribeResponse`、`TaskResult`（abc/key/bpm/time_signature/instrument_name/downloads）、`TaskStatus`。

## 4. API 路由 [app/routers/transcribe.py](../../backend/app/routers/transcribe.py)

`router = APIRouter()`，模块级 `ThreadPoolExecutor(max_workers=1)`。

| 方法 & 路径 | 关键函数 | 行为 |
|-------------|----------|------|
| `POST /api/transcribe` | `submit_transcription` | 校验乐器 → 安全文件名落盘 → `task_store.create` → 提交线程池 → 返回 `{task_id, status:"pending"}` |
| `GET /api/tasks/{task_id}` | `get_task_status` | 轮询状态，过滤内部字段 `_file_paths` |
| `GET /api/tasks/{task_id}/result` | `get_task_result` | 仅 completed 可获取，返回 abc/key/bpm/time_signature/instrument_name/downloads |
| `GET /api/tasks/{task_id}/download/{format}` | `download_file` | 按格式（abc/musicxml/midi）返回 `FileResponse`，含正确的 media_type |
| `DELETE /api/tasks/{task_id}` | `delete_task` | 清理任务目录 + 上传原始文件 + 删除任务记录 |

安全细节：`Path(file.filename).name` 仅取文件名防止路径穿越；结果中 `_file_paths` 为内部字段，对外接口统一过滤。

## 5. 流水线编排 [app/services/pipeline.py](../../backend/app/services/pipeline.py)

**职责**：串联 6 阶段、进度上报、异常兜底（失败时 `task_store.fail`）。

**关键数据**

- `PROGRESS_RANGES`：阶段 → 进度区间映射（见架构文档 5 节）。
- 5 个懒加载单例：`_separator / _transcriber / _analyzer / _quantizer / _exporter`，经 `_get_*()` 首次 import 实例化。

**核心函数**

| 函数 | 说明 |
|------|------|
| `run_pipeline(task_id, audio_path, instrument, audio_filename) -> dict` | 全流程：校验 → 分离 → 能量比判定选择转录源 → 转录 → 分析 → 量化 → 导出 → 清理临时目录 → `task_store.complete`；任一异常 `task_store.fail` 后 re-raise |
| `_update_progress(task_id, stage, progress)` | 转发 `task_store.update` |
| `_cleanup_temp(temp_dir)` | 删除任务临时目录 |
| `cleanup_task_files(task_id)` | 删除 temp/outputs 两个任务目录（供 DELETE 端点） |

**结果结构**（`result`）：
```json
{
  "abc": "...", "key": {"tonic": "...", "mode": "..."},
  "bpm": 120.0, "time_signature": "4/4", "instrument_name": "长笛 (Flute)",
  "downloads": {"abc": "...", "musicxml": "...", "midi": "..."},
  "_file_paths": {"musicxml": "...", "midi": "...", "abc": "..."}   // 内部字段
}
```

## 6. 人声分离 [app/services/separator.py](../../backend/app/services/separator.py)

类 `VocalSeparator`（Demucs 4.0.1 底层 API：`get_model` + `apply_model`，注意 PyPI 4.0.1 无 `demucs.api`）。

| 方法 | 说明 |
|------|------|
| `__init__` | 加载模型、`to("cpu")`、记录采样率 44100 与 sources 列表 |
| `_load_audio(path) -> torch.Tensor` | 加载为 `[channels, samples]` float32：优先 soundfile，失败回退 torchaudio（M4A）；librosa 重采样到 44.1k；单声道复制为立体声 |
| `separate(audio_path, progress_callback=None) -> str` | 归一化 → 加 batch 维 → `apply_model(shifts, split=True, overlap=0.25, num_workers=0, segment=7)` → 反归一化 → 取 `vocals` 索引 → 转 `[samples, channels]` → `sf.write` 保存 `vocals.wav`（44.1kHz PCM_16），返回路径 |

## 7. 音符转录 [app/services/transcriber.py](../../backend/app/services/transcriber.py)

类 `NoteTranscriber`（Spotify basic-pitch）。

| 方法 | 说明 |
|------|------|
| `__init__` | 加载 `Model(ICASSP_2022_MODEL_PATH)` |
| `transcribe(wav_path, instrument_config) -> note_events` | 调 `predict()`：`onset/frame threshold`、`minimum_note_length`、按乐器 `minimum/maximum_frequency` 过滤、`multiple_pitch_bends=False`、`melodia_trick=True`；随后 `_post_process_notes` |
| `_post_process_notes(events)` | **6 步后处理**：① 过滤 `< MIN_MIDI_PITCH`（36）次低频噪声；② 过滤 `< 100ms` 短音符；③ 过滤振幅 `< 0.1` 弱音符；④ 按振幅降序排序；⑤ 非最大抑制（与已接受音符重叠 > 50% 视为泛音丢弃）；⑥ 合并同音高且间隔 `< 0.08s` 的连续音符 |

返回格式：`[(start_s, end_s, midi_pitch, amplitude, pitch_bend), ...]`。

## 8. 音频分析 [app/services/analyzer.py](../../backend/app/services/analyzer.py)

类 `AudioAnalyzer`（madmom + librosa），并导出 `NOTE_NAMES`、Krumhansl-Schmuckler 轮廓常量。

| 方法 | 说明 |
|------|------|
| `analyze(wav_path) -> dict` | 组合调用，返回 `{beats, bpm, key:{tonic,mode}, key_abc, time_signature}` |
| `_detect_beats_and_tempo(path)` | madmom 路径：`RNNBeatProcessor → DBNBeatTrackingProcessor(fps=100) → beats`，`TempoEstimationProcessor → BPM`；BPM 异常（<40 或 >250）用节拍间隔中位数估算；**BPM 翻倍修正**：>140 且减半后落在 60–120 则减半 |
| `_detect_beats_librosa(path)` | madmom 不可用（numpy 2.x 不兼容）时降级：`librosa.beat.beat_track`，同样做 BPM 修正 |
| `_detect_key(path)` | `librosa.load(22050, mono) → chroma_cqt → mean(axis=1)`；对 24 个调性做 Pearson 相关，取最高分的 Krumhansl-Schmuckler 匹配 |
| `_infer_time_signature(beats, bpm)` | MVP 固定返回 `"4/4"` |
| `_key_to_abc(key_info)` | 小调加 `m` 后缀（如 `Am`），大调裸主音 |

## 9. 音符量化 [app/services/quantizer.py](../../backend/app/services/quantizer.py)

类 `NoteQuantizer`（music21）。

| 方法 | 说明 |
|------|------|
| `quantize(note_events, analysis, instrument_config) -> Stream` | 秒→四分音符单位（除以 beat_dur=60/bpm）→ 量化到 1/16 网格 → **间隙处理**（小间隙 ≤0.5 拍延长前音连音，大间隙插休止符）→ 音域钳制到 `[midi_low, midi_high]` → 创建 `note.Note` 并映射力度（`amp*127` 裁剪到 1–127）→ `s.insert` |
| `_create_empty_stream(analysis)` | 空 Stream + `key.Key`（`b`→`-` 转换）+ `meter.TimeSignature` + `MetronomeMark`；无音符时的兜底 |

## 10. 乐谱导出 [app/services/exporter.py](../../backend/app/services/exporter.py)

类 `ScoreExporter`，含完整的五度圈调号表（`MAJOR_KEY_SIGNATURES`、`MINOR_RELATIVE_MAJOR`、升降号序列）。

| 方法 | 说明 |
|------|------|
| `export_all(m21_stream, analysis, title, output_dir) -> dict` | 生成 ABC 字符串（内存）、`{title}.musicxml`、`{title}.mid` 三个产物 |
| `_export_abc(...)` | **自研 ABC 构建器**：头部 `X/T/M/L:1/8/Q/K` → 遍历 `stream.flat.notesAndRests` → 音符/休止符转 ABC token → 自动加小节线 `|` → 每 4 小节换行 → 每行 ≤80 字符 |
| `_midi_to_abc_note(midi, key_sharps, key_flats, use_flats)` | MIDI → ABC 音名：自然音级无记号；调号内音级无记号；否则临时升降号（`^`/`_`）；八度映射（≤3 大写+逗号、4 大写、5 小写、≥6 小写+撇号） |
| `_quarter_length_to_abc_duration(ql)` | 以 `L:1/8`（默认 0.5 四分音符）为基准转时值：1→空、整数→数字、0.5→`/`、0.25→`//`、其余→分数如 `3/2` |
| `_get_key_accidentals(tonic, mode)` | 小调转相对大调、升号写法转降号等价，返回 `(sharps_set, flats_set)` |
| `_parse_beats_per_measure(time_sig)` | `"4/4"` → 4 |

## 11. 乐器配置 [app/services/instruments.py](../../backend/app/services/instruments.py)

`INSTRUMENTS` 字典：5 个乐器（universal/flute/violin/recorder/oboe），每项含 `name / min_freq / max_freq / midi_low / midi_high / abc_clef`。

| 函数 | 说明 |
|------|------|
| `get_instrument(id)` | 取配置，不存在抛 `ValueError` |
| `list_instruments()` | 返回 `[{id, name, min_freq, max_freq}]` 供前端 |

## 12. 任务存储 [app/tasks/store.py](../../backend/app/tasks/store.py)

类 `TaskStore`：`dict + threading.Lock`，线程安全。

- 状态常量：`pending / running / completed / failed`
- 阶段常量：`validating / separating / transcribing / analyzing / quantizing / exporting / completed`
- `STAGE_LABELS`：中文标签（前端展示用）
- 方法：`create`（uuid4 前 12 位作 id）、`update`、`complete`（progress=100）、`fail`、`get`（返回副本）、`delete`
- 全局单例：`task_store = TaskStore()`

## 13. 音频校验 [app/utils/audio.py](../../backend/app/utils/audio.py)

| 函数 | 说明 |
|------|------|
| `validate_extension(filename)` | 白名单扩展名校验 |
| `validate_size(file_size)` | ≤50MB |
| `get_duration(path)` | 优先 soundfile（`frames/samplerate`），失败回退 `ffprobe -show_format` 解析 JSON；均失败抛 RuntimeError（提示安装完整版 ffmpeg） |
| `validate_duration(path)` | ≤600s，返回时长 |
| `validate_audio_file(path, filename, size)` | 组合校验，返回 `(ext, duration)` |

## 14. EXE 启动入口 [run.py](../../backend/run.py)

- `multiprocessing.freeze_support()`（Windows 打包必需）
- frozen 环境：设置 `TORCH_HOME` 指向打包缓存目录、创建 EXE 同级 `storage/{uploads,temp,outputs}`
- 后台线程延迟 3s 打开浏览器 `http://localhost:8000`
- `uvicorn.run("app.main:app", host=0.0.0.0, port=8000, reload=False)`
