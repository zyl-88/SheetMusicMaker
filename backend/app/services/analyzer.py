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
            }
        """
        path = str(vocals_wav_path)
        beats, bpm = self._detect_beats_and_tempo(path)
        key_info = self._detect_key(path)
        time_sig = self._infer_time_signature(beats, bpm)

        result = {
            "beats": beats,
            "bpm": bpm,
            "key": key_info,
            "key_abc": self._key_to_abc(key_info),
            "time_signature": time_sig,
        }
        logger.info(
            f"音频分析完成: BPM={bpm:.1f}, "
            f"Key={key_info['tonic']} {key_info['mode']}, "
            f"Time={time_sig}, "
            f"Beats={len(beats)}"
        )
        return result

    def _detect_beats_and_tempo(self, path: str) -> tuple[list[float], float]:
        """检测节拍和速度。优先用 madmom，不可用时降级到 librosa。"""
        try:
            from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
            from madmom.features.tempo import TempoEstimationProcessor
        except ImportError:
            logger.warning(
                "madmom 不可用（未安装或 numpy 2.x 不兼容），"
                "降级到 librosa.beat.beat_track"
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

        # 如果 BPM 不合理，用节拍间隔估算
        if bpm < 40 or bpm > 250:
            if len(beats) > 1:
                intervals = np.diff(beats)
                median_interval = np.median(intervals)
                bpm = 60.0 / median_interval if median_interval > 0 else 120.0
            else:
                bpm = 120.0

        # BPM 翻倍修正：librosa 常将 72 BPM 误检为 144 BPM
        # 如果 BPM > 120 且节拍间隔中位数的两倍仍在合理范围，则减半
        if bpm > 120 and len(beats) > 4:
            intervals = np.diff(beats)
            median_interval = np.median(intervals)
            halved_bpm = 60.0 / (median_interval * 2) if median_interval > 0 else bpm
            # 如果减半后 BPM 在 60-120 范围内，且节拍间隔分布支持减半
            if 60 <= halved_bpm <= 120:
                # 检查间隔是否接近半拍（说明实际是半拍被检为全拍）
                half_beat_interval = median_interval * 2
                # 统计有多少间隔接近 half_beat_interval
                close_to_double = np.sum(
                    np.abs(intervals - half_beat_interval) < half_beat_interval * 0.2
                )
                if close_to_double > len(intervals) * 0.3:
                    bpm = halved_bpm
                    logger.info(f"BPM 翻倍修正: {bpm * 2:.1f} → {bpm:.1f}")

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

        # 如果 BPM 不合理，用节拍间隔估算
        if bpm < 40 or bpm > 250:
            if len(beat_times) > 1:
                intervals = np.diff(beat_times)
                median_interval = np.median(intervals)
                bpm = 60.0 / median_interval if median_interval > 0 else 120.0
            else:
                bpm = 120.0

        # BPM 翻倍修正：librosa 常将 72 BPM 误检为 144 BPM
        if bpm > 120 and len(beat_times) > 4:
            intervals = np.diff(beat_times)
            median_interval = np.median(intervals)
            halved_bpm = 60.0 / (median_interval * 2) if median_interval > 0 else bpm
            if 60 <= halved_bpm <= 120:
                half_beat_interval = median_interval * 2
                close_to_double = np.sum(
                    np.abs(intervals - half_beat_interval) < half_beat_interval * 0.2
                )
                if close_to_double > len(intervals) * 0.3:
                    bpm = halved_bpm
                    logger.info(f"BPM 翻倍修正: {bpm * 2:.1f} → {bpm:.1f}")

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
        """将调性转为 ABC 调性标记。

        Examples:
            C major → "C"
            G major → "G"
            A minor → "Am"
            F# major → "F#"
        """
        tonic = key_info["tonic"]
        mode = key_info["mode"]
        if mode == "minor":
            return f"{tonic}m"
        return tonic
