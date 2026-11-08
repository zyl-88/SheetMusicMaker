"""音频分析服务：节拍/速度检测 + 调性检测。

- madmom: RNNBeatProcessor → DBNBeatTrackingProcessor → 节拍时间戳
- madmom: TempoEstimationProcessor → BPM
- librosa: chroma_cqt → Krumhansl-Schmuckler 算法 → 24 调性识别
"""
import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.config import tactus_halving_enabled

logger = logging.getLogger("sheet_music_maker.analyzer")

# Krumhansl-Schmuckler 调性轮廓（12 半音）
KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# ABC 调名规范化：优先选择升降号更少、且 abcjs 支持的写法
# （分析器内部用升号写法，但 "A#"/"D#"/"G#" 等调名在 ABC 中无效或等价于 10/8/9 个升降号）
ABC_MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
ABC_MINOR_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]

# 触拍（tactus）区间：人实际"数拍子"的速度一般落在这里。
# 检测器常把每个八分音符脉冲当成一拍上报，速度就翻倍；超出上限且减半后
# 仍在区间内时，还要有第二证据才降速，避免把真正 140 BPM 以上的快歌误减半。
TACTUS_MIN_BPM = 70.0
TACTUS_MAX_BPM = 140.0

# 第二证据：脉冲层面的重音必须按 3（或 6）个脉冲循环，且明显强于 4 脉冲循环。
# 八分音符细分网格会留下这种"三拍/六拍"循环；真 4/4 快歌的重音周期是 4。
TACTUS_ACCENT_MIN = 0.05
TACTUS_ACCENT_RATIO = 1.5

# 减半证据的「余量」门槛：刚过 1.5 倍只能算勉强成立，超过 2.5 倍才算明确。
# 余量不足时不能悄悄减半，要给用户一条可自行判断的告警。
TACTUS_EVIDENCE_STRONG = 2.5

# 自然音阶音级（相对主音的半音数），用于比较同一主音下大/小调各需多少临时记号
MAJOR_SCALE_STEPS = frozenset({0, 2, 4, 5, 7, 9, 11})
MINOR_SCALE_STEPS = frozenset({0, 2, 3, 5, 7, 8, 10})


