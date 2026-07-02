"""线程安全的内存任务存储。"""
import threading
import time
import uuid
from typing import Any

# 任务状态常量
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# 流水线阶段
STAGE_VALIDATING = "validating"
STAGE_SEPARATING = "separating"
STAGE_TRANSCRIBING = "transcribing"
STAGE_ANALYZING = "analyzing"
STAGE_QUANTIZING = "quantizing"
STAGE_EXPORTING = "exporting"
STAGE_COMPLETED = "completed"

# 阶段中文标签（前端展示用）
STAGE_LABELS = {
    "validating": "校验音频文件",
    "separating": "分离人声主旋律",
    "transcribing": "转录音符",
    "analyzing": "分析节拍与调性",
    "quantizing": "量化音符时值",
    "exporting": "生成乐谱",
    "completed": "完成",
}


class TaskStore:
    """线程安全的任务存储，支持 create/update/complete/fail/get。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, audio_filename: str, instrument: str) -> str:
        """创建新任务，返回 task_id。"""
        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": STATUS_PENDING,
                "stage": "",
                "progress": 0,
                "result": None,
                "error": None,
                "audio_filename": audio_filename,
                "instrument": instrument,
                "created_at": time.time(),
            }
        return task_id

    def update(self, task_id: str, stage: str, progress: int,
               status: str = STATUS_RUNNING) -> None:
        """更新任务进度。"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["stage"] = stage
                self._tasks[task_id]["progress"] = progress
                self._tasks[task_id]["status"] = status

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        """标记任务完成。"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["result"] = result
                self._tasks[task_id]["status"] = STATUS_COMPLETED
                self._tasks[task_id]["stage"] = STAGE_COMPLETED
                self._tasks[task_id]["progress"] = 100

    def fail(self, task_id: str, error: str) -> None:
        """标记任务失败。"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["error"] = error
                self._tasks[task_id]["status"] = STATUS_FAILED

    def get(self, task_id: str) -> dict[str, Any] | None:
        """获取任务状态（返回副本）。"""
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def delete(self, task_id: str) -> bool:
        """删除任务记录。"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False


# 全局单例
task_store = TaskStore()
