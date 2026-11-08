"""basic-pitch 自动音乐转录服务。

使用 Spotify basic-pitch 将音频转为音符事件。
支持按乐器音域过滤频率范围，并对原始输出进行后处理：
- 过滤次低频噪声音符
- 过滤过短/过弱音符
- 合并同音高连续音符（去重）
"""
import logging
import os
from bisect import bisect_left, bisect_right
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
    melody_continuity_gate_enabled,
    melody_continuity_gate_mode,
    low_bass_filter_enabled,
)
from app.services.debug_dump import DebugRecorder, notes_payload

logger = logging.getLogger("sheet_music_maker.transcriber")

# 选线参数：同一时间窗内视为“同时发声”，振幅低于最强音一半的候选视为泛音
ONSET_CLUSTER_S = 0.04
AMP_KEEP_RATIO = 0.6
HARMONIC_AMP_RATIO = 0.6
# 孤立弱峰（无同起步竞争者）仍低于此振幅时视为噪声残留
SELECT_AMP_FLOOR = 0.35

# 旋律连续性门控：低音起音缺少局部高音证据时判为伴奏渗漏（详见
# _suppress_low_interjections_by_continuity）。这些阈值来自 dump 回放取证：
# - 低音与音区基准相差 ≥12 半音才算「低音候选」。默认 covering 档只在
#   「高音确实覆盖该时刻」时剔除，GAP=12 复现验收过的 cover 档（415 音、覆盖
#   80.2%）；12/16 两档均保留，可用 MELODY_CONTINUITY_REGISTER_GAP 切换
#   （strict 档下 GAP=16 是覆盖率 ≥78.7% 档位里跳变下降最多的一档）
# - 高音候选必须在 ±0.2s 内起音，或仍跨过起音点发声 ≥0.02s（一帧）
# - 高音候选跨过起音点并继续发声 ≥0.08s 才算「覆盖」，属伴奏插在旋律持续期内
# - 音区基准取对称 ±6s 时间窗的 75 分位，避免连续低音拉低自己的判定基准
# - 低音若处于稀疏上下文（±0.5s 内没有其他旋律音），视为独奏旋律而保留
CONTINUITY_REGISTER_GAP = int(os.environ.get("MELODY_CONTINUITY_REGISTER_GAP", "12"))

# R1–R4 低音伴奏过滤（LOW_BASS_FILTER，默认 on）：覆盖门控之后仍有 A3 以下的
# 伴奏残留（尾段低音 ostinato、间隙单音低音等）。只作用于「A3 以下、且相对
# 音区基准低 ≥16 半音」的音，宁可漏删也不碰低音区真旋律（见 F17）。
LOW_BASS_MIN_MIDI = 57          # A3；护栏只统计 A3 以下
LOW_BASS_REGISTER_GAP = 16      # 相对音区基准的低音门槛（独立于门控 GAP）
LOW_BASS_NEIGHBOR_S = 1.5       # R1：±1.5s 内没有同一条旋律线上的音才算孤立低音
                                # （原 0.5s 在高低混合音区会误删真正的低音旋律；
                                # 验收方反事实：基准抬到高音区时 R1 会删 28/32）
LOW_BASS_REPEAT_WINDOW_S = 3.0  # R1：±3s 内同音高重复
LOW_BASS_R2_JUMP = 12           # R2：两侧旋律音与低音的跨度均 ≥12 半音
LOW_BASS_R4_JUMP = 6            # R4：低音段两侧旋律音与低音的跨度均 ≥6 半音
                                # （验收方原定 ≥5；F12-clean 的 G3 断奏后面只差 5
                                # 半音，5 会误删该边界旋律，故收紧到 6）
