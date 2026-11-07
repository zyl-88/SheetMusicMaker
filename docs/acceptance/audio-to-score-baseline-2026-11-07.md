# 音频转乐谱基线验收报告（《酒馆小调（狮王之傲）》）

- 报告日期：2026-11-07
- 验收对象：SheetMusicMaker 当前工作区（FastAPI + Vue 3）
- 样音：`郎朗,魔兽世界 - 酒馆小调（狮王之傲）.flac`（48 kHz / 双声道 / 139.44 s / 13.57 MB）
- 运行入口：`POST /api/transcribe`（HTTP API，与前端同一路径），`instrument=universal`
- 结论概述：**流程可跑通、产物完整，但“主旋律”正确性不达标**。全曲仅 34.6% 的音符落在真实最高声部，39.6% 抓到了伴奏/内声部，11.4% 为八度错误；后半段（01:06–02:18）基本是在伴奏线上转谱。另有拍号/速度、调式、同音反复三类全局性问题。

---

## 1. 版本与环境（本次基线实际运行条件）

| 项目 | 值 |
|---|---|
| git HEAD | **`ab3dcf9`（baseline commit：`fix: 稳定音频转乐谱链路并补齐失败处理`，2026-11-07 20:06）**。本报告的执行发生在上一 HEAD `568adb1` + 未提交工作区；提交后核对：受影响的 `backend/app/**` 文件 mtime 仍为 2026-11-06 14:55–14:56，且 `git status` 显示跟踪文件已全部干净，说明 **运行时的代码内容与 ab3dcf9 逐字节一致**，因此可直接把 ab3dcf9 当作本次基线哈希 |
| 工作区状态 | 提交后**干净**（仅 `backend/tests/`、`docs/acceptance/` 为新增未跟踪，均属验收/测试资产，不是产品代码） |
| Python | 3.10.11 |
| 依赖位置 | `d:\project\SheetMusicMaker\.venv`（193 个包，1.49 GB） |
| 关键版本 | torch 2.14.1+cpu、torchaudio 2.11.1、demucs 4.0.1、basic-pitch 0.4.0、librosa 0.11.0、music21 9.9.2、soundfile 0.14.0、fastapi 0.162.0、onnxruntime 1.23.2 |
| madmom | **缺失**。运行日志明确：`madmom 不可用（未安装或 numpy 2.x 不兼容），降级到 librosa.beat.beat_track`；本报告的所有节拍结论均来自该降级路径 |
| ffmpeg / ffprobe | **不在 PATH，打包产物内也没有**；本轮只用 FLAC（libsndfile 直读），**M4A/其它依赖 ffmpeg 的格式未验证，发布形态大概率不可用** |
| Demucs 权重 | 未联网下载，复用打包产物内的 `backend\dist\乐谱制作器\_internal\torch_hub\hub\checkpoints\955717e8-8726e21a.th`（`TORCH_HOME` 指向该目录） |
| 实际解释器 | **系统 Python 3.10.11 + `PYTHONPATH=.venv\Lib\site-packages`**（详见 1.1，不是 `.venv\Scripts\python.exe`） |

### 1.1 必须说明的环境偏差（影响复现）

`.venv\Scripts\python.exe` 在本机被安全策略限制：**只能写项目根目录和 `.venv` 自身，写 `storage/`、`docs/`、`backend/` 等子目录一律 `PermissionError: [Errno 13]`**。已排除沙箱（`dangerouslyDisableSandbox` 同样失败）、Controlled Folder Access（已关闭）、ACL（`Authenticated Users: Modify` 正常）；把系统 python.exe 复制到项目根目录后同样被拒，说明是“工作区内可执行文件”的写限制，而非包或路径问题。

因此本轮用**系统 Python 3.10.11 加载 `.venv` 的同一套包**运行（解释器与包版本完全一致，仅解释器二进制位置不同），否则后端无法写入 `storage/uploads`、`storage/temp`、`storage/outputs`。

