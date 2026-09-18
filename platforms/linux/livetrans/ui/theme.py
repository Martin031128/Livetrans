"""控制台视觉主题：配色 token、字体选择、ttk 样式与主题色版。"""
from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

_IND_IMGS: list[tk.PhotoImage] = []      # 自绘指示器图片缓存（防 GC）


BG = "#141824"        # 窗口底

PANEL = "#1c2230"     # 卡片/底栏

PANEL_2 = "#262f42"   # 控件面（按钮、下拉）

FIELD = "#10151f"     # 输入域

BORDER = "#2c3548"    # 边线

INK = "#dce3ee"       # 主文字

DIM = "#8b93a7"       # 次级文字（与外挂面板统一）

SIGNAL = "#39c5b8"    # 信号青：操作/就绪/选中

INTERNAL = "#89b4fa"  # 内部音频（系统）蓝

EXTERNAL = "#a6e3a1"  # 外部音频（麦克风）绿

WARN = "#e6b455"      # 警示琥珀

BAD = "#e07080"       # 错误红

ON_SIGNAL = "#0d1420"  # 青底上的深色文字

# ---- 控件尺寸令牌：全局统一（按钮高度、内边距都从这里取，别再各写各的）----
BTN_PAD = (18, 9)          # 标准按钮内边距（约 38px 高）
BTN_PAD_WIDE = (26, 9)     # 主按钮：只加宽、不加高（和标准按钮同高，底部一排能对齐）
BTN_PAD_CHIP = (11, 5)     # 卡片内紧凑小按钮（显示/清除/载入…）
BTN_FONT_BOLD = True       # 主按钮用粗体

def pick_font(candidates: list[str], fallback: str) -> str:
    try:
        fams = set(tkfont.families())
        for c in candidates:
            if c in fams:
                return c
    except tk.TclError:
        pass
    return fallback

def _rows_to_img(rows: list[list[str]]) -> tk.PhotoImage:
    img = tk.PhotoImage(width=len(rows[0]), height=len(rows))
    img.put(" ".join("{" + " ".join(r) + "}" for r in rows))
    return img

def _radio_rows(selected: bool, color: str, bg: str, size: int = 20) -> list[list[str]]:
    """圆环指示器；选中时中心加实心圆点。"""
    cx = cy = (size - 1) / 2
    r_out, r_ring_in, r_dot = size / 2 - 1.5, size / 2 - 4.0, size / 2 - 6.0
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            d = math.hypot(x - cx, y - cy)
            row.append(color if (r_ring_in <= d <= r_out
                                 or (selected and d <= r_dot)) else bg)
        rows.append(row)
    return rows

def _check_rows(selected: bool, color: str, bg: str, size: int = 20) -> list[list[str]]:
    """方框指示器；选中时填充并画深色对勾。"""
    rows = [[bg] * size for _ in range(size)]
    t = 2
    for i in range(size):
        for j in list(range(t)) + list(range(size - t, size)):
            rows[i][j] = color
            rows[j][i] = color
    if selected:
        for i in range(t, size - t):
            for j in range(t, size - t):
                rows[i][j] = color

        def line(x0, y0, x1, y1) -> None:
            steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 3) + 1
            for s in range(steps + 1):
                u = s / steps
                xi, yi = round(x0 + (x1 - x0) * u), round(y0 + (y1 - y0) * u)
                for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    xx, yy = xi + dx, yi + dy
                    if t < xx < size - t and t < yy < size - t:
                        rows[yy][xx] = ON_SIGNAL
        line(size * 0.22, size * 0.52, size * 0.42, size * 0.70)
        line(size * 0.42, size * 0.70, size * 0.78, size * 0.30)
    return rows


SPINBOX_ARROW = 16          # 实测选出的箭头尺寸（供自检/测试观察）


