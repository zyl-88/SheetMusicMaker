"""DEBUG_DUMP=1 时的中间产物旁路落盘。

默认关闭：未设置 DEBUG_DUMP 时 create_recorder() 返回 None，调用方只在
`if dump is not None` 分支里做额外记录，识别/量化逻辑完全不变。

启用后把每个阶段的原始数据写入 storage/debug/<task_id>/（可用
DEBUG_DUMP_DIR 覆盖根目录），便于核对音符数增长、拍号/调性、量化边界
等中间结果。
"""
import json
import logging
import os
from pathlib import Path
from typing import Any

from app.config import STORAGE_DIR

logger = logging.getLogger("sheet_music_maker.debug_dump")

_TRUTHY = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """DEBUG_DUMP 是否开启（默认关闭）。"""
    return os.environ.get("DEBUG_DUMP", "").strip().lower() in _TRUTHY


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def notes_payload(events: list[tuple]) -> list[dict[str, Any]]:
    """note_events → 可读 JSON（秒级时间 + MIDI + 振幅）。"""
    payload = []
    for start, end, midi, *rest in events:
        amp = rest[0] if rest else None
        payload.append(
            {
                "start_s": round(float(start), 4),
                "end_s": round(float(end), 4),
                "dur_s": round(float(end) - float(start), 4),
                "midi": int(round(float(midi))),
                "amp": round(float(amp), 4) if isinstance(amp, (int, float)) else amp,
            }
        )
    return payload


class DebugRecorder:
    """把一次任务的中间产物写入独立目录。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.warnings: list[str] = []
        self.decisions: list[dict[str, Any]] = []

    def write(self, name: str, payload: Any) -> None:
        try:
            path = self.directory / f"{name}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:  # 观测失败不影响主流程
            logger.warning(f"debug dump 写入失败 {name}: {exc}")

    def notes(self, name: str, events: list[tuple]) -> None:
        self.write(name, {"count": len(events), "events": notes_payload(events)})

    def decide(self, entry: dict[str, Any]) -> None:
        self.decisions.append(entry)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning(f"[debug_dump] {message}")

    def finish(self, metrics: dict[str, Any]) -> None:
        metrics = dict(metrics)
        metrics["warnings"] = list(self.warnings)
        self.write("decisions", self.decisions)
        self.write("metrics", metrics)
        self.write("warnings", self.warnings)


def create_recorder(task_id: str) -> DebugRecorder | None:
    """DEBUG_DUMP 关闭时返回 None（零开销）。"""
    if not is_enabled():
        return None
    base = Path(os.environ.get("DEBUG_DUMP_DIR") or (STORAGE_DIR / "debug"))
    return DebugRecorder(base / task_id)
