# 乐谱制作器 — EXE 打包计划

## 摘要

将"乐谱制作器"从命令行启动方式改为双击 EXE 运行。使用 PyInstaller onedir 模式打包，包含 FastAPI 后端 + Vue 前端静态文件 + soundfont 音色库 + Demucs/basic-pitch 模型。打包后用户双击 `乐谱制作器.exe` 即可自动启动服务并打开浏览器。

计划分三个阶段：**A. 代码修复**（适配 demucs 4.0.1 API + 路径兼容）→ **B. 功能验证**（确保 E2E 跑通）→ **C. EXE 打包**。

---

## 当前状态分析

### 已就绪
- **Python 环境**：torch 2.12.1+cpu (476MB), demucs 4.0.1, soundfile 0.14.0, librosa 0.11.0, music21 9.9.2, basic-pitch (ONNX), onnxruntime 1.20.1 — 全部安装正常
- **demucs 模型**：htdemucs 已缓存于 `C:\Users\zhuyu\.cache\torch\hub\checkpoints\955717e8-8726e21a.th`（80.2MB）
- **basic-pitch 模型**：`basic_pitch/saved_models/icassp_2022/nmp.onnx`（0.2MB，随包安装）
- **代码修复**：`audio.py`（soundfile 优先）、`analyzer.py`（librosa 降级）、`transcribe.py`（安全文件名 + 响应过滤）已完成
- **前端构建**：`backend/app/static/` 含 `index.html` + `assets/`
- **音色库**：`soundfont/` 下 5 目录，各 46 个 MP3

### 阻塞项
1. **`separator.py`**：使用 `demucs.api.Separator`（4.0.1 不存在），需改用 `get_model()` + `apply_model()`
2. **`main.py` 第 41 行**：`import demucs.api` 触发 ImportError
3. **`config.py`**：路径硬编码为开发环境结构，打包后需适配 `_MEIPASS`
4. **无启动入口**：当前依赖 `uvicorn` 命令行启动，需创建 `run.py` 入口
5. **测试音频**：生成脚本不存在

### 打包体积预估
| 组件 | 大小 |
|------|------|
| torch CPU | ~476MB |
| scipy | ~111MB |
| music21 | ~85MB |
| onnxruntime | ~34MB |
| numpy | ~29MB |
| 其他依赖 (fastapi/uvicorn/librosa/demucs/soundfile 等) | ~50MB |
| soundfont MP3 (230个) | ~50MB |
| Demucs 模型 | ~80MB |
| 前端静态文件 | ~5MB |
| **总计** | **~920MB** |

---

## 实施步骤

### 阶段 A：代码修复

#### 步骤 1：重写 `separator.py` — 适配 demucs 4.0.1 底层 API

**文件**：`backend/app/services/separator.py`

**改动**：完全重写 `VocalSeparator`，保留 `__init__()` 和 `separate(audio_path, progress_callback)` 接口不变。

- `__init__`：`from demucs.pretrained import get_model` → `self.model = get_model('htdemucs')` → `self.model.to('cpu')` → `self.sample_rate = self.model.samplerate`（44100） → `self.sources = self.model.sources`
- 新增 `_load_audio(path)`：soundfile.read 优先（WAV/FLAC/MP3），torchaudio.load 回退（M4A）；采样率 ≠ 44100 时用 librosa.resample；单声道转立体声；返回 `torch.Tensor [channels, samples]`
- `separate()`：归一化 → `apply_model(model, wav[None], shifts=1, split=True, overlap=0.25, device='cpu', num_workers=0, segment=7)` → 反归一化 → 提取 vocals → soundfile.write 保存 WAV
- **关键**：`num_workers=0` 避免 Windows multiprocessing spawn 问题

#### 步骤 2：修复 `main.py` — 替换 demucs.api 引用

**文件**：`backend/app/main.py`

