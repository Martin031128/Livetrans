# LiveTrans → Windows 移植 · 交接文档

> **怎么用这份文档**
> 1. 把【第一部分】整段复制给协助你的 AI（它不认识这个项目，所以提示词自带背景与纪律）；
> 2. 把【第二部分】也发过去（技术底账，AI 需要按它开工）；
> 3. 【第三部分】是坑清单与验收清单 —— 你自己留着对照，防止 AI"看起来做完了"。
>
> 本文件是开发交接材料，不随安装包发布。

---

# 第一部分：直接复制给 AI 的提示词

```
# 任务：把 LiveTrans 从 Linux 移植到 Windows，并让 Windows 成为可长期开发的环境

我是这个项目的作者。项目现在在 Linux 上开发完成并已发布，请帮我做 Windows 版本。
仓库：<填你的仓库地址，或说明代码已在本机某目录>

## 第一步：先读代码，再给方案，不要立刻改代码
请先读这些文件，理解现状后**先给我一份移植方案**（改哪些文件、每步怎么验证、有什么风险），
我确认后你再动手：
- README.md                    项目功能与用法
- TECH_ROADMAP.md              架构决策与逐次改动日志（信息量最大，务必读）
- prototype/requirements.txt   依赖清单（含系统依赖说明）
- prototype/livetrans/capture.py    音频捕获（Linux 用 parec 抓系统声音）
- prototype/livetrans/overlay.py    字幕外挂窗（GTK3，1111 行，平台耦合最重）
- prototype/livetrans/sysmon.py     资源监控（读 /proc）
- prototype/livetrans/paths.py      数据目录（XDG）
- prototype/livetrans/deps.py       依赖自检（apt 提示）
- prototype/tests/run.sh            测试入口

## 项目是什么
Linux 桌面**实时语音翻译字幕**工具：
「麦克风」+「系统播放声音」两路音频 → webrtcvad 切句 → SenseVoice ONNX 本地识别
→ 云端/本地 LLM 翻译 → 悬浮双语字幕窗。
- 主字幕窗 = Tkinter（Linux/Windows 都能跑）
- 字幕外挂 = GTK3 逐像素透明悬浮窗（平台耦合最重，Windows 需重做）
- 控制台 = Tkinter 多页（音频源 / 翻译后端 / 会话总结 / 对话设置）
- 另有：声纹角色标注（sherpa-onnx）、镜像模式（跟随另一实例的会话日志）、会话导出（SRT/TXT）、
  弱网降级（云端失败切本地模型）、API key 本地管理

## 硬性要求（请严格遵守）
1. **不要重写业务逻辑**。约 4000 行（识别/翻译/声纹/字幕窗/配置/导出）是跨平台的，只在必要时改动。
2. **不要破坏 Linux 版**。Windows 支持要靠「平台抽象层 + sys.platform 分支」实现，
   不要把 `if sys.platform == "win32"` 撒进业务逻辑。Linux 是现有 CI 与主开发环境，必须继续可跑可测。
3. **不要一次性倾倒大量改动**。按里程碑推进，每个里程碑结束必须能运行、能跑测试，
   并告诉我：① 改了哪些文件 ② 为什么这么改 ③ 我该怎么验证 ④ 哪些是平台限制。
4. 新增依赖必须说明理由，并更新 requirements.txt（区分 pip 依赖与系统依赖）。
5. 遵守项目既有工程习惯（见第二部分 2.6）。
6. 做不到的能力**如实说明并给降级方案**，不要假装实现。

## 输出格式要求
每个里程碑完成后，给我：
- `git diff --stat` 摘要
- 关键设计决定与理由（例如"为什么用 PySide6 而不是继续 GTK"）
- 我该执行的具体命令与操作步骤（要能照着做）
- 已知问题 / 未完成项 / 需要我决策的事项
```

---

# 第二部分：技术底账

## 2.1 现在的架构与规模

| 部分 | 文件 | 行数 | 跨平台性 |
|---|---|---|---|
| 业务逻辑 | `asr.py` `translate.py` `speaker.py` `subtitle.py` `config.py` `export.py` `langs.py` `providers.py` `main.py` | **约 4056** | ✅ 基本不用动 |
| 平台相关 | `capture.py` `overlay.py` `sysmon.py` `paths.py` `deps.py` | **约 1709** | ⚠️ 主要工作量在这里 |
| 控制台 UI | `launcher.py` + `ui/*.py`（Tkinter） | 约 3056 | ✅ 少量改动（字体/路径/依赖提示） |
| 测试 | `tests/*.py` + `run.sh` | 约 1002 | ⚠️ 部分用例是 Linux 专用 |

