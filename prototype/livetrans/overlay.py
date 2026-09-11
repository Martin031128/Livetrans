"""字幕外挂（GTK3）：无边框、逐像素透明的悬浮字幕窗 + galgame 式悬停控制面板。

用法：
    python3 -m livetrans.overlay [--config config.yaml]

由控制台「启动外挂」按钮拉起。复用主程序同一套「音频 → SenseVoice → LLM 翻译」
管线（ASRWorker / TranslatorWorker 原样复用），只把渲染层换成悬浮窗：
`OverlayWindow` 实现了与 `SubtitleWindow` 相同的接口
（post / update_translation / set_partial / set_level / set_status）。

为什么用 GTK3（而非 Tk）：
- Tk 的 `-alpha` 是整窗属性，没有逐元素/逐像素 alpha，无法做到
  "背景透明但文字不透明"；GTK3 有 RGBA 视觉 + cairo，可分别设 alpha。
- cairo 圆角是真抗锯齿；文字排版用 Pango（自动换行 + 精确测高）。

能力：
- 无边框 / 置顶（可配）/ 默认鼠标穿透（X11 输入区域置空，Wayland 用
  set_pass_through）/ 不抢焦点（从不 focus_acquire）；
- 默认主屏底部居中：宽 = 屏宽 x width_ratio，距底 = bottom_offset（字幕安全区）；
- 背景透明度（opacity）与文字透明度（text_opacity）**完全独立**；
- 控制面板贴在字幕框顶部、默认隐藏、鼠标悬停出现（指针轮询，穿透态同样有效）：
  隐藏/显示、暂停/继续、‹上一条/›下一条/最新、位置（拖动·缩放）、穿透、固定、退出
  + 背景/文字两根透明度滑条；
- 位置/宽度/透明度写回 config.yaml 的 overlay 段，下次启动沿用。
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import yaml  # noqa: E402

from .asr import ASRWorker, SenseVoiceASR  # noqa: E402
from .capture import (AudioCapture, ParecCapture, SourceInfo,  # noqa: E402
                      default_monitor_source, find_monitor_device,
                      friendly_source_name)
from .config import AppConfig, OverlayConfig, load_config  # noqa: E402
from .paths import ensure_data_files  # noqa: E402
from .main import TranslatorWorker, _make_fallback_builder  # noqa: E402
from .speaker import build_tracker  # noqa: E402
from .subtitle import DisplayItem  # noqa: E402
from .translate import (LLMTranslator, ensure_local_backend,  # noqa: E402
                        is_local_base_url)

INK = "#dce3ee"
SIGNAL = "#39c5b8"

_PANEL_CSS = b"""
window.ovpanel { background-color: #1c2230; }
window.ovpanel button {
    background-image: none; background-color: #232a3a; color: #dce3ee;
    border: none; border-radius: 4px; padding: 4px 9px; font-size: 12px;
}
window.ovpanel button:hover { background-color: #2f394f; }
window.ovpanel button.on { background-color: #39c5b8; color: #0d1420;
                           font-weight: bold; }
window.ovpanel button.on:hover { background-color: #4ad8cb; }
window.ovpanel label { color: #dce3ee; font-size: 12px; }
window.ovpanel scale trough { background-color: #5b6785; min-height: 10px;
                              border-radius: 5px; }
window.ovpanel scale highlight { background-color: #39c5b8; border-radius: 5px; }
window.ovpanel scale slider { background-color: #dce3ee; min-width: 12px;
                              min-height: 12px; }
"""


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def persist_overlay(cfg_path: Path | None, patch: dict) -> None:
    """把外挂的几何/透明度写回 config.yaml 的 overlay 段（保留其余内容）。"""
    if cfg_path is None:
        return
    try:
        raw: dict = {}
        if cfg_path.is_file():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        raw.setdefault("overlay", {})
        raw["overlay"].update(patch)
        cfg_path.write_text(
            "# LiveTrans 配置（overlay 段由字幕外挂自动维护）\n"
            + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"保存外挂设置失败: {e}")


def _rounded_path(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _layout(cr, text: str, size: int, width: int):
    lay = PangoCairo.create_layout(cr)
    lay.set_text(text or " ", -1)
    lay.set_width(int(max(20, width) * Pango.SCALE))
    lay.set_alignment(Pango.Alignment.CENTER)
    lay.set_wrap(Pango.WrapMode.WORD_CHAR)
    lay.set_font_description(Pango.FontDescription(f"sans {size}"))
    return lay


def _draw_panel(cr, width: int, height: int, main: str, src: str,
                bg_alpha: float, text_alpha: float, font_size: int,
                pad_x: int, pad_y: int) -> float:
    """绘制圆角半透明底 + 原文小字 + 译文大字；返回文本块高度。

    bg_alpha / text_alpha 完全独立（逐像素 alpha）。
    """
    r = max(6.0, font_size * 0.5)
    # 轻微阴影：只在边缘描一圈（整块叠加会让实际不透明度偏高）
    _rounded_path(cr, 1.5, 4.5, width - 3, height - 8, r)
    cr.set_source_rgba(0, 0, 0, min(1.0, bg_alpha * 0.7))
    cr.set_line_width(5)
    cr.stroke()
    # 背景圆角矩形（内部 alpha 严格等于 bg_alpha）
    _rounded_path(cr, 3, 3, width - 6, height - 9, r)
    cr.set_source_rgba(0, 0, 0, bg_alpha)
    cr.fill()

    inner_w = max(40, width - 2 * pad_x)
    lay_src = (_layout(cr, src, max(9, int(font_size * 0.55)), inner_w)
               if src else None)
    lay_main = _layout(cr, main, font_size, inner_w)
    h_src = (lay_src.get_pixel_size()[1] + int(font_size * 0.25)
             if lay_src is not None else 0)
    h_main = lay_main.get_pixel_size()[1]
    y = max(2.0, (height - (h_src + h_main)) / 2)
    if lay_src is not None:
        cr.set_source_rgba(0.78, 0.81, 0.87, text_alpha)
        cr.move_to(pad_x, y)
        PangoCairo.show_layout(cr, lay_src)
        y += h_src
    cr.set_source_rgba(1, 1, 1, text_alpha)
    cr.move_to(pad_x, y)
    PangoCairo.show_layout(cr, lay_main)
    return h_src + h_main


class OverlayWindow(Gtk.Window):
    """悬浮字幕窗：与 SubtitleWindow 同接口（供 TranslatorWorker/ASRWorker 复用）。"""

    POLL_MS = 60            # 队列消费间隔
    HOVER_MS = 150          # 悬停检测间隔
    AUTO_HIDE_MS = 700      # 鼠标离开后自动隐藏面板
    HOVER_PAD = 8

    def __init__(self, ov: OverlayConfig, on_pause=None, on_exit=None,
                 config_path: Path | None = None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.cfg = ov
        self.config_path = config_path
        self.on_pause = on_pause
        self.on_exit = on_exit
        self.on_source_change = None      # 由 run_overlay 注入：切换音频来源
        # 管线线程只往队列里塞，UI 线程轮询消费（与 Tk 版一致）
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
        self.manual_pos: tuple[int, int] | None = (
            (ov.pos_x, ov.pos_y) if ov.pos_x >= 0 and ov.pos_y >= 0 else None)
        self._passthrough = bool(ov.click_through)
        self._drag: tuple | None = None
        self._start_until = time.monotonic() + 4.0

        # ---- 窗口本体 ----
        self.set_decorated(False)
        self.set_keep_above(bool(ov.always_on_top))
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self._apply_visual()
        self.connect("draw", self._on_draw)
        self.connect("screen-changed", lambda *_: self._apply_visual())
        self.connect("realize", self._on_realize)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self._drag_start)
        self.connect("motion-notify-event", self._drag_move)
        self.connect("button-release-event", self._drag_end)

        self._last_geom = (0, 0, 0, 0)     # 最近一次字幕窗几何（隐藏时给面板定位）
        self.mon, self.mon_name = self._pick_monitor()
        self.width = max(360, int(self.mon.width *
                                  max(0.2, min(1.0, ov.width_ratio))))
        self.height = max(70, int(ov.font_size * 2.4))
        self._place()
        self.show_all()
        self.panel = ControlPanel(self)
        self._render()
        GLib.timeout_add(self.POLL_MS, self._poll)
        _log(f"外挂字幕窗已创建（GTK3）：{self.width}x{self.height}，"
             f"{self.mon_name} {self.mon.width}x{self.mon.height}"
             f"（置顶={'开' if ov.always_on_top else '关'}，"
             f"穿透={'开' if self._passthrough else '关'}）")

    # ---- 窗口设置 ----

    def _pick_monitor(self):
        """用哪块屏（副屏适配）：配置 0=主屏 / 1..N=对应序号 / -1=鼠标所在屏。"""
        display = Gdk.Display.get_default()
        if display is None:                         # 理论上不会发生
            from gi.repository import Gdk as _G
            return _G.Rectangle(), "屏幕"
        want = int(getattr(self.cfg, "monitor", -1) or -1)
        mon = None
        label = ""
        if want > 0:
            mon = display.get_monitor(want - 1)     # 1 = 第一块屏（0 号监视器）
            label = f"屏幕 {want}"
        elif want == 0:
            mon = display.get_primary_monitor()
            label = "主屏"
        if mon is None:                             # -1（自动）：鼠标所在屏
            seat = display.get_default_seat()
            ptr = seat.get_pointer() if seat is not None else None
            if ptr is not None:
                _win, x, y = ptr.get_position()
                mon = display.get_monitor_at_point(x, y)
                label = "鼠标所在屏"
        if mon is None:
            mon = display.get_primary_monitor() or display.get_monitor(0)
            label = "主屏"
        return mon.get_geometry(), label

    def _apply_visual(self) -> None:
        screen = self.get_screen()
        vis = screen.get_rgba_visual()
        if vis is not None:
            self.set_visual(vis)                 # ARGB 视觉 = 逐像素透明

    def _on_realize(self, *_a) -> None:
        self._apply_passthrough(self._passthrough)

    def _desktop_bounds(self):
        """所有显示器的并集（Gdk.Rectangle）：跨屏拖动时用它做边界。"""
        display = Gdk.Display.get_default()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, 0, 0
        if display is None:
            return rect
        for i in range(display.get_n_monitors()):
            g = display.get_monitor(i).get_geometry()
            if rect.width == 0:
                rect.x, rect.y, rect.width, rect.height = g.x, g.y, g.width, g.height
                continue
            x2 = max(rect.x + rect.width, g.x + g.width)
            y2 = max(rect.y + rect.height, g.y + g.height)
            rect.x = min(rect.x, g.x)
            rect.y = min(rect.y, g.y)
            rect.width, rect.height = x2 - rect.x, y2 - rect.y
        return rect

    def _place(self) -> None:
        """默认底部居中（字幕安全区）；手动拖过则用记住的位置。"""
        m = self.mon
        if self.manual_pos:
            # 可跨屏：边界用所有显示器的并集，而不是当前这一块屏
            d = self._desktop_bounds()
            x = max(d.x, min(d.x + d.width - self.width, self.manual_pos[0]))
            y = max(d.y, min(d.y + d.height - self.height, self.manual_pos[1]))
        else:
            x = m.x + (m.width - self.width) // 2
            bottom = m.y + int(m.height * (1.0 - max(0.0, min(0.5,
                                                              self.cfg.bottom_offset))))
            y = max(m.y, bottom - self.height)
        self.resize(self.width, self.height)
        self.move(x, y)
        self._last_geom = (x, y, self.width, self.height)   # 隐藏后面板定位用
        if hasattr(self, "panel"):
            self.panel.reposition()

    def set_click_through(self, enable: bool) -> None:
        self._passthrough = enable
        self._apply_passthrough(enable)

    def _apply_passthrough(self, enable: bool) -> None:
        gw = self.get_window()
        if gw is None:
            return
        try:
            if enable:
                region = cairo.Region()                     # 空输入区域 = 全穿透
            else:
                # PyGObject 不接受 None，用"整窗矩形"恢复默认输入区域
                region = cairo.Region(cairo.RectangleInt(
                    0, 0, max(1, self.width), max(1, self.height)))
            gw.input_shape_combine_region(region, 0, 0)
            if hasattr(gw, "set_pass_through"):              # Wayland 通用
                gw.set_pass_through(enable)
        except Exception as e:  # noqa: BLE001
            _log(f"穿透设置失败: {e}")

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
                panel.src_combo.set_sensitive(not on)
                panel.src_lab.set_text("音频（跟随主程序）" if on else "音频")
            except Exception:  # noqa: BLE001 - 面板未建好也不影响显示
                pass

    # ---- 绘制 ----

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

    def _measure(self, main: str, src: str) -> int:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, 2000)
        cr = cairo.Context(surf)
        h = _draw_panel(cr, self.width, 2000, main, src, self.cfg.opacity,
                        self.cfg.text_opacity, self.cfg.font_size,
                        self.cfg.margin_x, self.cfg.margin_y)
        return int(h + 2 * self.cfg.margin_y + 12)

    def _render(self) -> None:
        main, src = self._current()
        need = self._measure(main, src)
        if abs(need - self.height) > 2:
            self.height = max(64, need)
            self._place()
        self.queue_draw()

    def _on_draw(self, _w, cr):
        main, src = self._current()
        _draw_panel(cr, self.width, self.height, main, src, self.cfg.opacity,
                    self.cfg.text_opacity, self.cfg.font_size,
                    self.cfg.margin_x, self.cfg.margin_y)
        # 可拖动时的右下角缩放标（未穿透即可用；编辑模式另加虚线框与文字提示）
        if self._passthrough:
            return False
        cr.set_source_rgba(0.22, 0.77, 0.72, 0.55 if not self.edit_mode
                           else 1.0)
        cr.move_to(self.width - 22, self.height - 8)
        cr.line_to(self.width - 8, self.height - 22)
        cr.line_to(self.width - 8, self.height - 8)
        cr.close_path()
        cr.fill()
        if self.edit_mode:
            cr.set_source_rgba(0.22, 0.77, 0.72, 1.0)
            cr.set_dash([5, 4])
            cr.set_line_width(1.5)
            _rounded_path(cr, 1.5, 1.5, self.width - 3, self.height - 3, 8)
            cr.stroke()
            cr.set_dash([])
            lay = _layout(cr, "拖动移动 · 拖角缩放", max(9, int(
                self.cfg.font_size * 0.35)), self.width - 40)
            cr.move_to(10, 6)
            PangoCairo.show_layout(cr, lay)
        return False

    # ---- 队列消费 ----

    def _poll(self) -> bool:
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
        return True

    # ---- 控制动作 ----

    def toggle_visible(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.show_all()
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
        Gtk.main_quit()

    # ---- 透明度 / 持久化 ----

    def set_opacity(self, v: float, persist: bool = False) -> None:
        self.cfg.opacity = max(0.0, min(1.0, float(v)))
        self.queue_draw()
        if persist:
            self.persist_geom()

    def set_text_opacity(self, v: float, persist: bool = False) -> None:
        self.cfg.text_opacity = max(0.0, min(1.0, float(v)))
        self.queue_draw()
        if persist:
            self.persist_geom()

    def persist_geom(self) -> None:
        win = self.get_window()
        x, y = self.get_position()
        patch = {
            "width_ratio": round(self.width / max(1, self.mon.width), 3),
            "opacity": round(float(self.cfg.opacity), 2),
            "text_opacity": round(float(self.cfg.text_opacity), 2),
            "pos_x": int(x), "pos_y": int(y),
        }
        persist_overlay(self.config_path, patch)

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

    def _drag_start(self, _w, e) -> bool:
        """未穿透时可直接拖动/缩放（不需要先进「位置」模式）。

        穿透开启时窗口收不到鼠标事件，必须先关穿透或点「位置」。
        """
        if e.button != 1 or self._passthrough:
            return False
        w, h = self.width, self.height
        resize = e.x > w - 24 and e.y > h - 24
        x, y = self.get_position()
        self._drag = ("resize" if resize else "move", e.x_root, e.y_root,
                      w, x, y)
        return True

    def _drag_move(self, _w, e) -> bool:
        if self._drag is None:
            return False
        mode, ox, oy, ow, wx, wy = self._drag
        dx, dy = e.x_root - ox, e.y_root - oy
        if mode == "move":
            self.manual_pos = (int(wx + dx), int(wy + dy))
        else:
            # 宽度上限取"窗口当前所在那块屏"（跨屏拖动后也能继续拉宽）
            display = Gdk.Display.get_default()
            seat = display.get_default_seat() if display is not None else None
            ptr = seat.get_pointer() if seat is not None else None
            mon_w = self.mon.width
            if ptr is not None:
                _w, px, py = ptr.get_position()
                mon = display.get_monitor_at_point(px, py) if display else None
                if mon is not None:
                    mon_w = mon.get_geometry().width
            self.width = max(240, min(mon_w, int(ow + dx)))
            self.manual_pos = (int(wx), int(wy))
            self.height = self._measure(*self._current())
        self._place()
        self._render()
        return True

    def _drag_end(self, _w, _e) -> bool:
        if self._drag is not None:
            self._drag = None
            self.persist_geom()
        return False


class ControlPanel:
    """控制面板（galgame 风格）：贴字幕框顶部、默认隐藏、鼠标悬停出现。

    字幕窗默认鼠标穿透（收不到鼠标事件），故悬停检测用**全局指针轮询**
    （Gdk.Seat 指针位置）而不是 enter/leave 事件——穿透态下事件不会到达。
    """

    def __init__(self, ovw: OverlayWindow):
        self.ovw = ovw
        self.pinned = False
        self.shown = True
        self._leave_t: float | None = None
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_accept_focus(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.win.get_style_context().add_class("ovpanel")
        css = Gtk.CssProvider()
        css.load_from_data(_PANEL_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            self.win.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(spacing=4, margin=6)
        self.win.add(box)

        def btn(text: str, cmd, tip: str = ""):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda *_: cmd())
            if tip:
                b.set_tooltip_text(tip)
            box.pack_start(b, False, False, 0)
            return b

        self.btn_vis = btn("隐藏", ovw.toggle_visible,
                           "隐藏字幕窗（面板仍在，可再点「显示」恢复）")
        self.btn_pause = btn("暂停", ovw.toggle_pause,
                             "暂停识别与翻译：音频照常采集但丢弃，再点继续")
        btn("‹", ovw.prev, "上一条历史字幕（会自动暂停跟随）")
        btn("›", ovw.next, "下一条历史字幕")
        btn("最新", ovw.jump_latest, "回到最新字幕并恢复实时跟随")
        self.btn_edit = btn("位置", ovw.toggle_edit_mode,
                            "位置调整模式：高亮可拖动区域并临时关闭穿透；"
                            "再点结束（关闭穿透时可直接拖动，无需进此模式）")
        self.btn_pt = btn("穿透", lambda: (ovw.set_click_through(
            not ovw._passthrough), self.refresh()),
            "鼠标穿透：开启（高亮）时字幕完全不拦截鼠标，点击会落到背后的窗口；\n"
            "关闭时字幕可直接用鼠标拖动、拖右下角改宽度")
        self.btn_pin = btn("固定", self.toggle_pin,
                           "固定面板常显（默认鼠标移开后自动隐藏）")

        # 音频来源（运行时切换，无需重启外挂）
        src_lab = Gtk.Label(label="音频")
        src_lab.set_tooltip_text("选择外挂识别哪路声音：内部=系统播放声（看视频），"
                                 "外部=麦克风（自己说话）")
        self.src_lab = src_lab
        box.pack_start(src_lab, False, False, 0)
        self.src_combo = Gtk.ComboBoxText()
        for cid, label in (("both", "两者"), ("internal", "内部（系统）"),
                           ("external", "外部（麦克风）")):
            self.src_combo.append(cid, label)
        self.src_combo.set_tooltip_text(src_lab.get_tooltip_text())
        self.src_combo.set_active_id(str(getattr(ovw.cfg, "source", "both")))
        self.src_combo.connect("changed", self._on_source)
        box.pack_start(self.src_combo, False, False, 0)

        def slider(label: str, lo: float, val: float, cb, tip: str) -> Gtk.Scale:
            lab = Gtk.Label(label=f"{label} {val:.2f}")
            lab.set_tooltip_text(tip)
            box.pack_start(lab, False, False, 0)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, 1.0,
                                          0.05)
            sc.set_value(val)
            sc.set_size_request(96, -1)
            sc.set_draw_value(False)
            sc.set_tooltip_text(tip)
            sc.get_style_context().add_class("ovpanel")
            sc.connect("value-changed", cb)
            box.pack_start(sc, False, False, 0)
            return sc

        self.op_scale = slider("背景", 0.0, float(ovw.cfg.opacity),
                               self._on_bg,
                               "背景（黑色圆角底）的透明度：越低越能看清背后的画面")
        self.op_val = Gtk.Label(label=f"{ovw.cfg.opacity:.2f}")
        self.op_val.set_tooltip_text("当前背景透明度")
        box.pack_start(self.op_val, False, False, 0)
        self.tx_scale = slider("文字", 0.0,
                               float(getattr(ovw.cfg, "text_opacity", 1.0)),
                               self._on_tx,
                               "文字的透明度，与背景独立：背景很透时文字仍可保持清晰")
        self.tx_val = Gtk.Label(
            label=f"{getattr(ovw.cfg, 'text_opacity', 1.0):.2f}")
        self.tx_val.set_tooltip_text("当前文字透明度")
        box.pack_start(self.tx_val, False, False, 0)
        btn("退出", ovw.quit, "退出外挂（会记住当前的位置/宽度/透明度）")

        self.win.show_all()
        self.refresh()                      # 初始化按钮文字与开启态高亮
        GLib.timeout_add(ovw.HOVER_MS, self._poll_hover)

    # ---- 悬停显隐 ----

    @staticmethod
    def _pointer_pos():
        display = Gdk.Display.get_default()
        seat = display.get_default_seat() if display is not None else None
        ptr = seat.get_pointer() if seat is not None else None
        if ptr is None:
            return None
        _scr, x, y = ptr.get_position()
        return x, y

    @staticmethod
    def _inside(x, y, rect, pad=0) -> bool:
        rx, ry, rw, rh = rect
        return (rx - pad <= x <= rx + rw + pad
                and ry - pad <= y <= ry + rh + pad)

    def _sub_rect(self) -> tuple:
        """字幕窗矩形；隐藏时用最近一次的位置（面板仍能定位、仍可点）。"""
        if not self.ovw.visible:
            g = getattr(self.ovw, "_last_geom", (0, 0, 0, 0))
            if g[2] > 0 and g[3] > 0:
                return g
            m = self.ovw.mon
            return (m.x, m.y + m.height - 160, self.ovw.width, 100)
        x, y = self.ovw.get_position()
        return (x, y, self.ovw.width, self.ovw.height)

    def _bar_rect(self) -> tuple:
        x, y = self.win.get_position()
        w, h = self.win.get_size()
        return (x, y, w, h)

    def _poll_hover(self) -> bool:
        # 字幕窗隐藏时：面板常驻（否则鼠标无处可悬停，用户再也调不出面板，
        # 进程却仍在识别/翻译——本次用户困惑的根源）
        if not self.ovw.visible:
            if not self.shown:
                self.show()
            return True
        pos = self._pointer_pos()
        if pos is None:
            return True
        px, py = pos
        over = self._inside(px, py, self._sub_rect(), self.ovw.HOVER_PAD)
        if self.shown:
            over = over or self._inside(px, py, self._bar_rect(), 4)
        want = (over or self.pinned or self.ovw.edit_mode
                or time.monotonic() < self.ovw._start_until)
        if want:
            self._leave_t = None
            if not self.shown:
                self.win.show_all()
                self.shown = True
                self.reposition()
        elif self.ovw.cfg.panel_auto_hide and self.shown:
            if self._leave_t is None:
                self._leave_t = time.monotonic()
            elif (time.monotonic() - self._leave_t) * 1000 \
                    > self.ovw.AUTO_HIDE_MS:
                self.hide()
        return True

    def show(self) -> None:
        self.win.show_all()
        self.shown = True
        self.reposition()

    def hide(self) -> None:
        self.win.hide()
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
        mh = self.win.get_preferred_size()[1].height
        y = sy - mh - 4
        if y < 2:                              # 字幕贴屏幕顶部：落到框内顶部
            y = sy + 4
        self.win.resize(max(240, sw), mh)
        self.win.move(max(0, sx), max(0, y))

    def _on_bg(self, sc) -> None:
        v = sc.get_value()
        self.ovw.set_opacity(v)
        self.op_val.set_text(f"{v:.2f}")

    def _on_tx(self, sc) -> None:
        v = sc.get_value()
        self.ovw.set_text_opacity(v)
        self.tx_val.set_text(f"{v:.2f}")

    def _on_source(self, combo) -> None:
        cid = combo.get_active_id() or "both"
        if cid == str(getattr(self.ovw.cfg, "source", "both")):
            return
        self.ovw.set_source(cid)

    @staticmethod
    def _set_state(btn, on: bool) -> None:
        """按钮"开启"状态用高亮底色标识（开关一目了然）。"""
        ctx = btn.get_style_context()
        if on:
            ctx.add_class("on")
        else:
            ctx.remove_class("on")

    def refresh(self) -> None:
        ovw = self.ovw
        self.btn_vis.set_label("显示" if not ovw.visible else "隐藏")
        self._set_state(self.btn_vis, not ovw.visible)
        self.btn_pause.set_label("继续" if ovw.paused else "暂停")
        self._set_state(self.btn_pause, ovw.paused)
        self.btn_edit.set_label("完成" if ovw.edit_mode else "位置")
        self._set_state(self.btn_edit, ovw.edit_mode)
        self.btn_pt.set_label("穿透 开" if ovw._passthrough else "穿透 关")
        self._set_state(self.btn_pt, ovw._passthrough)
        self.btn_pin.set_label("自动" if self.pinned else "固定")
        self._set_state(self.btn_pin, self.pinned)
        if abs(self.op_scale.get_value() - float(ovw.cfg.opacity)) > 0.001:
            self.op_scale.set_value(round(float(ovw.cfg.opacity), 2))
        self.op_val.set_text(f"{float(ovw.cfg.opacity):.2f}")
        tx = float(getattr(ovw.cfg, "text_opacity", 1.0))
        if abs(self.tx_scale.get_value() - tx) > 0.001:
            self.tx_scale.set_value(round(tx, 2))
        self.tx_val.set_text(f"{tx:.2f}")


# ---------------------------------------------------------------- 运行管线


class SessionMirror(threading.Thread):
    """镜像模式：跟随主程序写出的会话 JSONL，只显示、不重复识别/翻译。

    主程序（`保存并启动`）每翻译完一句就往 `sessions/session-*.jsonl` 追加一行，
    这里只读新行并上屏 —— 省掉一整条 API/算力开销（外挂自己跑管线时是双份消耗）。
    会话文件换新的（主程序重启）会自动跟着切。
    """

    POLL = 0.4

    def __init__(self, log_dir: Path, overlay, stop: threading.Event):
        super().__init__(daemon=True, name="session-mirror")
        self.log_dir = Path(log_dir)
        self.overlay = overlay
        self.stop = stop
        self._fh = None                  # 当前跟随的文件句柄
        self._path: Path | None = None
        self._n = 0

    # ---- 文件跟随 ----

    def _newest(self) -> Path | None:
        """最新一场主程序会话（忽略 overlay-*.jsonl 自己写的）。"""
        if not self.log_dir.is_dir():
            return None
        files = [f for f in self.log_dir.glob("session-*.jsonl")
                 if not f.name.startswith("overlay-")]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _open(self, path: Path, at_end: bool) -> bool:
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError as e:  # noqa: BLE001
            _log(f"镜像模式：打不开 {path.name}: {e}")
            return False
        if at_end:
            fh.seek(0, 2)                # 首次跟随：只看之后新增的行
        self._close()
        self._fh, self._path = fh, path
        _log(f"镜像模式：跟随 {path.name}")
        self.overlay.set_status("镜像模式：显示主程序（保存并启动）的字幕")
        return True

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None

    # ---- 主循环 ----

    def run(self) -> None:
        while not self.stop.is_set():
            newest = self._newest()
            if newest is None:
                self.overlay.set_status("镜像模式：等待主程序启动（保存并启动）…")
                if self.stop.wait(self.POLL * 2):
                    break
                continue
            if self._path is None or newest != self._path:
                # 首次跟随看文件尾（不重放历史）；换会话则从头读
                self._open(newest, at_end=self._path is None)
            if self._fh is not None:
                self._drain()
            if self.stop.wait(self.POLL):
                break
        self._close()

    def _drain(self) -> None:
        for line in self._fh:
            line = line.strip()
            if not line or getattr(self.overlay, "paused", False):
                continue                      # 暂停：只推进不显示
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = row.get("translation") or row.get("text") or ""
            if not src:
                continue
            self._n += 1
            self.overlay.post(DisplayItem(
                kind=row.get("kind") or "internal",
                label=row.get("label", ""), app=row.get("app", ""),
                text=row.get("text", ""), translation=row.get("translation"),
                item_id=f"mirror-{self._n}", ts=time.time(),
                role=row.get("role", ""), speaker=row.get("speaker", "")))


def run_overlay(cfg: AppConfig, config_path: Path | None = None) -> int:
    """外挂模式：悬浮字幕窗 + 音频 → ASR → 翻译（复用主程序 worker）。

    音频来源可在运行时切换（控制面板下拉 / 配置）：每路采集与 ASR worker 有
    各自的停止事件，切换 = 停掉不需要的 + 起需要的，不重启外挂。
    """
    ensure_data_files()
    stop = threading.Event()
    overlay = OverlayWindow(cfg.overlay, config_path=config_path)
    mirror_mode = bool(getattr(cfg.overlay, "mirror", False))
    seg_q: "queue.Queue" = queue.Queue()
    running: dict[str, dict] = {}          # kind -> {cap, stop, pause}
    asr_box: dict = {}                     # SenseVoice 实例（boot 里加载）

    def on_pause(paused: bool) -> None:
        if mirror_mode:
            _log(f"外挂字幕{'暂停' if paused else '继续'}"
                 "（镜像模式：只停显示，不影响主程序）")
            return
        for info in running.values():
            (info["pause"].set if paused else info["pause"].clear)()
        _log(f"外挂字幕{'暂停' if paused else '继续'}")

    overlay.on_pause = on_pause

    def _source_of(kind: str):
        """返回 (SourceInfo, 采集工厂)；不可用返回 (None, None)。"""
        if kind == "mic":
            return (SourceInfo("mic", "external", "麦克风", "microphone"),
                    lambda s, q, sr: AudioCapture(s, cfg.audio.mic_device, q,
                                                  target_sr=sr))
        mon = cfg.audio.monitor_source or default_monitor_source()
        if mon:
            return (SourceInfo("monitor", "internal",
                               friendly_source_name(mon), mon),
                    lambda s, q, sr: ParecCapture(s, mon, q, target_sr=sr))
        dev = cfg.audio.monitor_device or find_monitor_device()
        if dev:
            return (SourceInfo("monitor", "internal", "系统音频", "system-mix"),
                    lambda s, q, sr: AudioCapture(s, dev, q, target_sr=sr))
        return None, None

    def start_source(kind: str) -> None:
        if kind in running or asr_box.get("asr") is None:
            return
        src, factory = _source_of(kind)
        if src is None:
            _log(f"⚠ 音频源不可用（{kind}），跳过")
            return
        cap_q: "queue.Queue" = queue.Queue()
        cap = factory(src, cap_q, cfg.audio.sample_rate)
        cap.start()
        ev, pv = threading.Event(), threading.Event()
        ASRWorker(src, cap_q, asr_box["asr"], cfg.asr, seg_q, ev,
                  on_level=overlay.set_level, on_partial=overlay.set_partial,
                  pause_event=pv, speaker=asr_box.get("speaker")).start()
        running[kind] = {"cap": cap, "stop": ev, "pause": pv}
        _log(f"音频源就绪: {src.label}（{cap.device_name}）")

    def stop_source(kind: str) -> None:
        info = running.pop(kind, None)
        if info is None:
            return
        info["stop"].set()
        try:
            info["cap"].stop()
        except Exception:  # noqa: BLE001
            pass
        _log(f"已停止音频源: {kind}")

    def set_source(want: str) -> None:
        """切换音频来源（运行时）：内部/外部/两者。"""
        if mirror_mode:
            overlay.set_status("镜像模式：音频来源跟随主程序设置")
            return
        if want not in ("internal", "external", "both"):
            want = "both"
        overlay.cfg.source = want
        _log(f"外挂音频来源: {want}")
        wish = {"mic": cfg.audio.mic_enabled and want in ("external", "both"),
                "monitor": cfg.audio.monitor_enabled
                and want in ("internal", "both")}
        for kind, on in wish.items():
            (start_source if on else stop_source)(kind)
        if not running:
            overlay.set_status("没有可用音频源，外挂仅显示占位")
        persist_overlay(config_path, {"source": want})

    overlay.on_source_change = set_source

    def quit_app() -> None:
        stop.set()
        for kind in list(running):
            stop_source(kind)

    overlay.on_exit = quit_app

    def boot() -> None:
        if mirror_mode:
            # 镜像模式：不加载 ASR/翻译模型，只跟随主程序的会话日志
            overlay.set_mirror(True)
            _log("镜像模式：只显示主程序（保存并启动）的字幕，"
                 "外挂不再自己识别/翻译（省 API 与算力）")
            SessionMirror(Path(cfg.session.log_dir), overlay, stop).start()
            return
        try:
            overlay.set_status("加载 SenseVoice 模型 ...")
            t0 = time.monotonic()
            asr_box["asr"] = SenseVoiceASR(cfg.asr.language)
            _log(f"SenseVoice 就绪（{time.monotonic() - t0:.1f}s）")

            # 声纹（可选，与外挂共用同一套说话人聚类）
            tracker = build_tracker(getattr(cfg, "speaker", None), _log)
            if tracker is not None:
                asr_box["speaker"] = tracker
                threading.Thread(target=tracker.preload, daemon=True,
                                 name="speaker-preload").start()

            set_source(getattr(cfg.overlay, "source", "both"))

            prov = cfg.providers[cfg.translate.provider]
            if is_local_base_url(prov.base_url):
                ensure_local_backend(prov.base_url, prov.model, _log)
                local_cfg = getattr(cfg, "local", None)
                if local_cfg is not None and local_cfg.unload_others_on_start:
                    from .sysmon import ollama_loaded, ollama_unload
                    others = [m.get("name", "")
                              for m in ollama_loaded(prov.base_url)
                              if m.get("name") and m.get("name") != prov.model]
                    if others:
                        ollama_unload(others, prov.base_url)
                        _log(f"释放显存：已卸载其它模型 {', '.join(others)}")
            translator = LLMTranslator(cfg.translate, cfg.providers)
            log_dir = Path(cfg.session.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"overlay-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
            TranslatorWorker(seg_q, overlay, translator, log_path, stop,
                             fallback_builder=_make_fallback_builder(
                                 cfg, _log)).start()
            _log(f"翻译后端: {translator.backend}/{translator.model}"
                 f" · 会话日志 {log_path.name} · 就绪，开始说话吧")
        except Exception as e:  # noqa: BLE001
            _log(f"外挂启动失败: {type(e).__name__}: {e}")
            overlay.set_status(f"启动失败: {e}")

    threading.Thread(target=boot, daemon=True, name="overlay-boot").start()
    try:
        Gtk.main()
    finally:
        stop.set()
        for kind in list(running):
            stop_source(kind)
    _log("外挂结束")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="livetrans.overlay",
                                 description="LiveTrans 字幕外挂（GTK3 悬浮字幕）")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config) if Path(args.config).is_file() else None
    cfg = load_config(str(cfg_path) if cfg_path else None)
    _log("字幕外挂启动（GTK3）")
    try:
        return run_overlay(cfg, config_path=cfg_path)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
