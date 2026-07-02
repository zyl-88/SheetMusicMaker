# 乐谱制作器 — Phase 8 收尾计划（续）

## 摘要

后端和前端代码已完成，Python 依赖全部安装。本计划聚焦剩余阻塞项：`separator.py` 适配 demucs 4.0.1 底层 API、`main.py` 修复 demucs 引用、生成测试音频、启动后端并完成端到端验证。

---

## 当前状态分析

### 已就绪（无需改动）
- **Python 环境**：torch 2.12.1+cpu, torchaudio 2.11.0+cpu, demucs 4.0.1, soundfile 0.14.0 (libsndfile 1.2.2), librosa 0.11.0, music21 9.9.2, basic-pitch (ONNX) — 全部已安装且导入正常
- **demucs 模型**：htdemucs 已预下载（BagOfModels, 41.9M 参数, sources=['drums','bass','other','vocals'], samplerate=44100）
- **madmom**：未安装（Windows 无 wheel），`analyzer.py` 已有 try/except 降级到 librosa
- **代码修复**：`audio.py`（soundfile 优先）、`analyzer.py`（librosa 降级）、`transcribe.py`（安全文件名 + 响应过滤）已完成
- **前端构建**：`backend/app/static/` 含 `index.html` + `assets/`（vite 输出目录为 `../backend/app/static`）
- **音色库**：`soundfont/` 下 5 目录，各 46 个 MP3
- **ffmpeg**：TRAE 精简版（无音频解码器）。WAV/FLAC/MP3 走 soundfile 无需 ffmpeg；M4A 需完整 ffmpeg（可选）

### 阻塞项
1. **`separator.py`**：使用 `demucs.api.Separator`，该模块在 demucs 4.0.1 (PyPI) 中不存在，导致人声分离阶段崩溃
2. **`main.py` 第 41 行**：`import demucs.api` 在 lifespan 中执行，虽在 try/except 内不会崩溃，但会输出误导性警告日志
3. **测试音频**：`gen_test_audio.py` 脚本不存在（上个会话已丢失），需重新创建

### 关键技术发现
- `demucs.api` 模块仅存在于 GitHub 开发版，PyPI 4.0.1 不含此模块
- demucs 4.0.1 底层 API：`demucs.pretrained.get_model(name)` → `BagOfModels`；`demucs.apply.apply_model(model, mix, shifts, split, overlap, device, num_workers, segment, progress)` → `torch.Tensor`
- `apply_model` 使用 `multiprocessing.Pool`（Windows spawn 模式有风险），应设 `num_workers=0` 避免问题
- Demucs 归一化约定：`ref = mix.mean(0); mix = (mix - ref.mean()) / ref.std()`；反归一化：`out * ref.std() + ref.mean()`
- `apply_model` 输入形状 `[batch, channels, samples]`，输出形状 `[batch, num_sources, channels, samples]`
- torchaudio 2.11.0 有 `load` 方法但无 `info`；soundfile 更可靠，separator 用 soundfile 加载音频
- soundfile + libsndfile 1.2.2 原生支持 WAV/FLAC/MP3 读取，WAV 测试音频无需 ffmpeg

---

## 实施步骤

### 步骤 1：重写 `separator.py` — 适配 demucs 4.0.1 底层 API

**文件**：`d:\project\SheetMusicMaker\backend\app\services\separator.py`

**原因**：`demucs.api.Separator` 在 demucs 4.0.1 中不存在，导致 `ImportError`。

**改动内容**：完全重写 `VocalSeparator` 类，保留对外接口 `__init__()` 和 `separate(audio_path, progress_callback)` 不变：

1. **`__init__`**：
   - `from demucs.pretrained import get_model`
   - `self.model = get_model(DEMUCS_MODEL)` → `BagOfModels`
   - `self.model.to(DEMUCS_DEVICE)` 
   - `self.sample_rate = self.model.samplerate`（44100）
   - `self.sources = self.model.sources`（['drums','bass','other','vocals']）
   - 删除 `import demucs.api` 和 `self.separator = demucs.api.Separator(...)`

