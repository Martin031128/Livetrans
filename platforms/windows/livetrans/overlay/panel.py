"""控制面板 + ⚙ 设置弹窗 + 悬停提示气泡（TipBubble / _TipHost）。"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QColorDialog, QComboBox, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QWidget)

from ._common import (INK, PANEL_BG, PANEL_BTN, PANEL_BTN_HOVER,
                      SIGNAL, _log)
from .win32 import _set_no_activate, _set_topmost

class TipBubble(QLabel):
    """深色悬停提示气泡（对齐控制台 ui/widgets.ToolTip 的观感与节奏）。

    为什么不用原生 QToolTip：面板/设置弹窗是**永不激活**的窗（不抢焦点），
    原生 tooltip 在这种窗上的触发时机不可靠（WA_AlwaysShowToolTips 时灵时不灵）。
    这里由控件 Enter/Leave 事件驱动：悬停约 450ms 浮出，离开/点击即收起；
    提示文本仍存在各控件的 setToolTip 里（单一数据源，测试可直接断言）。
    """

    DELAY_MS = 450
    MAX_W = 380
    _instance: "TipBubble | None" = None

    def __init__(self) -> None:
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self._pending: QTimer | None = None
        self.setWordWrap(True)
        self.setMaximumWidth(self.MAX_W)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # 永不拦鼠标
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(f"""
            QLabel {{ background-color: #0b0f18; color: #c6cfdd;
                      border: 1px solid {SIGNAL}; border-radius: 6px;
                      padding: 7px 10px; font-size: 12px;
                      font-family: "Microsoft YaHei UI", "Microsoft YaHei",
                                   "PingFang SC", "Noto Sans CJK SC", sans-serif; }}
        """)

    @classmethod
    def instance(cls) -> "TipBubble":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def schedule(cls, widget: QWidget) -> None:
        """悬停进入：延迟浮出该控件的提示（控件没有提示文本则忽略）。"""
        cls.cancel()
        try:
            if not widget.toolTip():
                return
        except RuntimeError:                      # 控件已销毁
            return
        tip = cls.instance()
        tip._pending = QTimer(tip)
        tip._pending.setSingleShot(True)
        tip._pending.timeout.connect(lambda w=widget: cls.show_for(w))
        tip._pending.start(cls.DELAY_MS)

    @classmethod
    def cancel(cls) -> None:
        tip = cls._instance
        if tip is not None and tip._pending is not None:
            tip._pending.stop()
            tip._pending = None

    @classmethod
    def show_for(cls, widget: QWidget) -> None:
        """立刻显示提示（贴控件下方，出屏时翻到上方并钳回屏幕内）。"""
        tip = cls.instance()
        try:
            text = widget.toolTip()
        except RuntimeError:
            return
        if not text:
            return
        tip.setText(text)
        tip.adjustSize()
        gp = widget.mapToGlobal(QPoint(0, 0))
        pos = QPoint(gp.x() + 12, gp.y() + widget.height() + 6)
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            if pos.x() + tip.width() > a.right() - 4:
                pos.setX(max(a.left() + 4, a.right() - tip.width() - 4))
            if pos.y() + tip.height() > a.bottom() - 4:
                pos.setY(max(a.top() + 4, gp.y() - tip.height() - 6))
        tip.move(pos)
        tip.show()
        tip.raise_()

    @classmethod
    def hide_now(cls) -> None:
        cls.cancel()
        if cls._instance is not None:
            cls._instance.hide()



class _TipHost:
    """混入：给容器窗上的控件装悬停提示过滤器（Enter 延迟弹出，离开/点击收起），
    并手绘 9px 圆角底——QSS 的 QWidget 背景在 WA_TranslucentBackground 的
    Tool 窗上不可靠（实机实测设置弹窗整体透明），改用与字幕窗同一套画法。"""

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PANEL_BG))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 9, 9)
        p.end()

    def install_tip_filter(self) -> None:
        for w in (self, *self.findChildren(QWidget)):
            w.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 - Qt 命名
        t = ev.type()
        if t == QEvent.Enter:
            TipBubble.schedule(obj)
        elif t in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
            TipBubble.hide_now()
        return super().eventFilter(obj, ev)



class ControlPanel(_TipHost, QWidget):
    """控制面板（galgame 风格）：贴字幕框顶部、默认隐藏、鼠标悬停出现。

    字幕窗默认鼠标穿透（收不到鼠标事件），故悬停检测用**全局指针轮询**
    （QCursor.pos()）而不是 enter/leave 事件——穿透态下事件不会到达。
    （与 Linux 版用 Gdk.Seat 指针查询同理）
    """

    def __init__(self, ovw: OverlayWindow):
        super().__init__(None)
        self.ovw = ovw
        self.pinned = False
        self.shown = True
        self._leave_t: float | None = None

        # 面板窗体：无边框 + 置顶 + 不抢焦点 + 不进任务栏（同字幕窗）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # 面板四角透出画面
        # 面板永不激活（不抢焦点）：原生 tooltip 在这种窗上触发不可靠，
        # 悬停提示改走 _TipHost 过滤器 + TipBubble 深色气泡（对齐控制台 ToolTip）
        self.setStyleSheet(self._qss())

        box = QHBoxLayout(self)
        box.setContentsMargins(8, 7, 8, 7)
        box.setSpacing(6)

        def btn(text: str, cmd, tip: str = "") -> QPushButton:
            b = QPushButton(text, self)
            b.clicked.connect(cmd)
            if tip:
                b.setToolTip(tip)
            b.setFixedHeight(24)
            box.addWidget(b)
            return b

        self.btn_vis = btn("隐藏", ovw.toggle_visible,
                           "隐藏字幕窗（面板仍在，可再点「显示」恢复）")
        self.btn_pause = btn("暂停", ovw.toggle_pause,
                             "暂停识别与翻译：音频照常采集但丢弃，再点继续")
        btn("‹", ovw.prev, "上一条历史字幕（会自动暂停跟随）")
        btn("›", ovw.next, "下一条历史字幕")
        btn("最新", ovw.jump_latest, "回到最新字幕并恢复实时跟随")
        # 「位置」按钮已删：关穿透时字幕可直接拖动/拖角缩放，
        # 专门的模式按钮反而多余（用户反馈后移除；toggle_edit_mode 方法保留兼容）
        self.btn_pt = btn("穿透", self._toggle_pt,
                          "鼠标穿透：开启（高亮）时字幕完全不拦截鼠标，点击会落到背后的窗口；\n"
                          "关闭时字幕可直接用鼠标拖动、拖右下角改宽度")
        self.btn_pin = btn("固定", self.toggle_pin,
                           "固定面板常显（默认鼠标移开后自动隐藏）")

        # 声音来源（运行时切换，无需重启外挂）
        self.src_lab = QLabel("声音来源", self)
        self.src_lab.setProperty("role", "dim")
        self.src_lab.setToolTip("选择外挂识别哪路声音：内部=系统播放声（看视频），"
                                "外部=麦克风（自己说话）")
        box.addWidget(self.src_lab)
        self.src_combo = QComboBox(self)
        for cid, label in (("both", "两者"), ("internal", "内部（系统）"),
                           ("external", "外部（麦克风）")):
            self.src_combo.addItem(label, cid)
        self.src_combo.setToolTip(self.src_lab.toolTip())
        want = str(getattr(ovw.cfg, "source", "both"))
        i = self.src_combo.findData(want)
        self.src_combo.setCurrentIndex(i if i >= 0 else 0)
        self.src_combo.currentIndexChanged.connect(self._on_source)
        box.addWidget(self.src_combo)

        def name_val(name: str, tip: str) -> tuple[QLabel, QLabel]:
            """滑条前的「灰标签 + 青色数值」组合（对齐控制台 theme 的层级）。"""
            lab = QLabel(name, self)
            lab.setProperty("role", "dim")
            lab.setToolTip(tip)
            box.addWidget(lab)
            val = QLabel(self)
            val.setProperty("role", "val")
            val.setToolTip(tip)
            val.setFixedWidth(34)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            box.addWidget(val)
            return lab, val

        # 两根透明度滑条（背景透明度 / 文字透明度，完全独立）
        self.op_lab, self.op_val = name_val(
            "背景透明度", "背景（黑色圆角底）的透明度：越低越能看清背后的画面")
        self.op_val.setText(f"{ovw.cfg.opacity:.2f}")
        self.op_scale = QSlider(Qt.Horizontal, self)
        self.op_scale.setRange(0, 100)
        self.op_scale.setValue(int(float(ovw.cfg.opacity) * 100))
        self.op_scale.setFixedWidth(96)
        self.op_scale.setToolTip(self.op_lab.toolTip())
        self.op_scale.valueChanged.connect(self._on_bg)
        box.addWidget(self.op_scale)

        tx0 = float(getattr(ovw.cfg, "text_opacity", 1.0))
        self.tx_lab, self.tx_val = name_val(
            "文字透明度", "文字的透明度，与背景独立：背景很透时文字仍可保持清晰")
        self.tx_val.setText(f"{tx0:.2f}")
        self.tx_scale = QSlider(Qt.Horizontal, self)
        self.tx_scale.setRange(0, 100)
        self.tx_scale.setValue(int(tx0 * 100))
        self.tx_scale.setFixedWidth(96)
        self.tx_scale.setToolTip(self.tx_lab.toolTip())
        self.tx_scale.valueChanged.connect(self._on_tx)
        box.addWidget(self.tx_scale)

        self.btn_gear = btn("⚙", self.toggle_settings,
                            "外观设置：字体大小 / 宽度 / 文字与背景颜色")
        self.btn_exit = btn("退出", ovw.quit,
                            "退出外挂（会记住当前的位置/宽度/透明度）")
        self.btn_exit.setObjectName("exitBtn")   # 悬停危险色（见 _qss）

        self.install_tip_filter()             # 悬停提示气泡（含面板空白处）
        self.adjustSize()
        self.show()
        self._init_win32()
        self.refresh()

        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start(ovw.HOVER_MS)

    @staticmethod
    def _qss() -> str:
        """面板/设置弹窗共用样式（对应 Linux 版 _PANEL_CSS，配色一脉相承）。

        文字统一雅黑 UI 字体栈（Windows 默认 CJK 回退宋体，小字号发虚显旧）；
        按钮补按压态与「退出」危险色 hover；标签分 dim/val 两档
        （灰标签 + 青色数值，对齐控制台 theme 的层级）；滑条细槽 + 大圆点。
        """
        return f"""
        QWidget {{
            color: {INK}; font-size: 12px;
            font-family: "Microsoft YaHei UI", "Microsoft YaHei",
                         "PingFang SC", "Noto Sans CJK SC", sans-serif;
        }}
        QPushButton {{
            background-color: {PANEL_BTN}; color: {INK};
            border: 1px solid #2c3548; border-radius: 5px;
            padding: 4px 10px; font-size: 12px;
        }}
        QPushButton:hover {{ background-color: {PANEL_BTN_HOVER};
                             border-color: #3d4a68; }}
        QPushButton:pressed {{ background-color: #26314a; }}
        QPushButton:checked {{ background-color: {SIGNAL}; color: #0d1420;
                               border-color: {SIGNAL}; font-weight: bold; }}
        QPushButton#exitBtn:hover {{ background-color: #4a2733;
                                     border-color: #8a4256; color: #ffb3b3; }}
        QLabel {{ color: {INK}; font-size: 12px; background: transparent;
                  border: none; }}
        QLabel[role="dim"] {{ color: #8b93a7; }}
        QLabel[role="val"] {{ color: {SIGNAL}; font-weight: bold; }}
        QComboBox {{ background-color: {PANEL_BTN}; color: {INK};
                     border: 1px solid #2c3548; border-radius: 5px;
                     padding: 3px 8px; font-size: 12px; }}
        QComboBox:hover {{ background-color: {PANEL_BTN_HOVER}; }}
        QComboBox QAbstractItemView {{ background-color: {PANEL_BTN};
                                       color: {INK};
                                       border: 1px solid #2c3548;
                                       selection-background-color: {SIGNAL};
                                       selection-color: #0d1420; }}
        QSlider {{ background: transparent; }}
        QSlider::groove:horizontal {{ background: #39415a; height: 6px;
                                      border-radius: 3px; }}
        QSlider::sub-page:horizontal {{ background: {SIGNAL};
                                        border-radius: 3px; }}
        QSlider::handle:horizontal {{ background: #e8edf5; width: 14px;
                                      height: 14px; margin: -4px 0;
                                      border-radius: 7px; }}
        QSlider::handle:horizontal:hover {{ background: #ffffff; }}
        QSlider::sub-page:horizontal:disabled {{ background: #4a5470; }}
        """

    def _init_win32(self) -> None:
        _set_no_activate(self, True)
        _set_topmost(self, True)

    # ---- 悬停显隐 ----

    def _sub_rect(self) -> tuple:
        """字幕窗矩形；隐藏时用最近一次的位置（面板仍能定位、仍可点）。"""
        if not self.ovw.visible:
            g = getattr(self.ovw, "_last_geom", (0, 0, 0, 0))
            if g[2] > 0 and g[3] > 0:
                return g
            m = self.ovw.mon_rect
            return (m.x(), m.y() + m.height() - 160, self.ovw.width_, 100)
        g = self.ovw.geometry()
        return (g.x(), g.y(), g.width(), g.height())

    @staticmethod
    def _inside(x, y, rect, pad=0) -> bool:
        rx, ry, rw, rh = rect
        return (rx - pad <= x <= rx + rw + pad
                and ry - pad <= y <= ry + rh + pad)

    def _poll_hover(self) -> None:
        # 字幕窗隐藏时：面板常驻（否则鼠标无处可悬停，用户再也调不出面板，
        # 进程却仍在识别/翻译——本次用户困惑的根源）
        if not self.ovw.visible:
            if not self.shown:
                self.show()
            return
        pos = QCursor.pos()
        px, py = pos.x(), pos.y()
        over = self._inside(px, py, self._sub_rect(), self.ovw.HOVER_PAD)
        if self.shown:
            g = self.geometry()
            over = over or self._inside(px, py,
                                        (g.x(), g.y(), g.width(), g.height()), 4)
        want = (over or self.pinned or self.ovw.edit_mode
                or time.monotonic() < self.ovw._start_until)
        if want:
            self._leave_t = None
            if not self.shown:
                self.show()
                self.shown = True
                self.reposition()
        elif self.ovw.cfg.panel_auto_hide and self.shown:
            if self._leave_t is None:
                self._leave_t = time.monotonic()
            elif (time.monotonic() - self._leave_t) * 1000 \
                    > self.ovw.AUTO_HIDE_MS:
                self.hide()
                self.shown = False

    def show(self) -> None:  # noqa: D102
        super().show()
        self.shown = True
        self.reposition()

    def hide(self) -> None:  # noqa: D102
        TipBubble.hide_now()                  # 面板收起时提示气泡一并收起
        super().hide()
        self.shown = False

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.refresh()
        if self.pinned:
            self.show()

    def reposition(self) -> None:
        """贴在字幕框顶部（空间不足则落到框内顶部），与字幕等宽。"""
        if not self.shown:
            return
        sx, sy, sw, _sh = self._sub_rect()
        mh = self.sizeHint().height()
        y = sy - mh - 4
        if y < 2:                              # 字幕贴屏幕顶部：落到框内顶部
            y = sy + 4
        self.resize(max(240, sw), mh)
        self.move(max(0, sx), max(0, y))

    # ---- 面板动作 ----

    def _toggle_pt(self) -> None:
        self.ovw.set_click_through(not self.ovw._passthrough)
        self.refresh()

    def toggle_settings(self) -> None:
        """⚙ 外观设置弹窗（单例：开着就关掉）。"""
        existing = getattr(self, "_settings_popup", None)
        if existing is not None and existing.isVisible():
            existing.hide()
            return
        self._settings_popup = SettingsPopup(self.ovw)
        # 贴在面板右侧（放不下则贴左侧）
        g = self.geometry()
        sp = self._settings_popup
        x = g.x() + g.width() + 6
        if x + sp.width() > self.ovw.mon_rect.right():
            x = max(0, g.x() - sp.width() - 6)
        sp.move(max(0, x), max(0, g.y()))
        sp.show()

    def _on_bg(self, v: int) -> None:
        val = v / 100.0
        self.ovw.set_opacity(val)
        self.op_val.setText(f"{val:.2f}")

    def _on_tx(self, v: int) -> None:
        val = v / 100.0
        self.ovw.set_text_opacity(val)
        self.tx_val.setText(f"{val:.2f}")

    def _on_source(self, _i: int) -> None:
        cid = self.src_combo.currentData()
        if cid and cid != str(getattr(self.ovw.cfg, "source", "both")):
            self.ovw.set_source(cid)

    def refresh(self) -> None:
        """按钮文字与开启态高亮（开关一目了然，对应 Linux 版 _set_state）。"""
        ovw = self.ovw

        def st(b: QPushButton, on: bool) -> None:
            b.setCheckable(True)
            b.setChecked(on)

        self.btn_vis.setText("显示" if not ovw.visible else "隐藏")
        st(self.btn_vis, not ovw.visible)
        self.btn_pause.setText("继续" if ovw.paused else "暂停")
        st(self.btn_pause, ovw.paused)
        self.btn_pt.setText("穿透 开" if ovw._passthrough else "穿透 关")
        st(self.btn_pt, ovw._passthrough)
        self.btn_pin.setText("自动" if self.pinned else "固定")
        st(self.btn_pin, self.pinned)
        op = round(float(ovw.cfg.opacity) * 100)
        if self.op_scale.value() != op:
            self.op_scale.blockSignals(True)
            self.op_scale.setValue(op)
            self.op_scale.blockSignals(False)
        self.op_val.setText(f"{float(ovw.cfg.opacity):.2f}")
        tx0 = float(getattr(ovw.cfg, "text_opacity", 1.0))
        tx = round(tx0 * 100)
        if self.tx_scale.value() != tx:
            self.tx_scale.blockSignals(True)
            self.tx_scale.setValue(tx)
            self.tx_scale.blockSignals(False)
        self.tx_val.setText(f"{tx0:.2f}")


class SettingsPopup(_TipHost, QWidget):
    """外观设置弹窗（面板 ⚙ 打开）：字体大小 / 宽度 / 文字与背景颜色。

    滑条改动**实时生效**，松手写回 config.yaml；颜色改动仅本会话生效——
    点「保存方案」才把文字/背景颜色写为启动方案，「恢复默认」回到白字黑底
    并清除方案。悬停提示与面板同走 TipBubble 气泡。
    """

    def __init__(self, ovw: OverlayWindow):
        super().__init__(None)
        self.ovw = ovw
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # 弹窗四角透出画面
        self.setStyleSheet(ControlPanel._qss())

        # 无边框 + 不激活窗：没有标题栏可拉，拖动交给下面的鼠标事件实现
        self._drag_off: QPoint | None = None

        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(9)

        def lab(text: str, row: int, tip: str = "") -> QLabel:
            l = QLabel(text, self)
            l.setProperty("role", "dim")      # 灰色小标签（见 _qss）
            if tip:
                l.setToolTip(tip)
            grid.addWidget(l, row, 0)
            return l

        # ① 字体大小（注：QGridLayout 单参 addWidget 是"每件一行"，必须显式给行列）
        lab("字体大小", 0, "译文正文字号（原文小字按比例缩小）")
        self.font_scale = QSlider(Qt.Horizontal, self)
        self.font_scale.setRange(16, 56)
        self.font_scale.setValue(int(ovw.cfg.font_size))
        self.font_scale.setFixedWidth(160)
        self.font_scale.setToolTip("拖动实时预览，松手保存")
        self.font_scale.valueChanged.connect(self._on_font)
        self.font_scale.sliderReleased.connect(self._persist)
        grid.addWidget(self.font_scale, 0, 1)
        self.font_lab = QLabel(str(int(ovw.cfg.font_size)), self)
        self.font_lab.setProperty("role", "val")
        self.font_lab.setMinimumWidth(34)
        self.font_lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.font_lab, 0, 2)

        # ② 宽度（屏宽比例）
        lab("字幕宽度", 1, "占屏幕宽度的比例")
        self.w_scale = QSlider(Qt.Horizontal, self)
        self.w_scale.setRange(20, 100)
        self.w_scale.setValue(int(float(ovw.cfg.width_ratio) * 100))
        self.w_scale.setFixedWidth(160)
        self.w_scale.setToolTip("拖动实时预览，松手保存")
        self.w_scale.valueChanged.connect(self._on_width)
        self.w_scale.sliderReleased.connect(self._persist)
        grid.addWidget(self.w_scale, 1, 1)
        self.w_lab = QLabel(f"{float(ovw.cfg.width_ratio) * 100:.0f}%", self)
        self.w_lab.setProperty("role", "val")
        self.w_lab.setMinimumWidth(34)
        self.w_lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.w_lab, 1, 2)

        # ③ 文字颜色
        lab("文字颜色", 2, "译文文字的颜色（透明度归主面板「文字透明度」滑条）")
        self.btn_text_col = QPushButton(self._swatch(ovw.cfg.text_color), self)
        self.btn_text_col.setToolTip("点击打开取色器")
        self.btn_text_col.clicked.connect(
            lambda: self._pick_color("text_color", self.btn_text_col))
        grid.addWidget(self.btn_text_col, 2, 1)

        # ④ 背景颜色
        lab("背景颜色", 3, "字幕圆角底的底色（透明度归主面板「背景透明度」滑条）")
        self.btn_bg_col = QPushButton(self._swatch(ovw.cfg.bg_color), self)
        self.btn_bg_col.setToolTip("点击打开取色器")
        self.btn_bg_col.clicked.connect(
            lambda: self._pick_color("bg_color", self.btn_bg_col))
        grid.addWidget(self.btn_bg_col, 3, 1)
        grid.setColumnStretch(1, 1)

        # ⑤ 配色方案：颜色改动默认只在本会话生效，重启回到默认；
        #    「保存方案」才把当前文字/背景颜色固化为启动方案
        self.btn_save = QPushButton("保存方案", self)
        self.btn_save.setFixedHeight(24)
        self.btn_save.setToolTip(
            "把当前文字/背景颜色存为启动方案：下次启动沿用；\n"
            "不保存的话颜色只在本次生效，重启回到默认（或上次保存的方案）")
        self.btn_save.clicked.connect(self._save_scheme)
        grid.addWidget(self.btn_save, 4, 0)
        self.btn_reset = QPushButton("恢复默认", self)
        self.btn_reset.setFixedHeight(24)
        self.btn_reset.setToolTip("文字/背景恢复出厂配色（白字黑底），并清除已保存的方案")
        self.btn_reset.clicked.connect(self._reset_scheme)
        grid.addWidget(self.btn_reset, 4, 1)

        # ⑥ 说明 + 关闭
        hint = QLabel("透明度用主面板的「背景透明度 / 文字透明度」滑条调；"
                      "颜色重启即回默认，「保存方案」后保留", self)
        hint.setStyleSheet("color: #8b93a7; font-size: 11px;")
        grid.addWidget(hint, 5, 0, 1, 2)
        close = QPushButton("完成", self)
        close.setFixedHeight(24)
        close.clicked.connect(self.hide)
        grid.addWidget(close, 4, 2)

        self.setToolTip("按住空白处或文字可拖动本窗口")
        self.adjustSize()
        self.install_tip_filter()             # 悬停提示气泡（同主面板）

    @staticmethod
    def _swatch(hex_color: str) -> str:
        """按钮文字：色块 + 当前色值。"""
        c = QColor(hex_color)
        return f"  {c.name().upper()}  " if c.isValid() else "  选择颜色  "

    # ---- 拖动窗口（无边框 + 不激活：没有标题栏，按住空白/文字拖） ----

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt 命名
        """按下左键记录偏移（空白处与 QLabel 都会落到这里；滑条/按钮走自身交互）。"""
        if e.button() == Qt.LeftButton:
            self._drag_off = (e.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 - Qt 命名
        """按住拖动（Qt 按下即隐式抓取鼠标，拖出窗口边界也能继续移动）。"""
        if self._drag_off is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt 命名
        self._drag_off = None
        super().mouseReleaseEvent(e)

    def hide(self) -> None:  # noqa: D102 - 收起时一并收起提示气泡
        TipBubble.hide_now()
        super().hide()

    def _on_font(self, v: int) -> None:
        self.ovw.cfg.font_size = int(v)
        self.font_lab.setText(str(v))
        self.ovw._render()

    def _on_width(self, v: int) -> None:
        ratio = v / 100.0
        self.ovw.cfg.width_ratio = ratio
        self.w_lab.setText(f"{v}%")
        self.ovw.width_ = max(240, int(self.ovw.mon_rect.width() * ratio))
        self.ovw._apply_geometry()
        self.ovw._render()

    def _pick_color(self, field: str, btn: QPushButton) -> None:
        cur = QColor(getattr(self.ovw.cfg, field))
        # 不开 ShowAlphaChannel：取色器里的 Alpha 存不进 hex（col.name() 会丢弃），
        # 用户拖了 Alpha 却"毫无反应"反而误导——透明度统一归主面板两根透明度滑条管。
        col = QColorDialog.getColor(
            cur if cur.isValid() else QColor("#ffffff"), self, "选择颜色")
        if not col.isValid():
            return
        setattr(self.ovw.cfg, field, col.name())
        btn.setText(self._swatch(col.name()))
        self.ovw.update()
        _log("颜色已改（仅本会话生效；要保留请点「保存方案」）")

    def _persist(self) -> None:
        self.ovw.persist_geom()
        _log(f"外观已保存：字号 {self.ovw.cfg.font_size}，"
             f"宽度 {float(self.ovw.cfg.width_ratio) * 100:.0f}%"
             "（配色用「保存方案」保存）")

    # ---- 配色方案 ----

    def _save_scheme(self) -> None:
        """把当前文字/背景颜色写为启动方案（几何/字号/透明度照旧各自落盘）。"""
        self.ovw.persist_scheme()
        _log(f"配色方案已保存：文字 {self.ovw.cfg.text_color}，"
             f"背景 {self.ovw.cfg.bg_color}（下次启动沿用）")

    def _reset_scheme(self) -> None:
        """恢复出厂配色（白字/黑底），并覆盖掉已保存的方案。"""
        self.ovw.cfg.text_color = "#ffffff"   # 与 OverlayConfig 出厂默认一致
        self.ovw.cfg.bg_color = "#000000"
        self.btn_text_col.setText(self._swatch(self.ovw.cfg.text_color))
        self.btn_bg_col.setText(self._swatch(self.ovw.cfg.bg_color))
        self.ovw.update()
        self.ovw.persist_scheme()
        _log("配色已恢复默认（白字/黑底），已保存的方案一并清除")