**结论：真正要移植的只有 5 个文件约 1700 行**，其中 `overlay.py`（1111 行）占了大头。

## 2.2 必须保持的契约（改了会破坏历史数据/兼容性）

| 契约 | 内容 |
|---|---|
| 音频块 | **16kHz 单声道 float32**，按 ~25–40ms 分块推入 `queue.Queue` |
| `SourceInfo` | `key`: `mic` / `monitor`（未来 `tab-xxx`）；`kind`: `external`(麦克风)/`internal`(系统声音)；`label`、`app` |
| `DisplayItem` | `kind, label, app, text, translation, item_id, ts, asr_ms, llm_ms, speaker, role`（`role`: self/other） |
| 会话记录 | `sessions/*.jsonl`（UTF-8，一行一条），导出 SRT/TXT 依赖它 |
| 配置文件 | `config.yaml`（含 `layout: dialog/external/internal`）、`glossary.yaml`、`keys.env`、`api_history.yaml` |
| 模型目录 | `models/`（ASR = SenseVoice ONNX，约 233MB；`models/speaker/` = CAM++ 28MB） |
| 翻译协议 | OpenAI 兼容（`openai` SDK）多服务商 + 本地 Ollama |
| 目录解耦 | 代码目录与数据目录分离（源码运行=工程目录；安装运行=用户数据目录） |

## 2.3 平台差异总表（Linux 现状 → Windows 方案）

| # | 能力 | Linux 现在怎么做 | Windows 要怎么做 | 难度 |
|---|---|---|---|---|
| 1 | 麦克风采集 | `sounddevice`(PortAudio) | **同一套 API 直接可用** | ★ |
| 2 | **系统声音采集** | `parec -d <monitor源> --format=s16le --rate=16000 --channels=1 --raw` | **WASAPI loopback**：`soundcard`（`all_microphones(include_loopback=True)`）或 `pyaudiowpatch`；**必须自己降采样到 16k 单声道**（WASAPI 通常 48k 立体声） | ★★★ |
| 3 | 音频源枚举 | `pactl list sources short` 找 `.monitor` | `soundcard` 的输出设备列表 + loopback 开关 | ★★ |
| 4 | 主字幕窗 | Tkinter + `-topmost` | **同一套代码**（Tk 跨平台） | ★ |
| 5 | **字幕外挂** | GTK3 + PangoCairo + X11「输入区域置空」/ Wayland `set_pass_through` | **推荐 PySide6**：`FramelessWindowHint` + `WA_TranslucentBackground` + `WindowTransparentForInput` + `WindowStaysOnTopHint` + `WS_EX_NOACTIVATE`；备选：GTK3 + Win32 ctypes（`WS_EX_LAYERED\|WS_EX_TRANSPARENT`），但 Windows 装 GTK3 很折腾 | ★★★★ |
| 6 | 文字排版度量 | PangoCairo layout 测宽高（`overlay._measure`） | Qt `QFontMetrics`/`QTextLayout` 重写度量 | ★★ |
| 7 | 多屏几何 | `Gdk.Display.get_monitor*/get_n_monitors` | Qt `QGuiApplication.screens()` 或 Win32 `EnumDisplayMonitors`；**注意副屏在主屏左/上时是负坐标** | ★★ |
| 8 | 外挂进程启停 | `pgrep -f` / `pkill -f livetrans.overlay` | 记住 `Popen` 的 PID + `taskkill /PID <pid> /T /F`（或 PID 文件） | ★ |
| 9 | 拉起 ollama | `Popen(["ollama","serve"], start_new_session=True)` | `shutil.which("ollama")` 找 `ollama.exe` + `creationflags=CREATE_NO_WINDOW\|DETACHED_PROCESS` | ★ |
| 10 | CPU/内存监控 | 读 `/proc/stat`、`/proc/meminfo` | `psutil`（新依赖）或 Win32 `GetSystemTimes`/`GlobalMemoryStatusEx` | ★★ |
| 11 | GPU 监控 | `nvidia-smi` | **同命令可用**（`C:\Windows\System32\nvidia-smi.exe`，需 NVIDIA 驱动）；无卡则跳过 | ★ |
| 12 | 数据目录 | `$XDG_DATA_HOME` / `~/.local/share` | `%LOCALAPPDATA%\LiveTrans`（建议 `platformdirs`） | ★ |
| 13 | 文件权限 | `chmod 0600 keys.env` | Windows 无 POSIX 权限 → 目录 ACL 或忽略（注释说明） | ★ |
| 14 | 依赖自检 | 提示 `sudo apt install ...` | 改 `winget`/pip 提示；`libportaudio2` 检查不适用（wheel 自带） | ★ |
| 15 | 桌面集成 | 写 `~/.local/share/applications/*.desktop` | 开始菜单快捷方式（安装器生成） | ★★ |
| 16 | 打包分发 | `build_deb.sh` → `.deb` | PyInstaller（one-dir）+ Inno Setup → `Setup.exe`，另出便携 zip；**图标需 `.ico`**（现资源是 `.svg`） | ★★★ |
| 17 | CI | `ubuntu-latest` + xvfb | 增加 `windows-latest` job；测试入口 `run.sh` 改成跨平台的 `run.py` | ★★ |

