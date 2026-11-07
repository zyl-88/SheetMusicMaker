"""转录 API 路由：提交转录任务、轮询状态、获取结果、下载文件、清理任务。"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import MAX_CONCURRENT_TASKS, MAX_FILE_SIZE_BYTES, UPLOADS_DIR
from app.services.instruments import INSTRUMENTS
from app.services.pipeline import cleanup_task_files, run_pipeline
from app.tasks.store import task_store
from app.utils.audio import validate_extension

logger = logging.getLogger("sheet_music_maker.router")

router = APIRouter()

# 单线程执行器：CPU 限制下同时只跑一个 pipeline
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TASKS)


@router.post("/transcribe")
async def submit_transcription(
    file: UploadFile = File(...),
    instrument: str = Form(...),
):
    """提交转录任务。

    接收音频文件和乐器选择，创建后台任务并立即返回 task_id。
    """
    # 校验乐器
    if instrument not in INSTRUMENTS:
        raise HTTPException(status_code=400, detail=f"不支持的乐器: {instrument}")

    # 安全文件名：仅保留文件名部分（防止路径穿越）
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名缺失")
    safe_name = Path(file.filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")

    # 扩展名白名单校验（快速失败，避免无效文件落盘）
    try:
        validate_extension(safe_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 先创建任务，用 task_id 前缀隔离上传文件（避免同名文件互相覆盖）
    task_id = task_store.create(safe_name, instrument)
    upload_path = UPLOADS_DIR / f"{task_id}_{safe_name}"
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    # 流式写入，避免将整个文件一次性读入内存
    with open(upload_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    file_size = os.path.getsize(upload_path)

    # 服务端大小校验（前端已限制，此处为防御）
    if file_size > MAX_FILE_SIZE_BYTES:
        upload_path.unlink(missing_ok=True)
        task_store.delete(task_id)
        raise HTTPException(
            status_code=413,
            detail=f"文件过大: {file_size / 1024 / 1024:.1f}MB，"
            f"最大支持 {MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f}MB",
        )

    logger.info(f"提交转录任务: {task_id}, 文件={safe_name}, 乐器={instrument}")

    # 提交到线程池异步执行
    _executor.submit(
        run_pipeline,
        task_id,
        str(upload_path),
        instrument,
        file.filename,
    )

    return {"task_id": task_id, "status": "pending"}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """轮询任务状态。"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 过滤内部字段，只返回前端需要的字段
    result = task.get("result")
    if result and "_file_paths" in result:
        result = {k: v for k, v in result.items() if k != "_file_paths"}

    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "stage": task["stage"],
        "progress": task["progress"],
        "result": result,
        "error": task["error"],
    }


@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """获取完整结果（ABC 字符串 + 元数据 + 下载链接）。"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"任务未完成，当前状态: {task['status']}",
        )

    result = task.get("result")
    if not result:
        raise HTTPException(status_code=500, detail="结果数据缺失")

    # 返回结果（排除内部字段）
    return {
        "abc": result["abc"],
        "key": result["key"],
        "bpm": result["bpm"],
        "time_signature": result["time_signature"],
        "instrument_name": result["instrument_name"],
        "downloads": result["downloads"],
    }


@router.get("/tasks/{task_id}/download/{format}")
async def download_file(task_id: str, format: str):
    """下载生成的乐谱文件。

    format: abc, musicxml, midi
    """
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"任务未完成，当前状态: {task['status']}",
        )

    result = task.get("result")
    if not result or "_file_paths" not in result:
        raise HTTPException(status_code=500, detail="文件路径缺失")

    file_paths = result["_file_paths"]

    if format not in file_paths:
        raise HTTPException(status_code=400, detail=f"不支持的格式: {format}")

    file_path = file_paths[format]
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 确定文件名和媒体类型
    media_types = {
        "abc": "text/plain",
        "musicxml": "application/xml",
        "midi": "audio/midi",
    }

    filename = Path(file_path).name
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_types.get(format, "application/octet-stream"),
    )


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """清理任务和文件。"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 清理文件
    cleanup_task_files(task_id)

    # 清理上传的原始文件（task_id 前缀命名）
    if task.get("audio_filename"):
        upload_path = UPLOADS_DIR / f"{task_id}_{task['audio_filename']}"
        try:
            if upload_path.exists():
                upload_path.unlink()
        except Exception as e:
            logger.warning(f"清理上传文件失败: {e}")

    # 从存储中删除
    task_store.delete(task_id)
    return {"status": "deleted"}
