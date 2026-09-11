# LiveTrans · Windows 版代码结构评审与重构建议

> 生成于 2026-09-11,基于对 `platforms/windows/` 的结构化分析
> （行数度量 + 模块大纲 + 写入点/重复模式检索）。只提建议，未动代码；
> 每条建议附验证方式，可按顺序逐条落地，符合项目"每步可跑可测"的习惯。

---

## 一、现状总览

| 模块 | 规模(约) | 职责 | 评估 |
|---|---|---|---|
| `livetrans/overlay.py` | 1233 行 | 悬浮字幕窗 + 控制面板 + 设置弹窗 + 提示气泡 + 音频管线装配 | ⚠ 单文件过重，UI 与管线混装 |
| `livetrans/subtitle.py` | 1147 行 | 主字幕窗(三布局) + 栏目渲染 + DisplayItem + 本地色表 | ⚠ 色表/Tooltip 与 ui 重复 |
| `livetrans/main.py` | 751 行 | 主程序管线装配 + DialogRouter + TranslatorWorker | 职责清晰但配置写盘散落 |
| `livetrans/ui/page_backend.py` | 738 行 | 翻译后端页(最复杂的一页) | 可接受 |
| `launcher.py` | 539 行 | 控制台壳 + 装配 6 个页面 Mixin + 保存 | ⚠ 配置双表示 |
| `livetrans/capture.py` | 367 行 | 麦克风 + WASAPI loopback | ⚠ 命名是 PulseAudio 时代遗产 |
| 其余核心 | 各 <350 行 | asr/translate/speaker/export/keys/config… | ✅ 单一职责清晰 |

**做得好的（保持不动）**：业务与平台分离的目录分叉（linux 冻结 / windows 演进）；
`config.py` 的 dataclass + `_merge` 默认值兜底；`tests/` 14 个文件 + `selfcheck.py`
的回归纪律（断言不写死像素）；`ui/theme.py` 的尺寸令牌集中管理；文档习惯
（TECH_ROADMAP 逐次日志）。

---

## 二、问题清单与建议（按优先级）

### P0-1 · `ui/*.py` 六个文件顶着同一套复制粘贴的 import 头

**证据**：`page_audio / page_backend / page_chat / page_sessions / overlay_card / key_ui`
六文件开头是完全相同的 10 个 stdlib import（json/math/os/queue/shutil/subprocess/
sys/threading/time/urllib.request/webbrowser），各页实际只用到两三个——这是
从 launcher 拆分 Mixin 时整段拷贝的遗产。

**建议**：逐文件删除未使用 import（改完跑 `python -m py_compile` + 全套件即可，
纯机械、零行为变化）。可顺手把 `tk.font` 等真正用到的保留。

**风险**：无。**工作量**：30 分钟。**验证**：`tests/run.py` 全绿 + 控制台能开。

### P0-2 · `config.yaml` 有三个独立的"读-改-写"方，存在并发覆盖风险

**证据**（三个写入方各写各的，全部整文件重写）：
- `overlay.persist_overlay()` —— 外挂的位置/字号/透明度/配色；
- `main._persist_subtitle_style()` / `_persist_dialog()` —— 样式面板/对话设置；
- `launcher._apply_and_save()` —— 控制台"保存并启动"整份重写。

控制台常驻运行、外挂独立进程：外挂保存配色后，若控制台再点"保存设置"，
会用它**启动时**的内存快照整份覆盖——这正是本轮修过的"text_color 丢失"同类坑
的通病形态（launcher 已对颜色两键做磁盘拉新，但 `pos_x/pos_y/opacity/
font_size/width_ratio` 等外挂托管键仍走旧快照）。

**建议**：抽一个唯一的配置写入层（如 `livetrans/configstore.py`）：
- 对外只暴露 `patch_section(section, dict)`（读→深合并该段→写回），
  三个写入方全部改走它；谁也不许整份 dump；