## 2.4 建议的平台抽象层（关键设计）

不要让 Windows 代码散落在业务逻辑里，抽一层：

```
prototype/livetrans/plat/
    __init__.py    # 按 sys.platform 选择实现，对外只暴露统一函数
    base.py        # 接口定义 + 公共工具（如 resample 已在 capture.py 里）
    linux.py       # 把现在 capture.py / overlay 启动 / paths 的 Linux 实现搬进来（行为不变）
    win.py         # Windows 新实现
```

统一接口建议：

| 函数 | 作用 |
|---|---|
| `data_dir() -> Path` | 数据目录（Linux XDG / Windows %LOCALAPPDATA%） |
| `list_audio_sources() -> list[SourceInfo]` | 列出可用的「麦克风」与「系统声音」来源 |
| `open_capture(src) -> Capture` | 返回对象，有 `.start()/.stop()`，把 16k/mono/f32 块推进队列 |
| `list_monitors() -> list[Rect]` / `screen_union() -> Rect` | 多屏几何（含负坐标） |
| `spawn_overlay(cfg) -> Popen` / `overlay_running() -> bool` / `stop_overlay()` | 外挂窗生命周期 |
| `sys_stats() -> dict` | CPU/内存/GPU |
| `spawn_detached(cmd) -> Popen` | 拉起 ollama 等外部进程（平台差异封装在这里） |
| `set_dpi_awareness()` | Windows 专有（Linux 空实现） |
| `ensure_single_instance()` | 单实例锁（Linux flock / Windows 命名互斥体） |

**要求：迁移到抽象层后，Linux 的全部测试必须仍然通过**（这是"没改坏"的唯一证明）。

## 2.5 Windows 上必须用的技术选型（已定，除非你能说服我）

| 场景 | 选型 | 理由 |
|---|---|---|
| 系统声音 | `soundcard`（备选 `pyaudiowpatch`） | 支持 WASAPI loopback，能直接列出"扬声器+loopback"设备 |
| 外挂悬浮窗 | **PySide6**（Qt6） | 逐像素透明、点击穿透、置顶、多屏、DPI 都是一等公民；GTK3 在 Windows 上安装/分发极麻烦 |
| 数据目录 | `platformdirs` | 跨平台标准位置，避免自己写注册表/环境变量 |
| CPU/内存 | `psutil` | 不再读 `/proc` |
| 打包 | PyInstaller（one-dir）+ Inno Setup | one-dir 比 one-file 启动快、模型文件可直接放旁边 |
| CI | `windows-latest` | 与现有 Ubuntu job 并存 |

> 注意：外挂窗是**独立进程**（现在用 `python3 -m livetrans.overlay` 拉起），
> 所以「Qt 与 Tkinter 混用」不会冲突 —— Qt 只出现在外挂进程里。
> 但打包后 `-m` 模块入口会失效，需改成参数入口（如 `LiveTrans.exe --overlay`）。

