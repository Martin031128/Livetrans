"""OverlayWindow：悬浮字幕窗本体（绘制 / 历史 / 拖动 / 持久化）。"""
from __future__ import annotations

import queue
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics,
                           QGuiApplication, QPainter, QPainterPath,
                           QPen)
from PySide6.QtWidgets import QApplication, QWidget

from ..config import OverlayConfig
from ..subtitle import DisplayItem
from ._common import SRC_COLOR, _log, _rounded_path
from .panel import ControlPanel
from .persist import persist_overlay
from .win32 import (_hwnd, _set_click_through, _set_no_activate,
                    _set_topmost)

class OverlayWindow(QWidget):
    """悬浮字幕窗：与 SubtitleWindow 同接口（供 TranslatorWorker/ASRWorker 复用）。"""

    POLL_MS = 60            # 队列消费间隔
    HOVER_MS = 150          # 悬停检测间隔
    AUTO_HIDE_MS = 700      # 鼠标离开后自动隐藏面板
    HOVER_PAD = 8

    def __init__(self, ov: OverlayConfig, on_pause=None, on_exit=None,
                 config_path: Path | None = None):
        super().__init__(None)
        self.cfg = ov
        self.config_path = config_path
        self.on_pause = on_pause
        self.on_exit = on_exit
        self.on_source_change = None      # 由 run_overlay 注入：切换音频来源
        # 管线线程只往队列里塞，UI 线程轮询消费（与 Tk/GTK 版一致）
        self.q: "queue.Queue[DisplayItem]" = queue.Queue()
        self.uq: "queue.Queue[tuple[str, str, float | None]]" = queue.Queue()
        self.pq: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.hist: list[DisplayItem] = []
        self.idx = -1
        self.follow = True
        self.paused = False
        self.mirror = False                # 镜像模式：只显示主程序结果
        self.visible = True
        self.live = ""
        self.edit_mode = False
        self._passthrough = bool(ov.click_through)
        self._drag: tuple | None = None
        self._start_until = time.monotonic() + 4.0
        self._last_geom = (0, 0, 0, 0)     # 最近一次几何（隐藏时给面板定位）

        # ---- 窗口本体 ----
        # 无边框 + 置顶 + 透明背景（逐像素 alpha 的前提）+ 不抢焦点 + 不进任务栏
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        if ov.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)

        self.mon_rect, self.mon_name = self._pick_monitor()
        self.manual_pos: tuple[int, int] | None = (
            (ov.pos_x, ov.pos_y) if ov.pos_x >= 0 and ov.pos_y >= 0 else None)

        self.width_ = max(360, int(self.mon_rect.width()
                                   * max(0.2, min(1.0, ov.width_ratio))))
        self.height_ = max(70, int(ov.font_size * 2.4))
        self._apply_geometry()
        self.show()
        self._init_win32()

        self.panel = ControlPanel(self)
        self._render()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(self.POLL_MS)

        _log(f"外挂字幕窗已创建（PySide6/Qt6）：{self.width_}x{self.height_}，"
             f"{self.mon_name} {self.mon_rect.width()}x{self.mon_rect.height()}"
             f"（置顶={'开' if ov.always_on_top else '关'}，"
             f"穿透={'开' if self._passthrough else '关'}）")

    # ---- 窗口设置 ----

    def _init_win32(self) -> None:
        """Win32 层设置：不激活 + 置顶 + 应用穿透状态。"""
        _set_no_activate(self, True)
        _set_topmost(self, bool(self.cfg.always_on_top))
        self._apply_passthrough(self._passthrough)

    def _pick_monitor(self) -> tuple[QRect, str]:
        """用哪块屏（副屏适配）：配置 0=主屏 / 1..N=对应序号 / -1=鼠标所在屏。"""
        screens = QGuiApplication.screens()
        if not screens:
            return QRect(0, 0, 1920, 1080), "屏幕"
        want = int(getattr(self.cfg, "monitor", -1) or -1)
        scr = None
        label = ""
        if want > 0:
            if want <= len(screens):
                scr = screens[want - 1]
                label = f"屏幕 {want}"
        elif want == 0:
            scr = QGuiApplication.primaryScreen()
            label = "主屏"
        if scr is None:                     # -1（自动）：鼠标所在屏
            scr = QGuiApplication.screenAt(QCursor.pos())
            if scr is not None:
                label = "鼠标所在屏"
        if scr is None:
            scr = QGuiApplication.primaryScreen() or screens[0]
            label = "主屏"
        return scr.geometry(), label

    def _desktop_rect(self) -> QRect:
        """所有显示器的并集：跨屏拖动时用它做边界（对应 GTK 的 _desktop_bounds）。"""
        rect = QRect()
        for s in QGuiApplication.screens():
            rect = rect.united(s.geometry())
        return rect

    def _apply_geometry(self) -> None:
        """默认底部居中（字幕安全区）；手动拖过则用记住的位置。"""
        m = self.mon_rect
        if self.manual_pos:
            # 可跨屏：边界用所有显示器的并集，而不是当前这一块屏
            d = self._desktop_rect()
            x = max(d.x(), min(d.x() + d.width() - self.width_, self.manual_pos[0]))
            y = max(d.y(), min(d.y() + d.height() - self.height_, self.manual_pos[1]))
        else:
            x = m.x() + (m.width() - self.width_) // 2
            bottom = m.y() + int(m.height() * (1.0 - max(
                0.0, min(0.5, self.cfg.bottom_offset))))
            y = max(m.y(), bottom - self.height_)
        self.setGeometry(x, y, self.width_, self.height_)
        self._last_geom = (x, y, self.width_, self.height_)
        if hasattr(self, "panel"):
            self.panel.reposition()

    def set_click_through(self, enable: bool) -> None:
        self._passthrough = enable
        self._apply_passthrough(enable)

    def _apply_passthrough(self, enable: bool) -> None:
        _set_click_through(_hwnd(self), enable)

    # ---- 与管线对接的接口（线程安全） ----

    def post(self, item: DisplayItem) -> None:
        self.q.put(item)

    def update_translation(self, item_id: str, text: str,
                           llm_ms: float | None = None) -> None:
        self.uq.put((item_id, text, llm_ms))

    def set_partial(self, kind: str, text: str) -> None:
        self.pq.put((kind, text))

    def set_level(self, kind: str, rms: float) -> None:
        pass                                      # 外挂不显示电平

    def set_status(self, text: str) -> None:
        _log(text)

    def set_source(self, kind: str) -> None:
        """切换音频来源（internal / external / both）：运行时启停采集，无需重启。"""
        if self.on_source_change is not None:
            self.on_source_change(kind)

    def set_mirror(self, on: bool) -> None:
        """镜像模式：音频来源由主程序决定，面板里对应项置灰并说明。"""
        self.mirror = on
        panel = getattr(self, "panel", None)
        if panel is not None:
            try:
                panel.src_combo.setEnabled(not on)
                panel.src_lab.setText("声音来源（跟随主程序）" if on else "声音来源")
            except Exception:  # noqa: BLE001 - 面板未建好也不影响显示
                pass

    # ---- 内容 ----

    def _current(self) -> tuple[str, str]:
        """(译文或原文, 原文小字)。"""
        if self.live and self.follow and not (0 <= self.idx < len(self.hist)):
            return self.live, ""
        if not (0 <= self.idx < len(self.hist)):
            return "等待字幕 …", ""
        it = self.hist[self.idx]
        # 声纹：换人时在译文前加 [S1]（同一人连续说话不重复标注）
        tag = ""
        if it.speaker and (self.idx == 0
                           or self.hist[self.idx - 1].speaker != it.speaker):
            tag = f"[{it.speaker}] "
        if it.translation:
            return tag + it.translation, (it.text if self.cfg.show_source else "")
        return tag + (self.live or it.text), (it.text if self.live else "")

    def _fonts(self):
        """正文/原文小字字体与度量（字号对应 Linux 版的 font_size 与 0.55 倍）。"""
        f_main = QFont()
        f_main.setPointSizeF(max(9.0, self.cfg.font_size * 0.75))
        f_src = QFont()
        f_src.setPointSizeF(max(7.0, self.cfg.font_size * 0.75 * 0.55))
        return f_main, f_src, QFontMetrics(f_main), QFontMetrics(f_src)

    def _measure_height(self, main: str, src: str) -> int:
        """按内容算窗口高度（对应 Linux 版的 _measure：量出文本块高度）。"""
        inner_w = max(40, self.width_ - 2 * self.cfg.margin_x)
        _f_main, _f_src, fm_main, fm_src = self._fonts()
        flags = int(Qt.TextWordWrap | Qt.AlignHCenter)
        h = fm_main.boundingRect(0, 0, inner_w, 4000, flags, main).height()
        if src:
            h += fm_src.boundingRect(0, 0, inner_w, 4000, flags, src).height() \
                + int(self.cfg.font_size * 0.25)
        return int(h + 2 * self.cfg.margin_y + 12)

    def _max_height(self) -> int:
        """窗口高度上限：屏幕的 55%（超长内容会被裁掉顶部旧文，见 _render）。"""
        return max(160, int(self.mon_rect.height() * 0.55))

    def _render(self) -> None:
        main, src = self._current()
        need = self._measure_height(main, src)
        # 超长句（一整段无换行的场景）不能无限撑高窗口：
        # 封顶 55% 屏高，溢出改为底对齐绘制 —— 最新译文永远可见，
        # 被裁掉的只是顶部的旧内容（用户反馈：长句导致译文一直不显示）
        self._overflow = need > self._max_height()
        if self._overflow:
            need = self._max_height()
        if abs(need - self.height_) > 2:
            self.height_ = max(64, need)
            self._apply_geometry()
        self.update()

    # ---- 绘制（对应 Linux 版 _draw_panel） ----

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt 命名
        main, src = self._current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        w, h = self.width_, self.height_
        font_size = self.cfg.font_size
        radius = max(6.0, font_size * 0.5)
        bg_alpha = max(0.0, min(1.0, self.cfg.opacity))
        text_alpha = max(0.0, min(1.0, self.cfg.text_opacity))

        # 底色 / 文字色（⚙ 面板可调；hex 解析失败回退默认，保证永不黑屏白字失效）
        bg_col = QColor(self.cfg.bg_color if QColor(self.cfg.bg_color).isValid()
                        else "#000000")
        tx_col = QColor(self.cfg.text_color
                        if QColor(self.cfg.text_color).isValid() else "#ffffff")

        # 轻微阴影：只在边缘描一圈（整块叠加会让实际不透明度偏高）
        shadow = _rounded_path(QRectF(1.5, 4.5, w - 3, h - 8), radius)
        pen = QPen(QColor(bg_col.red(), bg_col.green(), bg_col.blue(),
                          int(min(1.0, bg_alpha * 0.7) * 255)))
        pen.setWidth(5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(shadow)

        # 背景圆角矩形（内部 alpha 严格等于 bg_alpha）
        bg = _rounded_path(QRectF(3, 3, w - 6, h - 9), radius)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(bg_col.red(), bg_col.green(), bg_col.blue(),
                          int(bg_alpha * 255)))
        p.drawPath(bg)

        # 文字：原文小字在上、译文大字在下，整体垂直居中
        inner_w = max(40, w - 2 * self.cfg.margin_x)
        f_main, f_src, fm_main, fm_src = self._fonts()
        flags = int(Qt.TextWordWrap | Qt.AlignHCenter)
        h_main = fm_main.boundingRect(0, 0, inner_w, 4000, flags, main).height()
        h_src = 0
        if src:
            h_src = fm_src.boundingRect(0, 0, inner_w, 4000, flags, src).height() \
                + int(font_size * 0.25)

        # 超长溢出：底对齐（最新译文贴底可见，旧文从顶缘裁掉）；
        # 正常：垂直居中
        overflow = getattr(self, "_overflow", False) and (h_src + h_main) > h
        if overflow:
            y = h - h_main - h_src - 4
        else:
            y = max(2.0, (h - (h_src + h_main)) / 2)
        if src:
            p.setFont(f_src)
            p.setPen(QColor(SRC_COLOR[0], SRC_COLOR[1], SRC_COLOR[2],
                            int(text_alpha * 255)))
            p.drawText(QRectF(self.cfg.margin_x, y, inner_w, h_src),
                       int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap), src)
            y += h_src
        p.setFont(f_main)
        p.setPen(QColor(tx_col.red(), tx_col.green(), tx_col.blue(),
                        int(text_alpha * 255)))
        p.drawText(QRectF(self.cfg.margin_x, y, inner_w, h_main),
                   int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap), main)

        # 可拖动时的右下角缩放标（未穿透即可用；编辑模式另加虚线框与文字提示）
        if not self._passthrough:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(56, 196, 184, 140 if not self.edit_mode else 255))
            grip = QPainterPath()
            grip.moveTo(w - 22, h - 8)
            grip.lineTo(w - 8, h - 22)
            grip.lineTo(w - 8, h - 8)
            grip.closeSubpath()
            p.drawPath(grip)
            if self.edit_mode:
                p.setBrush(Qt.NoBrush)
                pen2 = QPen(QColor(56, 196, 184, 255))
                pen2.setWidthF(1.5)
                pen2.setStyle(Qt.DashLine)
                p.setPen(pen2)
                p.drawPath(_rounded_path(QRectF(1.5, 1.5, w - 3, h - 3), 8))
                f_tip = QFont()
                f_tip.setPointSizeF(max(6.5, font_size * 0.75 * 0.35))
                p.setFont(f_tip)
                p.setPen(QColor(56, 196, 184, 255))
                p.drawText(QPoint(10, 20), "拖动移动 · 拖角缩放")
        p.end()

    # ---- 队列消费 ----

    def _poll(self) -> None:
        changed = False
        try:
            while True:
                item = self.q.get_nowait()
                self.hist.append(item)
                if self.follow:
                    self.idx = len(self.hist) - 1
                changed = True
        except queue.Empty:
            pass
        if len(self.hist) > 400:
            self.hist = self.hist[-400:]
            self.idx = min(self.idx, len(self.hist) - 1)
        try:
            while True:
                item_id, text, _ms = self.uq.get_nowait()
                for it in reversed(self.hist[-80:]):
                    if it.item_id == item_id:
                        it.translation = text
                        if 0 <= self.idx < len(self.hist) \
                                and self.hist[self.idx] is it:
                            changed = True
                        break
        except queue.Empty:
            pass
        try:
            while True:
                _kind, text = self.pq.get_nowait()
                self.live = text
                if self.follow:
                    changed = True
        except queue.Empty:
            pass
        if changed:
            self._render()

    # ---- 控制动作 ----

    def toggle_visible(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.show()
            self._render()
        else:
            self.hide()
            self.panel.show()          # 隐藏后保留面板，便于恢复/退出
        self.panel.refresh()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.on_pause is not None:
            self.on_pause(self.paused)
        self._render()
        self.panel.refresh()

    def prev(self) -> None:
        if not self.hist:
            return
        self.idx = max(0, self.idx - 1)
        self.follow = self.idx >= len(self.hist) - 1
        self._render()

    def next(self) -> None:
        if not self.hist:
            return
        self.idx = min(len(self.hist) - 1, self.idx + 1)
        self.follow = self.idx >= len(self.hist) - 1
        self._render()

    def jump_latest(self) -> None:
        self.follow = True
        self.idx = len(self.hist) - 1
        self.panel.refresh()
        self._render()

    def quit(self) -> None:
        self.persist_geom()
        if self.on_exit is not None:
            self.on_exit()
        QApplication.quit()

    # ---- 透明度 / 持久化 ----

    def set_opacity(self, v: float, persist: bool = False) -> None:
        self.cfg.opacity = max(0.0, min(1.0, float(v)))
        self.update()
        if persist:
            self.persist_geom()

    def set_text_opacity(self, v: float, persist: bool = False) -> None:
        self.cfg.text_opacity = max(0.0, min(1.0, float(v)))
        self.update()
        if persist:
            self.persist_geom()

    def persist_geom(self) -> None:
        """几何/字号/透明度落盘。**不带颜色**——配色归 persist_scheme，
        拖动窗口/调字号不该把当前试的颜色固化下来（重启回默认的语义）。"""
        g = self.geometry()
        patch = {
            "width_ratio": round(self.width_ / max(1, self.mon_rect.width()), 3),
            "opacity": round(float(self.cfg.opacity), 2),
            "text_opacity": round(float(self.cfg.text_opacity), 2),
            "font_size": int(self.cfg.font_size),
            "pos_x": int(g.x()), "pos_y": int(g.y()),
        }
        persist_overlay(self.config_path, patch)

    def persist_scheme(self) -> None:
        """配色方案（文字/背景颜色）单独落盘：只由 ⚙「保存方案/恢复默认」调用。"""
        persist_overlay(self.config_path, {
            "text_color": str(self.cfg.text_color),
            "bg_color": str(self.cfg.bg_color),
        })

    # ---- 编辑模式：拖动移动 / 拖角缩放 ----

    def toggle_edit_mode(self) -> bool:
        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self._apply_passthrough(False)        # 编辑时必须能收鼠标
            self.panel.show()
            _log("位置调整模式：拖动窗口移动、拖右下角改宽度（再点「位置」结束）")
        else:
            self._apply_passthrough(self._passthrough)
            self.persist_geom()
            _log("已退出位置调整模式")
        self._render()
        self.panel.refresh()
        return self.edit_mode

    def mousePressEvent(self, e) -> None:  # noqa: N802
        """未穿透时可直接拖动/缩放（不需要先进「位置」模式）。

        穿透开启时窗口收不到鼠标事件（WS_EX_TRANSPARENT），必须先关穿透或点「位置」。
        """
        if e.button() != Qt.LeftButton or self._passthrough:
            return
        pos = e.position()
        resize = (pos.x() > self.width_ - 24 and pos.y() > self.height_ - 24)
        gpos = e.globalPosition().toPoint()
        g = self.geometry()
        self._drag = ("resize" if resize else "move",
                      gpos.x(), gpos.y(), self.width_, g.x(), g.y())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is None:
            return
        mode, ox, oy, ow, wx, wy = self._drag
        gp = e.globalPosition().toPoint()
        dx, dy = gp.x() - ox, gp.y() - oy
        if mode == "move":
            self.manual_pos = (int(wx + dx), int(wy + dy))
        else:
            # 宽度上限取"窗口当前所在那块屏"（跨屏拖动后也能继续拉宽）
            mon_w = self.mon_rect.width()
            scr = QGuiApplication.screenAt(QCursor.pos())
            if scr is not None:
                mon_w = scr.geometry().width()
            self.width_ = max(240, min(mon_w, int(ow + dx)))
            self.manual_pos = (int(wx), int(wy))
            self.height_ = self._measure_height(*self._current())
        self._apply_geometry()
        self._render()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.persist_geom()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.persist_geom()
        e.accept()

