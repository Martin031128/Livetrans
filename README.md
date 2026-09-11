# LiveTrans

实时语音翻译：捕获**系统播放的声音**与**麦克风**，用本地语音识别转写，
再用大模型翻译，以**置顶双语字幕**或**悬浮字幕窗**叠加在任意画面上。

**支持 Linux 与 Windows 两个平台，两者平级、同步开发、共同发行。**

技术路线、决策记录与逐次改动日志见 [TECH_ROADMAP.md](TECH_ROADMAP.md)。

[![License: MIT](https://img.shields.io/badge/License-MIT-39c5b8.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Martin031128/Livetrans?label=下载&color=39c5b8)](https://github.com/Martin031128/Livetrans/releases/latest)

**下载安装包**：[Releases 页面](https://github.com/Martin031128/Livetrans/releases/latest)
- Linux：`..._all_online.deb` 普通版 / `..._all_offline.deb` 完全离线版
- Windows：`LiveTrans-<版本>-portable.zip` 便携版（解压即用）

源代码仓库：<https://github.com/Martin031128/Livetrans>

## 平台与仓库结构

两个平台各有一份完整实现，互不干扰；共享的只有文档与发版流程。

```
platforms/
├── linux/      Linux 版（Debian/Ubuntu 发行，GTK3 外挂窗，parec 抓系统声音）
└── windows/    Windows 版（便携 zip / 安装器，Qt 外挂窗，WASAPI loopback 抓系统声音）
```

| | Linux 版（`platforms/linux`） | Windows 版（`platforms/windows`） |
|---|---|---|
| 系统声音 | PulseAudio/PipeWire monitor 源（`parec`） | WASAPI loopback（`soundcard`） |
| 字幕外挂 | GTK3（逐像素透明） | PySide6 / Qt6（逐像素透明） |
| 数据目录 | `$XDG_DATA_HOME/livetrans` | `%LOCALAPPDATA%\LiveTrans` |
| 资源监控 | 读 `/proc` | `psutil` |
| 打包 | `packaging/build_deb.sh` → `.deb` | `packaging/build_windows.py` → 便携 zip / 安装器 |
| 测试入口 | `tests/run.sh`（bash） | `tests/run.py`（Python，跨平台） |

> 下面「功能」「安装」「快速开始」等章节：**通用说明对两个平台都成立**，
> 命令与路径差异处会分别标注；具体安装步骤请按上方平台表进入对应目录。

## 功能

**实时字幕**
- 双路音频可单独或同时捕获（麦克风 / 系统声音），字幕分栏标注来源（绿=麦克风，蓝=系统）
- 本地语音识别 **SenseVoiceSmall**（中英日韩粤，离线、无需联网）
- 说话时原文**边说边上屏**，停顿即断句（可调），长句自动拆条；译文随模型生成逐字补全
- 字幕可上下滚动回看（阅读时不会被新字幕拽走）、文字可选中复制、样式面板可调字号与颜色
- **声纹角色标注**：同一路里换人会自动标 `S1/S2…` 并用不同颜色（多人会议能分清谁在说）
- **对话模式**：左右分栏（左=你 / 右=对方），**每一路各自选音频来源、各自配翻译方向**，
  例如 麦克风→English、系统声音→中文（双向同传）；换来源不打断该角色的上下文
- **导出字幕**：顶栏「导出」把本次会话存成 SRT（双语/仅译文/仅原文）或纯文本

**字幕外挂（悬浮字幕窗）**
- 无边框、**逐像素透明**（背景与文字透明度独立可调）、可置顶、可鼠标穿透、不抢焦点
- 默认贴在屏幕底部居中（电影字幕安全区），可用鼠标拖动移动、拖右下角改宽度
- 鼠标移到字幕上浮出控制面板：隐藏/显示、暂停/继续、上一条/下一条/最新、位置、穿透、固定、退出
- 音频来源可在运行时切换；位置、宽度、透明度会自动记住；支持**副屏**（默认跟随鼠标所在屏）
- **镜像模式**：只显示主程序的字幕、不自己识别翻译，两个一起用时不再重复消耗 API/算力

**翻译**
- 一套 OpenAI 兼容协议切换：DeepSeek / 智谱 GLM / 通义千问 / Kimi / OpenAI / Gemini / Claude / Grok
- 本地模型：**Ollama**（控制台可自动拉起服务），无需 API Key
- 普通 / 专业模式（领域提示词 + 术语表）、上下文窗口、并发数可调
- 中文语音自动译为英文（双向对话场景）、模型测速（出字时间/总耗时/每秒字数）
- **弱网降级**：云端 API 连续失败 2 句后自动改用本机模型继续翻译，云端恢复后自动回切
- **本地模型显存管理**：控制台可看常驻模型并「释放显存（取消挂载）」；可选空闲自动卸载与
  启动时清理旧模型（Ollama 换模型不会自动卸载旧的，实测两张会同时占显存）

**会话与助手**
- 每场会话自动存档（原文/译文/模型原始输出），可一键生成总结（主题/要点/行动项）
- 「对话」页：选一场会话作为素材，就其中的原文/译文向模型提问
- 总结与对话可用与翻译后端不同的模型（例如翻译用云端、总结用本地）

**控制台**
- 可视化设置、API Key 管理（粘贴自动识别服务商、按服务商隔离的历史记录）
- 模型列表从服务商接口实拉、本地服务状态与资源占用（GPU 显存/利用率/温度、CPU、内存）实时显示
- 所有选项都带悬停说明；状态消息以右下角气泡提示

## 安装

> **先回答最常见的问题：本地模型和 Ollama 是必需的吗？**
> **不是。** 只想用云端 API（DeepSeek / 智谱 GLM / 通义千问…）的话，**装好 Python 依赖就能跑**，
> 既不用下载本地模型，也不需要 Ollama。
> 想要离线 / 免费 / 隐私（识别在本地、翻译也在本地）时才需要本地模型 ——
> 安装脚本会**默认帮你准备好**，而且**装完随时可以删**（见下面「想删掉本地模型」）。

### 方式一：一键安装（源码运行，推荐）

**Windows：**

```powershell
cd platforms\windows
python -m pip install -r requirements.txt        # 必需依赖（Tkinter 随 Python 自带，无需系统包）
python launcher.py                                # 打开控制台
```

**Linux：**

```bash
cd platforms/linux
bash packaging/install.sh        # 命令行：默认装齐（pip 依赖 + 本地识别模型 + 声纹 + Ollama 本地翻译模型）
python3 packaging/installer_gui.py   # 图形化向导：勾选要装的东西 → 看进度 → 完事
```

**图形化安装向导**（也可从控制台「翻译后端 → 本地模型」卡片点「安装向导…」打开）：

- 五个可勾选项，**默认全部勾选**（默认同意）：Python 依赖 / 本地识别模型 / 声纹 / Ollama 本地翻译模型 / 应用菜单项
- 每项旁标注状态（`✓ 已就绪（会跳过）` 或 `↓ 约 240 MB`）与总体预计下载量
- 装完最后一页直接给「启动控制台」和「**删除本地模型**」（二次确认）
- 脚本化用法：`--plan` 只打印计划；`--dry-run` 走一遍流程但不改动系统；`--only asr,desktop` 只处理指定项

「本地模型」这一项**默认是装的**，不想装用开关关掉即可：

| 命令 | 作用 |
|---|---|
| `bash packaging/install.sh` | 全部默认（含本地模型与 Ollama） |
| `bash packaging/install.sh --no-models` | **只用云端 API**：不下载任何本地模型 |
| `--no-asr` / `--no-speaker` / `--no-ollama` | 只跳过其中一项（识别模型 / 声纹 / 本地翻译模型） |
| `--ask` | 逐项询问，**默认都是「是」**（直接回车即同意） |
| `--with-apt` / `--install-ollama` | 缺系统包 / 缺 Ollama 时让脚本自动安装（需要 sudo） |
| `--dry-run` | 只打印将要做什么，不动任何东西 |
| `--remove-local` | **事后删除**已下载的本地模型（识别/声纹 + Ollama 里的模型） |

### 想删掉本地模型（腾空间）

```bash
bash packaging/install.sh --remove-local      # 删识别/声纹模型，并 ollama rm 本地翻译模型
```

- 删完**不影响云端 API**；下次要用本地模型（或首次启用声纹）时会自动重新下载
- 控制台「翻译后端 → 本地模型」卡片里也会显示当前占用与这条命令

### 方式二：Windows 便携版 / 安装器

**普通用户请直接从 [Releases](https://github.com/Martin031128/Livetrans/releases/latest) 下载**（无需自己编译）：

- `LiveTrans-<版本>-portable.zip` —— 便携版：解压后直接运行 `LiveTrans.exe`，免安装

自己编译打包：

```powershell
cd platforms\windows
python -m pip install -r requirements.txt pyinstaller
python packaging\build_windows.py --portable     # 生成 dist\LiveTrans\（含模型则一并拷入）
python packaging\build_windows.py --check        # 只检查打包环境
python packaging\build_windows.py --clean        # 清理 dist\ 与 build\
```

> **强烈建议用独立虚拟环境打包**（实测：150s/2014MB → **55s/619MB**）。
> 若在 Anaconda 的 `base`（507 个包）里打包，PyInstaller 要遍历整棵依赖树：
> 耗时长、体积暴涨，还得手工排除大量无关包。
>
> ```powershell
> # 用 Miniconda（conda-forge 源）建专用环境
> conda create -n livetrans python=3.12 -y --override-channels -c conda-forge
> conda activate livetrans
> pip install -r requirements.txt pyinstaller
> ```

- 产物在 `platforms/windows/dist/LiveTrans/`（约 620MB，含 258MB 模型），整目录拷走即可运行（one-dir：启动快）
- 若 `models/` 存在则一并打进去（离线可用）；不存在则是「首次启动联网下载」版
- **数据位置**：源码运行时在 `platforms/windows/`；打包后是 **exe 所在目录**
  （配置 / 密钥 / 会话 / 日志都放那儿，便携版可整个目录搬走）
- 打包含自定义 hook（`packaging/hooks/`）：修掉 `webrtcvad-wheels` 与 PyInstaller
  自带 hook 不兼容导致的打包失败；`EXCLUDES` 排除了 Anaconda 里的
  torch/Jupyter/vtk/cv2 等无关大件（**实测省约 1.4GB**）
- ⚠ 不要从依赖里排除 `setuptools`：PyInstaller 运行时钩子 `pyi_rth_pkgres` 需要它

### 方式三：Debian / Ubuntu 安装包（Linux 版）

**从 [Releases](https://github.com/Martin031128/Livetrans/releases/latest) 下载**（无需自己编译）：

- `livetrans_<版本>_all_online.deb` —— 普通包（首次启动联网自动下载识别模型）
- `livetrans_<版本>_all_offline.deb` —— 完全离线包（识别/声纹模型已内置，装完即用，体积约 200MB+）

自己编译打包：

```bash
cd platforms/linux
bash packaging/build_deb.sh              # 普通包（不含模型，首次启动联网下载）
bash packaging/build_deb.sh --offline    # 完全离线包：连本地模型一起打进去（约 236MB）
# 生成 dist/livetrans_<版本>_all.deb
sudo dpkg -i dist/livetrans_1.0.0_all.deb            # 依赖缺失时再 sudo apt -f install
/usr/share/doc/livetrans/install-python-deps.sh      # 补齐 pip 侧依赖（sounddevice 等）
/usr/share/doc/livetrans/install.sh --no-models      # 也可用同一个安装脚本（按需准备本地模型）
livetrans                                            # 或从应用菜单启动
```

- 代码装到 `/opt/livetrans`，数据（配置 / 密钥 / 会话 / 日志 / 自己下载的模型）在 `~/.local/share/livetrans`
- **离线包**（`--offline`）把模型装到 `/usr/share/livetrans/models`，装完**无需联网**即可本地识别/声纹；
  运行时按 `用户目录 → 项目/models → /usr/share/livetrans/models` 的顺序查找模型，
  所以用户后来自己下载或替换的模型会优先于随包版本
- 装完应用菜单里会有两项：**LiveTrans**（控制台）与 **LiveTrans 安装向导**
  （`livetrans-installer`，可随时补装或删除本地模型）

### 方式四：手动安装（都自己来）

**Linux：**

```bash
# 1) 系统依赖（Tk 界面、GTK3 外挂、系统声音捕获、中文字体）
sudo apt install python3-tk python3-gi gir1.2-gtk-3.0 gir1.2-pango-1.0 \
                 python3-cairo pulseaudio-utils fonts-noto-cjk

# 2) Python 依赖（必需）
cd platforms/linux
pip install --user -r requirements.txt

# 3) （可选，默认不必装）本地模型：离线识别 + 离线翻译
bash packaging/install.sh            # 一步到位
#   或手动：模型下载到 models/（首次启动也会自动下），Ollama 见 https://ollama.com
```

**Windows：**

```powershell
cd platforms\windows
python -m pip install -r requirements.txt
# 可选：pip install PySide6 sherpa-onnx    # 字幕外挂 / 声纹角色标注
# 可选：Ollama  https://ollama.com/download/windows
```

依赖清单与说明见 [`platforms/linux/requirements.txt`](platforms/linux/requirements.txt) 与
[`platforms/windows/requirements.txt`](platforms/windows/requirements.txt)。

## 快速开始

**Windows：**

```powershell
cd platforms\windows
python launcher.py           # 打开控制台
```

**Linux：**

```bash
cd platforms/linux
python3 launcher.py          # 打开控制台
```

1. 「翻译后端」页：选服务商（或切到「本地模型」选 Ollama）→ 粘贴 API Key → 点「测速」确认可用
2. 「音频源」页：选择要识别的声音（看视频选「内部音频 · 系统声音」，自己说话选「外部音频 · 麦克风」）
3. 点底部 **「保存并启动」** → 字幕窗出现，开始说话/播放视频即可
4. 想要悬浮字幕：在「翻译后端」页的 **字幕外挂** 卡片点「启动外挂」

> 想让它像普通软件一样打开：控制台右上角「添加到应用菜单」，之后按 `Super` 搜索 **LiveTrans**。

### 命令行方式（备选）

```bash
cp config.example.yaml config.yaml && cp glossary.example.yaml glossary.yaml
python3 -m livetrans.main --list-devices                 # 查看音频设备与 monitor 源
python3 -m livetrans.main --no-monitor                   # 仅麦克风
python3 -m livetrans.main --provider glm --mode professional --domain 医学
python3 -m livetrans.overlay                             # 只开悬浮字幕外挂
python3 -m livetrans.summarize sessions/session-xxx.jsonl -o summary.md
```

## 两种字幕形态

| | 主字幕窗（`保存并启动`） | 字幕外挂（`启动外挂`） |
|---|---|---|
| 渲染 | Tkinter（两平台相同） | Linux：GTK3 / Windows：PySide6·Qt6（均为逐像素透明） |
| 布局 | 三种：**对话**（两条字幕流并排，聊天窗式：自己靠右、对方靠左，说话侧高亮）/ **外部**（只看麦克风）/ **内部**（只看系统声音）——后两者是单一界面 | 单条悬浮字幕，可置顶/穿透/跨屏拖动 |
| 适合 | 边听边读、需要分来源、回看历史 | 看电影/网课时不打扰画面 |
| 运行 | 独立进程，两者可同时开也可只开其一 | 独立进程，自带完整识别+翻译管线 |

> 两者共用同一份 `config.yaml`：翻译用哪个模型、哪种语言、什么模式，两边一致；
> 外挂另有自己的音频来源、外观与**屏幕**设置（可跨屏拖动，默认跟随鼠标所在屏）。
>
> 两个一起用时，把外挂设成**镜像模式**（控制台勾选）就只显示主程序的结果，
> 不再重复识别与翻译（省一份 API/算力）。

## 配置（`platforms/<平台>/config.yaml`）

| 段 | 内容 |
|---|---|
| `audio` | 采样率、麦克风/系统声音开关与设备 |
| `asr` | 识别语言、静音判句时长、单段最长、断句词数 |
| `translate` | 服务商、目标语言、模式、上下文窗口、并发、中文自动译英 |
| `providers` | 各服务商地址/Key 环境变量/模型清单（控制台维护） |
| `assistant` | 总结/对话用的模型（可与翻译后端不同） |
| `overlay` | 外挂：音频来源、置顶/穿透、字号、背景与文字透明度、边距、位置 |
| `session` | 会话日志目录 |
| `subtitle` | 主字幕窗样式（字号/颜色/不透明度/布局） |

修改方式：优先用控制台（会自动写回并保留注释）；也可直接编辑 YAML。

## 模块地图（`platforms/linux/` 与 `platforms/windows/`，两版结构一致）

```
launcher.py                 控制台外壳（窗口骨架、页面装配、配置读写、启动控制）
packaging/                  打包脚本（Linux: build_deb.sh / Windows: build_windows.py）
livetrans/
  main.py                   主程序入口：多路音频 → 识别 → 翻译 → 主字幕窗
                            （Windows 版还支持 --overlay 分派，打包后由它拉起外挂）
  overlay.py                字幕外挂（Linux: GTK3 / Windows: PySide6·Qt6）
  subtitle.py               主字幕窗（Tkinter，对话/外部/内部三布局 + 样式面板）
  mirror.py                 镜像模式：跟随主程序会话 JSONL（与 GUI 无关，两版共用逻辑）
  asr.py                    VAD 切段 + SenseVoice 转写 + 模型下载
  speaker.py                声纹角色标注（sherpa-onnx 说话人向量 + 在线聚类）
  export.py                 会话导出（SRT 双语 / TXT，时间轴与内容对齐）
  capture.py                麦克风（PortAudio）与系统声音采集
                            （Linux: parec monitor / Windows: WASAPI loopback）
  translate.py              LLM 翻译/总结/对话（多后端、思考链剥离、术语表）
  summarize.py              会话总结 CLI
  config.py                 配置加载（YAML → dataclass）
  deps.py                   运行依赖自检（缺件给 apt/pip 命令）
  sysmon.py                 资源监控（Linux: /proc / Windows: psutil）
  paths.py                  路径与数据目录（Linux: XDG / Windows: platformdirs）
```
  keys.py                   API Key 存储、服务商识别、历史记录
  providers.py              服务商数据层（模型列表拉取等）
  paths.py / langs.py       路径常量（代码/数据目录解耦）/ 语言表
  sysmon.py                 本机资源监控（GPU/CPU/内存）
  ui/
    theme.py                配色、字体、ttk 样式
    widgets.py              悬停提示、状态气泡
    page_backend.py         「翻译后端」页
    key_ui.py               Key 输入/归属识别/历史
    page_audio.py           「音频源」页
    page_sessions.py        「会话总结」页
    page_chat.py            「对话」页
    overlay_card.py         外挂设置卡片与启停
```

## 常见问题

- **没有系统声音**：确认「音频源」页的「来源」与实际播放设备一致（浏览器声音走哪个输出，就选哪个 monitor 源）；`--list-devices` 可查看可用源。
- **字幕不出/很慢**：看 `platforms/<平台>/run.log`（历史轮转在 `run.log.1…5`）。云端模型若开启了深度思考会明显变慢，控制台「测速」可对比；本地模型首次要加载（数秒~1 分钟）。
- **外挂启动失败**：多为缺少 GTK3 组件，按提示执行 `apt install`；`pgrep -af livetrans.overlay` 可查看是否已有实例在跑。
- **Key 放哪**：环境变量优先，其次是控制台「翻译后端」页填写的（保存在项目目录 `keys.env`，权限 600，已 gitignore）。粘贴时会自动识别服务商，`sk-` 前缀多家共用时会弹窗确认归属。

## 开发

**Windows：**

```powershell
cd platforms\windows
python selfcheck.py             # 自检（配置/键/ASR/翻译/导出/声纹/显存/字幕/外挂）
python tests\run.py             # 自检 + 全部专项测试
python tests\run.py router      # 只跑名字匹配的用例
python tests\run.py --list      # 只列出将要运行的用例
python launcher.py              # 控制台（改动后直接跑）
```

**Linux：**

```bash
cd platforms/linux
python3 selfcheck.py            # 10 项自检（配置/键/ASR/翻译/导出/声纹/显存/字幕/外挂）
tests/run.sh                    # 自检 + 8 个专项测试（布局/渲染/路由/镜像/降级/端到端/外挂拖拽/UI 规范）
tests/run.sh fallback           # 只跑名字匹配的用例
python3 launcher.py             # 控制台（改动后直接跑）
python3 -m livetrans.overlay    # 外挂单跑（日志打印在终端）
```

- 日志：控制台启动的程序输出写入 `platforms/<平台>/run.log`，启动前轮转（保留 run.log.1…5）
- 会话数据：`platforms/<平台>/sessions/*.jsonl`（含模型原始输出，便于复盘）
- 依赖自检：`python -c "from livetrans.deps import missing_pip_deps, missing_overlay_deps; print(missing_pip_deps(), missing_overlay_deps())"`

> 平台差异用例会自动跳过：Windows 上 GTK/`install.sh` 相关用例打 `[SKIP]` 并以 0 退出，
> 反之 Linux 上缺 GTK 时同理——这是项目既有的「外部条件不具备时优雅跳过」约定。

## 维护者：发布与更新

```bash
# 0) 只需一次：初始化仓库并推到 GitHub（.gitignore 已排除密钥/模型/会话/打包产物）
git init && git add -A && git commit -m "LiveTrans 1.0.0"
git branch -M main
git remote add origin git@github.com:<你的账号>/livetrans.git
git push -u origin main

# 1) 日常改动 → 提交（两个平台的改动都在同一个仓库里）
git add -A && git commit -m "说明这次改了什么" && git push

# 2) 发新版本：改版本号（唯一来源）→ 打 tag → 推送，CI 同时出两个平台的产物
$EDITOR platforms/linux/livetrans/__init__.py    # __version__ = "1.1.0"
#     （Windows 版同文件：platforms/windows/livetrans/__init__.py，两处保持一致）
git commit -am "bump 1.1.0" && git push
git tag v1.1.0 && git push origin v1.1.0    # ← 这条会触发 .github/workflows/release.yml
```

- 发版工作流会**同时产出两个平台的产物**，挂在**同一个 GitHub Release** 上：
  - Linux：两个 `.deb`（普通包 + 完全离线包）与 `SHA256SUMS`
  - Windows：`LiveTrans-<版本>-portable.zip` 与 `SHA256SUMS-windows.txt`
- 提代码/PR 会跑 `.github/workflows/test.yml`：`linux` job（ubuntu + xvfb）与 `windows` job（windows-latest）各自跑自检与专项测试
- **切记**：`keys.env` / `api_history.yaml` / `config.yaml` / `sessions/` / `models/` 已在 `.gitignore` 里；
  打包脚本另有一道**安全闸**（扫到密钥或用户数据会直接中止打包），所以不要为了省事去掉它
- 首次发布前建议先执行一次密钥体检（把仓库内容扫一遍）：

```bash
grep -rInE "sk-[A-Za-z0-9_-]{16,}|[0-9a-f]{32}\.[A-Za-z0-9_-]{8,}" \
     --include='*.py' --include='*.sh' --include='*.md' --include='*.yaml' . | head
```

> 如果历史上把 Key 写进过任何会被分享/提交的文件，**先去服务商后台吊销再重新生成**。

## 许可证

[MIT](LICENSE) —— 可自由使用、修改、再分发（含商用），保留版权声明即可。

作者：[Martin031128](https://github.com/Martin031128)