def _align_spinbox(st, root) -> int:
    """让 Spinbox 与 Combobox/Entry 等高：运行时实测箭头尺寸 + 上下内边距。

    Spinbox 高 = max(2 × arrowsize, 文字高) + 2 × pad_y，而 Combobox/Entry 高
    = 文字高 + 2 × pad_y —— 两个量（箭头、内边距）都得动才凑得上：
    只调箭头到不了更低（下限是"文字高 + 内边距"）。所以这里联合试，
    命中完全相等就立刻停（绝大多数机器一两次就够）。
    """
    global SPINBOX_ARROW
    probe = ttk.Combobox(root, width=8, values=["x"], state="readonly")
    probe.update_idletasks()
    target = probe.winfo_reqheight()
    probe.destroy()

    # 常见取值优先试（本机立刻命中），不行再全试一遍
    pads = (6, 5, 7, 4, 8, 3, 2, 1, 0)
    arrows = (16, 14, 18, 12, 20, 10, 22, 8, 24, 6)
    best = (6, 16, 999)
    for pad_y in pads:
        for arrow in arrows:
            st.configure("TSpinbox", arrowsize=arrow, padding=(8, pad_y))
            p = ttk.Spinbox(root, from_=0, to=1, width=4)
            p.update_idletasks()
            diff = abs(p.winfo_reqheight() - target)
            p.destroy()
            if diff < best[2]:
                best = (pad_y, arrow, diff)
            if diff == 0:
                st.configure("TSpinbox", arrowsize=arrow, padding=(8, pad_y))
                SPINBOX_ARROW = arrow
                return arrow
    pad_y, arrow, _ = best
    st.configure("TSpinbox", arrowsize=arrow, padding=(8, pad_y))
    SPINBOX_ARROW = arrow
    return arrow


