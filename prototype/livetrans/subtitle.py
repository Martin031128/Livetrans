"""置顶字幕悬浮窗（Tkinter）：对话/外部/内部布局 + 滚动历史 + 译文流式 + 样式。

- 每栏可滚动（滚轮/拖动条），保留最近 50 条历史；贴底跟随、上滚不拽回，
  「跳到最新」一键恢复跟随；译文补全使内容变高时同样保持贴底；
- 顶部模式条：对话 / 外部（只看麦克风）/ 内部（只看系统声音）、跳到最新、置顶、
  导出、样式入口；对话模式左右分栏（你/对方）右栏右对齐、活跃栏高亮；
- 每栏标题栏有「暂停/继续」按钮（丢弃该路音频，停止识别与翻译）与电平条；
- 条目：元信息（时间 · ASR · LLM）/ 原文（灰）/ 译文（白、大、粗）上下三行；
  译文异步：先占位"… 翻译中"（带脉冲光标），流式增量原位推进；
- 动效：新条目颜色淡入（Tk 无逐控件 alpha，用文字色向背景混合模拟）；
- 样式：文字不透明度 / 字号 / 译文与原文颜色（背景恒定墨蓝，窗口 alpha 单独管），
  实时生效并经回调持久化；
- 导出：顶栏「导出」把本次会话存成 SRT（双语/仅译文/仅原文）或 TXT。
"""
from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from tkinter import font
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from livetrans.langs import (DIALOG_AUDIO_LABELS, DIALOG_AUDIO_SHORT,
                             DIALOG_AUDIO_VALUES, DIALOG_DEFAULTS,
                             DIALOG_DST_LANGS, DIALOG_ROLE_LABELS,
                             DIALOG_ROLE_VALUES, DIALOG_SRC_LANGS,
                             dialog_audio_short, dialog_left_role,
                             dialog_role_kinds, dialog_role_label,
                             dialog_roles)
from livetrans.speaker import color_for

BG = "#1e1e2e"
FG_PENDING = "#6c7086"  # 翻译中占位（暗灰）
FG_META = "#5b6478"     # 元信息（时间/延迟）
FG_INTERNAL = "#89b4fa" # 内部音频栏标题（蓝）
FG_EXTERNAL = "#a6e3a1" # 外部音频栏标题（绿）
BAR_TRACK = "#2a3142"   # 进度条/电平条底槽
LEVEL_W = 70            # 栏头电平条宽度（栏窄也不挤：标题会让位裁切）
INK = "#dce3ee"         # 主文字
SIGNAL = "#39c5b8"      # 激活态
PANEL = "#1c2230"       # 弹出面板底色（样式/对话设置）
WARN = "#ff9f43"        # 提示/警示（状态栏与面板里的提醒文字）
PANEL_2 = "#262f42"     # 按钮/控件面
BORDER = "#2c3548"      # 边线
MAX_ITEMS_PER_PANE = 50     # 保留可回滚阅读的历史条数

DEFAULT_LAYOUT = "dialog"

STYLE_DEFAULTS = {
    "text_opacity": 1.0,    # 文字不透明度 0.3~1.0（颜色向当前背景混合模拟）
    "scale": 1.0,           # 字号缩放 0.8~1.6
    "dst_color": "#ffffff", # 译文颜色
    "src_color": "#9399b2", # 原文颜色
    "layout": DEFAULT_LAYOUT,   # dialog（对话）/ external（只看麦克风）/ internal（只看系统声音）
    "animate": True,        # 新条目淡入动效
}
# 布局（双栏已并入"对话"——两者都是两条字幕流并排，功能撞车）
LAYOUTS = ("dialog", "external", "internal")


def norm_layout(mode: str) -> str:
    """布局名归一化：老配置里的 dual（双栏）已删除，退回对话模式。"""
    return mode if mode in LAYOUTS else DEFAULT_LAYOUT


def is_source_layout(mode: str) -> bool:
    """只看某一路音频的单一界面（外部/内部）。"""
    return mode in ("external", "internal")
# 按钮尺寸令牌（与控制台一致的两档：顶栏标准 + 条目内小按钮）
CHIP_FONT = ("sans", 8)     # 条目里的「原文/译文」复制按钮
CHIP_PAD = 6
PENDING = "… 翻译中"
PULSE = "▌"                 # 脉冲光标（半个方块，字宽稳定不引起重排）
PULSE_MS = 620              # 脉冲切换周期


def blend(fg: str, bg: str, alpha: float) -> str:
    """颜色混合：alpha=1 取 fg，0 取 bg（Tk 不支持逐控件 alpha 的替代方案）。"""
    try:
        f, b = fg.lstrip("#"), bg.lstrip("#")
        a = max(0.0, min(1.0, float(alpha)))
        r = round(int(f[0:2], 16) * a + int(b[0:2], 16) * (1 - a))
        g = round(int(f[2:4], 16) * a + int(b[2:4], 16) * (1 - a))
        bl = round(int(f[4:6], 16) * a + int(b[4:6], 16) * (1 - a))
        return f"#{r:02x}{g:02x}{bl:02x}"
    except (ValueError, IndexError):
        return fg
DST_COLORS = [("白", "#ffffff"), ("黄", "#ffe08a"), ("青", "#7fe7dd"),
              ("绿", "#b6f2a6"), ("粉", "#ff9fb0")]
SRC_COLORS = [("灰", "#9399b2"), ("浅蓝", "#8ab6f0"), ("白", "#d8dde5")]


@dataclass
class DisplayItem:
    kind: str          # internal / external
    label: str
    app: str
    text: str
    translation: str | None = None    # None = 翻译中，稍后 update_translation 补
    item_id: str = ""
    ts: float = 0.0                   # 段完成时刻（epoch，显示 HH:MM:SS）
    asr_ms: float = 0.0               # 识别耗时
    llm_ms: float | None = None       # 翻译耗时（完成时回填）
    speaker: str = ""                 # 声纹说话人标签 S1/S2…（未启用为空）
    role: str = ""                    # 对话模式角色 self/other（单路模式为空）