class AudioAnalyzer:
    """音频分析器：节拍/速度/调性。"""

    def analyze(
        self,
        vocals_wav_path: str | Path,
        note_events: list | None = None,
    ) -> dict[str, Any]:
        """分析音频，返回节拍、速度、调性、拍号。

        Args:
            vocals_wav_path: 人声 WAV 文件路径
            note_events: 已提取的音符事件 [(start_s, end_s, midi, amp, ...)]，
                用于按谱面重音分组推断拍号；为 None 时拍号退回 4/4

        Returns:
            {
                "beats": [float, ...],  # 节拍时间戳（秒）
                "bpm": float,
                "key": {"tonic": "C", "mode": "major"},
                "key_abc": "C",          # ABC 调性标记
                "time_signature": "4/4",
                "warnings": [str, ...],  # BPM 置信度低等风险提示
            }
        """
        path = str(vocals_wav_path)
        raw_beats, raw_bpm = self._detect_beats_and_tempo(path)
        beats, bpm, halved, evidence_ratio = self._halve_tactus(
            raw_beats, raw_bpm, note_events
        )
        if halved:
            logger.info(
                f"节拍脉冲按 1/2 降速校正: BPM={bpm:.1f}, 拍数={len(beats)}"
            )
        key_info = self._detect_key(path)
        key_info = self._prefer_notation_friendly_mode(key_info, note_events)
        time_sig = self._infer_time_signature(beats, raw_beats, halved, note_events)
        warnings = self._bpm_warnings(beats, bpm)
        warnings.extend(
            self._tactus_warnings(raw_bpm, bpm, halved, evidence_ratio, time_sig)
        )

        result = {
            "beats": beats,
            "bpm": bpm,
            "key": key_info,
            "key_abc": self._key_to_abc(key_info),
            "time_signature": time_sig,
            "warnings": warnings,
        }
        logger.info(
            f"音频分析完成: BPM={bpm:.1f}, "
            f"Key={key_info['tonic']} {key_info['mode']}, "
            f"Time={time_sig}, "
            f"Beats={len(beats)}"
        )
        for message in warnings:
            logger.warning(message)
        return result

    @staticmethod
    def _bpm_warnings(beats: list[float], bpm: float) -> list[str]:
        """BPM 置信度检查：把倍速/半速、节拍不稳等风险显式报出来。

        导出使用每小节 tempo 图后，乐谱内部会与当前 BPM 完全自洽；如果 BPM 本身
        检测错误（倍速/半速），从谱面上反而更难察觉，因此这里必须留一条明示。
        文案面向最终用户：说明差多少、两边各是多少，不暴露内部术语。
        """
        if len(beats) < 4:
            return ["节拍序列过短，无法校验速度，速度标记仅供参考"]
        intervals = np.diff(np.asarray(beats, dtype=float))
        intervals = intervals[intervals > 0]
        if intervals.size == 0:
            return ["节拍序列没有有效间隔，无法校验速度，速度标记仅供参考"]
        median_interval = float(np.median(intervals))
        bpm_beats = 60.0 / median_interval
        ratio = bpm / bpm_beats if bpm_beats > 0 else 1.0

        warnings: list[str] = []
        if ratio > 1.25 or ratio < 0.8:
            factor = ratio if ratio >= 1.0 else 1.0 / ratio
            warnings.append(
                f"检测到的速度可能与实际相差约 {factor:.1f} 倍："
                f"当前记谱 {bpm:.1f} BPM，节拍间隔更接近 {bpm_beats:.1f} BPM"
            )
        elif ratio > 1.1 or ratio < 0.9:
            warnings.append(
                f"检测到的速度与节拍间隔略有出入："
                f"当前记谱 {bpm:.1f} BPM，节拍间隔约 {bpm_beats:.1f} BPM"
            )
        if float(np.std(intervals / median_interval)) > 0.25:
            warnings.append("节拍间隔波动较大，速度检测可能不稳定")
        return warnings

    @staticmethod
    def _tactus_warnings(
        raw_bpm: float,
        bpm: float,
        halved: bool,
        evidence_ratio: float | None,
        time_sig: str,
    ) -> list[str]:
        """1/2 降速被触发后，把「这可能是错的」直接告诉用户。

        触发条件（任一成立）：
        - 第二证据余量不足：p3/p4 只是刚过 1.5 倍门槛，没到 2.5 倍；
        - 谱面落在三拍子侧（3/4、6/8）且原速度已超过触拍上限——
          真实的 160 BPM 3/4/6/8 快歌本来就会被误减半。

        真实的 160 BPM 三拍子快歌和 80 BPM 的三拍子慢歌在脉冲层面长得一样，
        只看音频无法 100% 区分，所以这里不假装确定，而是让用户用「听感」判断。
        ``TACTUS_HALVING=off`` 时不降速（halved=False），也就不会出这条告警。
        """
        if not halved:
            return []
        weak_evidence = (
            evidence_ratio is not None and evidence_ratio < TACTUS_EVIDENCE_STRONG
        )
        triple_meter = time_sig in ("3/4", "6/8") and raw_bpm > TACTUS_MAX_BPM
        if not (weak_evidence or triple_meter):
            return []
        return [
            f"检测到的速度可能是 {bpm:.1f} BPM，也可能正是其两倍的 {raw_bpm:.1f} BPM："
            f"同一个节奏可以按这两种速度记谱。谱面按 {bpm:.1f} BPM 生成，"
            f"如果播放时觉得偏慢（节奏被拉长了一倍），请告知。"
        ]

    @classmethod
    def _halve_tactus(
        cls, beats: list[float], bpm: float, note_events: list | None = None
    ) -> tuple[list[float], float, bool, float | None]:
        """把「八分音符脉冲」还原成真实触拍。

        检测器（madmom/librosa 都如此）经常把每个八分音符脉冲当作一拍上报，
        得到的速度是真实触拍的 2 倍。真实曲实测 161.5 BPM 对应 0.372s 的脉冲，
        而人实际数的是 2 个脉冲一拍（约 80.8 BPM）。

        降速需要两个条件同时成立：
        1. 数值：速度超过触拍上限，且减半后仍落在区间内；
        2. 证据：脉冲层面的重音按 3/6 个脉冲循环，且明显强于 4 脉冲循环——
           说明这一层网格是细分，而不是真正的拍。
        只有数值没有证据时不降速（避免把 150 BPM 的 4/4 快歌减成 75）；
        如需整体回退，可用 ``TACTUS_HALVING=off`` 关闭这条校正。
        """
        if bpm <= TACTUS_MAX_BPM or bpm / 2.0 < TACTUS_MIN_BPM:
            return beats, bpm, False, None
        if not tactus_halving_enabled():
            logger.info(f"触拍降速已关闭（TACTUS_HALVING=off），保留 BPM={bpm:.1f}")
            return beats, bpm, False, None
        accents = cls._beat_accents(beats, note_events)
        p3 = cls._periodicity(accents, 3)
        p6 = cls._periodicity(accents, 6)
        p4 = cls._periodicity(accents, 4)
        best = max(p3, p6)
        if best < TACTUS_ACCENT_MIN or best < p4 * TACTUS_ACCENT_RATIO:
            logger.info(
                f"BPM={bpm:.1f} 超出触拍区间，但重音分组不支持八分脉冲"
                f"（p3={p3:.2f} p6={p6:.2f} p4={p4:.2f}），不做 1/2 校正"
            )
            return beats, bpm, False, None
        halved = beats[::2]
        if len(halved) < 4:
            return beats, bpm, False, None
        # 证据余量 = 三/六脉冲循环的自相关 / 四脉冲循环的自相关。
        # 门槛刚过（1.5 倍）时只能算勉强成立，要交给 _tactus_warnings 提醒用户。
        evidence_ratio = best / p4 if p4 > 0 else float("inf")
        return halved, round(bpm / 2.0, 1), True, evidence_ratio

    def _detect_beats_and_tempo(self, path: str) -> tuple[list[float], float]:
        """检测节拍和速度。优先用 madmom，不可用时降级到 librosa。"""
        try:
            from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
            from madmom.features.tempo import TempoEstimationProcessor
        except Exception:
            # numpy 2.x 下 madmom 可能在 import 阶段抛出 ImportError/ValueError/RuntimeError 等，
            # 统一降级到 librosa.beat.beat_track
            logger.warning(
                "madmom 不可用（未安装或 numpy 2.x 不兼容），"
                "降级到 librosa.beat.beat_track",
                exc_info=True,
            )
            return self._detect_beats_librosa(path)

        logger.info("检测节拍 (madmom)...")
        # RNN 起拍激活
        act = RNNBeatProcessor()(path)
        # DBN 节拍追踪
        beat_proc = DBNBeatTrackingProcessor(fps=100)
        beats = beat_proc(act)
        # 速度估计
        tempo_proc = TempoEstimationProcessor(fps=100)
        tempi = tempo_proc(act)
        bpm = float(tempi[0][0]) if len(tempi) > 0 else 120.0

        # 速度与节拍间隔一致性校正：tempo 估计常出现倍/半倍误差
        if len(beats) > 1:
            intervals = np.diff(beats)
            median_interval = float(np.median(intervals))
            if median_interval > 0:
                bpm_beats = 60.0 / median_interval
                if 40.0 <= bpm_beats <= 250.0:
                    ratio = bpm / bpm_beats if bpm_beats > 0 else 1.0
                    # 仅当 tempo 与节拍的倍率约等于 2（或 1/2）时才以节拍间隔为准
                    if ratio > 1.6 or ratio < 0.625:
                        logger.info(
                            f"BPM 与节拍间隔不一致: tempo={bpm:.1f}, "
                            f"节拍间隔估计={bpm_beats:.1f}，采用后者"
                        )
                        bpm = bpm_beats

        if bpm < 40 or bpm > 250:
            bpm = 120.0

        return beats.tolist(), round(bpm, 1)

    def _detect_beats_librosa(self, path: str) -> tuple[list[float], float]:
        """librosa 节拍检测（madmom 降级方案）。

        使用 librosa.beat.beat_track 检测节拍和速度，
        返回格式与 madmom 版本一致。
        """
        import librosa

        logger.info("检测节拍 (librosa)...")
        y, sr = librosa.load(path, sr=22050, mono=True)

        # 节拍追踪:返回 (tempo, beat_frames)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)

        # 帧索引转时间戳（秒）
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)

        # tempo 可能是标量或数组，统一取第一个值
        bpm = float(np.atleast_1d(tempo)[0])

        # 速度与节拍间隔一致性校正（同 madmom 路径）
        if len(beat_times) > 1:
            intervals = np.diff(beat_times)
            median_interval = float(np.median(intervals))
            if median_interval > 0:
                bpm_beats = 60.0 / median_interval
                if 40.0 <= bpm_beats <= 250.0:
                    ratio = bpm / bpm_beats if bpm_beats > 0 else 1.0
                    if ratio > 1.6 or ratio < 0.625:
                        logger.info(
                            f"BPM 与节拍间隔不一致: tempo={bpm:.1f}, "
                            f"节拍间隔估计={bpm_beats:.1f}，采用后者"
                        )
                        bpm = bpm_beats

        if bpm < 40 or bpm > 250:
            bpm = 120.0

        return beat_times.tolist(), round(bpm, 1)

    def _detect_key(self, path: str) -> dict[str, str]:
        """用 librosa + Krumhansl-Schmuckler 检测调性。"""
        import librosa

        logger.info("检测调性...")
        y, sr = librosa.load(path, sr=22050, mono=True)

        # 提取色度图（12 维音高级）
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)  # 12 维平均

        # 对 24 个调性计算 Pearson 相关
        best_score = -1
        best_key = 0  # 0-11: major, 12-23: minor
        for i in range(12):
            # 旋转色度匹配每个主音
            rotated = np.roll(chroma_mean, i)
            # 大调
            score_major = np.corrcoef(rotated, KRUMHANSL_MAJOR)[0, 1]
            if score_major > best_score:
                best_score = score_major
                best_key = i
            # 小调
            score_minor = np.corrcoef(rotated, KRUMHANSL_MINOR)[0, 1]
            if score_minor > best_score:
                best_score = score_minor
                best_key = i + 12

        if best_key < 12:
            return {"tonic": NOTE_NAMES[best_key], "mode": "major"}
        else:
            return {"tonic": NOTE_NAMES[best_key - 12], "mode": "minor"}

    @staticmethod
    def _beat_accents(beat_times: list[float], note_events: list | None) -> np.ndarray:
        """把音符强度按拍窗口累加，得到每一拍的强弱序列。"""
        if not note_events or len(beat_times) < 2:
            return np.zeros(0)
        onsets = np.array([float(e[0]) for e in note_events])
        amps = np.array([float(e[3]) if len(e) > 3 else 1.0 for e in note_events])
        fallback = (beat_times[-1] - beat_times[0]) / (len(beat_times) - 1)
        acc = np.zeros(len(beat_times))
        for i, t in enumerate(beat_times):
            hi = beat_times[i + 1] if i + 1 < len(beat_times) else t + fallback
            mask = (onsets >= t) & (onsets < hi)
            if mask.any():
                acc[i] = float(amps[mask].sum())
        return acc

    @staticmethod
    def _periodicity(accents: np.ndarray, period: int) -> float:
        """强弱序列在给定周期上的自相关（归一化，越大说明重音越按该周期重复）。"""
        if accents.size <= period + 4:
            return 0.0
        centered = accents - accents.mean()
        std = float(centered.std())
        if std <= 0:
            return 0.0
        norm = centered / std
        return float(np.dot(norm[:-period], norm[period:]) / (norm.size - period))

    @staticmethod
    def _scale_accidental_weight(
        tonic_pc: int, mode: str, note_events: list | None
    ) -> float:
        """这份谱在「该主音 + 该调式」下，需要临时记号的音符时值占比。"""
        scale = MINOR_SCALE_STEPS if mode == "minor" else MAJOR_SCALE_STEPS
        weights = np.zeros(12)
        for event in note_events or []:
            weights[int(event[2]) % 12] += max(float(event[1]) - float(event[0]), 0.01)
        total = float(weights.sum())
        if total <= 0:
            return 1.0
        outside = sum(
            float(weights[pc]) for pc in range(12) if (pc - tonic_pc) % 12 not in scale
        )
        return outside / total

    def _prefer_notation_friendly_mode(
        self, key_info: dict[str, str], note_events: list | None
    ) -> dict[str, str]:
        """同一主音下大/小调都说得通时，选临时记号更少的那个。

        色度模板在降级路径（librosa）下对 major/minor 区分不稳：真实曲的
        音级分布按 Krumhansl 小调模板拟合更好，却被判成同主音大调，导出后
        整份谱布满 ``=E``/``=D`` 之类的还原记号。这里用谱面音符自身的时值
        统计做仲裁：只有另一调式的临时记号明显更少（<75%）才改写，避免
        音高检出波动导致来回切换。
        """
        if not note_events or key_info.get("tonic") not in NOTE_NAMES:
            return key_info
        tonic_pc = NOTE_NAMES.index(key_info["tonic"])
        major_w = self._scale_accidental_weight(tonic_pc, "major", note_events)
        minor_w = self._scale_accidental_weight(tonic_pc, "minor", note_events)
        if key_info.get("mode") == "minor":
            if major_w < minor_w * 0.75:
                logger.info(
                    f"调式按谱面音符改写: {key_info['tonic']} minor -> major"
                    f"（临时记号 {minor_w:.0%} -> {major_w:.0%}）"
                )
                return {"tonic": key_info["tonic"], "mode": "major"}
        elif minor_w < major_w * 0.75:
            logger.info(
                f"调式按谱面音符改写: {key_info['tonic']} major -> minor"
                f"（临时记号 {major_w:.0%} -> {minor_w:.0%}）"
            )
            return {"tonic": key_info["tonic"], "mode": "minor"}
        return key_info

    def _infer_time_signature(
        self,
        beats: list[float],
        raw_beats: list[float],
        halved: bool,
        note_events: list | None,
    ) -> str:
        """按重音分组周期推断拍号，默认 4/4。

        用谱面音符强度按拍累加得到强弱序列，再看 3 拍 / 4 拍周期的自相关：
        - 4 拍更突出 → 4/4
        - 3 拍更突出 → 3/4；若八分脉冲层面 3 脉冲一组的倾向明显强于 2 脉冲
          一组（即"三三分组"），则记为 6/8
        拍数太少（<12）或没有音符事件时无法判断重音分组，保守退回 4/4。
        """
        if len(beats) < 12 or not note_events:
            return "4/4"
        accents = self._beat_accents(beats, note_events)
        score3 = self._periodicity(accents, 3)
        score4 = self._periodicity(accents, 4)
        if score3 <= 0.05 or score3 <= score4 * 1.5:
            return "4/4"
        if halved:
            pulse = self._beat_accents(raw_beats, note_events)
            pulse3 = self._periodicity(pulse, 3)
            pulse2 = self._periodicity(pulse, 2)
            if pulse3 > pulse2 * 1.3:
                return "6/8"
        return "3/4"

    def _key_to_abc(self, key_info: dict[str, str]) -> str:
        """将调性转为合法的 ABC 调名。

        分析器内部用升号写法（C#, D#, G#...），但 ABC/abcjs 对 "A#"/"D#"/"G#" 等
        调名支持不良（等价于 10/8/9 个升降号），因此统一规范化为等音调名。

        Examples:
            C major → "C"
            G major → "G"
            A minor → "Am"
            F# major → "F#"
            A# major → "Bb"
            D# minor → "Ebm"
        """
        tonic = key_info["tonic"]
        mode = key_info["mode"]
        pc = NOTE_NAMES.index(tonic) if tonic in NOTE_NAMES else 0

        if mode == "minor":
            return f"{ABC_MINOR_NAMES[pc]}m"
        return ABC_MAJOR_NAMES[pc]
