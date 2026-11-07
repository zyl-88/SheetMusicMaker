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
        from music21 import note

        if not note_events:
            logger.warning("无音符事件可量化，返回空 Stream")
            return self._create_empty_stream(analysis)

        bpm = float(analysis.get("bpm") or 120.0)
        if bpm <= 0:
            bpm = 120.0
        beat_dur = 60.0 / bpm  # 每拍秒数
        div = QUANTIZE_DIVISION  # 量化精度：一拍 div 等分

        # 用检测到的节拍序列做「时间 → 拍位置」映射。
        # 只用 t/beat_dur 会忽略节拍相位和速度漂移，导致整份乐谱节奏整体错位。
        beats = [float(b) for b in (analysis.get("beats") or [])]
        use_beat_grid = False
        if len(beats) >= 4:
            intervals = sorted(b - a for a, b in zip(beats, beats[1:]) if b > a)
            if intervals:
                median_interval = intervals[len(intervals) // 2]
                if 0.2 <= median_interval <= 2.0:
                    beat_dur = median_interval
                    use_beat_grid = True

        def time_to_beat(t: float) -> float:
            """把秒映射为连续的拍位置（beats 数组线性插值）。"""
            if not use_beat_grid:
                return t / beat_dur
            if t <= beats[0]:
                return (t - beats[0]) / beat_dur
            if t >= beats[-1]:
                return (len(beats) - 1) + (t - beats[-1]) / beat_dur
            lo, hi = 0, len(beats) - 1
            while lo < hi - 1:
                mid = (lo + hi) // 2
                if beats[mid] <= t:
                    lo = mid
                else:
                    hi = mid
            span = beats[hi] - beats[lo]
            frac = (t - beats[lo]) / span if span > 0 else 0.0
            return lo + frac

        # 创建 Stream 并设置元数据
        s = self._create_empty_stream(analysis)

        midi_low = instrument_config["midi_low"]
        midi_high = instrument_config["midi_high"]

        # 时间映射 + 量化到 1/16 网格
        events: list[tuple[float, float, int, float]] = []
        transposed = 0
        dropped = 0
        for start_s, end_s, midi_pitch, amp, *_ in sorted(note_events, key=lambda e: e[0]):
            if end_s <= start_s:
                continue

            start_raw = time_to_beat(start_s)
            end_raw = time_to_beat(end_s)
            start_q = round(start_raw * div) / div
            duration_q = round((end_raw - start_raw) * div) / div
            if duration_q <= 0:
                duration_q = 1.0 / div  # 最短 1/16

            raw_pitch = int(round(midi_pitch))
            pitch_int = self._fit_pitch_to_range(raw_pitch, midi_low, midi_high)
            if pitch_int is None:
                dropped += 1
                continue
            if pitch_int != raw_pitch:
                transposed += 1
            events.append((start_q, duration_q, pitch_int, float(amp)))

        if transposed or dropped:
            logger.info(
                f"音域处理: 移调 {transposed} 个音符回音域，丢弃 {dropped} 个越界音符"
            )

        if not events:
            return self._create_empty_stream(analysis)

        # 节拍网格起点可能晚于首个音符，整体平移使最小时值为 0
        min_start = min(e[0] for e in events)
        if min_start < 0:
            events = [(st - min_start, d, p, a) for st, d, p, a in events]
            events.sort(key=lambda e: e[0])

        # 消除重叠：单声部谱面中后一个音符不得早于前一个的结束
        normalized: list[tuple[float, float, int, float]] = []
        cursor = 0.0
        for start_q, duration_q, pitch_int, amp in events:
            if start_q < cursor - 1e-6:
                start_q = cursor
            normalized.append((start_q, duration_q, pitch_int, amp))
            cursor = start_q + duration_q

        current_offset = 0.0  # 当前位置（四分音符单位）
        # 小间隙阈值（四分音符单位）：小于此值的间隙通过延长前一个音符填充
        gap_fill_threshold = 0.5  # 1/2 拍 = 1/8 音符
        prev_note = None  # 记录前一个音符，用于延长

        for start_q, duration_q, pitch_int, amp in normalized:
            # 处理间隙
            if start_q > current_offset + 0.01:  # 有间隙
                gap = round((start_q - current_offset) * div) / div
                if gap <= gap_fill_threshold and prev_note is not None:
                    # 小间隙：延长前一个音符（连音效果）
                    prev_note.quarterLength += gap
                elif gap > 0:
                    # 大间隙：插入休止符
                    s.insert(current_offset, note.Rest(quarterLength=gap))
                    current_offset = start_q

            # 创建音符
            n = note.Note(midi=pitch_int, quarterLength=duration_q)
            # 设置力度
            if amp > 0:
                n.volume.velocity = int(min(127, max(1, amp * 127)))

            s.insert(start_q, n)
            prev_note = n
            current_offset = max(current_offset, start_q + duration_q)

        logger.info(
            f"量化完成: {len(normalized)} 个音符, "
            f"总时长 {current_offset:.1f} 四分音符 (BPM={bpm:.1f})"
        )
        return s

    @staticmethod
    def _fit_pitch_to_range(pitch: int, midi_low: int, midi_high: int) -> int | None:
        """把越界音高按八度移调回音域；无法用一个八度修正的返回 None（丢弃）。

        旧实现直接钳制到边界，会让所有越界音变成同一个边界音（伪造旋律，
        例如 flute 轨道上一串 C4）。改为优先移调一个八度（纠正八度误检），
        移调后仍越界的音符交给调用方丢弃。
        """
        if midi_low <= pitch <= midi_high:
            return pitch
        for shift in (12, -12):
            if midi_low <= pitch + shift <= midi_high:
                return pitch + shift
        return None

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
