# 乐谱制作器 — Phase 8 联调验证与修复计划

## 摘要

后端（14 个 Python 文件）和前端（13 个文件 + 设计系统 CSS）代码全部完成，`npm run build` 成功，230 个音色 MP3 已就位。本计划覆盖 Phase 8 的全部工作：修复环境阻塞问题、代码缺陷修复、测试音频生成、后端启动、端到端测试、四乐器验证、LAN 部署验证。

**核心阻塞问题：**
1. Python 依赖未安装（仅 numpy/onnxruntime，缺 fastapi/uvicorn/demucs/basic-pitch/librosa/music21/soundfile 等）
2. ffmpeg 是精简视频版（`--disable-everything`，无音频解复用器/解码器），导致 ffprobe 时长校验失败
3. madmom 在 Windows + numpy 2.x 下可能安装/运行失败

**代码缺陷：**
4. `GET /api/tasks/{id}` 返回完整 task dict，暴露 `result._file_paths` 内部绝对路径
5. `transcribe.py` 上传文件用原始文件名，存在重名覆盖和路径穿越风险

---

## 当前状态分析

### 已完成（无需改动）
- 后端 14 文件：config / main / schemas / routers/transcribe / services/(separator, transcriber, analyzer, quantizer, exporter, pipeline, instruments) / tasks/store / utils/audio
- 前端 13 文件：main.ts / App.vue / types.ts / api/client.ts / composables/(useTranscription, useAbcjs) / components/(FileUpload, InstrumentSelect, ProgressTracker, SheetMusicView, PlaybackControls, DownloadButtons) / styles/design-system.css
- 前端构建产物：`backend/app/static/`（含 index.html、JS、CSS、@fontsource 字体文件）
- 音色库：`soundfont/` 下 5 目录（acoustic_grand_piano/flute/violin/recorder/oboe），各 46 个 MP3
- Vite 配置：`/api` 和 `/soundfont` 代理到 `localhost:8000`，`host: true`

### 环境现状
- Python 3.10.11（TRAE 托管，无虚拟环境）
- pip 25.3
- 已装：numpy 2.2.6, onnxruntime 1.20.1, scipy 1.15.3
- ffmpeg 6.1.1（TRAE 自带精简版，仅视频解复用器 mov/avi/flv/matroska，无音频解码器）
- **关键发现：basic-pitch 在 Windows 上使用 onnxruntime（已安装），不依赖 PyTorch。仅 Demucs 依赖 torch/torchaudio。**

---

## 实施步骤

### 步骤 1：安装 PyTorch CPU 版（Demucs 依赖）

Demucs 依赖 torch + torchaudio。必须先装 CPU 版 torch，否则 `pip install demucs` 会拉取 CUDA 版（约 2GB+）。