class _Pane:
    """一栏：标题 + 暂停按钮 + 电平条 + "识别中"行 + 可滚动字幕区。"""

    def __init__(self, parent, title: str, color: str,
                 on_pause=None, win=None, align: str = "left"):
        self.color = color
        self.title = title
        self.title_base = title             # 不含"（已暂停）"等后缀
        self.lang_suffix = ""               # 语言标签（如 "自动 → 中文"）
        self.win = win
        self.paused = False
        self.follow = True                  # 是否自动滚动到最新
        self.align = align                  # 对话模式右栏右对齐
        self.active = True                  # 活跃栏高亮（对话模式）
        self.pending: dict[str, tk.Text] = {}   # 待翻译条目 -> 译文控件（脉冲光标）
        bg = win.bg()
        frame = tk.Frame(parent, bg=bg)

        header = tk.Frame(frame, bg=bg)
        header.pack(fill="x", pady=(0, 4))
        # 标题占剩余宽度、放不下就自动裁切（pack 会撑破栏宽：窄栏时电平条被挤出窗口）
        header.grid_columnconfigure(0, weight=1)
        self.title_lab = tk.Label(header, text=title, bg=bg, fg=color,
                                  font=("sans", 11, "bold"), anchor="w")
        self.title_lab.grid(row=0, column=0, sticky="ew")
        self.level = tk.Canvas(header, width=LEVEL_W, height=8, bg=BAR_TRACK,
                               highlightthickness=0)
        self.level.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.level_rect = self.level.create_rectangle(0, 0, 0, 8, fill=color,
                                                      outline="")
        if on_pause is not None:
            self.pause_btn = tk.Button(header, text="暂停", width=4,
                                       command=lambda: on_pause(self.kind_ref),
                                       bg=PANEL_2, fg=color,
                                       activebackground=BORDER,
                                       activeforeground=color, relief="flat",
                                       bd=0, font=("sans", 9), cursor="hand2",
                                       takefocus=0)
            self.pause_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))
            _Tooltip(self.pause_btn,
                     "暂停本栏（这个角色）：只停它的翻译与上屏，\n"
                     "音频仍照常提供给另一栏；线下共用麦克风时靠它轮流说话")

        # 流式 partial：说话进行中即在主字幕流里创建"进行中"条目，
        # 边说边更新原文（见 upsert_live/finalize_live）；不再用底部小行
        self.live_item_id: str | None = None
        self._live_src: tk.Label | None = None

        body = tk.Frame(frame, bg=bg)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=bg, highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(body, orient="vertical", command=self._on_vbar)
        self.canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self._on_canvas_resize(e))
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())
        self.frame = frame
        self.bg_widgets = [frame, header, body, self.canvas, self.inner,
                           self.title_lab]            # 背景暗度联动
        self.kind_ref = None                # 暂停回调的标识 = 角色（由窗口设置）
        self.role = ""                      # self / other（对话模式）
        self.source_kind = ""               # 该角色当前用哪一路音频（external/internal）
        self.label_refs: dict[str, tk.Label] = {}   # item_id -> 译文 Label
        self.meta_refs: dict[str, tk.Label] = {}    # item_id -> 元信息 Label
        self.src_labels: list[tk.Text] = []         # 原文 Text（样式联动）
        self.wrap_targets: list[tk.Label] = []           # 换行宽度跟随栏宽
        self._meta_btns: list[tk.Button] = []       # 每条目的复制按钮（样式联动）
        self._spk_chips: list[tk.Label] = []        # 说话人色块（换人时出现）
        self.last_speaker = ""                      # 上一句的说话人（连续不重复标注）

    # ---- 暂停状态 ----

    def set_paused(self, paused: bool, win) -> None:
        self.paused = paused
        if hasattr(self, "pause_btn"):
            self.pause_btn.config(text="继续" if paused else "暂停")
        self.refresh_title()

    def refresh_title(self) -> None:
        """标题 = 栏目名 · 语言标签 + 暂停后缀（对话模式/语言变更后刷新）。"""
        parts = [self.title_base]
        if self.lang_suffix:
            parts.append(self.lang_suffix)
        text = " · ".join(parts) + ("（已暂停）" if self.paused else "")
        if self.title_lab.winfo_exists():
            self.title_lab.config(text=text)

    # ---- 对话模式：活跃态 / 对齐 ----

    def set_active(self, active: bool) -> None:
        """活跃栏高亮：说话中的一侧标题与电平条满色，另一侧淡出。"""
        if active == self.active:
            return
        self.active = active
        bg = self.win.bg() if self.win is not None else BG
        col = self.color if active else blend(self.color, bg, 0.42)
        if self.title_lab.winfo_exists():
            self.title_lab.config(fg=col)
        btn = getattr(self, "pause_btn", None)
        if btn is not None and btn.winfo_exists():
            btn.config(fg=col, activeforeground=col)
        self.level.configure(bg=BAR_TRACK if active else "#232b3c")

    def set_align(self, align: str) -> None:
        """切换栏内文字对齐（对话模式右栏右对齐）。"""
        if align == self.align:
            return
        self.align = align
        for txt in list(self.src_labels) + list(self.label_refs.values()):
            if txt.winfo_exists():
                self._apply_align(txt, align)
        for entry in self.inner.winfo_children():
            if isinstance(entry, tk.Frame):
                entry.pack_configure(anchor="e" if align == "right" else "nw")
        for mid in list(self.meta_refs.values()):
            if mid.winfo_exists():
                mid.config(anchor="e" if align == "right" else "w")

    # ---- 滚轮 / 滚动条（驱动 follow：上滚即停跟随，滚回底部恢复） ----

    def _on_wheel(self, e) -> None:
        up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
        if up:
            self.follow = False
            self.canvas.yview_scroll(-2, "units")
        else:
            self.canvas.yview_scroll(2, "units")
            self.follow = self.at_bottom()

    def _on_vbar(self, *args) -> None:
        """滚动条拖动：滚动后按位置恢复/关闭跟随。"""
        self.canvas.yview(*args)
        self.canvas.after_idle(lambda: setattr(self, "follow", self.at_bottom()))

    def _bind_wheel(self) -> None:
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(ev, self._on_wheel)

    def _unbind_wheel(self) -> None:
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(ev)

    # ---- 滚动位置 ----

    def at_bottom(self) -> bool:
        frac = self.canvas.yview()
        return frac[1] >= 0.985

    def stick_bottom(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)
        # 二次定位：inner 增高的 <Configure> 事件稍后到达，再贴一次底
        self.canvas.after(30, lambda: self.canvas.yview_moveto(1.0))

    # ---- 条目 ----

    @staticmethod
    def _meta_text(item: DisplayItem, llm_ms: float | None) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(item.ts)) if item.ts else ""
        parts = [p for p in (ts,
                             f"ASR {item.asr_ms:.0f}ms" if item.asr_ms else "",
                             f"LLM {llm_ms/1000:.1f}s" if llm_ms else "") if p]
        return " · ".join(parts)

    def add_item(self, item: DisplayItem, win) -> None:
        bg = win.bg()
        anim = bool(win.style.get("animate", True))
        entry = tk.Frame(self.inner, bg=bg)
        entry.item_id = item.item_id          # type: ignore[attr-defined]
        done = bool(item.translation)
        # 原文/译文用 tk.Text：支持鼠标选择（I 型光标）与 Ctrl+C，防键盘编辑
        src_txt = self._make_text(entry, bg, win._src_color(bg), win.f_src(),
                                  item.text, justify=self.align)
        dst_txt = self._make_text(entry, bg,
                                  win._dst_color(bg) if done else FG_PENDING,
                                  win.f_dst() if done else win.f_pending(),
                                  item.translation if done else PENDING,
                                  justify=self.align)
        # 元信息行：时间戳 + 说话人色块 + 复制按钮（原文/译文）
        meta_row = tk.Frame(entry, bg=bg)
        info = tk.Frame(meta_row, bg=bg)
        meta_lab = tk.Label(info, text=self._meta_text(item, item.llm_ms),
                            bg=bg, fg=FG_META, font=("sans", 8), anchor="w")
        meta_lab.pack(side="left")
        if item.speaker and item.speaker != self.last_speaker:
            self.last_speaker = item.speaker          # 换人才标注，同人不重复
            self._speaker_chip(info, item.speaker).pack(side="left", padx=(6, 0))
        info.pack(side="right" if self.align == "right" else "left")
        self._copy_btn(meta_row, src_txt, bg, "原文").pack(side="right")
        self._copy_btn(meta_row, dst_txt, bg, "译文").pack(side="right")
        meta_row.pack(fill="x")
        src_txt.pack(fill="x")
        dst_txt.pack(fill="x")
        self.src_labels.append(src_txt)
        entry.pack(fill="x", pady=6,
                   anchor="e" if self.align == "right" else "nw")
        self._fit_height(src_txt)
        self._fit_height(dst_txt)

        if item.item_id:
            self.label_refs[item.item_id] = dst_txt
            self.meta_refs[item.item_id] = meta_lab
            if not done:
                self.pending[item.item_id] = dst_txt   # 脉冲光标 + 等译文
        if anim:                                       # 入场动效：颜色淡入
            self._fade(src_txt, win._src_color(bg), bg)
            self._fade(dst_txt, win._dst_color(bg) if done else FG_PENDING, bg)
        children = self.inner.winfo_children()
        if len(children) > MAX_ITEMS_PER_PANE:
            removed = children[0]
            iid = getattr(removed, "item_id", "")
            if iid:
                self.label_refs.pop(iid, None)
                self.meta_refs.pop(iid, None)
                self.pending.pop(iid, None)
            removed.destroy()
            self.src_labels = [l for l in self.src_labels if l.winfo_exists()]
            self._meta_btns = [b for b in self._meta_btns if b.winfo_exists()]
            self._spk_chips = [c for c in self._spk_chips if c.winfo_exists()]
            self.pending = {k: v for k, v in self.pending.items()
                            if v.winfo_exists()}

    def _speaker_chip(self, parent, name: str) -> tk.Label:
        """说话人色块（S1/S2… 各一色）：只在换人时出现，不污染可复制的正文。"""
        chip = tk.Label(parent, text=name, bg=color_for(name), fg="#0d1420",
                        font=("sans", 7, "bold"), padx=4, pady=0)
        self._spk_chips.append(chip)
        return chip

    def _fade(self, txt: tk.Text, fg: str, bg: str, steps: int = 4,
              interval: int = 45) -> None:
        """颜色淡入（约 200ms）：Tk 无逐控件 alpha，用文字色向背景混合模拟。"""
        for i in range(1, steps + 1):
            col = blend(fg, bg, i / steps)
            try:
                txt.after(i * interval,
                          lambda c=col, t=txt: t.winfo_exists() and t.config(fg=c))
            except tk.TclError:
                return

    # ---- 文本条目（可选中的 Text） ----

    @staticmethod
    def _apply_align(txt: tk.Text, align: str) -> None:
        """Text 没有 -justify 选项：用 tag 实现整段左/右对齐。"""
        try:
            txt.tag_configure("align", justify=align)
            txt.tag_add("align", "1.0", "end")
        except tk.TclError:
            pass

    @staticmethod
    def _make_text(parent, bg: str, fg: str, font, s: str,
                   justify: str = "left") -> tk.Text:
        """无边框只读观感的 Text：可选中复制，但拦截键盘编辑（Ctrl+C 放行）。"""
        # width=1：Text 默认请求宽约 80 字符，pack 不会把它压到请求宽度以下，
        # 于是长句按 ~600px 排版、被窄栏裁掉（双栏 534px 时必现）。
        # 请求宽度设成 1 字符后由 pack(fill="x") 撑满栏宽，换行才按真实宽度算。
        txt = tk.Text(parent, height=1, width=1, wrap="word", bd=0, relief="flat",
                      highlightthickness=0, bg=bg, fg=fg, font=font,
                      cursor="xterm", takefocus=0, padx=0, pady=0,
                      insertwidth=0, selectbackground=BORDER,
                      selectforeground="#ffffff")
        txt.insert("1.0", s)
        _Pane._apply_align(txt, justify)
        # 无 Control/Alt 修饰的按键一律吞掉：不可编辑；Ctrl+C 复制走类绑定
        txt.bind("<Key>", lambda e: "break" if not (
            e.state & 0x0004 or e.state & 0x20000) else None)
        txt.bind("<Configure>", lambda _e: _Pane._fit_height(txt))
        return txt

    _measure_lab: tk.Label | None = None   # 离屏测量用（复用一个，别每行建一个）

    @classmethod
    def _fit_height(cls, txt: tk.Text) -> None:
        """按显示行数自适应高度（Text 不会像 Label 那样自动撑高）。

        坑（双栏窄栏时必现）：Text 只排版"看得见"的内容——height=1 时
        `count -displaylines` 恒为 1、`dlineinfo(末字)` 为 None，于是
        "Configure → 量到 1 行 → 设回 1 行"自我循环，长句换行后的第二行永远被吃掉；
        而"临时撑高再量"会在 Configure 上与自身反复触发（死循环）。
        做法：用一个**离屏 Label**（同字体、同换行宽度）量文本要占几行，
        再回填给 Text——完全不碰 Text 自己的排版时序。
        """
        if not txt.winfo_exists():
            return
        try:
            text = txt.get("1.0", "end-1c")
            w = txt.winfo_width()
            if w <= 1:                           # 还没布局：等下一次 Configure
                return
            lab = cls._measure_lab
            if lab is None or not lab.winfo_exists():
                lab = tk.Label(txt.master, bd=0, padx=0, pady=0, justify="left",
                               bg=txt.cget("bg"), fg=txt.cget("fg"))
                lab.place(x=-10000, y=0)         # 离屏：仍需真实绘制才能量
                cls._measure_lab = lab
            # 字体每次都要带：原文/译文字号不同（10/14），复用同一个 Label 会串味
            lab.configure(text=text or " ", font=txt.cget("font"),
                          wraplength=max(40, w - 6))
            lab.update_idletasks()
            line_h = max(1, font.Font(font=txt.cget("font")).metrics("linespace"))
            n = max(1, round(lab.winfo_reqheight() / line_h))
            if int(txt.cget("height")) != n:
                txt.configure(height=n)
        except tk.TclError:
            pass

    def _set_text(self, txt: tk.Text, s: str) -> None:
        txt.delete("1.0", "end")
        txt.insert("1.0", s)
        self._apply_align(txt, self.align)               # 重插后补对齐 tag
        txt.after_idle(lambda: self._fit_height(txt))    # 重排后重新量行数

    def _copy_btn(self, parent, txt: tk.Text, bg: str, label: str) -> tk.Button:
        # 统一小按钮（chip）尺寸：字号 8 + padx 6，与控制台的小按钮观感一致
        b = tk.Button(parent, text=label, font=CHIP_FONT, bd=0, relief="flat",
                      bg=bg, fg=FG_META, activebackground=BORDER,
                      activeforeground=INK, cursor="hand2", takefocus=0,
                      padx=CHIP_PAD, pady=1,
                      command=lambda: self._copy_text(b, txt))
        self._meta_btns.append(b)
        return b

    def _copy_text(self, btn: tk.Button, txt: tk.Text) -> None:
        s = txt.get("1.0", "end-1c").strip()
        if not s or s.startswith("…"):               # 占位（含脉冲光标）不复制
            return
        self.canvas.clipboard_clear()
        self.canvas.clipboard_append(s)
        old = btn.cget("text")
        btn.config(text="已复制")
        btn.after(900, lambda: btn.winfo_exists() and btn.config(text=old))

    def update_translation(self, item_id: str, text: str,
                           llm_ms: float | None = None, win=None) -> None:
        dst = self.label_refs.get(item_id)
        if dst is not None and dst.winfo_exists() and win is not None:
            bg = win.bg()
            first = bool(text) and item_id in self.pending
            self.pending.pop(item_id, None)          # 出译文后不再脉冲
            dst.config(fg=win._dst_color(bg), font=win.f_dst())
            self._set_text(dst, text if text else PENDING)
            if first and win.style.get("animate", True):
                self._fade(dst, win._dst_color(bg), bg, steps=3, interval=50)
        meta = self.meta_refs.get(item_id)
        if llm_ms and meta is not None and meta.winfo_exists():
            cur = meta.cget("text")
            if "LLM" not in cur:              # 流式增量不重复改写
                meta.config(text=f"{cur} · LLM {llm_ms/1000:.1f}s")

    def upsert_live(self, text: str, win) -> None:
        """流式 partial：在主字幕流中创建/更新"进行中"条目。

        只更新原文标签（译文位置保持"… 翻译中"占位，由正式条目+LLM 接管）。
        """
        bg = win.bg()
        if self.live_item_id is None or \
                self.live_item_id not in self.label_refs or \
                not self.label_refs[self.live_item_id].winfo_exists():
            item = DisplayItem(kind=self.source_kind or "external", label="",
                               app="", text=text, translation=None,
                               item_id=f"{self.role or self.source_kind}-live",
                               ts=time.time(), role=self.role)
            self.add_item(item, win)
            self.live_item_id = f"{self.role or self.source_kind}-live"
            self._live_src = self.src_labels[-1]      # 进行中条目的原文标签
        else:
            self._set_text(self._live_src, text)      # 原文随语音增长

    def finalize_live(self) -> None:
        """定稿：**真正移除**进行中条目（其内容由正式条目接替）。

        此前只清标记不移除，导致同一句出现两遍、且残留条目永远"… 翻译中"。
        """
        iid = self.live_item_id
        self.live_item_id = None
        self._live_src = None
        if not iid:
            return
        dst = self.label_refs.pop(iid, None)
        self.meta_refs.pop(iid, None)
        self.pending.pop(iid, None)
        entry = None
        if dst is not None and dst.winfo_exists():
            entry = dst.master                        # dst 标签所在的条目框
        if entry is not None:
            entry.destroy()                           # 移除整个进行中条目
        self.src_labels = [l for l in self.src_labels
                           if l.winfo_exists() and l.master is not entry]
        self._meta_btns = [b for b in self._meta_btns if b.winfo_exists()]

    def restyle(self, win) -> None:
        """样式变更后刷新存量标签的字体/颜色与各容器背景（背景暗度联动）。"""
        bg = win.bg()
        src_eff = win._src_color(bg)
        dst_eff = win._dst_color(bg)
        for wd in self.bg_widgets:
            if wd.winfo_exists():
                wd.config(bg=bg)
        for t in self.src_labels:
            if t.winfo_exists():
                t.config(fg=src_eff, font=win.f_src(), bg=bg)
        for iid, lab in self.label_refs.items():
            if not lab.winfo_exists():
                continue
            if iid in self.pending:                  # 未出译文：保持占位灰
                lab.config(fg=blend(FG_PENDING, bg, 0.85), font=win.f_pending(),
                           bg=bg)
            else:
                lab.config(fg=dst_eff, font=win.f_dst(), bg=bg)
        for b in self._meta_btns:
            if b.winfo_exists():
                b.config(bg=bg, activebackground=BORDER)

    # ---- 换行宽度跟随 ----

    def _on_canvas_resize(self, e) -> None:
        """窗口/栏宽变化：内嵌窗口同宽 + 所有正文标签换行宽度跟随（字幕长度=窗口宽）。"""
        self.canvas.itemconfigure(self._win, width=e.width)
        self._retab(e.width)

    def _retab(self, width: float) -> None:
        wl = max(120, int(width) - 12)      # 留出滚动条余量
        for lab in self.wrap_targets:
            if lab.winfo_exists():
                lab.config(wraplength=wl)

    # ---- 电平条 ----

    def set_level(self, rms: float) -> None:
        full = self.level.winfo_width() or LEVEL_W             # 实际像素宽（可能被裁）
        w = full * min(1.0, math.sqrt(max(rms, 0.0)) * 1.7)    # 开方标度，小音量也可见
        self.level.coords(self.level_rect, 0, 0, w, 8)


