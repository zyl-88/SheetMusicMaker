# 乐谱制作器 (Sheet Music Maker)

上传音频文件，自动分离人声主旋律并生成演奏用乐谱。

## 功能

- 上传混合多乐器音频（MP3/WAV/M4A/FLAC，≤10分钟，≤50MB）
- 自动分离人声主旋律（Demucs）
- 自动转录为音符（basic-pitch）
- 自动检测节拍速度、调号、拍号（madmom + librosa）
- 生成指定 C 调高音谱号乐器的分谱
- 浏览器内渲染乐谱并播放（abcjs）
- 导出 MIDI、MusicXML、ABC 三种格式

## 系统依赖

- Python 3.9+
- Node.js 18+
- ffmpeg + ffprobe（音频解码/校验）

## 安装

### 后端

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 前端

```bash
cd frontend
npm install
```

## 运行

### 开发模式

后端（终端1）：
```bash
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端（终端2）：
```bash
cd frontend
npm run dev
```

访问 http://localhost:5173

### 生产模式

```bash
cd frontend
npm run build
cd ../backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

## 技术栈

- 后端：FastAPI + Demucs + basic-pitch + madmom + librosa + music21
- 前端：Vue 3 + abcjs + Vite + TypeScript
- 部署：CPU 处理，本地局域网

## 支持乐器

| 乐器 | 音域 |
|------|------|
| 长笛 (Flute) | C4 - D7 |
| 小提琴 (Violin) | G3 - E7 |
| 竖笛 (Recorder) | C4 - D7 |
| 双簧管 (Oboe) | Bb3 - G6 |
