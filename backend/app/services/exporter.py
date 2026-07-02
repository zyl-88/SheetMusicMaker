"""乐谱导出服务：ABC / MusicXML / MIDI。

- MusicXML: music21 stream.write("musicxml") — 原生支持
- MIDI: music21 stream.write("midi") — 原生支持
- ABC: 专用构建器从量化数据直接生成 abcjs 兼容字符串
"""
import logging
from pathlib import Path
from typing import Any

from app.services.analyzer import NOTE_NAMES

logger = logging.getLogger("sheet_music_maker.exporter")

# 自然音级（无升降号）
NATURAL_PCS = {0, 2, 4, 5, 7, 9, 11}
# 升号顺序（五度圈）：F, C, G, D, A, E, B → 音高级 6, 1, 8, 3, 10, 5, 0
SHARP_SEQUENCE = [6, 1, 8, 3, 10, 5, 0]
# 降号顺序：B, E, A, D, G, C, F → 音高级 10, 3, 8, 1, 6, 0, 5
FLAT_SEQUENCE = [10, 3, 8, 1, 6, 0, 5]

# 大调主音 → 升号数（正）或降号数（负）
MAJOR_KEY_SIGNATURES = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}
# 小调主音 → 对应大调主音（相对大调）
MINOR_RELATIVE_MAJOR = {
    "A": "C", "E": "G", "B": "D", "F#": "A", "C#": "E", "G#": "B", "D#": "F#",
    "D": "F", "G": "Bb", "C": "Eb", "F": "Ab", "Bb": "Db", "Eb": "Gb",
    "A#": "C#", "D#": "F#", "G#": "B",
}

# 音高级 → 升号写法的音名
SHARP_LETTER = {0: "C", 1: "C", 2: "D", 3: "D", 4: "E", 5: "F", 6: "F", 7: "G", 8: "G", 9: "A", 10: "A", 11: "B"}
# 音高级 → 降号写法的音名
FLAT_LETTER = {0: "C", 1: "D", 2: "D", 3: "E", 4: "E", 5: "F", 6: "G", 7: "G", 8: "A", 9: "A", 10: "B", 11: "B"}


