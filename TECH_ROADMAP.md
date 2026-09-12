# LiveTrans 技术路线与现状

> 实时语音翻译工具（Python）——系统声音/麦克风 → 本地识别 → LLM 翻译
> → 置顶双语字幕 / 悬浮外挂字幕。
> **Linux 与 Windows 两个平台平级、同步开发、共同发行**（Android 见里程碑）。

## 目录

0. [平台布局与协作约定](#0-平台布局与协作约定)
1. [功能现状](#1-功能现状)
2. [系统架构与数据流](#2-系统架构与数据流)
3. [技术选型决策记录](#3-技术选型决策记录)
4. [里程碑与完成情况](#4-里程碑与完成情况)
5. [目录结构（模块地图）](#5-目录结构模块地图)
6. [开发环境与依赖](#6-开发环境与依赖)
7. [待办与已知问题](#7-待办与已知问题)
8. [附录：开发日志](#8-附录开发日志)
9. [风险与备选](#9-风险与备选)

---

## 0. 平台布局与协作约定

两个平台**各有一份完整实现**，代码互不干扰；共享的只有本文档、根 `README.md` 与发版流程。

```
platforms/
├── linux/      Linux 版：Debian/Ubuntu 发行，GTK3 外挂，parec 抓系统声音，tests/run.sh
└── windows/    Windows 版：便携 zip / 安装器，Qt 外挂，WASAPI loopback，tests/run.py
```

**协作约定（重要）：**

1. **Linux 版已冻结为稳定基线**——功能与行为不再主动变更，只在修 bug 时小步改动。
2. **Windows 版由 Linux 版复制而来**，在副本里独立演进；Windows 专属改动**只进 `platforms/windows/`**。
3. **没有主次之分**：两个平台都是正式支持目标，发版时同时产出两者的安装包，挂在同一个 Release。
4. **可共享的改动要两边都改**：修业务逻辑（识别/翻译/声纹/导出/配置等跨平台部分）的 bug 时，
   应考虑是否同样适用于另一平台；有则两边都提交，并在日志里注明「双平台」。
5. **平台差异处保持对照**（新增功能时两边都要有）：

| 能力 | Linux | Windows |
|---|---|---|
| 系统声音 | PulseAudio/PipeWire monitor 源（`parec` 子进程） | WASAPI loopback（`soundcard`） |
| 字幕外挂 | GTK3 + cairo + Pango（逐像素 alpha） | PySide6 / Qt6 |
| 数据目录 | `$XDG_DATA_HOME/livetrans`（`paths.py`） | `%LOCALAPPDATA%\LiveTrans`（`platformdirs`） |
| 资源监控 | 读 `/proc/stat`、`/proc/meminfo` | `psutil` |
| 外部进程 | `start_new_session=True`、`SIGTERM` | `creationflags`、`KeyboardInterrupt` |
| 打包 | `build_deb.sh` → `.deb` | `build_windows.py` → 便携 zip |
| 测试入口 | `tests/run.sh`（bash + timeout） | `tests/run.py`（Python，跨平台） |

> 统一的移植技术底账见 [WINDOWS_PORT_BRIEF.md](WINDOWS_PORT_BRIEF.md)（该文件为交接材料，不随包发布）。

---

## 1. 功能现状

| # | 需求 | 现状 | 实现要点 |
|---|------|------|----------|
| R1 | 本地模型 / LLM API 翻译 | ✅ | OpenAI 兼容协议一套通吃：DeepSeek/GLM/千问/Kimi/OpenAI/Gemini/Claude/Grok + Ollama（本地） |
| R3 | 识别内外部语音并标注来源 | ✅ | 双路独立捕获（parec monitor / PortAudio），字幕分栏 + 颜色标注 |
| R4 | 内外部音频同时翻译 | ✅ | 每路一个 ASR worker，共享翻译层（线程池，默认并发 2） |
| R5 | 标注音频来自哪个软件/标签页 | ⏳ M4 | Linux 可用 PulseAudio 流元数据；浏览器需 tabCapture 扩展 |
| R6 | 实时字幕 + 翻译 | ✅ | webrtcvad 切段 → SenseVoiceSmall ONNX → LLM 流式翻译 → 置顶字幕窗 |
| R7 | 普通/专业模式 | ✅ | 领域提示词 + YAML 术语表注入 |
| R8 | 上下文理解 | ✅ | 滑动窗口注入最近 N 句（原文+译文） |
| R9 | 总结等附带功能 | ✅ | 会话 JSONL → 总结（主题/要点/行动项）；另有基于会话的对话助手 |
| R10 | Win / Linux / Android | ⏳ | 当前 Linux 桌面（Python）；跨平台见里程碑 |
| — | 字幕外挂（悬浮字幕） | ✅ | GTK3 逐像素透明 + 悬停控制面板 + 自带管线，可运行时切音频源 |
| — | 本地模型服务自管理 | ✅ | 自动拉起 `ollama serve`、检查模型是否已 pull、显示模型显存与资源占用 |
| — | 体验细节 | ✅ | 中文自动译英、模型测速、Key 归属识别、历史记录按服务商隔离、全局悬停提示 |
| — | 声纹角色标注 | ✅ | sherpa-onnx CAM++ 说话人向量（192 维）+ 在线聚类，换人自动标 S1/S2 并配色；模型首次启用自动下载 |
| — | 对话模式（角色制） | ✅ | 两栏=你/对方，**每路各自选音频来源与翻译方向**（可同源，同源则各翻一遍）；换来源不打断上下文；暂停按角色 |
| — | 字幕导出 | ✅ | 会话 JSONL → SRT（双语/仅译文/仅原文）/ TXT；时间轴由耗时推算、单调不重叠 |
| — | 外挂镜像模式 | ✅ | 只跟随主程序会话日志显示，不重复识别/翻译（两程序同开不再双份消耗） |
| — | 本地模型显存管理 | ✅ | 常驻模型一览 +「释放显存（取消挂载）」；可选空闲自动卸载 / 启动清理旧模型 / 退出卸载 |
| — | 弱网降级 | ✅ | 云端连续失败 2 句自动切本地模型并补翻当前句，每 60s 探云端，恢复自动回切 |

## 2. 系统架构与数据流

```
音频源（麦克风 / 系统声音，可单独或同时；外挂可运行时切换）
   ├── AudioCapture（sounddevice / PortAudio）   麦克风
   └── ParecCapture（parec → monitor 源）        系统播放声
            │  队列<音频块 16kHz float32>
            ▼
   ASRWorker（每路一个线程）
            webrtcvad 帧状态机切段 → SenseVoiceSmall ONNX 转写
            → 词数断句（长句拆条）→ on_partial 实时原文 / on_level 音量
            │  队列<SegmentEvent>
            ▼
   TranslatorWorker（线程池，默认并发 2）
            上下文窗口 + 术语表 + 普通/专业模式 → OpenAI 兼容流式翻译
            显示乱序上屏（先完成先显示）；上下文与 JSONL 日志按提交顺序落盘
            │
            ├──▶ SubtitleWindow（Tkinter 主字幕窗：双栏/聚焦、滚动、样式面板）
            └──▶ OverlayWindow（GTK3 悬浮字幕：逐像素透明 + 悬停控制面板）

   Launcher（Tkinter 控制台）：配置 / Key / 模型下拉与实拉 / 测速 / 总结 / 对话 / 外挂启停
```

关键设计决策：

- **多路并行 = 多条独立捕获流**，不混音后分离：实时场景盲分离不可行，每路独立 VAD+ASR 天然带来源标签。
- **引擎与 UI 只通过队列通信**：捕获 → ASR → 翻译 → 显示全队列解耦，换 UI（Tk/GTK3）不动引擎。
- **显示与记录分离**：显示要快（乱序上屏 + 流式增量），上下文与日志要准（按提交顺序落盘）。
- **翻译后端统一为 OpenAI 兼容协议**：切后端 = 改一行配置；本地模型（Ollama）走同一协议。
- **窗口栈分工**：主字幕窗用 Tk（控件与样式面板方便），外挂用 GTK3（Tk 的 `-alpha` 是整窗属性，做不到逐元素透明）。

## 3. 技术选型决策记录

### ASR
- **选定：SenseVoiceSmall（funasr_onnx）**——非自回归，CPU 上比 Whisper 快约 10 倍，支持中英日韩粤；离线运行，模型在项目 `models/sensevoice/`。
- 早期用过 faster-whisper，因速度与体积劣势已彻底移除（相关代码与模型文件均已删除）。

### 翻译
- **选定：LLM API 抽象层 + 本地模型服务**。术语表、上下文、总结都必须 LLM；OpenAI 兼容已成事实标准。
- 本地：**Ollama**（控制台自动拉起服务、查模型清单与显存占用、未 pull 时提示）。
- llama.cpp / llama-server 路线**已放弃并移除**（需自行安装服务端、模型文件与 Ollama 副本重复 2.4GB）。

### 音频捕获
| 平台 | 内部音频 | 来源标注能力 |
|------|----------|--------------|
| Linux（当前） | PulseAudio/PipeWire `.monitor`（parec 直连最可靠，PortAudio 兜底） | 可按 monitor 源区分输出设备 |
| Windows | WASAPI Loopback / Process Loopback | 精确到进程（未实现） |
| Android | AudioPlaybackCapture + MediaProjection | 可查各 App 音频会话（未实现） |

### UI
- 控制台 + 主字幕窗：**Tkinter**（零额外依赖、启动快、控件丰富）。
- 字幕外挂：**GTK3 + cairo + Pango**（逐像素 alpha、抗锯齿圆角、精确文字排版）。

## 4. 里程碑与完成情况

**M1（Linux 桌面 MVP）——已达成并超出**：双路音频、VAD 切段、本地识别、LLM 流式翻译、字幕窗、
术语/模式/上下文、JSONL 日志、总结 CLI；另完成图形控制台、字幕外挂、总结/对话双后端、
本地模型服务自管理、Key 归属识别、模型测速、资源监控、依赖自检。

| 里程碑 | 目标 | 状态 |
|--------|------|------|
| M2 | **Windows 版**（WASAPI loopback / Qt 外挂 / 便携打包） | 🚧 进行中（仓内已分叉出 `platforms/windows/`） |
| M3 | partial 增量字幕、上下文压缩、更多翻译后端 | 部分（流式 partial 已实现） |
| M4 | 浏览器扩展（tabCapture + WebSocket）：标签页级来源标注 | 未开始 |
| M5 | Rust + Tauri 正式版 + Android | 未开始 |
| M6 | 打磨：打包分发、全局热键、开机自启、自动更新 | 部分（应用菜单、外挂样式已完成） |

### Windows 版分步计划（M2 展开）

每个阶段结束都要能运行、能自测；平台差异用例在非目标平台自动 `[SKIP]`。

| 阶段 | 内容 | 验收 |
|---|---|---|
| W0 骨架 | 目录分叉（`platforms/linux` 冻结 + `platforms/windows` 复制）、CI 双 job、`tests/run.py`、`build_windows.py` | ✅ 两平台 CI 都能跑；Windows 侧 `python tests/run.py` 可用 |
| W1 麦克风链路 | 装 pip 依赖 → 跑通自检 → 主字幕窗 + 识别 + 翻译 | ✅ 自检 10/10 通过；`AudioCapture` 跨平台可用（待真人说话验收） |
| W2 系统声音 | WASAPI loopback（`soundcard`）实现「内部音频」，重采样到 16k 单声道 | ✅ 采集实测 4 源、时间轴 1.003、全链路合成人声验证通过 |
| W3 字幕外挂 | PySide6 重写 overlay：逐像素透明 + 点击穿透 + 置顶 + 多屏 + DPI | ✅ Qt6 版完成，功能/视觉对齐 Linux 版；实跑字幕上屏成功 |
| W4 全面回归 | 声纹 / 会话 / 导出 / 镜像 / 弱网降级全链路 | ✅ 声纹实测可用；专项测试 12 文件全绿 |
| W5 打包发版 | PyInstaller one-dir → 便携 zip（+ 可选 Inno Setup 安装器） | ✅ 打包成功（621MB）、exe 冒烟与端到端通过 |

## 5. 目录结构（模块地图）

```
translate/
├── TECH_ROADMAP.md           # 本文件（现状 + 决策 + 开发日志，双平台共用）
├── README.md                 # 快速开始 / 安装 / 配置 / 排障（双平台共用）
├── WINDOWS_PORT_BRIEF.md     # Windows 移植交接材料（不随包发布）
├── .github/workflows/        # test.yml（linux + windows 两个 job）/ release.yml（同时出两平台产物）
└── platforms/
    ├── linux/                # Linux 版（稳定基线）
    │   ├── launcher.py       # 控制台外壳（窗口骨架/装配/配置读写/启动控制）
    │   ├── selfcheck.py      # 自检脚本
    │   ├── diagnose.py       # 音频链路诊断脚本
    │   ├── requirements.txt  # pip 依赖 + 系统包说明
    │   ├── packaging/        # build_deb.sh / install.sh / installer_gui.py / livetrans.desktop
    │   ├── install-desktop.sh / livetrans-gui.sh
    │   ├── assets/livetrans.svg
    │   ├── models/           # sensevoice + speaker（本地模型）
    │   ├── sessions/         # 会话 JSONL（每次启动一份）
    │   ├── tests/run.sh      # 测试入口（bash）
    │   └── livetrans/
    │       ├── main.py       # 主程序入口与启动链
    │       ├── overlay.py    # 字幕外挂（GTK3）
    │       ├── subtitle.py   # 主字幕窗（Tkinter）
    │       ├── asr.py        # VAD + SenseVoice + 模型下载
    │       ├── capture.py    # 麦克风 / 系统声音采集（parec monitor）
    │       ├── translate.py  # LLM 翻译 / 总结 / 对话
    │       ├── summarize.py  # 总结 CLI
    │       ├── config.py     # 配置加载（YAML → dataclass）
    │       ├── deps.py       # 运行依赖自检
    │       ├── keys.py       # Key 存储 / 服务商识别 / 历史
    │       ├── providers.py  # 服务商数据层（模型列表拉取）
    │       ├── paths.py / langs.py / sysmon.py
    │       └── ui/           # theme / widgets / page_* / key_ui / overlay_card
    └── windows/              # Windows 版（自 Linux 版复制后独立演进）
        ├── packaging/build_windows.py   # PyInstaller one-dir → 便携 zip
        ├── requirements.txt  # 含 Windows 专属：soundcard / psutil / platformdirs / PySide6
        ├── tests/run.py      # 测试入口（Python，替代 bash）
        └── livetrans/        # 结构与 Linux 版一致，平台差异文件待改造（见第 4 节）
```

## 6. 开发环境与依赖

- **Python 3.10.12**；pip 依赖：numpy、sounddevice、webrtcvad-wheels、funasr_onnx、PyYAML、openai
- **系统包**：python3-tk、python3-gi、gir1.2-gtk-3.0、gir1.2-pango-1.0、python3-cairo、pulseaudio-utils、fonts-noto-cjk
- **可选**：Ollama（本地模型服务）；NVIDIA 驱动 + CUDA（GPU 推理——本机 RTX 4070 Laptop 8GB，qwen3:4b-instruct 热态出字 ~41ms）
- **依赖自检**：`python3 -c "from livetrans.deps import missing_pip_deps, missing_overlay_deps; print(missing_pip_deps(), missing_overlay_deps())"`；控制台启动外挂前也会自动检查并给出 apt 命令
- 已知环境坑：本机系统包 flatbuffers 版本号非法 → pip 安装需加 `--ignore-installed`

## 7. 待办与已知问题

| 项 | 说明 |
|---|---|
| 标签页级来源（R5） | 要区分"哪个标签页/哪个应用"的声音，需 PulseAudio 流元数据（M4）+ 浏览器扩展 |
| 打包形态 | 已有 `packaging/build_deb.sh`（Debian/Ubuntu）；rpm / AppImage / PyInstaller 未做 |
| 首次依赖 | deb 只声明 apt 包；sounddevice/funasr_onnx/openai 等 pip 包需跑 `install-python-deps.sh` |
| 跨平台（R10） | 当前 Linux/X11（Wayland 穿透走 `set_pass_through` 兜底）；Windows/Android 见里程碑 |
| 已知无害项 | parec 退出时 C++ runtime 打印 `terminate called without an active exception`，不影响功能 |

---

## 8. 附录：开发日志

**追记（同日）**：①Windows 包改为**离线版**（构建前预下载识别/声纹模型，
便携 zip 389MB / 安装器 338MB，替代在线版；在线版今后不再发布）；
②踩坑记录：**删除远端标签重推会把已发布的 Release 变孤儿/草稿**（公开列表
不可见）→ 两个 release job 的更新路径强制 `gh release edit --draft=false`；
③Inno 向导默认装 {autopf}（C 盘 Program Files）但"选择安装位置"页可改任意盘符。

### 2026-09-12（二十七）：双平台独立发版 + Windows CI 全绿

**双平台独立 Release**（用户需求：Linux/Windows 版本号不互通、命名带平台后缀）：
- `release.yml` 重写：tag `v1.2.3-linux` 只构建/发布 Linux，`v1.2.3-win` 只构建/
  发布 Windows；版本号 = tag 去后缀（手动触发时各读各平台的 `__version__`）；
  Release 标题 "LiveTrans Linux/Windows v1.2.3"，产物名带 `-win` 后缀；
- 首个 Windows Release 已发布：**LiveTrans Windows v1.0.0**
  （便携 zip 153.6MB 在线版 + Inno 安装器 103.4MB + SHA256SUMS）；
  旧 v1.0.0 Release（仅 Linux deb）保留不动。

**Windows CI 三连修**（每个都是通过新增的 `::error` 注解远程定位的）：
1. **wheel 排除炸打包**：`ValueError: Target module "wheel" already imported as
   ExcludedModule` —— `--exclude-module wheel` 与 PyInstaller setuptools hook
   冲突（venv 复现确认）；EXCLUDES 移除 pip/wheel（与"不要排除 setuptools"
   同类坑，已在注释中警告）；
2. **PYTHONUTF8 缺失**：runner 默认 cp1252，所有中文 print 直接
   UnicodeEncodeError（测试/打包双中招）→ 两个 workflow 顶层加
   `env: PYTHONUTF8: "1"`；
3. **临时目录清理竞态**：test_fallback/test_router/test_mirror 的会话 jsonl
   被 worker 占用 → `TemporaryDirectory(ignore_cleanup_errors=True)`；
4. **声纹降级测试前提失效**：CI 联网导致模型自动下载成功，"缺模型返回空标签"
   的前提不成立 → 改为打桩 `ensure_speaker_model` 返回 None 的确定性验证。

**诊断机制（对齐 Linux 版 run.sh 的 ::error 注解）**：tests/run.py 与
build_windows.py 在失败时把错误末尾行打进 `::error title=...::` 注解——
注解走 check-runs API **匿名即可读**，彻底解决"日志要管理员权限才能拉"。

**验证**：GitHub Actions test 工作流 **Linux + Windows 双平台 success**；
release 工作流 success 且 Release 产物齐全。

### 2026-09-11（二十六）：死代码清扫（仅 Windows 版）

方法：AST 收集全树定义（函数/类/方法）→ 全文本引用计数（注释/字符串提及
也算引用，宁多留不误删）→ 逐个人工复核 → 删后全量回归。

**已删**：
- `langs.dialog_audio_label()`、`sysmon.ollama_vram_gb()`、
  `keys.remove_history()` —— 全树零引用（各自保留被真实使用的姊妹函数）；
- `packaging/installer_gui.py` 的 `PlanState` + `_pip_ok()`（含孤儿化的
  `field` 导入）—— 向导演进遗留，文件内外均无引用；
- `tests/test_installer.py` —— 守的是 Linux install.sh，该脚本在 Windows
  树中不存在，此用例在**任何平台**都无法通过（纯死文件）；
- `build/`（PyInstaller 临时工作目录，63MB，每次构建重建）。

**复核后保留（易误判项）**：`asr.isatty`（stdout 包装类的流协议方法，tqdm
动态调用）、`summarize.py`（page_sessions 以 `python -m livetrans.summarize`
子进程调用）、`diagnose.py`（手动诊断工具）、`installer_gui.py`（控制台
「安装向导」按钮真实拉起，含 Windows 进程适配）、`test_installer_gui.py`
（平台门控跳过，守向导 dry-run/二次确认不变量）、`run.log`（活跃运行日志，
自动轮转）。

**验证**：死代码扫描复扫（函数级候选 6→1，仅剩协议方法）；py_compile +
全套件 15 文件全绿 + selfcheck 10/10。

### 2026-09-11（二十五）：M5 收尾——拆包后打包回归 + Inno Setup 安装器

- **拆包后打包回归（实测）**：`build_windows.py --portable` 真实构建
  **619 MB**（含 models/ 258MB）→ `dist/LiveTrans/LiveTrans.exe`；
  frozen 冒烟：`LiveTrans.exe --overlay` 启动存活 10s 无导入崩溃。
  overlay 拆包对打包链路零影响（入口 run_livetrans.py + lazy import 被
  PyInstaller 静态分析正常跟随）。环境坑：PyInstaller COLLECT 清空旧
  dist（1382 文件）会触发环境的批量删除保护 → 先手动清 dist 再构建。
- **打包契约回归测试**：新增 `tests/test_packaging.py`（4 组）：入口
  run_livetrans.py 与 --overlay 转发、overlay 包 -m 入口（__main__.py）、
  build_windows 素材齐全（且不得再引用拆包前的 overlay.py 路径）、
  `app_command` 源码 -m / frozen exe--overlay 双语义（monkeypatch
  sys.frozen）；
- **Inno Setup 安装器**：新增 `packaging/installer.iss`（装 {autopf}，
  数据自动落 %LOCALAPPDATA%\LiveTrans——paths.DATA_DIR 约定天然适配
  只读安装；开始菜单含「字幕外挂 --overlay」入口；可选桌面图标）；
  `release.yml` 接入（choco 装 innosetup → ISCC /DVERSION 编译 →
  `-setup.exe` 进 artifact 与 Release 汇总 + 发版说明更新；步骤
  continue-on-error，安装器失败不阻断 zip 发版）。

**验证**：真实构建 + frozen 冒烟 + 全套件 16 文件全绿（新增
test_packaging 5 项断言），selfcheck 10/10。M5 的可选安装器自此补齐；
M6（热键/自启/自动更新）未动。

### 2026-09-11（二十四）：结构优化第二批（SourceManager 抽取 + 管线测试 + 命名澄清）

- **P2-2 `run_overlay` 测试接缝**：把音频源启停/切换/暂停的闭包群抽成
  `overlay.pipeline.SourceManager` 类（GUI 鸭子类型解耦：sink 只要求
  set_level/set_partial/set_status；`source_factory` 注入点供测试替换假源；
  `persist` 回调解耦落盘）。`run_overlay` 瘦身为纯装配（字体设置 → 窗口 →
  SourceManager → boot 线程 → app.exec）。新增
  `tests/test_overlay_pipeline.py`：替身 ASRWorker（只等停止事件）+ 假采集，
  6 组用例覆盖按 kind 启停切换、非法来源归一化 + persist、暂停/继续/
  stop_all、不可用源跳过提示、全不可用占位提示、ASR 未就绪空转与镜像模式
  守卫——**全程不需要音频设备**；
- **P2-1 capture 命名澄清**：新增 `WasapiLoopbackCapture = ParecCapture`
  别名（Windows 上是 WASAPI loopback 而非 parec 子进程）；历史名保留
  （main/pipeline/diagnose 按旧名引用），配置键不动；
- **P2-3 修正**：复核发现 `.github/workflows/test.yml` **已有 windows job**
  （windows-latest + tests/run.py），评审报告该项判断有误，无需新增。

**验证**：`tests/test_overlay_pipeline.py` 7 项断言全过；全套件 15 文件
（含新文件）全绿，selfcheck 10/10。

### 2026-09-11（二十三）：代码结构优化第一批落地（P0 全部 + P1-1 + P1-3 色表）

按《CODE_STRUCTURE_REVIEW.md》执行，每步全绿再进下一步：

- **P0-1 import 清理**：`ui/` 六个页面/卡片文件（page_audio/page_backend/
  page_chat/page_sessions/overlay_card/key_ui）删除 42 行复制粘贴的
  stdlib import（AST 外正则检测未使用项，py_compile + 套件验证）；
- **P0-2 配置唯一写入层**：新增 `livetrans/configstore.py`
  （`patch_section` 读→合并单段→写回、`refresh_sections` 保存前拉新、
  `read_section`）。三个写入方全部收口：`overlay.persist_overlay` 变委托；
  `main._persist_subtitle_style/_persist_dialog` 变委托；
  `launcher._apply_and_save` ①外挂托管键（pos/宽/偏移/配色）一律取磁盘
  最新（旧代码 pos_x=0 会被真值判断吞掉的 bug 一并修掉），字号/两根透明度
  仅当控制台未动过滑条时取磁盘 ②整份重写前 refresh subtitle/dialog 段，
  不再用启动快照冲掉字幕窗运行中的修改。新增 `tests/test_configstore.py`
  （5 组用例：合并/并发不丢键/段内合并不覆盖/兼容委托/拉新）；
- **P0-3 Tooltip 收口**：删除 `subtitle._Tooltip`（51 行），12 处调用点
  切到 `ui.widgets.ToolTip`（主页同款观感，含置顶属性与 winfo_exists 防护）；
- **P1-1 overlay 拆包**：`livetrans/overlay.py`（1484 行）→
  `livetrans/overlay/` 包（__init__ re-export 保住全部导入路径与
  `python -m livetrans.overlay` 入口 + __main__.py）：
  `_common.py`（配色/_log/_rounded_path）/ `win32.py` / `persist.py` /
  `window.py`（OverlayWindow 478 行）/ `panel.py`（面板+弹窗+气泡 620 行）/
  `pipeline.py`（run_overlay + main）。函数内相对导入 `.keys/.sysmon`
  升级为 `..keys/..sysmon`；测试的 Win32 常量导入经 __init__ re-export 保留。
  教训：迁移脚本按行号切片丢了两行（PANEL_BTN_HOVER/HWND_NOTOPMOST），
 靠测试逐一暴露后补齐——"每步全绿"纪律再次兜底；
- **P1-3 色表收口（部分）**：subtitle.py 与 ui/theme 重复的 6 个色值
  （INK/SIGNAL/PANEL/PANEL_2/BORDER/INTERNAL）改为从 theme 导入；
  字幕窗特有 4 色保留并注明与控制台的刻意差异（BG 更暗/WARN 更橙）。

**未做（下一步候选）**：subtitle `_Pane` 物理拆分（值低风险中，暂缓）、
P1-2 控制台配置单表示（建议 M5 打包前做）、P2-2 run_overlay 测试接缝、
P2-3 CI windows job。**验证**：selfcheck 10/10 + 全套件 14 文件全过
（test_overlay_qt 20 项）。

### 2026-09-11（二十一）：修复设置弹窗透明回归 + 控制台/字幕窗字体美化（ui-ux-pro-max 协作）

**① 透明回归修复**：用户反馈 ⚙ 设置弹窗背景变透明——上一轮把顶层背景交给
QSS `QWidget` 规则 + `WA_StyledBackground`，实测在 `WA_TranslucentBackground`
的半透明 Tool 窗上不可靠（offscreen 探针：弹窗中心 alpha=0）。
修复：顶层背景改由 `_TipHost.paintEvent` 手绘 9px 圆角矩形（与字幕窗同一套
画法），QSS 只管子控件（`QWidget` 规则去掉 background/border-radius），
两个窗口都去掉 `WA_StyledBackground`。新增
`test_panel_popup_background_painted`（grab 像素采样：边缘 #1c2230 不透明、
四角透出）→ 外挂测试 20 项全过。

**② 控制台/其他窗口美化**（应用 ui-ux-pro-max 设计系统检索：深底+高对比+
明确悬停反馈方向与现有一致，最大短板在字体）：
- `theme.pick_fonts()` 补 **Windows 字体候选**（Microsoft YaHei UI/雅黑 →
  Noto/WenQuanYi；等宽 Cascadia Mono/Consolas）——此前只有 Linux 候选，
  Windows 上整个控制台回退宋体（移植简报 3.1-13 的遗留项）；
- `theme.ui_family()` 懒解析助手（导入期不能枚举字体族）：字幕窗
  `subtitle.py` 28 处写死 `("sans", …)`、`ui/widgets.py` 提示/气泡默认字体
  全部收口到雅黑栈；主字幕窗文字/按钮/tooltip 与控制台观感统一；
- 次级文字 DIM 统一为 #8b93a7（与外挂面板一致）。
验证：`test_ui_theme`（按钮两档同高/输入域同高，相对断言不写死像素）全过，
全套件绿，自检 10/10。

### 2026-09-11（二十）：外挂 UI 打磨（界面/按钮/文字）

功能稳定后的一轮视觉优化，只动样式与文字层级，不改行为：

- **字体**：面板/设置弹窗/提示气泡统一中文字体栈
  （Microsoft YaHei UI → 雅黑 → PingFang SC → Noto Sans CJK SC）；
  `run_overlay` 给 QApplication 设同一族字体——Qt 简中 Windows 默认 CJK
  回退是宋体（衬线、小字号发虚），字幕绘制 `_fonts()` 的 `QFont()` 默认
  构造会继承 app 字体，字幕文字一并受益；
- **按钮**：加描边（默认/hover/pressed 三态）、checked 态带边框；
  「退出」用 `#exitBtn` objectName 上危险色 hover（暗红底/粉红字）；
- **文字层级**：面板滑条与弹窗数值拆成「灰标签 + 青色数值」两档
  （`QLabel[role=dim/val]` 属性选择器，对齐控制台 theme），
  数值右对齐定宽不再抖动；「声音来源」标签同步转灰；
- **形状**：面板与设置弹窗 `WA_StyledBackground + WA_TranslucentBackground`
  + QSS `border-radius: 9px` → 四角圆角透出背后画面；滑条改 6px 细槽 +
  14px 大圆点（hover 变白）；面板边距/间距放宽（8/7/6）；
  弹窗滑条加宽到 160、数值列定宽 34。

**测试**：`test_settings_popup_draggable_and_labels` 补断言
（dim/val 角色、op_val 与 cfg 同步、exitBtn objectName）→ 19 项全过，
套件全绿，自检 10/10（面板悬停提示控件数 15→17：新增两个数值标签）。

### 2026-09-11（十九）：配色改为"会话生效 + 保存方案"（重启回默认）

用户反馈：外挂字幕的颜色和背景希望每次重启回到默认，要么提供「保存方案」。
两个诉求一并满足，取显式控制语义：

- **颜色不再随几何落盘**：`persist_geom` 移除 `text_color`/`bg_color`——此前
  拖动窗口/调字号/退出都会把当时试的颜色固化进 config.yaml；
- **⚙ 弹窗新增两个按钮**：「保存方案」→ 新增 `OverlayWindow.persist_scheme()`
  只把当前文字/背景颜色写盘（下次启动沿用）；「恢复默认」→ 白字黑底 +
  覆盖已存方案；取色器改动仍实时预览，但只在本会话生效（日志会提示）；
- **控制台保存不再冲掉方案**：`_apply_and_save()` 里 overlay 的
  text_color/bg_color 改为从磁盘最新值补齐（self.cfg 是启动快照，
  外挂后来保存的方案不在里面——与（十七）"丢颜色"同类坑）；
- **存量试验配色已清**：config.yaml 里用户此前试出的
  `#b0ff70`/`#699faa` 按新语义移除（重启即回默认白字黑底；想要可重新
  取色后点「保存方案」）。

**测试**：新增 `test_scheme_persist_and_reset`（几何不带色 / 保存写盘 /
恢复默认覆盖）→ `test_overlay_qt.py` 19 项全过，套件全绿，自检 10/10。

### 2026-09-11（十八）：外挂面板/设置弹窗悬停提示气泡（对齐控制台 ToolTip）

用户反馈：字幕栏顶部选项悬停时要有简洁明了的提示（主页控制台的提示体验不错）。

**根因**：面板/设置弹窗是**永不激活**的窗（WS_EX_NOACTIVATE，不抢焦点），
原生 QToolTip 的触发在这种窗上不可靠——（十六）加的 `WA_AlwaysShowToolTips`
实测用户仍看不到提示。

**方案**：自绘深色气泡接管，交互模型照搬控制台 `ui/widgets.ToolTip`
（#0b0f18 底 + 浅字，450ms 延迟，Enter 弹出 / Leave·点击·隐藏即收起）：
- `TipBubble(QLabel)`：单例、`Qt.ToolTip` 无边框置顶窗、
  `WA_TransparentForMouseEvents`（永不拦鼠标）、超宽 380px 自动换行、
  贴控件下方，出屏时翻到控件上方并钳回所在屏 availableGeometry；
- `_TipHost` 混入：对容器及全部子控件装事件过滤器，Enter→schedule 延迟弹出、
  Leave/Hide/MouseButtonPress→收起；提示文本仍存各控件 `setToolTip`
  （单一数据源）；ControlPanel/SettingsPopup 的 `hide()` 一并收起气泡；
- 移除两处 `WA_AlwaysShowToolTips`（避免与自绘气泡双重显示）。
- 面板 15 个控件全部有提示文案（此前已配好，数据源无需改）。

**测试**：`test_overlay_qt.py` 的 tooltip 用例改写为 `test_hover_tips_bubble`
（控件全覆盖 + 显示/收起/取消 + 气泡不拦鼠标）；顺带修正
`test_bg_color_change_reaches_paint`——起点色不再写死 #000000
（用户实测已把背景色调成 #699faa 并持久化，恰证明（十七）的颜色修复生效），
先归零→改色→还原。18 项全过，套件全绿。

### 2026-09-11（十七）：外挂设置弹窗二轮反馈 4 项（拖动/背景色/文案/翻译来源答疑）

用户实测反馈 4 项，全部处理：

1. **⚙ 设置弹窗无法用鼠标移动**：无边框 + 不激活窗没有标题栏，且没实现拖动
   → `SettingsPopup` 增加 mousePress/Move/Release 拖动（按住空白或文字即可拖，
   滑条/按钮仍走自身交互；Qt 按下隐式抓取鼠标，拖出边界不丢）。顺带修复布局：
   `QGridLayout` 单参 `addWidget` 实测是"每件一行"纵向堆叠（探针证实），说明/
   完成按钮还被钉在第 4 行挤在中间 → 全部改为显式行列（label | 控件 | 值）。
2. **背景颜色调整没反应**（文字颜色正常）：两条根因——
   ① 取色器开了 `ShowAlphaChannel`，但 `col.name()` 存 hex 会丢弃 Alpha，
   用户拖 Alpha 滑条（想调透明度）等于空操作 → 取色器去掉 Alpha 通道，
   透明度统一归主面板「背景透明度/文字透明度」两根滑条；
   ② 控制台 `_apply_and_save()` 重建 `overlay` 段时丢掉 `text_color`/`bg_color`
   （外挂 ⚙ 调好的颜色控制台一保存就丢、重启外挂即回退）→ 改为先继承
   `ov_prev` 再覆盖。RGB 修改实时重绘链路经像素采样验证正常（#000000→#ff0000）。
3. **文案对齐控制台叫法**：面板「音频」→「声音来源」（含镜像态"跟随主程序"），
   滑条「背景/文字」→「背景透明度/文字透明度」；⚙ 弹窗说明文字同步。
4. **外挂翻译来源答疑**：外挂启动时读 config.yaml 的 `translate.provider` +
   `providers[...].model`，即首页「翻译后端」的选择（控制台启动外挂前会先
   `_apply_and_save()` 落盘）；外挂运行中改首页模型不会热切换，需停止再启动；
   镜像模式则完全跟随主程序。「启动外挂」按钮 tooltip 已补充说明。

**测试**：`test_overlay_qt.py` 新增 2 项（⚙ 弹窗可拖动+文案、背景色到达绘制）
→ 18 项全过；selfcheck 10/10 + 专项套件全绿。

### 2026-09-11（十六）：用户实测反馈 7 项修复（黑框/缩放/装载状态/外挂长句/设置面板）

用户实际使用后反馈 7 项，全部定位到根因并修复：

1. **控制台窗口无法调整大小**：`launcher.py` 写死 `resizable(False, False)`（Linux 版
   固定布局的遗产）→ 改 `resizable(True, True)`，保留 minsize(760,520)；
   `_fit_window` 的高度跟随页面行为保持不变。
2. **黑色终端反复闪现**：真凶是资源状态条**周期轮询 nvidia-smi**（控制台程序）——
   GUI 从 pythonw（无控制台）运行时，每 spawn 一个控制台子系统程序 Windows 都新建
   控制台窗口。新增共享模块 **`winsub.nowin()`**（`CREATE_NO_WINDOW`，非 Windows 为空
   dict），应用到 nvidia-smi（sysmon）、安装向导全部子进程（pip/ollama list/pull/rm）、
   `installer_gui` 的 `start_new_session`（POSIX 专有参数，Windows 换 CREATE_NO_WINDOW）。
3. **本地模型装载状态不明**：`_local_mem_text()` 改为三态——「服务未运行 / 模型未装载
   （首次翻译自动载入，冷启约 30~40s）/ 装载完毕 · 常驻 N 个 · 共 X GB」，
   并区分"服务没起"与"服务在但模型未驻留"（原版混为一谈）。
4. **外挂长句译文不显示**：内容过长时窗口无限撑高、超出屏幕 → 底部内容被裁。
   修复：高度封顶 55% 屏高 + 溢出时**底对齐绘制**（最新译文贴底永远可见，
   被裁的是顶部旧文）。测试用例确认 594/1080 封顶生效。
5. **面板按钮悬停无文字提示**：面板从不激活（不抢焦点），Qt 默认非激活窗口
   不显示 tooltip → 面板与设置弹窗显式 `WA_AlwaysShowToolTips`。
6. **缺字号/宽度/颜色设置**：新增 **⚙ 设置弹窗**（`SettingsPopup`）：
   字体大小滑条（16-56，实时预览）、字幕宽度滑条（20-100%），
   文字/背景颜色取色器（QColorDialog）；松手/选完才写回 config.yaml
   （`OverlayConfig` 新增 `text_color`/`bg_color` 字段，`persist_geom` 一并持久化）。
7. **「位置」按钮多余**：已从面板删除（关穿透时字幕本就可直接拖动/拖角缩放）；
   `toggle_edit_mode` 方法保留（测试与程序化调用兼容）。

**测试**：`test_overlay_qt.py` 新增 4 项（位置按钮删除+⚙存在、tooltip 属性、
长句封顶、颜色字段+设置弹窗实时生效）→ 16 项全过；全套件自检 10/10 + 专项全绿；
真实启动外挂链路复核正常。

### 2026-09-11（十五）：安装 Ollama + 本地翻译模型落地（GPU 100%）

按用户决策「deepseek 默认 + 本地兜底」补齐本地侧：

- **安装**：`winget install Ollama.Ollama --silent`（0.34.0，装到 `%LOCALAPPDATA%\Programs\Ollama`，
  自动加入用户 PATH 并自启服务——之前那条"幽灵 PATH"条目自此有了真实归属）。
- **模型**：`ollama pull qwen3:4b-instruct`（2.5GB）。
- **实测（项目 LLMTranslator 走 ollama，非裸 API）**：
  - `ensure_local_backend` 正确探测"服务已就绪"；
  - 翻译质量正常（英→中三句全对）；**热态 125~172ms/句**，优于 Linux 版在同一张
    4070 上的基准（0.13~0.33s/句）；冷态 39.6s 为模型加载进显存的一次性开销；
  - `ollama ps`：**100% GPU**（3.2GB，ctx 4096），显存 4.8/8GB——qwen3 + SenseVoice +
    声纹同驻显存仍有余量。
- **配置未改**：默认仍是 `deepseek`（云端）+ `fallback_local: true`——云端失败时
  自动兜底到本地；想切本地为主，控制台「翻译后端」页选 Ollama 即可。
- 注：Ollama 默认 5 分钟空闲后自动卸载模型（下次使用重新加载，即再次冷态）；
  常驻可在 `ollama serve` 环境设 `OLLAMA_KEEP_ALIVE`（项目不干预）。

### 2026-09-11（十四）：修复「启动外挂」报错（deps.py 漏改 GTK 自检）

**现象**：用户点控制台「启动外挂」即弹窗报错"缺少运行组件"，还给出
`sudo apt install python3-gi ...` 的 Linux 命令；`run.log` 里没有任何外挂记录
（说明死在启动前的自检，而非运行中）。

**根因**：`deps.py` 是 Linux 版原样复制，`missing_overlay_deps()` 检查的还是
**GTK3 栈（gi/cairo）+ parec** —— W3 已把 Windows 外挂换成 PySide6/Qt6，
这个自检函数却没跟着改 → 在 Windows 上必然误报"全缺"，用户被 apt 命令劝退。

**修复（`deps.py`，Linux 分支原样保留）**：
- `missing_overlay_deps()`：Windows 检查 `PySide6`（含 `QtCore` DLL 真实可加载性）
  + `missing_system_deps()`（soundcard/PortAudio）；Linux 保持 gi/cairo/parec 原逻辑。
- `missing_system_deps()`：Windows 增加 soundcard（MediaFoundation/WASAPI）检查。
- 新增 `overlay_fix_hint()`：按平台返回修复命令（Windows=pip / Linux=apt），
  `overlay_card.py` 弹窗改用它，不再直接引用 `APT_FIX`。

**验证**：本机自检返回全空（不误报 GTK/parec）；从源码真实启动外挂
→ 窗口创建、keys 注入、SenseVoice/声纹加载、deepseek 后端就绪、
实际识别并翻译一句（ttft 875ms）全链路 OK。测试套件全绿。

### 2026-09-11（十三）：翻译后端对齐 Linux 版（deepseek 默认 + Ollama 本地兜底）

**背景**：分析确认翻译后端体系两版**逐字节相同**（`translate.py`/`providers.py`/`keys.py`/
`config.py`/`config.example.yaml`/`ui/key_ui.py`/`ui/page_backend.py` 全部 SAME），
差异仅在 `config.yaml` 的两处**测试遗留改动**。按用户决策对齐并完成平台适配。

**① Windows 版专属修复（`translate.py` 的 `ensure_local_backend`，Linux 版不动）**

| 位置 | Linux 原实现 | Windows 适配 |
|---|---|---|
| Ollama 未安装提示 | `curl -fsSL https://ollama.com/install.sh \| sh` | `https://ollama.com/download/windows` 下载页 |
| 自动拉起 ollama serve | `start_new_session=True`（POSIX 脱离终端） | `CREATE_NO_WINDOW`（不弹黑框；该 POSIX 参数在 Windows 无效） |

同型修复：`ui/page_backend.py` `_open_installer()` 的 `start_new_session` → `CREATE_NO_WINDOW`；
`packaging/installer_gui.py` 两处 Linux 文案（Ollama 安装指引、`bash install.sh --remove-local` 引用——
Windows 版没有该脚本，删除引用）。

**② 配置对齐（`config.yaml`）**

- 还原测试遗留：`overlay.source: both`、`local.unload_others_on_start: true`、首行注释 —— 与 Linux 版一致。
- 用户决策「默认云端 + 保留本地兜底」：`translate.provider: deepseek`（唯一有意保留的两版差异），
  `fallback_local: true` 不变。

**③ 验证**

- `ensure_local_backend` 在本机（无 Ollama）正确抛错且提示为 Windows 下载页（无 `install.sh` 残留）。
- 兜底链路完整：`provider=deepseek`（api 类型）+ `fallback_local=true` + `ollama`（local 类型）存在
  → `_make_fallback_builder()` 返回构造器，云端失败时自动切本地。
- 测试套件全绿（自检 10/10 + 专项全 PASS）；`translate.py`/`page_backend.py`/`installer_gui.py` 编译通过。
- **注意**：`translate.py` 自此两版不再逐字节相同（+21/-4 为 Windows 平台分支），
  后续同步该文件时需保留此差异。

### 2026-09-11（十二）：开发环境迁移 Anaconda → Miniconda（conda-forge）

**动机**：打包慢（150s）、体积大（2014MB 需手工排除 1.4GB）。根因不是项目问题，而是
**开发环境落在 Anaconda 的 `base`**（507 个包，含 torch/jax/vtk/cv2/Jupyter 等），
PyInstaller 要遍历整棵依赖树。

**执行**：

1. **确认影响面**：`base` = `D:\anaconda` 本身（507 包）；`envs\agent`（Python 3.10，176 包）、
   `envs\bp`（Python 3.14，160 包）建在其下 —— 卸载会一起删除。经确认两个环境均不再需要。
2. **卸载 Anaconda**（21.7 GB）：官方卸载程序**只删了 `envs`，base 原封不动**
   （21.7GB / 570,336 文件仍在）→ 手动 `cmd /c rd /s /q D:\anaconda`。
   另清理：用户 PATH 里 5 条 `D:\anaconda\*`、`~\Documents\WindowsPowerShell\profile.ps1`
   的 conda 初始化块、`.condarc`/`.conda`/`.continuum`/`.anaconda`、开始菜单文件夹、注册表项。
3. **安装 Miniconda** 到 `D:\miniconda`（不勾 PATH，`D:\python` 保持为默认 `python`）。
4. **配置 conda-forge**（避开 Anaconda 官方源的商用许可限制）：
   `conda config --remove-key channels && --add channels conda-forge && --set channel_priority strict`。
   ⚠ 仍报 ToS 错误 —— 因为 `default_channels` 是**内置值**且 `conda config --set` 不接受它；
   解决：创建环境时显式 `--override-channels -c conda-forge`。
5. **建专用环境**：`conda create -n livetrans python=3.12 -y --override-channels -c conda-forge`
   （**仅 20 个包**，对比 Anaconda base 的 507）；再 `pip install -r requirements.txt pyinstaller`。

**效果（实测，均从零干净构建）**：

| 指标 | Anaconda base | conda-forge 专用环境 |
|---|---|---|
| 打包耗时 | 149.9 s | **54.6 s**（快 2.7 倍） |
| 产物体积 | 621 MB | **619 MB** |
| **文件数** | 3349 | **1375**（少 59%） |
| 手工 EXCLUDES | 必需（否则 2014MB） | 仅少量通用项 |

> 文件数大降揭示了一个此前被掩盖的问题：`--clean` **并没有真正清空 `dist/LiveTrans`**
> （PyInstaller 的 COLLECT 是增量复制），导致历次打包的 `skimage`/`h5py`/`astropy` 等
> **旧残留层层堆积**。这些包其实**根本不在新环境里**。排查时一度误以为 EXCLUDES 失效。

**④ 打包新环境暴露的两个 conda 特有问题（已修）**

conda 把非 Python 运行库放在 `<env>\Library\bin\`，PyInstaller **不会去那里找**，
于是打包后运行时报 "DLL load failed"（DLL 明明在环境里，只是没被带上）：

| 报错 | 缺失的 DLL | 位置 |
|---|---|---|
| `DLL load failed while importing _ctypes` | `ffi-8.dll`（注意不是 `libffi-8.dll`） | `Library\bin` |
| `DLL load failed while importing _tkinter` | `tk86t.dll`、`tcl86t.dll`、`zlib1.dll` | `Library\bin` |

→ 新增 `conda_runtime_dlls()`：从 `Library\bin` 与 `DLLs` 里有选择地收集关键运行库
（libffi / tk / tcl / zlib / ssl / sqlite / expat / openblas），用 `--add-binary` 带上。
**只挑明确需要的**，避免把整个 `Library\bin`（常几百 MB）塞进包。

**⑤ 最终验证（打包后的 exe，非源码）**

- `--help` / `--list-devices` / `--overlay --help` 三条路径正常。
- **真实端到端**：exe 启动外挂 + 系统声音 → 4 条识别 + 4 条翻译成功；
  SenseVoice 3.5s、声纹 dim=192/453ms、keys 自动注入 2 个、deepseek 后端就绪。

### 2026-09-11（十一）：Windows 版 W4+W5 —— 声纹落地、PyInstaller 打包与发版

**成果：声纹在 Windows 实测可用；便携版打包成功并冒烟通过，体积从 2014MB 降到 621MB。**

**① 声纹（W4）**

- 装 `sherpa-onnx` 1.13.8 + 复用已有模型 `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`（27MB）。
- 实测：模型载入 **422ms**、`dim=192`、向量提取与聚类正常；过短段/None/未启用均安全返回空标签；
  `build_tracker(未启用)` 返回 None（零开销）。**依赖链在 Windows 上完全就绪**，
  真实区分效果需真人语音验证（合成音会被判为同一人，属预期）。
- **顺手修掉一个两平台共有的 bug**：`speaker.py` 使用了 `BASE` 但只 import 了
  `MODELS_DIR, models_search_dirs` —— 当配置里写**相对路径**的 `speaker.model` 时
  会 `NameError`。Linux 版同样存在，**两平台一并修复**。

**② 打包（W5）：`build_windows.py` 从"能跑"到"真能出包"**

遇到并解决 5 个真实问题（全部记入脚本注释）：

1. **`webrtcvad-wheels` 与 PyInstaller 自带 hook 不兼容**
   → 自带 `hook-webrtcvad.py` 假设 webrtcvad 是包，而 wheels 版是
   `webrtcvad.py` 单文件 + `_webrtcvad.cp312-win_amd64.pyd`，导致
   `ImportErrorWhenRunningHook`、打包直接失败。
   → 新增 `packaging/hooks/hook-webrtcvad.py` 并用 `--additional-hooks-dir` 优先加载。
2. **Anaconda 环境把整套 Jupyter/科学计算栈牵连进来**，`jupyter labextensions` 的
   超长路径（`vendors-node_modules_...`）让 COLLECT 阶段 `FileNotFoundError`。
   → EXCLUDES 大幅扩充（Jupyter、conda、科学计算、GUI 绑定等）。
3. **包体积 2014MB**：实测 `torch` 290MB + `jaxlib` 205MB + `vtk.libs` 166MB +
   `cv2` 98MB + `panel` 98MB 等全是无关依赖（项目只用 onnxruntime 推理）。
   → 精准排除后 **2014MB → 621MB**（含 258MB 模型），**省 1.4GB**。
4. **`setuptools` 不能排除**：PyInstaller 运行时钩子 `pyi_rth_pkgres` 依赖
   `pkg_resources`（进而 `jaraco.text`），排掉后 exe 启动即
   `ModuleNotFoundError: No module named 'jaraco.text'`。
5. **相对导入在打包后失效**：`livetrans/main.py` 用 `from .asr import ...`，
   被 PyInstaller 当顶层脚本执行时 `ImportError: attempted relative import
   with no known parent package`。→ 新增顶层入口 **`run_livetrans.py`**（绝对导入
   `livetrans.main`，并转发 `--overlay`），打包入口指向它。
6. **`keys.env` 未被注入**：用户直接跑 exe（不经控制台）时没人调 `merged_env()`，
   翻译后端报"需要环境变量 DEEPSEEK_API_KEY"。
   → 在 `main.py` 与 `overlay.py` 的启动处**兜底注入** `key_env_overlay()`
   （已存在的环境变量优先，不覆盖用户显式设置）。

**③ 打包产物实测**

| 项 | 结果 |
|---|---|
| 体积 | 621 MB（含 258MB 模型；排除无关依赖前为 2014MB） |
| 启动 | `LiveTrans.exe` 可执行，`--overlay` 分派正常 |
| 设备枚举 | ✅ 列出 38 个设备 + 4 个 WASAPI loopback 设备 |
| SenseVoice | ✅ frozen 下 3.3s 加载成功 |
| 声纹 | ✅ `dim=192`、422ms 载入 |
| API key | ✅ 自动从 keys.env 注入 2 个 |
| 翻译后端 | ✅ deepseek/deepseek-flash 就绪 |

**④ 新增测试**

- `tests/test_speaker_win.py`（7 项）：BASE 导入（守住上述 NameError 回归）、
  未启用零开销、None/空/过短音频安全、降级路径、配色映射、模型定位与 ready 一致性、
  **真实向量提取**。
- 注：用例内**不能输出 `[SKIP]` 字样** —— `tests/run.py` 见到 `[SKIP]` 会把整个
  用例判为 SKIP，会掩盖同文件其它 PASS（已写入该用例注释）。

**⑤ Windows 版测试总况**：自检 10/10 通过；专项测试 12 个文件全绿
（新增 `test_capture_win` 11 项、`test_overlay_qt` 12 项、`test_speaker_win` 7 项）。

### 2026-09-11（十）：Windows 版 W3 —— PySide6 重写字幕外挂 + 主程序端到端实测

**成果：外挂字幕用 Qt6 重建完成，功能与视觉对齐 Linux 版；主程序链路实测全通。**

**① 主程序端到端实测（内部 + 外部音频）**

用 DeepSeek 云端后端实跑完整链路（采集 → VAD → SenseVoice → LLM 翻译 → 上屏）：

- **外部音频（麦克风）**：`麦克风 (H USB Audio)` → 识别 `y`/`yeah` → 译文 `是的`（672ms）。
- **内部音频（WASAPI loopback）**：`系统音频(USB)` → 5 段全部识别并翻译，
  例如「这把损耗六十九万多舍四百一十六万五套两把满改 m 七兄弟们」→
  「这把损耗六十九万多，舍掉四百一十六万，五套两把满改M7，兄弟们。」（识别 RMS 0.0857）。
- 注意：`keys.env` 里的 key 需经 `merged_env()` 注入环境变量（控制台走 launcher 时会做；
  直接跑脚本要自己注入，否则 `LLMTranslator` 会报"需要环境变量 XXX"）。

**② 字幕外挂：GTK3 → PySide6/Qt6（功能逐项对齐）**

`overlay.py` 整体重写（Linux 版 1111 行 GTK3 保留不动，Windows 版为 Qt 实现）。对照表：

| 能力 | Linux (GTK3) | Windows (Qt6) |
|---|---|---|
| 逐像素透明 | `set_visual(RGBA)` + cairo | `WA_TranslucentBackground` + `QPainter` |
| 鼠标穿透 | `input_shape_combine_region` | Win32 `WS_EX_TRANSPARENT`（+`WS_EX_LAYERED`） |
| 置顶 / 不抢焦点 | `set_keep_above` / `set_accept_focus(False)` | `SetWindowPos(HWND_TOPMOST)` / `WS_EX_NOACTIVATE` |
| 圆角与文字 | cairo `arc` + Pango | `QPainterPath.addRoundedRect` + `QFontMetrics` |
| 定时器 | `GLib.timeout_add` | `QTimer` |
| 全局指针（悬停检测） | `Gdk.Seat.get_pointer()` | `QCursor.pos()` |
| 面板样式 | GTK CSS（`#1c2230`/`#39c5b8`/`#dce3ee`） | Qt StyleSheet（同配色） |

覆盖 Linux 版全部能力：无边框/置顶/穿透/不抢焦点、底部居中按宽高比、
**背景与文字透明度完全独立**、悬停式控制面板（隐藏/暂停/‹›/最新/位置/穿透/固定/退出
+ 双滑条 + 音频来源下拉）、几何写回 config.yaml、声纹 `[Sx]` 标注、镜像模式、
运行时切换音频来源。

**③ 踩到的坑**

1. **PySide6 6.11.x 在本机 Anaconda 环境 `import QtCore` 报 DLL 加载失败**
   （6.11.2 先后报"找不到指定的程序"/"找不到指定的模块"）。装 6.8.3 并清掉 6.11 残留后正常。
   → `requirements.txt` 钉 `PySide6==6.8.3` 并注明原因。
2. **关穿透时不能把 `WS_EX_LAYERED` 一起清掉** —— LAYERED 是透明背景的前提，
   清掉后字幕会变成一块不透明黑矩形（不报错，视觉上完全坏掉）。已加专项测试守住。
3. **打包后 `python -m livetrans.overlay` 失效** → 新增 `paths.app_command()`：
   frozen 时返回 `[LiveTrans.exe, --overlay]`，源码时返回 `[python, -m, livetrans.overlay]`；
   并在 `main.py` 的 argparse **之前**摘出 `--overlay` 分派（两个 parser 参数不打架）。
4. **`paths.BASE` 在打包后指向临时解包目录** → 改为 frozen 时取 `sys.executable` 所在目录
   （模型/配置就放在 exe 旁边，符合 one-dir 布局）。
5. **控制台的外挂进程管理用了 `pgrep`/`pkill`**（Windows 没有）→ 改用 `psutil` 遍历/终止，
   匹配词同时认 `-m livetrans.overlay` 与 `LiveTrans.exe --overlay`。

**④ 实测验证**

- 外挂窗创建：1365x88 → 内容变长自动撑到 1365x121 / 416（不裁切）。
- **截图确认**：圆角半透明底、原文小字灰色 + 译文大字白色、`[S1]` 声纹前缀、
  右下角缩放标、悬停控制面板与 Linux 版配色一致。
- **穿透开关实测**（读回 Win32 扩展样式）：`WS_EX_TRANSPARENT` 正确增删，
  `WS_EX_LAYERED` 始终保留，`NOACTIVATE`/`TOOLWINDOW` 全程在位。
- **真实外挂端到端**：启动外挂 + 系统声音 → 3 条字幕上屏并翻译成功
  （如「螂不随便进应下有老人啊」→「蟑螂不会随便进有老人的家」）。

**⑤ 新增测试 `tests/test_overlay_qt.py`（12 项，全过）**：管线接口齐全（18 个）、
穿透开关（含 LAYERED 保留）、不抢焦点/不进任务栏、窗口标志（无边框/透明/置顶）、
几何随内容增长、历史跟随翻页、声纹标注只在换人时出现、译文回填、
背景/文字透明度独立与边界、位置模式自动关穿透、隐藏显示、几何持久化不破坏其它字段。

### 2026-09-11（九）：Windows 版 W1+W2 —— 麦克风链路与 WASAPI loopback 系统声音

**成果：Windows 版音频链路全部打通并实机验证。**

- **W1 麦克风链路**：`AudioCapture` 本就是跨平台的（sounddevice/PortAudio 同一套 API），
  实测可用（本机默认输入设备 `麦克风 (H USB Audio)`，16 个输入设备可枚举）。仅更新了报错文案。
- **W2 系统声音（WASAPI loopback）**：`capture.py` 改造完成，**对外契约完全不变**——
  `ParecCapture`（类名沿用）、`list_monitor_sources()`、`default_monitor_source()`、
  `friendly_source_name()` 全部保留原签名，因此 `main.py` / `overlay.py` / 6 个 UI 页面
  无需任何改动（`list_monitor_sources()` 被 8 处调用，返回 `[(key, label)]` 格式不变）。
- **踩到并修复的真坑（记入代码注释与测试）**：
  1. **COM 未初始化**：`soundcard` 底层走 MediaFoundation，在**子线程**里调
     `all_microphones()` 会抛 `RuntimeError: Error 0x800401f0`（`CO_E_NOTINITIALIZED`）。
     主线程能用、放进 Thread 就炸，非常隐蔽。→ 新增 `_com_init()/_com_uninit()`，
     在采集线程入口与所有涉及 soundcard 枚举的函数里兜底调用。
  2. **`_Microphone` 没有 `samplerate` 属性**（只有 `channels`）→ 取不到时回退 48k。
  3. **HDMI 音频设备名不含 "hdmi"**：NVIDIA 的音频设备叫
     "HC-L220A (NVIDIA High Definition Audio)"，只看 "hdmi" 会漏判；
     蓝牙免提设备（驱动名 `bthhfenum`）会被误判成 USB。
     → `friendly_source_name()` 关键词表改为**按优先级从具体到宽泛**排列
     （蓝牙 → HDMI/显卡音频 → 数字 → USB → 模拟/板载），并补充 `nvidia`、
     `high definition audio`、`bthhfenum`、`hands-free`、`senary` 等实测关键词。
- **实机验证数据**（RTX 4070 笔记本）：
  - 枚举到 **4 个 loopback 设备**（NVIDIA HDMI / Steam Streaming / Senary 板载 / USB 音频）；
    `default_monitor_source()` 自动选中 `扬声器 (H USB Audio)`（与系统默认扬声器一致）。
  - loopback 采集 12s：**块长恒定 640 采样（40ms@16k）零异常，时长比 1.003**（无丢帧漂移），
    动态范围 7400 万倍（有清晰声音起伏），静音块占 22%（VAD 有足够静音间隙）。
  - **全链路验证**：合成人声 → VAD 正确切段 → SenseVoice 识别出结果（0.08s/段）。
  - `soundcard` 会打印 `SoundcardRuntimeWarning: data discontinuity` —— 经实测判定为
    **库的误报**（内部缓冲重组时触发），实际块长恒定、时间轴无漂移，不影响功能。
- **新增专项测试 `tests/test_capture_win.py`（11 项，全过）**：COM 子线程初始化、
  8 种设备名转换、关键词归类（用例内的设备名**全部取自本机实测**）、
  48k↔16k 重采样精度与边界、`list_monitor_sources()` 接口契约、
  `default_monitor_source()` 返回值合法性、Windows 上 `find_monitor_device()` 返 None、
  `ParecCapture` 可构造、`AudioCapture` 对无效设备正确报错。
- **测试总况**：Windows 版自检 10/10 通过，专项测试 11 项新增全过、其余全绿。

### 2026-09-11（八）：仓库分叉为双平台（Linux 冻结基线 + Windows 独立实现）

用户决定：**没有主次之分，两个平台都要开发好，push 到 GitHub 时按两个版本共同发行。**

- **目录重构**：`prototype/` → **`git mv` 到 `platforms/linux/`**（保留文件历史，GitHub 上显示为重命名而非删除+新增）；
  复制一份为 **`platforms/windows/`**，作为 Windows 版独立演进的起点。
- **Linux 版冻结**：`platforms/linux/` 作为稳定基线，功能与行为不再主动变更；`platforms/linux/tests/run.sh` 等原样保留。
- **Windows 版改造（W0 骨架）**：
  - 新增跨平台测试入口 **`tests/run.py`**（等价替换 `run.sh`：同样「退出码 0 且无 Traceback」判定、`[SKIP]` 不计失败、240s 超时），
    支持 `--list` 与按名过滤；删除 Windows 侧的 `run.sh`。
  - 新增 **`packaging/build_windows.py`**（对应 Linux 的 `build_deb.sh`）：PyInstaller one-dir → `dist/LiveTrans/`，
    自动收集 `funasr_onnx`/`sounddevice`/`soundcard` 等隐式依赖，`models/` 存在则随包拷入；支持 `--check` / `--clean`。
  - 删除 Linux 专用脚本（`build_deb.sh` / `install.sh` / `install-python-deps.sh` / `livetrans.desktop` / `*.sh`）。
  - `requirements.txt` 加入 Windows 专属依赖：`soundcard`（WASAPI loopback）、`psutil`（替代读 `/proc`）、
    `platformdirs`（替代 XDG）、可选 `PySide6`；移除全部 apt 说明。
  - 平台差异用例改为**非目标平台优雅跳过**：`test_overlay_drag.py`（GTK）、`test_installer.py`（bash）、
    `test_installer_gui.py`（Linux 向导）在 Windows 上打 `[SKIP]` 退出 0；`test_e2e_dialog.py` 的 `/tmp/` 改为 `tempfile`。
- **CI 双 job**：`test.yml` 拆为 `linux`（ubuntu-22.04 + xvfb + `run.sh`）与 `windows`（windows-latest + Python 3.12 + `run.py`）；
  `release.yml` 拆为 `build-linux`（两个 deb）/ `build-windows`（便携 zip）/ `release`（汇总到**同一个** Release + `SHA256SUMS`）。
- **`.gitignore` 重写**：路径从 `prototype/*` 改为 `platforms/*/*`（密钥/会话/模型/配置/产物继续全排除），
  并补充 Windows 产物类型（`*.exe` / `*.msi` / `*.zip` / `Thumbs.db`）。
- **文档**：`README.md` 与本文档新增「平台布局与协作约定」，安装/快速开始/开发/发版章节均分平台呈现。
- **Windows 侧待改造清单**（W1 起逐项处理，扫描实测）：
  `capture.py`(parec×20 / pactl×8)、`overlay.py`(GTK/cairo)、`sysmon.py`(`/proc`×2)、
  `paths.py`(XDG×2 / `/usr/share`×4 / chmod×2)、`deps.py`(apt×3)、`main.py`(SIGTERM×1)、`translate.py`(start_new_session×2)。

### 2026-09-11（七）：完全离线 .deb + 图形化安装向导
- **模型搜索路径**（离线包的关键）：新增 `paths.models_search_dirs()` —— 读取模型时按
  `用户目录/源码 models → BASE/models → /usr/share/livetrans/models` 顺序查找；
  `asr.sensevoice_model_dir()` 与 `speaker.speaker_model_path()` 改为按此查找，
  **写入/下载目标仍是可写的 `MODELS_DIR`**（用户自己下的模型优先于随包版本）。可用 `LIVETRANS_SYSTEM_MODELS` 覆盖系统目录。
- **`build_deb.sh --offline`（= `WITH_MODELS=1`）**：模型不再放 `/opt/livetrans/models`
  （那是只读代码目录，运行时找不到 → 这是原先 `WITH_MODELS=1` 的隐性 bug），改放
  `/usr/share/livetrans/models`；缺模型时明确报错退出。实测产包 **236MB**，解开后 `/usr/share/livetrans/models` 里是
  sensevoice + speaker ✓、`/opt` 下无模型 ✓。
- **端到端验证**：把 .deb 解开当"已安装"，用全新数据目录运行 → 识别模型/声纹模型都从系统目录命中、
  **零下载**；真加载 231MB ONNX 并转写成功（离线识别链路可用）；包内向导 `--plan` 可运行
  （`_bootstrap_path()` 兼容源码目录 / `/opt/livetrans` / 被拷到 doc 目录三种位置，找不到时给清晰指引）。
- **图形化安装向导 `packaging/installer_gui.py`**：三步式（选项 → 进度 → 完成），复用控制台主题；
  五项默认全勾选（pip 依赖 / 识别模型 240MB / 声纹 28MB / Ollama 本地翻译 2.5GB / 应用菜单项），
  每项标注"已就绪会跳过"或预计体积；完成页可「启动控制台」与「删除本地模型」（二次确认）。
  支持 `--plan`（只打印计划，CI 用）、`--dry-run`（走流程不改系统）、`--only a,b`。
  控制台「本地模型」卡片新增「安装向导…」入口；.deb 里注册为 `livetrans-installer` + 应用菜单项。
- **踩坑修复**：安装线程里**不能**调 `self.after()`（Tkinter 抛 "main thread is not in main loop"，
  线程被打死 → 进度页永远卡在第一步）；改为线程只往 `queue.Queue` 投递消息、主线程 `_pump` 消费。
- 顺带主题化：深色 **滚动条** 与 **进度条**（原先 ttk 默认浅灰，在深色面板上很扎眼）。
- 新增回归测试 `tests/test_installer_gui.py`：`--plan`、默认全勾选与体积、**安装线程不得碰 Tk**（源码级检查）、
  删除需二次确认、dry-run 零副作用且 5 步全过、主题化检查。

### 2026-09-11（六）：安装体验（一键安装 + 本地模型"默认装、可删"）+ 去掉应用菜单按钮
- **删掉控制台「添加到应用菜单」按钮**（用户要求）：按钮、`_install_desktop()`、`paths.DESKTOP_TEMPLATE`（随之成为死代码）以及 6 个页面里顺带的无用导入一并清理；命令行途径 `install-desktop.sh` + `livetrans.desktop` 保留。
- **新增 `packaging/install.sh` 一键安装**（回答"用户怎么装、要不要自己配本地模型/Ollama"）：
  - 默认四步：系统依赖检查 → pip 依赖 → **本地模型（识别 240MB + 声纹 28MB）** → **本地翻译模型（Ollama 拉起 + pull）**；
  - 「本地模型」作为**默认同意**的选项提供：`--no-models`（只走云端 API）/`--no-asr`/`--no-speaker`/`--no-ollama` 可分别关闭，`--ask` 逐项确认（默认都是「是」）；
  - `--with-apt` / `--install-ollama` 可在缺件时自动安装（需 sudo）；`--dry-run` 只打印计划；
  - **`--remove-local` 事后删除**：删识别/声纹模型目录 + `ollama rm` 本地模型，并说明"下次要用会自动重下、云端 API 不受影响"。
  - 模型目录与本地模型名都从程序自身代码取（`paths.MODELS_DIR`、配置里 base_url 是本机的服务商模型），不写死。
- **控制台可发现删除入口**：「翻译后端 → 本地模型（Ollama）」卡片新增一行 `本地模型占用：270 MB（识别 + 声纹）· 删除：bash packaging/install.sh --remove-local`（下载完成后自动刷新）。
- **README 安装章节重写**：开头直接回答"本地模型/Ollama 不是必需"；三种方式（一键脚本 / .deb / 手动）；配一张开关表；单列「想删掉本地模型」小节。`build_deb.sh` 把 `install.sh` 一并打进 `/usr/share/doc/livetrans/`，`install-python-deps.sh` 的提示也改指向它。
- 新增回归测试 `tests/test_installer.py`：`bash -n` 语法、`--help`、未知参数报错、**dry-run 零副作用**、`--no-models` 跳过全部本地模型、**`--remove-local --dry-run` 绝不真删**、控制台提示存在。

### 2026-09-11（五）：界面统一与美观（按钮两档制 / 输入域同高 / 窗口高度跟随页）
用户反馈"「声纹角色标注」处选择的按钮过小、请统一按钮风格与大小、优化美观"。做了一套**控件尺寸规范**并落到代码里（不再各页各写各的）：
- **按钮两档制**（`theme.BTN_PAD / BTN_PAD_WIDE / BTN_PAD_CHIP` 令牌 + `widgets.button()` 工厂，`kind=primary/secondary/chip/ghost`）：
  - 标准/主按钮 **38px 同高**（主按钮只加宽不加高——底部「保存并启动 / 仅保存设置 / 运行日志」三个终于齐平）；
  - 卡片内小按钮（显示/清除/原文/译文）**28px** 一档，字号与内边距统一。
  - 全程序按钮都经由工厂创建，样式只有 theme 一处可改。
- **输入域统一 36px**：Combobox/Entry 本来就是 36，**Spinbox 是 44**（高度由箭头尺寸决定，arrowsize 20 → 16 才落回 36）—— 这就是各页"同一行控件高低不齐"的根因。
- **声纹卡片重做**：原「最多人数」是 `width=4` 的 Spinbox（就是用户说的"过小"）→ 换成与「设备/来源」同款下拉（2~8 人）；原「判定阈值」是经典 `tk.Scale`（裸 Motif 观感、数值浮在上面）→ 换成主题 `ttk.Scale` + 等宽数值标签。顺手修一个真 bug：`ttk.Scale.set()` 会**立刻**触发回调，而状态标签当时还没创建 → `_spk_hint` 容错处理。
- **窗口高度跟随当前页**：原来窗口高度被最高页撑成 1265px（小屏直接顶出屏幕、矮页一大片空白）→ 监听 `<<NotebookTabChanged>>` 重算高度（受屏幕高度上限约束）。顺带修**底部操作栏被记事本挤没**（pack 顺序：`side="bottom"` 必须先 pack）。
- **高页可滚动**：翻译后端 1250px 内容放进 `_scroll_page()`（Canvas + 滚动条按需出现、滚轮仅在指针位于该页时接管），小屏也能看全。
- 其它：状态气泡抬到操作栏之上（原来压住「运行日志」）、头部加分隔线、字幕窗条目内小按钮字号 7→8 与主题令牌对齐、后台线程回 UI 统一走 `_ui_call()`（关窗不再刷 traceback）。
- 新增回归测试 `tests/test_ui_theme.py`：按钮两档同高、输入域同高 36、声纹控件尺寸、操作栏可见且齐平、窗口高度跟随页。

### 2026-09-11（四）：布局收敛为三种（删双栏；外部/内部改单一界面）
- **删掉"双栏"**：双栏与对话都是"两条字幕流并排"，功能撞车——按用户要求删除。`LAYOUTS` 收敛为 `dialog / external / internal`，默认对话；**老配置里的 `layout: dual` 自动归一为对话**（`norm_layout()`，启动时归一并通过 `on_style_change` 落盘）。
- **外部/内部改成单一界面**：不再按"你/对方"分栏，而是**只显示这一路音频**（外部=麦克风，内部=系统声音），全宽一栏、标题就是来源：
  - 各用一路时：`麦克风 · 自动 → English` ｜ `系统声音 · 自动 → 中文`；
  - 同一路被两个角色共用时（线上会议按声纹分说话人）：单一界面只能取一个方向，标题标明 `系统声音（按「你（自己）」） · 自动 → English`（取左栏角色的设置），"对话设置"面板的同源提示里也写明了这条规则；
  - 没有角色用这一路：仍给明确提示（"在「对话设置」里把某个角色的音频来源改成它即可"）。
- **实现**：`panes_for_kind()` 改为**布局相关**——对话返回所有用这一路的角色栏（同源可两栏），单一界面只返回该来源的"主角色"栏；`_poll` 原有的 `p.role == item.role` 过滤顺带保证了同源另一角色的字幕流**不会在单界面里重复上屏**（实测 self 2 条 / other 0 条）。
- 回归测试 `tests/test_layout.py` 重写：dual 归一、对话两栏聊天式等宽、外部/内部单界面（标题=来源、同源时标明按谁）、单界面不重复上屏、没人用某路给提示。

### 2026-09-11（三）：双栏退化与栏内渲染（长句被裁 / 栏头溢出）
用户反馈"双栏只有一个界面（写着系统声音）"，顺藤摸瓜又挖出两个渲染 bug（截图逐张目视核对过）。
- **双栏只剩一栏**：上一轮我加的"两个角色同源时只显示一栏"是错的——用户配置恰好是两个角色都用系统声音（线上会议在一个音频里按声纹分说话人，这是合法场景），于是双栏退化成一栏。改为**永远两栏**：标题用「来源 · 角色」（`系统声音 · 你（自己）`｜`系统声音 · 对方`），同源也能区分；各用一路时即 `麦克风 · 你`｜`系统声音 · 对方`。「对话设置」面板新增**同源提示**，直接告诉用户"两栏为什么都写系统声音、想看到麦克风｜系统声音该怎么改"。
- **长句被裁掉半句**：`tk.Text` 默认请求宽约 80 字符，而 pack **不会**把控件压到请求宽度以下 → 译文按 ~600px 排版、被 534px 的窄栏裁掉。给 Text 设 `width=1`，宽度交给 `pack(fill="x")`，换行才按栏宽算。
- **换行后第二行永远不显示**：Text 只排版"看得见"的内容，`height=1` 时 `count -displaylines` 恒为 1、`dlineinfo(末字)` 为 None，于是"Configure → 量到 1 行 → 设回 1 行"自我循环（试过"临时撑高再量"，会与 `<Configure>` 互相触发直接死循环）。最终方案：用**离屏测量 Label**（同字体、同 wraplength）算行数再回填；坑中坑——Label 复用时**必须每次重设字体**（原文 10pt / 译文 14pt，否则量出的行高是错的）。
- **栏头溢出**：标题+暂停键+电平条用 pack 排在 `fill="x"` 的一行里，窄栏撑破栏宽（右侧电平条被挤出窗口）。改 `grid`（标题 `weight=1` 自动裁切、电平条 70px、暂停键贴右），电平条按实际像素画。
- 新增回归测试 `tests/test_pane_render.py`：长句换行完整、缩小窗口要重排（行数 3→5）、栏头元素不越界、两栏等宽且都在。四张布局截图存 `/tmp/shots/`（渲染脚本 `/tmp/shot_modes.py` + `/tmp/grab.py`，Gdk 独立进程截屏）。

### 2026-09-11（二）：用户反馈四处修复（布局四态 / 移除朗读 / 跨屏拖动 / 两栏等宽）
- **① 顶栏四种布局长得一样**：根因有两条——(a) 重构成"角色制"后 `dual` 与 `dialog` 只差一个右对齐，几乎看不出区别；(b) 用户配置是两个角色都用系统声音，于是"外部"聚焦模式找不到用麦克风的栏，走了"退化成两栏"的兜底，与双栏一模一样。重写 `_set_layout`：
  - **双栏** = 按**音频来源**分栏（标题直接叫「麦克风 / 系统声音」），同源时只显示一栏；
  - **对话** = 按**角色**分栏，聊天窗式（自己靠右、对方靠左）+ 说话侧高亮；
  - **外部/内部** = 只显示用这一路的角色栏；没人用则显示占位提示「当前没有角色使用「麦克风」，在「对话设置」里改」（不再悄悄显示两栏）。
- **④ 对话模式两栏宽度不一**：pack 的 `expand` 只在"请求宽度相同"时才均分，内容长短不同就不等宽 → 改用 `grid` + `grid_columnconfigure(weight=1, uniform="pane")`，实测两栏 534/534 像素一致。
- **③ 外挂拖不到副屏**：`_place()` 用手动位置时只按**当前这一块屏**钳制（`self.mon`），拖出边界就被弹回。新增 `_desktop_bounds()`（所有显示器并集）作为拖动边界；缩放的宽度上限改为"窗口当前所在那块屏"。实测本机双屏并集 4480x1600，拖到副屏的位置 (2120,820) 能保留、越界仍钳回。
- **② 移除朗读（TTS）**：实测效果不佳，按用户要求整块删除——`livetrans/tts.py`、字幕窗每条译文的「朗读」按钮与样式面板「语音朗读」段、自动朗读、外挂面板「朗读」按钮、`style.tts_*` 配置键、对应测试与文档。全库 `tts/朗读/speak_current` 零残留。
- 新增回归测试：`tests/test_layout.py`（四态形态各异 + 两栏等宽 + 落盘）、`tests/test_overlay_drag.py`（跨屏保留 + 越界钳回）。

### 2026-09-11：收尾工程（清理 → 镜像模式 → TTS → 日志/多屏/打包 → 弱网降级 → 文档）
- **清理与解耦**：删字幕窗失效的下载进度条机制（`set_progress`/进度条 Canvas/`_pane` 孤儿方法，-48 行）；声纹缺失提示改走 `deps.missing_speaker_deps()` 单一出口；清掉用户 config 里透明度功能遗留的 `opacity/pure_*` 键；`config.example.yaml` 补 `subtitle.animate`/`tts_*`。
- **路径解耦（为打包铺路）**：`paths.py` 拆成**代码目录 BASE** 与**数据目录 DATA_DIR**（源码运行仍是 BASE；只读安装走 `LIVETRANS_DATA_DIR`/`$XDG_DATA_HOME/livetrans`），首次运行从 `config.example.yaml` 播种 config.yaml + glossary.yaml；`keys.py`/`asr.py`(MODELS_DIR)/`speaker.py`/`translate.py`(术语表)/`main.py`(4 处硬编码配置路径) 全部改走该出口；模型目录只读时落数据目录。
- **外挂镜像模式**（`overlay.mirror`）：不加载 ASR/翻译，`SessionMirror` 跟随主程序的 `session-*.jsonl`（不重放历史、坏行跳过、暂停不上屏、换会话自动跟随、面板「音频」置灰显示"跟随主程序"）。
- **TTS 语音播报**：新增 `livetrans/tts.py`（spd-say 优先 → espeak-ng，`speak/stop`，非阻塞、只念最新一条）；字幕窗每条译文加「朗读」按钮（按该栏语言/语速，样式面板可关并即时收起已显示的按钮）+ 自动朗读开关（仅最终译文触发）；外挂面板加「朗读」。
- **日志轮转**：启动前 `run.log → run.log.1 → …`，保留 5 份。
- **副屏定位**：外挂 `_pick_monitor()` 支持 自动（鼠标所在屏）/ 主屏 / 屏幕 1..3，控制台加「屏幕」下拉，日志打印实际使用哪块屏。
- **deb 打包**：新增 `packaging/{build_deb.sh,livetrans.desktop,install-python-deps.sh}`；代码装 `/opt/livetrans`（`/usr/bin/livetrans` 启动器把数据目录指到 `~/.local/share/livetrans`），数据/模型/密钥不进包；实测构建 + 解包 + **从解包目录直接跑起来**（自动播种配置、SenseVoice 就绪）。
- **弱网降级**：`translate.fallback_local`（默认开）——云端连续失败 2 句 → 自动切本地兜底翻译器（按方向按需建、缓存）并**立即补翻当前句**，每 60s 探云端，恢复自动回切；主程序与外挂都已接入；失败提示按本地/云端给出不同排查建议。
- **文档同步**：README 补声纹/对话模式/导出/TTS/镜像/副屏/降级/显存管理，自检数字 6→10，模块地图补 speaker/export/tts/packaging；路线图现状表补 6 行、待办表重写（原 5 项已全部落地）。
- 验证：见下一节「最终测试」，本轮所有新功能均有真机或专项实测。


### 2026-09-09（M1 启动）
- 完成需求分析与技术调研：翻译方式全景（LLM API / 传统 MT / 语音专用 / 本地部署）、各平台音频捕获方案。
- 调研四个本地技能市场（anthropics / cb_teams / codebuddy-official / vercel-labs），安装 8 个开发类技能。
- 确定技术路线：**M1 用 Python 在 Linux 快速验证核心链路**；引擎与 UI 队列解耦，为 M5 Rust+Tauri 三端迁移做准备。
- 完成 M1 代码骨架：capture / asr / translate / subtitle / summarize / main 六模块 + selfcheck.py 自检脚本。
- 依赖安装：全部 Python 依赖已装到用户目录（faster-whisper 等就绪）；pyright 1.1.411 就绪。
- 环境问题记录：系统 flatbuffers 包版本号非法（pip 需 `--ignore-installed`）；缺 python3.10-venv；**缺 libportaudio2（运行前需 sudo apt 安装）**。
- 自检结果（selfcheck.py）：4 项全部通过——9 个翻译后端配置加载、术语表+滑动上下文窗口、VAD 状态机流式切段、字幕模块导入。

### 2026-09-09（二）：GUI 控制台 + 内部音频捕获加固）
- 用户安装好系统依赖（libportaudio2、pulseaudio-utils），PortAudio 就绪（18 设备）。
- **关键发现**：PortAudio 枚举不到 PulseAudio monitor 源（内部音频）→ 新增 `ParecCapture`（parec 子进程按源名直接捕获，`pactl list sources short` 检出 3 个 monitor 源：模拟输出/HDMI/USB，自动优先模拟输出）；PortAudio monitor 作为后备。`--list-devices` 与配置项 `audio.monitor_source` 支持。
- 新增 `launcher.py` 图形控制台（Tkinter，冒烟测试通过）：麦克风/系统音频开关与设备下拉、翻译后端选择 + API key 环境变量检测、目标语言、普通/专业模式、上下文句数、Whisper 模型档位、保存到 config.yaml、一键启动/停止主程序、会话选择+一键总结并打开结果。
- 编写 `~/CODEBUDDY_SKILLS.md` 技能清单文档（含调用机制实测结论）。
- 实测技能调用机制：本会话可调用内置技能（agent-browser 加载成功，且加载的是新安装副本路径）；**新装技能需新会话注入生效**（development-essentials 当前会话不可见，属预期）。

### 2026-09-09（三）：控制台 GUI 重构 —— Key 管理 / 模型联动 / 桌面集成（frontend-design 技能实测）
- **API key GUI 化**：新增 `livetrans/keys.py` —— 控制台填写 key 保存到 `prototype/keys.env`（环境变量风格 `export ENV=值`，权限 600，已入 `.gitignore`，可 source 供 curl 复用）；启动主程序/总结器时经 `merged_env()` 注入子进程环境变量，核心代码零改动。优先级：环境变量 > keys.env（CLI 用户不受影响）。徽章状态机：未配置 / 已输入未保存 / 已保存 / 环境变量已设置 / 本地无需 / **归属不符警示**。
- **服务商-模型联动**：`config.example.yaml` 每个后端增加 `label`（展示名）+ `models`（该家模型清单，DeepSeek 2 / GLM 9 / 千问 6 / Kimi 4 / OpenAI 7 / Gemini 5 / Claude 5 / Grok 4 / Ollama 4）；GUI 选择服务商后模型下拉自动切换，且可自由输入清单外模型名；选择写回 `config.yaml` 的 `providers.<name>.model`。
- **桌面方式启动**：`assets/livetrans.svg` 图标（双色声波）+ `livetrans-gui.sh` 启动脚本 + `livetrans.desktop`（StartupWMClass=livetrans）；GUI 底部「添加到应用菜单」按钮或 `install-desktop.sh` 安装到 `~/.local/share/applications/`，按 Super 键搜索 LiveTrans 启动。
- **GUI 全面重构（深色主题）**：与字幕窗同族的深墨蓝底（#141824）+ 信号青操作色，内外音频沿用字幕窗绿/蓝语义色；ttk clam 深色样式全套（含 Combobox 下拉 Listbox）；三页签布局（翻译后端/音频源/会话总结）+ 底部常驻操作栏；签名元素：顶部双色声波呼吸动画。className=livetrans 供任务栏分组。
- 测试：selfcheck 5 项通过（新增 keys 存取/权限/注入项）；GUI 冒烟 + 功能冒烟 6 项通过（联动/徽章/落盘/env 注入/桌面安装）；pyright 零错误。
- 技能实测结论：frontend-design 技能的 token 系统/签名元素/自查流程可迁移到 Tkinter 桌面 GUI，效果好。

### 2026-09-09（四）：Key 归属自动识别 + keys.env（用户实测反馈修复）
- **用户反馈暴露的设计缺口**：旧逻辑 key 存哪由"当前下拉选中的服务商"决定，粘贴智谱 key 时未切下拉 → key 被存到 deepseek 名下、模型列表仍是 DeepSeek 的。联动方向只有 服务商→模型，缺了 key→服务商。
- **修复：粘贴即识别**（`detect_provider()` 按格式签名）：`sk-ant-`→claude、`AIza`→gemini、`xai-`→grok、智谱两段式 `xxxxx.yyyyy`→glm（主流厂商中唯一用该格式）；自动切换服务商下拉 → 模型列表联动 → key 归位，状态栏提示"检测到 xx 的 Key"。`sk-` 系（DeepSeek/OpenAI/Kimi/千问同前缀）无法可靠区分，保持所选服务商，徽章给出归属不符警示。兼容粘贴带 `Bearer ` 前缀/引号。
- **存储改 keys.env**（环境变量风格）：`export GLM_API_KEY=xxx` 每行一条，终端 `source prototype/keys.env` 后 curl 可直接用 `$GLM_API_KEY`；权限 600；保存时自动移除旧 keys.yaml 防双源混淆；兼容读取旧 yaml（provider 名键）。
- **启动期归属纠错**：加载时对每个 key 跑签名检测，发现归属错误（如 deepseek 名下的智谱 key）自动移到正确服务商名下并提示。
- 测试：selfcheck keys 项扩充（env 读写/legacy 迁移/签名识别/overlay 双键名/env 优先）；GUI 功能冒烟 6 项：启动纠错 / 切换后 key 显示 / 粘贴智谱 key 全链路联动 / 四种签名 + sk- 不误切 / keys.env 落盘 + 旧文件移除 / 子进程注入；curl 直连智谱 API 验证 source 复用（假 key 得 401，链路通）。
- 教训记录：**测试脚本清理时误删了用户的 keys.yaml（真实 key）**——后续凡涉及用户数据文件的测试，必须先确认文件归属/备份，重定向到临时目录（本次 GUI 冒烟虽重定向了 LIVETRANS_KEYS_DIR，但首跑失败时的一次 `rm -f keys.yaml` 在重定向前执行了）。

### 2026-09-09（五）：模型列表动态获取（用户追问静态清单来源暴露的短板）
- **用户追问确认**：模型下拉清单为**静态手写**（基于训练知识的各家公开模型线，可能滞后于现实，如 moonshot-v1 系已趋下线）。补上动态来源。
- **新增「从 API 获取」按钮**：`fetch_model_list()` 走 OpenAI 兼容标准端点 `GET {base_url}/models`（urllib，零新依赖）；解析兼容 `data[].id`（OpenAI）/`models[].name`（Ollama tags），剥 Gemini 的 `models/` 前缀，去重排序；claude 后端附 `x-api-key + anthropic-version` 头。后台线程执行，完成后回主线程刷新下拉；结果写入 `cfg.providers.<名>.models`，随下次保存持久化到 config.yaml（下次启动无需联网即用最新清单）。Key 未配置时前置拦截提示；401/连接失败给可读错误。
- 测试：本地 mock HTTP 服务器验证解析（前缀/去重/空项/鉴权头）+ 错误路径（401/拒连）+ GUI 按钮全链路（下拉更新/cfg 写回）；selfcheck 5 项保持通过；pyright 零错误。

### 2026-09-09（六）：Key 更换流 bug 修复（用户实测反馈）
- **bug 复现**：粘贴一个 key 自动切换服务商后，清空再换另一家的 key（`sk-` 前缀：Kimi/DeepSeek/OpenAI/千问共用），服务商与模型列表不更新，且新 key 被存到旧服务商名下。根因：`detect_provider` 对 `sk-` 返回 None（前缀多家共用无法自动区分），没有任何切换路径，下拉停在自动切换后的旧服务商。
- **修复（Key 输入统一入口 `_handle_key_input`）**：
  - `sk-` key **粘贴时弹模态窗选归属**（DeepSeek/Kimi/OpenAI/通义千问四按钮，默认高亮当前基准，取消 = 放弃本次输入）；
  - **清空输入框 = 撤销本次未保存输入**：临时归属的 key 移除、下拉回退基准服务商；已保存的 key 不受影响（旧版会把已存 key 从内存抹掉、随后保存时从 keys.env 删除——一并修复）；显式删除走「清除」按钮；
  - **手动切换服务商**：输入框中的新 key 保留并随切换重新归属（临时归属移动），已存 key 则换显对应服务商的存量；
  - 键入防抖 300ms + (服务商, 输入值) 去重 + 弹窗防重入；支持 X11 中键粘贴（`<<PasteSelection>>`）；保存/启动后以当前选择为新基准；
  - 关窗统一走 `_on_close` 取消 after 定时器，消除 Tcl 残留报错。
- 测试：10 项 GUI 功能验证（原 bug 场景复现修复 / 多轮更换 / 清空语义 / 手动切换保留 / 真实弹窗点击与取消 / 真实 Ctrl+V 事件链路 / 清除按钮）+ selfcheck 5 项 + pyright 零错误。
- 教训：测试目录必须**预清理**（上一轮失败运行残留的 keys.env 被新一轮 Launcher 加载，导致断言误报排查浪费一轮）。

### 2026-09-09（七）：语言输入/输出分离 + UI 细节打磨（用户 5 项反馈）
- **输入/输出语言分离**：「输入」= ASR 源语言（`asr.language`，新增「自动识别」选项=Whisper 自动检测，默认）、「输出」= 译文目标语言（`translate.target_lang`）；8 种语言（zh/en/ja/ko/fr/de/ru/es）显示名↔代码映射，保存往返验证通过。
- **小字注释→悬停提示**：新增 `ToolTip` 气泡类，界面上的说明文字（keys.env 说明/模型清单说明/Whisper 档位说明/音频分栏说明/总结说明等）全部迁移为 hover 提示，主界面保持清爽；「添加到应用菜单」从底栏移到顶栏 Ghost 轻量按钮，安装后显示「✓ 已在应用菜单」。
- **控件放大**：Combobox `arrowsize=20` + 内边距 (8,6)；按钮 padding 加大一档；上下文句数由 Spinbox 换成可编辑 Combobox（大箭头可下拉 0-30，仍可键入）；**自绘 20px 大指示器**（PhotoImage 逐像素生成：单选圆环+选中圆点 / 复选方框+对勾），经 `element_create image` + 自定义 layout 替换 clam 主题的小圆点，配色保持信号青/外绿/内蓝语义。
- **专业模式按需展开**：「专业领域」输入框 + 新增「术语表…」文件选择按钮 + 术语表状态提示，包进 `pro_frame`，选「专业」grid 展开、选「普通」grid_remove 收起（旧版常驻置灰）。
- 顺手修复：卡片内 ttk.Label 底色与卡片不一致（新增 Panel.TLabel 样式）。
- 测试：11 项 GUI 验证（语言默认/保存/重载/自动识别、专业模式展开收起、指示器图片+布局、arrowsize、tooltip 挂载与显示/隐藏、顶栏菜单按钮、智谱 key 回归）+ selfcheck 5 项 + pyright 零错误。
- 教训（再次）：**失败的测试运行不会执行尾部的清理语句**，测试前必须预清理（config.yaml/keys.env/临时目录），本轮因上轮残留又浪费一次重跑。

### 2026-09-09（八）：启动可见性重构 + 运行日志（用户反馈"看不见字幕窗"）
- **根因**：main.py 旧启动顺序为 Whisper 加载 → 捕获启动 → 翻译后端构造 → **最后才建字幕窗**。首次运行要下载 460MB 模型（期间无任何窗口）、key 缺失/设备失败任一环节异常进程直接退出——用户什么都看不到；且 launcher Popen 未捕获输出，错误信息全丢。
- **重构 main.py**：字幕窗**立即弹出**（置顶），Whisper/音频源/翻译后端全部移入后台 boot 线程；每步进度与错误实时写入字幕窗状态栏（`SubtitleWindow.set_status`，队列+轮询实现线程安全）+ 带时间戳的 stdout 日志；任一环节失败**不再静默退出**——错误停在状态栏等用户关窗。麦克风/monitor 每路 spawn 单独 try/except。
- **运行日志**：launcher 启动主程序时 stdout/stderr 重定向到 `logs/run-<ts>.log`；**异常退出自动弹窗展示日志尾部**（直接看到缺 key 等原因）；底栏新增「运行日志」按钮打开日志目录。`logs/` 入 .gitignore。
- 测试：main 三条路径（成功链 5 段状态推进+全节点日志 / 缺 key 错误上状态栏 / 设备失败不再崩）+ launcher 日志重定向/异常弹窗/句柄关闭，全部通过；selfcheck 5 项；pyright 零错误。
- 备注：若浏览器播放无系统声音字幕，多为 monitor 源与实际输出设备不匹配（声音走 HDMI 而 monitor 选了模拟输出）——日志会打印所用 monitor 源名，README 增加排查提示。

### 2026-09-09（九）：run.log 固定单文件 + 模型下载进度条（用户 2 项反馈）
- **运行日志改固定文件**：`prototype/run.log`（当前文件夹），每次启动 **w 模式覆盖**上次日志（原 logs/run-<ts>.log 累积方案废弃）；「运行日志」按钮直接打开该文件（无日志时提示先启动一次）；异常退出弹窗与状态栏文案同步改为指向 run.log；.gitignore 更新。
- **模型下载进度显示**：huggingface_hub 的 tqdm 进度条写在 stderr → 新增 `_StderrTee`（asr.py）：临时替换 `sys.stderr`，按 `\r`/`\n` 切行解析 tqdm 格式（`45%|██| 231M/465M [.., 19.2MB/s]`）得 (百分比, 已下载, 总量, 速度)；原文透传到真实 stderr（CLI/日志保留）；`isatty()=True` 防 tqdm 在重定向场景自动禁用；0.5s 节流且 100% 始终放行。`WhisperASR(progress_cb=...)` 加载完 finally 恢复 stderr。
- **字幕窗进度条**：`SubtitleWindow.set_progress(pct|None)`（线程安全队列，UI 线程只取最新值）；6px Canvas 细条（信号青填充）位于状态栏上方，下载时出现、完成/无下载隐藏。
- 接线：main.py boot 中 `_on_download` -> 状态文字（`下载模型 small: 45%（231M/465M · 19.2MB/s）`）+ 进度条；模型就绪后 `set_progress(None)` 收起。
- 测试：5 项——tqdm 解析（含 k/M/B 单位、透传、isatty）/ 节流+100% 放行 / 进度条显示钳制隐藏 / main 回调接线（progs == [0,45,100,None]）/ launcher 两次启动同文件覆盖（测试前备份恢复用户 run.log）；selfcheck 5 项；pyright 零错误。

### 2026-09-09（十）：模型预下载按钮（用户反馈：不等翻译时再下载）
- **`livetrans.asr.preload_model(model_size, progress_cb)`**：走 faster-whisper 自带 `faster_whisper.utils.download_model`（与 WhisperModel 运行时同一仓库/缓存路径，之后启动直接命中）；复用 `_StderrTee` 捕获 tqdm 进度；finally 恢复 stderr；旧版无该函数时退化为实例化一次触发下载。
- **控制台「语音识别」卡片新增「下载模型」按钮**（模型下拉旁）：后台线程下载所选档位；按钮文本实时显示百分比（“下载中 40%”，下载中禁用防重入）；状态栏显示 `下载模型 small: 40%（186M/465M · 19.2MB/s）`；完成显示「模型 X 已就绪 ✓（启动时直接加载，无需联网等待）」，失败显示原因且按钮恢复可重试。悬停提示含 HF_ENDPOINT 国内加速说明。
- 测试：5 项——preload_model 进度回调/节流窗口行为/stderr 恢复（含异常路径）/ GUI 成功链路（按钮进度文本+状态栏+就绪恢复）/ 失败链路 / 防重入（连点三次只启动一次）；selfcheck 5 项；pyright 零错误。
- 备注：首次踩到 _StderrTee 节流在测试中的表现（两条进度间隔 <0.5s 时后者被吞，100% 始终放行）——属设计行为，测试补间隔后通过。

### 2026-09-09（十一）：状态栏改为右下角 toast（用户反馈：常驻状态文字会拉伸窗口）
- **问题**：launcher 底栏常驻状态 Label 显示最近一条状态，长文本（下载进度/错误详情）会把窗口撑宽。
- **方案**：新增 `StatusToast` —— 状态消息改为**窗口右下角浮动气泡**：`place(relx=1, rely=1, anchor=se)` 锚定不参与布局计算（绝不改变窗口尺寸）；左侧色条标识级别色；文本 `wraplength=440` 多行换行，>400 字符截断；停留时长分级（错误 10s / 警告 7s / 普通 5s）；新消息直接替换旧气泡；**悬停暂停消失计时、移开重启**（长错误来得及读完）。`set_status` 重写为 toast 入口，底栏常驻 Label 移除（只留三个按钮，更紧凑）；关窗清理 toast。
- 测试：7 项——右下角定位+窗口尺寸不变 / 自动消失 / 超长截断+多行换行不拉伸 / 新消息替换 / 悬停暂停移开重启 / 时长分级 / 启动链路回归；selfcheck 5 项；pyright 零错误。

### 2026-09-09（十二）：模型存放项目文件夹（用户：不依赖 HF 缓存，直接放当前文件夹使用）
- **模型目录**：`prototype/models/<档位>/`（`asr.MODELS_DIR`）。**运行时优先从该目录加载（`local_model_path`：目录含 model.bin 即就绪）——存在即用、绝不联网**；目录缺失才回退模型名（HF 缓存/自动下载，`loaded_from` 记录实际来源，main 启动日志标注「本地目录 xxx / HF 缓存」）。
- **`preload_model` 重写**：下载**落到项目 models/<档位>/** 而非 HF 缓存——优先 `huggingface_hub.snapshot_download(repo_id=_MODELS[size], local_dir=...)`（repo 与官方 download_model 同源）；拿不到 `_MODELS` 时退化为 download_model 到缓存再 `copytree` 过来；产物校验（缺 model.bin 明确报错）；进度捕获/已就绪秒回不变。手动把模型文件拷进该目录同样生效。
- **launcher**：「下载模型」按钮随就绪状态切换（`✓ 已在本地` / `下载模型`，模型下拉切换与下载完成都刷新）；完成 toast 改为「已保存到 models/<档位>/」；悬停提示改为项目目录说明。
- **顺带修复严重隐藏 bug**：上轮插入 preload_model 时 `WhisperASR.transcribe` 被挤出类成悬空死代码（真实运行会 AttributeError）——此前测试全部用 mock ASR 未暴露，本轮复验方法行为（分段拼接/短段忽略）并已归位。
- `.gitignore` 加 `prototype/models/`。测试 9 项：transcribe 回归 / local_model_path 三态 / WhisperASR 本地优先传路径 / 已就绪秒回不联网 / snapshot 落项目目录+进度解析 / 退化路径拷贝 / 产物校验 / main 来源判定 / launcher 按钮刷新；selfcheck 5 项；pyright 零错误。
- 教训：**类中插入模块级函数时必须检查后续方法的缩进归属**——上轮 replace 把函数插在类方法之间，transcribe 被静默吞掉；mock 测试无法发现"方法消失"，涉及类结构改动应加实例方法存在性断言（本轮已补）。

### 2026-09-09（十三）：真实下载 tiny/small 到项目 models/ + Xet 兼容修复
- **回答用户"模型下好了吗"**：此前一个都没真实下载过（全部 mock 测试）。本轮真实联网下载：**tiny（75MB，~24s）与 small（464MB，~3min）均已就绪于 `prototype/models/`**，hf-mirror 镜像速度约 3MB/s。
- **真实环境暴露 Xet 兼容问题并修复**：huggingface_hub 1.30.0 大文件默认走 Xet 存储后端（cas-server.xethub.hf.co），**与 HF_ENDPOINT 镜像不兼容（CAS 401）**，小文件成功而 model.bin 失败。修复：`preload_model` 与 `WhisperASR` 下载路径统一 `os.environ.setdefault("HF_HUB_DISABLE_XET", "1")` 禁用 Xet 走普通 HTTP（尊重用户显式设置），镜像与直连均兼容。
- **真实链路验证（无 mock）**：`WhisperASR('tiny')` 从项目目录加载 0.2s、`WhisperASR('small')` 加载 2.0s，各完成一次真实转写。备注：**纯静音转写会出 Whisper 经典幻觉**（small 对 1s 静音输出 'you'）——实际管线中 VAD 保证静音到不了 Whisper，无需处理，记录备查。
- 下载命令范式：`HF_ENDPOINT=https://hf-mirror.com python3 -c "from livetrans.asr import preload_model; preload_model('small')"`。

### 2026-09-09（十四）：内部音频根因修复——monitor 自动选择改为跟随系统默认输出（用户实测：浏览器视频无字幕）
- **用户反馈**：浏览器播放英文视频，字幕窗无内部音频字幕。run.log 显示监听 `模拟输出.monitor`，而系统默认 sink 实为 **USB 音频**——浏览器声音走 USB，程序监听没声音的模拟口。
- **根因**：`default_monitor_source()` 硬编码"优先 analog 再 usb"，与用户实际输出设备无关。内部音频的正确语义 = **用户当前听到的一切**，应跟随系统默认输出。
- **修复**：`_default_sink()`（pactl get-default-sink）→ 默认 sink 名 + ".monitor" 且存在于源列表即选中；回退链：默认 sink 无 monitor > analog > usb > 任一。用户 config 的 `monitor_source: null`（自动选择）重启即生效，无需改配置。
- **真实验证（非 mock）**：paplay 播 3s 测试音到默认 sink → 修复后的选择（USB monitor）ParecCapture 实捕获 RMS 0.234；**对照组（修复前会选的模拟 monitor）RMS 0.0000**——用户问题确凿复现并消除。单元测试覆盖默认 sink 命中/无 monitor 回退/无 pactl 边界。
- **日志位置答疑**：用户在看已弃用的旧 `logs/` 文件夹（测试残留），实际日志为 **`prototype/run.log`**（13:31 的运行记录完整：模型 0.5s 本地加载、双路音频、deepseek-v4-flash 后端就绪、80s 会话）。旧 logs/ 已删除。另发现用户已自行通过「从 API 获取」用上 deepseek-v4-flash 模型名 + key 配置成功（无 key 报错）——功能被真实使用。
- 遗留无害项：会话结束尾部有 `terminate called without an active exception`（parec SIGTERM 时 C++ runtime 打印），不影响功能，后续里程碑清理。

### 2026-09-09（十五）：亲测内部音源端到端 OK + 新增诊断工具（用户反馈"仍无字幕"，要求自行开网页测试）
- **亲测（用户要求）**：Chrome（独立 profile + `--autoplay-policy=no-user-gesture-required`）打开本地 autoplay 页循环播放 JFK 英文演讲（openai/whisper 官方测试样本 jfk.flac，GitHub 直连下载成功）；探针（USB monitor→VAD→Whisper small）完整跑 90 秒：**转写出 `'And so my fellow Americans, ask not.'` / `'what your country can do for you'`**（与音频内容一致），整体 RMS 0.0457——**浏览器→默认 sink→USB monitor→识别全链路验证通过**。期间 USB sink RUNNING。注：agent-browser 不可用（无 Node），改用系统 Chrome。
- **用户 13:43 会话复盘**：monitor 已正确选中 USB（上轮修复生效），但 39 秒会话 internal 0 条、mic 仅 1 条 "you" 幻觉——若音箱在放英文视频，麦克风必然收到大量声音；**推断当时 USB sink 上实际没有声音**（播放器/系统静音、视频未真正播放、或播放时长不足）。
- **新增 `prototype/diagnose.py` 诊断探针**：`python3 diagnose.py [秒数] [语言]`——实时打印 monitor 选择、每段识别文本、整体 RMS 与结论（无声→查音量/静音/输出设备；有声无段→音量太小；出文本→链路正常）。无字幕时先跑它一锤定音。
- 测试残留清理（Chrome 独立 profile/临时文件）；selfcheck 5 项通过。

### 2026-09-09（十六）：字幕滚动 + 生成提速（原文即时上屏+译文并行）+ 实时音量条（用户 3 项反馈）
- **字幕滚动阅读**：每栏改 Canvas+Scrollbar 可滚动结构，历史保留 50 条（原 3 条即销毁）；滚轮（MouseWheel/Button-4/5）进入面板自动绑定、离开解绑；**贴底时新字幕自动跟随、上滚阅读时不拽回**（加条前判定 at_bottom）。
- **生成提速（两项结构性优化）**：① **原文即时上屏**——旧版 DisplayItem 等翻译完成才 post（原文也要等 1-2s LLM 延迟）；现改 `translation=None` 占位（"… 翻译中"暗灰小字）先上，翻译完成 `update_translation(item_id)` 原位补全（白字 15bold）——**原文体感延迟 = 断句+转写（约降 40-60%）**；② **译文线程池 2 并发**（ThreadPoolExecutor，乱序完成乱序上屏；ContextWindow 在完成时 append 保持"最近对话"语义；JSONL 写完整行加锁）。实测 mock：原文 0.15s 上屏，4 句连说总 0.80s（串行 1.6s）。
- **实时音量条**：每栏标题右侧 90x8 Canvas 电平条（外绿/内蓝）；`ASRWorker(on_level=)` 每路每 100ms 上报 RMS（字幕窗 `set_level` 线程安全队列），UI 侧 EMA 平滑（0.55 衰减防抖）+ 开方标度（小音量可见）；排查"没声音"一眼可辨。
- 文档：README 增加字幕节奏说明与提速调优（模型档位 / silence_ms）。
- 测试：8 项——占位/补全样式、50 条保留+scrollregion、贴底跟随+上滚不拽、滚轮、电平映射/EMA 衰减、TranslatorWorker 原文 0.15s+并行 0.80s+JSONL 完整、电平回调节流 12 次/1.2s；真实字幕窗冒烟（双栏+占位+补全+电平条）；selfcheck 5 项；pyright 零错误。
- 教训（测试侧）：① 输出被 Python 缓冲吞掉（断言失败时看不到 traceback，`-u` + `2>&1` 才暴露）；② `cget('font')` 返回字符串不是元组（`[3]` 取字符）；③ EMA 防抖断言需连续喂零样本衰减。三个都是测试写法问题，功能本身一次写对。

### 2026-09-09（十八）：字幕条目改版（时间+延迟指标/并排布局）+ 测速按钮 + 翻译慢根因确诊（用户 2 项反馈）
- **字幕条目改版**（subtitle.py）：删掉条目上的来源前缀（`[系统音频(USB)]` 等，栏标题已表明来源）→ 改为顶部 8px 小字元信息 `HH:MM:SS · ASR 213ms · LLM 1.2s`；正文改**原文（灰 10px，左 166px 列）与译文（白 14px bold，右 250px 列）并排**——译文更宽更大加粗，突出翻译结果。DisplayItem 增 `ts/asr_ms/llm_ms`；`update_translation(item_id, text, llm_ms)` 流式增量不重复写 LLM 指标，终态回填。
- **延迟计时全链路**：ASRWorker 测每段转写耗时 → `SegmentEvent.asr_ms`；TranslatorWorker 测 LLM TTFT（首个增量）与总耗时 → 字幕回填 + JSONL 新增 `asr_ms/llm_ms/llm_ttft_ms`。
- **控制台「测速」按钮**（服务商行）：真实流式一句话实测 TTFT/总耗时/吐字速度，弹窗诊断——TTFT≥2.5s 提示疑似深度思考/网络远；模型名含 reasoner/thinking/r1/o1 直接提示换非思考模型；附推荐型号清单。
- **翻译慢根因确诊（真实 API，用户配置 deepseek-v4-flash）**：TTFT 2831ms（首次）/1000ms（后续）、总 4671ms、流式仅 2 次增量——**服务端憋到最后一次吐，疑似深度思考型行为**；对照 `deepseek-chat` 同 key 同代码：TTFT 1000ms、总 653ms（快 7 倍）、正常流式。**结论：换 deepseek-chat 即解决**（GUI 测速按钮可自助复测）。
- 测试：mock 5 项（元信息/并排/补全+LLM 回填/流式不重复/ASR 计时/Worker 计时+JSONL）——ASR 测试改用 selfcheck 同款调制人声信号（纯静音 VAD 不切段属正确行为，静音段测试桩曾误判）；GUI 测速按钮就绪；真实 API 测速对比完成；selfcheck 5 项；pyright 零错误。

### 2026-09-09（十九）：模型列表默认 API 获取 + 字幕上下布局 + 词数断句（用户 4 项反馈）
- **① 模型列表默认 API 获取**：`_fetch_models(auto)` 统一手动/自动——启动后、切换服务商后、粘贴 Key 归位后自动触发（有 Key 才请求；失败静默保留静态清单不弹错）；手动按钮保留（失败会提示）。真实生效验证（fake fetch 注入）+ 失败静默分支覆盖。窗口关闭时后台 after 回调包 TclError 防噪声。
- **② 测速输出含模型**：状态行与弹窗均显示调用模型（`测速 deepseek-chat ✓ TTFT ...`）。
- **③ 字幕上下两行**：条目改回纵向三行——元信息 / 原文（灰 10px）/ 译文（白 14px bold），同宽 wraplength=430，不再两列（两列导致原文两三词就换行）。
- **④ 词数断句**：`asr.max_words_per_segment`（默认 14，0=不限）——转写后 `split_by_words` 拉丁按词分组、CJK 按字符块（词数×2）尽量停在标点；`max_segment_sec` 15→8s（长语音强制切段提前）；GUI ASR 卡片新增"断句词数"Spinbox。
- **测试过程中抓到真 bug**：上轮给 ASRWorker 加电平/拆分时 `run()` 引用 `self.asr_cfg` 但 `__init__` 未存 → 每段转写抛 AttributeError 被吞、**长句拆分完全失效**——用 selfcheck 同款调制人声信号（两音正弦在 aggressiveness=3 下不被认作语音）才暴露。已修（`self.asr_cfg = asr_cfg`）。教训：mock 测试桩信号太假（全零/纯两音）会连 VAD 都不触发，测 ASR 链路必须用真实频谱特征信号。
- 测试 8 项：split_by_words（拉丁/CJK/不限）、ASRWorker 45 词→4 条事件、上下布局三行、译文补全+LLM 单次回填、自动获取（无 Key 跳过/有 Key 拉取替换/失败静默保留）、selfcheck 5 项；真实环境冒烟（用户 keys.env 存在时自动获取真实触发无报错）；pyright 零错误。

### 2026-09-09（二十）：测速修复 + API 历史记录 + Whisper 全量移除（用户 3 项反馈）
- **① 测速 no attribute 修复**：根因 = `_speed_test_bg` 把原始 yaml dict 的 providers 直接传给 `LLMTranslator`（需 ProviderConfig 对象，dict 无 `.api_key_env` 属性）→ 改用 `load_config(CONFIG_PATH)` 正规加载；顺带发现 launcher 缺 `import time`（bg 线程 `time.monotonic()` NameError）——两个 bug 都修。
- **② API 历史记录**（keys.py）：`api_history.yaml`（项目目录，600，.gitignore）存 `{provider, key, time}`，最新在前/按 (provider,key) 去重/上限 20；保存时自动记录（`_record_history`）；GUI「服务商与 Key」卡片新增「历史」下拉（key 打码 `sk-9f3a...91a`）+「载入」按钮——一键切服务商+回填 Key；真实链路验证（保存→清空→载入回填→徽章已保存）。
- **③ Whisper 全量移除（一个不留）**：`asr_sensevoice.py` 删除、SenseVoice 合并进 `asr.py`；`WhisperASR`/`local_model_path`/faster_whisper 导入全删；`ASRConfig` 去 `engine/model_size/device/compute_type`（保存时 pop 遗留字段）；main.py 引擎分支删除（固定 SenseVoice）；launcher 删引擎下拉与模型档位下拉（保留断句词数）；requirements.txt 去 faster-whisper；config.yaml/example 清理；diagnose.py 改用 SenseVoiceASR。grep 全库零残留（仅 selfcheck 断言更新）。
- selfcheck 更新为 6 项（新增词数断句项；替换 model_size 断言）。真实链路：SenseVoice 1.1s 从 models/sensevoice 加载；当前配置流式翻译 TTFT 1000ms/总 1808ms 正常。
- 备注：用户当前 deepseek-v4-flash 增量仍只有 2 次（服务端行为），建议换 deepseek-chat（上轮实测快 7 倍）——测速按钮可自助复测。

### 2026-09-09（二十二）：**deepseek-chat 下线纠正** + 原文流式上屏 + 测速窗口化（用户 4 项反馈）
- **③ 模型清单纠错（用户指正）**：用用户 key 直查 DeepSeek 官方 `/models`：**仅 deepseek-v4-flash / v4-pro / v4-flash-vision-exp**（2026-07-31 V4 更新后 deepseek-chat/reasoner 已下线）；web_search 交叉证实。静态清单已修正；**我上轮"换 deepseek-chat 快 7 倍"的建议作废**（当时该名实测 653ms 疑为服务端兼容别名映射）。教训：静态清单必然过时，上一轮的"自动获取"才是正解；给用户的选型建议必须先查官方 /models。
- **①+② 测速结果窗口化**：messagebox 弃用（细长 + 建议啰嗦）→ 自绘 `Toplevel`（约 471×385，居中，Esc/按钮关闭）：五行简洁——模型 / 首字时间 / 总耗时 / 吐字速度 / 译文样例；建议与评级全部移除；重复测速先销毁旧窗。
- **④ 原文流式上屏**（参考 LiveTranslate 的实时体验核心）：`VadSegmenter.in_speech + current_speech()` 暴露进行中语音快照；`ASRWorker(on_partial=)` 每 0.8s 独立线程转写增量快照（SenseVoice 0.1s/次，与 final 共锁串行）→ 字幕窗每栏底部固定"识别中"实时行（`set_partial`，暗灰 … 前缀），**原文边说边上屏**；段定稿即清空、正式条目入库。SenseVoiceASR 加锁（partial/final 并发串行化）。
- 顺带：SubtitleWindow 关窗取消 _poll 定时器（消除关字幕窗时控制台 Tcl 噪声——此前用户真实可遇）。
- 测试：5 项（VAD 快照/ASRWorker 4s 语音 5 次增量+清空+快照增长/live 行三态/final 入库/测速窗口内容与尺寸/deepseek 清单）+ selfcheck 6 项 + pyright 零错误。测试教训四连：静音段 VAD 不切段属正确、CJK 拆条断言用字数、after 回调需 mainloop/update 处理、真实 keys.env 会让"无 Key"分支测试悄悄变绿（测试必须隔离 keys）。

### 2026-09-09（二十三）：分路暂停 + 主窗聚焦 + 字幕样式设置（用户 3 项反馈；第 4 项速度分析见回复）
- **① 分路暂停**：字幕窗每栏标题栏「暂停/继续」按钮（控制台与字幕窗是两个进程，实时操作就近放字幕窗）——`ASRWorker(pause_event=)` 置位即丢弃该路音频（零识别零翻译、电平归零、清"识别中"行、标题"（已暂停）"），再点恢复。main 为每路建 Event 并经 `on_pause_toggle` 接线。
- **② 主窗聚焦**：字幕窗顶部模式条「双栏 / 外部 / 内部」——聚焦模式隐藏另一栏、该栏全宽；`_set_layout` 重排 pack 并回调持久化 `subtitle.layout`。
- **③ 字幕样式设置**：字幕窗「样式」面板——透明度 Scale(0.3-1.0)、字号 Scale(0.8-1.6)、译文颜色 5 色块、原文颜色 3 色块；`apply_style` 实时生效（窗口 alpha + 存量标签字体/颜色刷新，src_labels 列表联动）并回调 `_persist_subtitle_style` 写 `config.yaml` 的 `subtitle:` 段（read-modify-write 保留其余字段）；启动时加载。
- 测试：4 组（ASRWorker 暂停零产出/恢复、暂停按钮联动、聚焦布局、样式联动+持久化保留其余字段）+ 真实字幕窗冒烟（样式回调 4 次）+ selfcheck 6 项 + pyright 零错误。测试教训：该 Tk 的 `cget('font')` 返回字符串（`"sans 8"`）非元组，断言用 split；`root.cget('alpha')` 不存在，用 `attributes('-alpha')`。

### 2026-09-09（二十四）：样式面板 NameError 修复 + 译文顺序门 + 4G 显存本地方案（用户 3 项反馈）
- **① 样式面板空窗**：`_open_style_panel` 引用未定义常量 `PANEL`（只有 `PANEL_2`）→ 点击时构造到一半 NameError，Toplevel 已建但内容全无。补常量并统一使用。教训：**上一轮自称"pyright 零错误"但根本没跑 read_lints**——自检必须真跑，不能引用上轮结论。
- **② 译文乱序**：线程池 2 并发下后句先翻完、前句仍在流式（显示层面"前面的一直转圈"）。修复 = **顺序门**（TranslatorWorker）：并行预取保留，但显示/上下文/JSONL 一律按提交顺序——非头部句的流式增量只进缓冲，头部句增量实时上屏，终态按序应用并推进游标。**顺带修复两个连带 bug**：a) ContextWindow 的 append 原在 translate_stream 内按完成顺序执行 → 移到顺序门按显示顺序记录（上下文质量修复）；b) **partial 分支缺 item_id → KeyError → 所有流式译文变成"[翻译失败:KeyError]"**（本轮新写的顺序门代码自带的 bug，被新测试当场抓住）；c) fallback 从 translate()（重试）改为直接标注错误文本（避免乱序 ctx + 双倍延迟）。
- **③ 4G 显存本地方案**（web_search 调研）：多份 2026 指南一致推荐 **Qwen3-4B q4（约 2.5GB 显存）** 为 4GB 档最佳；Ollama 默认模型改 `qwen3:4b`，备选 `qwen2.5:3b`（更省）/ `gemma3:4b`（多语言），config.example.yaml + 用户 config.yaml + 控制台提示同步。
- 测试：4 组（样式面板内容/顺序门显示+ctx+JSONL 顺序/头部流式增量/ollama 清单）+ selfcheck 6 项 + pyright 零错误。
- 教训：本轮 KeyError 由新测试当场抓到——**"改完自测"里必须包含新代码路径的端到端断言**（partial 分支在无 partial 的旧测试里是盲区）；引用上轮的"pyright 零错误"结论前必须重新真跑。

### 2026-09-09（二十五）：字幕宽度跟随窗口 + 样式窗可缩放 + 纯享版（用户 3 项反馈）
- **① 字幕宽度跟随窗口**：根因是 wraplength 固定 430px。`_Pane` 新增 `wrap_targets` + `_retab(width)`，canvas `<Configure>` 与每条 add_item 后都重设（留滚动条余量）；主窗 1100x480 + minsize(760,360) 可自由缩放。验证：1400 宽双栏 675（半窗）/聚焦 1355（全窗）/缩窗 855 全部跟随。
- **② 样式窗可缩放**：去掉 `resizable(False,False)`，`minsize(430,300)` 允许拖动边界调整。
- **③ 纯享版**：topbar「纯享」开关 → 无边框置顶 Toplevel（整窗点击拖动）：每路"原文小字+译文大字"两块、底部 partial 实时行、✕关闭/⚙样式迷你按钮；背景 4 色可选（黑/深蓝/墨/暗绿）+ 透明度 0.2-1.0 + 字号 0.8-2.0，设置入口在主样式面板"纯享版"区，实时生效并随 subtitle 段持久化（`pure_bg/pure_opacity/pure_scale`）；打开即显示各路最新字幕。
- 测试：3 项（动态宽度三态/样式窗可缩放/纯享开关+同步+partial+样式联动+关闭）+ selfcheck 6 项。

### 2026-09-09（二十六）：Qwen3-4B 本地部署落地 + 思维链剥离（用户要求部署并运行）
- **Ollama 路线放弃**：ollama.com 404（URL 已变）→ GitHub release 资产 tar.zst 直连 0KB/s（被墙）、镜像 78KB/s（5 小时）——网络不可行；且官方安装需 sudo 密码（无终端权限）。
- **改走 ModelScope + llama.cpp**（国内网络最优）：`Qwen/Qwen3-4B-GGUF` 的 **Q4_K_M（2382MB）** 直连下载（ModelScope 百 MB/s 级，20 秒完成）落 `models/qwen3-4b/`；`llama-cpp-python[server]`（pip 预编译 wheel，CPU 可用免 GPU/免 root）内建 OpenAI 兼容 server `:8321/v1`。  ⚠️ **此路线已于 2026-09-10 移除**（GGUF 与 llama.cpp 代码全部删除，本地模型统一走 Ollama）。
- **发现并处理：Qwen3-4B 是深度思考模型**——输出自带 `<think>…</think>`，server 忽略 enable_thinking 参数。translate_stream 升级为**思维链感知流式**：think 阶段不上屏（字幕保持"… 翻译中"）、`</think>` 后的译文才流式显示、终态剥离思维链。对 Qwen3/DeepSeek-R1 类思考模型通用；也解释了 deepseek-v4-flash 的"憋着输出"行为。
- **真实运行结果**（本机 CPU，无 GPU）：翻译质量优秀（"The universe is under no obligation…"→"宇宙没有义务对你有意义。"）但 TTFT 39s（CPU 5-8 tok/s 下思维链数百 token）——本机不适合实时；**用户 4GB 显存 GPU 上 q4 约占 2.6GB，预期 TTFT 1-2s、总 2-4s，可用**。
- `llamacpp` provider 已加入用户 config.yaml（base_url http://127.0.0.1:8321/v1，model=gguf 路径）；启动 server 命令：`python3 -m llama_cpp.server --model models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf --n_ctx 4096 --port 8321`。

### 2026-09-09（二十七）：样式窗重排 + 双透明度 + API/本地分类 + 纯享可缩放 + 提示词升级（用户 4 项反馈 + LiveTranslate 调研）
- **LiveTranslate translator.py 源码调研**（README 未含提示词，抓源码）：a) **DEFAULT_PROMPT 规则式英文提示词**（单一最佳译文/无解释/专有名词保留/重复表达简洁/**ASR 纠错**）+ 4 场景预设（daily/esports/anime/webid）；b) **上下文对话式注入**（user/assistant 交替，优于纯文本）；c) **thinking_disable_body() 按服务商关闭深度思考**（deepseek/qwen/vllm/openai 四风格）——**这正是 deepseek-v4-flash"憋着输出"的官方解法**；d) stream_options 降级/总时限/重复检测/思考烧穿诊断。
- **落地三件**：① SYSTEM_NORMAL 升级为英文规则式（含 ASR 纠错条），_system 用 {source}/{target} 语言名映射；② 上下文改**多轮对话消息注入**（_messages user/assistant 交替）；③ **_thinking_disable()**：deepseek→`thinking:{type:disabled}`、qwen→`enable_thinking:false`、ollama/llamacpp+qwen→`chat_template_kwargs`，`_create_stream` 失败自动降级重试（LiveTranslate 同款防御）。TranslateConfig 加 source_lang（main 从 asr.language 传入）。
- **① 样式面板重排**：三区块卡片（窗口/文字/纯享版），区块标题+分隔线、滑条/色块统一 grid 列对齐。
- **② 双透明度拆分**：`win_opacity`（root alpha=背景）与 `text_opacity`（0.3-1.0，文字颜色向背景色 RGB 混合模拟——Tk 不支持逐标签 alpha）；`_blend()/_dst_color(bg)/_src_color(bg)`；旧 `opacity` 兼容迁移。验证：背景 0.5 时字体色不变；文字 0.5 → #ffffff 混向 #1e1e2e = #8e8e96。
- **③ API/本地分类**：ProviderConfig 加 `type: api|local`（example 全部补齐+用户 config）；控制台服务商卡片顶部「模型来源」radio（API 云端/本地模型），`_refresh_provider_list` 按类型过滤下拉、`_select_type_for` 在历史载入时同步类型。验证：API 8 家过滤正确、本地含 Qwen3-4B。
- **④ 纯享可缩放**：右下角「◢」grip（cursor=sizing），B1-Motion 计算新尺寸（min 320x160）并同步 wraplength；主窗 1100x480 可缩放、字幕换行宽度跟随（双栏=半窗/聚焦=全窗，上轮已做，本轮验证三态）。
- 测试：样式面板三区+双透明度混色数学验证、grip geometry 捕获断言（+100/+60 且 wraplength 跟随）、API/本地过滤切换、selfcheck 6 项；pyright 零错误。
- 测试教训：组合测试中真实 keys.env 触发自动获取后台线程会干扰窗口时序（测试必须隔离）；该 Tk 的 `attributes('-alpha')` 读取合法而 `cget('alpha')` 不存在；cursor 合法名是 `sizing` 非 `size_nw_se`；未映射窗口 `winfo_width()=1`，resize 断言基准须在 update 后取。

### 2026-09-09（二十八）：LiveTranslate 源码对照——测速无参数根因 + ThinkFilter 重写 + 布局重叠（用户 4 项反馈，源码已本地化）
- **用户 attach LiveTranslate-main 源码**，translator.py 全读：DEFAULT_PROMPT/PROMPT_PRESETS（4 场景）、`resolve_thinking_style()`（按 model id 与 endpoint 自动推断 deepseek/qwen/vllm/openai/off 五风格）、`thinking_disable_body()`、stream_options 降级、**"Thinking left ON silently burns the whole max_tokens budget"**（issue #38）注释直指我们遇到的"长时间翻译中"。
- **② "仍然长时间翻译中"根因**：上轮 `_thinking_disable` 只覆盖了 deepseek/qwen 两种且条件有缺陷。本轮移植 LiveTranslate 的 `resolve_thinking_style`：nested（deepseek/glm 模型或 deepseek/volces/z.ai/bigmodel 端点）→ `thinking:{type:disabled}`；openai/x.ai/anthropic 官端 → 不发参数；其余（qwen/ollama/llamacpp）→ `enable_thinking:false`。`_create_stream` 保留降级重试。
- **③ `<think>` 泄漏根因（两个叠加 bug）**：a) 流式剥离无跨 chunk 保护——`<thi`+`nk>` 拆分时标签碎片直接上屏；b) **ThinkFilter 状态机的 in_think 分支把思维链内容当安全文本输出**，且改写时 `find('</think>')` 消费逻辑丢失导致永不退出思考态。重写为真正的状态机：非 think 态 find('<') 输出前缀、非 think 标签原样保留；think 态只消费不输出、rfind('<') 保留闭合标签残片、find 命中即退出思考态继续处理余文；final 丢弃未闭合残片。端到端验证：跨 chunk think 流 → 字幕全干净、JSONL llm_raw 保留原始输出。
- **④ 详细日志**：run.log 每句 LLM 请求/完成（TTFT/总耗时/原文截断）；JSONL 新增 `llm_raw` 字段——**模型原始输出（含思维链）完整留存**，"后端内容全部可查"。用户跑一次会话后，deepseek-v4-flash 是否真正关闭思考、思考链多长，日志直接可见。
- **① 按钮重叠**：加"模型来源"行时 provider/key_badge/btn_speed 的 grid 行号未上移，与 radio 重叠。已重排（radio r0 / 服务商 r1 / API Key r2 / 历史 r3）。
- 测试：ThinkFilter 单元（跨块残片/正常标签/未闭合丢弃/长思维链滚雪球防护）+ 端到端（跨块 think 流→字幕干净+llm_raw 留存）+ thinking 风格三分支 + svc 布局不重叠 + selfcheck 6 项 + pyright 零错误。
- 测试教训三连：**测试桩的 translate_stream 绕过产品 ThinkFilter 会假绿**——必须 mock 底层 client 走真实全链路；ThinkFilter 改写时 find 消费逻辑丢失（改成 rfind-only），**状态机改动必须逐 chunk trace**（本轮 trace 直接暴露 buf 滚雪球与永闭）；测试 chunk 数据 '</th'+'nk>' 少了 'in' 两字符（期望值随错误数据写错）——**测试数据要从真实协议推导，不要手拍**。

### 2026-09-10：布局重叠修复 + 置顶按钮 + 纯享移除 + 滚动跟随真修复 + 背景暗度（用户 5 项反馈）+ 日志诊断
- **日志诊断（用户要求自查）**：run.log 显示 **LLM 已快**（TTFT 178-700ms、总 250-700ms，思考禁用生效，llm_raw 无思维链）；但 52 条记录全是 'the'/'y'/'theay' 类 **1-2 词碎片**（全部 mic 路）——当前瓶颈不是翻译而是**输入碎片化**（VAD 对麦克风环境声过敏感，每 1-2 词即切句，LLM 被灌满无意义短句）。建议：说话场景将 silence_ms 调回 650+ 或环境嘈杂时暂停麦克风路。
- **① 清除/测速重叠**：上轮加"模型来源"行时 API Key 行(原 r1)与 历史(r2) 未跟随重排——API Key 行控件与 服务商行(现 r1) 完全重叠（用户看到"清除跟测速重合"）。重排：r0 模型来源 / r1 服务商+徽章+测速 / r2 API Key+显示+清除 / r3 历史。全窗 44 网格位按父容器分组校验零重叠。
- **② 置顶按钮**：字幕窗 topbar「置顶 ✓/✗」toggle（-topmost 切换）。
- **③ 纯享版全量移除**：topbar 按钮、_toggle_pure/_pure_* 8 个方法、PURE_BGS、style 键 pure_*、_last_items 同步逻辑、样式面板纯享区——源码 grep 零残留。
- **④ 滚动跟随真修复**：用户描述的"长译文补全后被顶走"根因 = 贴底判断发生在**内容增高之后**（永远 false）。修复：uq 应用前先记录各栏贴底状态，应用后对贴底且 follow 的栏 stick_bottom；topbar 新增「↓ 最新」按钮（恢复跟随+跳底）。
- **⑤ 背景透明度独立**：Tk 硬限制——root alpha 必然整窗（含文字）。改为**背景暗度**方案：win_opacity 调背景色向黑混合（root 恒不透明），文字颜色混向当前背景色——两者完全独立（实测背景 0.5 时字体色不变、文字 0.5 混色 #8e8e96）。滑条更名"背景暗度"，tip 说明为 Tk 方案的近似实现。
- 测试：6 组（样式面板/双透明度独立/置顶/滚动三态+补全跟随/纯享零残留/网格无重叠）+ selfcheck 6 项 + pyright 零错误。
- 测试教训：① 跨页签 grid 重叠检测必须按父容器分组（不同页签同位合法）；② 贴底类断言必须在状态变化**前**采样（与被修 bug 同构）；③ 真实关窗走 _close()（直接 destroy 会留 after 噪声）。

### 2026-09-10：日志诊断 + partial 主流化 + 断句间隔可调（用户 2 项反馈）
- **日志诊断**：10:07 会话（真实视频）——内部音频正常（monitor=USB ✓、8 条英文识别）、**翻译层快**（TTFT 172-544ms/总 250-705ms，思考禁用生效、无堆积）；mic 路混入 3 条 'the' 碎片（环境声）。"翻译中很久"的体验根源 = **partial 只在底部小行、主字幕流要等切段+翻译才出条目**（长句 8 秒内大字区域空白）。
- **① partial 主流化**（LiveTranslate 同款体验）：说话开始即在**主字幕流**创建"进行中"条目（原文边说边长大、占位译文），定稿时正式条目**原位接替**（无重复），译文流式补全——大字区域不再空白。实现：`_Pane.upsert_live/finalize_live` + `_live_src`（partial 增量同步原文标签）+ `_poll` 条目循环先 finalize 再 add。
- **② 断句间隔可调**：控制台 ASR 卡片「断句间隔」Spinbox（300-1200ms 步进 50）→ `asr.silence_ms`；与「断句词数」并排。
- 测试：partial 创建/增长/定稿接替、断句间隔 550↔700 往返、ASRWorker 暂停回归；selfcheck 6 项；pyright 零错误。
- 测试教训：ad-hoc 组合测试的 update()/after() 时序抖动会造成间歇性断言失败（-u + 充分等待 + 直接打印状态三件套定位）。

### 2026-09-10：partial 主流化两个遗留 bug + 滚动 follow 重构（用户 2 项反馈）
- **① "原文出现在译文位置 + 同句复述 + 永远翻译中"**（两个叠加 bug）：a) `upsert_live` 增量更新时调 `update_translation`——把 partial 文本写进了**译文标签**（应只更新原文标签，译文位保持"… 翻译中"占位）；b) `finalize_live` 只清 `live_item_id` 标记**不移除条目**——正式条目到来时旧条目还在（同句复述），且残留条目的译文永远停在"… 翻译中"。修复：partial 只更新 `_live_src`；finalize 真正 destroy 条目框并同步清理 label_refs/meta_refs/src_labels。
- **② 滚动不跟随的深层根因**：跟随判断散落在"新条目到达时算 at_bottom"——但说话期间进行中条目**每 0.8s 增高**，视窗早已被顶离底部，等正式条目到来时 at_bottom() 恒 False，跟随永久断开。重构为 **follow 标志驱动**：滚轮上滚→follow=False、滚轮滚回底部→True、滚动条拖动→after_idle 按位置同步、条目/译文/partial 更新后 follow 态一律 stick_bottom（stick 内置 30ms 二次 moveto 兜底 Configure 晚到）、「↓ 最新」重置 True。上滚阅读从此稳定不被拽回。
- 测试：4 组（partial 占位语义/增长跟随/定稿无复述无残留/滚动 follow 三态）+ selfcheck 6 项 + pyright 零错误。
- 教训：**"何时判断状态"与"状态由谁驱动"是两回事**——贴底跟随这类连续 UI 状态应有一个明确的所有者（follow 标志 + 明确的置位/复位事件），而不是在每个消费者处重新推断。

### 2026-09-10：拆掉翻译顺序门（head-of-line blocking）——"持续翻译中"根因
- **诊断**（用户要求先查不改）：逐条核对当天 4 次会话共 154 条 jsonl——译文缺失 0、失败标记 0、`llm_raw` 与译文 63/63 完全一致（无思维链泄漏/空返回/格式错误）。**模型没有问题**；"持续翻译中"是我们自己的 `_drain` 顺序门：严格按 seq 上屏，长句（LLM 慢）作队头扣住后面已完成短句，语速快时段落密集 + 并发=2 排队叠加等待。日志实证：seq=18 先完成却等同秒才完成的 seq=17。
- **改造**：显示层完全乱序——partial 与最终译文均独立、先完成先上屏（每句原文先于本句 partial 入显示队列，乱序安全）；仅"上下文记忆 + 会话 JSONL"仍按提交顺序落盘（`_ctx_commit` 缓冲按 seq 连续刷出），翻译质量的上下文顺序与日志顺序不受影响。
- **验证**：端到端模拟（2s 长句 + 0.1/0.2s 短句）：短句 0.11/0.32s 上屏、长句 2.02s、上下文与 JSONL 仍按序、无残留"翻译中"；selfcheck 6 项通过。
- 测试教训：ad-hoc 测试里 item_id 由时间戳生成（`source_key-ts`），多条 SegmentEvent 忘了错开 ts 会导致 item_id 撞车 + 按 iid 归类句子的断言全部失真——构造用例时给每条独立 ts。

### 2026-09-10：item_id 撞车（拆分段落共享 ts）+ 可选中字幕与复制按钮
- **"持续翻译中"真凶找到**（用户再报，快语速场景）：`split_by_words`（词数断句）拆出的多段共用 `ts=time.time()`，而 `item_id = f"{source_key}-{ts:.3f}"` → 同一次转写拆出的第 2..N 段 item_id 全部相同 → UI `label_refs[item_id]` 字典互相顶掉 → 除最后一段外全部永久"… 翻译中"。与"语速快"强相关：说得越长越触发拆分。日志实证：seq=0/1（"i miss my cat…"/"misses me…"）同秒提交即第一批受害者。**修复：item_id 尾部追加 seq**（任何来源的 ts 重复都不再撞）。
- **字幕条目升级**：原文/译文由 Label 改为无边框 `tk.Text`——鼠标悬停 I 型光标、可拖选、Ctrl+C 复制；`<Key>` 拦截无修饰键输入（放行 Control/Alt，复制走类绑定）；`count('1.0','end-1c','displaylines')` + `<Configure>` 实现高度自适应；元信息行加"原文/译文"小复制按钮（点击入剪贴板、按钮 900ms 变"已复制"）；restyle/prune/finalize 全链路适配。
- 测试：同 ts 双段各自拿到译文、复制按钮剪贴板内容正确、I 型光标+编辑拦截、长句自动 6 行；selfcheck 6 项通过。

### 2026-09-10：历史记录按服务商隔离 + 本地模型（ollama）就绪检查
- **① 历史互通问题**：`api_history.yaml` 是全量单列，GUI 历史下拉不区分服务商——本地模型与各 API 混在一起。改：`load_history(provider)` 按服务商过滤；`_refresh_history` 只显示当前服务商的记录，且在 `_on_provider_change` 时联动刷新（显示格式去掉冗余的前缀，只留 打码key · 时间）。
- **② 本地模型"翻译失败"根因**：日志 11 条全部 `APIConnectionError`——**机器上根本没装 ollama**（无二进制、11434 拒连），之前 UI 只显示 `[翻译失败:APIConnectionError]` 没头绪。改三层：a) `translate.py` 新增 `ensure_local_backend()`：启动前探活 `/api/version`，服务没跑但有 ollama 二进制则自动后台拉起 `ollama serve`（脱会话）并等就绪 10s；没装则直接抛出带安装+pull 指引的可读错误；顺带检查模型是否已 pull（`/api/tags` 比对，缺失给 `ollama pull <model>` 指引）；b) `main.py` boot 链接线（`is_local_base_url` 判定 localhost 类 base_url）；c) 翻译失败兜底文案对连接类错误加"本地模型请确认 ollama 已运行"提示。
- **待用户动作**：要用本地翻译仍需安装 ollama 并 `ollama pull qwen3:4b`（应用现在会把这件事说清楚）。
- 测试：历史过滤互不混列、未装 ollama 明确报错、假 ollama 服务（本地 HTTP）探活通过 + 模型缺失指引；selfcheck 6 项通过。

### 2026-09-10：本地模型冷启动预热 + 停止时彻底中断翻译
- **"一直翻译中"真因**：ollama 已装好（v0.33.3、qwen3:4b 已 pull、服务被自动拉起），但**首次请求要把 2.5GB 模型载入内存，实测 TTFT 106.8s**——第一句"翻译中"挂满加载时长。修复：本地后端就绪后立即异步预热（预热线程发一次短请求），状态栏诚实显示"正在预热本地模型（首次约 1-2 分钟）"，预热完成日志提示并切回就绪状态；热请求恢复正常速度。
- **停止不彻底（用户问对了）**：日志实证 13:26:39"会话结束"后 13:27:24 仍在发新请求（seq=2）、进程挂而不退——`with ThreadPoolExecutor` 退出要等在跑请求跑完（本地模型可达分钟级），且 stop 瞬间取到的积压事件仍会被提交。修复：停止时 ①丢弃积压事件；②`translator.client.close()` 中断在跑请求；③`pool.shutdown(wait=False, cancel_futures=True)` 取消排队；④被中断的任务不写"翻译失败"污染会话日志。实测 worker 0.3s 内退出（旧实现需等 12s+）。
- **ollama serve 生命周期（有意保留）**：应用退出后 ollama 服务驻留——下次启动秒级就绪；其空闲 5 分钟自动卸载模型释放内存。这是服务化设计，不做随退随杀。
- 测试：停止 0.3s 退出 + 连接关闭中断长请求 + jsonl 无污染；selfcheck 6 项通过。
- 澄清（用户问）：配置里有"Ollama（本地）"（11434，ollama 守护进程）与"本地 Qwen3-4B（llama.cpp）"（8321，llama-server）两个服务商——**同一份 Qwen3-4B Q4_K_M 权重、两套推理引擎**。预热按选中服务商的服务地址探活，两者都生效。

### 2026-09-10：本地后端就绪检查引擎感知（ollama vs llama.cpp）
- **隐患**：`ensure_local_backend` 原本按 ollama 硬编码——选"本地 Qwen3-4B（llama.cpp）"且 8321 未运行时会误去启动 ollama serve，再报"ollama 启动超时"，驴唇不对马嘴。修复：按 base_url 端口判引擎（11434=ollama，其他=llama.cpp）；llama.cpp 分支用 OpenAI 风格 `/v1/models` 探活，未运行则尝试自动拉起 `llama-server -m models/<gguf> --port <port>`（gguf 相对路径解析 models/ 前缀），未安装 llama-server 时给出双选项指引（装 llama.cpp / 改用 Ollama）。
- 实测本机 llama-server 未安装——"本地 Qwen3-4B（llama.cpp）"当前选了会得到明确报错而非神秘失败；要用它需装 llama.cpp 或直接用 Ollama 条目。
- 测试：llama.cpp 未装明确指引（不误启 ollama）、假 llama-server 探活通过、ollama 分支回归；selfcheck 6 项通过。

### 2026-09-10：本地测速秒表 + 服务商标签精简 + 本地资源监控
- **① 测速"卡住"**：测速本就在后台线程，但本地模型冷加载（~107s）期间零反馈。改：测速按钮做**秒表**（"测速中… Ns" 每秒刷新，done 时取消）；本地服务商点测速先弹提示"首次需加载 1-2 分钟"；`_speed_test_bg` 里补上 `ensure_local_backend` 预检（与启动同款，服务没起先拉起）；模型选择在进线程前捕获（不再从后台线程读 tk 控件）。
- **② 服务商标签**：按用户要求精简为引擎名——"Ollama（本地）"→"Ollama"、"本地 Qwen3-4B（llama.cpp）"→"llama-server"；模型仍在模型下拉选择（本地服务商的"从 API 获取模型列表"无需 Key，服务在时自动拉真实清单）。
- **③ 本地资源监控**：服务商卡片新增常驻小标签（本地服务商时显示，云端自动隐藏），每 2s 采集：nvidia-smi 有则 GPU 显存/利用率/温度，无独显则 ollama `/api/ps` 汇报模型显存占用；CPU%（/proc/stat 差分）+ 内存%（/proc/meminfo）；服务不可达显示"⚠ 模型服务未运行"。采集在后台线程、`after` 回 UI（不在 tk 线程做阻塞 IO）。
- 测试：标签断言、CPU/内存采集与监控行组装、selfcheck 6 项通过。

### 2026-09-10：本地模型"翻译中"真因（qwen3 思维链）+ API 专属行按类型显隐
- **① 本地翻译 20~50s/句的真因**：驱动装好后 GPU 正常（71 tok/s），但 qwen3:4b 是思考模型——翻译一个 "the" 生成 **1435 个思维链 token**（~20s）。`think:false`/`/no_think` 全部无效：`ollama show --template` 显示该 blob 模板为旧版，**无条件输出 `<think>`**（无 `.Think` 条件分支），结构上无法关闭；空思考块自定义变体（qwen3:4b-nothink）也失败——量化版在空思考下继续在 content 里"出声推理"。
- **正解**：换官方非思考变体 `qwen3:4b-instruct`（tag 探测：`qwen3:4b-instruct-2507` 不存在，`qwen3:4b-instruct` 200）。实测：冷加载 2.1s、**热态 0.13~0.33s/句**（比思考版快约 100 倍），译文干净正确。config 默认模型已切，`qwen3:4b` 保留在下拉备选。教训：**本地小模型的"快"先看生成 token 数再看 tok/s**——71 tok/s 很健康也能慢到 20s/句，思维链是隐形大头。
- **② API Key/历史行按服务商类型显隐**：本地服务商隐藏两行（`grid_remove` 保留布局参数，切回 API 恢复），API 服务商正常显示。
- **③ GPU 显存"不变"澄清**：显存 used = 常驻模型 2.96GB + CUDA 上下文 + 桌面 ≈ 3.8GB，模型在显存里期间基本恒定（正常）；动态的是利用率（实测 34~38% 波动）与温度（56°C）——监控本身工作正常。
- 遗留：ollama 空闲 5 分钟自动卸载模型 → 下次请求重新冷加载（instruct 版仅 ~2s，可接受；要常驻可设 OLLAMA_KEEP_ALIVE=-1）。

### 2026-09-10：透明度修复 + 中文自动译英 + 总结/对话独立后端 + 对话页面
- **① 背景透明度逻辑错误**：`bg()` 原把背景色向黑色混合——调小只会"变黑"而非透明（与用户预期相反）。改为**窗口级 alpha**（`root.attributes("-alpha", v)`，0.25~1.0），背景色恒定墨蓝；滑块更名"不透明度（低=更透明）"；启动与 apply_style 均应用。
- **② 中文自动译英开关**（`translate.auto_zh_to_en`）：GUI「翻译」卡新增复选框；翻译时按输入文本脚本判定（`_is_chinese`：CJK 占比高且非英文句），中文入则 source/target 强制 中文→English，其余仍走「输出语言」。解决双向对话场景。
- **③ 删除 qwen3:4b（思考版）**：`ollama rm`（省 2.5GB）+ config 模型清单只留 `qwen3:4b-instruct`。
- **④ 会话总结可选后端**：`summarize.py` 新增 `--provider/--model`（本地后端自动 `ensure_local_backend`）；控制台「会话总结」页新增"总结/对话模型"卡（来源 API/本地 + 服务商 + 模型，交互同翻译后端页），持久化到新配置段 `assistant:{source_type,provider,model}`（config.py 新增 `AssistantConfig`）。实测本地模型总结 3 条记录成功。
- **⑤ 新增「对话」页面**：选一份会话 jsonl 作为上下文（最近 150 条、限 8000 字符，渲染为 [原文]/[译文]），针对翻译内容多轮提问；`LLMTranslator.chat(system, history)` 非流式接口（取最近 12 轮）；聊天记录区（只读 Text + 滚动、user/AI/系统 三色）、输入框 + 发送（Ctrl+Enter）、清空；后台线程调用、失败提示到界面；模型沿用 ④ 的选择并在页内显示当前模型。
- 测试：alpha 1.0→0.5、背景色恒定；双语开关开/关的提示词目标语言断言；`_is_chinese` 判定；对话接口实测 3s 内正确回答记录内容；总结 CLI 走本地模型端到端；控制台页签/选择器/上下文渲染/配置写回集成验证；selfcheck 6 项通过。

### 2026-09-10：移除窗口透明度选项 + 新增「字幕外挂」（悬浮字幕窗）
- **移除**：字幕样式面板的"不透明度"滑块、`STYLE_DEFAULTS.win_opacity`、`_apply_alpha()` 及 config.yaml 键（用户不要该功能）。文字不透明度（text_opacity）保留。
- **新增 `livetrans/overlay.py`（外挂模式）**：`python3 -m livetrans.overlay`，复用主程序同一条「音频 → SenseVoice → LLM 翻译」管线（`ASRWorker`/`TranslatorWorker` 原样复用），渲染层换成 `OverlayWindow`——实现与 `SubtitleWindow` 相同接口（post/update_translation/set_partial/set_level/set_status）即可插入。
  - 窗口：`overrideredirect` 无边框 + 默认置顶 + **不抢焦点**（从不 focus_*）；屏幕底部居中、宽 80%、距底 8%（字幕安全区）；白色无衬线居中自动换行；黑底 + 轻微阴影带。
  - **鼠标穿透与真圆角**：ctypes 直接调 libX11/libXext——`XShapeCombineRegion(ShapeInput)` 空区域实现穿透，`XPolygonRegion + ShapeBounding` 裁剪圆角（Tk 无逐像素透明），失败自动退化为矩形并提示。
  - 控制条：独立可拖动小窗（不穿透）：隐藏/显示、暂停/继续（联动 ASR pause_event）、‹上一条/›下一条（临时停止跟随）/最新（恢复跟随）、穿透开关、退出。
  - 历史缓冲（400 条）+ 跟随标志；翻译补全按 item_id 原地更新；窗高随内容自适应（长多行 188px ↔ 短句 129px）。
  - 配置段 `overlay:`（OverlayConfig）：置顶/穿透/显示原文/宽比/距底/字号/透明度/左右上下边距；控制台翻译页新增"字幕外挂"卡片（复选框 + 数值 + 启动/停止按钮，`_toggle_overlay` 独立进程并接入 watchdog）。
- 测试：几何（4480x1600 屏 -> 3584(80%)x99 @ 底部 128px）、穿透请求、双行渲染与高度自适应、译文补全、上下条/最新、显隐与暂停回调、控制条按钮齐全、真进程冒烟（SenseVoice 1.1s、双音源就绪、本地模型 TTFT 41ms 热态、会话日志落盘）、控制台配置读写闭环；selfcheck 6 项通过。

### 2026-09-10：外挂窗支持鼠标拖动/缩放 + 透明度改拖动条
- **鼠标拖动与缩放**：新增「位置」编辑模式（控制条按钮，进入时临时关闭穿透并显示虚线框 + 角标 + 光标提示；退出恢复穿透）——拖窗口本体移动（`fleur` 光标）、拖右下角改宽度（`bottom_right_corner` 光标，高度仍随内容自适应）；位置/宽度比例/透明度**写回 config.yaml 的 overlay 段**（新增 `pos_x/pos_y`，-1 = 底部居中自动），下次启动沿用。穿透态下鼠标不经过窗口，因此编辑必须先点「位置」——这是穿透与可拖动的必然取舍。
- **透明度改拖动条**：控制条新增实时透明度 Scale（松开时写回配置）；控制台外挂卡片的透明度由 Spinbox（上下箭头）换为 `tk.Scale` 拖动条（0.2~1.0，步进 0.05）。
- 测试：编辑模式元素、拖动位移与鼠标一致（+300,-100）、拖角宽度 +200、透明度滑块实时 alpha 0.6 并写回、退出恢复穿透、控制台 Scale 读写闭环；selfcheck 6 项通过。
### 2026-09-10：外挂背景/文字透明度分离 + galgame 式悬停控制面板
- **① 两根透明度条**：新增 `overlay.text_opacity`（0.3~1.0）。**背景透明度**走窗口级 `-alpha`（真半透明，能看到视频）；**文字透明度**用"颜色向底色混合"模拟（`_blend(白, 黑, a)`，1.0=纯白、0.5=#808080）——两者互不干扰（测试断言：改背景 alpha 时文字仍 #ffffff；改文字时背景 alpha 不变）。控制条与控制台卡片各两根滑块（亮色滑槽 #5b6785 + 数值显示）。
  - **技术说明（Tk/X11 限制）**：窗口 alpha 是整窗属性，无法逐元素透明；因此"背景透明"必然连带文字轻微变淡。要在透底时保持字亮，把「文字透明度」拉满并适当提高「背景透明度」（或降低边距让黑底更窄）。真正的逐像素 alpha 需要合成器 + ARGB 视觉，Tk 不支持。
- **② galgame 式控制面板**：面板改为**贴在字幕框顶部、与字幕等宽居中**（`reposition()`，字幕移动/缩放时跟随；顶部无空间则落到框内顶部）；**默认隐藏**，鼠标悬停字幕区域时出现、移开 0.7s 后自动消失（启动先亮 4 秒便于发现），可「固定」钉住。
  - **关键实现**：字幕窗默认鼠标穿透 → 收不到任何鼠标事件，`<Enter>/<Leave>` 绑定完全无效；改用**全局指针轮询**（`winfo_pointerxy()` 每 160ms + 矩形命中判定），穿透态下依然可靠。
  - 面板内容一行居中：隐藏/暂停/‹/›/最新/位置/穿透/固定/退出 + 背景·文字两根滑条 + 数值；移除面板自身的拖动绑定（此前"拖滑块带动整条"的根因是顶层绑定对子控件生效，现在面板跟随字幕，无需拖动）。
- 测试：背景/文字透明度相互独立（含 alpha 与颜色断言）、面板等宽贴顶（3584x44 @ 字幕上方 4px）、悬停显示/移开隐藏、固定钉住、双滑条持久化、控制台双滑块读写闭环；selfcheck 6 项通过。
- **外挂音频来源可选**：新增 `overlay.source`（internal / external / both，默认 both）——外挂只启动选定来源（实测 internal 只起系统音频、external 只起麦克风），避免内外音频混进同一条字幕流；控制台外挂卡片加"音频来源"下拉。

### 2026-09-10：GUI 工具包调研（Tk 逐元素透明限制的替代方案）
- **Tk 的硬限制**：`-alpha` 是整窗属性，Tk 没有逐像素/逐元素 alpha，也没有 `-transparentcolor`（Windows 专属），故"背景透明但文字不透明"在 Tk/X11 上无法真正实现（只能颜色混合模拟）。
- **实测本机可行性**（会话类型 X11，GTK 3.24，PySide6 6.11.1）：
  - **GTK3 + PyGObject：开箱可用（推荐）**。PoC 验证：`screen.get_rgba_visual()` → True（ARGB 视觉=逐像素 alpha）、cairo 画 `rgba(0,0,0,.55)` 圆角底 + `rgba(1,1,1,1)` 不透明文字、`gdk_window.input_shape_combine_region(cairo.Region(),0,0)` 置空输入区域实现穿透、`set_keep_above/set_accept_focus(False)/set_skip_taskbar_hint` 置顶与不抢焦点。API 齐备：`Gdk.Window.set_pass_through`（X11/Wayland 通用）、Pango 排版（自动换行/测量）。
  - **Qt6 (PySide6)：需补一个系统包**。PoC 失败于 `libxcb-cursor0` 缺失（Qt≥6.5 加载 xcb 插件必需）——`sudo apt install libxcb-cursor0` 后可用；能力对等：`WA_TranslucentBackground`（逐像素）+ `WindowTransparentForInput`（穿透）+ `WindowDoesNotAcceptFocus`（不抢焦点）+ `Qt.Tool`（不进任务栏）。
  - Wayland：GTK3/Qt 走 `wl_surface` 可透明；wlroots 系可用 layer-shell 协议做真正的"桌面层"字幕（gtk-layer-shell/QtLayerShell）。
  - Electron/webview（`transparent + setIgnoreMouseEvents`）亦可但体积过大；python-xlib + cairo 直写 ARGB 视觉可行但过于底层，不推荐。
- **结论/建议**：把外挂渲染层换成 **GTK3**（同一接口实现，管线（音频→ASR→翻译）不动，Tk 版保留为回退），即可获得真正独立的背景/文字透明度、抗锯齿圆角与更好的文字排版；依赖为零（系统自带 PyGObject）。待用户确认后实施。

### 2026-09-10：外挂全面切换到 GTK3（Tk 版删除）
- 用户试用最小版后确认满意 → **`livetrans/overlay.py` 整体重写为 GTK3**，`overlay_gtk.py` 演示文件删除，Tk 版代码清零（`grep tkinter` = 0）。
- 实现要点：`set_app_paintable` + `screen.get_rgba_visual()`（ARGB 视觉）实现**逐像素 alpha** → 背景 `rgba(0,0,0,bg)` 与文字 `rgba(1,1,1,text)` 完全独立（实测：改文字透明度时底色像素 34 不变，字芯 255→122；背景 0 时只见文字）；cairo 圆角描边式阴影（避免整块叠加导致实际不透明度偏高）；Pango 排版（自动换行 + 精确测高做窗高自适应）；穿透用 `input_shape_combine_region`（空区域写入 / **整窗矩形恢复**——PyGObject 不接受 None）+ `set_pass_through`（Wayland）。
- 完整功能平移：管线接口（post/update_translation/set_partial）、历史上下条与跟随、音频来源选择、编辑模式（拖动移动 / 拖角缩放，事件绑定在窗口）、位置/宽度/透明度持久化、galgame 式悬停面板（Gdk.Seat 指针轮询；贴字幕顶部等宽；隐藏/暂停/‹/›/最新/位置/穿透/固定/退出 + 背景·文字双滑条，CSS 主题）。
- 修掉的真 bug：`ControlPanel` 引用了不存在的 `self._start_until`（应为 `ovw._start_until`）、缺 `hide()` 方法、`input_shape_combine_region(None)` 报错（改传整窗区域）。另修：控制台「保存设置」会清掉外挂记住的 `pos_x/pos_y`（现在保留）。
- 测试：几何（2560x1600 屏 → 2048x142 底部居中）、管线三接口、双透明度独立性（像素级）、上下条/最新、拖动 +150/-70 与拖角 +180 及写回、穿透切换、面板等宽贴顶（48px 高、贴字幕上方 4px）、悬停显隐/固定、双滑条；真进程冒烟（SenseVoice 1.1s、双音源、本地模型 TTFT 2.3s 冷启动、会话日志）；selfcheck 6 项通过。

### 2026-09-10：穿透默认关 + 面板音频来源（运行时切换）+ 全局 Tooltip 体系
- **① 鼠标穿透默认关闭**（`OverlayConfig.click_through=False` + config/example 同步）：默认可直接拖动/操作字幕，需要不挡鼠标时再开（面板「穿透」按钮）。
- **② 外挂面板新增「音频来源」下拉**（两者/内部/外部）：`run_overlay` 重构为**可运行时启停**——每路采集与 ASR worker 各自持有停止事件，`set_source()` 按选择停掉不需要的、起需要的，无需重启外挂，并写回配置。实测 both→internal→external 切换链路：启用/停用日志与配置记忆全部正确。
- **③ 全局悬停提示（Tooltip）体系**：控制台 `self.tip` 全量补齐（24 → 52 处，覆盖每个可选控件、按钮、数值框、滑条，含此前遗漏的「置顶/穿透/显示原文/字号/透明度/边距/音频来源/模式/模型来源/操作栏」等）；外挂 GTK 面板用 `set_tooltip_text`（9 处）；字幕窗（Tk）新增轻量 `_Tooltip` 类（450ms 悬停弹出、移开/点击消失）覆盖布局切换、最新、置顶、样式、样式面板滑条与颜色按钮、两路暂停按钮。**同时删除内联说明小字**：本地后端的「本地后端 · 无需 Key」徽标改为空（仅云端显示真实 Key 状态）、外挂边距的「左右/上下（px）」、ASR 的「长句超词数自动拆条…」、对话页的「Ctrl+Enter」等一律移入对应控件的提示文本；纯单位标签（句/路）与真实状态文本（当前模型、术语表文件名）保留。
- 测试：控制台 20 个新控件引用完整、本地后端徽标为空而云端非空、字幕窗样式面板 + 悬停弹出提示、外挂默认不穿透、面板下拉切换回调、面板 9 个提示齐全；真进程冒烟（穿透=关）；selfcheck 6 项通过。
- **提示文案风格统一（用户反馈"外挂卡片提示太繁琐"）**：卡片级/长提示一律"只讲用途、不讲实现"。外挂卡片由 5 行方法清单（提到 GTK3 渲染、逐像素透明、抗锯齿、宽 80%/距底 8%）精简为 3 行介绍（"叠加一条悬浮字幕…鼠标移上去浮出控制面板…可调字号/透明度/位置/音频来源"）；同步去掉各提示里的实现细节词（PulseAudio/PipeWire monitor、cairo/GTK3、sessions/*.jsonl、.summary.md、keys.env 权限 600/source、TTFT 缩写），共 54 条提示经校验零实现细节泄露、平均 41 字；`tip()` 顺带把文本存到控件 `_tip_text` 便于自检。**「启动外挂」按钮提示**同步精简为"启动悬浮字幕；可与主程序同时使用，也可以单独用它 / 再点一次即停止"，并修掉一个显示 bug——原文里的 `**独立进程**` 是 Markdown 写法，Tk 气泡会原样显示星号（已全项目排查，无其他残留）；启动后的状态提示也改为只讲"在哪看、怎么停"。

- **用户困惑澄清 + 修复**："没点「保存并启动」，外挂也会出现翻译内容"——**外挂是独立进程**，自带完整管线（采集→识别→翻译→显示），与主程序互不依赖（两者共用同一份 config.yaml：翻译后端/模型/语言/模式等）。真正的隐患是**静默运行**：外挂被「隐藏」后，面板的悬停判定依赖字幕矩形，字幕一藏面板就再也出不来了，进程却仍在识别/翻译。修复：① 字幕隐藏时**面板常驻**（用 `_last_geom` 记住原位置继续贴在那里），点「显示」可恢复、点「退出」可停止；② 控制台启动外挂前用 `pgrep -f livetrans.overlay` **检测并清理残留实例**（控制台重启或手动启动后失去追踪的旧进程），避免重复实例静默消耗 API；③ 启动提示与按钮 tooltip 明确写出"独立运行，与「保存并启动」互不依赖 / 停止方式"。测试：隐藏→面板常驻且按钮变「显示」、恢复后悬停逻辑照旧、残留实例（真实进程）被自动清理；selfcheck 6 项通过。

- **用户反馈修复**：① **"点了穿透后拖不动字幕"**——原实现要求先进「位置」编辑模式才能拖；改为**未穿透（能收到鼠标事件）即可直接拖动/拖右下角缩放**，编辑模式只保留"高亮边框 + 临时关闭穿透"的辅助作用；右下角缩放标在未穿透时常显（半透明，编辑模式转实色）。② **开关按钮状态难分辨**——面板五个开关按钮统一加"文字状态 + 开启高亮"：`穿透 开/关`（原来文字不变）、暂停↔继续、位置↔完成、固定↔自动、隐藏↔显示，开启态加 CSS `.on` 类（青色底 + 深色粗体字），关闭态恢复暗色按钮；`ControlPanel.refresh()` 初始化与每次切换都同步两者。

- **修复（用户反馈）**：① 滑块"看不清"——`troughcolor` 由 #2f394f（与面板底色 #1c2230 亮度比 1.5x）改为 **#5b6785（3.04x）**、槽宽 14px、`sliderrelief="raised"`，并加数值标签（控制台与外挂控制条同款）。② **拖透明度滑块时整个控制条跟着跑**：Tk 的 bindtags 中**顶层窗口绑定对子控件同样生效**，原先绑在 `self.win` 上的拖动逻辑被滑块事件触发；修法是在 `_drag_start` 里对 `e.widget` 做 `isinstance(Scale/Button)` 放行 + `self._dragging` 门闩（拖空白处仍可移动，拖滑块/按钮只走自身交互）。测试：拖滑块/按钮时控制条坐标不变、拖空白处正常位移、滑槽亮度对比 3.04x。
- 教训：长耗时外部服务的**启动成本**（冷加载）和**停止成本**（在跑请求）都要在管线里显式管理——前者用预热摊销，后者用连接关闭硬中断，"等线程自然结束"对分钟级请求等于不停。

### 2026-09-09（二十一）：测速"请先 export"根因修复——测速前自动保存（用户实测反馈）
- **用户报"点击测速显示需要环境变量请先 export"**。排查：`keys.env` 不存在——用户在 GUI 填了 key 但没点保存，key 只在内存；测速后台线程只从 keys.env/环境变量取 → `LLMTranslator` 拿不到 → 抛"请先 export"。
- **修复**：`_speed_test` 先 `_apply_and_save()`（与「保存并启动」同款行为：keys.env + config.yaml 落盘 + 记历史），失败即中止；`_speed_test_bg` 用 load_config 新鲜配置后按 providers 再注入一次 env（GUI 填写的 Key 此刻已在文件）；顺带给测速结果/异常退出弹窗包 TclError 防护（关窗时机竞态）。
- 测试：复现用户场景（GUI 填 key 未保存 → 点测速）——自动落盘 keys.env + 记历史 + 测速成功 + 无线程异常；selfcheck 6 项；pyright 零错误。
- 教训：**依赖"先保存再用"的隐式时序的按钮（测速/总结），必须自己保证保存先发生**——上一轮只修了 providers 类型 bug，没堵住"未保存"这条路，同类路径「生成总结」同样依赖 keys.env（已由 _apply_and_save 统一覆盖的仅有启动/测速，总结走 merged_env 读文件，若用户未保存同样会失败——待下次反馈一并处理）。

### 2026-09-09（十七）：参考 LiveTranslate 做实时化改造——SenseVoice 引擎 + LLM 流式翻译 + 低延迟断句（用户反馈"完全做不到实时"）
- **调研**（web_fetch GitHub API + raw README）：LiveTranslate（TheDeathDragon，634★，MIT）= Windows/WASAPI 32ms 块 + **Silero VAD** + **SenseVoice/FunASR 默认引擎** + **LLM 流式翻译逐字显示** + GPU/远程 ASR。四个可移植方法逐一落地前三个（WASAPI/GPU 属平台差异）。
- **① SenseVoice 引擎**（`asr_sensevoice.py`）：funasr_onnx（纯 onnxruntime 免 torch）；模型取 HF `haixuantao/SenseVoiceSmall-onnx`（调研后选定：ModelScope 官方 repo 根目录无 onnx、转导需 torch；该仓库含 model_quant.onnx 231MB + config/am.mvn/bpe.model，与 funasr_onnx 期望布局完全匹配），经 hf-mirror 下载到 `models/sensevoice/`；`preload_model('sensevoice')` 分流；标签 `<|en|><|NEUTRAL|>` 剥离；fr/de/ru/es 等不支持语言自动回退 auto。**真实对比（同一段 11s JFK 英文语音，本机 CPU）**：SenseVoice 0.15s（RTF 0.01）vs Whisper small 1.07s（RTF 0.10）→ **快 7.3 倍**，识别内容一致完整。真实下载 231MB 用时约 2 分钟。
- **② LLM 流式翻译**（translate.py `translate_stream`）：OpenAI 兼容 stream=True，增量 80ms 节流回调 → 字幕窗原位逐字推进（`update_translation` 复用）；translate() 改为流式包装保持兼容；_messages 提取去重。**感知延迟：首个译文像素 ≈ 1s（原 3-5s），全文边生成边显示**。
- **③ 低延迟断句/捕获**：silence_ms 700→550、min_speech_ms 250→200（config 默认+用户配置迁移）；parec 读块 100ms→25ms（800B）、PortAudio block 100→40ms——VAD 起止判定更跟手。
- **配置/GUI**：`asr.engine: whisper|sensevoice`；控制台 ASR 卡片新增"引擎"下拉（切换联动：SenseVoice 时档位禁用；下载按钮按引擎分流并随就绪状态显示 ✓）；requirements.txt 加 funasr_onnx。
- 测试：mock 5 项（流式回调/Worker 增量/SenseVoice 接口/分流/低延迟配置）+ GUI 3 项（引擎回显/联动/持久化）+ 真实链路（231MB 下载、双引擎同音频对比）+ selfcheck 5 项；pyright 零错误。
- 端到端延迟重估（11s 句子）：旧 = 700ms 断句 + 1.1s Whisper + 等完整译文 2-3s ≈ 4-5s；新 = 550ms + 0.15s SenseVoice + 原文上屏 + 译文首字 ~1s 流式 ≈ **首字 ~1.7s，全文边生成边看**。Silero VAD 与 GPU/远程 ASR 留 M3。

### 2026-09-10：工程化整理（资产清理 + 控制台拆分 + 依赖声明 + 文档）
- **清理遗留资产**：删除已废弃 faster-whisper 的模型残留 `models/tiny`(75MB) + `models/small`(464MB)（全项目零引用）；`models/` 由 3.1G 降到 2.6G。
  - 澄清：**Ollama 的模型不在项目里**——由系统服务管理，位于 `/usr/share/ollama/.ollama/models`（systemd 用户 `ollama`，2.4G）；项目内 `models/qwen3-4b/*.gguf`(2.4G) 是给 llama.cpp/llama-server 路径用的**另一份拷贝**（该路径需自行安装 llama-server），已在待办中标记。
- **控制台拆分（2375 → 488 行）**：按"数据层 / UI 基础设施 / 页面"三层拆出模块——
  `livetrans/providers.py`（服务商数据层）、`paths.py`、`langs.py`、`sysmon.py`（资源监控）、`deps.py`（依赖自检）、
  `ui/theme.py`（配色/字体/ttk 样式 + 自绘指示器）、`ui/widgets.py`（悬停提示/状态气泡）、
  `ui/page_backend.py`、`ui/key_ui.py`、`ui/page_audio.py`、`ui/page_sessions.py`、`ui/page_chat.py`、`ui/overlay_card.py`；
  `Launcher` 改为 mixin 组合，只保留窗口骨架、页面装配、配置读写与启动控制。
  - 拆分方式：AST 精准搬移（保源码原样）+ 自写"未定义名"静态检查脚本兜底（发现并修掉 4 处漏迁/漏导入：`math`、`tkfont`、`CHAT_SYSTEM`、`_IND_IMGS`）。
- **依赖声明补全**：`requirements.txt` 增补系统包说明与一键 apt 命令；新增 `livetrans/deps.py`（`missing_pip_deps()` / `missing_overlay_deps()`），控制台点「启动外挂」前自检，缺组件时弹窗给出 apt 修复命令而不是抛堆栈。
- **文档重构**：README 全面重写（功能/安装含系统依赖/快速开始/两种字幕形态对比/配置表/模块地图/排障/开发）；
  TECH_ROADMAP 增加目录、以"功能现状表 + 架构数据流 + 选型决策 + 里程碑完成情况 + 模块地图 + 环境依赖 + 待办清单"重写前六节，历史日志移入「附录：开发日志」。
- **验证**：全量 `py_compile`、静态未定义名检查 0 项、控制台实例化（4 页签/54 条提示/关键控件与方法齐全/配置写回）、外挂进程冒烟、selfcheck 6 项通过。

### 2026-09-10：移除 llama.cpp / llama-server 路线（含 2.4GB GGUF）
- **删除资产**：`models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf`（2.4GB）——该模型仅供 llama.cpp 路径使用，且与 Ollama 自管的模型副本（`/usr/share/ollama/.ollama/models`，2.4G）完全重复。`models/` 现仅剩 `sensevoice/`（231MB）。
- **删除代码**：`translate.py` 去掉 llama.cpp 分支——`_resolve_gguf()`、`_probe_openai_models()` 与 `ensure_local_backend()` 中"按端口判断引擎 + 自动拉起 llama-server + 15s 超时"整段逻辑，函数收敛为"确保 Ollama 可用"（探活 `/api/version` → 必要时自动 `ollama serve` → `/api/tags` 校验模型是否已 pull）。
- **删除配置**：`config.yaml` 去掉 `providers.llamacpp` 段（服务商列表由 10 家变 9 家）；`requirements.txt`、README、控制台提示（"本机模型服务（Ollama / llama-server）"）同步更新。
- 验证：全量编译 + 静态未定义名 0 + selfcheck 6 项 + 控制台实例化（服务商列表无 llamacpp、模型下拉正常）+ 本地模型真实翻译冒烟。

### 2026-09-10：SRT/TXT 导出 + 主字幕窗同传 UI 对齐
- **新增 `livetrans/export.py`**（零新依赖）：会话 JSONL -> SRT 双语字幕 / TXT。时间轴推算规则：每条记录视为一段语音区间 `[ts - (asr+llm 耗时), ts]`，整体平移到首句起点 = offset，并强制单调不重叠（后句顺延），播放器不跳字；双语模式译文失败的那句保留原文（只丢译文），转录稿不缺句；纯文本模式用 `原文 → 译文` 单行版式。
- **导出入口三处**：主字幕窗顶栏「导出 ▾」菜单（双语/仅译文/仅原文/纯文本，后台线程 + 状态栏回执）、控制台「会话总结」页（导出内容 + 声道过滤下拉 + SRT/TXT 按钮）、CLI `python3 -m livetrans.export <jsonl|dir> [--mode] [--channel] [--txt] [--all]`。
- **主字幕窗 UI 对齐夸克同传**：① 新增**对话模式**布局（左=你·麦克风、右=对方·系统声音，右栏整段右对齐——Tk 的 Text 无 `-justify` 选项，改用 `tag_configure(justify=...)` 实现）；② **活跃栏高亮**（最近说话侧标题满色、另一侧淡至 42%，仅对话模式）；③ **partial 脉冲光标**（未出译文的条目显示 `… 翻译中▌` 交替闪烁，出译文即停并淡入新文字）；④ **入场动效**（新条目文字色由背景色向目标色 4 帧淡入约 200ms，Tk 无逐控件 alpha 故用颜色混合模拟）；⑤ 样式面板新增「动效」开关（可关，省 CPU）。
- **字幕窗接线**：`set_session_path()`（导出数据源，main 在建会话日志后注入）、`set_lang_labels()`（各栏标题显示「自动 → 中文」）。
- 验证：全量编译 + 静态未定义名 0 + selfcheck **7 项**（新增导出项：时间戳格式/首条对齐 0/单调不重叠/双语行序/坏行跳过/失败译文丢弃）+ 字幕窗实测（淡入色值、脉冲交替、右对齐 tag、活跃高亮、导出落地 2KB/23 条、空文件与坏文件兜底提示）+ 主程序冒烟（boot 全流程无异常，会话文件正常创建）。

### 2026-09-10：声纹角色标注（说话人区分，sherpa-onnx + 在线聚类）
- **选型**：`sherpa-onnx` 1.13.7（PyPI 直连可装，约 20MB）+ CAM++ 中英双语说话人向量模型 `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`（28MB，192 维），走 hf-mirror 下载（26 秒落盘 `models/speaker/`）。
- **新增 `livetrans/speaker.py`**：`SpeakerTracker`（懒加载 + 模型后台预热；`assign(seg) -> "S1"/"S2"…`；两个 ASR 线程共享，内部加锁）。判定用官方 `SpeakerEmbeddingExtractor` + `SpeakerEmbeddingManager`：**向量必须 L2 归一**（踩坑：不归一相似度虚高到 0.92，会把异人判成同人）；满员后归入最相似者；段长 < 0.6s 不判定；失败永久降级（不影响识别/翻译）。
- **真人语音实测**（用 piper TTS 合成两个说话人，临时目录安装不污染环境）：同人跨句余弦 **0.717**、异人 **0.282/0.133** → 阈值 0.55 居中余量最大；交错顺序 Amy→Ryan→Amy 判定 **S1→S2→S1** 正确回到同一人。
- **延迟开销**：热态 单句识别 43ms + 声纹 25ms = **63ms/句**（LLM 翻译 ~300ms 仍占大头，端到端目标 1.5s 内无压力）。
- **管线接入**：`SegmentEvent.speaker` + `ASRWorker(speaker=…)`（VAD 切出的整段直接抽向量，无需额外切分/滑窗）→ `DisplayItem.speaker` → 会话 JSONL 新增 `speaker` 字段；主程序与外挂各自 `build_tracker(cfg.speaker)` 并 `preload()`。
- **渲染**：主字幕窗在元信息行显示**说话人色块**（仅换人时出现，同一人连续说话不重复标注；色块不污染可复制的正文）；外挂 GTK3 在译文前加 `[S1] ` 前缀；8 色高对比调色板（`color_for`）。
- **控制台**：「音频源」页新增声纹卡片（启用开关 / 判定阈值滑块 0.35~0.80 / 最多人数 2~8 / 就绪状态行：缺 sherpa-onnx 或缺模型时给出修复指引），随「保存并启动」落盘，重启生效。
- 验证：编译 + 静态未定义名 0 + selfcheck **8 项**（新增声纹项：配色映射/未启用零开销/短段跳过 + 真模型同段同标签/reset；无 sherpa-onnx 时自动 SKIP）+ 真实语音端到端（灌入 ASR 管线 → S1/S2/S1）+ 字幕窗外挂双渲染实测 + 控制台卡片保存/重载 + 主程序/外挂冒烟（启动即「声纹就绪」，实际系统音频抓到 S1）。

### 2026-09-10：对话模式可调（每侧音频来源 + 每侧翻译方向）
- **问题**：对话模式此前写死「左=麦克风、右=系统声音」与「自动→中文」，既不能换边（开会时系统声音该在左）也不能双向（自己说中文该译成英文、对方说英文该译成中文）。
- **新增 `dialog:` 配置段**（`config.DialogConfig`）：`left_source`（左栏放哪一路，另一路自动去右栏）、`left/right_src_lang`、`left/right_dst_lang`、`bidirectional`。
- **每侧独立翻译器**：`main._direction_translator()` 用 `dataclasses.replace` 复制翻译配置（方向不同才新建，独立上下文/术语表、共用后端与凭证）；`_same_direction()` 判别「与「翻译」页设置一致则复用主翻译器」（源语言「自动」视为通配）。`TranslatorWorker` 新增 `translators: kind -> transformer` 映射 + `pick(kind)` + `set_translators()`（切换时关闭不再使用的连接），日志新增 `kind=` 便于排查。
- **字幕窗「对话设置」面板**（顶栏新按钮）：左栏音频来源（麦克风/系统声音）、左右两侧各自的 源语言/译成、双向同传开关；改动**即时生效**（重排两侧 + 重建该侧翻译器）并落盘 `config.yaml`，无需重启。
- **目标语言新增「自动（中↔英）」**：中文入→英文出、其余入→中文出（`langs.dialog_direction()`），不确定双方说什么语言时最省心。
- 验证：真模型实测同一句英文——右栏（自动→中文）得「能否请您分享一下屏幕吗？」，左栏（自动→English）得英文原样；目标「自动（中↔英）」实测中文入→英文、英文入→中文；映射复用（同方向→复用主翻译器，双向→仅左侧新建）；`TranslatorWorker.pick/set_translators` 分路与旧连接关闭；面板 5 下拉 + 1 开关全部可交互并触发回调；落盘回环（其余配置段保留）；真机 `--config` 冒烟日志出现两行方向；编译 + 静态未定义名 0 + selfcheck **9 项**。

### 2026-09-10：对话设置面板修正（下拉显示中文标签 + 两侧来源都可选 + 方向改为跟音频走）
- **起因**：用户反馈两点——①「音频来源」只有左栏能选；②下拉里显示的是 `external` / `internal`，看不懂。
- **Bug 根因**：面板用 `list(DIALOG_AUDIO_LABELS)` 取值，拿到的是 **dict 的 keys**（external/internal）而不是中文标签；且选中项经 `DIALOG_AUDIO_VALUES.get("internal")` 反查失败 → 选「系统声音」实际被回退成麦克风（静默失效）。
- **语义修正**：翻译方向从「按左/右栏存」改为「**按音频路存**」。旧模型下一换边就把「系统声音→中文」变成「系统声音→English」，直接空翻；新模型 `external_src/dst_lang`、`internal_src/dst_lang` + `left_source`（只管位置），换边仅改布局。`langs.migrate_dialog()` 做旧键就近迁移（`left_*` → `left_source` 指向的那一路），`config.load_config` / `main._load_dialog` 都已接入。
- **面板重做**（`subtitle._open_dialog_panel`）：
  - 「显示位置 · 左栏放」下拉（中文标签：麦克风（自己的声音）/ 系统声音（电脑播放）），另一路自动去右栏；
  - 「麦克风（自己的声音）」与「系统声音（电脑播放）」两段各自独立 源语言/译成 —— **两路都能改，不再只有左栏**；
  - 底部摘要行实时显示「麦克风：English（显示在左栏）／系统声音：中文（显示在右栏）」，每项都带悬停说明；
  - 改动即时生效（重排 + 重建该路翻译器）并落盘。
- **附带清理**：上一次编辑在 `subtitle.py` 里留下了一段旧的同面板实现残留（112 行，后创建的一批控件覆盖了新面板，导致界面表现仍是旧版）——已删除，`_open_dialog_panel` 现在只有一份实现。
- 验证：面板 5 个下拉均为中文值、换边后两路译法不变、改麦克风方向时无论它在哪一栏都跟着变、摘要行正确；旧配置（`left_dst_lang` 等）迁移到 `internal_dst_lang` 等；真机日志 `对话模式 麦克风（自己的声音）（左栏）：自动 → English`；编译 + 静态未定义名 0 + selfcheck 9 项。

### 2026-09-10：对话模式改为「角色制」+ 移除外挂卡片说明
- **角色制重构**（用户澄清：重点不是左右栏放谁，而是「你」和「对方」**各自**能选音频来源）：`dialog` 段改为
  `left_role`（左栏显示谁）+ `self_source`（「你」用哪一路）+ `self/other_src_lang/dst_lang`。
  面板里两个角色**各自**都有「音频来源」下拉（麦克风（自己的声音）/ 系统声音（电脑播放）），
  另有「左栏显示」控制位置；选了一路后另一路自动归另一个角色（两角色各占一路音频）。
- **语义再次校正**：翻译方向跟着**角色**走 —— 把「你」的来源从麦克风换成系统声音，你的「译成 English」不变，
  只换音频输入；`main._build_dialogue_translators` 按角色拥有的那一路建翻译器并路由（真机验证：
  你=系统声音时 `internal` 走 English 方向、`external` 走中文方向）。
- **旧格式迁移链**（`langs.migrate_dialog`）：v1（方向按 left_*/right_* 存）→ v2（按音频路 external_*/internal_*）→ v3（角色制）。
  踩坑记录：v1→v2 那一步必须直接用 v1 的 `left_source` 推左右对应哪一路，不能调 `dialog_kinds()`（它按 v3 字段解析，读不到 left_source）。
- **面板文案**：栏标题改为「角色 · 音频」（如 `你（自己） · 系统声音 · 自动 → English`）；底部摘要行显示
  「你：麦克风 → English（左栏）／对方：系统声音 → 中文（右栏）」。
- **移除字幕外挂卡片级说明**：悬停整张「字幕外挂」卡片会弹出的一大段介绍文字已删除（各控件自身的提示保留）。
- 验证：面板 7 个下拉（左栏显示 + 两角色各 3 项）均为中文值；改「你/对方」来源时两角色来源互换而方向不变；
  只改「左栏显示」时位置互换而方向不变；旧 v1/v2 配置迁移正确；真机日志 `对话模式 你（自己）·麦克风（左栏）：自动 → English`；
  编译 + 静态未定义名 0 + selfcheck 9 项。

### 2026-09-10：对话模式「角色制」二次校正——来源独立、同源各翻一遍、按角色暂停
- **用户澄清**：重点不是"两个角色各占一路"，而是**每个角色自己挑输入源**（「你」可以用麦克风说一段、再切系统声音放一段，**上下文仍按「你」连续**）；线下两人共用麦克风时靠**暂停键**轮流。
- **数据模型（v4）**：`self_source` 与 `other_source` **相互独立**（可相同）；`left_role` 只管显示位置；方向 `self/other_src_lang/dst_lang` 与 `bidirectional` 不变。`langs.migrate_dialog` 现在是 v1→v2→v3→v4 四级链（v3→v4 补 `other_source` = 另一路，保持旧行为）。
- **路由按角色**：新增 `main.DialogRouter`（音频路 → 在用它的角色 → 该角色翻译器 / 是否暂停），`TranslatorWorker` 支持 router：
  - **同一路音频有两个角色在用** → 这一句**产出两条字幕**（`item_id` 加角色后缀），各自用自己角色的翻译器与上下文；
  - 某角色暂停 → 跳过它那条；某路音频没有角色在用 → 整句丢弃（不翻不上屏）；
  - 字幕窗 `_panes` 改为按**角色**组织（self/other），每栏自带 `source_kind`，投递按"哪些角色在用这一路"分发（同源时两栏都要）；
  - **暂停按角色**（`window.paused_roles`）：只停该角色的翻译与上屏，音频照常供另一角色；某一路已无未暂停角色时才真正停采集（省 CPU）。
- **换来源不重建翻译器**：`_on_dialog_change` 先比对方向指纹 `_direction_sig()`，只有方向变了才重建；换输入来源/换位置只更新 UI 与路由 → 该角色上下文不中断（用户的核心诉求）。
- **修掉一个真 bug**：同段两角色原先共用同一个 `seq`，`_ctx_pending[seq]` 槽位互相覆盖 → 只落一条日志、其中一角色的上下文丢失。现在序号按"产出的每一条"分配（日志实测两条，role=self/other 各一）。
- 验证：`/tmp/test_router.py`（同源两角色各翻一条 / 按角色暂停跳过 / 无人使用丢弃 / 上下文按角色隔离 / 换来源仍用同一翻译器）；真窗口+真路由+本地模型端到端（同一句英文 → 「你」栏英文、「对方」栏中文，日志两条分角色）；面板 7 下拉可把两个角色都设为麦克风；聚焦布局兜底；编译 + 静态未定义名 0 + selfcheck 9 项。

### 2026-09-11：本地模型显存管理（取消挂载）+ 换模型旧模型不退场的实测与修复
- **实测结论（用户问："换本地模型时旧的会不会停"）**：**不会**。给 ollama 先载入 `qwen3:4b-instruct`（3.18GB），再请求 `qwen2.5:0.5b` → `/api/ps` 显示**两张模型同时驻留**（3.66GB，显存 3648→4439MiB），旧模型要等它自己的 keep_alive 到期（默认 5 分钟）才释放。卸载手段有效：`POST /api/generate {"model": x, "keep_alive": 0}` → 立刻回收（4439→1270MiB）。
- **新增 `sysmon` 三个函数**：`ollama_root(base_url)`（从 OpenAI 兼容 base_url 推原生根）、`ollama_loaded()`（/api/ps）、`ollama_unload(names=None)`（keep_alive=0 卸载，names 空 = 全卸；服务不可达返回 False 不抛异常）。
- **新增 `local:` 配置段 + 控制台「本地模型显存（Ollama）」卡片**：
  - 状态行实时显示常驻了哪些模型、各占多少显存、何时空闲回收；
  - **「释放显存（取消挂载）」按钮**：点一下把常驻模型全部卸下（实测提示"已卸载 qwen2.5:0.5b，释放显存约 0.4GB ✓"）；
  - ☐ **空闲自动卸载**（`auto_unload_min`，默认 **0=关闭**，按用户要求默认手动）+ 分钟数；
  - ☑ **启动时清理其它已加载模型**（`unload_others_on_start`，默认开）：只卸"你没在用的"（换模型后残留的旧模型），当前模型不受影响；
  - ☐ **停止字幕时卸载当前模型**（`unload_on_exit`，默认关）。
- **主程序/外挂接线**：启动时（本地后端）按开关清理其它模型并打印回收量；`TranslatorWorker.last_active` 供空闲看门狗判断；退出时可选择卸载。
- 真机验证：① 预置两张常驻后启动 → 日志 `释放显存（启动时清理其它模型）: 已卸载 qwen2.5:0.5b，回收约 0.4GB`，启动后仅剩在用模型；② `unload_on_exit=true` 停止字幕 → `释放显存（退出时释放）: 已卸载 qwen3:4b-instruct，回收约 3.0GB`，/api/ps 清空、显存回到 800MiB；③ `auto_unload_min=1` → 空闲 60 秒后自动 `释放显存（空闲 1 分钟）`；④ 控制台卡片状态/按钮/三项设置持久化。
- 编译 + 静态未定义名 0 + selfcheck **10 项**（新增 local 项：地址解析/卸载容错/默认手动）。

## 9. 风险与备选

| 风险 | 缓解 |
|------|------|
| whisper CPU 转写慢 | 模型降档 small→base/tiny；或 GPU；M3 换 sherpa-onnx 流式 |
| LLM API 句级延迟高 | 低延迟后端（deepseek-chat/gemini-flash）；M3 引入流式输出与 partial 字幕；极端场景切 Azure Speech |
| Linux 按 App 拆流复杂 | M2 用 null-sink + loopback 路由方案；或直接上 PipeWire link 管理 |
| 多路同说时共享模型锁竞争 | 句级队列天然削峰；必要时每路独立模型实例或换流式引擎 |
| 国内下载 HuggingFace 模型慢 | `export HF_ENDPOINT=https://hf-mirror.com` |
