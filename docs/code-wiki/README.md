# 乐谱制作器 (Sheet Music Maker) — Code Wiki

> 本 Wiki 是项目的结构化代码文档，覆盖**整体架构、模块职责、关键类与函数、依赖关系、运行方式**五大主题。
> 面向新加入的开发者、维护者以及希望快速定位代码的人。

## 项目一句话

上传一段混合多乐器的音频（MP3/WAV/M4A/FLAC），后端自动完成 **人声分离 → 音符转录 → 节拍/调性分析 → 时值量化 → 乐谱导出**，前端在浏览器内渲染可播放、可下载的乐谱。

## 文档索引

| 文档 | 内容 | 读者 |
|------|------|------|
| [01-architecture.md](01-architecture.md) | 系统整体架构、技术栈、目录结构、请求生命周期、6 阶段流水线 | 所有人（入门必读） |
| [02-backend.md](02-backend.md) | 后端各模块职责、关键类与方法、配置项 | 后端开发者 |
| [03-frontend.md](03-frontend.md) | 前端各模块职责、组件树、composable、状态机 | 前端开发者 |
| [04-dependencies.md](04-dependencies.md) | 模块间依赖、第三方库清单、数据流、API 契约 | 维护者 |
| [05-running.md](05-running.md) | 开发模式 / 生产模式 / EXE 打包的运行方式 | 部署与运维 |

## 快速导览

```
┌────────────────────────────┐
│  前端 Vue 3 SPA (:5173)    │
│  上传 → 轮询(2s) → 渲染/播放 │
└──────────┬─────────────────┘
           │ /api/*  (Vite 代理 / 同源)
┌──────────▼─────────────────┐
│  后端 FastAPI (:8000)       │
│  router → pipeline → services │
│  线程池异步执行(单并发)       │
└────────────────────────────┘
```

- 后端入口：[backend/app/main.py](../../backend/app/main.py)
- 流水线编排：[backend/app/services/pipeline.py](../../backend/app/services/pipeline.py)
- 前端入口：[frontend/src/App.vue](../../frontend/src/App.vue)
- 系统依赖：Python 3.9+ / Node.js 18+ / ffmpeg + ffprobe
- 支持乐器：通用乐谱 / 长笛 / 小提琴 / 竖笛 / 双簧管

## 版本与仓库

- 版本：1.0.0（FastAPI `version="1.0.0"`）
- 仓库：https://github.com/zyl-88/SheetMusicMaker （master 分支）
- 说明：本项目由主仓库的 worktree（`feat-code-wiki-doc-JZHcx1`）维护，本文档即在该分支生成。