CONTINUITY_LOCAL_WINDOW_S = 0.2
CONTINUITY_SOUNDING_S = 0.02
CONTINUITY_COVER_S = 0.08
CONTINUITY_COVER_START_GUARD_S = 0.02
CONTINUITY_REGISTER_WINDOW_S = 6.0
CONTINUITY_REGISTER_QUANTILE = 0.75
CONTINUITY_SPARSE_GAP_S = 0.5


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
                        "amp_floor": round(float(amp_floor), 4),
                        "candidate_notes": [
                            {
                                "start_s": round(float(e[0]), 4),
                                "midi": int(e[2]),
                                "amp": round(float(e[3]), 4),
                            }
                            for e in candidates
                        ],
                        "floor_rejected_notes": [
                            {
                                "start_s": round(float(e[0]), 4),
                                "midi": int(e[2]),
                                "amp": round(float(e[3]), 4),
                            }
                            for e in cluster
                            if e[3] < amp_floor
                        ],
                        "picked": notes_payload([pick])[0],
                        "harmonic_fallback": harmonic_fallback,
                    }
                )

        if dump is not None:
            dump.write(
                "melody_selection",
                {"clusters": len(clusters), "kept": len(selected), "details": details},
            )
        if melody_continuity_gate_enabled():
            selected = self._suppress_low_interjections_by_continuity(
                selected, filtered, dump=dump
            )
        else:
            selected = self._suppress_low_interjections(selected, dump=dump)
        if low_bass_filter_enabled():
            selected = self._suppress_low_bass_accompaniment(selected, dump=dump)
        return selected

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

    def _suppress_low_interjections_by_continuity(
        self,
        selected: list[tuple],
        candidates: list[tuple],
        dump: DebugRecorder | None = None,
    ) -> list[tuple]:
        """旋律连续性门控：低音起音缺少局部高音证据时判为伴奏渗漏。

        旧规则要求「上一个旋律音仍在响」，但 basic-pitch 常把前音切短
        （dump 回放里 57 个存活低音有 52 个败在这一条），因此改用候选池里的
        局部证据，不再依赖被切短的相邻音：
        - 高音候选跨过该起音点并继续发声 ≥0.08s → 伴奏插在旋律持续期内，丢弃
        - （仅 strict 档）±0.2s 内既无高音候选起音、也无高音候选仍在发声 → 丢弃
        - 低音处于稀疏上下文（±0.5s 内没有其他旋律音）→ 独奏旋律，保留

        默认 covering 档只在「高音确实覆盖该时刻」时剔除，因此对「该时刻
        完全没有高音」的跳变成因不处理——这是与验收方共同接受的取舍。

        音区基准取对称 ±6s 时间窗的 75 分位，连续低音不会把自己的判定基准
        拉低后「自己给自己作证」。
        """
        if not selected:
            return selected

        mode = melody_continuity_gate_mode()
        starts = [float(event[0]) for event in selected]
        registers = self._register_baselines(selected, starts)
        kept: list[tuple] = []
        for event, start_s, register in zip(selected, starts, registers):
            pitch_int = int(round(event[2]))
            if pitch_int > register - CONTINUITY_REGISTER_GAP:
                kept.append(event)
                continue

            high = [
                c for c in candidates
                if int(round(c[2])) >= pitch_int + CONTINUITY_REGISTER_GAP
            ]
            covering = any(
                float(c[0]) <= start_s - CONTINUITY_COVER_START_GUARD_S
                and float(c[1]) - start_s >= CONTINUITY_COVER_S
                for c in high
            )
            local_onset = any(
                abs(float(c[0]) - start_s) <= CONTINUITY_LOCAL_WINDOW_S for c in high
            )
            local_sounding = any(
                float(c[0]) <= start_s
                and float(c[1]) - start_s >= CONTINUITY_SOUNDING_S
                for c in high
            )
            sparse = self._is_sparse_context(starts, start_s)
            if mode == "covering":
                drop = covering
            else:
                drop = covering or (not local_onset and not local_sounding and not sparse)
            if drop:
                if dump is not None:
                    dump.decide(
                        {
                            "stage": "selection",
                            "reason": "low_interjection_continuity",
                            "onset_s": round(float(start_s), 4),
                            "midi": pitch_int,
                            "register_midi": int(register),
                            "covering": covering,
                            "local_onset": local_onset,
                            "local_sounding": local_sounding,
                            "sparse_context": sparse,
                        }
                    )
                continue
            kept.append(event)

        if len(kept) != len(selected):
            logger.info(
                f"旋律连续性门控: {len(selected)} → {len(kept)} 个音符 "
                f"(剔除 {len(selected) - len(kept)} 个缺乏局部高音证据的低音)"
            )
        return kept

    @staticmethod
    def _register_baselines(selected: list[tuple], starts: list[float]) -> list[int]:
        """逐音计算音区基准：对称 ±6s 时间窗内的 75 分位音高。"""
        registers: list[int] = []
        for t in starts:
            left = bisect_left(starts, t - CONTINUITY_REGISTER_WINDOW_S)
            right = bisect_right(starts, t + CONTINUITY_REGISTER_WINDOW_S)
            values = sorted(int(round(selected[i][2])) for i in range(left, right))
            index = int(round(CONTINUITY_REGISTER_QUANTILE * (len(values) - 1)))
            registers.append(values[index])
        return registers

    @staticmethod
    def _is_sparse_context(starts: list[float], t: float) -> bool:
        """该起音 ±0.5s 内是否没有任何其他旋律音（独奏上下文）。"""
        previous = bisect_left(starts, t - 1e-9) - 1
        following = bisect_right(starts, t + 1e-9)
        gaps = []
        if previous >= 0:
            gaps.append(t - starts[previous])
        if following < len(starts):
            gaps.append(starts[following] - t)
        return not gaps or min(gaps) > CONTINUITY_SPARSE_GAP_S

    def _suppress_low_bass_accompaniment(
        self, selected: list[tuple], dump: DebugRecorder | None = None
    ) -> list[tuple]:
        """R1–R4：剔除覆盖门控之后残留的低音伴奏（见 LOW_BASS_* 常量）。

        规则只看旋律线自身的邻接结构，不依赖参考音高；每个音符按
        R1→R2→R3→R4 优先级归因一次。全部条件都要求该音在 A3 以下且比
        局部音区基准低 ≥16 半音，避免误伤低音区旋律（F17 对抗样本）。
        """
        n = len(selected)
        if n < 2:
            return selected
        starts = [float(e[0]) for e in selected]
        pitches = [int(round(e[2])) for e in selected]
        registers = self._register_baselines(selected, starts)
        low = [
            pitches[i] < LOW_BASS_MIN_MIDI
            and registers[i] - pitches[i] >= LOW_BASS_REGISTER_GAP
            for i in range(n)
        ]
        if not any(low):
            return selected

        def has_melody_near(i: int, same_line: bool = True) -> bool:
            # same_line：邻近的 ≥A3 音还必须与低音相差 ≤一个音区门限，才算
            # 同一条旋律线；否则那是高低两个声部（前奏 ostinato 距旋律
            # 17–29 半音，窗口拉长到 1.5s 后会误把 7.28–8.23s 的低音保下来）
            return any(
                j != i
                and not low[j]
                and abs(starts[j] - starts[i]) <= LOW_BASS_NEIGHBOR_S
                and (not same_line or abs(pitches[j] - pitches[i]) <= LOW_BASS_REGISTER_GAP)
                for j in range(n)
            )

        def same_pitch_near(i: int) -> bool:
            return any(
                j != i
                and pitches[j] == pitches[i]
                and abs(starts[j] - starts[i]) <= LOW_BASS_REPEAT_WINDOW_S
                for j in range(n)
            )

        def run_bounds(i: int) -> tuple[int, int]:
            a = i
            while a - 1 >= 0 and low[a - 1]:
                a -= 1
            b = i
            while b + 1 < n and low[b + 1]:
                b += 1
            return a, b

        def r1_ostinato(i: int) -> bool:
            a, b = run_bounds(i)
            run_length = b - a + 1
            repeated = same_pitch_near(i)
            # 短促下探（≤2 音）用普通邻近窗口保护（F12-clean 的 G3、F18 的双音下探）；
            # 连续 ≥3 音的低音段才要求邻近音与自己相差 ≤16 半音，否则那是高低
            # 两个声部（见 has_melody_near 注释）
            near = has_melody_near(i, same_line=run_length >= 3)
            # 「无旋律」必须对所有删除路径成立：此前另有
            # (run_length >= 3 and repeated) 兜底，会在低音旋律附近误删整段
            # （F17：±1.5s 内有 A3，仍被 run 条件删除 18/18）
            return not near and (repeated or run_length >= 2)

        def r2_gap_single(i: int) -> bool:
            if i == 0 or i + 1 >= n:
                return False
            prev_pitch, next_pitch = pitches[i - 1], pitches[i + 1]
            if not (
                prev_pitch >= LOW_BASS_MIN_MIDI
                and next_pitch >= LOW_BASS_MIN_MIDI
                and prev_pitch - pitches[i] >= LOW_BASS_R2_JUMP
                and next_pitch - pitches[i] >= LOW_BASS_R2_JUMP
            ):
                return False
            # 装饰性回环（如 F13 的 C5→G3→C5）：后一个音回到低音前两个音的
            # 同一音高 ±1 时，是旋律短暂下探而不是伴奏，保留
            return not (i >= 2 and abs(next_pitch - pitches[i - 2]) <= 1)

        def r3_tail(i: int) -> bool:
            return i == n - 1 and pitches[i - 1] >= LOW_BASS_MIN_MIDI

        def r4_bass_run(i: int) -> bool:
            a, b = run_bounds(i)
            if b - a + 1 < 2 or a == 0 or b == n - 1:
                return False
            prev_pitch, next_pitch = pitches[a - 1], pitches[b + 1]
            if (
                prev_pitch >= LOW_BASS_MIN_MIDI
                and next_pitch >= LOW_BASS_MIN_MIDI
                and abs(next_pitch - prev_pitch) <= 1
            ):
                # 装饰性回环（如 C5→G3→F3→C5）：低音段结束后回到进入低音段
                # 之前的同一旋律音高 ±1，是旋律短暂下探而不是伴奏，保留
                return False
            return (
                prev_pitch >= LOW_BASS_MIN_MIDI
                and next_pitch >= LOW_BASS_MIN_MIDI
                and prev_pitch - pitches[i] >= LOW_BASS_R4_JUMP
                and next_pitch - pitches[i] >= LOW_BASS_R4_JUMP
            )

        rules = (
            ("r1_ostinato", r1_ostinato),
            ("r2_gap_single", r2_gap_single),
            ("r3_tail", r3_tail),
            ("r4_bass_run", r4_bass_run),
        )
        kept: list[tuple] = []
        counts = {name: 0 for name, _ in rules}
        for i, event in enumerate(selected):
            reason = None
            if low[i]:
                for name, rule in rules:
                    if rule(i):
                        reason = name
                        break
            if reason is not None:
                counts[reason] += 1
                if dump is not None:
                    dump.decide(
                        {
                            "stage": "selection",
                            "reason": reason,
                            "onset_s": round(float(starts[i]), 4),
                            "midi": int(pitches[i]),
                            "register_midi": int(registers[i]),
                        }
                    )
                continue
            kept.append(event)
        dropped = n - len(kept)
        if dropped:
            logger.info(
                f"低音伴奏过滤: {n} → {len(kept)} 个音符 (剔除 {dropped} 个; "
                f"{', '.join(f'{k}={v}' for k, v in counts.items() if v)})"
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
