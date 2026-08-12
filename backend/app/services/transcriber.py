"""basic-pitch 自动音乐转录服务。

使用 Spotify basic-pitch 将音频转为音符事件。
支持按乐器音域过滤频率范围，并对原始输出进行后处理：
- 过滤次低频噪声音符
- 过滤过短/过弱音符
- 合并同音高连续音符（去重）
"""
import logging
from pathlib import Path
from typing import Any

from app.config import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_NOTE_LENGTH,
    BASIC_PITCH_ONSET_THRESHOLD,
    MERGE_GAP_THRESHOLD,
    MIN_MIDI_PITCH,
    MIN_NOTE_AMPLITUDE,
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

        raw_count = len(note_events)
        logger.info(f"basic-pitch 原始输出: {raw_count} 个音符")

        # 后处理：过滤噪声 + 去重合并
        note_events = self._post_process_notes(note_events)

        if not note_events:
            logger.warning("后处理后未检测到任何音符")

        return note_events

    def _post_process_notes(
        self, note_events: list[tuple]
    ) -> list[tuple]:
        """对 basic-pitch 原始输出进行后处理。

        步骤：
        1. 过滤低于 MIN_MIDI_PITCH 的次低频噪声
        2. 过滤时长过短的音符（< BASIC_PITCH_MIN_NOTE_LENGTH 毫秒）
        3. 过滤振幅过弱的音符（< MIN_NOTE_AMPLITUDE）
        4. 按开始时间排序
        5. 合并同音高且间隔很小的连续音符

        Args:
            note_events: [(start_s, end_s, midi, amplitude, pitch_bend)]

        Returns:
            清洗后的 note_events
        """
        if not note_events:
            return note_events

        min_duration_s = BASIC_PITCH_MIN_NOTE_LENGTH / 1000.0
        original_count = len(note_events)

        # --- 步骤 1-3：过滤 ---
        filtered = []
        for event in note_events:
            start_s, end_s, midi_pitch, amp, *rest = event
            pitch_bend = rest[0] if rest else None

            # 过滤次低频噪声
            if midi_pitch < MIN_MIDI_PITCH:
                continue

            # 过滤过短音符
            duration = end_s - start_s
            if duration < min_duration_s:
                continue

            # 过滤过弱音符
            if amp < MIN_NOTE_AMPLITUDE:
                continue

            filtered.append(
                (start_s, end_s, midi_pitch, amp, pitch_bend)
            )

        filtered_count = len(filtered)
        logger.info(
            f"过滤: {original_count} → {filtered_count} 个音符 "
            f"(移除 {original_count - filtered_count} 个噪声/过短/过弱音符)"
        )

        if not filtered:
            return []

        # --- 步骤 4：排序（按振幅降序，用于旋律提取） ---
        filtered.sort(key=lambda e: -e[3])  # 振幅从大到小

        # --- 步骤 5：旋律提取（非最大抑制）
        # 按振幅从大到小遍历，若与已接受音符重叠超过 50%，则丢弃（视为泛音/和声）
        accepted = []
        for event in filtered:
            start_s, end_s, midi_pitch, amp, pb = event
            duration = end_s - start_s
            if duration <= 0:
                continue

            overlap_too_much = False
            for acc in accepted:
                acc_start, acc_end = acc[0], acc[1]
                # 计算重叠
                overlap_start = max(start_s, acc_start)
                overlap_end = min(end_s, acc_end)
                overlap = overlap_end - overlap_start
                if overlap > 0:
                    shorter_dur = min(duration, acc_end - acc_start)
                    if overlap / shorter_dur > 0.5:
                        overlap_too_much = True
                        break

            if not overlap_too_much:
                accepted.append(event)

        # 按开始时间重新排序
        accepted.sort(key=lambda e: (e[0], e[2]))
        logger.info(
            f"旋律提取: {filtered_count} → {len(accepted)} 个音符 "
            f"(移除 {filtered_count - len(accepted)} 个重叠泛音/和声)"
        )

        if not accepted:
            return []

        # --- 步骤 6：合并同音高连续音符 ---
        merged = []
        for event in accepted:
            if not merged:
                merged.append(list(event))
                continue

            prev = merged[-1]
            prev_start, prev_end, prev_pitch, prev_amp, prev_pb = prev
            curr_start, curr_end, curr_pitch, curr_amp, curr_pb = event

            # 同音高 + 间隔小于阈值 → 合并
            if (
                curr_pitch == prev_pitch
                and curr_start - prev_end < MERGE_GAP_THRESHOLD
            ):
                # 延长前一个音符的结束时间
                prev[1] = max(prev_end, curr_end)
                # 振幅取较大值
                prev[3] = max(prev_amp, curr_amp)
            else:
                merged.append(list(event))

        merged = [tuple(e) for e in merged]
        logger.info(
            f"合并: {len(accepted)} → {len(merged)} 个音符 "
            f"(合并 {len(accepted) - len(merged)} 个同音高连续音符)"
        )

        return merged

    def transcribe_melody(
        self,
        audio_path: str | Path,
        instrument_config: dict[str, Any],
    ) -> list[tuple]:
        """使用 Melodia 风格引擎从多声部音乐中提取主旋律。

        基于 PYIN 音高追踪 + 旋律轮廓选择，专为器乐独奏/合奏设计，
        每帧只追踪一个最强音高，天然滤除和声/伴奏。

        Args:
            audio_path: WAV/MP3 文件路径
            instrument_config: 乐器配置

        Returns:
            note_events: 已含后处理
        """
        from app.services.melody import MelodyExtractor

        extractor = MelodyExtractor()
        note_events = extractor.extract(str(audio_path), instrument_config)

        logger.info(f"Melodia 引擎原始输出: {len(note_events)} 个音符")

        # 后处理：去掉 PYIN 本身不会产生低频噪声，但仍需合并和 NMS
        note_events = self._post_process_notes(note_events)

        return note_events
