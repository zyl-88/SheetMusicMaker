"""乐谱导出服务：ABC / MusicXML / MIDI。

- MusicXML: music21 stream.write("musicxml") — 原生支持
- MIDI: music21 stream.write("midi") — 原生支持
- ABC: 专用构建器从量化数据直接生成 abcjs 兼容字符串
"""
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("sheet_music_maker.exporter")

# 自然音级 → 字母
NATURAL_LETTER = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
NATURAL_PCS = set(NATURAL_LETTER)
# 字母 → 自然音级
LETTER_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# 升号顺序（五度圈）：F, C, G, D, A, E, B → 音高级 6, 1, 8, 3, 10, 5, 0
SHARP_SEQUENCE = [6, 1, 8, 3, 10, 5, 0]
# 降号顺序：B, E, A, D, G, C, F → 音高级 10, 3, 8, 1, 6, 0, 5
FLAT_SEQUENCE = [10, 3, 8, 1, 6, 0, 5]

# 大调主音 → 升号数（正）或降号数（负）
MAJOR_KEY_SIGNATURES = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}
# 小调主音 → 对应大调主音（相对大调），键为分析器规范化后的调名
MINOR_RELATIVE_MAJOR = {
    "C": "Eb", "C#": "E", "D": "F", "Eb": "Gb", "E": "G", "F": "Ab",
    "F#": "A", "G": "Bb", "G#": "B", "A": "C", "Bb": "Db", "B": "D",
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
        """专用 ABC 构建器：从 music21 Stream 生成 abcjs 兼容 ABC 字符串。

        关键点：
        - 音符跨越小节线时必须拆成两个音符并用连音线 ``-`` 连接，否则小节时值会错乱；
        - 小节位置用取模累加，不能用 ``新位置 - 一小节长度``（长音符/长休止会累积漂移）。
        """
        bpm = analysis.get("bpm", 120.0)
        time_sig = analysis.get("time_signature", "4/4")
        key_abc = analysis.get("key_abc", "C")

        # 头部
        header = [
            "X:1",
            f"T:{title}",
            f"M:{time_sig}",
            "L:1/8",  # 默认音符时值：八分音符
            f"Q:1/4={int(round(bpm))}",
            f"K:{key_abc}",
        ]

        # 调号升降音高级集合（与头部 K: 完全一致）
        key_sharps, key_flats = self._get_key_accidentals(key_abc)
        use_flats = bool(key_flats) and not key_sharps

        from music21 import note as m21note

        beats_per_measure = self._parse_beats_per_measure(time_sig)
        bar_length = float(beats_per_measure)  # 四分音符单位
        EPS = 1e-4

        parts: list[str] = []
        current_bar_pos = 0.0  # 当前小节内位置（四分音符单位）
        bars_in_line = 0

        for element in m21_stream.flat.notesAndRests:
            try:
                ql = float(element.quarterLength)
            except (TypeError, ValueError):
                continue
            if ql <= 0:
                continue

            if isinstance(element, m21note.Rest):
                symbol, is_rest = "z", True
            elif isinstance(element, m21note.Note):
                symbol = self._midi_to_abc_note(
                    element.pitch.midi, key_sharps, key_flats, use_flats
                )
                is_rest = False
            else:
                continue

            remaining = ql
            while remaining > EPS:
                space = bar_length - current_bar_pos
                if remaining >= space - EPS:
                    # 填满当前小节；若音符还有剩余，用连音线连到下一小节
                    remaining -= space
                    token = symbol + self._quarter_length_to_abc_duration(space)
                    tie = (not is_rest) and remaining > EPS
                    parts.append(token + ("-" if tie else ""))
                    parts.append("|")
                    current_bar_pos = 0.0
                    bars_in_line += 1
                    if bars_in_line % 4 == 0:
                        parts.append("\n")
                else:
                    token = symbol + self._quarter_length_to_abc_duration(remaining)
                    parts.append(token)
                    current_bar_pos += remaining
                    remaining = 0.0

        # 拼装乐谱正文（每行不超过约 80 字符）
        body = ""
        line_len = 0
        for part in parts:
            if part == "\n":
                body += "\n"
                line_len = 0
                continue
            if line_len + len(part) + 1 > 80:
                body += "\n"
                line_len = 0
            body += part + " "
            line_len += len(part) + 1
        body = body.strip()

        if not body:
            body = "z |]"
        elif body.endswith("|"):
            body = body[:-1].rstrip() + " |]"
        elif not body.endswith("|]"):
            body += " |]"

        return "\n".join(header + body.splitlines())

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

        # 选择音名拼写：自然音级优先；变化音按调号倾向选择升/降拼写
        if pitch_class in NATURAL_PCS:
            letter = NATURAL_LETTER[pitch_class]
        elif use_flats:
            letter = FLAT_LETTER[pitch_class]
        else:
            letter = SHARP_LETTER[pitch_class]

        accidental = self._accidental_for(pitch_class, letter, key_sharps, key_flats)

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

    @staticmethod
    def _accidental_for(
        pitch_class: int, letter: str, key_sharps: set, key_flats: set
    ) -> str:
        """计算某音名在给定调号下所需的临时记号（含还原号 ``=``）。

        ABC 的调号会作用到对应字母上，因此：
        - 调号已经把该字母升高/降低到目标音级 → 不需要记号；
        - 目标音级正是该字母的自然音，但调号改变了它 → 需要还原号 ``=``；
        - 否则按半音差给 ``^`` / ``_``（必要时双升/双降）。
        """
        base = LETTER_BASE[letter]
        if (base + 1) % 12 in key_sharps:
            key_alter = 1
        elif (base - 1) % 12 in key_flats:
            key_alter = -1
        else:
            key_alter = 0

        effective = (base + key_alter) % 12
        if effective == pitch_class:
            return ""
        if base == pitch_class:
            return "="

        delta = (pitch_class - effective + 6) % 12 - 6
        return {1: "^", -1: "_", 2: "^^", -2: "__"}.get(delta, "=")

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

    def _get_key_accidentals(self, key_abc: str) -> tuple[set, set]:
        """根据 ABC 调名获取调号升/降号音高级集合。

        Args:
            key_abc: ABC 调名，如 "C" / "Bb" / "F#m" / "Am"

        Returns:
            (sharps_set, flats_set)
        """
        mode = "minor" if key_abc.endswith("m") else "major"
        tonic = key_abc[:-1] if mode == "minor" else key_abc

        if mode == "minor":
            # 小调按相对大调的调号处理
            tonic = MINOR_RELATIVE_MAJOR.get(tonic, "C")

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
