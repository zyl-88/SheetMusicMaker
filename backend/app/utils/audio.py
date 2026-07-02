"""音频文件校验：格式、大小、时长。"""
import json
import logging
import subprocess
from pathlib import Path

from app.config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES, MAX_DURATION_SEC

logger = logging.getLogger("sheet_music_maker.audio")


def validate_extension(filename: str) -> str:
    """校验文件扩展名，返回小写扩展名（含点）。"""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件格式: {ext}，支持的格式: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return ext


def validate_size(file_size: int) -> None:
    """校验文件大小。"""
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"文件过大: {file_size / 1024 / 1024:.1f}MB，"
            f"最大支持 {MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f}MB"
        )


def get_duration(file_path: str | Path) -> float:
    """获取音频时长（秒）。

    优先使用 soundfile（libsndfile）读取 WAV/FLAC/MP3，
    失败时回退到 ffprobe（完整版 ffmpeg）处理 M4A 等格式。
    """
    file_path = str(file_path)

    # 方案 1: soundfile — 支持 WAV/FLAC，libsndfile>=1.1.0 支持 MP3
    try:
        import soundfile as sf

        info = sf.info(file_path)
        duration = info.frames / info.samplerate
        logger.debug(f"soundfile 读取时长成功: {file_path} -> {duration:.2f}s")
        return duration
    except Exception as e:
        logger.debug(f"soundfile 无法读取 {file_path}: {e}，尝试 ffprobe")

    # 方案 2: ffprobe（完整版 ffmpeg）— 支持 M4A 等其他格式
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                file_path,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        logger.debug(f"ffprobe 读取时长成功: {file_path} -> {duration:.2f}s")
        return duration
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"无法读取音频时长: {e}") from e
    except FileNotFoundError:
        raise RuntimeError(
            "无法读取音频时长：soundfile 不支持该格式，"
            "且 ffprobe 未安装或不在 PATH 中。"
            "请安装完整版 ffmpeg (https://www.gyan.dev/ffmpeg/builds/)"
        )


def validate_duration(file_path: str | Path) -> float:
    """校验音频时长，返回时长（秒）。"""
    duration = get_duration(file_path)
    if duration > MAX_DURATION_SEC:
        raise ValueError(
            f"音频过长: {duration:.0f}秒 ({duration / 60:.1f}分钟)，"
            f"最大支持 {MAX_DURATION_SEC}秒 ({MAX_DURATION_SEC / 60:.0f}分钟)"
        )
    return duration


def validate_audio_file(file_path: str | Path, filename: str, file_size: int) -> tuple[str, float]:
    """完整校验音频文件，返回 (扩展名, 时长秒)。"""
    ext = validate_extension(filename)
    validate_size(file_size)
    duration = validate_duration(file_path)
    return ext, duration
