"""basic-pitch 自动音乐转录服务。

使用 Spotify basic-pitch 将音频转为音符事件。
支持按乐器音域过滤频率范围。
"""
import logging
from pathlib import Path
from typing import Any

from app.config import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_NOTE_LENGTH,
    BASIC_PITCH_ONSET_THRESHOLD,
)

logger = logging.getLogger("sheet_music_maker.transcriber")


class NoteTranscriber:
    """basic-pitch 音符转录器。"""

    def __init__(self) -> None:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model

        logger.info("加载 basic-pitch 模型...")
        self.model = Model(ICASSP_2022_MODEL_PATH)
        logger.info("basic-pitch 模型加载完成")

    def transcribe(
        self,
        vocals_wav_path: str | Path,
        instrument_config: dict[str, Any],
    ) -> list[tuple]:
        """转录音频为音符事件。

        Args:
            vocals_wav_path: 人声 WAV 文件路径
            instrument_config: 乐器配置（含 min_freq/max_freq）

        Returns:
            note_events: List[(start_time_s, end_time_s, midi_pitch, amplitude, pitch_bend)]
        """
        from basic_pitch.inference import predict

        logger.info(f"开始转录: {vocals_wav_path}")
        logger.info(
            f"频率范围过滤: {instrument_config['min_freq']:.1f}Hz - "
            f"{instrument_config['max_freq']:.1f}Hz"
        )

        model_output, midi_data, note_events = predict(
            str(vocals_wav_path),
            self.model,
            onset_threshold=BASIC_PITCH_ONSET_THRESHOLD,
            frame_threshold=BASIC_PITCH_FRAME_THRESHOLD,
            minimum_note_length=BASIC_PITCH_MIN_NOTE_LENGTH,
            minimum_frequency=instrument_config["min_freq"],
            maximum_frequency=instrument_config["max_freq"],
            multiple_pitch_bends=False,
            melodia_trick=True,
        )

        logger.info(f"转录完成: {len(note_events)} 个音符")

        if not note_events:
            logger.warning("未检测到任何音符")

        return note_events
