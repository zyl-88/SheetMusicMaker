"""music21 音符量化服务。

将 basic-pitch 的 note_events（秒级时间戳）转为 music21 Stream，
量化到 1/16 网格，设置调号/拍号/速度，钳制音域。
"""
import logging
from typing import Any

from app.config import DEFAULT_TIME_SIG, QUANTIZE_DIVISION, tempo_map_enabled, tempo_value
from app.services.debug_dump import DebugRecorder

logger = logging.getLogger("sheet_music_maker.quantizer")


class NoteQuantizer:
    """音符量化器：note_events → music21 Stream。"""

    def quantize(
        self,
        note_events: list[tuple],
        analysis: dict[str, Any],
        instrument_config: dict[str, Any],
        audio_duration_s: float | None = None,
        dump: DebugRecorder | None = None,
    ) -> Any:
        """量化音符事件为 music21 Stream。

        Args:
            note_events: [(start_s, end_s, midi, amplitude, pitch_bend)]
            analysis: {bpm, key, time_signature, ...}
            instrument_config: {midi_low, midi_high, ...}
            audio_duration_s: 原音频时长（秒），用于钳制音符起点不越过音频末尾
            dump: DEBUG_DUMP=1 时的旁路观测器（None 时不做任何额外记录）

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

        # 创建 Stream 并设置元数据（per_bar 模式下速度图在音符写入后插入）
        use_tempo_map = tempo_map_enabled() and self._has_beat_grid(analysis)
        s = self._create_empty_stream(analysis, include_tempo=not use_tempo_map)

        midi_low = instrument_config["midi_low"]
        midi_high = instrument_config["midi_high"]

        # 时间映射 + 量化到 1/16 网格
        events: list[tuple[float, float, int, float]] = []
        transposed = 0
        dropped = 0
        # 音频结束位置对应的拍位（向下取整到网格），用于保证音符起点不越界
        max_start_q = None
        if audio_duration_s and audio_duration_s > 0:
            max_start_q = float(int(time_to_beat(float(audio_duration_s)) * div)) / div
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
                if dump is not None:
                    dump.decide({"stage": "range_fit", "action": "drop", "midi": raw_pitch})
                continue
            if pitch_int != raw_pitch:
                transposed += 1
                if dump is not None:
                    dump.decide(
                        {
                            "stage": "range_fit",
                            "action": "transpose",
                            "from": raw_pitch,
                            "to": pitch_int,
                        }
                    )
            events.append((start_q, duration_q, pitch_int, float(amp)))

        if transposed or dropped:
            logger.info(
                f"音域处理: 移调 {transposed} 个音符回音域，丢弃 {dropped} 个越界音符"
            )

        if not events:
            return self._create_empty_stream(analysis)

        # 节拍网格起点可能晚于首个音符：整体平移一次使最小时值为 0（只平移
        # 这一次，保留前导音的相对间隔；后续重叠不再后推任何音符）。
        min_start = min(e[0] for e in events)
        shift = -min_start if min_start < 0 else 0.0
        if shift:
            events = [(st + shift, d, p, a) for st, d, p, a in events]
            events.sort(key=lambda e: e[0])

        # 平移后若仍有起点越过音频末尾，钳回音频末尾（保证越界音符为 0）
        if max_start_q is not None:
            limit = max_start_q + shift
            events = [(min(st, limit), d, p, a) for st, d, p, a in events]

        # 消除重叠：保留每个音符的起点（onset），重叠时截短前一个音；
        # 同一量化格内只合并「同音高」的重复，异音高必须分开保留（顺延一个
        # 最小量化单位），否则落选音的 pitch 会静默消失。绝不后推任何音符
        # 起点，否则重叠会沿时间轴累积（原实现实测累计 +31.8s）。
        min_duration = 1.0 / div
        normalized: list[tuple[float, float, int, float]] = []
        for start_q, duration_q, pitch_int, amp in events:
            shifted_from = None
            if normalized:
                prev_start, prev_dur, prev_pitch, prev_amp = normalized[-1]
                prev_end = prev_start + prev_dur
                same_slot = start_q - prev_start < min_duration - 1e-9
                if same_slot and pitch_int == prev_pitch:
                    # 同音高同格：真正的重复，保留振幅更大的那条
                    if amp > prev_amp:
                        normalized[-1] = (
                            prev_start,
                            max(prev_dur, duration_q),
                            pitch_int,
                            amp,
                        )
                    continue
                if same_slot:
                    # 异音高同格：不能说谁响就留谁，顺延到下一个最小量化单位
                    shifted_from = start_q
                    start_q = prev_start + min_duration
                    if dump is not None:
                        dump.decide(
                            {
                                "stage": "quantize",
                                "action": "shift_same_slot",
                                "from_q": round(float(shifted_from), 6),
                                "to_q": round(float(start_q), 6),
                                "midi": int(pitch_int),
                                "kept_midi": int(prev_pitch),
                            }
                        )
                if start_q < prev_end - 1e-6:
                    if start_q - prev_start < min_duration - 1e-9:
                        # 同格且同音高的情况已在上面处理；这里只剩浮点边界
                        if amp > prev_amp:
                            normalized[-1] = (
                                prev_start,
                                max(prev_dur, duration_q),
                                pitch_int,
                                amp,
                            )
                        continue
                    # 截短前一个音到当前音起点，当前音起点保持不变
                    normalized[-1] = (prev_start, start_q - prev_start, prev_pitch, prev_amp)
            normalized.append((start_q, duration_q, pitch_int, amp))

        current_offset = 0.0  # 当前位置（四分音符单位）
        # 小间隙阈值（四分音符单位）：小于此值的间隙通过延长前一个音符填充
        gap_fill_threshold = 0.5  # 1/2 拍 = 1/8 音符
        prev_note = None  # 记录前一个音符，用于延长

        if dump is not None:
            dump.write(
                "quantized",
                {
                    "count": len(normalized),
                    "events": [
                        {"start_q": st, "dur_q": d, "midi": p, "amp": round(a, 4)}
                        for st, d, p, a in normalized
                    ],
                },
            )

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

        if use_tempo_map:
            last_start_q = max(st for st, *_ in normalized)
            self._insert_tempo_map(s, analysis, shift, last_start_q)

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

    def _create_empty_stream(self, analysis: dict[str, Any], include_tempo: bool = True) -> Any:
        """创建带元数据的空 Stream。

        include_tempo=False 时不写单一速度记号（由 ``_insert_tempo_map`` 写每小节
        tempo 图）。
        """
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
        if include_tempo:
            bpm = analysis.get("bpm", 120.0)
            # 回滚模式保持旧的整数截断（与 d79bc3b 逐条一致）；默认路径与
            # MusicXML/MIDI/ABC 共用同一个取整规则。
            number = int(bpm) if not tempo_map_enabled() else tempo_value(bpm)
            s.append(m21tempo.MetronomeMark(number=number))

        return s

    @staticmethod
    def _has_beat_grid(analysis: dict[str, Any]) -> bool:
        """检测到的节拍序列是否可用于 tempo 图（与 quantize 的判据一致）。"""
        beats = [float(b) for b in (analysis.get("beats") or [])]
        if len(beats) < 4:
            return False
        intervals = sorted(b - a for a, b in zip(beats, beats[1:]) if b > a)
        if not intervals:
            return False
        return 0.2 <= intervals[len(intervals) // 2] <= 2.0

    @staticmethod
    def _measure_length_q(analysis: dict[str, Any]) -> float:
        """小节长度（四分音符单位）：4/4→4，3/4→3，6/8→3。"""
        ts = str(analysis.get("time_signature") or DEFAULT_TIME_SIG)
        try:
            num, den = ts.split("/")
            length = float(num) * 4.0 / float(den)
        except (ValueError, ZeroDivisionError):
            return 4.0
        return length if length > 0 else 4.0

    def _insert_tempo_map(
        self,
        s: Any,
        analysis: dict[str, Any],
        shift: float,
        last_start_q: float,
    ) -> None:
        """按检测到的节拍序列，为每个小节写入一条速度记号。

        量化时的「秒 → 拍」用的是 beats 数组分段线性插值；导出时 music21 的
        「拍 → 秒」只认速度记号。若只写一条平均速度，tempo 变化会被压平，
        交付谱的起音位置会漂移（实测单曲 −534..+232ms）。这里让每小节的
        时长等于 beats 数组给出的实际时长，导出后的拍→秒与量化输入一致。
        """
        from music21 import tempo as m21tempo

        beats = [float(b) for b in (analysis.get("beats") or [])]
        intervals = sorted(b - a for a, b in zip(beats, beats[1:]) if b > a)
        beat_dur = intervals[len(intervals) // 2]
        bar_len = self._measure_length_q(analysis)

        def beat_to_time(b: float) -> float:
            if b <= 0:
                return beats[0] + b * beat_dur
            if b >= len(beats) - 1:
                return beats[-1] + (b - (len(beats) - 1)) * beat_dur
            lo = int(b)
            frac = b - lo
            return beats[lo] + frac * (beats[lo + 1] - beats[lo])

        last_bar = int(max(last_start_q, 0.0) // bar_len) + 1
        inserted = 0
        for k in range(last_bar):
            t0 = beat_to_time(k * bar_len - shift)
            if k == 0:
                # MIDI 时间轴固定从 0 开始，而检测到的拍网格从 beats[0] 起算；
                # 把这段前导偏移并入第 0 小节，保证后续各小节的绝对时间与
                # 量化时的「秒→拍」映射一致（否则整条谱会提前 beats[0]）。
                t0 = min(t0, 0.0)
            t1 = beat_to_time((k + 1) * bar_len - shift)
            duration = t1 - t0
            if duration <= 0:
                continue
            bpm_bar = 60.0 * bar_len / duration
            if not (1.0 <= bpm_bar <= 1000.0):
                continue
            s.insert(
                k * bar_len,
                m21tempo.MetronomeMark(number=tempo_value(bpm_bar), referent=1),
            )
            inserted += 1

        logger.info(f"速度图: {inserted} 个小节 tempo（拍间隔中位 {beat_dur:.3f}s）")
