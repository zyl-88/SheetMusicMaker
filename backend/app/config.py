"""集中配置常量。"""
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
MERGE_GAP_THRESHOLD = 0.08           # 秒，同音高音符间隔小于此值则合并

# Melodia 旋律提取配置
MELODY_MIN_FREQ = 220.0              # Hz (A3)，低于此频率视为伴奏/低音，只追踪以上旋律
MELODY_LINE_STRATEGY = "dp_register"  # 主旋律线选择策略：legacy_nms / dp_register（可用同名环境变量覆盖）

# 人声分离能量比阈值：低于此值说明非人声音乐，回退用原始音频
DEMUCS_VOCALS_ENERGY_RATIO_THRESHOLD = 0.20

# 量化配置
QUANTIZE_DIVISION = 4  # 量化到 1/16 音符 (一拍四等分)
DEFAULT_TIME_SIG = "4/4"

# 任务配置
MAX_CONCURRENT_TASKS = 1  # CPU 限制，同时只跑一个
POLL_INTERVAL_MS = 2000  # 前端轮询间隔（毫秒，用于文档参考）

# 音频格式
OUTPUT_SAMPLE_RATE = 44100
OUTPUT_AUDIO_FORMAT = "wav"
