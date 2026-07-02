"""C 谱号乐器音域配置。

频率范围直接传入 basic-pitch 的 minimum_frequency/maximum_frequency，
在转录阶段即过滤超出音域的音符。
"""
from typing import Any

INSTRUMENTS: dict[str, dict[str, Any]] = {
    "universal": {
        "name": "通用乐谱 (Universal)",
        "min_freq": 27.5,      # A0 — 钢琴最低音
        "max_freq": 4186.01,   # C8 — 钢琴最高音
        "midi_low": 21,        # A0
        "midi_high": 108,      # C8
        "abc_clef": "treble",
    },
    "flute": {
        "name": "长笛 (Flute)",
        "min_freq": 261.63,    # C4
        "max_freq": 2349.32,   # D7
        "midi_low": 60,
        "midi_high": 98,
        "abc_clef": "treble",
    },
    "violin": {
        "name": "小提琴 (Violin)",
        "min_freq": 196.00,    # G3
        "max_freq": 2637.02,   # E7
        "midi_low": 55,
        "midi_high": 100,
        "abc_clef": "treble",
    },
    "recorder": {
        "name": "竖笛 (Recorder)",
        "min_freq": 261.63,    # C4
        "max_freq": 2349.32,   # D7
        "midi_low": 60,
        "midi_high": 98,
        "abc_clef": "treble",
    },
    "oboe": {
        "name": "双簧管 (Oboe)",
        "min_freq": 233.08,    # Bb3
        "max_freq": 1567.98,   # G6
        "midi_low": 58,
        "midi_high": 91,
        "abc_clef": "treble",
    },
}


def get_instrument(instrument_id: str) -> dict[str, Any]:
    """获取乐器配置，不存在则抛出 ValueError。"""
    if instrument_id not in INSTRUMENTS:
        raise ValueError(f"不支持的乐器: {instrument_id}")
    return INSTRUMENTS[instrument_id]


def list_instruments() -> list[dict[str, Any]]:
    """返回乐器列表（前端选择用）。"""
    return [
        {
            "id": key,
            "name": cfg["name"],
            "min_freq": cfg["min_freq"],
            "max_freq": cfg["max_freq"],
        }
        for key, cfg in INSTRUMENTS.items()
    ]