## 2.6 项目既有工程习惯（必须遵守）

1. **Bug 修完要配回归测试**：新增 `tests/test_xxx.py`，文件顶部注释写清"守的是什么坑"。
2. **断言不要写死像素/字体相关数值**：换机器/换字体就会误报（我们在 CI 上踩过 `[33, 36, 33]`）。
   要比较就比"相对关系"，并留合理容差。
3. **外部条件不具备时优雅跳过**：打印 `[SKIP] 原因` 并以 0 退出，不要失败
   （如 `test_e2e_dialog.py` 需要 Ollama、`test_overlay_drag.py` 需要 GTK）。
4. 中文注释、中文 UI 文案；commit message 中文，说明「改了什么 + 为什么」。
5. 每次改动后跑：`python3 selfcheck.py` + `python3 tests/run.py`（或 `bash tests/run.sh`）。
6. 重要决策与踩坑记录写进 `TECH_ROADMAP.md`（这个项目的文档习惯）。

---

# 第三部分：坑清单与验收标准（你自己留着）

## 3.1 高优先级坑（按"踩中概率 × 破坏力"排序）

1. **UTF-8 编码（最高优先级）**
   Windows 默认编码是 `cp936`/`mbcs`。所有 `open()` 必须显式 `encoding="utf-8"`；
   子进程输出读取要 `encoding="utf-8", errors="replace"`；建议启动设 `PYTHONUTF8=1`。
   否则中文会话记录 / 配置 / 导出会直接崩或乱码。
2. **DPI 缩放**
   Windows 默认 125%/150% 缩放会让悬浮窗坐标、字号全部错位。
   启动即调 `SetProcessDpiAwareness(2)`（或 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`），
   并同步处理 Tk 的 `tk scaling`。
3. **打包后子进程入口失效**
   现在用 `[sys.executable, "-m", "livetrans.overlay"]` 拉外挂 —— PyInstaller 打包后没有模块入口，
   要改成 `--overlay` 参数入口（同时保留源码运行方式）。
4. **WASAPI loopback 静音时不出数据**
   没有声音播放时 loopback 流可能一直不给数据 → 读取线程必须有超时与"静音填充"策略，
   否则 VAD 状态机卡住、切歌/暂停后再也不出字幕。
5. **采样率与声道**
   WASAPI 原生多为 48kHz 立体声，必须降采样并混为单声道 16k（项目里已有 `resample()` 可用）。
   Linux 的 `parec` 直接给 16k，所以这条路径现在是空的。
6. **切换默认输出设备**（耳机↔扬声器、插拔 HDMI）会让 loopback 失效 → 要监听并重开流。
7. **负坐标多屏**：副屏在主屏左侧/上方时坐标是负数，别用 `max(0, x)` 之类把它裁掉。
8. **杀进程**：`pkill`/`pgrep` 不存在 → PID + `taskkill /PID /T /F`。
9. **信号**：`signal.SIGTERM` 在 Windows 无效 → 用 `KeyboardInterrupt` + 线程事件停止。
10. **`start_new_session=True` 在 Windows 被忽略** → 用 `creationflags`。
11. **控制台中文乱码**：`chcp 65001` 或 `sys.stdout.reconfigure(encoding="utf-8")`。
12. **带空格路径**（`C:\Program Files\...`）：subprocess 必须用 list 形式（现有代码已是，保持）。
13. **字体**：`ui/theme.py` 的字体候选要加 `Microsoft YaHei UI`/`Microsoft YaHei`/`SimHei`，
    等宽加 `Consolas`/`Cascadia Mono`。（Tk 选不到中文字体会显示方框）
14. **测试平台化**：`test_installer*`（apt/dpkg）、`test_overlay_drag`（GTK）在 Windows 上要跳过或改写；
    `tests/run.sh` 换成 `run.py`（bash 在 Windows 上不可靠）。
15. **模型下载**：国内建议设 `HF_ENDPOINT=https://hf-mirror.com`（项目已在用 hf-mirror）。
16. **杀毒/防火墙**可能拦 PyInstaller 产物（首次启动变慢或被隔离）→ 分发时说明，或用代码签名。
17. **单实例**：Linux 用 `flock`，Windows 要用命名互斥体。
18. **模型与运行库体积**：one-dir 包 + 模型共几百 MB，安装器要允许选择"是否内置模型"
    （对应 Linux 的 online/offline 两种包）。

