"""basic-pitch 自动音乐转录服务。

使用 Spotify basic-pitch 将音频转为音符事件。
支持按乐器音域过滤频率范围，并对原始输出进行后处理：
- 过滤次低频噪声音符
- 过滤过短/过弱音符
- 合并同音高连续音符（去重）
"""
import logging
import os
from pathlib import Path
from typing import Any

from app.config import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_NOTE_LENGTH,
    BASIC_PITCH_ONSET_THRESHOLD,
    MELODY_LINE_STRATEGY,
    MERGE_GAP_THRESHOLD,
    MIN_MIDI_PITCH,
    MIN_NOTE_AMPLITUDE,
)
from app.services.debug_dump import DebugRecorder, notes_payload

logger = logging.getLogger("sheet_music_maker.transcriber")

# 选线参数：同一时间窗内视为“同时发声”，振幅低于最强音一半的候选视为泛音
ONSET_CLUSTER_S = 0.04
AMP_KEEP_RATIO = 0.6
HARMONIC_AMP_RATIO = 0.6
# 孤立弱峰（无同起步竞争者）仍低于此振幅时视为噪声残留
SELECT_AMP_FLOOR = 0.35


def current_strategy() -> str:
    """读取主旋律线选择策略（环境变量优先，便于 A/B 回滚）。"""
    value = os.environ.get("MELODY_LINE_STRATEGY", MELODY_LINE_STRATEGY)
    return value if value in ("legacy_nms", "dp_register") else MELODY_LINE_STRATEGY


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
        dump: DebugRecorder | None = None,
    ) -> list[tuple]:
        """转录音频为音符事件。

        Args:
            vocals_wav_path: 人声 WAV 文件路径
            instrument_config: 乐器配置（含 min_freq/max_freq）
            dump: DEBUG_DUMP=1 时的旁路观测器（None 时不做任何额外记录）

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

        # 后处理：过滤噪声 + 选线 + 去重合并
        dp_mode = current_strategy() == "dp_register"
        if dump is not None:
            dump.notes("raw_notes", note_events)
            dump.decide({"stage": "engine", "engine": "basic_pitch", "strategy": current_strategy()})
        note_events = self._post_process_notes(
            note_events, select_melody_line=dp_mode, merge_same_pitch=not dp_mode, dump=dump
        )

        if not note_events:
            logger.warning("后处理后未检测到任何音符")

        return note_events

    def _post_process_notes(
        self,
        note_events: list[tuple],
        select_melody_line: bool = False,
        merge_same_pitch: bool = True,
        dump: DebugRecorder | None = None,
    ) -> list[tuple]:
        """对 basic-pitch 原始输出进行后处理。

        步骤：
        1. 过滤低于 MIN_MIDI_PITCH 的次低频噪声
        2. 过滤时长过短的音符（< BASIC_PITCH_MIN_NOTE_LENGTH 毫秒）
        3. 过滤振幅过弱的音符（< MIN_NOTE_AMPLITUDE）
        4. 按开始时间排序
        5. 选线（legacy_nms：振幅优先非最大抑制；dp_register：音区优先选线）
        6. 可选合并同音高且间隔很小的连续音符

        Args:
            note_events: [(start_s, end_s, midi, amplitude, pitch_bend)]
            select_melody_line: True 时使用 dp_register 选线，否则保持 legacy NMS
            merge_same_pitch: True 时执行间隔合并。basic-pitch 的每个音符都由
                独立起音点生成，DP 路径应传 False，否则会吞掉真实同音反复
                （F4）；PYIN/legacy 路径需要合并尾部续段（F13）。

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
                if dump is not None:
                    dump.decide({"stage": "filter", "reason": "below_min_midi", "event": notes_payload([event])[0]})
                continue

            # 过滤过短音符
            duration = end_s - start_s
            if duration < min_duration_s:
                if dump is not None:
                    dump.decide({"stage": "filter", "reason": "too_short", "event": notes_payload([event])[0]})
                continue

            # 过滤过弱音符
            if amp < MIN_NOTE_AMPLITUDE:
                if dump is not None:
                    dump.decide({"stage": "filter", "reason": "too_weak", "event": notes_payload([event])[0]})
                continue

            filtered.append(
                (start_s, end_s, midi_pitch, amp, pitch_bend)
            )

        filtered_count = len(filtered)
        logger.info(
            f"过滤: {original_count} → {filtered_count} 个音符 "
            f"(移除 {original_count - filtered_count} 个噪声/过短/过弱音符)"
        )
        if dump is not None:
            dump.notes("filtered_notes", filtered)

        if not filtered:
            return []

        # --- 步骤 4：排序（按振幅降序，用于旋律提取） ---
        filtered.sort(key=lambda e: -e[3])  # 振幅从大到小

        # --- 步骤 5：旋律线提取 ---
        if select_melody_line:
            accepted = self._select_melody_line(filtered, dump=dump)
            logger.info(
                f"旋律线选线(dp_register): {filtered_count} → {len(accepted)} 个音符"
            )
        else:
            accepted = self._suppress_overlaps(filtered)

        # 按开始时间重新排序
        accepted.sort(key=lambda e: (e[0], e[2]))

        if dump is not None:
            dump.notes("selected_notes", accepted)

        if not accepted:
            return []

        # --- 步骤 6：按需合并同音高连续音符 ---
        if not merge_same_pitch:
            if dump is not None:
                dump.notes("canonical_events", accepted)
            return accepted
        merged = self._merge_same_pitch(accepted, dump=dump)
        if dump is not None:
            dump.notes("canonical_events", merged)
        return merged

    def _suppress_overlaps(self, filtered: list[tuple]) -> list[tuple]:
        """legacy NMS：按振幅从大到小遍历，与已接受音符重叠超过 50% 则丢弃。"""
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

        logger.info(
            f"旋律提取: {len(filtered)} → {len(accepted)} 个音符 "
            f"(移除 {len(filtered) - len(accepted)} 个重叠泛音/和声)"
        )
        return accepted

    def _select_melody_line(
        self, filtered: list[tuple], dump: DebugRecorder | None = None
    ) -> list[tuple]:
        """dp_register 选线：同一时刻优先高音区，保留每个起音点的旋律音。

        背景：有伴奏时 basic-pitch 会同时输出旋律与和弦/低音，旧 NMS 按振幅
        优先保留更响的伴奏。这里以 40ms 起音窗聚类，先按振幅剔除明显泛音，
        再在剩余候选中取音高最高者；若最高音与次高音相差 ≥12 半音且振幅
        明显更弱，则回退到次高音（抑制八度泛音）。
        """
        ordered = sorted(filtered, key=lambda e: (e[0], -e[2], -e[3]))

        clusters: list[list[tuple]] = []
        for event in ordered:
            if not clusters or event[0] - clusters[-1][0][0] > ONSET_CLUSTER_S:
                clusters.append([event])
            else:
                clusters[-1].append(event)

        selected = []
        details = [] if dump is not None else None
        for cluster in clusters:
            max_amp = max(e[3] for e in cluster)
            amp_floor = max(SELECT_AMP_FLOOR, max_amp * AMP_KEEP_RATIO)
            candidates = sorted(
                (e for e in cluster if e[3] >= amp_floor), key=lambda e: (-e[2], -e[3])
            )
            if not candidates:
                if dump is not None:
                    dump.decide(
                        {
                            "stage": "selection",
                            "reason": "below_amp_floor",
                            "onset_s": round(float(cluster[0][0]), 4),
                            "max_amp": round(float(max_amp), 4),
                            "pitches": [int(e[2]) for e in cluster],
                        }
                    )
                continue

            pick = candidates[0]
            harmonic_fallback = False
            if len(candidates) > 1:
                second = candidates[1]
                if pick[2] - second[2] >= 12 and pick[3] < second[3] * HARMONIC_AMP_RATIO:
                    pick = second
                    harmonic_fallback = True
            selected.append(pick)
            if details is not None:
                details.append(
                    {
                        "onset_s": round(float(cluster[0][0]), 4),
                        "candidates": len(candidates),
                        "max_amp": round(float(max_amp), 4),
                        "picked": notes_payload([pick])[0],
                        "harmonic_fallback": harmonic_fallback,
                    }
                )

        if dump is not None:
            dump.write(
                "melody_selection",
                {"clusters": len(clusters), "kept": len(selected), "details": details},
            )
        return self._suppress_low_interjections(selected, dump=dump)

    def _suppress_low_interjections(
        self, selected: list[tuple], dump: DebugRecorder | None = None
    ) -> list[tuple]:
        """剔除插在旋律持续区间内的低音伴奏音（八度跳变的主要来源）。

        单声部旋律里，一个音起音时前一个旋律音不应仍在持续；若前一个音
        尚未结束、新音比它低 ≥12 半音、且低出近几音的中位音区 ≥12 半音，
        则新音是伴奏/低音渗漏，丢弃它而不是让旋律线在高低音区之间来回跳。

        整曲实测 82.9% 的八度跳变以 accompaniment/inner 音为低端点，且这些
        低音起音时对应的旋律音仍在响（basic-pitch 参考里可看到 8.91-9.28s
        的 C#5 覆盖 9.12s 的 F#2 起音），符合本规则。
        """
        kept: list[tuple] = []
        recent: list[int] = []
        for event in selected:
            start_s, end_s, pitch_int, _amp, *_ = event
            if len(kept) >= 2 and recent:
                prev = kept[-1]
                reg = sorted(recent)[len(recent) // 2]
                # 前音「确实仍在持续」才判伴奏插入：basic-pitch 相邻音符会有
                # 一两帧的尾部重叠（F13 的 C5→G3 只重叠 ~20ms），不能当成伴奏。
                prev_dur = max(prev[1] - prev[0], 0.0)
                prev_remaining = prev[1] - start_s
                if (
                    prev_remaining >= max(0.08, 0.2 * prev_dur)
                    and prev[2] - pitch_int >= 12
                    and pitch_int <= reg - 12
                ):
                    if dump is not None:
                        dump.decide(
                            {
                                "stage": "selection",
                                "reason": "low_interjection",
                                "onset_s": round(float(start_s), 4),
                                "midi": int(pitch_int),
                                "prev_midi": int(prev[2]),
                                "prev_end_s": round(float(prev[1]), 4),
                                "register_midi": int(reg),
                            }
                        )
                    continue
            kept.append(event)
            recent.append(int(pitch_int))
            if len(recent) > 5:
                recent.pop(0)
        if len(kept) != len(selected):
            logger.info(
                f"旋律线单一化: {len(selected)} → {len(kept)} 个音符 "
                f"(剔除 {len(selected) - len(kept)} 个旋律持续期内的低音伴奏)"
            )
        return kept

    def _merge_same_pitch(
        self, accepted: list[tuple], dump: DebugRecorder | None = None
    ) -> list[tuple]:
        """合并同音高且间隔小于 MERGE_GAP_THRESHOLD 的连续音符。

        仅用于 legacy/非 DP 路径：PYIN 的音符切分会在同一音符尾部留下一段
        12ms 重叠（一个 hop）的续段，需要按间隔合并；basic-pitch 的每个音符
        都由独立起音点生成，应用同一规则会把真实的同音反复吞掉（F4）。
        """
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
                if dump is not None:
                    dump.decide(
                        {
                            "stage": "merge",
                            "reason": "same_pitch_gap",
                            "prev": notes_payload([tuple(prev)])[0],
                            "curr": notes_payload([event])[0],
                        }
                    )
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
        dump: DebugRecorder | None = None,
    ) -> list[tuple]:
        """使用 Melodia 风格引擎从多声部音乐中提取主旋律。

        基于 PYIN 音高追踪 + 旋律轮廓选择，专为器乐独奏/合奏设计，
        每帧只追踪一个最强音高，天然滤除和声/伴奏。

        dp_register 策略下改用 basic-pitch 多音高候选 + DP 选线：
        实测有伴奏时 PYIN 只会跟随更响的低音/和弦（F5/F11 的旋律音
        在轮廓中完全不存在），需多音高候选才能检出后再选线。

        Args:
            audio_path: WAV/MP3 文件路径
            instrument_config: 乐器配置
            dump: DEBUG_DUMP=1 时的旁路观测器（None 时不做任何额外记录）

        Returns:
            note_events: 已含后处理
        """
        if current_strategy() == "dp_register":
            try:
                logger.info("dp_register: 使用 basic-pitch 多音高候选替代 PYIN 单声道路径")
                return self.transcribe(audio_path, instrument_config, dump=dump)
            except Exception as e:
                logger.warning(f"basic-pitch 候选提取失败，回退 Melodia: {e}")
                if dump is not None:
                    dump.warn(f"basic-pitch 候选提取失败，回退 Melodia: {e}")

        from app.services.melody import MelodyExtractor

        extractor = MelodyExtractor()
        note_events = extractor.extract(str(audio_path), instrument_config)

        logger.info(f"Melodia 引擎原始输出: {len(note_events)} 个音符")
        if dump is not None:
            dump.notes("raw_notes", note_events)
            dump.decide({"stage": "engine", "engine": "melody_pyin", "strategy": current_strategy()})

        # 后处理：去掉 PYIN 本身不会产生低频噪声，但仍需合并和 NMS
        note_events = self._post_process_notes(note_events, dump=dump)

        return note_events
