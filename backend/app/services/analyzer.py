"""音频分析服务：节拍/速度检测 + 调性检测。

- madmom: RNNBeatProcessor → DBNBeatTrackingProcessor → 节拍时间戳
- madmom: TempoEstimationProcessor → BPM
- librosa: chroma_cqt → Krumhansl-Schmuckler 算法 → 24 调性识别
"""
import logging
from pathlib import Path
from typing import Any

import numpy as np

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


class AudioAnalyzer:
    """音频分析器：节拍/速度/调性。"""

    def analyze(self, vocals_wav_path: str | Path) -> dict[str, Any]:
        """分析音频，返回节拍、速度、调性、拍号。

        Args:
            vocals_wav_path: 人声 WAV 文件路径

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
        beats, bpm = self._detect_beats_and_tempo(path)
        key_info = self._detect_key(path)
        time_sig = self._infer_time_signature(beats, bpm)
        warnings = self._bpm_warnings(beats, bpm)

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

        导出使用每小节 tempo 图后，乐谱内部会与上报 BPM 完全自洽；如果 BPM 本身
        检测错误（倍速/半速），从谱面上反而更难察觉，因此这里必须留一条明示。
        """
        if len(beats) < 4:
            return ["bpm 置信度低：节拍序列过短，无法用节拍间隔校验速度"]
        intervals = np.diff(np.asarray(beats, dtype=float))
        intervals = intervals[intervals > 0]
        if intervals.size == 0:
            return ["bpm 置信度低：节拍序列无有效间隔"]
        median_interval = float(np.median(intervals))
        bpm_beats = 60.0 / median_interval
        ratio = bpm / bpm_beats if bpm_beats > 0 else 1.0

        warnings: list[str] = []
        if ratio > 1.25 or ratio < 0.8:
            warnings.append(
                f"bpm 置信度低：上报 {bpm:.1f} 与节拍间隔估计 {bpm_beats:.1f} "
                f"相差 {ratio:.2f}x，疑似倍速/半速"
            )
        elif ratio > 1.1 or ratio < 0.9:
            warnings.append(
                f"bpm 置信度低：上报 {bpm:.1f} 与节拍间隔估计 {bpm_beats:.1f} 不一致"
            )
        if float(np.std(intervals / median_interval)) > 0.25:
            warnings.append("bpm 置信度低：节拍间隔波动大（节奏不稳或检测抖动）")
        return warnings

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

    def _infer_time_signature(self, beats: list[float], bpm: float) -> str:
        """推断拍号。默认 4/4。"""
        # MVP 阶段默认 4/4
        # 后续可根据节拍强度模式推断 3/4 等
        return "4/4"

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