**操作：**
```powershell
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

**验证：**
```powershell
python -c "import torch, torchaudio; print(f'torch {torch.__version__}, torchaudio {torchaudio.__version__}')"
pip show numpy | findstr Version
```
- 期望：torch 2.5.x+，torchaudio 匹配版本
- **关键检查**：numpy 版本须仍为 2.2.6，若被降级则 `pip install "numpy>=2.0,<3.0"` 恢复

### 步骤 2：安装后端 Python 依赖

按依赖顺序安装，避免版本冲突：

```powershell
pip install "librosa>=0.10.0" "music21>=9.1.0" "soundfile>=0.12.0"
pip install "demucs>=4.0.0"
pip install "basic-pitch>=0.2.0"
pip install "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" "python-multipart>=0.0.6"
```

说明：
- librosa 自动拉入 soundfile（但显式安装确保版本）
- basic-pitch 在 Windows 上使用已安装的 onnxruntime，不拉入 TensorFlow
- demucs 检测到 torch/torchaudio 已满足，不重新安装

**验证：**
```powershell
python -c "
import librosa, music21, soundfile, demucs.api, fastapi, uvicorn, numpy
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import Model
print(f'numpy={numpy.__version__}, librosa={librosa.__version__}, soundfile={soundfile.__version__}')
print(f'libsndfile={soundfile.__libsndfile_version__}')
print(f'basic-pitch model: {ICASSP_2022_MODEL_PATH}')
print('全部通过')
"
```
- 期望：无报错
- libsndfile 版本须 >= 1.1.0（支持 MP3 读取）

### 步骤 3：尝试安装 madmom

madmom 0.16.1 无 Windows 预编译 wheel，需 Cython + C 编译器。预期大概率失败。

```powershell
pip install madmom
```

**两种结果：**
- **失败（预期）**：出现 `error: Microsoft Visual C++ 14.0 or greater is required`。这是预期行为，步骤 6 的运行时降级方案会自动处理。
- **成功**：验证导入 `python -c "from madmom.features.beats import RNNBeatProcessor; print('OK')"`。若导入失败（numpy 2.x ABI 不兼容），步骤 6 的 try/except 也会自动降级。

### 步骤 4：安装完整版 ffmpeg

当前 TRAE 自带 ffmpeg 是精简视频版，无音频解码能力。

**操作（下载 gyan.dev essentials 构建版）：**
```powershell
Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "$env:TEMP\ffmpeg-essentials.zip"
New-Item -ItemType Directory -Force -Path "C:\ffmpeg" | Out-Null
Expand-Archive -Path "$env:TEMP\ffmpeg-essentials.zip" -DestinationPath "C:\ffmpeg" -Force
$ffmpegBin = (Get-ChildItem "C:\ffmpeg\*\bin" -Directory | Select-Object -First 1).FullName
Write-Output "ffmpeg bin: $ffmpegBin"
$env:PATH = "$ffmpegBin;$env:PATH"
```

**验证：**
```powershell
ffmpeg -version | Select-Object -First 2
ffmpeg -hide_banner -demuxers 2>&1 | findstr "mp3 wav flac mov"
ffmpeg -hide_banner -decoders 2>&1 | findstr "mp3float pcm_s16le flac aac"
```
- 期望：显示 `gyan.dev` 构建而非 TRAE 版本，demuxers 含 mp3/wav/flac，decoders 含 mp3float/pcm_s16le/flac/aac
- **重要**：后续每个运行 uvicorn 的终端都须先 `$env:PATH = "$ffmpegBin;$env:PATH"` 前置完整版

### 步骤 5：预下载 Demucs 模型

首次运行 Demucs 会下载 htdemucs 模型（约 80MB），预下载避免首次 E2E 测试超时。

```powershell
python -c "import demucs.pretrained; m = demucs.pretrained.get_model('htdemucs'); print('模型参数量:', sum(p.numel() for p in m.parameters()))"
```

### 步骤 6：修复 `backend/app/utils/audio.py` — soundfile 替换 ffprobe

**问题：** 当前 `get_duration()` 依赖 ffprobe，但 TRAE 精简版 ffmpeg 无 mp3/wav/flac 解复用器，对所有支持的音频格式均会失败。

**修复方案：** soundfile 优先（自带 WAV/FLAC/MP3 解码），ffprobe 后备（完整版 ffmpeg 可处理 M4A）。

**修改文件：** `d:\project\SheetMusicMaker\backend\app\utils\audio.py`

**修改内容：** 将 `get_duration()` 函数替换为双策略实现：
1. 先用 `soundfile.info()` 读取时长（支持 WAV/FLAC/MP3，libsndfile >= 1.1.0）
2. soundfile 失败时降级到 ffprobe（需完整版 ffmpeg，支持 M4A）
3. 新增 `import logging` 和 `logger`
4. 保留 `validate_extension()`、`validate_size()`、`validate_duration()`、`validate_audio_file()` 不变

### 步骤 7：修复 `backend/app/services/analyzer.py` — madmom 运行时降级

**问题：** madmom 可能未安装或与 numpy 2.x 不兼容，导致 `analyzing` 阶段崩溃。

**修复方案：** 在 `_detect_beats_and_tempo()` 中加 try/except ImportError，自动降级到 `librosa.beat.beat_track`。

**修改文件：** `d:\project\SheetMusicMaker\backend\app\services\analyzer.py`

**修改内容：**
1. 替换 `_detect_beats_and_tempo()` 方法：开头 `try: from madmom...` / `except ImportError: return self._detect_beats_librosa(path)`
2. 新增 `_detect_beats_librosa()` 方法：用 `librosa.beat.beat_track(y=y, sr=sr)` + `librosa.frames_to_time()` 实现，返回格式与 madmom 版本一致（`(beats_list, bpm_float)`）
3. BPM 合理性校验（40-250）和节拍间隔估算逻辑保持一致
4. 日志标注使用哪个引擎

### 步骤 8：修复 `backend/app/routers/transcribe.py` — 安全修复

**问题 1：** `GET /api/tasks/{id}` 返回完整 task dict，暴露 `result._file_paths`（内部绝对路径）、`audio_filename`、`instrument`、`created_at` 等内部字段。

**问题 2：** `upload_path = UPLOADS_DIR / file.filename` 直接用原始文件名，存在重名覆盖和路径穿越风险。

**修改文件：** `d:\project\SheetMusicMaker\backend\app\routers\transcribe.py`

**修改内容：**
1. `get_task_status()` 端点：返回前过滤掉 `result._file_paths`、`audio_filename`、`instrument`、`created_at`，只返回前端需要的字段（task_id, status, stage, progress, result[不含_file_paths], error）
2. `submit_transcription()` 端点：用 `uuid` 或 `task_id` 作为上传文件名前缀（如 `{task_id}_{filename}`），避免重名覆盖；或用 `secure_filename` 思路去除路径分隔符

### 步骤 9：代码修复验证

```powershell
cd d:\project\SheetMusicMaker\backend
python -c "from app.utils.audio import get_duration; print('audio.py OK')"
python -c "from app.services.analyzer import AudioAnalyzer; a=AudioAnalyzer(); assert hasattr(a,'_detect_beats_librosa'); print('analyzer.py OK')"
python -c "from app.main import app; print(f'FastAPI OK, routes={len(app.routes)}')"
```
期望：全部无报错。

### 步骤 10：生成测试音频

用 numpy + soundfile 生成约 15 秒 C 大调音阶 WAV，谐波丰富（基频 + 5 次谐波）模拟人声/乐器音色，利于 Demucs 人声分离。

**脚本位置：** `c:\Users\zhuyu\.trae-cn\work\6a4358cbdfd24a89591453dd\gen_test_audio.py`
**输出位置：** `d:\project\SheetMusicMaker\test_audio\test_c_major.wav`

**音频规格：**
- 音阶：C4 D4 E4 F4 G4 A4 B4 C5（C 大调，8 个音符）
- 每音 1.5 秒，间隔 0.1 秒，首尾各 1 秒静音
- 总时长约 14.8 秒
- 采样率 22050Hz，单声道，16-bit PCM WAV
- 谐波结构：基频 + 5 次谐波（幅度递减），ADSR 包络（10ms attack, 100ms release）

**验证：**
```powershell
python -c "
import soundfile as sf
info = sf.info(r'd:\project\SheetMusicMaker\test_audio\test_c_major.wav')
print(f'时长={info.frames/info.samplerate:.2f}s, sr={info.samplerate}, ch={info.channels}')
assert 14 < info.frames/info.samplerate < 16
"
cd d:\project\SheetMusicMaker\backend
python -c "from app.utils.audio import get_duration; d=get_duration(r'd:\project\SheetMusicMaker\test_audio\test_c_major.wav'); print(f'{d:.2f}s'); assert 14<d<16"
```

### 步骤 11：启动后端

```powershell
cd d:\project\SheetMusicMaker\backend
$env:PATH = "C:\ffmpeg\<解压目录>\bin;$env:PATH"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**启动日志期望：**
```
应用启动中...
预加载 basic-pitch 模型...
basic-pitch 模型加载完成
Demucs 可用
应用启动完成
```