**改动**：
- lifespan 中 `import demucs.api` 替换为 `from demucs.pretrained import get_model; get_model(DEMUCS_MODEL)`
- 删除 `import torch.hub`
- 顶部 import 加入 `DEMUCS_MODEL`
- static 目录引用改为从 config 导入（为打包路径适配做准备）

#### 步骤 3：修改 `config.py` — 支持 PyInstaller 打包路径

**文件**：`backend/app/config.py`

**改动**：增加 `frozen` 检测，区分打包/开发环境：
```python
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    # PyInstaller onedir 环境
    _BUNDLE_DIR = Path(sys._MEIPASS)        # 打包资源目录（只读）
    APP_DIR = Path(sys.executable).parent    # EXE 所在目录（可写）
    SOUNDFONT_DIR = _BUNDLE_DIR / "soundfont"
    STATIC_DIR = _BUNDLE_DIR / "app" / "static"
else:
    # 开发环境
    APP_DIR = Path(__file__).resolve().parent.parent.parent
    SOUNDFONT_DIR = APP_DIR / "soundfont"
    STATIC_DIR = Path(__file__).resolve().parent / "static"

STORAGE_DIR = APP_DIR / "storage"            # 运行时可写目录
UPLOADS_DIR = STORAGE_DIR / "uploads"
TEMP_DIR = STORAGE_DIR / "temp"
OUTPUTS_DIR = STORAGE_DIR / "outputs"
```

- `main.py` 中 `static_dir = Path(__file__).parent / "static"` 改为 `from app.config import STATIC_DIR`

### 阶段 B：功能验证

#### 步骤 4：创建测试音频生成脚本

**脚本**：`c:\Users\zhuyu\.trae-cn\work\6a4358cbdfd24a89591453dd\gen_test_audio.py`
**输出**：`d:\project\SheetMusicMaker\test_audio\test_c_major.wav`

C 大调音阶 C4-C5，8 音 × 1.5s，22050Hz，谐波 1-5，ADSR 包络，总时长约 14.8s。

#### 步骤 5：代码导入验证

```powershell
cd d:\project\SheetMusicMaker\backend
python -c "from app.services.separator import VocalSeparator; print('separator OK')"
python -c "from app.main import app; print(f'main OK, routes={len(app.routes)}')"
```

#### 步骤 6：启动后端 + API 验证 + E2E 测试

- 启动：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 验证 `/api/health`、`/api/instruments`、`/`、`/soundfont/flute-mp3/C4.mp3`
- 上传 `test_c_major.wav` → 轮询 → 验证 completed → 下载 ABC/MusicXML/MIDI
- 期望：调性 C major，BPM 合理，ABC 含 "K:C"

### 阶段 C：EXE 打包

#### 步骤 7：创建 `run.py` 启动入口

**文件**：`d:\project\SheetMusicMaker\backend\run.py`

**职责**：
1. `multiprocessing.freeze_support()` 在最前（Windows 打包必需）
2. 设置 `TORCH_HOME` 环境变量指向打包内的模型缓存目录
3. 延迟 2 秒后自动打开浏览器 `http://localhost:8000`
4. 启动 uvicorn（`reload=False`，`host=0.0.0.0`，`port=8000`）
5. 控制台显示访问地址和关闭提示

```python
"""乐谱制作器 EXE 启动入口。"""
import multiprocessing
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def main():
    multiprocessing.freeze_support()

    # PyInstaller 打包环境：设置模型缓存路径
    if getattr(sys, 'frozen', False):
        bundle_dir = Path(sys._MEIPASS)
        os.environ['TORCH_HOME'] = str(bundle_dir / 'torch_hub')
        # 确保运行时存储目录存在
        app_dir = Path(sys.executable).parent
        storage = app_dir / 'storage'
        (storage / 'uploads').mkdir(parents=True, exist_ok=True)
        (storage / 'temp').mkdir(parents=True, exist_ok=True)
        (storage / 'outputs').mkdir(parents=True, exist_ok=True)

    # 延迟打开浏览器
    def _open_browser():
        time.sleep(3)
        webbrowser.open('http://localhost:8000')
    threading.Thread(target=_open_browser, daemon=True).start()

    print('=' * 50)
    print('  乐谱制作器 正在启动...')
    print('  访问地址: http://localhost:8000')
    print('  按 Ctrl+C 关闭服务')
    print('=' * 50)

    import uvicorn
    uvicorn.run(
        'app.main:app',
        host='0.0.0.0',
        port=8000,
        reload=False,
        log_level='info',
    )


if __name__ == '__main__':
    main()
```

