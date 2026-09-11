"""可复用的小部件：悬停提示、右下角状态气泡、统一按钮工厂。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# kind -> ttk 样式（样式定义在 theme.apply_theme，尺寸令牌也只有那一处）
_BTN_STYLE = {
    "primary": "Accent.TButton",     # 主操作（保存并启动 / 发送）
    "secondary": "TButton",          # 常规操作（默认）
    "chip": "Chip.TButton",          # 卡片内小按钮（显示/清除/载入）
    "ghost": "Ghost.TButton",        # 顶栏轻量入口
}


def button(parent, text: str, command=None, kind: str = "secondary",
           **kw) -> "ttk.Button":
    """统一按钮：全程序都经由它创建，样式/尺寸只认 theme 里的令牌。

    kind: primary（主操作）/ secondary（常规）/ chip（卡片内小按钮）/ ghost（轻量）
    """
    return ttk.Button(parent, text=text, command=command,
                      style=_BTN_STYLE.get(kind, "TButton"), **kw)


class ToolTip:
    """悬停气泡：把界面上的小字注释收纳到 hover 提示，主界面保持清爽。"""

    def __init__(self, widget, text: str, font=("sans", 9), delay: int = 450):
        self.w, self.text, self.font, self.delay = widget, text, font, delay
        self.tip: tk.Toplevel | None = None
        self._job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, *_a) -> None:
        self._cancel()
        self._job = self.w.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.w.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _show(self) -> None:
        if self.tip is not None or not self.text or not self.w.winfo_exists():
            return
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        t = tk.Toplevel(self.w)
        t.wm_overrideredirect(True)
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass
        t.wm_geometry(f"+{x}+{y}")
        tk.Label(t, text=self.text, justify="left", bg="#0b0f18", fg="#c6cfdd",
                 font=self.font, bd=1, relief="solid", padx=10, pady=6).pack()
        self.tip = t

    def _leave(self, *_a) -> None:
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

class StatusToast:
    """右下角浮动的状态气泡：替代常驻状态栏，长文本换行、不拉伸窗口。

    - place 锚定窗口右下角（不参与布局计算，绝不改变窗口尺寸）；
    - 按消息颜色决定停留时长（错误更久）；新消息直接替换旧气泡；
    - 鼠标悬停时暂停消失计时，移开后重启。
    """

    def __init__(self, master, font=("sans", 9)):
        self.master = master
        self.font = font
        self.frame: tk.Frame | None = None
        self._hide_job: str | None = None
        self._remain_ms = 0

    def show(self, text: str, color: str, duration_ms: int = 5000) -> None:
        self._cancel()
        if self.frame is not None:
            self.frame.destroy()
            self.frame = None
        text = text.strip()
        if not text:
            return
        if len(text) > 400:                     # 超长截断，防气泡占满窗口
            text = text[:400] + " …"
        f = tk.Frame(self.master, bg="#0b0f18", highlightthickness=1,
                     highlightbackground=color)
        tk.Frame(f, bg=color, width=3).pack(side="left", fill="y")
        tk.Label(f, text=text, justify="left", anchor="w", bg="#0b0f18",
                 fg="#d5dce8", font=self.font, wraplength=440,
                 padx=10, pady=6).pack(side="left", fill="both")
        # 抬到操作栏之上：原来贴着窗口底边，会压住「运行日志」按钮
        f.place(relx=1.0, rely=1.0, anchor="se", x=-16, y=-72)
        f.bind("<Enter>", self._pause, add="+")
        f.bind("<Leave>", self._resume, add="+")
        self.frame = f
        self._remain_ms = duration_ms
        self._arm()

    def _arm(self) -> None:
        self._cancel()
        self._hide_job = self.master.after(self._remain_ms, self.hide)

    def _cancel(self) -> None:
        if self._hide_job:
            try:
                self.master.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None

    def _pause(self, *_a) -> None:
        self._cancel()

    def _resume(self, *_a) -> None:
        if self.frame is not None:
            self._arm()

    def hide(self) -> None:
        self._cancel()
        if self.frame is not None:
            self.frame.destroy()
            self.frame = None

    @property
    def text(self) -> str:
        """当前显示文本（测试/诊断用）。"""
        if self.frame is None:
            return ""
        for c in self.frame.winfo_children():
            if isinstance(c, tk.Label):
                return c.cget("text")
        return ""