def apply_theme(root, ui_font: str, mono_font: str) -> ttk.Style:
    """深色主题 + 自绘指示器（原 launcher._setup_style 原样迁移）。"""
    # Combobox 的下拉列表是经典 tk Listbox，须用 option_add 上色
    root.option_add("*TCombobox*Listbox.background", PANEL_2)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", SIGNAL)
    root.option_add("*TCombobox*Listbox.selectForeground", ON_SIGNAL)
    root.option_add("*TCombobox*Listbox.borderWidth", 1)
    root.option_add("*TCombobox*Listbox.relief", "flat")

    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=BG, foreground=INK, borderwidth=0,
                 font=(ui_font, 10))
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=INK)
    st.configure("Panel.TLabel", background=PANEL, foreground=INK)
    st.configure("Dim.TLabel", foreground=DIM)
    st.configure("Small.TLabel", font=(ui_font, 9))
    st.configure("DimSmall.TLabel", foreground=DIM, font=(ui_font, 9))
    st.configure("Panel.DimSmall.TLabel", background=PANEL, foreground=DIM,
                 font=(ui_font, 9))
    st.configure("Badge.TLabel", font=(ui_font, 9), background=PANEL)
    st.configure("Title.TLabel", font=(ui_font, 16, "bold"))
    st.configure("Mono.TLabel", font=(mono_font, 9), foreground=DIM)

    # 卡片
    st.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER,
                 borderwidth=1, relief="solid")
    st.configure("Card.TLabelframe.Label", background=PANEL,
                 foreground=SIGNAL, font=(ui_font, 10, "bold"))

    # ---- 按钮：四种变体共用同一套字体/高度（尺寸只认上面的令牌）----
    # 1. 标准（次级操作）
    st.configure("TButton", background=PANEL_2, foreground=INK,
                 borderwidth=0, focusthickness=0, padding=BTN_PAD,
                 font=(ui_font, 10))
    st.map("TButton",
           background=[("pressed", BORDER), ("active", BORDER)],
           foreground=[("disabled", DIM)])
    # 2. 主按钮：同高、加宽、加粗（底部「保存并启动」这类）
    st.configure("Accent.TButton", background=SIGNAL, foreground=ON_SIGNAL,
                 font=(ui_font, 10, "bold" if BTN_FONT_BOLD else "normal"),
                 padding=BTN_PAD_WIDE)
    st.map("Accent.TButton",
           background=[("pressed", "#2ba396"), ("active", "#4bd6c9"),
                       ("disabled", "#2a5a57")],
           foreground=[("disabled", "#123532")])
    # 3. 卡片内小按钮：卡片底色上的紧凑 chip（显示/清除/载入…）
    st.configure("Chip.TButton", background=PANEL_2, foreground=INK,
                 font=(ui_font, 9), padding=BTN_PAD_CHIP)
    st.map("Chip.TButton",
           background=[("pressed", BORDER), ("active", BORDER)],
           foreground=[("disabled", DIM)])
    # 4. 幽灵按钮（顶栏轻量入口：应用菜单安装等）
    st.configure("Ghost.TButton", background=BG, foreground=DIM,
                 font=(ui_font, 9), padding=(10, 5))
    st.map("Ghost.TButton",
           background=[("pressed", PANEL_2), ("active", PANEL_2)],
           foreground=[("active", INK), ("disabled", "#4a5a6e")])

    # Notebook
    st.configure("TNotebook", background=BG, borderwidth=0,
                 tabmargins=(0, 6, 0, 0))
    st.configure("TNotebook.Tab", background=BG, foreground=DIM,
                 padding=(16, 8), font=(ui_font, 10))
    st.map("TNotebook.Tab",
           background=[("selected", PANEL)],
           foreground=[("selected", SIGNAL), ("hover", INK)])

    # 输入控件（下拉/输入框整体放大：箭头 20px、内边距加大）
    for w in ("TCombobox", "TSpinbox"):
        st.configure(w, fieldbackground=FIELD, background=PANEL_2,
                     foreground=INK, arrowcolor=SIGNAL, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, insertcolor=INK,
                     selectbackground=PANEL_2, selectforeground=INK,
                     arrowsize=20, padding=(8, 6))
        st.map(w, fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
               foreground=[("disabled", DIM)],
               arrowcolor=[("disabled", DIM)])
    # Spinbox 的高度由「箭头尺寸」决定，而 Combobox/Entry 的高度由字体+padding 决定：
    # 写死 arrowsize 只是在本机字体下凑巧等高 —— 换个字体/DPI 就错开几像素
    # （CI 上就报过 输入域高度 [33, 36, 33]）。这里现场量一次再定箭头尺寸。
    _align_spinbox(st, root)
    # 滚动条：默认 ttk 是浅灰，放在深色面板上很扎眼 → 统一主题化
    for w in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        st.configure(w, background=PANEL_2, troughcolor=FIELD,
                     bordercolor=BORDER, arrowcolor=SIGNAL,
                     lightcolor=PANEL_2, darkcolor=PANEL_2,
                     borderwidth=0, arrowsize=14)
        st.map(w, background=[("pressed", BORDER), ("active", BORDER)],
               arrowcolor=[("disabled", DIM)])
    # 进度条：默认 ttk 是浅灰底，深色主题下很刺眼 → 统一（向导/控制台共用）
    for w in ("Horizontal.TProgressbar", "TProgressbar"):
        st.configure(w, background=SIGNAL, troughcolor=FIELD,
                     bordercolor=BORDER, lightcolor=SIGNAL, darkcolor=SIGNAL,
                     borderwidth=0, thickness=8)
    st.configure("TScale", background=PANEL_2, troughcolor=FIELD,
                 borderwidth=0, lightcolor=BORDER, darkcolor=BORDER,
                 focuscolor=PANEL)
    st.map("TScale", background=[("active", SIGNAL)])
    st.configure("TEntry", fieldbackground=FIELD, foreground=INK,
                 insertcolor=SIGNAL, bordercolor=BORDER, lightcolor=BORDER,
                 darkcolor=BORDER, padding=(8, 6), selectbackground=SIGNAL,
                 selectforeground=ON_SIGNAL)
    st.map("TEntry", fieldbackground=[("disabled", PANEL)],
           foreground=[("disabled", DIM)])

    # 复选/单选：自绘 20px 大指示器（信号青 / 外绿 / 内蓝），替换主题小圆点
    def _ind(name: str, off_rows, on_rows) -> None:
        off, on = _rows_to_img(off_rows), _rows_to_img(on_rows)
        _IND_IMGS.append(off)            # 模块级持有，防 GC
        _IND_IMGS.append(on)
        try:
            st.element_create(name, "image", off, ("selected", on),
                              sticky="w")
        except tk.TclError:
            pass    # 已注册（样式重建）

    _ind("Ind.Check.signal", _check_rows(False, SIGNAL, PANEL),
         _check_rows(True, SIGNAL, PANEL))
    _ind("Ind.Check.ext", _check_rows(False, EXTERNAL, PANEL),
         _check_rows(True, EXTERNAL, PANEL))
    _ind("Ind.Check.int", _check_rows(False, INTERNAL, PANEL),
         _check_rows(True, INTERNAL, PANEL))
    _ind("Ind.Radio", _radio_rows(False, SIGNAL, PANEL),
         _radio_rows(True, SIGNAL, PANEL))
    for style_name, ind, base in (
            ("Card.TCheckbutton", "Ind.Check.signal", "Checkbutton"),
            ("Ext.TCheckbutton", "Ind.Check.ext", "Checkbutton"),
            ("Int.TCheckbutton", "Ind.Check.int", "Checkbutton"),
            ("Card.TRadiobutton", "Ind.Radio", "Radiobutton")):
        st.layout(style_name, [
            (f"{base}.button", {"sticky": "nswe", "children": [
                (f"{base}.focus", {"sticky": "nswe", "children": [
                    (ind, {"side": "left", "sticky": ""}),
                    (f"{base}.label", {"sticky": "nswe"}),
                ]}),
            ]})])
    for s in ("Card.TCheckbutton", "Ext.TCheckbutton", "Int.TCheckbutton"):
        st.configure(s, background=PANEL, foreground=INK, focuscolor=PANEL,
                     padding=(6, 5))
        st.map(s, background=[("active", PANEL)])
    st.configure("Card.TRadiobutton", background=PANEL, foreground=INK,
                 focuscolor=PANEL, padding=(6, 5))
    st.map("Card.TRadiobutton", background=[("active", PANEL)])
    return st


