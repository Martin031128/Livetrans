"""Win32 窗口风格助手：鼠标穿透 / 不抢焦点 / 置顶（对应 GTK 三件套）。"""
from __future__ import annotations

import ctypes
import sys


# Win32 常量（点击穿透 / 不激活 / 置顶）
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

from ._common import _log


# ---------------------------------------------------------------- Win32 辅助


def _hwnd(widget) -> int:
    """取 Qt 窗口的 Win32 句柄（HWND）。"""
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001
        return 0


def _set_click_through(hwnd: int, enable: bool) -> None:
    """鼠标穿透：WS_EX_TRANSPARENT 让点击落到背后窗口，WS_EX_LAYERED 是前提。

    （对应 Linux 版的 `input_shape_combine_region(空区域)` / `set_pass_through`）
    """
    if not hwnd or sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enable:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT          # 保留 LAYERED：透明背景需要它
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception as e:  # noqa: BLE001
        _log(f"穿透设置失败: {e}")


def _set_no_activate(widget, enable: bool = True) -> None:
    """不抢焦点 + 不在任务栏显示（对应 GTK 的 set_accept_focus(False)/skip_taskbar）。"""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = _hwnd(widget)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enable:
            style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        else:
            style &= ~(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:  # noqa: BLE001
        pass


def _set_topmost(widget, enable: bool) -> None:
    """置顶（对应 GTK 的 set_keep_above）。"""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = _hwnd(widget)
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST if enable else HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:  # noqa: BLE001
        pass

