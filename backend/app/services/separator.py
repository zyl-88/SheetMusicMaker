"""Demucs 人声分离服务。

使用 Demucs 4.0.1 底层 API（get_model + apply_model）分离音频，提取 vocals 轨。
注意：demucs.api 模块在 PyPI 4.0.1 中不存在，需使用底层 API。
"""
import logging
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from app.config import (
    DEMUCS_DEVICE,
    DEMUCS_MODEL,
    DEMUCS_SEGMENT,
    DEMUCS_SHIFTS,
)

logger = logging.getLogger("sheet_music_maker.separator")


class VocalSeparator:
    """Demucs 人声分离器。"""

    def __init__(self) -> None:
        from demucs.pretrained import get_model

        logger.info(f"加载 Demucs 模型: {DEMUCS_MODEL}...")
        self.model = get_model(DEMUCS_MODEL)
        self.device = DEMUCS_DEVICE
        self.model.to(self.device)
        self.sample_rate = self.model.samplerate  # 44100
        self.sources = self.model.sources  # ['drums','bass','other','vocals']
        logger.info(
            f"Demucs 分离器初始化完成 (model={DEMUCS_MODEL}, device={self.device}, "
            f"sr={self.sample_rate}, sources={self.sources})"
        )

    def _load_audio(self, audio_path: str) -> "torch.Tensor":
        """加载音频文件为 torch.Tensor [channels, samples]，采样率转为 44100Hz。

        优先用 soundfile（WAV/FLAC/MP3），失败时回退 torchaudio（M4A 等）。
        """
        import torch

        wav = None
        sr = None

        # 方案 1: soundfile（支持 WAV/FLAC/MP3，无需 ffmpeg）
        try:
            data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
            # data 形状 [samples, channels]，转置为 [channels, samples]
            wav = data.T  # [channels, samples]
            logger.debug(f"soundfile 加载成功: {audio_path}, sr={sr}, shape={wav.shape}")
        except Exception as e:
            logger.debug(f"soundfile 无法读取 {audio_path}: {e}，尝试 torchaudio")

        # 方案 2: torchaudio（支持 M4A 等，需 ffmpeg）
        if wav is None:
            import torchaudio
            try:
                wav_t, sr = torchaudio.load(audio_path)
                wav = wav_t.numpy()  # [channels, samples]
                logger.debug(f"torchaudio 加载成功: {audio_path}, sr={sr}, shape={wav.shape}")
            except Exception as e:
                raise RuntimeError(
                    f"无法加载音频文件 {audio_path}: soundfile 和 torchaudio 均失败。"
                    f"soundfile 错误已记录，torchaudio 错误: {e}"
                ) from e

        # 确保是二维 [channels, samples]
        if wav.ndim == 1:
            wav = np.expand_dims(wav, axis=0)

        # 采样率转换（如果需要）
        if sr != self.sample_rate:
            import librosa
            logger.info(f"重采样: {sr}Hz → {self.sample_rate}Hz")
            # librosa.resample 接受 [channels, samples] 并按 axis=-1 重采样
            wav = librosa.resample(wav, orig_sr=sr, target_sr=self.sample_rate, axis=-1)

        # 单声道转立体声（Demucs 期望立体声输入）
        if wav.shape[0] == 1:
            wav = np.repeat(wav, 2, axis=0)

        return torch.from_numpy(wav.astype(np.float32))

    def separate(
        self,
        audio_path: str | Path,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> str:
        """分离音频，提取人声，保存为 WAV 文件。

        Args:
            audio_path: 输入音频路径
            progress_callback: 进度回调函数（保留接口，当前未使用）

        Returns:
            保存的 vocals WAV 文件路径
        """
        import torch
        from demucs.apply import apply_model

        audio_path = str(audio_path)
        output_path = str(Path(audio_path).parent / "vocals.wav")

        logger.info(f"开始分离音频: {audio_path}")

        # 1. 加载音频
        wav = self._load_audio(audio_path)  # [channels, samples]
        logger.info(f"音频加载完成: shape={wav.shape}, sr={self.sample_rate}")

        # 2. Demucs 归一化
        ref = wav.mean(0)
        wav_norm = (wav - ref.mean()) / ref.std()

        # 3. 增加 batch 维度: [1, channels, samples]
        wav_batch = wav_norm[None].to(self.device)

        # 4. 应用模型分离
        #    num_workers=0 避免 Windows multiprocessing spawn 问题
        #    split=True 对长音频分段处理，避免内存溢出
        logger.info("运行 Demucs 模型分离...")
        estimates = apply_model(
            self.model,
            wav_batch,
            shifts=DEMUCS_SHIFTS,
            split=True,
            overlap=0.25,
            device=self.device,
            num_workers=0,  # Windows 安全
            segment=DEMUCS_SEGMENT,
            progress=False,
        )
        # estimates 形状: [batch, num_sources, channels, samples]

        # 5. 反归一化
        estimates = estimates * ref.std() + ref.mean()

        # 6. 提取 vocals
        vocals_idx = self.sources.index("vocals")
        vocals = estimates[0, vocals_idx]  # [channels, samples]

        # 7. 转为 numpy [samples, channels]
        vocals = vocals.cpu().numpy()
        if vocals.ndim == 2 and vocals.shape[0] < vocals.shape[1]:
            vocals = vocals.T  # → [samples, channels]

        # 确保二维
        if vocals.ndim == 1:
            vocals = np.column_stack([vocals, vocals])

        # 8. 保存为 WAV（44.1kHz, 16-bit）
        sf.write(output_path, vocals, self.sample_rate, subtype="PCM_16")
        duration = vocals.shape[0] / self.sample_rate
        logger.info(
            f"人声分离完成，保存至: {output_path} "
            f"(采样率={self.sample_rate}, 时长={duration:.1f}s)"
        )

        return output_path
