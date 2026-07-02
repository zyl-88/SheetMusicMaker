"""乐谱制作器 EXE 启动入口。

双击运行后自动启动 FastAPI 服务并打开浏览器。
"""
import multiprocessing
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def main():
    # Windows 打包必需：支持 multiprocessing 子进程
    multiprocessing.freeze_support()

    # PyInstaller 打包环境：设置模型缓存路径
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys._MEIPASS)
        # 让 Demucs/torch 从打包缓存加载模型（而非尝试下载）
        os.environ["TORCH_HOME"] = str(bundle_dir / "torch_hub")

        # 确保运行时存储目录存在（EXE 同级，可写）
        app_dir = Path(sys.executable).parent
        storage = app_dir / "storage"
        (storage / "uploads").mkdir(parents=True, exist_ok=True)
        (storage / "temp").mkdir(parents=True, exist_ok=True)
        (storage / "outputs").mkdir(parents=True, exist_ok=True)

    # 延迟打开浏览器（等服务启动后）
    def _open_browser():
        time.sleep(3)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=_open_browser, daemon=True).start()

    print("=" * 50)
    print("  乐谱制作器 正在启动...")
    print("  访问地址: http://localhost:8000")
    print("  按 Ctrl+C 关闭服务")
    print("=" * 50)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