#### 步骤 8：安装 PyInstaller

```powershell
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 步骤 9：创建 PyInstaller spec 文件

**文件**：`d:\project\SheetMusicMaker\backend\sheet_music_maker.spec`

**关键配置**：
- **入口**：`run.py`
- **模式**：onedir（`COLLECT` 段，不用 `--onefile`）
- **collect_all**：自动收集 torch、torchaudio、demucs、onnxruntime、librosa、soundfile、basic_pitch 的全部子模块/数据/二进制
- **datas**：
  - `('app/static', 'app/static')` — 前端构建产物
  - `('soundfont', 'soundfont')` — 音色库（相对于 backend 的上级目录）
  - Demucs 模型：`(C:\Users\zhuyu\.cache\torch\hub\checkpoints, 'torch_hub/hub/checkpoints')`
- **hiddenimports**：uvicorn 子模块（protocols.http.auto, protocols.websockets.auto, lifespan.on）、demucs.pretrained、demucs.apply、basic_pitch.inference、multipart
- **excludes**：tkinter, matplotlib, pytest, pip, IPython（减小体积）
- **console**：True（显示运行日志）
- **name**：`乐谱制作器`
- **pathex**：`['d:\\project\\SheetMusicMaker\\backend']`

```python
# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# 自动收集大型库的全部依赖
torch_d, torch_b, torch_h = collect_all('torch')
ta_d, ta_b, ta_h = collect_all('torchaudio')
demucs_d, demucs_b, demucs_h = collect_all('demucs')
ort_d, ort_b, ort_h = collect_all('onnxruntime')
librosa_d, librosa_b, librosa_h = collect_all('librosa')
sf_d, sf_b, sf_h = collect_all('soundfile')
bp_d, bp_b, bp_h = collect_all('basic_pitch')
m21_d, m21_b, m21_h = collect_all('music21')

# Demucs 模型缓存
torch_hub_path = os.path.expanduser('~/.cache/torch/hub/checkpoints')