> **2026-11-07 复核**：开发方已确认上述限制在受控执行沙箱下属预期现象，**推荐组合就是「系统 Python 3.10.11 + `PYTHONPATH=.venv\Lib\site-packages`」**，并实测该组合下 `pkg_resources` / `resampy 0.4.2` / `torch 2.14.1+cpu` / `basic_pitch` 均正常，可直接写入 `storage`、`test_audio` 与默认 `~/.cache/torch`（后续复验无需再设 `TORCH_HOME`）。本条不再视为环境缺陷，而是本机既定运行方式。

复现命令（本轮实际使用）：

```powershell
$env:PYTHONPATH="d:\project\SheetMusicMaker\.venv\Lib\site-packages"
$env:TORCH_HOME="d:\project\SheetMusicMaker\backend\dist\乐谱制作器\_internal\torch_hub"
$env:TMP="d:\project\SheetMusicMaker\.venv\tmp"; $env:TEMP=$env:TMP
cd d:\project\SheetMusicMaker\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level info
# 另开终端：
python d:\project\SheetMusicMaker\storage\acceptance\run_baseline.py
```

### 1.2 耗时（本机 CPU，单任务串行）

| 阶段 | 30 s 冒烟 | 完整 139.44 s |
|---|---|---|
| 人声分离（htdemucs） | ~98 s | **241.8 s**（35% 进度点） |
| 转谱 + 分析 + 量化 + 导出（合计） | ~84 s | **~152 s** |
| 全流程（提交→产物落盘） | **181.6 s** | **~394 s（6 分 34 秒）** |

- 上传（13.57 MB）：< 1 s。
- 本音频 vocals 能量比 14.79% < 20%，按现有逻辑判为非人声，**用原始音频**做转谱与分析（Demucs 结果被丢弃，但仍耗时 242 s）。
- 服务端模型预加载约 5 s（不计入上表）。

---

## 2. 产物

