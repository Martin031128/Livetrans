"""栏内渲染回归：长句必须换行显示完整、栏头不能溢出、两栏等宽。

两个真实 bug：
  ① tk.Text 默认请求宽 ~80 字符，pack 不会把它压到请求宽度以下 → 长句按 600px 排版被窄栏裁掉；
  ② Text 只排版"看得见"的内容 → height=1 时量行数恒为 1，第二行永远被吃掉。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.subtitle import DisplayItem, SubtitleWindow  # noqa: E402

LONG = ("I've gone through the Q3 numbers; they're in line with expectations, "
        "and the revenue mix looks better than we modelled.")
# 短句样本必须短到"任何字体下都只占一行"：这个断言是用来抓
# "height 虚高"的 bug，不能因为 CI 字体更宽而误报（曾报 height=2）
SHORT = "Ok, will do."

win = SubtitleWindow(style={"layout": "dialog", "animate": False, "scale": 1.3},
                     dialog={"self_source": "internal", "other_source": "internal",
                             "bidirectional": True})
win.post(DisplayItem(kind="internal", label="会议", app="Zoom",
                     text="这份季报的数据我看过了，整体符合预期", translation=LONG,
                     item_id="a1", ts=time.time(), asr_ms=180, llm_ms=420,
                     role="self"))
win.post(DisplayItem(kind="internal", label="会议", app="Zoom", text="下周一之前把结论发我",
                     translation=SHORT, item_id="a2", ts=time.time() + 1,
                     asr_ms=180, llm_ms=420, role="self"))
for _ in range(30):
    win._poll()
    win.root.update()
    win.root.update_idletasks()
    time.sleep(0.05)

pane = win._panes["self"]
long_txt, short_txt = pane.label_refs["a1"], pane.label_refs["a2"]
pw = pane.frame.winfo_width()
print(f"栏宽={pw} | 长句控件 {long_txt.winfo_width()}x{long_txt.winfo_height()} "
      f"height={long_txt.cget('height')} | 短句 height={short_txt.cget('height')}")

# ① 长句要按栏宽换行（不是被裁在一行里）
assert int(long_txt.winfo_width()) <= pw, (long_txt.winfo_width(), pw)
lines = int(long_txt.cget("height"))
assert lines >= 2, f"长句应占多行（height={lines}）：换行后第二行曾被吃掉"
assert int(short_txt.cget("height")) == 1, short_txt.cget("height")

# ② 栏头元素都必须落在栏内（曾把电平条挤出窗口）
title, level, pause = pane.title_lab, pane.level, pane.pause_btn
for name, wdg in (("标题", title), ("电平条", level), ("暂停键", pause)):
    x, w = wdg.winfo_x(), wdg.winfo_width()
    print(f"  {name}: x={x} 宽={w} → 右边界 {x + w}（栏宽 {pw}）")
    assert x >= 0 and x + w <= pw, f"{name}溢出栏宽: {x}+{w} > {pw}"
assert int(level.winfo_height()) > 0 and int(pause.winfo_width()) > 0

# ③ 两栏等宽
win._set_layout("dialog")
win.root.update()
w1 = win._panes["self"].frame.winfo_width()
w2 = win._panes["other"].frame.winfo_width()
print(f"两栏宽: {w1} / {w2}")
assert abs(w1 - w2) <= 2, (w1, w2)

# ④ 窗口变窄要重新换行（行数必须增加，不能还按旧宽度排）
before = int(long_txt.cget("height"))
win.root.geometry("700x480")
for _ in range(20):
    win.root.update()
    win.root.update_idletasks()
    time.sleep(0.05)
after = int(long_txt.cget("height"))
narrow_w = int(long_txt.winfo_width())
print(f"缩窄后：控件宽 {narrow_w} 行数 {before} → {after}")
assert after > before, f"变窄后行数应增加（{before} → {after}）"
assert narrow_w < 521

# ⑤ 双栏同源时两栏都要在（曾被"同源只显示一栏"退化掉）
mapped = [r for r, p in win._panes.items() if p.frame.winfo_ismapped()]
assert mapped == ["self", "other"], mapped
win._close()
print("[PASS] 栏内渲染：长句换行完整 / 栏头不溢出 / 两栏等宽且都在")