- 控制台对 `overlay` 段的**全部外挂托管键**（不只颜色）改为从磁盘拉新；
- 可加一个 mtime 检查：写入前发现文件被外部改过则重新读基线。

**风险**：低（写入语义不变，只收口）。**工作量**：半天。**验证**：
现有 `test_persist_overlay_writes_yaml` + 新增"外挂保存后控制台保存不丢键"用例。

### P0-3 · 字幕窗 `_Tooltip` 与 `ui/widgets.ToolTip` 是两套重复实现

**证据**：`subtitle.py` 的 `_Tooltip`（Enter 延迟/离开销毁/overrideredirect）
与 `ui/widgets.py` 的 `ToolTip` 功能同构；本轮 widgets 版已接入雅黑字体，
subtitle 版还是写死 `("sans", 9)` 的旧款。

**建议**：subtitle.py 改用 `ui.widgets.ToolTip`（或把 subtitle 的特殊行为——
如换行宽——并进去），删除 `_Tooltip`。

**风险**：低。**工作量**：1 小时。**验证**：字幕窗悬停提示正常 + 全套件。

### P1-1 · `overlay.py`（1233 行）按职责拆分

**证据**：单文件内混装 5 类职责——①窗口渲染（`OverlayWindow`：绘制/历史/拖动/
持久化）；②面板与弹窗（`ControlPanel/SettingsPopup/_TipHost/TipBubble`）；
③Win32 风格助手（`_hwnd/_set_click_through/_set_no_activate/_set_topmost`）；
④管线装配（`run_overlay`：音频源启停/ASR/翻译 worker/镜像模式/keys 注入）；
⑤入口（`main/argparse`）。每次改设置弹窗都要在千行文件里上下横跳。

**建议**：拆为 `livetrans/overlay/` 包（对 `livetrans.overlay` 的导入路径保持
兼容——包 `__init__.py` re-export `OverlayWindow/SettingsPopup/run_overlay/
persist_overlay` 等现有符号，测试零改动）：
```
livetrans/overlay/
    __init__.py      # re-export，外部 import 路径不变
    window.py        # OverlayWindow（含 _rounded_path/_measure）
    panel.py         # ControlPanel + SettingsPopup + TipBubble + _TipHost
    win32.py         # _hwnd/_set_click_through/_set_no_activate/_set_topmost
    pipeline.py      # run_overlay（音频源管理 + boot 线程）
    persist.py       # persist_overlay + persist_geom/scheme 的落盘逻辑
```

**风险**：低-中（纯移动 + re-export，契约不变）。**工作量**：半天。
**验证**：`tests/test_overlay_qt.py` 原样全过即证明没拆坏。

### P1-2 · 控制台配置"双表示"：原始 dict 与 dataclass 并存

**证据**：`launcher.Launcher.cfg` 是原始 yaml dict，而 `livetrans/config.py`
有 `load_config() → AppConfig`。两套表示各有一半读写逻辑，且已经踩过坑——
`page_backend._speed_test_bg` 里注释自证：*"self.cfg 是原始 yaml dict，
直接传会 'dict' object has no attribute"*（于是那里临时再 load_config 一份）。

**建议**：分两步走——
1. 短期（低风险）：给 `self.cfg` 的读写收口成少量方法（`cfg_get/cfg_patch`），
   在 `_apply_and_save` 与磁盘间建立唯一通道（与 P0-2 合并实施）；
2. 长期（可选）：Launcher 迁移到 `AppConfig` dataclass 单一表示，保存时由
   dataclass 序列化。涉及所有 Mixin 的 `self.cfg[...]` 调用点，工作量 1-2 天，
   建议在 M5 打包前完成即可。

**验证**：`test_layout`（四态形态/落盘）+ 全套件 + 手工"改设置→重启→生效"。

### P1-3 · `subtitle.py`（1147 行）拆分与色表收口

**证据**：文件顶部 36-49 行重复定义了 `BG/PANEL/PANEL_2/SIGNAL/INK/BORDER…`
（与 `ui/theme.py` 平行的第二份色表，两处改色会漂移）；类结构清晰
（`DisplayItem` 数据类 / `_Pane` 栏目渲染 / `SubtitleWindow` 窗口与布局）。

