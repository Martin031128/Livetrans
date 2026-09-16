#!/usr/bin/env python3
"""assets/livetrans.svg -> assets/livetrans.ico（多尺寸 16~256）。

用法：python packaging/make_icon.py
依赖：PySide6（QtSvg 矢量渲染）+ Pillow（ICO 打包）。
Logo（assets/livetrans.svg）改动后重新跑一次，再重打包即可带上新图标。
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "livetrans.svg"
DST = ROOT / "assets" / "livetrans.ico"

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)               # Qt 渲染需 QApplication

renderer = QSvgRenderer(str(SRC))
img = QImage(256, 256, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
renderer.render(p, QRectF(0, 0, 256, 256))
p.end()

buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
assert img.save(buf, "PNG"), "PNG 渲染失败"

from PIL import Image  # noqa: E402

pil = Image.open(io.BytesIO(bytes(buf.data())))
pil.save(DST, format="ICO",
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                (128, 128), (256, 256)])
print("OK ->", DST)
