"""P0 合成音频 fixture 生成器（纯新增文件，不修改产品代码）。

用法：
    python backend/tests/fixtures/generate_fixtures.py --gt-only
    python backend/tests/fixtures/generate_fixtures.py
    python backend/tests/fixtures/generate_fixtures.py --only F11,F13
    python backend/tests/fixtures/generate_fixtures.py --list

约定：
- GT JSON 只用标准库生成（--gt-only 不导入 numpy/soundfile）。
- 音频合成依赖 numpy + soundfile，输出到 test_audio/fixtures/。
- 随机性来自 np.random.default_rng(SEED + 序号)，结果可复现。
- 权威时间是「拍」，onset_s/dur_s 由 bpm 或 tempo_map 换算得到。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[2]
AUDIO_DIR = REPO_ROOT / "test_audio" / "fixtures"
SEED = 20260106
SAMPLE_RATE = 22050

DEFAULT_ACCEPTANCE: dict[str, Any] = {
    "recall_min": 0.7,
    "precision_min": 0.7,
    "f1_min": 0.7,
    "bass_leakage_max": 0.25,
    "octave_error_rate_max": 0.2,
    "octave_jumps_per_8bars_max": 2.0,
    "note_count_deviation_max": 0.3,
    "boundary_pitch_ratio_max": 0.1,
    "bpm_tolerance_ratio": 0.02,
    "require_time_signature": True,
    "require_key": True,
    "diagnostic": False,
    "known_limitations": [],
}

DEFAULT_MATCHING: dict[str, Any] = {
    "onset_tolerance_s": 0.05,
    "duration_tolerance_ratio": 0.25,
    "pitch_tolerance_semitones": 0,
    "match_roles": ["melody"],
}


def A(**overrides: Any) -> dict[str, Any]:
    """在默认验收口径上叠加覆盖项。"""
    merged = dict(DEFAULT_ACCEPTANCE)
    merged.update(overrides)
    return merged


@dataclass
class Note:
    """GT 音符，时间单位为拍。"""

    start: float
    dur: float
    midi: int
    role: str = "melody"
    gain_db: float = 0.0


@dataclass
class Spec:
    """一个 fixture 的完整定义。"""

    id: str
    kind: str
    instrument: str
    bpm: float
    time_signature: str
    key: str
    notes: list[Note]
    role: str = "melody_line"
    tags: list[str] = field(default_factory=list)
    measures: int = 4
    engine: str = "melody"
    timbre: str = "piano"
    timbre_by_role: dict[str, str] = field(default_factory=dict)
    tempo_map: list[dict[str, Any]] | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    acceptance: dict[str, Any] = field(default_factory=A)
    special: dict[str, Any] = field(default_factory=dict)
    group: str | None = None
    audio_ext: str = ".wav"
    reverb_wet: float = 0.0
    vibrato: bool = True
    matching: dict[str, Any] = field(default_factory=dict)


def n(start: float, dur: float, midi: int, role: str = "melody", gain: float = 0.0) -> Note:
    return Note(float(start), float(dur), int(midi), role, float(gain))


def seq(start: float, dur: float, pitches: list[int], role: str = "melody", gain: float = 0.0) -> list[Note]:
    """连续等时值音符。"""
    return [n(start + i * dur, dur, p, role, gain) for i, p in enumerate(pitches)]


def chord(start: float, dur: float, pitches: list[int], role: str = "harmony", gain: float = 0.0) -> list[Note]:
    """同时发声的和弦音。"""
    return [n(start, dur, p, role, gain) for p in pitches]


def rep(start: float, dur: float, gap: float, midi: int, count: int, role: str = "melody") -> list[Note]:
    """同音反复。"""
    return [n(start + i * (dur + gap), dur, midi, role) for i in range(count)]


def merge(*groups: list[Note]) -> list[Note]:
    out: list[Note] = []
    for g in groups:
        out.extend(g)
    return out


def beat_to_seconds(beat: float, bpm: float, tempo_map: list[dict[str, Any]] | None = None) -> float:
    """拍 → 秒。tempo_map 为分段的恒定速度（每项 {beat, bpm}）。"""
    if not tempo_map:
        return beat * 60.0 / bpm
    points = sorted(tempo_map, key=lambda p: float(p["beat"]))
    seconds = 0.0
    prev_beat = 0.0
    cur_bpm = float(points[0]["bpm"])
    for point in points:
        pb = float(point["beat"])
        if pb <= 0:
            cur_bpm = float(point["bpm"])
            continue
        if beat <= pb:
            return seconds + (beat - prev_beat) * 60.0 / cur_bpm
        seconds += (pb - prev_beat) * 60.0 / cur_bpm
        prev_beat = pb
        cur_bpm = float(point["bpm"])
    return seconds + (beat - prev_beat) * 60.0 / cur_bpm


# --------------------------------------------------------------------------
# fixture 定义
# --------------------------------------------------------------------------
F1 = Spec(
    id="F1",
    kind="scale",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["scale", "sustained", "baseline"],
    measures=2,
    notes=seq(0, 1, [60, 62, 64, 65, 67, 69, 71, 72]),
    acceptance=A(recall_min=0.85, precision_min=0.85),
)

F2 = Spec(
    id="F2",
    kind="time_signature_3_4",
    instrument="universal",
    bpm=120.0,
    time_signature="3/4",
    key="C major",
    role="melody_line",
    tags=["waltz", "time_signature", "bass_bounce"],
    measures=4,
    notes=merge(
        [n(0, 2, 72), n(2, 1, 76), n(3, 2, 74), n(5, 1, 67),
         n(6, 2, 72), n(8, 1, 69), n(9, 2, 67), n(11, 1, 64)],
        [n(0, 1, 48, "bass"), n(3, 1, 48, "bass"), n(6, 1, 48, "bass"), n(9, 1, 48, "bass")],
    ),
    acceptance=A(recall_min=0.75, precision_min=0.8, require_time_signature=False,
                 known_limitations=["P2 已知限制：analyzer 拍号恒返回 4/4"]),
)

F3 = Spec(
    id="F3",
    kind="time_signature_6_8",
    instrument="universal",
    bpm=120.0,
    time_signature="6/8",
    key="G major",
    role="melody_line",
    tags=["compound_meter", "eighth_notes"],
    measures=2,
    notes=seq(0, 0.5, [67, 69, 71, 72, 74, 76, 74, 72, 71, 69, 67, 64]),
    acceptance=A(recall_min=0.7, precision_min=0.75, require_time_signature=False,
                 known_limitations=["P2 已知限制：analyzer 拍号恒返回 4/4"]),
)

F4 = Spec(
    id="F4",
    kind="repeated_pitch_short_notes",
    instrument="universal",
    bpm=120.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["merge_gap", "60ms", "90ms", "120ms", "core"],
    measures=2,
    notes=merge(
        rep(0.0, 0.12, 0.04, 72, 4),
        rep(2.0, 0.18, 0.06, 72, 4),
        rep(4.0, 0.24, 0.06, 72, 4),
    ),
    segments=[
        {"label": "60ms", "beats": [0.0, 2.0]},
        {"label": "90ms", "beats": [2.0, 4.0]},
        {"label": "120ms", "beats": [4.0, 6.0]},
    ],
    special={
        "segment_note_counts": {
            "diagnostic": True,
            "expected": {"melody": [1, 1, 1], "basic_pitch": [0, 0, 4]},
            "note": "未执行校准：理论推算，首次运行后按实测更新",
        },
        "short_note_eval": {
            "decision": "只评 onset+pitch，不评时值",
            "reason": (
                "真值时值 60/90ms 低于 basic-pitch 的 minimum_note_length=100ms，"
                "在 duration ±25% 口径下结构性不可能命中；保留原 60/90/120ms 音频设计"
                "（用于观察同音反复合并），只把评分口径改为 onset+pitch。"
            ),
            "decided_at": "2026-11-07",
            "decided_by": "验收方（独立验收）",
        },
    },
    matching={"duration_check": False},
    acceptance=A(recall_min=0.6, precision_min=0.5, note_count_deviation_max=0.5,
                 note="F4 重点：MERGE_GAP_THRESHOLD=0.08s 对短音符的影响；"
                      "评分只看 onset+pitch（见 special.short_note_eval）"),
)

F5 = Spec(
    id="F5",
    kind="melody_with_chords",
    instrument="universal",
    bpm=96.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["chords", "nms", "harmony_leakage"],
    measures=2,
    notes=merge(
        [n(0, 2, 72), n(2, 2, 74), n(4, 2, 76), n(6, 2, 79)],
        chord(0, 2, [60, 64, 67], "harmony", -3.0),
        chord(2, 2, [62, 66, 69], "harmony", -3.0),
        chord(4, 2, [64, 67, 71], "harmony", -3.0),
        chord(6, 2, [60, 64, 67], "harmony", -3.0),
    ),
    acceptance=A(precision_min=0.7, recall_min=0.7, bass_leakage_max=0.3),
)

F6 = Spec(
    id="F6",
    kind="low_register_melody",
    instrument="universal",
    bpm=80.0,
    time_signature="4/4",
    key="A minor",
    role="melody_line",
    tags=["low_register", "melody_floor_220hz", "diagnostic"],
    measures=2,
    notes=seq(0, 1, [45, 47, 48, 47, 45, 48]),
    acceptance=A(recall_min=0.0, diagnostic=True,
                 known_limitations=["MELODY_MIN_FREQ=220Hz 会把 A2–C3 整条旋律判为伴奏"]),
)

F7 = Spec(
    id="F7",
    kind="vocal_with_louder_band",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="A minor",
    role="melody_line",
    tags=["vocal", "basic_pitch", "band_masking"],
    measures=2,
    engine="basic_pitch",
    timbre="vocal",
    timbre_by_role={"harmony": "pad"},
    notes=merge(
        seq(0, 1, [64, 67, 69, 72, 72, 69, 67, 64], "melody"),
        chord(0, 4, [57, 60, 64], "harmony", 4.0),
        chord(4, 4, [53, 57, 60], "harmony", 4.0),
    ),
    acceptance=A(recall_min=0.6, precision_min=0.75, bass_leakage_max=0.3, octave_error_rate_max=0.15),
)

F7_LONG = Spec(
    id="F7-long",
    kind="vocal_with_louder_band_long",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="A minor",
    role="melody_line",
    tags=["vocal", "basic_pitch", "band_masking", "long_form", "split_of_F7"],
    measures=10,
    engine="basic_pitch",
    timbre="vocal",
    timbre_by_role={"harmony": "pad"},
    notes=merge(
        [n(rep * 8 + i, 1, p)
         for rep in range(5) for i, p in enumerate([64, 67, 69, 72, 72, 69, 67, 64])],
        *[chord(rep * 8 + half * 4, 4,
                [57, 60, 64] if half == 0 else [53, 57, 60], "harmony", 4.0)
          for rep in range(5) for half in range(2)],
    ),
    special={
        "split_of": "F7",
        "rationale": (
            "F7 只有 5.3s，librosa 节拍检测退化为 Beats=1、BPM 117.5（真值 100），"
            "量化网格错位会把 0.6s 的旋律音切成 1/16 碎片。本样本把同一动机重复 5 遍到 ~24s，"
            "用于在节拍可检测的前提下单独判断“人声路径的音高与数量是否可用”；"
            "原 F7 保留作短片长历史对照。"
        ),
        "decided_at": "2026-11-07",
        "decided_by": "验收方（独立验收）",
    },
    acceptance=A(recall_min=0.6, precision_min=0.75, bass_leakage_max=0.3,
                 octave_error_rate_max=0.15,
                 note="F7-long 重点：24s 人声+更响伴奏带，检验节拍可检测时的旋律音高与数量"),
)

F8 = Spec(
    id="F8",
    kind="tempo_map",
    instrument="universal",
    bpm=90.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["accelerando", "tempo_drift"],
    measures=2,
    tempo_map=[{"beat": 0.0, "bpm": 80.0}, {"beat": 2.0, "bpm": 95.0}, {"beat": 4.0, "bpm": 110.0}],
    notes=seq(0, 1, [60, 62, 64, 65, 67, 69, 71, 72]),
    acceptance=A(recall_min=0.7, bpm_tolerance_ratio=0.25,
                 known_limitations=["曲中变速；bpm 按容差放宽"]),
)

F9_C = Spec(
    id="F9_C",
    group="F9",
    kind="key_variant_major",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["key_detection", "major"],
    measures=2,
    notes=seq(0, 1, [60, 64, 67, 72, 67, 64, 60, 64]),
    acceptance=A(recall_min=0.7, precision_min=0.7),
)

F9_Am = Spec(
    id="F9_Am",
    group="F9",
    kind="key_variant_minor",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="A minor",
    role="melody_line",
    tags=["key_detection", "minor"],
    measures=2,
    notes=seq(0, 1, [57, 60, 64, 69, 64, 60, 57, 60]),
    acceptance=A(recall_min=0.7, precision_min=0.7),
)

F10 = Spec(
    id="F10",
    kind="format_m4a",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["m4a", "container", "deferred"],
    measures=2,
    notes=F1.notes,
    audio_ext=".m4a",
    acceptance=A(recall_min=0.8, precision_min=0.8),
)

F11 = Spec(
    id="F11",
    kind="piano_left_hand_louder",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["left_hand_6db_louder", "register", "core"],
    measures=2,
    notes=merge(
        seq(0, 1, [72, 74, 76, 77, 79, 76, 72, 74], "melody"),
        [n(0, 2, 48, "bass", 6.0), n(2, 2, 55, "bass", 6.0),
         n(4, 2, 45, "bass", 6.0), n(6, 2, 48, "bass", 6.0)],
    ),
    acceptance=A(recall_min=0.8, precision_min=0.7, bass_leakage_max=0.15, octave_error_rate_max=0.1,
                 note="F11 重点：左手伴奏比右手旋律响 6dB，NMS 会偏向左手"),
)

F12 = Spec(
    id="F12",
    kind="solo_staccato_octave_jumps",
    instrument="violin",
    bpm=120.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["staccato", "octave_jumps", "g3_boundary", "core"],
    measures=2,
    timbre="violin",
    notes=[n(0, 0.5, 55), n(1, 0.5, 72), n(2, 0.5, 84),
           n(3, 0.5, 79), n(4, 0.5, 72), n(5, 0.5, 55), n(6, 0.5, 60)],
    acceptance=A(recall_min=0.7, precision_min=0.7, octave_jumps_per_8bars_max=2.5, octave_error_rate_max=0.2,
                 note="F12 重点：G3(196Hz) 与 C6 之间的八度跳进、staccato 断奏"),
)

F12_CLEAN = Spec(
    id="F12-clean",
    kind="solo_staccato_octave_jumps_clean",
    instrument="violin",
    bpm=120.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["staccato", "octave_jumps", "g3_boundary", "core", "split_of_F12"],
    measures=2,
    timbre="violin",
    vibrato=False,
    notes=F12.notes,
    matching={"duration_check": False},
    special={
        "split_of": "F12",
        "rationale": (
            "F12 原样本的 violin 音色带 ±1.4 半音颤音，0 半音容差下无法区分"
            "“素材诱导的音高偏差”与“产品漏检”。本样本去掉颤音、保持 0 半音容差，"
            "专门检验八度跳进选线；颤音鲁棒性另见 F12-vibrato。原 F12 保留作历史对照。"
        ),
        "duration_policy": (
            "关闭时值检查：basic-pitch 对 250ms 断奏音输出约 0.313s（+25.2%），"
            "恰好在 ±25% 边界外 1ms，会把“音高+onset 全对”的 legacy 结果判成 0。"
            "本样本只评 onset+pitch（时值漂移另见 F4/F12 原样本）。"
        ),
        "decided_at": "2026-11-07",
        "decided_by": "验收方（独立验收）",
    },
    acceptance=A(recall_min=0.7, precision_min=0.7, octave_jumps_per_8bars_max=2.5,
                 octave_error_rate_max=0.2,
                 note="F12-clean 重点：无颤音、0 半音容差下的 G3↔C6 八度跳进与 staccato"),
)

F12_VIBRATO = Spec(
    id="F12-vibrato",
    kind="solo_staccato_octave_jumps_vibrato",
    instrument="violin",
    bpm=120.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["staccato", "octave_jumps", "g3_boundary", "vibrato_robustness", "split_of_F12"],
    measures=2,
    timbre="violin",
    vibrato=True,
    notes=F12.notes,
    matching={"pitch_tolerance_semitones": 1, "duration_check": False},
    special={
        "split_of": "F12",
        "rationale": (
            "保留 ±1.4 半音颤音，音高容差显式放宽到 ±1 半音（写进 matching），"
            "用来单独衡量颤音鲁棒性；音高精确度不再用本样本考核。"
        ),
        "duration_policy": (
            "关闭时值检查：与 F12-clean 相同，250ms 断奏被 basic-pitch 报成 ~0.313s，"
            "±25% 容差会掩盖音高/漏检的真实差异。"
        ),
        "decided_at": "2026-11-07",
        "decided_by": "验收方（独立验收）",
    },
    acceptance=A(recall_min=0.7, precision_min=0.7, octave_jumps_per_8bars_max=2.5,
                 octave_error_rate_max=0.2,
                 known_limitations=["颤音导致 ±1 半音音高抖动，容差已显式放宽到 ±1"],
                 note="F12-vibrato 重点：颤音鲁棒性（±1 半音容差），不考核精确音高"),
)

F13 = Spec(
    id="F13",
    kind="melody_short_dip",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["register_dip", "g3_below_220hz", "core"],
    measures=2,
    notes=[n(0, 1, 72), n(1, 1, 74), n(2, 0.5, 55),
           n(2.5, 0.5, 72), n(3, 1, 76), n(4, 1, 77),
           n(5, 1, 79), n(6, 1, 72), n(7, 1, 74)],
    segments=[{"label": "dip", "beats": [2.0, 3.0]}],
    acceptance=A(recall_min=0.75, precision_min=0.7,
                 segment_recall_min={"dip": 0.6},
                 segment_recall_strategy={"dip": "dp_register"},
                 note="F13 重点：旋律短暂下探 C5→G3→C5，legacy 220Hz 下限会丢音；dp_register 应保留"),
)

F14 = Spec(
    id="F14",
    kind="reverb_heavy",
    instrument="universal",
    bpm=100.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["reverb", "room_40pct"],
    measures=2,
    reverb_wet=0.4,
    notes=F1.notes,
    acceptance=A(recall_min=0.75, precision_min=0.65, octave_error_rate_max=0.25,
                 note="F14 重点：强混响下的音符切分稳定性"),
)

F15 = Spec(
    id="F15",
    kind="inter_note_gaps",
    instrument="universal",
    bpm=120.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["gap_fill", "0.1_0.3_0.6_beat"],
    measures=1,
    notes=[n(0.0, 0.5, 72), n(0.6, 0.5, 72), n(1.4, 0.5, 72), n(2.5, 0.5, 72)],
    special={
        "expected_merge": {
            "diagnostic": True,
            "expected_count": 3,
            "note": "0.1 拍间隙(80ms@120bpm)小于 MERGE_GAP_THRESHOLD，理论上应合并；未执行校准",
        }
    },
    acceptance=A(recall_min=0.6, precision_min=0.6, note_count_deviation_max=0.5),
)


F16 = Spec(
    id="F16",
    kind="fast_tempo_4_4",
    instrument="universal",
    bpm=150.0,
    time_signature="4/4",
    key="C major",
    role="melody_line",
    tags=["fast_tempo", "150_bpm", "tactus_halving_reverse", "accent_period_4"],
    measures=8,
    timbre="piano",
    # 每拍一个音、每 4 拍一个重音：脉冲层面的重音周期是 4（不是 3/6），
    # 专门用来反向检验「150 BPM 4/4 快歌不应被 1/2 降速成 75」。
    notes=[
        n(i, 0.9, [72, 73, 74, 76, 77, 79, 81, 84][i % 8],
          gain=(8.0 if i % 4 == 0 else 0.0))
        for i in range(32)
    ],
    acceptance=A(
        recall_min=0.7, precision_min=0.7, bpm_tolerance_ratio=0.05,
        note="F16 反向样本：150 BPM 4/4，重音周期 4；证据门控应阻止 1/2 降速",
    ),
)


F17 = Spec(
    id="F17",
    kind="low_register_melody_big_jumps",
    instrument="universal",
    bpm=120.0,
    time_signature="4/4",
    key="A minor",
    role="melody_line",
    tags=["low_register", "a2_c3", "octave_jump", "same_pitch_repeats",
          "adversarial", "low_bass_filter"],
    measures=4,
    timbre="piano",
    # 对抗样本：旋律主体在 A2–C3（45–52），靠 A3（57）做 12 半音大跳，
    # 中间夹同音反复。R1/R2/R4 若把「低音区真旋律」当成伴奏，会在这里误删。
    notes=seq(0, 0.5, [
        45, 45, 48, 52,
        57, 52, 48, 45,
        45, 48, 52, 57,
        52, 48, 45, 45,
    ] * 2),
    special={
        "purpose": "针对 LOW_BASS_FILTER 的 R1–R4：低音区旋律 + ≥12 半音大跳 + 同音反复",
        "rationale": (
            "F13 只覆盖「高音区旋律短暂下探」，F6 的整条低音旋律没有大跳。"
            "本样本让旋律本身就在 A2–C3，并用 A3 制造 12 半音跳进与同音反复，"
            "检验 R2/R4 不会把低音大跳旋律误判为间隙单音/低音段。"
        ),
    },
    acceptance=A(
        recall_min=0.7, precision_min=0.7, f1_min=0.7, require_key=False,
        note=(
            "F17 对抗样本：A2–C3 低音区旋律含 12 半音跳进与同音反复，低音过滤不得误删。"
            "不校验调性：旋律只有 A2/C3/E3/A3 四个音，调性检测对这类稀疏低音素材"
            "不可靠（与 F6 同一已知限制）。"
        ),
    ),
)


FIXTURES: list[Spec] = [
    F1, F2, F3, F4, F5, F6, F7, F8, F9_C, F9_Am, F10, F11, F12, F13, F14, F15,
    # 追加样本放在末尾，避免改变既有 fixture 的合成 RNG 序号（index）
    F12_CLEAN, F12_VIBRATO, F7_LONG, F16, F17,
]


# --------------------------------------------------------------------------
# ground truth / manifest
# --------------------------------------------------------------------------
def build_gt(spec: Spec, audio_rel: str) -> dict[str, Any]:
    """把 Spec 展开为定稿的 ground truth JSON。"""
    notes: list[dict[str, Any]] = []
    for note in sorted(spec.notes, key=lambda x: (x.start, x.midi)):
        onset_s = beat_to_seconds(note.start, spec.bpm, spec.tempo_map)
        end_s = beat_to_seconds(note.start + note.dur, spec.bpm, spec.tempo_map)
        notes.append({
            "start": round(note.start, 6),
            "dur": round(note.dur, 6),
            "onset_s": round(onset_s, 6),
            "dur_s": round(end_s - onset_s, 6),
            "midi": note.midi,
            "role": note.role,
            "gain_db": note.gain_db,
        })

    segments = []
    for segment in spec.segments:
        start_beat, end_beat = segment["beats"]
        segments.append({
            "label": segment["label"],
            "beats": [start_beat, end_beat],
            "start_s": round(beat_to_seconds(start_beat, spec.bpm, spec.tempo_map), 6),
            "end_s": round(beat_to_seconds(end_beat, spec.bpm, spec.tempo_map), 6),
        })

    expected = {
        "bpm": spec.bpm,
        "time_signature": spec.time_signature,
        "key": spec.key,
        "measures": spec.measures,
        "role": spec.role,
        "tags": spec.tags,
        "notes": notes,
    }
    if spec.tempo_map:
        expected["tempo_map"] = spec.tempo_map

    return {
        "schema_version": "1.0",
        "id": spec.id,
        "group": spec.group,
        "kind": spec.kind,
        "instrument": spec.instrument,
        "audio": audio_rel,
        "engine": "basic_pitch" if spec.engine == "basic_pitch" else "melody",
        "expected": expected,
        "matching": {**DEFAULT_MATCHING, **spec.matching},
        "acceptance": spec.acceptance,
        "segments": segments,
        "special": spec.special,
        "synthesis": {
            "sample_rate": SAMPLE_RATE,
            "seed": SEED,
            "timbre": spec.timbre,
            "timbre_by_role": spec.timbre_by_role,
            "reverb_wet": spec.reverb_wet,
        },
    }


def build_manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "seed": SEED,
        "sample_rate": SAMPLE_RATE,
        "audio_dir": "test_audio/fixtures",
        "gt_dir": "backend/tests/fixtures",
        "total": len(entries),
        "fixtures": entries,
    }


def manifest_entry(spec: Spec, audio_rel: str) -> dict[str, Any]:
    return {
        "id": spec.id,
        "group": spec.group,
        "kind": spec.kind,
        "instrument": spec.instrument,
        "engine": "basic_pitch" if spec.engine == "basic_pitch" else "melody",
        "audio": audio_rel,
        "gt": f"backend/tests/fixtures/{spec.id}.json",
        "priority": "core" if "core" in spec.tags else "normal",
        "diagnostic": bool(spec.acceptance.get("diagnostic")),
        "notes": len(spec.notes),
        "measures": spec.measures,
        "tags": spec.tags,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 音频合成（仅默认模式，延迟导入 numpy / soundfile）
# --------------------------------------------------------------------------
TIMBRE_PARTIALS: dict[str, list[float]] = {
    "piano": [1.0, 0.5, 0.3, 0.2, 0.12, 0.08, 0.05],
    "flute": [1.0, 0.22, 0.1, 0.05],
    "violin": [1.0, 0.6, 0.4, 0.28, 0.18, 0.1],
    "vocal": [1.0, 0.45, 0.28, 0.16, 0.09],
    "pad": [1.0, 0.35, 0.18, 0.08],
}


def synth_note(rng: Any, dur_s: float, midi: int, timbre: str, gain_db: float,
               vibrato: bool = True) -> Any:
    """加法合成单个音符：谐波 + ADSR + 可选颤音 + 随机微失谐。"""
    import numpy as np

    n_samples = max(1, int(dur_s * SAMPLE_RATE))
    t = np.arange(n_samples) / SAMPLE_RATE
    f0 = 440.0 * 2 ** ((midi - 69) / 12.0) * (1.0 + rng.normal(0.0, 0.0015))
    partials = TIMBRE_PARTIALS.get(timbre, TIMBRE_PARTIALS["piano"])

    # 颤音：器乐/人声用轻微频率调制；vibrato=False 时生成干净音高样本
    if vibrato and timbre in ("vocal", "violin"):
        t_mod = t + 0.0025 * np.sin(2 * np.pi * 5.5 * t + rng.uniform(0, 2 * np.pi))
    else:
        t_mod = t

    signal = np.zeros(n_samples, dtype=np.float64)
    for k, amp in enumerate(partials, start=1):
        if f0 * k >= SAMPLE_RATE / 2 - 200:
            break
        phase = rng.uniform(0, 2 * np.pi)
        signal += amp * np.sin(2 * np.pi * f0 * k * t_mod + phase)

    attack = min(int(0.012 * SAMPLE_RATE), n_samples // 2)
    release = min(int(0.04 * SAMPLE_RATE), n_samples // 2)
    env = np.ones(n_samples, dtype=np.float64)
    if attack > 0:
        env[:attack] = np.linspace(0.0, 1.0, attack)
    if release > 0:
        env[-release:] = np.linspace(1.0, 0.0, release)
    if timbre == "piano":
        env *= np.exp(-t / 0.8)
    signal *= env

    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal /= peak
    return signal * (0.5 * 10 ** (gain_db / 20.0))


def apply_reverb(rng: Any, data: Any, wet: float) -> Any:
    """指数衰减噪声脉冲响应的 FFT 卷积混响。"""
    import numpy as np

    if wet <= 0:
        return data
    ir_len = int(0.8 * SAMPLE_RATE)
    ir = rng.normal(0.0, 1.0, ir_len) * np.exp(-np.arange(ir_len) / (0.25 * SAMPLE_RATE))
    norm = float(np.sqrt(np.sum(ir ** 2)))
    ir /= max(1e-9, norm)
    n_fft = 1 << (len(data) + ir_len - 1).bit_length()
    spec = np.fft.rfft(data, n_fft) * np.fft.rfft(ir, n_fft)
    wet_signal = np.fft.irfft(spec, n_fft)[: len(data)]
    return (1.0 - wet) * data + wet * wet_signal


def synthesize(spec: Spec, out_path: Path, index: int) -> None:
    """按 Spec 合成整段音频并写 WAV。"""
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(SEED + index)
    durations = [
        beat_to_seconds(note.start + note.dur, spec.bpm, spec.tempo_map)
        for note in spec.notes
    ]
    total_s = max(durations) + 0.5
    buffer = np.zeros(int(total_s * SAMPLE_RATE) + 1, dtype=np.float64)

    for note in spec.notes:
        onset_s = beat_to_seconds(note.start, spec.bpm, spec.tempo_map)
        end_s = beat_to_seconds(note.start + note.dur, spec.bpm, spec.tempo_map)
        timbre = spec.timbre_by_role.get(note.role, spec.timbre)
        block = synth_note(rng, end_s - onset_s, note.midi, timbre, note.gain_db,
                           vibrato=spec.vibrato)
        offset = int(onset_s * SAMPLE_RATE)
        buffer[offset: offset + len(block)] += block

    buffer = apply_reverb(rng, buffer, spec.reverb_wet)
    peak = float(np.max(np.abs(buffer)))
    if peak > 0:
        buffer = buffer / peak * 0.9
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), buffer.astype(np.float32), SAMPLE_RATE, subtype="PCM_16")


def transcode_m4a(wav_path: Path, m4a_path: Path) -> bool:
    """尝试用 ffmpeg 转 M4A；无 ffmpeg 时返回 False（不视为错误）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(f"[F10] 未找到 ffmpeg，保留 WAV：{wav_path.name}（M4A 转码标记为 deferred）")
        return False
    import subprocess

    cmd = [ffmpeg, "-y", "-i", str(wav_path), "-c:a", "aac", "-b:a", "128k", str(m4a_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[F10] ffmpeg 转码失败：{result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown'}")
        return False
    return True


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def select_specs(only: list[str]) -> list[Spec]:
    if not only:
        return list(FIXTURES)
    ids = {item.strip().upper() for item in only if item.strip()}
    selected = [s for s in FIXTURES if s.id.upper() in ids or (s.group and s.group.upper() in ids)]
    unknown = ids - {s.id.upper() for s in selected} - {s.group.upper() for s in selected if s.group}
    if unknown:
        raise SystemExit(f"未知 fixture: {', '.join(sorted(unknown))}")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 P0 音频 fixture 与 ground truth")
    parser.add_argument("--gt-only", action="store_true", help="只写 GT JSON（不导入 numpy/soundfile）")
    parser.add_argument("--only", default="", help="只处理指定 ID，逗号分隔，例如 F11,F13")
    parser.add_argument("--list", action="store_true", help="列出全部 fixture 后退出")
    parser.add_argument("--audio-dir", default=str(AUDIO_DIR), help="音频输出目录")
    parser.add_argument("--gt-dir", default=str(FIXTURE_DIR), help="GT JSON 输出目录")
    args = parser.parse_args(argv)

    if args.list:
        for spec in FIXTURES:
            print(f"{spec.id:<6} {spec.kind:<32} {spec.instrument:<10} engine={spec.engine:<11} notes={len(spec.notes)}")
        return 0

    specs = select_specs(args.only.split(",") if args.only else [])
    audio_dir = Path(args.audio_dir)
    gt_dir = Path(args.gt_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict[str, Any]] = []
    generated_audio = 0

    for index, spec in enumerate(FIXTURES):
        if spec not in specs:
            continue

        ext = spec.audio_ext
        audio_rel = f"test_audio/fixtures/{spec.id}{ext}"
        audio_path = audio_dir / f"{spec.id}{ext}"

        if not args.gt_only:
            wav_path = audio_dir / f"{spec.id}.wav"
            synthesize(spec, wav_path, index)
            if ext == ".m4a":
                m4a_path = audio_dir / f"{spec.id}.m4a"
                if transcode_m4a(wav_path, m4a_path):
                    audio_path = m4a_path
                else:
                    audio_path = wav_path
                    audio_rel = f"test_audio/fixtures/{spec.id}.wav"
            generated_audio += 1

        gt = build_gt(spec, audio_rel)
        write_json(gt_dir / f"{spec.id}.json", gt)
        manifest_entries.append(manifest_entry(spec, audio_rel))

    # manifest 按 id 合并，避免 --only 只写回部分 fixture 时丢掉其它条目
    manifest_path = gt_dir / "manifest.json"
    merged: dict[str, dict[str, Any]] = {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            merged = {item["id"]: item for item in old.get("fixtures", [])}
        except (json.JSONDecodeError, KeyError):
            merged = {}
    merged.update({item["id"]: item for item in manifest_entries})
    ordered = [merged[spec.id] for spec in FIXTURES if spec.id in merged]
    ordered += [item for fid, item in merged.items() if fid not in {s.id for s in FIXTURES}]
    write_json(manifest_path, build_manifest(ordered))

    mode = "仅 GT JSON（音频未生成）" if args.gt_only else f"音频 {generated_audio} 个 + GT"
    print(f"完成：{mode}；GT {len(manifest_entries)} 个；目录 {gt_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