class _Tooltip:
    """极简 Tk 悬停提示：鼠标停留 ~450ms 后在指针旁弹出，移开/点击即消失。"""

    DELAY = 450

    def __init__(self, widget, text: str):
        self.widget, self.text = widget, text
        self._job = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._hide()
        try:
            self._job = self.widget.after(self.DELAY, self._show)
        except tk.TclError:
            self._job = None

    def _show(self) -> None:
        if self._tip is not None or not self.widget.winfo_exists():
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            tk.Label(tip, text=self.text, bg="#1c2230", fg="#dce3ee",
                     font=("sans", 9), justify="left", padx=8, pady=5,
                     relief="flat").pack()
            self._tip = tip
        except tk.TclError:
            self._tip = None

    def _hide(self, _e=None) -> None:
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class SubtitleWindow:
    """线程安全：工作线程调用 post/update_translation/set_partial/set_level，
    UI 线程轮询消费。暂停/布局/样式经回调交由 main 处理与持久化。"""

    def __init__(self, status: str = "", stop_event=None, style: dict | None = None,
                 on_pause_toggle=None, on_style_change=None,
                 dialog: dict | None = None, on_dialog_change=None):
        self.q: "queue.Queue[DisplayItem]" = queue.Queue()
        self.uq: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.lq: "queue.Queue[tuple[str, float]]" = queue.Queue()
        self.pq: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._status_q: "queue.Queue[str]" = queue.Queue()
        self._levels: dict[str, float] = {}
        self._stop_event = stop_event
        # 两栏都按「角色」组织（self=你 / other=对方）；每栏自己知道用哪一路音频
        self._panes: dict[str, _Pane] = {}
        self.style = {**STYLE_DEFAULTS, **(style or {})}
        self.style["layout"] = norm_layout(self.style.get("layout"))   # dual 已删
        self.paused_roles: set[str] = set()   # 被暂停翻译/上屏的角色
        self.on_pause_toggle = on_pause_toggle
        self.on_style_change = on_style_change
        # 对话模式：两侧各自的音频来源与翻译方向（运行时可调，见「对话设置」）
        self.dialog = {**DIALOG_DEFAULTS, **(dialog or {})}
        self.on_dialog_change = on_dialog_change
        self.session_path: Path | None = None    # 本次会话 JSONL（导出数据源）
        self._lang_label = ""                    # 语言标签（源 → 目标）
        self._active_kind = ""                   # 对话模式：最近说话的栏
        self._pulse_phase = False
        self._pulse_job = None

        self.root = tk.Tk()
        self.root.title("LiveTrans 实时字幕")
        self.root.configure(bg=self.bg())
        self.root.geometry("1100x480")
        self.root.minsize(760, 360)           # 可自由缩放；字幕宽度跟随窗口
        self.root.attributes("-topmost", True)
        self.topmost = True
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        # 顶部模式条：布局切换 + 跳到最新 + 置顶 + 样式入口
        topbar = tk.Frame(self.root, bg=self.bg())
        topbar.pack(fill="x", padx=10, pady=(8, 0))
        self._topbar = topbar
        self._mode_btns: dict[str, tk.Button] = {}
        for label, mode in (("对话", "dialog"), ("外部", "external"),
                            ("内部", "internal")):
            b = tk.Button(topbar, text=label, command=lambda m=mode: self._set_layout(m),
                          bg=BG, fg=FG_PENDING, activebackground=BG,
                          activeforeground=INK, relief="flat", bd=0,
                          font=("sans", 9), cursor="hand2", takefocus=0,
                          padx=6)
            b.pack(side="left")
            self._mode_btns[mode] = b
            _Tooltip(b, {
                "dialog": "对话：两条字幕流并排（按角色）——\n"
                          "自己（你）靠右、对方靠左，说话侧高亮；\n"
                          "各自的音频来源与语言在「对话设置」里",
                "external": "只看「麦克风」这一路音频的单一界面\n"
                            "（谁在用麦克风就显示谁的字幕流）",
                "internal": "只看「系统声音」这一路音频的单一界面\n"
                            "（谁在用系统声音就显示谁的字幕流）",
            }[mode])
        self.btn_jump = tk.Button(topbar, text="↓ 最新", command=self._jump_to_latest,
                                  bg=BG, fg=FG_PENDING, activebackground=BG,
                                  activeforeground=INK, relief="flat", bd=0,
                                  font=("sans", 9), cursor="hand2", takefocus=0,
                                  padx=6)
        self.btn_jump.pack(side="left", padx=(8, 0))
        _Tooltip(self.btn_jump,
                 "回到最新字幕并恢复自动跟随\n（上滚查看历史后点这里回来）")
        # 导出：本次会话 -> SRT（双语/仅译文/仅原文）或纯文本
        self.btn_export = tk.Menubutton(topbar, text="导出 ▾", bg=BG,
                                        fg=FG_PENDING, activebackground=BG,
                                        activeforeground=INK, relief="flat",
                                        bd=0, font=("sans", 9), cursor="hand2",
                                        takefocus=0, padx=6,
                                        highlightthickness=0)
        menu = tk.Menu(self.btn_export, tearoff=0, bg="#1c2230", fg=INK,
                       activebackground=SIGNAL, activeforeground="#0d1420",
                       font=("sans", 9), bd=0)
        for label, mode, as_txt in (("双语字幕 SRT", "bilingual", False),
                                    ("仅译文 SRT", "dst", False),
                                    ("仅原文 SRT", "src", False),
                                    ("纯文本 TXT", "bilingual", True)):
            menu.add_command(label=label,
                             command=lambda m=mode, t=as_txt: self._export(m, t))
        self.btn_export["menu"] = menu
        self.btn_export.pack(side="left", padx=(8, 0))
        _Tooltip(self.btn_export,
                 "把本次会话导出为字幕文件（时间轴与内容严格对齐）\n"
                 "双语=原文+译文两条；也可只导一侧或纯文本")
        self.btn_top = tk.Button(topbar, text="置顶 ✓", command=self._toggle_topmost,
                                 bg=BG, fg=FG_PENDING, activebackground=BG,
                                 activeforeground=INK, relief="flat", bd=0,
                                 font=("sans", 9), cursor="hand2", takefocus=0,
                                 padx=6)
        self.btn_top.pack(side="right")
        _Tooltip(self.btn_top, "是否让字幕窗始终浮在其它窗口之上")
        self.btn_style = tk.Button(topbar, text="样式", command=self._open_style_panel,
                                   bg=BG, fg=FG_PENDING, activebackground=BG,
                                   activeforeground=INK, relief="flat", bd=0,
                                   font=("sans", 9), cursor="hand2",
                                   takefocus=0, padx=6)
        self.btn_style.pack(side="right", padx=(0, 8))
        _Tooltip(self.btn_style,
                 "字幕样式：文字不透明度 / 字号 / 译文与原文颜色\n（改动即时生效并保存）")
        self.btn_dialog = tk.Button(topbar, text="对话设置",
                                    command=self._open_dialog_panel,
                                    bg=BG, fg=FG_PENDING, activebackground=BG,
                                    activeforeground=INK, relief="flat", bd=0,
                                    font=("sans", 9), cursor="hand2",
                                    takefocus=0, padx=6)
        self.btn_dialog.pack(side="right", padx=(0, 8))
        _Tooltip(self.btn_dialog,
                 "对话模式：左/右两栏各用哪一路音频（麦克风/系统声音）、\n"
                 "各自译成什么语言；改完立即生效并保存，无需重启")

        self.header = tk.Frame(self.root, bg=self.bg())
        self.header.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        for role, color in (("self", FG_EXTERNAL), ("other", FG_INTERNAL)):
            pane = _Pane(self.header, DIALOG_ROLE_LABELS.get(role, role), color,
                         on_pause=self._toggle_pause, win=self)
            pane.role = role
            pane.kind_ref = role                # 暂停回调按角色
            self._panes[role] = pane

        # 布局占位提示（聚焦某一路但没有角色用它时显示）
        self._layout_hint = tk.Label(
            self.header, text="", bg=self.bg(), fg=FG_META, font=("sans", 11),
            justify="center", padx=20, pady=20, wraplength=520)

        self.status = tk.Label(self.root, text=status, bg=self.bg(), fg="#6c7086",
                               font=("sans", 10), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=10, pady=(2, 6))

        self._set_layout(norm_layout(self.style.get("layout")))

    # ---- 背景/颜色（背景暗度：背景色向黑混合；文字颜色混向当前背景） ----

    def bg(self) -> str:
        return BG                     # 背景固定墨蓝；透明度由窗口 alpha 实现

    def _dst_color(self, bg_hex: str) -> str:
        return self._blend(self.style["dst_color"], bg_hex,
                           self.style.get("text_opacity", 1.0))

    def _src_color(self, bg_hex: str) -> str:
        return self._blend(self.style["src_color"], bg_hex,
                           self.style.get("text_opacity", 1.0))

    @staticmethod
    def _blend(fg: str, bg: str, alpha: float) -> str:
        """文字透明度模拟：把文字颜色向背景色按 alpha 混合（见模块级 blend）。"""
        return blend(fg, bg, alpha)

    # ---- 字体 ----

    def f_src(self) -> tuple:
        return ("sans", max(8, round(10 * self.style["scale"])))

    def f_dst(self) -> tuple:
        return ("sans", max(11, round(14 * self.style["scale"])), "bold")

    def f_pending(self) -> tuple:
        return ("sans", max(9, round(11 * self.style["scale"])))

    # ---- 布局（对话 / 外部 / 内部） ----

    def _sync_pane_sources(self) -> None:
        """把每个角色当前用的音频路同步到栏目上（来源可在运行时随时改）。"""
        role_kinds = dialog_role_kinds(self.dialog)
        for role, pane in self._panes.items():
            pane.source_kind = role_kinds.get(role, "external")

    def _source_role(self, kind: str) -> str | None:
        """这一路音频"归"哪个角色显示（左栏角色优先；没人用则 None）。

        外部/内部是**单界面**：只看这一路音频，不再按角色分两栏。
        """
        roles = dialog_roles(self.dialog)
        left = dialog_left_role(self.dialog)
        for role in (left, "other" if left == "self" else "self"):
            if roles[role]["source"] == kind:
                return role
        return None

    def panes_for_kind(self, kind: str) -> list["_Pane"]:
        """这一路音频当前显示在哪些栏。

        - 对话：所有用这一路的角色栏（两个角色同源时可以有两栏，各翻一遍）；
        - 外部/内部（单一界面）：只有一栏 —— 这一路的主角色栏。
        """
        if is_source_layout(self.style.get("layout")):
            role = self._source_role(kind)
            return [self._panes[role]] if role else []
        return [p for p in self._panes.values() if p.source_kind == kind]

    def _set_layout(self, mode: str) -> None:
        """三种布局：

        - 对话（dialog）：两条字幕流并排（按**角色**），聊天窗式——自己靠右、
          对方靠左、说话侧高亮；
        - 外部（external）：只看**麦克风**这一路的单一界面；
        - 内部（internal）：只看**系统声音**这一路的单一界面；
        - 单一界面没角色在用这一路时，给明确提示（而不是悄悄显示别的）。
        两栏一律用 grid + uniform 保证**等宽**（pack 会按内容宽度分，两边不一样宽）。
        """
        mode = norm_layout(mode)
        self.style["layout"] = mode
        self._style_topbar()
        self._sync_pane_sources()              # 角色 -> 当前音频路
        for pane in self._panes.values():
            pane.frame.grid_forget()
        self._layout_hint.grid_forget()

        bidir = bool(self.dialog.get("bidirectional", True))
        roles = dialog_roles(self.dialog)
        left_role = dialog_left_role(self.dialog)
        right_role = "other" if left_role == "self" else "self"

        def suffix_of(role: str) -> str:
            cfg = roles[role]
            return (f"{cfg['src_lang']} → {cfg['dst_lang']}" if bidir
                    else self._lang_label)

        def in_column(pane, col: int) -> None:
            pane.frame.grid(row=0, column=col, sticky="nsew",
                            padx=(0, 6) if col == 0 else (6, 0))

        for col in (0, 1):
            self.header.grid_columnconfigure(col, weight=1, uniform="pane")

        if mode == "dialog":
            for i, role in enumerate((left_role, right_role)):
                pane = self._panes[role]
                pane.title_base = (f"{dialog_role_label(role)}"
                                   f" · {dialog_audio_short(roles[role]['source'])}")
                pane.lang_suffix = suffix_of(role)
                pane.refresh_title()
                # 聊天窗式：自己（你）靠右，对方靠左
                pane.set_align("right" if role == "self" else "left")
                in_column(pane, i)
            self._set_active(self._active_kind or roles[left_role]["source"])
        else:                                            # external / internal
            want = mode                              # external=麦克风 / internal=系统声音
            role = self._source_role(want)
            if role is None:
                self._layout_hint.config(
                    text=f"当前没有角色使用「{dialog_audio_short(want)}」\n"
                         f"在「对话设置」里把某个角色的音频来源改成它即可")
                self._layout_hint.grid(row=0, column=0, columnspan=2,
                                       sticky="nsew")
                self.header.grid_columnconfigure(1, weight=0, uniform="")
            else:
                pane = self._panes[role]
                src = dialog_audio_short(want)
                # 同一路被两个角色共用时，方向只能取一个：标明按谁的设置
                shared = sum(1 for r in ("self", "other")
                             if roles[r]["source"] == want) > 1
                pane.title_base = (f"{src}（按「{dialog_role_label(role)}」）"
                                   if shared else src)
                pane.lang_suffix = suffix_of(role)
                pane.refresh_title()
                pane.set_align("left")
                pane.set_active(True)
                in_column(pane, 0)
                self.header.grid_columnconfigure(1, weight=0, uniform="")

        if self.on_style_change is not None:
            self.on_style_change(dict(self.style))

    def _style_topbar(self) -> None:
        """模式按钮选中态（样式/背景变更后重刷）。"""
        bg = self.bg()
        self._topbar.config(bg=bg)
        self.btn_jump.config(bg=bg)
        self.btn_top.config(bg=bg)
        self.btn_export.config(bg=bg)
        self.btn_dialog.config(bg=bg)
        for w in self._topbar.winfo_children():
            if isinstance(w, tk.Button) and w not in (self.btn_jump, self.btn_top) \
                    and w.cget("text") != "样式":
                mode = next((m for m, b in self._mode_btns.items() if b is w), None)
                if mode:
                    sel = mode == self.style.get("layout", DEFAULT_LAYOUT)
                    w.config(bg=SIGNAL if sel else bg,
                             fg="#0d1420" if sel else FG_PENDING)

    # ---- 置顶 ----

    def _toggle_topmost(self) -> None:
        self.topmost = not self.topmost
        try:
            self.root.attributes("-topmost", self.topmost)
        except tk.TclError:
            pass
        self.btn_top.config(text="置顶 ✓" if self.topmost else "置顶 ✗")

    # ---- 跳到最新 ----

    def _jump_to_latest(self) -> None:
        for pane in self._panes.values():
            pane.follow = True
            pane.stick_bottom()

    # ---- 暂停 ----

    def _toggle_pause(self, role: str) -> None:
        """暂停/恢复某个角色：只停它的翻译与上屏，音频照常供另一角色。"""
        paused = role not in self.paused_roles
        pane = self._panes.get(role)
        if paused:
            self.paused_roles.add(role)
            if pane is not None:
                self.pq.put((pane.source_kind, ""))    # 清"识别中"
        else:
            self.paused_roles.discard(role)
        if pane is not None:
            pane.set_paused(paused, self)
        if self.on_pause_toggle is not None:
            self.on_pause_toggle(role, paused)

    # ---- 会话文件 / 导出 ----

    def set_session_path(self, path) -> None:
        """登记本次会话的 JSONL（导出数据源）；启动时由 main 调用。"""
        self.session_path = Path(path) if path else None

    def set_lang_labels(self, label: str) -> None:
        """全局语言标签（如 "自动 → 中文"）：仅在"单向"时显示在各栏标题上。"""
        self._lang_label = label or ""
        if self.dialog.get("bidirectional", True):
            return                              # 双向：各栏显示自己的角色方向
        for pane in self._panes.values():
            pane.lang_suffix = self._lang_label
            pane.refresh_title()

    # ---- 对话模式设置（两侧来源 + 各自翻译方向） ----

    def set_dialog(self, dialog: dict) -> None:
        """更新对话设置并立即重排（角色的来源/方向改了都能立刻反映）。"""
        self.dialog.update({k: v for k, v in (dialog or {}).items()
                            if k in DIALOG_DEFAULTS})
        self._set_layout(norm_layout(self.style.get("layout")))

    def _apply_dialog(self, patch: dict) -> None:
        """面板里的改动：本地生效 + 刷新面板 + 回调 main（保存并重建翻译器）。"""
        self.set_dialog(patch)
        refresh = getattr(self, "_dlg_refresh", None)
        if callable(refresh):
            refresh()                     # 让"另一侧"的下拉同步显示
        if self.on_dialog_change is not None:
            self.on_dialog_change(dict(self.dialog))

    def _open_dialog_panel(self) -> None:
        """「对话设置」：两个角色（你/对方）各自选音频来源与翻译方向。

        - 每个角色都能选麦克风或系统声音（可各选一路，也可都选同一路）；
        - 两个角色各自配 源语言 / 译成；
        - 「左栏显示」只决定谁在左边（纯布局）。
        """
        if getattr(self, "_dlg_win", None) is not None \
                and self._dlg_win.winfo_exists():
            self._dlg_win.destroy()
        win = tk.Toplevel(self.root)
        win.title("对话模式设置")
        win.configure(bg=PANEL, padx=18, pady=14)
        win.transient(self.root)
        self._dlg_win = win
        P = PANEL
        audio_values = list(DIALOG_AUDIO_LABELS.values())     # 中文标签，不用 external/internal

        def head(text: str, row: int) -> int:
            tk.Label(win, text=text, bg=P, fg=SIGNAL, font=("sans", 10, "bold")
                     ).grid(row=row, column=0, columnspan=3, sticky="w",
                            pady=(10 if row else 0, 2))
            return row + 1

        def combo(row: int, label: str, values, on_pick, width: int = 18,
                  tip: str = "改动立即生效并保存（无需重启）") -> "ttk.Combobox":
            tk.Label(win, text=label, bg=P, fg="#dce3ee", font=("sans", 10)
                     ).grid(row=row, column=0, sticky="w", pady=3)
            cb = ttk.Combobox(win, width=width, state="readonly",
                              values=list(values))
            cb.grid(row=row, column=1, sticky="w", padx=(8, 16), pady=3)
            cb.bind("<<ComboboxSelected>>", lambda _e: on_pick(cb.get()))
            _Tooltip(cb, tip)
            return cb

        def pick_source(role: str, label: str) -> None:
            """给某个角色选音频来源：**只改它自己**（两个角色可以指向同一路）。

            换来源不会改变该角色的翻译方向、也不会新建翻译器，上下文保持连续。
            """
            kind = DIALOG_AUDIO_VALUES.get(label)
            if kind is None:
                kind = label if label in DIALOG_AUDIO_LABELS else "external"
            self._apply_dialog({f"{role}_source": kind})

        # 双向同传开关
        bidir_var = tk.BooleanVar(value=bool(self.dialog.get("bidirectional", True)))
        chk = tk.Checkbutton(
            win, text="双向同传（两个角色各按自己的方向翻译）", variable=bidir_var,
            bg=P, fg="#dce3ee", selectcolor=PANEL_2, activebackground=P,
            activeforeground=INK, font=("sans", 10), bd=0, highlightthickness=0,
            anchor="w",
            command=lambda: self._apply_dialog({"bidirectional": bool(bidir_var.get())}))
        chk.grid(row=0, column=0, columnspan=3, sticky="w")
        _Tooltip(chk, "开启：每个角色按自己的「译成」翻译（例如 你说中文→英文、\n"
                      "对方说英文→中文）；关闭：两路统一用「翻译」页的单向设置")

        r = head("显示位置", 1)
        cb_left = combo(r, "左栏显示", list(DIALOG_ROLE_LABELS.values()),
                        lambda v: self._apply_dialog(
                            {"left_role": DIALOG_ROLE_VALUES.get(v, "self")}),
                        width=22,
                        tip="左右栏只是布局：谁显示在左边由这里决定\n"
                            "（各自的音频来源与翻译方向不受影响）")

        r2 = head("你（自己）", r + 1)
        r3 = head("对方", r2 + 3)

        summary = tk.Label(win, text="", bg=P, fg=INK, font=("sans", 10, "bold"),
                           justify="left")
        summary.grid(row=r3 + 3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        tk.Label(win, text="两个角色的音频来源相互独立：可以各用一路，也可以都选同一路\n"
                           "（线下两人共用麦克风时，用各栏的「暂停」键轮流说话）。\n"
                           "换来源只换输入：该角色的翻译方向与上下文都不变。",
                 bg=P, fg=FG_META, font=("sans", 9), justify="left").grid(
            row=r3 + 4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        # 同源提示：说明"为什么对话模式两栏都写着系统声音"
        same_tip = tk.Label(win, text="", bg=P, fg=WARN, font=("sans", 9),
                            justify="left", wraplength=460)
        same_tip.grid(row=r3 + 5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._dlg_same_tip = same_tip

        def refresh(*_a) -> None:
            """把面板刷新成当前设置（含从别处改动后的同步）。"""
            try:
                if not win.winfo_exists():
                    return
            except tk.TclError:
                return
            roles = dialog_roles(self.dialog)
            left_role = dialog_left_role(self.dialog)
            cb_left.set(DIALOG_ROLE_LABELS.get(left_role, ""))
            cbs: dict[str, list] = {}
            for role, widgets in (("self", (cb_self_src, cb_self_ln, cb_self_dt)),
                                  ("other", (cb_other_src, cb_other_ln,
                                             cb_other_dt))):
                cfg = roles[role]
                widgets[0].set(DIALOG_AUDIO_LABELS.get(cfg["source"], ""))
                widgets[1].set(cfg["src_lang"])
                widgets[2].set(cfg["dst_lang"])
                cbs[role] = list(widgets)
            where = {role: ("左栏" if role == left_role else "右栏")
                     for role in roles}
            summary.config(text=(
                f"你：{DIALOG_AUDIO_SHORT[roles['self']['source']]} → "
                f"{roles['self']['dst_lang']}（{where['self']}）\n"
                f"对方：{DIALOG_AUDIO_SHORT[roles['other']['source']]} → "
                f"{roles['other']['dst_lang']}（{where['other']}）"))
            self_src = roles["self"]["source"]
            other_src = roles["other"]["source"]
            if self_src == other_src:
                src = DIALOG_AUDIO_SHORT[self_src]
                same_tip.config(text=(
                    f"提示：两个角色的音频来源都是「{src}」——对话模式两栏都写 {src}\n"
                    f"（线上会议在一个音频里按声纹分说话人，这是对的）；"
                    f"「外部/内部」单一界面则按左栏角色的方向翻。\n"
                    f"想看到「麦克风 ｜ 系统声音」两栏，把其中一个角色改成另一路即可。"))
            else:
                same_tip.config(text="")   # 各用一路：无需提示

        tip_src = ("这个角色用哪一路音频：麦克风（自己的声音）或系统声音（电脑播放）\n"
                   "两个角色可各选一路，也可都选同一路（共用麦克风：用暂停键轮流说话）\n"
                   "换来源不会重置该角色的翻译上下文")
        tip_src_lang = "这个角色说话的源语言；不确定就选「自动」"
        tip_dst = ("这个角色的话译成什么语言\n"
                   "选「自动（中↔英）」= 中文说进去译成英文、英文说进去译成中文")
        cb_self_src = combo(r2, "音频来源", audio_values,
                            lambda v: pick_source("self", v), tip=tip_src)
        cb_self_ln = combo(r2 + 1, "源语言", DIALOG_SRC_LANGS,
                           lambda v: self._apply_dialog({"self_src_lang": v}),
                           tip=tip_src_lang)
        cb_self_dt = combo(r2 + 2, "译成", DIALOG_DST_LANGS,
                           lambda v: self._apply_dialog({"self_dst_lang": v}),
                           tip=tip_dst)
        cb_other_src = combo(r3, "音频来源", audio_values,
                             lambda v: pick_source("other", v), tip=tip_src)
        cb_other_ln = combo(r3 + 1, "源语言", DIALOG_SRC_LANGS,
                            lambda v: self._apply_dialog({"other_src_lang": v}),
                            tip=tip_src_lang)
        cb_other_dt = combo(r3 + 2, "译成", DIALOG_DST_LANGS,
                            lambda v: self._apply_dialog({"other_dst_lang": v}),
                            tip=tip_dst)

        self._dlg_refresh = refresh
        refresh()

    def _export(self, mode: str = "bilingual", as_txt: bool = False) -> None:
        """导出本次会话（另存为对话框 + 后台线程，不阻塞字幕刷新）。"""
        src = Path(self.session_path) if self.session_path else None
        if src is None or not src.is_file():
            self.set_status("这次会话还没开始（或日志尚未生成），暂无可导出的内容")
            return
        ext = ".txt" if as_txt else ".srt"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出字幕", initialdir=str(src.parent),
            initialfile=src.stem + ext, defaultextension=ext,
            filetypes=[("文本文件", "*.txt")] if as_txt else
                      [("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if not path:
            return
        dest = Path(path)
        self.set_status(f"导出中：{dest.name} ...")

        def work() -> None:
            from livetrans.export import export
            try:
                out = export(src, out=dest, mode=mode, as_txt=as_txt)
                msg = f"已导出 {out.name}（{out.stat().st_size / 1024:.0f} KB）✓"
            except Exception as e:  # noqa: BLE001 - 导出失败只提示，不影响字幕
                msg = f"导出失败：{e}"
            self.set_status(msg)          # 队列投递（跨线程安全，无需 Tk 调用）

        threading.Thread(target=work, daemon=True, name="export").start()

    # ---- 对话模式：活跃栏 + 脉冲光标 ----

    def _set_active(self, kind: str) -> None:
        """对话模式：点亮"正在用这一路音频"的栏（其余布局不区分主次）。"""
        if self.style.get("layout") != "dialog" or kind == self._active_kind:
            return
        self._active_kind = kind
        for pane in self._panes.values():
            pane.set_active(pane.source_kind == kind)

    def _pulse(self) -> None:
        """"… 翻译中" 末尾的脉冲光标：交替显示，直到译文到达。"""
        self._pulse_phase = not self._pulse_phase
        suffix = PULSE if self._pulse_phase else ""
        for pane in self._panes.values():
            for txt in list(pane.pending.values()):
                if txt.winfo_exists():
                    pane._set_text(txt, PENDING + suffix)
        if self._stop_event is not None and self._stop_event.is_set():
            return
        self._pulse_job = self.root.after(PULSE_MS, self._pulse)

    # ---- 样式 ----

    def apply_style(self, patch: dict) -> None:
        """应用样式补丁（文字不透明度/字号/颜色），刷新存量标签，并回调持久化。"""
        self.style.update(patch)
        bg = self.bg()
        self.root.configure(bg=bg)
        self.status.config(bg=bg)
        self._style_topbar()
        for pane in self._panes.values():
            pane.restyle(self)
        if self.on_style_change is not None:
            self.on_style_change(dict(self.style))

    def _open_style_panel(self) -> None:
        if getattr(self, "_style_win", None) is not None \
                and self._style_win.winfo_exists():
            self._style_win.destroy()
        win = tk.Toplevel(self.root)
        win.title("字幕样式")
        win.configure(bg="#1c2230", padx=18, pady=14)
        win.transient(self.root)
        win.minsize(470, 360)                 # 可拖动边界缩放
        self._style_win = win
        P = PANEL

        def section(i: int, title: str) -> int:
            """区块标题 + 分隔线；返回下一可用行。"""
            tk.Label(win, text=title, bg=P, fg=SIGNAL,
                     font=("sans", 10, "bold")).grid(row=i, column=0,
                                                     columnspan=3, sticky="w",
                                                     pady=(2, 4))
            tk.Frame(win, bg=BORDER, height=1).grid(row=i + 1, column=0,
                                                    columnspan=3, sticky="ew")
            return i + 2

        def slider(i: int, label: str, key: str, lo: float, hi: float,
                   tip: str = "") -> None:
            lab = tk.Label(win, text=label, bg=P, fg="#dce3ee",
                           font=("sans", 10))
            lab.grid(row=i, column=0, sticky="w", pady=5, padx=(0, 10))
            sc = tk.Scale(win, from_=lo, to=hi, resolution=0.05,
                          orient="horizontal", length=170, bg=P,
                          fg="#dce3ee", highlightthickness=0,
                          command=lambda v: self.apply_style({key: float(v)}))
            sc.set(float(self.style[key]))
            sc.grid(row=i, column=1, columnspan=2, sticky="w")
            if tip:
                _Tooltip(lab, tip)
                _Tooltip(sc, tip)

        def colors(i: int, label: str, key: str, opts: list) -> None:
            tk.Label(win, text=label, bg=P, fg="#dce3ee",
                     font=("sans", 10)).grid(row=i, column=0, sticky="w",
                                             pady=4, padx=(0, 10))
            what = "译文" if key == "dst_color" else "原文"
            for j, (name, hexv) in enumerate(opts):
                b = tk.Button(win, text=name, width=4, bg=hexv,
                              fg="#0d1420", relief="flat", bd=0, cursor="hand2",
                              activebackground=hexv, font=("sans", 9),
                              command=lambda h=hexv: self.apply_style({key: h}))
                b.grid(row=i, column=1, sticky="w", padx=(j * 48, 0), pady=2)
                _Tooltip(b, f"{what}文字颜色：{name}")

        r = section(0, "文字")
        slider(r, "不透明度", "text_opacity", 0.3, 1.0,
               "文字不透明度：调低会让文字变淡（背景不变）")
        slider(r + 1, "字号", "scale", 0.8, 1.6,
               "整体字号缩放：同时影响原文与译文")
        colors(r + 2, "译文颜色", "dst_color", DST_COLORS)
        colors(r + 3, "原文颜色", "src_color", SRC_COLORS)

        r2 = section(r + 4, "动效")
        anim_var = tk.BooleanVar(value=bool(self.style.get("animate", True)))
        chk = tk.Checkbutton(
            win, text="新字幕淡入 + 翻译中脉冲", variable=anim_var, bg=PANEL,
            fg="#dce3ee", selectcolor=PANEL_2, activebackground=PANEL,
            activeforeground=INK, font=("sans", 10), bd=0,
            highlightthickness=0, anchor="w",
            command=lambda: self.apply_style({"animate": bool(anim_var.get())}))
        chk.grid(row=r2, column=0, columnspan=3, sticky="w", pady=(0, 4))
        _Tooltip(chk, "开启：新条目文字淡入（约 200ms），未出译文时显示脉冲光标\n"
                      "关闭：字幕直接出现（省一点 CPU）")

        win.grid_columnconfigure(1, weight=1)

    # ---- 线程安全入口 ----

    def post(self, item: DisplayItem) -> None:
        self.q.put(item)

    def update_translation(self, item_id: str, text: str,
                           llm_ms: float | None = None) -> None:
        """译文推进/完成（配合 translation=None 先上原文）；llm_ms 回填延迟指标。"""
        self.uq.put((item_id, text, llm_ms))

    def set_partial(self, kind: str, text: str) -> None:
        """更新某栏"识别中"实时行（说话中流式文本）；text 为空则清空。

        任意线程可调，UI 线程轮询消费。
        """
        self.pq.put((kind, text))

    def set_level(self, kind: str, rms: float) -> None:
        """实时音量（kind: internal/external；rms 0~1）。"""
        self.lq.put((kind, rms))

    def set_status(self, text: str) -> None:
        """更新底部状态栏（任意线程可调，UI 线程轮询消费）。"""
        self._status_q.put(text)

    # ---- UI 线程 ----

    def _consume_partials(self) -> None:
        try:
            while True:
                kind, text = self.pq.get_nowait()
                if text:
                    self._set_active(kind)             # 对话模式：说话侧高亮
                for pane in self.panes_for_kind(kind):
                    if text:
                        pane.upsert_live(text, self)   # 边说边上屏（主流内）
                        if pane.follow:
                            pane.stick_bottom()        # 进行中条目增高，保持贴底
                    else:
                        pane.finalize_live()           # 清空信号：定稿/放弃
        except queue.Empty:
            pass

    def _consume_levels(self) -> None:
        latest: dict[str, float] = {}
        try:
            while True:
                kind, rms = self.lq.get_nowait()
                latest[kind] = rms            # 只留最新
        except queue.Empty:
            pass
        for kind, rms in latest.items():
            for pane in self.panes_for_kind(kind):
                role = pane.role or kind          # 电平按栏（角色）平滑
                level = 0.0 if role in self.paused_roles else rms
                ema = self._levels.get(role, 0.0) * 0.55 + level * 0.45
                self._levels[role] = ema
                pane.set_level(ema)

    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                # 按音频路找到使用它的栏；对话模式同一路可能有两个角色在用，
                # 此时一句话会带角色各自产出一条（两栏各翻一遍）
                panes = [p for p in self.panes_for_kind(item.kind)
                         if not item.role or p.role == item.role]
                if not panes:
                    continue
                self._set_active(item.kind)     # 对话模式：说话侧高亮
                for pane in panes:
                    pane.finalize_live()        # 移除进行中条目（正式条目接替）
                    pane.add_item(item, self)
                    if pane.follow:             # follow 由滚轮/滚动条驱动
                        pane.stick_bottom()
        except queue.Empty:
            pass
        followed = False
        try:
            while True:
                item_id, text, llm_ms = self.uq.get_nowait()
                for k, pane in self._panes.items():
                    pane.update_translation(item_id, text, llm_ms, self)
                followed = True               # 译文/增量可能撑高内容
        except queue.Empty:
            pass
        if followed:
            for pane in self._panes.values():
                if pane.follow:               # follow 态：增高后仍保持贴底
                    pane.stick_bottom()
        try:
            while True:
                self.status.config(text=self._status_q.get_nowait())
        except queue.Empty:
            pass
        self._consume_partials()
        self._consume_levels()
        if self._stop_event is not None and self._stop_event.is_set():
            self.root.quit()
            return
        self._poll_job = self.root.after(80, self._poll)

    def run(self) -> None:
        self._poll_job = self.root.after(80, self._poll)
        self._pulse_job = self.root.after(PULSE_MS, self._pulse)
        self.root.mainloop()

    def _close(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for job in (getattr(self, "_poll_job", None),
                    getattr(self, "_pulse_job", None)):
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.root.destroy()
