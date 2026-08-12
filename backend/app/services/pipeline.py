"""全流程编排：串联 6 阶段，进度上报至 TaskStore。

阶段：validating → separating → transcribing → analyzing → quantizing → exporting
进度占比：0-5% → 5-35% → 35-55% → 55-70% → 70-85% → 85-100%
"""
import logging
import os
from pathlib import Path
from typing import Any

from app.config import (
    DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD,
    OUTPUTS_DIR,
    TEMP_DIR,
    UPLOADS_DIR,
)
from app.services.instruments import get_instrument
from app.tasks.store import (
    STAGE_ANALYZING,
    STAGE_EXPORTING,
    STAGE_QUANTIZING,
    STAGE_SEPARATING,
    STAGE_TRANSCRIBING,
    STAGE_VALIDATING,
    task_store,
)

logger = logging.getLogger("sheet_music_maker.pipeline")

# 进度区间
PROGRESS_RANGES = {
    STAGE_VALIDATING: (0, 5),
    STAGE_SEPARATING: (5, 35),
    STAGE_TRANSCRIBING: (35, 55),
    STAGE_ANALYZING: (55, 70),
    STAGE_QUANTIZING: (70, 85),
    STAGE_EXPORTING: (85, 100),
}

# 服务单例（懒加载）
_separator = None
_transcriber = None
_analyzer = None
_quantizer = None
_exporter = None


def _get_separator():
    global _separator
    if _separator is None:
        from app.services.separator import VocalSeparator
        _separator = VocalSeparator()
    return _separator


def _get_transcriber():
    global _transcriber
    if _transcriber is None:
        from app.services.transcriber import NoteTranscriber
        _transcriber = NoteTranscriber()
    return _transcriber


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from app.services.analyzer import AudioAnalyzer
        _analyzer = AudioAnalyzer()
    return _analyzer


def _get_quantizer():
    global _quantizer
    if _quantizer is None:
        from app.services.quantizer import NoteQuantizer
        _quantizer = NoteQuantizer()
    return _quantizer


def _get_exporter():
    global _exporter
    if _exporter is None:
        from app.services.exporter import ScoreExporter
        _exporter = ScoreExporter()
    return _exporter


