# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec 文件 — 乐谱制作器。

打包命令: pyinstaller sheet_music_maker.spec --clean --noconfirm
输出: dist/乐谱制作器/乐谱制作器.exe
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# ============================================================
# 自动收集大型库的全部依赖（子模块 + 数据文件 + 二进制）
# ============================================================
datas = []
binaries = []
hiddenimports = []

for pkg in [
    "torch",
    "torchaudio",
    "demucs",
    "onnxruntime",
    "librosa",
    "soundfile",
    "basic_pitch",
    "music21",
    "uvicorn",
    "fastapi",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# ============================================================
# 额外 hiddenimports（PyInstaller 静态分析可能遗漏的动态导入）
# ============================================================
extra_hidden = [
    # uvicorn 子模块
    "uvicorn.logging",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # multipart（FastAPI 文件上传）
    "multipart",
    "multipart.python_multipart",
    # resampy（basic-pitch → librosa 依赖）在导入时 require pkg_resources；
    # setuptools>=81 移除了它，构建环境固定 setuptools==80.9.0 时必须显式打包，
    # 否则音频一进转录就报 No module named 'pkg_resources'
    "pkg_resources",
    # pkg_resources → jaraco.context 在 Python<3.12 上 `from backports import tarfile`，
    # 缺这一项会在启动时被 pyi_rth_pkgres 直接打断
    "backports",
    "backports.tarfile",
    # demucs 底层 API
    "demucs.pretrained",
    "demucs.apply",
    "demucs.hdemucs",
    "demucs.demucs",
    # basic-pitch
    "basic_pitch.inference",
    # 项目模块
    "app",
    "app.main",
    "app.config",
    "app.routers",
    "app.routers.transcribe",
    "app.services.separator",
    "app.services.transcriber",
    "app.services.analyzer",
    "app.services.quantizer",
    "app.services.exporter",
    "app.services.pipeline",
    "app.services.instruments",
    "app.tasks.store",
    "app.utils.audio",
]
hiddenimports += extra_hidden

# ============================================================
# 数据文件
# ============================================================
# 前端构建产物
datas += [("app/static", "app/static")]

# 音色库（在 backend 的上级目录，即项目根目录下）
soundfont_src = os.path.join(os.path.dirname(os.path.abspath(SPECPATH)), "soundfont")
datas += [(soundfont_src, "soundfont")]

# Demucs 模型缓存（torch hub）
torch_hub_path = os.path.expanduser("~/.cache/torch/hub/checkpoints")
if os.path.isdir(torch_hub_path):
    datas += [(torch_hub_path, "torch_hub/hub/checkpoints")]
else:
    print("WARNING: torch hub checkpoints 目录不存在，模型可能需要运行时下载")

# ============================================================
# Analysis
# ============================================================
a = Analysis(
    ["run.py"],
    pathex=[os.path.dirname(os.path.abspath(SPECPATH))],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "pip",
        "IPython",
        "jupyter",
        "notebook",
        "jupyterlab",
    ],
    noarchive=False,
    # numba 的 cache=True 装饰器需要能定位到真实的 .py 源文件；librosa 只收进
    # PYZ 压缩包时没有 locator（"pyz+py" 仍优先从 PYZ 导入），运行时报
    # RuntimeError: cannot cache function '__o_fold'。只以源码形式落到
    # _internal 目录，导入时 __file__ 才是真实路径。
    module_collection_mode={"librosa": "py"},
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="乐谱制作器",
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
    name="乐谱制作器",
)
