"""Pydantic 请求/响应模型。"""
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str
    models_loaded: bool


class InstrumentInfo(BaseModel):
    """乐器信息。"""
    id: str
    name: str
    min_freq: float
    max_freq: float


class InstrumentsResponse(BaseModel):
    """乐器列表响应。"""
    instruments: list[InstrumentInfo]


class TranscribeRequest(BaseModel):
    """转录请求（multipart 表单）。"""
    instrument: str


class TranscribeResponse(BaseModel):
    """转录提交响应。"""
    task_id: str
    status: str


class TaskResult(BaseModel):
    """任务结果详情。"""
    abc: str
    key: dict
    bpm: float
    time_signature: str
    instrument_name: str
    downloads: dict[str, str]


class TaskStatus(BaseModel):
    """任务状态响应。"""
    task_id: str
    status: str
    stage: str
    progress: int
    result: TaskResult | None = None
    error: str | None = None