class ScoreExporter:
    """乐谱导出器。"""

    def export_all(
        self,
        m21_stream: Any,
        analysis: dict[str, Any],
        title: str,
        output_dir: str | Path,
    ) -> dict[str, str]:
        """导出全部格式。

        Returns:
            {
                "abc": ABC 字符串（直接返回内容）,
                "musicxml_path": 文件路径,
                "midi_path": 文件路径,
            }
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # ABC（直接返回字符串）
        abc_str = self._export_abc(m21_stream, analysis, title)

        # MusicXML
        musicxml_path = str(output_dir / f"{title}.musicxml")
        m21_stream.write("musicxml", fp=musicxml_path)

        # MIDI
        midi_path = str(output_dir / f"{title}.mid")
        m21_stream.write("midi", fp=midi_path)

        logger.info(f"导出完成: ABC({len(abc_str)}字符), MusicXML, MIDI")

        return {
            "abc": abc_str,
            "musicxml_path": musicxml_path,
            "midi_path": midi_path,
        }

    def _export_abc(self, m21_stream: Any, analysis: dict, title: str) -> str:
        """专用 ABC 构建器：从 music21 Stream 生成 abcjs 兼容 ABC 字符串。"""
        key_info = analysis.get("key", {"tonic": "C", "mode": "major"})
        bpm = analysis.get("bpm", 120.0)
        time_sig = analysis.get("time_signature", "4/4")
        key_abc = analysis.get("key_abc", "C")

        # 头部
        lines = [
            "X:1",
            f"T:{title}",
            f"M:{time_sig}",
            "L:1/8",  # 默认音符时值：八分音符
            f"Q:1/4={int(bpm)}",
            f"K:{key_abc}",
        ]

        # 获取调号的升降音高级集合
        key_sharps, key_flats = self._get_key_accidentals(key_info["tonic"], key_info["mode"])
        use_flats = bool(key_flats)

        # 遍历 Stream 中的音符/休止符
        from music21 import note as m21note

        # 获取按 offset 排序的音符和休止符
        notes_and_rests = m21_stream.flat.notesAndRests

        # 拍号信息用于小节线
        beats_per_measure = self._parse_beats_per_measure(time_sig)
        bar_length = float(beats_per_measure)  # 四分音符单位

        # 构建 ABC 音符串
        music_parts: list[str] = []
        current_bar_pos = 0.0  # 当前小节内位置（四分音符单位）

        for element in notes_and_rests:
            ql = element.quarterLength
            if ql <= 0:
                continue

            if isinstance(element, m21note.Rest):
                abc_note = "z"
            elif isinstance(element, m21note.Note):
                midi_num = element.pitch.midi
                abc_note = self._midi_to_abc_note(midi_num, key_sharps, key_flats, use_flats)
            else:
                continue

            abc_dur = self._quarter_length_to_abc_duration(ql)
            token = abc_note + abc_dur

            # 小节线处理
            new_pos = current_bar_pos + ql
            if new_pos >= bar_length - 0.01:
                # 到达小节末尾
                music_parts.append(token + " | ")
                current_bar_pos = new_pos - bar_length
                if current_bar_pos < 0:
                    current_bar_pos = 0.0
            else:
                music_parts.append(token + " ")
                current_bar_pos = new_pos

        # 合并音符串
        music_str = "".join(music_parts).strip()
        if not music_str.endswith("|"):
            music_str += " |]"

        # 每 4 小节换行（ABC 约定）
        music_str = music_str.replace("| | ", "|\n")
        # 确保每行不超过约 80 字符
        result_lines = []
        current_line = ""
        for part in music_str.split(" "):
            if len(current_line) + len(part) + 1 > 80:
                result_lines.append(current_line.strip())
                current_line = part + " "
            else:
                current_line += part + " "
        if current_line.strip():
            result_lines.append(current_line.strip())

        return "\n".join(lines + result_lines)

    def _midi_to_abc_note(
        self,
        midi_num: int,
        key_sharps: set,
        key_flats: set,
        use_flats: bool,
    ) -> str:
        """将 MIDI 音符号转为 ABC 音名（含八度标记和临时记号）。"""
        pitch_class = midi_num % 12
        octave = midi_num // 12 - 1  # MIDI 60 = C4

        # 确定音名和临时记号
        if pitch_class in NATURAL_PCS:
            # 自然音级，无需临时记号
            letter = SHARP_LETTER[pitch_class]
            accidental = ""
        elif pitch_class in key_sharps:
            # 该音高级在调号中已升，无需临时记号
            letter = SHARP_LETTER[pitch_class]
            accidental = ""
        elif pitch_class in key_flats:
            # 该音高级在调号中已降，无需临时记号
            letter = FLAT_LETTER[pitch_class]
            accidental = ""
        else:
            # 需要临时记号
            if use_flats:
                letter = FLAT_LETTER[pitch_class]
                accidental = "_"
            else:
                letter = SHARP_LETTER[pitch_class]
                accidental = "^"

        # 八度标记
        # 八度 4 (MIDI 60-71): 大写无标记 → C, D, E, F, G, A, B
        # 八度 3 (MIDI 48-59): 大写 + 逗号 → C,, D,, ...
        # 八度 5 (MIDI 72-83): 小写无标记 → c, d, e, f, g, a, b
        # 八度 6 (MIDI 84-95): 小写 + 撇号 → c', d', ...
        if octave <= 3:
            abc_note = letter.upper() + "," * (4 - octave)
        elif octave == 4:
            abc_note = letter.upper()
        elif octave == 5:
            abc_note = letter.lower()
        else:  # octave >= 6
            abc_note = letter.lower() + "'" * (octave - 5)

        return accidental + abc_note

    def _quarter_length_to_abc_duration(self, ql: float) -> str:
        """将 quarterLength 转为 ABC 时值符号（L:1/8 时默认为八分音符=0.5）。"""
        # 量化到最近的 1/16 (0.25 四分音符)
        ql = round(ql * 4) / 4
        if ql <= 0:
            ql = 0.25

        # L:1/8 时默认时值 = 0.5 四分音符
        multiplier = ql / 0.5  # = ql * 2

        if multiplier == 1.0:
            return ""
        elif multiplier == int(multiplier) and multiplier > 1:
            return str(int(multiplier))
        elif multiplier == 0.5:
            return "/"
        elif multiplier == 0.25:
            return "//"
        else:
            # 用分数表示，如 3/2 (附点八分), 5/2 等
            num = round(multiplier * 2)
            return f"{num}/2"

    def _get_key_accidentals(self, tonic: str, mode: str) -> tuple[set, set]:
        """获取调号的升号/降号音高级集合。

        Returns:
            (sharps_set, flats_set)
        """
        # 小调转相对大调
        if mode == "minor":
            tonic = MINOR_RELATIVE_MAJOR.get(tonic, "C")

        # 处理 "Bb" 等降号主音（分析器返回的是升号写法，这里做兼容）
        # 分析器 NOTE_NAMES 使用 "C#", "D#" 等升号写法
        # 但 MAJOR_KEY_SIGNATURES 也包含 "Bb", "Eb" 等降号写法
        # 如果 tonic 不在 MAJOR_KEY_SIGNATURES 中，尝试转换
        if tonic not in MAJOR_KEY_SIGNATURES:
            # 尝试将升号转为降号等价
            sharp_to_flat = {"A#": "Bb", "D#": "Eb", "G#": "Ab", "C#": "Db", "F#": "Gb"}
            tonic = sharp_to_flat.get(tonic, "C")

        sig = MAJOR_KEY_SIGNATURES.get(tonic, 0)

        if sig > 0:
            return set(SHARP_SEQUENCE[:sig]), set()
        elif sig < 0:
            return set(), set(FLAT_SEQUENCE[:abs(sig)])
        else:
            return set(), set()

    def _parse_beats_per_measure(self, time_sig: str) -> int:
        """解析拍号的小节数（如 4/4 → 4, 3/4 → 3）。"""
        try:
            return int(time_sig.split("/")[0])
        except (ValueError, IndexError):
            return 4
