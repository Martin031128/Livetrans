"""外挂共享件：配色常量 + 日志 + 圆角路径（窗口/面板/管线共用）。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath


# 与 Linux 版一致的配色
INK = "#dce3ee"          # 主文字色（浅灰蓝）
SIGNAL = "#39c5b8"       # 强调色（青）
SRC_COLOR = (199, 207, 222)     # 原文小字（近似 Linux 的 0.78/0.81/0.87）
PANEL_BG = "#1c2230"
PANEL_BTN = "#232a3a"
PANEL_BTN_HOVER = "#2f394f"



def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    """圆角矩形路径（对应 Linux 版的 _rounded_path：cairo 四段 arc）。"""
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path