2. **新增 `_load_audio(audio_path)` 私有方法**：
   - 用 `soundfile.read(path, dtype="float32", always_2d=True)` 加载音频（支持 WAV/FLAC/MP3）
   - 转置为 `[channels, samples]`
   - soundfile 失败时回退到 `torchaudio.load(path)`（支持 M4A，需 ffmpeg）
   - 如果采样率 ≠ 44100，用 `librosa.resample(wav, orig_sr, target_sr, axis=-1)` 重采样
   - 单声道转立体声：`np.repeat(wav, 2, axis=0)`
   - 返回 `torch.Tensor` 形状 `[channels, samples]`

3. **`separate(audio_path, progress_callback)` 重写**：
   - 调用 `_load_audio()` 加载音频
   - Demucs 归一化：`ref = wav.mean(0); wav_norm = (wav - ref.mean()) / ref.std()`
   - 调用 `apply_model(self.model, wav_norm[None], shifts=DEMUCS_SHIFTS, split=True, overlap=0.25, device=self.device, num_workers=0, segment=DEMUCS_SEGMENT, progress=False)`
     - **关键**：`num_workers=0` 避免 Windows multiprocessing spawn 问题
     - `wav_norm[None]` 增加 batch 维度：`[1, channels, samples]`
   - 反归一化：`estimates = estimates * ref.std() + ref.mean()`
   - 提取 vocals：`vocals_idx = self.sources.index("vocals"); vocals = estimates[0, vocals_idx]`
   - 转 numpy `[samples, channels]`，确保二维
   - 用 `soundfile.write(output_path, vocals, self.sample_rate, subtype="PCM_16")` 保存
   - `progress_callback` 参数保留但不使用（pipeline 的进度通过开始/结束日志控制）

### 步骤 2：修复 `main.py` — 替换 demucs.api 引用

**文件**：`d:\project\SheetMusicMaker\backend\app\main.py`

**原因**：第 41 行 `import demucs.api` 在 lifespan 中会触发 ImportError，虽被 try/except 捕获但输出误导性日志。

**改动内容**：将 lifespan 中 Demucs 预加载块替换为：
```python
try:
    logger.info("预加载 Demucs 模型...")
    from demucs.pretrained import get_model
    get_model(DEMUCS_MODEL)  # 预下载/缓存模型
    logger.info("Demucs 可用")
except Exception as e:
    logger.warning(f"Demucs 预加载检查失败: {e}")
```
- 需在文件顶部 `from app.config import` 中加入 `DEMUCS_MODEL`（如果尚未导入）
- 删除 `import torch.hub` 行（不再需要）

### 步骤 3：创建测试音频生成脚本

**脚本位置**：`c:\Users\zhuyu\.trae-cn\work\6a4358cbdfd24a89591453dd\gen_test_audio.py`
**输出位置**：`d:\project\SheetMusicMaker\test_audio\test_c_major.wav`

**音频规格**：
- 音阶：C4(261.63) D4(293.66) E4(329.63) F4(349.23) G4(392.00) A4(440.00) B4(493.88) C5(523.25)
- 每音 1.5 秒，间隔 0.1 秒静音，首尾各 1 秒静音
- 总时长约 14.8 秒
- 采样率 22050Hz，单声道，16-bit PCM WAV
- 谐波结构：基频 + 2~5 次谐波（幅度 1/harmonic），模拟乐器音色
- ADSR 包络：10ms attack, 100ms release

### 步骤 4：生成测试音频并验证

```powershell
python c:\Users\zhuyu\.trae-cn\work\6a4358cbdfd24a89591453dd\gen_test_audio.py
```

**验证**：
```powershell
python -c "
import soundfile as sf
info = sf.info(r'd:\project\SheetMusicMaker\test_audio\test_c_major.wav')
dur = info.frames / info.samplerate
print(f'时长={dur:.2f}s, sr={info.samplerate}, ch={info.channels}')
assert 14 < dur < 16, f'时长异常: {dur}'
print('测试音频验证通过')
"
cd d:\project\SheetMusicMaker\backend
python -c "from app.utils.audio import get_duration; d=get_duration(r'd:\project\SheetMusicMaker\test_audio\test_c_major.wav'); print(f'{d:.2f}s'); assert 14<d<16"
```

### 步骤 5：代码导入验证

