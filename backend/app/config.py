"""集中配置常量。"""
import os
import sys
from pathlib import Path

# ============================================================
# 路径配置：区分开发环境与 PyInstaller 打包环境
# ============================================================
if getattr(sys, "frozen", False):
    # PyInstaller onedir 环境
    _BUNDLE_DIR = Path(sys._MEIPASS)          # 打包资源目录（只读）
    _APP_DIR = Path(sys.executable).parent     # EXE 所在目录（可写）
    SOUNDFONT_DIR = _BUNDLE_DIR / "soundfont"
    STATIC_DIR = _BUNDLE_DIR / "app" / "static"
    BASE_DIR = _APP_DIR                         # 兼容旧引用
else:
    # 开发环境
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    SOUNDFONT_DIR = BASE_DIR / "soundfont"
    STATIC_DIR = Path(__file__).resolve().parent / "static"

# 运行时可写目录（上传/临时/输出）
STORAGE_DIR = _APP_DIR if getattr(sys, "frozen", False) else BASE_DIR
STORAGE_DIR = STORAGE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
TEMP_DIR = STORAGE_DIR / "temp"
OUTPUTS_DIR = STORAGE_DIR / "outputs"

# 确保运行时目录存在
for d in [UPLOADS_DIR, TEMP_DIR, OUTPUTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 音频限制
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_DURATION_SEC = 600  # 10 分钟
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}

# Demucs 配置
DEMUCS_MODEL = "htdemucs"
DEMUCS_DEVICE = "cpu"
DEMUCS_SEGMENT = 7
DEMUCS_JOBS = 4
DEMUCS_SHIFTS = 1

# basic-pitch 配置
BASIC_PITCH_ONSET_THRESHOLD = 0.5    # 起拍阈值：提高以减少泛音误检
BASIC_PITCH_FRAME_THRESHOLD = 0.3    # 帧阈值：提高以减少噪声音符
BASIC_PITCH_MIN_NOTE_LENGTH = 100.0  # 毫秒，最短音符时长

# 音符后处理配置
MIN_NOTE_AMPLITUDE = 0.1             # 最小振幅，低于此值的音符视为噪声
MIN_MIDI_PITCH = 36                  # 最低 MIDI 音高 (C2 ≈ 65Hz)，低于此为次低频噪声
MERGE_GAP_THRESHOLD = 0.08           # 秒，同音高音符间隔小于此值则合并（legacy/PYIN 路径）

# Melodia 旋律提取配置（音域下限直接采用乐器 min_freq，不再统一抬高到 220Hz）
MELODY_LINE_STRATEGY = "dp_register"  # 主旋律线选择策略：legacy_nms / dp_register（可用同名环境变量覆盖）

# 人声分离能量比阈值：低于此值说明非人声音乐，回退用原始音频
DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD = 0.20

# 量化配置
QUANTIZE_DIVISION = 4  # 量化到 1/16 音符 (一拍四等分)
DEFAULT_TIME_SIG = "4/4"


def tempo_value(bpm: float) -> float:
    """三方（ABC / MusicXML / MIDI）共用的 tempo 取整规则。

    统一保留 2 位小数：ABC 的 ``Q:``、MusicXML 的 ``<sound tempo>``、
    MIDI 的 setTempo 事件必须写同一个数值，否则页面谱与下载谱会累积速度差
    （实测 0.6% 的速度差在 138 秒上累积成 0.852 秒）。
    """
    return round(float(bpm), 2)


def tempo_text(bpm: float) -> str:
    """tempo 的可读文本（ABC / MusicXML 共用），去掉多余的尾随零。"""
    text = "%.2f" % tempo_value(bpm)
    return text.rstrip("0").rstrip(".")


def tempo_map_enabled() -> bool:
    """导出速度基准开关。

    - ``per_bar``（默认）：按检测到的节拍序列写「每小节 tempo 图」，导出时
      MIDI/MusicXML 的拍→秒换算与量化时的时间→拍映射一致。
    - ``single``：回滚到旧的「单一速度」线性换算（``EXPORT_TEMPO_MAP=single``），
      用于 A/B 对照与快速回退。
    """
    value = os.environ.get("EXPORT_TEMPO_MAP", "per_bar").strip().lower()
    return value != "single"

# 任务配置
MAX_CONCURRENT_TASKS = 1  # CPU 限制，同时只跑一个
POLL_INTERVAL_MS = 2000  # 前端轮询间隔（毫秒，用于文档参考）

# 音频格式
OUTPUT_SAMPLE_RATE = 44100
OUTPUT_AUDIO_FORMAT = "wav"