## 3.2 里程碑建议（每步都要能跑 + 能验证）

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 骨架** | Windows 装依赖、跑通 `selfcheck.py`；把 Linux 实现搬进 `plat/`，行为不变 | Linux CI 仍全绿 + Windows 上自检通过 |
| **M1 麦克风链路** | 控制台能启动、选麦克风、识别 + 翻译，主字幕窗出字 | 对着麦克风说话，字幕与译文正确出现 |
| **M2 系统声音**（核心） | WASAPI loopback 实现「内部音频」，重采样到 16k，接入音频源页 | 不接麦克风，浏览器播英文视频就能出字幕 |
| **M3 外挂悬浮窗** | PySide6 重写 overlay：逐像素透明 + 点击穿透 + 置顶 + 悬停面板 + 多屏 + DPI；复用原有 ASR/翻译管线 | 见 3.3 验收清单相关项 |
| **M4 全面回归** | 声纹 / 会话 / 导出 / 镜像 / 弱网降级；Windows 测试集可跑 | 3.3 清单全部通过 |
| **M5 打包分发** | PyInstaller + Inno Setup + 便携 zip + `.ico` + 开始菜单；首启模型下载 | 干净 Windows（未装 Python）安装后可用；卸载干净 |
| **M6 CI** | 加 `windows-latest` job；发版流程产出 Windows 安装器 | 两个平台 CI 都绿，Release 同时含 deb 与 exe |

## 3.3 人工验收清单（勾完才算 Windows 版可用）

```
□ 麦克风：说一句 → 字幕 + 译文出现，延迟可接受
□ 系统声音：浏览器播英文视频 → 出字幕（不依赖麦克风）
□ 静音场景：暂停播放 30 秒再恢复 → 不报错、字幕继续
□ 两路同开：对话模式两栏分别显示，不串台
□ 外部 / 内部模式：单一界面显示正确
□ 声纹：多人视频能标 S1/S2（装了 sherpa-onnx）
□ 设备切换：耳机↔扬声器 切换后字幕能恢复
□ DPI：125% / 150% 缩放下外挂窗位置与字号正确
□ 多屏：外挂窗拖到副屏（含左侧负坐标副屏）不弹回
□ 点击穿透：外挂窗下方的按钮可以直接点到
□ 置顶：全屏视频 / 自动隐藏任务栏 之下仍在最前
□ 不抢焦点：点外挂窗不会把游戏/视频窗口切走
□ 会话：保存 + 导出 SRT/TXT，中文不乱码
□ 镜像模式：能跟随另一个实例的会话
□ 休眠唤醒：睡 1 分钟唤醒后仍能出字幕
□ 控制台：所有页面可打开，输入框高度一致（UI 规范测试）
□ 打包版：干净机器 + 无 Python 环境可安装运行；不需要管理员权限
□ 安装包体积与卸载干净
```

## 3.4 CI 与发版建议

- 新增 job `windows-tests`（`windows-latest`, Python 3.11）：`pip install -r requirements.txt` →
  `python selfcheck.py` → `python tests/run.py`。
  ⚠️ runner 上没有 NVIDIA 卡（`nvidia-smi` 不存在），GPU 相关检查必须能优雅跳过。
- 发版 `release.yml` 增加 Windows 产物：PyInstaller（one-dir）→ Inno Setup →
  `LiveTrans-Setup-<版本>.exe` + `LiveTrans-<版本>-portable.zip`，与 `.deb` 一起传到同一个 Release
  （现有用 `gh release create/upload` 的逻辑可直接复用；注意"已存在则更新"的幂等分支）。

---

## 附：给 AI 的一句话开场（可直接用）

> 这个项目是 Linux 上已完成并发布的实时语音翻译字幕工具，现在要移植到 Windows。
> 请你先读完 `README.md`、`TECH_ROADMAP.md` 和 `prototype/` 下的代码，
> 按 `WINDOWS_PORT_BRIEF.md` 的第二部分理解技术底账，
> 然后**先给我一份移植方案**（改动清单 + 每步验证方式 + 风险），我确认后再动手。
> 逐个里程碑推进，每步都要能运行、能测试，并且不要破坏 Linux 版。