```powershell
cd d:\project\SheetMusicMaker\backend
python -c "from app.services.separator import VocalSeparator; print('separator.py OK')"
python -c "from app.services.analyzer import AudioAnalyzer; a=AudioAnalyzer(); assert hasattr(a,'_detect_beats_librosa'); print('analyzer.py OK')"
python -c "from app.utils.audio import get_duration; print('audio.py OK')"
python -c "from app.main import app; print(f'main.py OK, routes={len(app.routes)}')"
```
期望：全部无报错。特别注意 `separator.py` 不再报 `No module named 'demucs.api'`。

### 步骤 6：启动后端

```powershell
cd d:\project\SheetMusicMaker\backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**启动日志期望**：
```
应用启动中...
预加载 basic-pitch 模型...
basic-pitch 模型加载完成
预加载 Demucs 模型...
Demucs 可用
应用启动完成
```
- 不应出现 "Demucs 预加载检查失败" 警告

### 步骤 7：API 接口验证

在另一终端执行：
```powershell
curl http://localhost:8000/api/health
# 期望: {"status":"ok","models_loaded":true}

curl http://localhost:8000/api/instruments
# 期望: 4 个乐器的 JSON 列表

curl -o NUL -s -w "%{http_code}" http://localhost:8000/
# 期望: 200（前端 index.html）