a = Analysis(
    ['run.py'],
    pathex=['d:\\project\\SheetMusicMaker\\backend'],
    binaries=(
        torch_b + ta_b + demucs_b + ort_b + librosa_b + sf_b + bp_b + m21_b
    ),
    datas=[
        ('app/static', 'app/static'),
        ('../soundfont', 'soundfont'),
        (torch_hub_path, 'torch_hub/hub/checkpoints'),
    ] + torch_d + ta_d + demucs_d + ort_d + librosa_d + sf_d + bp_d + m21_d,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'multipart',
        'demucs.pretrained',
        'demucs.apply',
        'basic_pitch.inference',
        'app',
        'app.main',
        'app.config',
        'app.routers.transcribe',
        'app.services.separator',
        'app.services.transcriber',
        'app.services.analyzer',
        'app.services.quantizer',
        'app.services.exporter',
        'app.services.pipeline',
        'app.services.instruments',
        'app.tasks.store',
        'app.utils.audio',
    ] + torch_h + ta_h + demucs_h + ort_h + librosa_h + sf_h + bp_h + m21_h,
    hookspath=[],
    excludes=['tkinter', 'matplotlib', 'pytest', 'pip', 'IPython', 'jupyter'],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='乐谱制作器',
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='乐谱制作器',
)
```

#### 步骤 10：执行打包

```powershell
cd d:\project\SheetMusicMaker\backend
pyinstaller sheet_music_maker.spec --clean --noconfirm
```

**输出**：`d:\project\SheetMusicMaker\backend\dist\乐谱制作器\乐谱制作器.exe`

**预期耗时**：5-15 分钟（取决于磁盘速度）
**预期体积**：dist/乐谱制作器/ 目录约 900MB-1.1GB

#### 步骤 11：测试打包后的 EXE

1. 双击 `dist\乐谱制作器\乐谱制作器.exe`
2. 控制台应显示启动日志，3 秒后浏览器自动打开
3. 验证 `/api/health` 返回 `models_loaded: true`
4. 上传测试音频 → 轮询 → 验证 completed
5. 验证乐谱渲染、播放、下载

#### 步骤 12：（可选）压缩分发

```powershell
cd d:\project\SheetMusicMaker\backend\dist
# 使用 7z 压缩（高压缩比）
7z a -t7z -mx=9 乐谱制作器.7z 乐谱制作器/
```

---

## 假设与决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 打包模式 | onedir（文件夹） | torch 476MB，onefile 每次启动需解压到临时目录，启动极慢 |
| 控制台窗口 | 显示（console=True） | 工厂内部使用，方便查看日志和错误 |
| 自动打开浏览器 | 是 | 用户体验：双击即用 |
| 模型打包 | Demucs 模型打包进 EXE | 内网环境无法下载 |
| TORCH_HOME | 指向 _MEIPASS/torch_hub | 让 demucs.get_model 从打包缓存加载 |
| 运行时存储 | EXE 同级 storage/ 目录 | 打包目录只读，storage 需可写 |
| multiprocessing | num_workers=0 + freeze_support | 避免 Windows spawn 模式问题 |
| 体积优化 | excludes 排除 tkinter/matplotlib 等 | 减小约 50MB |

---

## 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| PyInstaller 未正确收集 torch 子模块 | 中 | 高 | 使用 collect_all('torch') 全量收集 |
| demucs 动态导入遗漏 | 中 | 高 | collect_all('demucs') + 显式 hiddenimports |
| 打包后 _MEIPASS 路径找不到资源 | 中 | 高 | config.py frozen 检测 + sys._MEIPASS |
| Demucs 模型缓存路径不匹配 | 中 | 高 | run.py 设置 TORCH_HOME，spec 中放到对应目录 |
| 打包体积过大 (>1GB) | 高 | 低 | excludes 排除无用模块；UPX 压缩；可接受 |
| 打包过程 OOM 或超时 | 低 | 中 | 关闭其他程序；增加 --clean 避免缓存冲突 |
| Windows 防火墙提示 | 中 | 低 | 用户点击允许即可 |
| soundfont 路径在 spec 中相对路径错误 | 中 | 中 | 使用绝对路径或从 backend 上级引用 |

---

## 验证清单

### 阶段 A 验证
1. `separator.py` 导入正常，不含 `demucs.api`
2. `main.py` lifespan 不再报 "Demucs 预加载检查失败"
3. `config.py` 在开发环境下路径不变，frozen 环境下指向正确

### 阶段 B 验证
4. 测试音频 15s WAV 生成成功
5. 后端启动，`/api/health` 返回 `models_loaded: true`
6. 命令行 E2E：上传→轮询→completed，调性 C major，ABC 含 "K:C"
7. 三格式下载文件非空

### 阶段 C 验证
8. PyInstaller 打包成功，生成 `dist\乐谱制作器\乐谱制作器.exe`
9. 双击 EXE，控制台显示启动日志，浏览器自动打开
10. EXE 模式下 `/api/health` 返回 `models_loaded: true`
11. EXE 模式下上传→轮询→completed
12. EXE 模式下乐谱渲染、播放、下载正常
13. EXE 同级生成 `storage/` 目录（可写）
