# 05 · 项目运行方式

## 0. 前置条件

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.9+ | 后端运行 |
| Node.js | 18+ | 前端构建/开发 |
| ffmpeg + ffprobe | 完整版（gyan.dev builds） | 音频解码（M4A 等）、时长校验回退 |

## 1. 开发模式（两个终端）

### 终端 1 — 后端

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 启动（热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

首次启动会预加载 basic-pitch / Demucs 模型（耗时较长，日志可见 `模型加载完成`）。模型缓存默认在 `torch_hub/`。

### 终端 2 — 前端

```bash
cd frontend
npm install
npm run dev
```

- 访问 **http://localhost:5173**（Vite Dev Server）
- `/api` 与 `/soundfont` 由 [vite.config.ts](../../frontend/vite.config.ts) 代理到 `http://localhost:8000`

> 若只启动后端不启动前端，可直接访问 http://localhost:8000（此时 app/static 需已有构建产物）。

## 2. 生产模式（单服务单端口）

```bash
# 1) 构建前端（vue-tsc 类型检查 + vite build → 输出到 backend/app/static）
cd frontend
npm run build

# 2) 启动后端（托管 API + /soundfont + 前端静态资源）
cd ../backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 访问 **http://localhost:8000**，局域网内其他机器通过 `http://<本机IP>:8000` 访问。
- 后端会自动挂载 `static/`（HTML 模式）与 `soundfont/`。

## 3. 音色库（soundfont）准备

abcjs 试听需要音色库（`/soundfont/` 下为通用 MIDI 音色文件）。音色库体积大、已被 `.gitignore` 排除：

1. 从外部渠道获取 soundfont 文件（如 `soundfont.sf2` 及对应分片），放到仓库根 `soundfont/` 目录（开发）或打包目录（EXE）。
2. 缺失时后端会跳过挂载，页面试听不可用，但下载/复制 ABC 功能不受影响。

## 4. Windows EXE 打包（PyInstaller）

项目通过 [run.py](../../backend/run.py) 作为启动入口打包为 onedir 应用。

1. 构建前端并放入后端静态目录（见生产模式步骤 1）。
2. 安装打包依赖并执行 PyInstaller（spec 需包含：`app/static`、`soundfont`、`torch_hub` 模型缓存、ffmpeg 可执行文件）。
3. 产物为 onedir 目录，双击其中 EXE 启动：
   - 自动设置 `TORCH_HOME` 指向打包缓存（避免运行时联网下载模型）
   - 自动创建 EXE 同级 `storage/{uploads,temp,outputs}`
   - 延迟 3 秒自动打开浏览器 http://localhost:8000

> 打包配置细节见 `.trae/documents/exe_packaging_plan.md`。

## 5. 快速验证

| 步骤 | 方式 | 预期 |
|------|------|------|
| 健康检查 | `curl http://localhost:8000/api/health` | `{"status":"ok","models_loaded":true}` |
| 乐器列表 | `curl http://localhost:8000/api/instruments` | 返回 5 个乐器 |
| 完整转录 | 页面选择 ≤50MB / ≤10 分钟音频 + 乐器 → 开始转录 | 进度条走完 6 阶段 → 渲染乐谱 |
| 下载 | 结果页三格式下载 | ABC / MusicXML / MIDI 文件 |

## 6. 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 启动时 `basic-pitch 模型预加载失败` | 仅为告警，首次使用时再加载；确认网络或本地缓存 |
| M4A 无法读取时长 | 安装完整版 ffmpeg（soundfile 不支持 M4A） |
| madmom 相关报错 | numpy 2.x 不兼容，analyzer 自动降级 librosa，可忽略 |
| 转录慢 | CPU 推理 + 单并发（`MAX_CONCURRENT_TASKS=1`），大文件等待属正常 |
| 试听无声音 | `/soundfont` 缺失或浏览器阻止音频；检查音色库在位 |
| 局域网无法访问 | 确保以 `--host 0.0.0.0` 启动，并放行防火墙 8000 端口 |