curl -o NUL -s -w "%{http_code}" http://localhost:8000/soundfont/flute-mp3/C4.mp3
# 期望: 200
```

### 步骤 8：命令行端到端测试

**上传**：
```powershell
$resp = curl -s -X POST http://localhost:8000/api/transcribe -F "file=@d:\project\SheetMusicMaker\test_audio\test_c_major.wav" -F "instrument=flute"
$taskId = ($resp | ConvertFrom-Json).task_id
Write-Output "Task ID: $taskId"
```

**轮询**：
```powershell
while ($true) {
    $task = (curl -s http://localhost:8000/api/tasks/$taskId | ConvertFrom-Json)
    Write-Output "[$(Get-Date -Format 'HH:mm:ss')] status=$($task.status) stage=$($task.stage) progress=$($task.progress)%"
    if ($task.status -eq "completed" -or $task.status -eq "failed") { break }
    Start-Sleep -Seconds 3
}
$task | ConvertTo-Json -Depth 5
```

**期望轮询过程**：
- `validating` 0-5% — 快速通过
- `separating` 5-35% — Demucs CPU 处理约 30-120 秒（15s 音频）
- `transcribing` 35-55% — basic-pitch 约 10-30 秒
- `analyzing` 55-70% — librosa 节拍/调性约 5-15 秒
- `quantizing` 70-85% — music21 量化，快速
- `exporting` 85-100% — 导出，快速
- `completed` — 最终结果

**结果验证**：
- `status` = "completed"
- `result.key.tonic` = "C"，`result.key.mode` = "major"（C 大调音阶）
- `result.bpm` 在 60-200 范围内
- `result.time_signature` = "4/4"
- `result.abc` 以 "X:1" 开头，包含 "K:C"
- `result.downloads` 含 abc/musicxml/midi 三个链接
- `result` 中不包含 `_file_paths` 字段（安全过滤生效）

**下载验证**：
```powershell
curl -s -o "$env:TEMP\test.abc" http://localhost:8000/api/tasks/$taskId/download/abc
curl -s -o "$env:TEMP\test.xml" http://localhost:8000/api/tasks/$taskId/download/musicxml
curl -s -o "$env:TEMP\test.mid" http://localhost:8000/api/tasks/$taskId/download/midi
Get-Content "$env:TEMP\test.abc" | Select-Object -First 6   # ABC 头部
Get-Content "$env:TEMP\test.xml" | Select-Object -First 1   # <?xml ...
(Get-Item "$env:TEMP\test.mid").Length                        # > 0
```

**清理**：
```powershell
curl -s -X DELETE http://localhost:8000/api/tasks/$taskId
```

### 步骤 9：四乐器验证

对同一测试音频分别用 4 种乐器执行命令行 E2E：

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

**期望**：4 个全部 completed，调性均为 C major，ABC 非空。

### 步骤 10：浏览器端到端测试

访问 `http://localhost:8000/`，验证：
1. 标题"乐谱制作器"显示，健康徽章"模型就绪"
2. 上传 `test_c_major.wav`，选择长笛，点击"开始转录"
3. ProgressTracker 阶段依次推进
4. 结果态：五线谱渲染、元数据栏（调性/速度/拍号/乐器）、播放控件、下载按钮
5. 点击 Play → 能听到 C 大调音阶（flute-mp3 音色）
6. 三格式下载成功
7. "重新开始"回到上传态

### 步骤 11：（可选）安装完整 ffmpeg 用于 M4A 支持

当前 soundfile 支持 WAV/FLAC/MP3，M4A 需完整 ffmpeg。

**操作**：
```powershell
$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$zip = "$env:TEMP\ffmpeg-essentials.zip"
Invoke-WebRequest -Uri $url -OutFile $zip
New-Item -ItemType Directory -Force -Path "C:\ffmpeg" | Out-Null
Expand-Archive -Path $zip -DestinationPath "C:\ffmpeg" -Force
$ffmpegBin = (Get-ChildItem "C:\ffmpeg\*\bin" -Directory | Select-Object -First 1).FullName
# 后续每个运行 uvicorn 的终端先执行: $env:PATH = "$ffmpegBin;$env:PATH"
```

**验证**：
```powershell
$env:PATH = "$ffmpegBin;$env:PATH"
ffmpeg -version | Select-Object -First 2
ffmpeg -hide_banner -demuxers 2>&1 | Select-String "mp3|wav|flac|mov"
```

### 步骤 12：（可选）LAN 部署测试

1. 确认 uvicorn 以 `--host 0.0.0.0` 启动
2. 查看本机 IP：`ipconfig | Select-String "IPv4"`
3. 从 LAN 另一设备访问 `http://<本机IP>:8000/`
4. 验证页面加载、上传、轮询、渲染、播放、下载
5. 如防火墙阻止：`netsh advfirewall firewall add rule name="SheetMusicMaker" dir=in action=allow protocol=TCP localport=8000`

---

## 假设与决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| separator 音频加载 | soundfile 优先，torchaudio 回退 | soundfile 已验证可读 WAV/FLAC/MP3；torchaudio 回退处理 M4A |
| separator 重采样 | librosa.resample(axis=-1) | librosa 已安装，支持多维数组按轴重采样 |
| apply_model num_workers | 0（忽略配置中的 4） | Windows multiprocessing spawn 模式有风险；CPU 串行处理 15s 音频可接受 |
| ffmpeg 安装 | 标记为可选步骤 11 | WAV 测试无需 ffmpeg；M4A 为生产需求，非验证阻塞项 |
| 测试音频采样率 | 22050Hz | 匹配原计划设计；同时验证 separator 重采样路径 |

---

## 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Demucs CPU 处理 15s 音频较慢（60-120s） | 高 | 低 | 预下载模型已完成；耐心等待轮询 |
| Demucs 将合成音归入 "other" 而非 "vocals" | 中 | 高 | 谐波丰富波形已设计；若失败可跳过分离直接用原始音频测试下游 |
| basic-pitch 未检测到音符 | 中 | 高 | 调低 onset_threshold/frame_threshold；确保测试音频足够响亮 |
| apply_model 在 Windows 上报错 | 低 | 高 | num_workers=0 避免 multiprocessing；split=True 分段处理 |
| librosa 调性检测偏差 | 低 | 低 | C 大调音阶是最明确的调性，Krumhansl 算法应正确识别 |
| music21 MusicXML/MIDI 导出路径问题 | 低 | 中 | 导出目录已 mkdir；title 使用 Path.stem |

---

## 验证清单

1. `separator.py` 重写后导入正常，不含 `demucs.api`
2. `main.py` lifespan 不再输出 "Demucs 预加载检查失败"
3. 测试音频 15s WAV 生成成功，soundfile 可读取
4. 四个模块导入验证全部通过
5. 后端启动，`/api/health` 返回 `models_loaded: true`
6. `/api/instruments` 返回 4 个乐器
7. `/` 返回 200（前端页面）
8. `/soundfont/flute-mp3/C4.mp3` 返回 200
9. 命令行 E2E：上传→轮询→completed
10. 结果中调性为 C major，BPM 合理，ABC 含 "K:C"
11. 结果中不含 `_file_paths`（安全过滤）
12. 三格式下载文件非空
13. 四乐器全部 completed
14. 浏览器 E2E：五线谱渲染、播放出声、下载正常
