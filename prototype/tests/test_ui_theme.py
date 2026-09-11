"""控制台 UI 规范回归：按钮分两档同高、输入域同高、操作栏不被挤掉、声纹控件够大。

用户反馈过两件事：
  · 音频源「声纹角色标注」处的选择控件过小；
  · 各处按钮风格/大小不统一。
"""
import sys
import time
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher import Launcher                        # noqa: E402
from livetrans.ui.widgets import button              # noqa: E402

app = Launcher()
for _ in range(6):
    app.update()
    time.sleep(0.1)

# ---- 1) 按钮两档高度（标准 38 / 紧凑 28），同档必须完全一致 ----
f = ttk.Frame(app)
rows = {
    "standard": [button(f, "保存并启动"), button(f, "仅保存设置")],
    "primary": [button(f, "▶  保存并启动", kind="primary")],
    "chip": [button(f, "显示", kind="chip"), button(f, "清除", kind="chip")],
    "ghost": [button(f, "添加到应用菜单", kind="ghost")],
}
for ws in rows.values():
    for w in ws:
        w.grid(row=0, column=0)
app.update()
app.update_idletasks()
heights = {k: {w.winfo_reqheight() for w in ws} for k, ws in rows.items()}
print("按钮高度:", {k: sorted(v) for k, v in heights.items()})
# 只断言"同档一致 + 两档分得开"，不写死像素：不同字体/DPI 下度量会变（CI 上曾因此误报）
assert len(heights["standard"]) == 1, f"标准按钮高度应一致：{heights['standard']}"
assert heights["standard"] == heights["primary"], "主按钮应与标准按钮同高"
assert len(heights["chip"]) == 1 and len(heights["ghost"]) == 1
assert heights["chip"] == heights["ghost"], "紧凑两档应同高"
assert max(heights["standard"]) - max(heights["chip"]) >= 5, \
    f"标准档应明显高于紧凑档：{heights}"
assert 30 <= max(heights["standard"]) <= 46, heights["standard"]
for ws in rows.values():
    for w in ws:
        w.destroy()

# ---- 2) 输入域同高（下拉 / 数字框 / 输入框都是 36）----
fields = [ttk.Combobox(f, width=10, values=["a"], state="readonly"),
          ttk.Spinbox(f, from_=0, to=10, width=4),
          ttk.Entry(f, width=10)]
for w in fields:
    w.grid(row=0, column=0)
app.update()
fh = [w.winfo_reqheight() for w in fields]
print("输入域高度:", fh)
# 同样不写死像素：只要求"三者一致"且在合理区间（CI 字体不同不影响这个结论）
assert len(set(fh)) == 1, f"输入域高度应统一：{fh}"
assert 28 <= fh[0] <= 46, fh
for w in fields:
    w.destroy()

# ---- 3) 声纹卡的控件（曾是一个小 Spinbox + 经典 Tk Scale）----
app.nb.select(1)
app.update()
assert isinstance(app.spk_max, ttk.Combobox), type(app.spk_max)
assert app.spk_max.winfo_reqheight() >= 28, app.spk_max.winfo_reqheight()
assert isinstance(app.spk_th, ttk.Scale), type(app.spk_th)
assert app.spk_th.winfo_reqwidth() >= 150, app.spk_th.winfo_reqwidth()
print(f"声纹控件：阈值滑块 {app.spk_th.winfo_reqwidth()}px、"
      f"人数选择 {app.spk_max.winfo_reqheight()}px 高（{app.spk_max.get()}）")
# 数值随滑块同步
app.spk_th.set(0.65)
app.update()
assert app.spk_th_val.cget("text") == "0.65", app.spk_th_val.cget("text")

# ---- 4) 底部操作栏必须可见且同高（曾被记事本挤没）----
assert app.btn_start.winfo_ismapped(), "「保存并启动」不可见"
bar_h = {b.winfo_height() for b in (app.btn_start, app.btn_save_only, app.btn_logs)}
print("操作栏按钮高度:", sorted(bar_h))
assert app.btn_start.winfo_height() >= 30
assert len(bar_h) == 1, f"操作栏三个按钮高度应一致：{bar_h}"

# ---- 5) 窗口高度跟随当前页（矮页不该被高页撑出大片空白）----
app.nb.select(2)                      # 会话总结（矮页）
for _ in range(4):
    app.update()
    time.sleep(0.1)
app.update_idletasks()
short = app.winfo_height()
app.nb.select(0)                      # 翻译后端（最高页）
for _ in range(6):
    app.update()
    time.sleep(0.1)
app.update_idletasks()
tall = app.winfo_height()
print(f"窗口高度：会话总结 {short} / 翻译后端 {tall}")
assert short < tall, (short, tall)
assert tall <= app.winfo_screenheight() - 60, "窗口不应顶出屏幕"

app.destroy()
print("[PASS] UI 规范：按钮两档同高 / 输入域同高 / 声纹控件够大 / 操作栏可见且齐平 / 窗口高度跟随页")
