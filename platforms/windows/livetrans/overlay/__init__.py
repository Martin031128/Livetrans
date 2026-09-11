"""字幕外挂（PySide6/Qt6）：无边框、逐像素透明的悬浮字幕窗 + galgame 式悬停控制面板。

用法：
    python -m livetrans.overlay [--config config.yaml]      # 源码运行
    LiveTrans.exe --overlay [--config config.yaml]          # 打包后（-m 入口失效）

由控制台「启动外挂」按钮拉起。复用主程序同一套「音频 → SenseVoice → LLM 翻译」
管线（ASRWorker / TranslatorWorker 原样复用），只把渲染层换成悬浮窗：
`OverlayWindow` 实现了与 `SubtitleWindow` 相同的接口
（post / update_translation / set_partial / set_level / set_status）。

为什么用 Qt（对应 Linux 版的 GTK3）：
- Tk 的 `-alpha` 是整窗属性，没有逐元素 alpha，做不到"背景透明但文字不透明"；
  Qt 用 `WA_TranslucentBackground` + `QPainter` 可分别设 alpha（等价 GTK3 RGBA + cairo）。
- Qt 的圆角/抗锯齿与文字排版（QPainterPath / QFontMetrics）能力与 cairo/Pango 相当。
- GTK3 在 Windows 上需 MSYS2 全家桶，分发极麻烦；PySide6 一个 wheel 搞定。

能力（与 Linux 版逐项对齐）：
- 无边框 / 置顶（可配）/ 鼠标穿透（Win32 `WS_EX_TRANSPARENT`）/ 不抢焦点；
- 默认主屏底部居中：宽 = 屏宽 x width_ratio，距底 = bottom_offset（字幕安全区）；
- 背景透明度（opacity）与文字透明度（text_opacity）**完全独立**；
- 控制面板贴在字幕框顶部、默认隐藏、鼠标悬停出现（指针轮询，穿透态同样有效）：
  隐藏/显示、暂停/继续、‹上一条/›下一条/最新、位置（拖动·缩放）、穿透、固定、退出
  + 背景/文字两根透明度滑条（背景透明度/文字透明度）+ 声音来源下拉；
  + ⚙ 外观设置弹窗：颜色仅本会话生效（「保存方案」持久化/「恢复默认」复位）；
  + 面板/弹窗控件悬停显示深色提示气泡（对齐控制台 ToolTip 的观感与节奏）；
- 位置/宽度/透明度写回 config.yaml 的 overlay 段，下次启动沿用。

拆包说明（2026-09，结构优化）：本包由单文件 overlay.py 拆分而来，
对外导入路径与 `python -m livetrans.overlay` 入口保持不变：

- window.py   OverlayWindow（字幕窗本体：绘制/历史/拖动/持久化）
- panel.py    ControlPanel + SettingsPopup + TipBubble + _TipHost
- pipeline.py run_overlay + main（音频源管理 + ASR/翻译 boot）
- persist.py  persist_overlay（统一走 configstore 写入层）
- win32.py    Win32 窗口风格助手（穿透/不激活/置顶）
- _common.py  配色常量 + _log + _rounded_path
"""

from ._common import (INK, PANEL_BG, PANEL_BTN, PANEL_BTN_HOVER,
                      SIGNAL, SRC_COLOR, _log, _rounded_path)
from .panel import ControlPanel, SettingsPopup, TipBubble, _TipHost
from .persist import persist_overlay
from .pipeline import main, run_overlay
from .win32 import (_hwnd, _set_click_through, _set_no_activate,
                    _set_topmost)
from .win32 import (GWL_EXSTYLE, HWND_NOTOPMOST, HWND_TOPMOST, SWP_NOACTIVATE,
                    SWP_NOMOVE, SWP_NOSIZE, WS_EX_LAYERED, WS_EX_NOACTIVATE,
                    WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT)
from .window import OverlayWindow

__all__ = ["OverlayWindow", "ControlPanel", "SettingsPopup",
           "TipBubble", "_TipHost", "run_overlay", "main",
           "persist_overlay", "_log", "_hwnd", "_set_click_through",
           "_set_no_activate", "_set_topmost", "_rounded_path",
           "INK", "SIGNAL", "SRC_COLOR", "PANEL_BG", "PANEL_BTN",
           "PANEL_BTN_HOVER",
           "GWL_EXSTYLE", "WS_EX_LAYERED", "WS_EX_TRANSPARENT",
           "WS_EX_NOACTIVATE", "WS_EX_TOOLWINDOW",
           "SWP_NOMOVE", "SWP_NOSIZE", "SWP_NOACTIVATE",
           "HWND_TOPMOST", "HWND_NOTOPMOST"]