def pick_fonts() -> tuple[str, str]:
    """按可用性挑选界面字体与等宽字体（中文优先，Windows/Linux 双栈）。

    Windows 候选放最前（雅黑 UI 是 Win8+ 自带）：此前只有 Linux 字体候选，
    Windows 上必然回退 "sans"→宋体，整个控制台文字发虚显旧（移植简报 3.1-13）。
    """
    ui = pick_font(["Microsoft YaHei UI", "Microsoft YaHei",
                    "Noto Sans CJK SC", "Noto Sans SC",
                    "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
                    "Droid Sans Fallback"], "sans")
    mono = pick_font(["Cascadia Mono", "Consolas",
                      "DejaVu Sans Mono", "Noto Sans Mono CJK SC",
                      "Liberation Mono"], "monospace")
    return ui, mono


_UI_FONT: str | None = None


def ui_family() -> str:
    """中文字体族（懒解析并缓存）：雅黑优先，取不到回退 Tk 默认。

    供字幕窗/气泡等写死 "sans" 的散点收口用——字体族列表需要 Tk root
    就绪后才能枚举，故懒解析、不能在模块导入期调用。
    """
    global _UI_FONT
    if _UI_FONT is None:
        fam = ""
        try:
            lower = {f.lower(): f for f in tkfont.families()}
            for want in ("microsoft yahei ui", "microsoft yahei",
                         "noto sans cjk sc", "noto sans sc",
                         "wenquanyi micro hei", "wenquanyi zen hei"):
                if want in lower:
                    fam = lower[want]
                    break
        except tk.TclError:
            pass
        _UI_FONT = fam or "sans"
    return _UI_FONT
