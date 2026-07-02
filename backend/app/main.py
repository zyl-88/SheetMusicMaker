"""FastAPI 应用入口。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import DEMUCS_MODEL, SOUNDFONT_DIR, STATIC_DIR
from app.routers import transcribe
from app.services.instruments import list_instruments

logger = logging.getLogger("sheet_music_maker")

# 全局标志：模型是否已加载
models_loaded = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时预加载模型。"""
    global models_loaded
    logger.info("应用启动中...")
    try:
        # 预加载 basic-pitch 模型
        from basic_pitch.inference import Model
        from basic_pitch import ICASSP_2022_MODEL_PATH
        logger.info("预加载 basic-pitch 模型...")
        Model(ICASSP_2022_MODEL_PATH)
        logger.info("basic-pitch 模型加载完成")
    except Exception as e:
        logger.warning(f"basic-pitch 模型预加载失败（将在首次使用时加载）: {e}")

    try:
        # 预加载 Demucs 模型（从缓存加载）
        logger.info("预加载 Demucs 模型...")
        from demucs.pretrained import get_model
        get_model(DEMUCS_MODEL)
        logger.info("Demucs 可用")
    except Exception as e:
        logger.warning(f"Demucs 预加载检查失败: {e}")

    models_loaded = True
    logger.info("应用启动完成")
    yield
    logger.info("应用关闭")


app = FastAPI(
    title="乐谱制作器",
    description="上传音频文件，自动生成演奏用乐谱",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(transcribe.router, prefix="/api", tags=["transcribe"])


@app.get("/api/health")
async def health():
    """健康检查 + 模型就绪状态。"""
    return {"status": "ok", "models_loaded": models_loaded}


@app.get("/api/instruments")
async def get_instruments():
    """获取支持的乐器列表。"""
    return {"instruments": list_instruments()}


# 挂载 soundfont 静态文件（abcjs 播放需要）
if SOUNDFONT_DIR.exists():
    app.mount("/soundfont", StaticFiles(directory=str(SOUNDFONT_DIR)), name="soundfont")

# 挂载前端构建产物（生产模式）
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
