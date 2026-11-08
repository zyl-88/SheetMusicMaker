"""P0 fixture 评测器（纯新增文件，不修改产品代码）。

两种输入方式：
1. --pred-dir DIR：评测已有预测 JSON（文件名或内容里带 fixture_id / strategy）
2. --run：直接调用产品引擎（跳过 Demucs），可选 --dump-dir 落盘供之后对比

支持 --compare 对比 legacy_nms / dp_register 两版策略。

注意：--strategy 只是结果分组标签。在 P1 的 MELODY_LINE_STRATEGY 开关落地前，
产品代码只存在 legacy_nms 一条路径；脚本会在报告里明确标注开关是否已实现，
不会把两份相同结果伪装成"对比结论"。

指标口径：
- note precision/recall：onset ±50ms、音高精确匹配、时值 ±25%
- bass_leakage_rate：预测音匹配到 GT 非旋律音，或低于旋律音区 floor 且未匹配的比例
- octave_error_rate：onset 对得上但音高差 ≥12 半音的比例
- octave_jumps_per_8bars：与 GT 同位置相邻音程相比凭空多出的 ≥12 半音跳变，按 8 小节归一
- note_count_deviation：(预测数 - GT 数) / GT 数
- boundary_pitch_ratio：落在乐器音域上下边界（钳制痕迹）的预测音比例
- key/bpm/time_signature：从预测的 analysis 字段读取，缺失则记为 skipped
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = FIXTURE_DIR.parents[2]
STRATEGIES = ("legacy_nms", "dp_register")

# 与 backend/app/services/instruments.py 保持一致的音域边界（避免纯标准库模式导入产品代码）
INSTRUMENT_BOUNDS: dict[str, tuple[int, int]] = {
    "universal": (21, 108),
    "flute": (60, 98),
    "violin": (55, 100),
    "recorder": (60, 98),
    "oboe": (58, 91),
}

A3_MIDI = 57  # 220Hz 对应的 MIDI（A3）


# --------------------------------------------------------------------------
# 加载与匹配
# --------------------------------------------------------------------------
def load_fixtures(fixtures_dir: Path, only: list[str]) -> list[dict[str, Any]]:
    ids = {item.strip().upper() for item in only if item.strip()}
    fixtures = []
    for path in sorted(fixtures_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("id"):
            continue
        if ids and data["id"].upper() not in ids and str(data.get("group") or "").upper() not in ids:
            continue
        fixtures.append(data)
    return fixtures


def normalize_notes(raw: Any) -> list[dict[str, Any]]:
    """把预测音符统一成 {onset_s, dur_s, midi}。

    支持：
    - [start, end, midi, amplitude]
    - {"onset_s"|"start_s"|"start", "dur_s"|"end_s"|"end", "midi"|"pitch"}
    """
    notes: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            onset, end, midi = float(item[0]), float(item[1]), int(round(float(item[2])))
            dur = max(0.0, end - onset)
        elif isinstance(item, dict):
            onset = float(item.get("onset_s", item.get("start_s", item.get("start", 0.0))))
            if "dur_s" in item or "dur" in item:
                dur = float(item.get("dur_s", item.get("dur", 0.0)))
            else:
                dur = max(0.0, float(item.get("end_s", item.get("end", onset))) - onset)
            midi_value = item.get("midi", item.get("pitch", 0))
            midi = int(round(float(midi_value)))
        else:
            continue
        notes.append({"onset_s": onset, "dur_s": dur, "midi": midi})
    notes.sort(key=lambda n: (n["onset_s"], n["midi"]))
    return notes


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------
def normalize_key(value: Any) -> tuple[str, str] | None:
    """把 {"tonic","mode"} 或 "C major"/"A minor" 统一为 (tonic, mode)。"""
    if not value:
        return None
    if isinstance(value, dict):
        tonic = str(value.get("tonic") or "").strip()
        mode = str(value.get("mode") or "").strip().lower()
    else:
        text = str(value).strip().lower().replace("_", " ")
        tokens = text.split()
        if not tokens:
            return None
        tonic = tokens[0]
        mode = "minor" if any(t in ("minor", "min", "小调") for t in tokens[1:]) else "major"
    if not tonic:
        return None
    tonic = tonic[0].upper() + tonic[1:]
    mode = "minor" if mode in ("minor", "min", "小调") else "major"
    return tonic, mode


def compute_metrics(
    fixture: dict[str, Any],
    entry: dict[str, Any],
    onset_tolerance: float | None,
) -> dict[str, Any]:
    matching = dict(fixture.get("matching") or {})
    if onset_tolerance is not None:
        matching["onset_tolerance_s"] = float(onset_tolerance)
    expected = fixture["expected"]
    gt_notes = expected["notes"]
    roles = matching.get("match_roles") or ["melody"]
    gt_melody = [g for g in gt_notes if g.get("role") in roles]
    gt_other = [g for g in gt_notes if g.get("role") not in roles]
    pred = entry.get("notes") or []

    matches, used_gt, used_pred = greedy_match(gt_melody, pred, matching)
    tp = len(matches)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gt_melody) if gt_melody else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # 伴奏泄漏：匹配到非旋律 GT 音，或落在地板以下且未匹配
    melody_floor = min((g["midi"] for g in gt_melody), default=A3_MIDI)
    floor = melody_floor - 2
    leakage = 0
    other_matches, _, other_used_pred = greedy_match(gt_other, pred, matching) if gt_other else ([], set(), set())
    leakage += len([pi for pi in other_used_pred if pi not in used_pred])
    leakage += sum(1 for pi, p in enumerate(pred) if pi not in used_pred and pi not in other_used_pred and p["midi"] < floor)
    bass_leakage_rate = leakage / len(pred) if pred else 0.0

    # 八度/音高错误：按 onset 对齐后看音高差
    onset_pairs = onset_only_matches(gt_melody, pred, float(matching.get("onset_tolerance_s", 0.05)))
    diffs = [pred[pi]["midi"] - gt_melody[gi]["midi"] for gi, pi in onset_pairs]
    octave_errors = sum(1 for d in diffs if abs(d) >= 12)
    octave_error_rate = octave_errors / len(diffs) if diffs else 0.0

    # 只统计"凭空多出"的八度跳变：GT 同位置本来就有大跳的（F12/F13）不算
    pred_to_gt = {pi: gi for gi, pi in onset_pairs}
    jumps = 0
    for index in range(len(pred) - 1):
        if index not in pred_to_gt or index + 1 not in pred_to_gt:
            continue
        pred_interval = pred[index + 1]["midi"] - pred[index]["midi"]
        gt_interval = (
            gt_melody[pred_to_gt[index + 1]]["midi"] - gt_melody[pred_to_gt[index]]["midi"]
        )
        if abs(pred_interval) >= 12 and abs(gt_interval) < 12:
            jumps += 1
    measures = int(expected.get("measures") or 1)
    octave_jumps_per_8bars = jumps * 8.0 / max(1, measures)

    count_deviation = (len(pred) - len(gt_melody)) / len(gt_melody) if gt_melody else 0.0

    # A3 以下旋律的保留率：用于「整段低音旋律 0 删除」门禁
    gt_below = [gi for gi, g in enumerate(gt_melody) if g["midi"] < A3_MIDI]
    below_hit = sum(1 for gi in gt_below if gi in used_gt)
    below_a3_recall = below_hit / len(gt_below) if gt_below else 1.0

    low, high = INSTRUMENT_BOUNDS.get(fixture.get("instrument"), (21, 108))
    boundary_hits = sum(1 for p in pred if p["midi"] in (low, high))
    boundary_pitch_ratio = boundary_hits / len(pred) if pred else 0.0

    analysis = entry.get("analysis") or {}
    pred_bpm = analysis.get("bpm")
    pred_key = normalize_key(analysis.get("key"))
    pred_time_sig = analysis.get("time_signature")

    return {
        "pred_count": len(pred),
        "gt_count": len(gt_melody),
        "matched": tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "bass_leakage_rate": bass_leakage_rate,
        "octave_error_rate": octave_error_rate,
        "octave_jumps": jumps,
        "octave_jumps_per_8bars": octave_jumps_per_8bars,
        "note_count_deviation": count_deviation,
        "boundary_pitch_ratio": boundary_pitch_ratio,
        "pred_below_220hz_count": sum(1 for p in pred if p["midi"] < A3_MIDI),
        "gt_below_220hz_count": sum(1 for g in gt_melody if g["midi"] < A3_MIDI),
        "below_a3_recall": below_a3_recall,
        "pred_bpm": pred_bpm,
        "pred_key": list(pred_key) if pred_key else None,
        "pred_time_signature": pred_time_sig,
        "expected_bpm": expected["bpm"],
        "expected_key": expected["key"],
        "expected_time_signature": expected["time_signature"],
        "analysis_available": bool(analysis),
        "_matches": matches,
        "_used_gt": sorted(used_gt),
        "_used_pred": sorted(used_pred),
        "_onset_pairs": onset_pairs,
    }


def segment_recall(
    fixture: dict[str, Any], segment: dict[str, Any], metrics: dict[str, Any], matching: dict[str, Any]
) -> tuple[float, int, int]:
    """某分段内的旋律 recall。"""
    gt_notes = [g for g in fixture["expected"]["notes"] if g.get("role") in (matching.get("match_roles") or ["melody"])]
    in_seg = [
        (gi, g) for gi, g in enumerate(gt_notes)
        if segment["start_s"] <= g["onset_s"] < segment["end_s"]
    ]
    used = set(metrics["_used_gt"])
    hit = sum(1 for gi, _ in in_seg if gi in used)
    return (hit / len(in_seg) if in_seg else 1.0), hit, len(in_seg)


# --------------------------------------------------------------------------
# 验收判定
# --------------------------------------------------------------------------
def add_check(
    checks: list[dict[str, Any]],
    name: str,
    value: Any,
    limit: Any,
    mode: str,
    diagnostic: bool = False,
    skipped: bool = False,
    note: str = "",
) -> None:
    if skipped:
        passed = True
    elif mode == "min":
        passed = value >= limit
    elif mode == "max":
        passed = value <= limit
    elif mode == "eq":
        passed = value == limit
    else:
        passed = bool(value)
    checks.append({
        "name": name,
        "value": value,
        "limit": limit,
        "mode": mode,
        "passed": passed,
        "diagnostic": diagnostic,
        "skipped": skipped,
        "note": note,
    })


def evaluate_fixture(
    fixture: dict[str, Any],
    entry: dict[str, Any] | None,
    strategy: str,
    onset_tolerance: float | None,
    strict: bool,
) -> dict[str, Any]:
    fixture_id = fixture["id"]
    acc = fixture.get("acceptance") or {}
    fixture_diagnostic = bool(acc.get("diagnostic"))

    if entry is None:
        return {
            "id": fixture_id,
            "strategy": strategy,
            "status": "NO_PREDICTION",
            "passed": False,
            "metrics": {},
            "checks": [],
        }

    metrics = compute_metrics(fixture, entry, onset_tolerance)
    matching = fixture.get("matching") or {}
    checks: list[dict[str, Any]] = []
    # 单项诊断：acceptance.diagnostic_checks 里的指标只报告、不参与 PASS/FAIL
    diag_checks = set(acc.get("diagnostic_checks") or [])

    def diag(name: str) -> bool:
        return fixture_diagnostic or name in diag_checks

    add_check(checks, "recall", metrics["recall"], acc.get("recall_min", 0.7), "min", diag("recall"))
    add_check(checks, "precision", metrics["precision"], acc.get("precision_min", 0.7), "min", diag("precision"))
    add_check(checks, "f1", metrics["f1"], acc.get("f1_min", 0.7), "min", diag("f1"))
    add_check(checks, "bass_leakage_rate", metrics["bass_leakage_rate"], acc.get("bass_leakage_max", 0.25), "max", diag("bass_leakage_rate"))
    add_check(checks, "octave_error_rate", metrics["octave_error_rate"], acc.get("octave_error_rate_max", 0.2), "max", diag("octave_error_rate"))
    add_check(checks, "octave_jumps_per_8bars", metrics["octave_jumps_per_8bars"], acc.get("octave_jumps_per_8bars_max", 2.0), "max", diag("octave_jumps_per_8bars"))
    add_check(checks, "note_count_deviation", abs(metrics["note_count_deviation"]), acc.get("note_count_deviation_max", 0.3), "max", diag("note_count_deviation"))
    add_check(checks, "boundary_pitch_ratio", metrics["boundary_pitch_ratio"], acc.get("boundary_pitch_ratio_max", 0.1), "max", diag("boundary_pitch_ratio"))
    if acc.get("below_a3_recall_min") is not None:
        add_check(checks, "below_a3_recall", metrics["below_a3_recall"], acc["below_a3_recall_min"], "min",
                  diag("below_a3_recall"))

    # bpm / key / time signature：缺 analysis 时记 skipped，而不是判失败
    ratio = float(acc.get("bpm_tolerance_ratio", 0.02))
    if metrics["pred_bpm"] is None:
        add_check(checks, "bpm", None, ratio, "max", diag("bpm"), skipped=True, note="pred analysis 无 bpm")
    else:
        bpm_dev = abs(float(metrics["pred_bpm"]) - float(metrics["expected_bpm"])) / float(metrics["expected_bpm"])
        add_check(checks, "bpm", bpm_dev, ratio, "max", diag("bpm"))

    if acc.get("require_key", True):
        expected_key = normalize_key(metrics["expected_key"])
        if metrics["pred_key"] is None:
            add_check(checks, "key", None, expected_key, "eq", fixture_diagnostic, skipped=True, note="pred analysis 无 key")
        else:
            pred_key = tuple(metrics["pred_key"])
            add_check(checks, "key", pred_key, expected_key, "eq", fixture_diagnostic)

    if acc.get("require_time_signature", True):
        if metrics["pred_time_signature"] is None:
            add_check(checks, "time_signature", None, metrics["expected_time_signature"], "eq",
                      fixture_diagnostic, skipped=True, note="pred analysis 无 time_signature")
        else:
            add_check(checks, "time_signature", str(metrics["pred_time_signature"]).replace(" ", ""),
                      str(metrics["expected_time_signature"]).replace(" ", ""), "eq", fixture_diagnostic)

    # 分段 recall（可指定只对某个策略生效，用于 F13 legacy→dp_register 的预期差异）
    for segment in fixture.get("segments") or []:
        label = segment["label"]
        min_recall = (acc.get("segment_recall_min") or {}).get(label)
        if min_recall is None:
            continue
        scope = (acc.get("segment_recall_strategy") or {}).get(label)
        rec, hit, total = segment_recall(fixture, segment, metrics, matching)
        if scope and scope != strategy:
            add_check(checks, f"segment_recall[{label}]", rec, min_recall, "min",
                      fixture_diagnostic, skipped=True,
                      note=f"仅对 {scope} 生效；当前 strategy={strategy}")
        else:
            add_check(checks, f"segment_recall[{label}]", rec, min_recall, "min", fixture_diagnostic)

    # 诊断型期望（首次运行校准前不参与 PASS/FAIL）
    special = fixture.get("special") or {}
    seg_spec = special.get("segment_note_counts")
    if seg_spec and fixture.get("segments"):
        engine_key = "basic_pitch" if fixture.get("engine") == "basic_pitch" else "melody"
        expected_counts = (seg_spec.get("expected") or {}).get(engine_key)
        if expected_counts:
            for index, segment in enumerate(fixture["segments"]):
                if index >= len(expected_counts):
                    break
                count = sum(
                    1 for p in entry["notes"]
                    if segment["start_s"] <= p["onset_s"] < segment["end_s"]
                )
                add_check(checks, f"segment_note_count[{segment['label']}]", count,
                          expected_counts[index], "eq", True, note=seg_spec.get("note", ""))

    merge_spec = special.get("expected_merge")
    if merge_spec:
        expected_count = int(merge_spec.get("expected_count", 0))
        add_check(checks, "merge_expected_count", metrics["pred_count"], expected_count, "eq", True,
                  note=merge_spec.get("note", ""))

    failed = [
        c["name"] for c in checks
        if not c["passed"] and not c["skipped"] and (not c["diagnostic"] or strict)
    ]
    if fixture_diagnostic:
        status = "DIAGNOSTIC_PASS" if not failed else "DIAGNOSTIC_FAIL"
    else:
        status = "PASS" if not failed else "FAIL"

    public_metrics = {k: v for k, v in metrics.items() if not k.startswith("_")}
    return {
        "id": fixture_id,
        "strategy": strategy,
        "status": status,
        "passed": status in ("PASS", "DIAGNOSTIC_PASS"),
        "metrics": public_metrics,
        "checks": checks,
    }


# --------------------------------------------------------------------------
# 直接调用产品引擎（跳过 Demucs）
# --------------------------------------------------------------------------
def run_prediction(
    fixture: dict[str, Any],
    strategy: str,
    engine_override: str,
) -> dict[str, Any]:
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    os.environ["MELODY_LINE_STRATEGY"] = strategy

    from app.services.instruments import get_instrument

    config = get_instrument(fixture["instrument"])
    audio_path = REPO_ROOT / fixture["audio"]
    engine = fixture.get("engine", "melody") if engine_override == "auto" else engine_override
    engine_used = engine

    if engine == "basic_pitch":
        from app.services.transcriber import NoteTranscriber
        events = NoteTranscriber().transcribe(audio_path, config)
    else:
        try:
            from app.services.transcriber import NoteTranscriber
            events = NoteTranscriber().transcribe_melody(audio_path, config)
        except ImportError:
            # basic_pitch 未安装时退化到 MelodyExtractor 直调（不含 transcriber 后处理）
            from app.services.melody import MelodyExtractor
            events = MelodyExtractor().extract(audio_path, config)
            engine_used = "melody_direct"

    analysis: dict[str, Any] = {}
    try:
        from app.services.analyzer import AudioAnalyzer
        analysis = AudioAnalyzer().analyze(audio_path)
    except Exception as exc:  # analyzer 依赖缺失时只影响 key/bpm 检查
        analysis = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "notes": normalize_notes(events),
        "analysis": analysis,
        "engine_used": engine_used,
    }


def detect_strategy_switch() -> bool | None:
    """检测产品代码是否已提供 MELODY_LINE_STRATEGY 开关。"""
    try:
        backend_dir = str(REPO_ROOT / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        import app.config as config
        return hasattr(config, "MELODY_LINE_STRATEGY")
    except Exception:
        return None




def load_predictions(pred_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """读取预测目录，返回 {strategy: {fixture_id: entry}}。"""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(pred_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        stems = path.stem.split(".")
        fixture_id = data.get("fixture_id") or data.get("id") or stems[0]
        strategy = data.get("strategy") or (stems[1] if len(stems) > 1 else "unknown")
        entry = {
            "notes": normalize_notes(data.get("notes") or data.get("predictions") or []),
            "analysis": data.get("analysis") or {},
            "source": str(path),
        }
        result.setdefault(strategy, {})[fixture_id] = entry
    return result


def greedy_match(
    gt_notes: list[dict[str, Any]],
    pred_notes: list[dict[str, Any]],
    matching: dict[str, Any],
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """一对一贪心匹配（onset 最近优先）。

    matching.duration_check=False 时只按 onset+pitch 匹配，忽略时值
    （用于真值时值低于 basic-pitch minimum_note_length 的短音样本，见 F4）。
    """
    onset_tol = float(matching.get("onset_tolerance_s", 0.05))
    dur_ratio = float(matching.get("duration_tolerance_ratio", 0.25))
    pitch_tol = int(matching.get("pitch_tolerance_semitones", 0))
    check_duration = bool(matching.get("duration_check", True))
    pairs: list[tuple[float, int, int]] = []
    for gi, gt in enumerate(gt_notes):
        for pi, pred in enumerate(pred_notes):
            dt = abs(gt["onset_s"] - pred["onset_s"])
            if dt > onset_tol:
                continue
            if abs(gt["midi"] - pred["midi"]) > pitch_tol:
                continue
            if check_duration:
                dur_dev = abs(pred["dur_s"] - gt["dur_s"]) / max(gt["dur_s"], 1e-6)
                if dur_dev > dur_ratio:
                    continue
            else:
                dur_dev = 0.0
            pairs.append((dt + 0.2 * dur_dev, gi, pi))
    pairs.sort()
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _, gi, pi in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matches.append((gi, pi))
    return matches, used_gt, used_pred


def onset_only_matches(
    gt_notes: list[dict[str, Any]], pred_notes: list[dict[str, Any]], tolerance: float
) -> list[tuple[int, int]]:
    """只按 onset 匹配，用于判断八度/音高错误。"""
    pairs: list[tuple[float, int, int]] = []
    for gi, gt in enumerate(gt_notes):
        for pi, pred in enumerate(pred_notes):
            dt = abs(gt["onset_s"] - pred["onset_s"])
            if dt <= tolerance:
                pairs.append((dt, gi, pi))
    pairs.sort()
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _, gi, pi in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matches.append((gi, pi))
    return matches


# --------------------------------------------------------------------------
# 汇总 / 命令行
# --------------------------------------------------------------------------
def check_status(result: dict[str, Any], name: str) -> str:
    for check in result["checks"]:
        if check["name"] == name:
            if check["skipped"]:
                return "skip"
            return "ok" if check["passed"] else "no"
    return "-"


def format_result(result: dict[str, Any]) -> str:
    if result["status"] == "NO_PREDICTION":
        return f'{result["id"]:<6} NO_PREDICTION'
    m = result["metrics"]
    failed = [
        c["name"] for c in result["checks"]
        if not c["passed"] and not c["skipped"] and not c["diagnostic"]
    ]
    line = (
        f'{result["id"]:<6} {result["status"]:<16} '
        f'prec={m["precision"]:.2f} rec={m["recall"]:.2f} f1={m["f1"]:.2f} '
        f'leak={m["bass_leakage_rate"]:.2f} oct_err={m["octave_error_rate"]:.2f} '
        f'jumps8={m["octave_jumps_per_8bars"]:.1f} dev={m["note_count_deviation"]:+.2f} '
        f'below220={m["pred_below_220hz_count"]}/{m["gt_below_220hz_count"]} '
        f'key={check_status(result, "key")} ts={check_status(result, "time_signature")} '
        f'bpm={check_status(result, "bpm")}'
    )
    if failed:
        line += f'  FAIL: {",".join(failed)}'
    return line


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {
        "total": len(results),
        "counts": counts,
        "passed": counts.get("PASS", 0) + counts.get("DIAGNOSTIC_PASS", 0),
        "failed": counts.get("FAIL", 0) + counts.get("DIAGNOSTIC_FAIL", 0),
        "no_prediction": counts.get("NO_PREDICTION", 0),
    }


def compare_reports(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """两策略逐 fixture 指标差值。"""
    rows: list[dict[str, Any]] = []
    base = {r["id"]: r for r in reports["legacy_nms"]["fixtures"]}
    new = {r["id"]: r for r in reports["dp_register"]["fixtures"]}
    for fixture_id, old_result in base.items():
        new_result = new.get(fixture_id)
        row: dict[str, Any] = {
            "id": fixture_id,
            "legacy_status": old_result["status"],
            "dp_status": new_result["status"] if new_result else "NO_PREDICTION",
        }
        if old_result["metrics"] and new_result and new_result["metrics"]:
            for key in (
                "precision", "recall", "bass_leakage_rate", "octave_error_rate",
                "octave_jumps_per_8bars", "note_count_deviation", "boundary_pitch_ratio",
            ):
                row[f"delta_{key}"] = round(
                    new_result["metrics"][key] - old_result["metrics"][key], 6
                )
        rows.append(row)
    return rows


def dump_prediction(dump_dir: Path, fixture: dict[str, Any], strategy: str, entry: dict[str, Any]) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fixture_id": fixture["id"],
        "strategy": strategy,
        "engine": entry.get("engine_used"),
        "notes": [
            [round(note["onset_s"], 6), round(note["onset_s"] + note["dur_s"], 6), note["midi"]]
            for note in entry["notes"]
        ],
        "analysis": entry["analysis"],
    }
    path = dump_dir / f'{fixture["id"]}.{strategy}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测 P0 fixture 的主旋律提取效果")
    parser.add_argument("--fixtures-dir", default=str(FIXTURE_DIR), help="GT JSON 目录")
    parser.add_argument("--only", default="", help="只评测指定 ID/组，逗号分隔")
    parser.add_argument("--pred-dir", default="", help="已有预测 JSON 目录")
    parser.add_argument("--run", action="store_true", help="直接调用产品引擎（跳过 Demucs）")
    parser.add_argument("--engine", default="auto", choices=["auto", "melody", "basic_pitch"], help="覆盖 fixture 的引擎选择")
    parser.add_argument("--strategy", default="legacy_nms", choices=list(STRATEGIES), help="策略标签")
    parser.add_argument("--compare", action="store_true", help="对比 legacy_nms 与 dp_register")
    parser.add_argument("--dump-dir", default="", help="--run 时把预测结果落盘（供之后 --pred-dir 复算）")
    parser.add_argument("--json-out", default="", help="完整报告写入 JSON 文件")
    parser.add_argument("--onset-tolerance", type=float, default=None, help="覆盖 onset 匹配容差（秒）")
    parser.add_argument("--strict", action="store_true", help="诊断型检查也计入失败")
    args = parser.parse_args(argv)

    fixtures = load_fixtures(
        Path(args.fixtures_dir),
        args.only.split(",") if args.only else [],
    )
    if not fixtures:
        parser.error(f"在 {args.fixtures_dir} 未找到 GT fixture")

    strategies = list(STRATEGIES) if args.compare else [args.strategy]
    switch_implemented = detect_strategy_switch()
    if switch_implemented is False:
        print("注意：产品代码尚未定义 MELODY_LINE_STRATEGY，dp_register 结果将与 legacy_nms 相同。")
    elif switch_implemented is None:
        print("注意：无法导入 app.config，策略开关状态未知。")

    predictions: dict[str, dict[str, dict[str, Any]]] = {}

    if args.run:
        missing = [fx["id"] for fx in fixtures if not (REPO_ROOT / fx["audio"]).exists()]
        if missing:
            print(f"未执行：以下 fixture 音频不存在（未生成或未转码）：{', '.join(missing)}")
        for strategy in strategies:
            predictions[strategy] = {}
            for fixture in fixtures:
                audio_path = REPO_ROOT / fixture["audio"]
                if not audio_path.exists():
                    continue
                engine = fixture.get("engine", "melody") if args.engine == "auto" else args.engine
                print(f'[run] {fixture["id"]} strategy={strategy} engine={engine}')
                try:
                    entry = run_prediction(fixture, strategy, args.engine)
                except Exception as exc:
                    print(f'  {fixture["id"]} 运行失败：{type(exc).__name__}: {exc}')
                    continue
                predictions[strategy][fixture["id"]] = entry
                if args.dump_dir:
                    dump_prediction(Path(args.dump_dir), fixture, strategy, entry)

    elif args.pred_dir:
        predictions = load_predictions(Path(args.pred_dir))
        if not predictions:
            parser.error(f"{args.pred_dir} 中没有可读取的预测 JSON")
        if args.strategy not in predictions and not args.compare:
            actual = next(iter(predictions))
            print(f"预测目录中未找到 strategy={args.strategy}，改用 {actual}")
            strategies = [actual]
        elif args.compare:
            missing = [s for s in STRATEGIES if s not in predictions]
            if missing:
                print(f"对比缺少策略：{', '.join(missing)}")
    else:
        parser.error("需要 --run 或 --pred-dir")

    reports: dict[str, dict[str, Any]] = {}
    overall_fail = False
    for strategy in strategies:
        entries = predictions.get(strategy, {})
        results = [
            evaluate_fixture(fixture, entries.get(fixture["id"]), strategy, args.onset_tolerance, args.strict)
            for fixture in fixtures
        ]
        for result in results:
            print(format_result(result))
            if not result["passed"]:
                overall_fail = True
        summary = summarize(results)
        print(
            f'策略 {strategy}：PASS {summary["counts"].get("PASS", 0)} / '
            f'FAIL {summary["counts"].get("FAIL", 0)} / '
            f'DIAGNOSTIC {summary["counts"].get("DIAGNOSTIC_PASS", 0) + summary["counts"].get("DIAGNOSTIC_FAIL", 0)} / '
            f'NO_PREDICTION {summary["no_prediction"]}'
        )
        reports[strategy] = {"fixtures": results, "summary": summary}

    comparison = None
    if args.compare and all(strategy in reports for strategy in STRATEGIES):
        comparison = compare_reports(reports)
        print("\n对比（dp_register - legacy_nms）：")
        for row in comparison:
            print(
                f'{row["id"]:<6} {row["legacy_status"]:<16} → {row["dp_status"]:<16} '
                f'Δprec={row.get("delta_precision", 0):+.2f} Δrec={row.get("delta_recall", 0):+.2f} '
                f'Δleak={row.get("delta_bass_leakage_rate", 0):+.2f} '
                f'Δjumps8={row.get("delta_octave_jumps_per_8bars", 0):+.1f} '
                f'Δdev={row.get("delta_note_count_deviation", 0):+.2f}'
            )

    if args.json_out:
        payload = {
            "generated_by": "backend/tests/eval_fixtures.py",
            "strategy_switch_implemented": switch_implemented,
            "onset_tolerance": args.onset_tolerance,
            "strict": args.strict,
            "strategies": reports,
            "comparison": comparison,
        }
        out_path = Path(args.json_out)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"报告已写入 {out_path}")

    return 1 if overall_fail else 0


if __name__ == "__main__":
    sys.exit(main())