### 步骤 12：API 接口验证

在另一终端执行：

```powershell
curl http://localhost:8000/api/health
# 期望: {"status":"ok","models_loaded":true}

curl http://localhost:8000/api/instruments
# 期望: 4 个乐器的 JSON

curl -o NUL -w "%{http_code}" http://localhost:8000/
# 期望: 200（前端 index.html）

curl -o NUL -w "%{http_code}" http://localhost:8000/soundfont/flute-mp3/C4.mp3
# 期望: 200（音色文件可访问）
```

### 步骤 13：命令行端到端测试

**上传：**
```powershell
$resp = curl -s -X POST http://localhost:8000/api/transcribe -F "file=@d:\project\SheetMusicMaker\test_audio\test_c_major.wav" -F "instrument=flute"
$taskId = ($resp | ConvertFrom-Json).task_id
```

**轮询：**
```powershell
while ($true) {
    $task = (curl -s http://localhost:8000/api/tasks/$taskId | ConvertFrom-Json)
    Write-Output "[$(Get-Date -Format 'HH:mm:ss')] status=$($task.status) stage=$($task.stage) progress=$($task.progress)%"
    if ($task.status -eq "completed" -or $task.status -eq "failed") { break }
    Start-Sleep -Seconds 3
}
$task | ConvertTo-Json -Depth 5
```

