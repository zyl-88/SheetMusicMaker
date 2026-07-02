"""music21 音符量化服务。

将 basic-pitch 的 note_events（秒级时间戳）转为 music21 Stream，
量化到 1/16 网格，设置调号/拍号/速度，钳制音域。
"""
import logging
from typing import Any

from app.config import QUANTIZE_DIVISION

logger = logging.getLogger("sheet_music_maker.quantizer")


class NoteQuantizer:
    """音符量化器：note_events → music21 Stream。"""

    def quantize(
        self,
        note_events: list[tuple],
        analysis: dict[str, Any],
        instrument_config: dict[str, Any],
    ) -> Any:
        """量化音符事件为 music21 Stream。

        Args:
            note_events: [(start_s, end_s, midi, amplitude, pitch_bend)]
            analysis: {bpm, key, time_signature, ...}
            instrument_config: {midi_low, midi_high, ...}

        Returns:
            music21 stream.Stream
        """
        from music21 import key, meter, note, tempo as m21tempo

        if not note_events:
            logger.warning("无音符事件可量化，返回空 Stream")
            return self._create_empty_stream(analysis)

        bpm = analysis["bpm"]
        beat_dur = 60.0 / bpm  # 每拍秒数
        div = QUANTIZE_DIVISION  # 量化精度：一拍 div 等分

        # 创建 Stream 并设置元数据
        s = self._create_empty_stream(analysis)

        # 按开始时间排序
        sorted_events = sorted(note_events, key=lambda e: e[0])

        midi_low = instrument_config["midi_low"]
        midi_high = instrument_config["midi_high"]

        current_offset = 0.0  # 当前位置（四分音符单位）
        # 小间隙阈值（四分音符单位）：小于此值的间隙通过延长前一个音符填充
        gap_fill_threshold = 0.5  # 1/2 拍 = 1/8 音符

        prev_note = None  # 记录前一个音符，用于延长

        for start_s, end_s, midi_pitch, amp, *rest in sorted_events:
            # 跳过无效音符
            if end_s <= start_s:
                continue

            # 秒 → 四分音符单位
            start_q = start_s / beat_dur
            end_q = end_s / beat_dur
            duration_q = end_q - start_q

            # 量化到 1/16 网格
            start_q = round(start_q * div) / div
            duration_q = round(duration_q * div) / div

            if duration_q <= 0:
                duration_q = 1.0 / div  # 最短 1/16

            # 处理间隙
            if start_q > current_offset + 0.01:  # 有间隙
                gap = start_q - current_offset
                gap = round(gap * div) / div

                if gap <= gap_fill_threshold and prev_note is not None:
                    # 小间隙：延长前一个音符（连音效果）
                    prev_note.quarterLength += gap
                    # current_offset 不变，因为前一个音符延长了
                elif gap > 0:
                    # 大间隙：插入休止符
                    rest_dur = gap
                    r = note.Rest(quarterLength=rest_dur)
                    s.insert(current_offset, r)
                    current_offset = start_q
                # gap == 0 时不处理
            elif start_q < current_offset - 0.01:
                # 音符开始时间早于当前位置（重叠）：跳到该音符
                current_offset = start_q

            # 音域钳制
            pitch_int = int(round(midi_pitch))
            if pitch_int < midi_low:
                pitch_int = midi_low
            elif pitch_int > midi_high:
                pitch_int = midi_high

            # 创建音符
            n = note.Note(midi=pitch_int, quarterLength=duration_q)
            # 设置力度
            if amp > 0:
                n.volume.velocity = int(min(127, max(1, amp * 127)))

            s.insert(start_q, n)
            prev_note = n
            current_offset = max(current_offset, start_q + duration_q)

        logger.info(
            f"量化完成: {len(sorted_events)} 个音符, "
            f"总时长 {current_offset:.1f} 四分音符"
        )
        return s

    def _create_empty_stream(self, analysis: dict[str, Any]) -> Any:
        """创建带元数据的空 Stream。"""
        from music21 import key, meter, tempo as m21tempo, stream

        s = stream.Stream()

        # 调号
        key_info = analysis.get("key", {"tonic": "C", "mode": "major"})
        tonic = key_info["tonic"]
        mode = key_info["mode"]
        # music21 用 '-' 表示降号，我们的 NOTE_NAMES 用 'b'
        tonic_m21 = tonic.replace("b", "-")
        try:
            ks = key.Key(tonic_m21, mode)
        except Exception:
            ks = key.Key("C", "major")
        s.append(ks)

        # 拍号
        ts_str = analysis.get("time_signature", "4/4")
        s.append(meter.TimeSignature(ts_str))

        # 速度
        bpm = analysis.get("bpm", 120.0)
        s.append(m21tempo.MetronomeMark(number=int(bpm)))

        return s
