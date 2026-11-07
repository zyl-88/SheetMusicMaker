"""旋律提取服务（Melodia 风格）。

基于 librosa PYIN 音高追踪 + 旋律轮廓提取，专为从多声部音乐中提取主旋律设计。

算法流程：
1. PYIN 逐帧追踪最强音高（f0 + voiced probability）
2. 构建音高轮廓：分组连续 voiced 帧
3. 轮廓过滤：时长、音高稳定性、平均 salience
4. 旋律线选择：取每帧 salience 最高的轮廓
5. 音符切分：onset detection + 音高跳变检测 → 离散音符
"""
import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.config import (
    MELODY_MIN_FREQ,
    MERGE_GAP_THRESHOLD,
    MIN_MIDI_PITCH,
    MIN_NOTE_AMPLITUDE,
)

logger = logging.getLogger("sheet_music_maker.melody")

# 音符转 MIDI：A4 = 440Hz = MIDI 69
def _hz_to_midi(hz: np.ndarray) -> np.ndarray:
    hz = np.maximum(hz, 1.0)
    return 69.0 + 12.0 * np.log2(hz / 440.0)


class MelodyExtractor:
    """节奏型旋律提取器：PYIN + 轮廓分析 + 音符切分。"""

    def __init__(self) -> None:
        logger.info("旋律提取器初始化完成（librosa PYIN + 轮廓选择）")

    def extract(
        self,
        audio_path: str | Path,
        instrument_config: dict[str, Any],
    ) -> list[tuple]:
        """从多声部音频中提取主旋律。

        Args:
            audio_path: WAV/MP3 文件路径
            instrument_config: 乐器配置 {min_freq, max_freq, midi_low, midi_high}

        Returns:
            note_events: [(start_s, end_s, midi, amplitude, pitch_bend)]
        """
        import librosa

        path = str(audio_path)

        # --- 加载音频 ---
        logger.info(f"加载音频: {path}")
        y, sr = librosa.load(path, sr=22050, mono=True)
        duration_s = len(y) / sr
        logger.info(f"音频: {duration_s:.1f}s, sr={sr}")

        min_freq = instrument_config.get("min_freq", 65)
        max_freq = instrument_config.get("max_freq", 2093)

        # 如果乐器配置的频率下限过低（通用模式 A0=27.5Hz），
        # 用 MELODY_MIN_FREQ 覆盖，排除左手低音伴奏
        if min_freq < MELODY_MIN_FREQ:
            logger.info(
                f"min_freq 从 {min_freq:.0f}Hz 提高到 {MELODY_MIN_FREQ:.0f}Hz "
                f"(过滤低音伴奏)"
            )
            min_freq = MELODY_MIN_FREQ

        # === 第1步：PYIN 音高追踪 ===
        logger.info("PYIN 音高追踪中...")
        fmin = max(min_freq, 50.0)
        fmax = min(max_freq, sr / 2 - 1)

        f0, voiced_flag, voiced_prob = librosa.pyin(
            y,
            sr=sr,
            fmin=fmin,
            fmax=fmax,
            frame_length=2048,
            hop_length=256,
            fill_na=0.0,
        )

        hop_s = 256 / sr  # 每帧秒数
        frame_times = np.arange(len(f0)) * hop_s
        logger.info(f"PYIN 完成: {len(f0)} 帧, hop={hop_s*1000:.0f}ms")

        # === 第2步：构建音高轮廓 ===
        contours = self._build_contours(f0, voiced_flag, voiced_prob, frame_times, hop_s)
        logger.info(f"构建了 {len(contours)} 个音高轮廓")

        # === 第3步：轮廓过滤 ===
        contours = self._filter_contours(contours)
        logger.info(f"过滤后保留 {len(contours)} 个轮廓")

        # === 第4步：旋律线选择 ===
        melody_f0, melody_salience = self._select_melody_line(
            contours, len(f0), frame_times
        )
        logger.info("旋律线选择完成")

        # === 第5步：音符切分 ===
        note_events = self._segment_notes(
            melody_f0, melody_salience, y, sr, frame_times, hop_s, instrument_config
        )
        logger.info(f"音符切分完成: {len(note_events)} 个音符")

        return note_events

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_contours(
        self,
        f0: np.ndarray,
        voiced_flag: np.ndarray,
        voiced_prob: np.ndarray,
        frame_times: np.ndarray,
        hop_s: float,
    ) -> list[dict]:
        """将连续 voiced 帧分组为音高轮廓。

        Returns:
            [{frames: [(idx, time, f0_hz, salience)], ...}]
        """
        contours = []
        current = []
        gap_frames = 0
        max_gap = 3  # 最多容忍 3 帧间隙 (~35ms) 来合并断续轮廓

        for i in range(len(f0)):
            # 直接用 voiced_flag（PYIN 的 voicing 判定），
            # 不叠加 voiced_prob 阈值（钢琴衰减快，prob 普遍偏低）
            if voiced_flag[i] and f0[i] > 0:
                # 有声音帧
                if gap_frames > 0 and gap_frames <= max_gap and current:
                    # 小间隙：继续当前轮廓
                    current.append((i, frame_times[i], f0[i], voiced_prob[i]))
                elif gap_frames > max_gap or not current:
                    # 新轮廓开始
                    if current:
                        contours.append(self._finalize_contour(current))
                    current = [(i, frame_times[i], f0[i], voiced_prob[i])]
                else:
                    current.append((i, frame_times[i], f0[i], voiced_prob[i]))
                gap_frames = 0
            else:
                if current:
                    gap_frames += 1

        if current:
            contours.append(self._finalize_contour(current))

        return contours

    @staticmethod
    def _finalize_contour(frames: list) -> dict:
        """计算轮廓统计特征。"""
        idxs = [f[0] for f in frames]
        times = np.array([f[1] for f in frames])
        pitches = np.array([f[2] for f in frames])
        saliences = np.array([f[3] for f in frames])

        # 中值滤波去除音高野点
        if len(pitches) > 3:
            from scipy.ndimage import median_filter
            pitches_smoothed = median_filter(pitches, size=min(5, len(pitches) // 2 * 2 + 1))
        else:
            pitches_smoothed = pitches

        duration = times[-1] - times[0] + (times[1] - times[0] if len(times) > 1 else 0.01)

        # 音高稳定性（标准差越小越稳定）
        midi_vals = _hz_to_midi(pitches_smoothed)
        pitch_std = float(np.std(midi_vals)) if len(midi_vals) > 1 else 0

        # 平均 salience
        mean_sal = float(np.mean(saliences))

        return {
            "frames": frames,
            "times": times,
            "pitches": pitches_smoothed,
            "saliences": saliences,
            "start_idx": int(idxs[0]),
            "end_idx": int(idxs[-1]),
            "duration_s": duration,
            "pitch_std": pitch_std,
            "mean_salience": mean_sal,
            "n_frames": len(frames),
        }

    def _filter_contours(self, contours: list[dict]) -> list[dict]:
        """过滤低质量轮廓。

        注意：轮廓是按「有声连续性」分组的，一段旋律本身就会跨多个音高
        （八度跳进、级进走句），因此不能用音高标准差当过严的过滤条件，
        否则整句旋律会被丢掉（表现为乐谱大段缺失、只剩零星音符和长休止）。
        """
        filtered = []
        for c in contours:
            # 移除过短轮廓（< 80ms）
            if c["duration_s"] < 0.08:
                continue
            # 仅剔除音高漂移明显异常的噪声轮廓（超过一个八度）
            if c["pitch_std"] > 12.0:
                continue
            # 平均 salience 阈值极低（钢琴 prob 普遍 0.01~0.13）
            if c["mean_salience"] < 0.005:
                continue
            filtered.append(c)

        return filtered

    def _select_melody_line(
        self,
        contours: list[dict],
        n_frames: int,
        frame_times: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """选择每帧的主旋律音高。

        对每帧，从覆盖该帧的轮廓中选择 salience 最高的。
        无人声帧填 0。
        """
        melody_f0 = np.zeros(n_frames, dtype=np.float32)
        melody_sal = np.zeros(n_frames, dtype=np.float32)

        # 按 salience 排序，覆盖到帧数组
        sorted_contours = sorted(contours, key=lambda c: -c["mean_salience"])

        for c in sorted_contours:
            for i, frame_info in enumerate(c["frames"]):
                idx = frame_info[0]
                sal = c["saliences"][i]
                if sal > melody_sal[idx]:
                    melody_sal[idx] = sal
                    melody_f0[idx] = c["pitches"][i]

        return melody_f0, melody_sal

    def _segment_notes(
        self,
        melody_f0: np.ndarray,
        melody_sal: np.ndarray,
        y: np.ndarray,
        sr: int,
        frame_times: np.ndarray,
        hop_s: float,
        instrument_config: dict[str, Any],
    ) -> list[tuple]:
        """将连续旋律线切分为离散音符。

        触发新音符的条件：
        1. 从无声 → 有声
        2. 音高跳变超过阈值（半音以上）
        3. onset detection 检测到起拍
        """
        import librosa

        n_frames = len(melody_f0)
        midi_low = instrument_config.get("midi_low", 36)
        midi_high = instrument_config.get("midi_high", 108)

        # --- RMS 振幅包络（用于力度） ---
        rms = librosa.feature.rms(
            y=y,
            frame_length=2048,
            hop_length=256,
        )[0]
        # 对齐帧数
        min_len = min(n_frames, len(rms))
        rms = rms[:min_len]
        rms_normalized = rms / (rms.max() + 1e-8)

        # --- onset 检测 ---
        onset_frames = librosa.onset.onset_detect(
            y=y,
            sr=sr,
            hop_length=256,
            backtrack=True,
            units="frames",
        )

        # --- 音高跳变检测 ---
        # 逐帧计算相邻帧的音高变化
        midi_vals = np.zeros(n_frames)
        voiced = melody_f0 > 0
        midi_vals[voiced] = _hz_to_midi(melody_f0[voiced])

        # 找出音高跳变帧（> 1.5 半音且从 voiced→voiced）
        pitch_change = np.abs(np.diff(midi_vals, prepend=midi_vals[0]))
        pitch_jump_frames = set()
        for i in range(1, n_frames):
            if voiced[i] and voiced[i - 1] and pitch_change[i] > 1.5:
                pitch_jump_frames.add(i)

        # 合并 onset_frames 和 pitch_jump_frames 作为候选起始点
        note_starts = set(onset_frames)
        note_starts.update(pitch_jump_frames)

        # --- 构建音符 ---
        i = 0
        note_events = []
        while i < n_frames:
            # 跳过无声帧（melody_f0 > 0 即为有声）
            if not voiced[i]:
                i += 1
                continue

            # 音符开始
            note_start_idx = i
            note_start_s = frame_times[i]
            note_pitches = [melody_f0[i]]
            note_sals = [melody_sal[i]]
            note_rmss = [rms_normalized[i] if i < len(rms_normalized) else 0]

            i += 1
            # 扩展音符直到遇到起拍、跳变、或无声
            while i < n_frames:
                # 停止条件：无声
                if not voiced[i]:
                    # 允许 3 帧短暂无声作为连音（~35ms）
                    gap_count = 0
                    j = i
                    while j < n_frames and not voiced[j]:
                        gap_count += 1
                        if gap_count > 3:
                            break
                        j += 1
                    if gap_count > 3 or j >= n_frames:
                        break
                    else:
                        # 短暂间隙，跳过
                        i = j
                        continue

                # 遇到起拍或音高跳变 → 新音符开始
                if i in note_starts and i > note_start_idx + 1:
                    break

                note_pitches.append(melody_f0[i])
                note_sals.append(melody_sal[i])
                note_rmss.append(rms_normalized[i] if i < len(rms_normalized) else 0)
                i += 1

            if not note_pitches:
                continue

            # 音符属性
            note_end_s = frame_times[min(i, n_frames - 1)] + hop_s

            # 取中位音高（抗野点）
            valid_pitches = [p for p in note_pitches if p > 0]
            if not valid_pitches:
                continue

            median_hz = float(np.median(valid_pitches))
            midi_pitch = int(round(_hz_to_midi(median_hz)))

            # 音域钳制
            if midi_pitch < midi_low:
                midi_pitch = midi_low
            elif midi_pitch > midi_high:
                midi_pitch = midi_high

            # 过滤低于 MIN_MIDI_PITCH 的噪声
            if midi_pitch < MIN_MIDI_PITCH:
                continue

            # 振幅
            amp = float(np.mean(note_rmss)) if note_rmss else 0.5
            if amp < MIN_NOTE_AMPLITUDE * 0.5:  # 稍宽松的振幅阈值
                continue

            # 最短时长过滤（80ms）
            duration_s = note_end_s - note_start_s
            if duration_s < 0.08:
                continue

            note_events.append(
                (note_start_s, note_end_s, midi_pitch, amp, None)
            )

        return note_events