**期望轮询过程：**
- `validating` 0-5% — 快速通过
- `separating` 5-35% — Demucs CPU 处理约 30-90 秒
- `transcribing` 35-55% — basic-pitch 约 10-30 秒
- `analyzing` 55-70% — 节拍/调性约 5-15 秒
- `quantizing` 70-85% — music21 量化，快速
- `exporting` 85-100% — 导出，快速
- `completed` — 最终

**结果验证：**
- 调性：C major（C 大调音阶，Krumhansl 算法应正确识别）
- BPM：合理值（60-200 范围）
- 拍号：4/4
- ABC 字符串：以 `X:1` 开头，包含 `K:C` 调性标记
- 音符：C4-C5 范围（abcjs 表示 C D E F G A B c）

**下载验证：**
```powershell
curl -s -o "$env:TEMP\test.abc" http://localhost:8000/api/tasks/$taskId/download/abc
curl -s -o "$env:TEMP\test.xml" http://localhost:8000/api/tasks/$taskId/download/musicxml
curl -s -o "$env:TEMP\test.mid" http://localhost:8000/api/tasks/$taskId/download/midi
```
期望：三个文件非空，ABC 是文本，MusicXML 以 `<?xml` 开头，MIDI 非空。

### 步骤 14：浏览器端到端测试

**访问** `http://localhost:8000/`

**验证上传态：**
- 标题"乐谱制作器"显示，衬线字体
- 健康徽章显示"模型就绪"（绿色圆点）
- 文件上传区域 + 乐器选择可见

**执行流程：**
1. 选择 `test_c_major.wav`
2. 选择"长笛"
3. 点击"开始转录"

**验证处理态：**
- ProgressTracker 显示当前阶段和进度
- 阶段标签依次：校验音频文件 → 分离人声主旋律 → 转录音符 → 分析节拍与调性 → 量化音符时值 → 生成乐谱

**验证结果态：**
- 元数据栏：调性(C)、速度(XX BPM)、拍号(4/4)、乐器(长笛)
- abcjs 渲染五线谱：高音谱号、C 大调无升降号、C4-C5 音符
- 播放控件可见
- 下载按钮（ABC/MusicXML/MIDI）+ 复制 ABC + 重新开始

**验证播放：**
1. 点击 Play → 浏览器播放（flute-mp3 音色）
2. 能听到 C 大调音阶旋律
3. F12 Network 中可见 `GET /soundfont/flute-mp3/*.mp3` 请求（200）

**验证下载和重置：**
- 三格式下载成功
- 复制 ABC → 按钮变"已复制 ✓"
- 重新开始 → 回到上传态，选择清空

### 步骤 15：四乐器验证

对同一测试音频分别用 4 种乐器执行命令行 E2E：

| 乐器 | instrument | 音域 | program | 音色文件夹 |
|------|-----------|------|---------|-----------|
| 长笛 | flute | C4-D7 | 73 | flute-mp3 |
| 小提琴 | violin | G3-E7 | 40 | violin-mp3 |
| 竖笛 | recorder | C4-D7 | 74 | recorder-mp3 |
| 双簧管 | oboe | Bb3-G6 | 68 | oboe-mp3 |