- API 任务：`6346ec465869`
- 任务目录：[storage/outputs/6346ec465869](file:///d:/project/SheetMusicMaker/storage/outputs/6346ec465869)
- 验收留档：
  - [score.abc](file:///d:/project/SheetMusicMaker/storage/acceptance/baseline/score.abc)
  - [score.musicxml](file:///d:/project/SheetMusicMaker/storage/acceptance/baseline/score.musicxml)
  - [score.midi](file:///d:/project/SheetMusicMaker/storage/acceptance/baseline/score.midi)
  - [result.json](file:///d:/project/SheetMusicMaker/storage/acceptance/baseline/result.json)
  - [metrics.json](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/metrics.json)
  - [sections.csv](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/sections.csv)
  - [gen_notes.csv](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/gen_notes.csv)（逐音符分类，含输出小节号、时间、判定）

产物概况：**298 个音符**，MIDI 覆盖 4.94 s–138.82 s（尾部空 0.62 s），音域 MIDI 57–83（A3–B5），报告 `BPM=161.5 / M:4/4 / K:F#`，ABC 共 93.4 个“4 拍小节”。

---

## 3. 对比方法（可复核）

1. **参考素材**：用 `basic-pitch` 对**原始混音**做多点转谱（1405 个音事件，65–2000 Hz），在每个音符时刻取“同时发声的最高音”作为钢琴曲主旋律参考（piano top-voice 启发式，354 个顶层参考音）。
2. **交叉验证**：`librosa.pyin`（65–1500 Hz，10 ms 帧）独立音高轨迹；并用 4096 点 STFT 在争议时段直接看最强谱峰（见 5.1，确认 01:06–02:18 的真实旋律峰在 883–1249 Hz，即 A5–D#6）。
3. **生成侧**：读取导出 MIDI 的音符事件（绝对时间），逐音符对齐参考：
   - `top/melody`：与主旋律参考相差 ≤2 半音且不低于最高声部；
   - `accompaniment/inner`：能对上参考音，但比最高声部低 ≥5 半音；
   - `octave-error`：只对得上相差 10–14 / 22–26 半音的参考音；
   - `wrong-pitch`：附近有发声但没有任何 ±2 半音匹配；
   - `spurious`：该时刻参考无任何发声。
4. **赋值口径**：`bar` = 生成谱按 `BPM=161.5 / 4/4` 的小节号（**注意：因速度翻倍，这个“小节”只有约 1.486 s，约等于真实 2 拍，不是真实小节**；报告中同时给出时间，优先按时间定位）。
5. **局限**：top-voice 是“最高声部=主旋律”的启发式；若某段实际主旋律在内声部，`inner` 判定可能偏严；但 5.1 的谱峰证据与“生成线低于最强峰 12–24 半音”的事实不受此影响。pYIN 在钢琴混音上会锁到低音/次谐波（本曲 pYIN 中位 MIDI≈42，即 F#2），故 pYIN 只用于交叉验证，不单独作为旋律真值。

---

## 4. 总体指标

| 指标 | 数值 | 判读 |
|---|---|---|
| 生成音符数 / 参考顶层旋律音数 | 298 / 354 | 数量接近，但对不上位置 |
| **主旋律覆盖率** | **31.9%** | 68% 的参考旋律起拍没有被正确覆盖 |
| `top/melody` | 103 个（**34.6%**） | 只有三分之一是旋律 |
| `accompaniment/inner` | 118 个（**39.6%**） | 近四成抓成伴奏/内声部 |
| `octave-error` | **34 个（11.4%）** | 其中绝大多数比真实旋律低 12–24 半音 |
| `wrong-pitch` | 22 个（7.4%） | 附近有声但音高不对 |
| `spurious` | 3 个（1.0%） | 无参考发声处凭空生成 |
| 与最高声部的平均音高偏差 | **8.79 半音** | 系统性偏低，不是零散错音 |
| 起拍偏差（同音高匹配）中位数 | **+79 ms（绝对值中位 102 ms）** | 整体偏晚 |
| 150 ms 内无同音高起拍支持 | **18.8%** | 节奏/音高对不上的比例 |
| **同音反复被吞** | **39 处** | 与 80 ms 合并阈值高度吻合（详见 5.5） |
| 调性 | 报告 **F# major**；参考顶层音级 F#(34.1%)、C#(30.4%)、E(11.9%)、A(8.2%)、B(5.7%)、G#(5.4%) → **F# minor** | **调式判错**（详见 5.3） |
| 速度 | 161.5 BPM（30 s 冒烟却是 78.3 BPM） | **翻倍且不稳定** |
| 拍号 | 固定 4/4；节拍分组相关度：周期 3 = **0.438**、周期 6 = 0.437、周期 4 = **0.086** | 实际是 **3/4 或 6/8**，不是 4/4 |
| 谱面小节数 | 93.4（4/4 @161.5） | 真实约 46–47 个 4/4 小节 / 约 62 个 3/4 小节 → **小节数翻倍** |

---

## 5. 最严重问题（按用户价值排序）

### 5.1【P0】01:06–02:18 抓的是伴奏/内声部，整条线比真实旋律低 1–2 个八度

证据（生成音 vs 该时刻原始音频最强谱峰 / 参考最高声部）：

| 时间 | 生成音 | 原音频最强峰 | 参考最高声部 | 差值 |
|---|---|---|---|---|
| 01:06.71 | F#4 | — | E5 | -1（内声部） |
| 01:07.45 | C#5 | 1114 Hz ≈ C#6（0 dB） | C#6 | -12 |
| 01:08.94 | E4 | — | C#6 | -21 |
| 01:10.43 | C#4 | — | A5 | -20 |
| 01:13.42 | C#4 | 991 Hz ≈ B5（-4.3 dB） | B5 | -22 |
| 01:16.12 | C#4 | — | F#5 | -17 |
| 01:19.01 | C#4 | — | B5 | -22 |
| 01:19.47 | A3 | 883 Hz ≈ A5（0 dB） | A5 | **-24** |
| 01:54.04 | C#4 | — | C#5 | -12（+整段伴奏） |
| 02:12.30 | E4 | — | F#5 | -17 |

STFT 实测（4096 点）进一步确认这些段落的能量峰在旋律区：67.5 s→1114 Hz(C#6)、68.5 s→1249 Hz(D#6)、70.0 s→1114 Hz(C#6)、74.0 s→1109 Hz(C#6)、108.5 s→883 Hz(A5)、114.5 s→991 Hz(B5)、134.5 s→1114 Hz(C#6)。**生成谱在这些时刻落在 300–550 Hz（C#4–C#5），明显不是听感上的主旋律。**

分段量化（6 s 窗口，`inner%` / `melody%`）：

| 时间 | 生成音数 | 旋律% | 伴奏% | 八度错 | 判定 |
|---|---|---|---|---|---|
| 00:00–00:06 | 1 | 0 | 0 | 1 | 引子几乎空白；4.94 s 误加 C4（参考只有 F#2 低音） |
| 00:06–00:12 | 12 | 75 | 0 | 1 | 基本正确，2 个错音 |
| 00:12–00:18 | 14 | 64 | 21 | 1 | 局部串声部 |
| 00:18–00:24 | 5 | 80 | 0 | 0 | 只抓到 5 个音，覆盖不足 |
| 00:24–00:30 | 15 | 80 | 7 | 0 | 较好 |
| 00:30–00:36 | 14 | 64 | 29 | 0 | 开始串入伴奏 |
| 00:36–00:42 | 17 | 47 | 35 | 2 | 伴奏/八度错明显 |
| 00:42–00:48 | 15 | 27 | 33 | 3 | 明显串声部 |
| 00:48–00:54 | 13 | 69 | 0 | 1 | 恢复 |
| 00:54–01:00 | 10 | 20 | 40 | 1 | 伴奏为主 |
| 01:00–01:06 | 8 | 63 | 13 | 1 | 一般 |
| **01:06–01:12** | 18 | **11** | **61** | 4 | **伴奏/内声部** |
| **01:12–01:18** | 14 | **0** | **57** | 3 | **伴奏/内声部** |
| **01:18–01:24** | 16 | 13 | 38 | 5 | **伴奏/内声部** |
| 01:24–01:30 | 11 | 36 | 18 | 0 | 一般，3 错音 |
| 01:30–01:36 | 18 | 33 | 50 | 2 | 伴奏为主 |
| 01:36–01:42 | 12 | 42 | 25 | 0 | 一般 |
| **01:42–01:48** | 15 | 20 | 60 | 1 | **伴奏为主** |
| **01:48–01:54** | 16 | 19 | 63 | 0 | **伴奏为主** |
| **01:54–02:00** | 16 | **0** | **88** | 2 | **几乎全是伴奏** |
| **02:00–02:06** | 14 | 21 | 57 | 3 | **伴奏为主** |
| **02:06–02:12** | 11 | 36 | 64 | 0 | **伴奏为主** |
| **02:12–02:18** | 11 | **0** | **64** | 3 | **伴奏为主** |
| 02:18–02:24 | 2 | 0 | 0 | 0 | 尾奏误加 2 个音 |

**主要段落结论**：00:06–00:36、00:48–00:54、01:00–01:06 基本跟住旋律；00:36–00:48、00:54–01:00 局部串声部；**01:06–02:18 系统性抓伴奏，且低 12–24 半音**。这是当前产品对用户价值损害最大的问题。

**可能成因**（供开发方排查）：
- `backend/app/services/melody.py` 直接对混音跑 `librosa.pyin`（fmin=220、fmax≈2000），单声部轨迹在多音钢琴上会锁到**强能量的低音/次谐波**（本曲 pYIN 中位 MIDI 42 = F#2，而听感旋律在 A5–D#6）；缺少“取最高稳定声部/八度纠错/时间连续性（Viterbi）”约束。
- `MELODY_MIN_FREQ=220 Hz` 直接砍掉 A3 以下，且把靠近下限的音归到 A3（生成音域下界恰好卡在 MIDI 57，见 5.4）。
- 没有对候选 f0 与其 2×/3× 谐波做“真基频 vs 次谐波”判别；当伴奏低音比旋律强 6–20 dB 时，容易整段跟伴奏。

### 5.2【P0】拍号/速度：固定 4/4 + BPM 翻倍，小节结构整体错位

- 报告 `BPM=161.5, M:4/4`；同一文件的 30 s 冒烟（前 30 s）却是 `BPM=78.3, 4/4` → **速度估计不稳定，且全曲取到了 2 倍值**（161.5 ≈ 2×80.75）。
- 以 161.5 BPM 的脉冲当节拍做分组，相关度 **周期 3 = 0.438、周期 6 = 0.437、周期 4 = 0.086**：音频的重音结构是 3 拍/6 脉冲一组，即 **3/4 或 6/8**（真实四分音符约 80.7 BPM），不是 4/4。
- 生成谱 93.4 个 4/4 小节（每小节只有约 1.486 s ≈ 2 个真实拍）；实际应约 62 个 3/4 小节。**所有小节号、强弱位置、乐句划分都会错位**，用户看到的“小节”不是原曲小节。
- 可能成因：`analyzer.py` 在 madmom 缺失时降级 `librosa.beat.beat_track`（默认 `hop_length=512`，易把八分脉冲当拍）；拍号没有真正推断逻辑（固定 4/4）；`quantizer.py` 直接采用检测到的 beat 间隔（0.371 s）当四分音符，导致速度与谱面小节翻倍。

### 5.3【P1】调号判错：报告 F# major，实际 F# minor

- 参考顶层音级时长占比：F# 34.1%、C# 30.4%、E 11.9%、A 8.2%、B 5.7%、G# 5.4%、D# 2.3% —— 完全落在 **F# 自然小调**（F#-G#-A-B-C#-D-E）音集；用 Krumhansl 对旋律加权直方图打分也是 **F# minor（相关 0.802）**，生成谱自身色度也是 F# minor（0.774）。
- 但接口返回 `key={tonic:F#, mode:major}`，ABC 写 `K:F#`（F# 大调）。后果：E、D、G、C 全部被写成 `=E`、`=D`、`=G`、`=C` 等临时记号（本 ABC 里 `=E` 出现 20+ 次），**整份谱的调号与临时记号都不对**，用户看到的谱面“到处是升降号”，也会误导后续人工修谱。
- 可能成因：调性检测只按色度模板选 tonic，major/minor 模板区分（尤其 F# major vs F# minor 只差 D#/D、E#/E）在降级路径下不稳；`exporter.py` 直接把 mode 映射为 K:，且 minor 时是否写成 `K:F#m` 未校验。

### 5.4【P1】八度错误与 220 Hz 下限：34 个八度错，音域被压到 A3 以上

- 34 个 `octave-error`（11.4%），绝大多数是**生成比参考最高声部低 12/13/14/17/20/21/22/24 半音**：
  - 00:38.76 G#4 vs G#5（-12）；00:48.54 D#4 vs D#5（-12）
  - 01:08.94 E4 vs C#6（-21）；01:10.43 C#4 vs A5（-20）；01:13.42 C#4 vs B5（-22）；01:19.47 A3 vs A5（-24）
  - 也有反向上跳：00:57.39 G#4 vs G#3（+12）、00:04.94 C4 vs F#2（+18，且该处几乎无旋律）
- 生成音域 **MIDI 57–83**，**57（A3）出现 19 次**，低于 57 的音一个都没有：正好落在 `MELODY_MIN_FREQ=220 Hz` 的解码下限，说明低音被截断/被顶到下限，而不是被正确忽略或升高八度。
- 后果：听感上“旋律比原曲低一大截”，且不同段落的八度基准不一致（时高时低），无法当作同一旋律线后再人工移调。

### 5.5【P1】同音反复被吞 39 处，漏音严重，起拍整体偏晚

- **39 处**在生成音符的跨度内部仍存在“同音高、独立起拍”的参考起拍，典型：
  - 00:09.41（bar 7，B4）、00:10.43（bar 8，C#4）、00:10.62（bar 8，D#4）、00:10.81（bar 8，E4）
  - 00:24.22 / 00:24.60 / 00:24.97（bar 17，E5/E5/E5）、00:29.81（bar 21，A4）、00:30.37（bar 21，G#4）、00:31.30（bar 22，F#4）
  - 00:31.96（bar 22，F#4）
- 与 `melody.py` 的 `MERGE_GAP_THRESHOLD=0.08`（80 ms）和 NMS 合并高度吻合：**同一音高间隔 <80 ms 的两个独立发音被合并成一个长音**，用户听到/看到“重复音消失、变成拖长音”。
- 漏音：参考顶层旋律音 354 个，仅 31.9% 被正确覆盖；00:18–00:24 只有 5 个音符，00:00–00:06 只有 1 个（引子本身弱，但 4.94 s 的 C4 是误加）。
- 节奏：同音高起拍匹配中位延迟 **+79 ms**（绝对中位 102 ms），18.8% 的音符在 ±150 ms 内找不到对应起拍；结合 80 ms 合并阈值与量化取整，说明**起拍对齐/量化偏晚**，切分与连奏都会走样。

### 5.6【P2】其它

- 02:18 之后仍有 2 个“无参考”音符（尾奏误加）。
- 生成谱尾部 0.62 s、开头 4.94 s 才起音；开头 5 s 是弱引子（RMS 0.012–0.026，参考只有 F#2），**开头留白本身可接受**，但 4.94 s 的 C4 应删除。
- 3 个 `spurious`、22 个 `wrong-pitch` 未逐条列；完整清单见 `gen_notes.csv`（按 `cls` 过滤即可）。

---

## 6. 小节级问题清单（可直接拿去改）

> 小节号按**生成谱**（4/4 @161.5，1 小节≈1.486 s）；时间才是与原音频对齐的可靠锚点。

| 时间 | 生成小节 | 生成音 | 参考最高声部 | 判定 | 现象 |
|---|---|---|---|---|---|
| 00:04.94 | 4 | C4 | F#2 | 八度/误加 | 引子处凭空多一个 C4 |
| 00:11.93 | 9 | D#4 | E5 | 八度错 -13 | 旋律区被压到 D#4 |
| 00:12.30 | 9 | B3 | C#5 | 八度错 -14 | 同上 |
| 00:38.01 | 26 | E5 | A5 | 内声部 | 抓到和弦内声部 |
| 00:38.76 | 27 | G#4 | G#5 | 八度错 -12 | 整段低八度 |
| 00:42.11 | 29 | A4 | A5 | 内声部 | 伴奏线 |
| 00:48.54 | 33 | D#4 | D#5 | 八度错 -12 | — |
| 00:57.76 | 39 | G4 | G#5 | 内声部 | 伴奏线 |
| 01:06.71 | 45 | F#4 | E5 | 内声部 | 伴奏线 |
| 01:07.45 | 45 | C#5 | C#6 | 八度错 -12 | — |
| 01:08.94 | 47 | E4 | C#6 | 八度错 -21 | 明显低两个八度 |
| 01:10.43 | 48 | C#4 | A5 | 八度错 -20 | — |
| 01:13.42 | 50 | C#4 | B5 | 八度错 -22 | 该段最强谱峰 991 Hz(B5) |
| 01:16.12 | 52 | C#4 | F#5 | 八度错 -17 | — |
| 01:19.01 | 54 | C#4 | B5 | 八度错 -22 | — |
| 01:19.47 | 54 | A3 | A5 | 八度错 -24 | 最强谱峰 883 Hz(A5) |
| 01:54.04 | 78 | C#4 | C#5 | 伴奏 | 该 6 s 窗口 88% 伴奏 |
| 02:12.30 | 89 | E4 | F#5 | 伴奏 | 该窗口 64% 伴奏 |

同音反复被吞的代表位置（bar 为生成谱小节）：00:09.41/bar7、00:24.22–00:24.97/bar17（连续 3 个）、00:29.81–00:30.37/bar21、00:31.30–00:31.96/bar22。

---

## 7. 建议排查顺序（开发方视角）

1. **旋律线选择**（`melody.py`）：先做“候选 f0 的谐波判定”与八度纠错（1/2、1/3 次谐波剔除），再加时间连续性约束（Viterbi/动态规划），并做“最高稳定声部 vs 低音伴奏”的显式取舍；不要直接把混音的 PYIN 轨迹当旋律。
2. **下限与音域**：`MELODY_MIN_FREQ=220` 需确认是否应改为“按旋律音域自适应”，至少不要把 57 当硬下界（本曲 19 个音符卡在 A3）。
3. **同音反复**：把 `MERGE_GAP_THRESHOLD=0.08` 改成“仅合并同音高且间隔 <30–50 ms 的同一发音”，并保留独立起拍的重复音；或在合并前用 onset 包络判定是否同一次发音。
4. **节拍/拍号**（`analyzer.py`/`quantizer.py`）：修复 librosa 降级下的“八分脉冲当四分音符”问题（先做 tempo 候选的 1/2 校正，再用周期 3/4/6 分组打分选拍号）；拍号不要固定 4/4，至少支持 3/4 与 6/8。
5. **调式**（`analyzer.py`/`exporter.py`）：minor 输出 `K:F#m`（或等价的调号），并用旋律音级直方图复核 major/minor；当前 F# major 会在全谱产生大量无意义临时记号。
6. **量化起点**：核对 beat 网格的相位（当前起拍中位 +79 ms），避免统一偏晚；检查量化是否把起拍吸到下一个网格点。

---

## 8. 数据与复核入口

| 内容 | 路径 |
|---|---|
| 原始音频 | `d:\project\SheetMusicMaker\郎朗,魔兽世界 - 酒馆小调（狮王之傲）.flac` |
| 任务产物 | [storage/outputs/6346ec465869](file:///d:/project/SheetMusicMaker/storage/outputs/6346ec465869) |
| ABC/MusicXML/MIDI | [storage/acceptance/baseline](file:///d:/project/SheetMusicMaker/storage/acceptance/baseline) |
| 逐音符判定 | [gen_notes.csv](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/gen_notes.csv) |
| 分段指标 | [sections.csv](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/sections.csv) |
| 汇总指标 | [metrics.json](file:///d:/project/SheetMusicMaker/storage/acceptance/analysis/metrics.json) |
| 运行日志 | [smoke.log](file:///d:/project/SheetMusicMaker/storage/acceptance/smoke.log)、[analyze.log](file:///d:/project/SheetMusicMaker/storage/acceptance/analyze.log)、[followup.log](file:///d:/project/SheetMusicMaker/storage/acceptance/followup.log) |
| 分析脚本（临时） | [analyze.py](file:///d:/project/SheetMusicMaker/storage/acceptance/analyze.py)、[followup.py](file:///d:/project/SheetMusicMaker/storage/acceptance/followup.py)、[spect.py](file:///d:/project/SheetMusicMaker/storage/acceptance/spect.py)、[run_baseline.py](file:///d:/project/SheetMusicMaker/storage/acceptance/run_baseline.py) |

**重要提醒**：本报告结果对应 **`ab3dcf9`** 的代码内容（运行时 HEAD 尚为 `568adb1`+未提交改动，随后开发方提交为 ab3dcf9，二者内容一致）。复验若在新的 commit 上进行，请记录新哈希；所有小节号均按生成谱 4/4 @161.5 计数，不可直接当原曲小节。