def run_pipeline(
    task_id: str,
    audio_path: str,
    instrument: str,
    audio_filename: str,
) -> dict[str, Any]:
    """执行完整的转录流水线。

    Args:
        task_id: 任务 ID
        audio_path: 上传的音频文件路径
        instrument: 乐器 ID
        audio_filename: 原始文件名

    Returns:
        结果字典 {abc, key, bpm, time_signature, instrument_name, downloads}
    """
    logger.info(f"[{task_id}] 开始流水线: {audio_filename}, 乐器={instrument}")

    try:
        # 获取乐器配置
        instrument_config = get_instrument(instrument)
        instrument_name = instrument_config["name"]

        # 任务目录
        task_temp = TEMP_DIR / task_id
        task_output = OUTPUTS_DIR / task_id
        task_temp.mkdir(parents=True, exist_ok=True)
        task_output.mkdir(parents=True, exist_ok=True)

        title = Path(audio_filename).stem

        # === 阶段 1: 校验 ===
        _update_progress(task_id, STAGE_VALIDATING, 3)
        from app.utils.audio import validate_audio_file

        ext, duration = validate_audio_file(audio_path, audio_filename, os.path.getsize(audio_path))
        logger.info(f"[{task_id}] 校验通过: {ext}, {duration:.1f}s")
        _update_progress(task_id, STAGE_VALIDATING, 5)

        # === 阶段 2: 人声分离 ===
        _update_progress(task_id, STAGE_SEPARATING, 6)

        def separation_callback(info: dict) -> None:
            state = info.get("state", "")
            if state == "end":
                _update_progress(task_id, STAGE_SEPARATING, min(34, 6 + 28 * 0.5))

        # 复制音频到任务临时目录
        import shutil

        task_audio = task_temp / f"input{ext}"
        shutil.copy2(audio_path, task_audio)

        separator = _get_separator()
        vocals_path = separator.separate(task_audio, progress_callback=separation_callback)
        _update_progress(task_id, STAGE_SEPARATING, 35)

        # 智能源选择：检测 vocals 能量比，过低则回退到原始音频
        # （钢琴/器乐独奏会被 Demucs 归入 "other" 而非 "vocals"）
        transcribe_source = vocals_path
        use_instrumental_mode = False
        try:
            import numpy as np
            import soundfile as sf

            orig_data, orig_sr = sf.read(str(task_audio), dtype="float32", always_2d=True)
            voc_data, voc_sr = sf.read(vocals_path, dtype="float32", always_2d=True)
            orig_rms = float(np.sqrt(np.mean(orig_data ** 2)))
            voc_rms = float(np.sqrt(np.mean(voc_data ** 2)))
            ratio = voc_rms / orig_rms if orig_rms > 0 else 0.0
            logger.info(f"[{task_id}] vocals/原始 能量比: {ratio:.2%}")

            if ratio < DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD:
                logger.warning(
                    f"[{task_id}] vocals 能量比 {ratio:.2%} 低于阈值 "
                    f"{DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD:.0%}，"
                    f"判定为非人声音乐，使用原始音频转录"
                )
                transcribe_source = str(task_audio)
                use_instrumental_mode = True
            else:
                logger.info(f"[{task_id}] vocals 能量比正常，使用分离后 vocals 轨")
        except Exception as e:
            logger.warning(f"[{task_id}] 能量比检测失败: {e}，使用 vocals 轨")
        logger.info(f"[{task_id}] 人声分离完成")

        # === 阶段 3: 转录 ===
        _update_progress(task_id, STAGE_TRANSCRIBING, 36)
        transcriber = _get_transcriber()

        # 智能引擎选择：
        # - 器乐（vocals 能量比低）→ Melodia 引擎（专为多声部旋律提取）
        # - universal 模式 → Melodia 引擎
        # - 有人声 → basic-pitch 引擎
        if use_instrumental_mode or instrument == "universal":
            logger.info(
                f"[{task_id}] 使用 Melodia 旋律提取引擎 "
                f"(器乐={use_instrumental_mode}, 模式={instrument})"
            )
            note_events = transcriber.transcribe_melody(
                transcribe_source, instrument_config
            )
        else:
            note_events = transcriber.transcribe(
                transcribe_source, instrument_config
            )
        _update_progress(task_id, STAGE_TRANSCRIBING, 55)
        logger.info(f"[{task_id}] 转录完成: {len(note_events)} 个音符")

        # === 阶段 4: 分析 ===
        _update_progress(task_id, STAGE_ANALYZING, 56)
        analyzer = _get_analyzer()
        analysis = analyzer.analyze(transcribe_source)
        _update_progress(task_id, STAGE_ANALYZING, 70)
        logger.info(f"[{task_id}] 分析完成: BPM={analysis['bpm']}, Key={analysis['key']}")

        # === 阶段 5: 量化 ===
        _update_progress(task_id, STAGE_QUANTIZING, 71)
        quantizer = _get_quantizer()
        m21_stream = quantizer.quantize(note_events, analysis, instrument_config)
        _update_progress(task_id, STAGE_QUANTIZING, 85)
        logger.info(f"[{task_id}] 量化完成")

        # === 阶段 6: 导出 ===
        _update_progress(task_id, STAGE_EXPORTING, 86)
        exporter = _get_exporter()
        export_result = exporter.export_all(m21_stream, analysis, title, task_output)
        _update_progress(task_id, STAGE_EXPORTING, 100)
        logger.info(f"[{task_id}] 导出完成")

        # 清理临时文件
        _cleanup_temp(task_temp)

        # 构建结果
        result = {
            "abc": export_result["abc"],
            "key": analysis["key"],
            "bpm": analysis["bpm"],
            "time_signature": analysis["time_signature"],
            "instrument_name": instrument_name,
            "downloads": {
                "abc": f"/api/tasks/{task_id}/download/abc",
                "musicxml": f"/api/tasks/{task_id}/download/musicxml",
                "midi": f"/api/tasks/{task_id}/download/midi",
            },
            "_file_paths": {
                "musicxml": export_result["musicxml_path"],
                "midi": export_result["midi_path"],
            },
        }

        # 保存 ABC 文件
        abc_path = task_output / f"{title}.abc"
        abc_path.write_text(export_result["abc"], encoding="utf-8")
        result["_file_paths"]["abc"] = str(abc_path)

        task_store.complete(task_id, result)
        logger.info(f"[{task_id}] 流水线完成")

        return result

    except Exception as e:
        logger.error(f"[{task_id}] 流水线失败: {e}", exc_info=True)
        task_store.fail(task_id, str(e))
        raise


def _update_progress(task_id: str, stage: str, progress: int) -> None:
    """更新任务进度。"""
    task_store.update(task_id, stage, progress)


def _cleanup_temp(temp_dir: Path) -> None:
    """清理临时文件。"""
    import shutil

    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    except Exception as e:
        logger.warning(f"清理临时文件失败: {e}")


def cleanup_task_files(task_id: str) -> None:
    """清理任务的所有文件（用于 DELETE 端点）。"""
    import shutil

    for d in [TEMP_DIR / task_id, OUTPUTS_DIR / task_id]:
        try:
            if d.exists():
                shutil.rmtree(d)
        except Exception as e:
            logger.warning(f"清理任务文件失败: {d}: {e}")