```powershell
foreach ($inst in @("flute","violin","recorder","oboe")) {
    $resp = curl -s -X POST http://localhost:8000/api/transcribe -F "file=@d:\project\SheetMusicMaker\test_audio\test_c_major.wav" -F "instrument=$inst"
    $taskId = ($resp | ConvertFrom-Json).task_id
    while ($true) {
        $task = (curl -s http://localhost:8000/api/tasks/$taskId | ConvertFrom-Json)
        if ($task.status -eq "completed" -or $task.status -eq "failed") { break }
        Start-Sleep -Seconds 3
    }
    Write-Output "$inst: $($task.status)"
    if ($task.status -eq "completed") {
        $r = $task.result
        Write-Output "  调性=$($r.key.tonic) $($r.key.mode), BPM=$($r.bpm), ABC长度=$($r.abc.Length)"
    } else { Write-Output "  错误=$($task.error)" }
    curl -s -X DELETE http://localhost:8000/api/tasks/$taskId | Out-Null
}
```

**期望：** 4 个全部 completed，调性均为 C major，ABC 非空。浏览器中验证各乐器播放音色不同。

### 步骤 16：LAN 部署测试

**启动：** `uvicorn app.main:app --host 0.0.0.0 --port 8000`（已绑定所有接口）

**LAN IP：** 查看本机 WLAN IP（如 `172.16.0.116`）

**从另一设备访问：** `http://<本机IP>:8000/`

**验证项：**
1. 页面加载（CSS/字体/JS 静态资源可访问）
2. 健康徽章显示"模型就绪"
3. 乐器列表可获取
4. 上传音频可成功提交
5. 轮询进度正常更新
6. 乐谱渲染正确
7. 音色播放（`/soundfont/*.mp3` 可访问）
8. 三格式下载

**防火墙：** 如无法访问，添加入站规则：
```powershell
netsh advfirewall firewall add rule name="SheetMusicMaker" dir=in action=allow protocol=TCP localport=8000
```

---

## 假设与决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| ffmpeg 修复策略 | soundfile 替代 ffprobe + 安装完整 ffmpeg | soundfile 自带 WAV/FLAC/MP3 解码；完整 ffmpeg 为 Demucs/librosa 后备 |
| madmom 降级 | 运行时 try/except 自动降级到 librosa | 比安装时判断更健壮，覆盖安装成功但运行时 ABI 不兼容的情况 |
| 测试音频 | numpy 生成 C 大调音阶 | 明确旋律便于验证转录正确性；谐波丰富利于 Demucs 分离 |
| API 安全修复 | 过滤 _file_paths + 安全文件名 | 防止内部路径泄露和文件覆盖 |
| 部署模式 | FastAPI 静态服务（生产模式） | 前端构建产物已输出到 backend/app/static/，单端口部署 |

---

## 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| madmom 安装/运行失败 | 高 | 低 | 运行时降级到 librosa.beat.beat_track，功能等价 |
| numpy 被降级到 1.x | 中 | 高 | 每次安装后验证 `pip show numpy`，必要时 `pip install "numpy>=2.0,<3.0"` |
| Demucs CPU 处理慢 | 高 | 中 | 预下载模型；15s 音频预计 30-90s；耐心等待 |
| Demucs 将合成音分类为 "other" | 中 | 高 | 谐波丰富波形已设计；若仍失败可跳过分离直接用原始音频测试下游 |
| abcjs recorder 音色映射异常 | 低 | 低 | 回退到 piano 音色，不影响功能验证 |
| Windows 防火墙阻止 8000 | 中 | 中 | 添加入站规则 |

---

## 验证清单

1. `pip install` 全部成功，numpy 仍为 2.2.6
2. 完整版 ffmpeg 在 PATH 中（demuxers 含 mp3/wav/flac）
3. Demucs 模型预下载完成
4. `audio.py` 修改后 `get_duration()` 能读取 WAV
5. `analyzer.py` 降级方法 `_detect_beats_librosa()` 存在
6. `transcribe.py` 不再返回 `_file_paths`
7. 测试音频 15 秒 WAV 生成成功
8. 后端启动，`/api/health` 返回 `models_loaded: true`
9. `/soundfont/flute-mp3/C4.mp3` 返回 200
10. 命令行 E2E：上传→轮询→completed→ABC 含 C 大调音符
11. 三格式下载文件非空
12. 浏览器 E2E：五线谱渲染、播放出声、下载正常
13. 四乐器全部 completed
14. LAN 设备可访问并完成 E2E