**建议**：①色表删除，改 `from livetrans.ui.theme import …`（theme 已被
ui 包外共享，无循环依赖风险——theme 不 import subtitle）；②`_Pane` 拆到
`livetrans/subtitle_pane.py`（或包），`subtitle.py` 留 `DisplayItem +
SubtitleWindow`（`DisplayItem` 是跨模块契约，保持导入路径不变）。
①机械低险，②中等建议与 P1-1 同批做。

**验证**：`test_pane_render / test_layout / test_router` 全过。

### P2-1 · `capture.py` 命名债：`ParecCapture` 实为 WASAPI loopback

**证据**：类 docstring 自述"Windows 上内部实现是 **WASAPI loopback**，而非
parec 子进程"；`list_monitor_sources` 里还保留 pactl 分支（注明仅供保留）。
名字是 PulseAudio 时代遗产，`monitor_source/monitor_device` 等配置键同理。

**建议**：加别名 `WasapiLoopbackCapture = ParecCapture` + docstring 说明，
**不改配置键**（`audio.monitor_*` 是持久化契约，改了会破坏用户配置）；
注释里已有解释，属于"低成本澄清"。同时评估 pactl 分支是否可删
（Windows 版本目录里保留它只为对照 Linux 版）。

**风险**：无（纯加别名）。**验证**：导入 + `test_capture_win` 全过。

### P2-2 · `run_overlay` 管线缺自动化测试接缝

**证据**：`tests/test_overlay_qt.py` 覆盖窗口/面板/持久化，但 `run_overlay`
的音频源启停/切换（`start_source/stop_source/set_source`）依赖真实音频设备，
没有用例（静音/设备热插拔正是简报 3.1-4/6 的坑区）。

**建议**：给 `run_overlay` 加接缝——把"音频源工厂"抽成可注入参数
（`source_factory: Callable[[kind], (SourceInfo, Capture)] | None`），
测试注入假源（队列喂 16k 数据）即可测"切换不停窗/暂停丢帧/退出清线程"。

**工作量**：半天。**验证**：新增 `tests/test_overlay_pipeline.py`（假源驱动）。

### P2-3 · CI 补 Windows job（对应 M6）

`tests/run.py` 已跨平台（Linux 用例优雅跳过），`windows-latest` job 直接
`pip install -r requirements.txt → selfcheck → tests/run.py` 即可，
Linux 用例自动 SKIP。上打包（PyInstaller/Inno）前先落这个。

---

## 三、建议执行顺序

| 步骤 | 内容 | 为什么排这里 |
|---|---|---|
| 1 | P0-1 import 清理 | 纯机械热身，顺手熟悉 ui 包 |
| 2 | P0-2 配置写入层收口 | 先堵数据丢失类风险，P1-2 依赖它 |
| 3 | P0-3 Tooltip 收口 | 小而独立 |
| 4 | P1-1 overlay 拆包 | 拆完后续改面板/弹窗效率大幅提升 |
| 5 | P1-3 subtitle 色表+拆分 | 与 4 同手法，趁热 |
| 6 | P1-2 配置单表示 | 摊子大，放在结构稳定后 |
| 7 | P2-* | 视里程碑（打包/CI）再排 |

每步完成后固定动作：`python selfcheck.py` + `python tests/run.py` 全绿，
TECH_ROADMAP 记一条。

## 四、不要动的契约（重构红线）

- `sessions/*.jsonl` 行格式、`DisplayItem` 字段（导出/镜像依赖）；
- `config.yaml` 既有键名（含 `audio.monitor_*`、`overlay.*`、`subtitle.*`）；
- `livetrans.overlay` / `livetrans.subtitle` 的**导入路径与符号名**
  （overlay 进程入口 `--overlay`、`app_command()` 匹配词都依赖它们）；
- Linux 版目录（冻结基线，Windows 侧不回写）。
