"""三种布局：对话=两条角色流（聊天窗式）；外部/内部=单一界面（只看这一路音频）。

回归点：
  · 四键一样 / 两栏不等宽；
  · 外部/内部曾按角色分成两栏（用户要的是"一个界面，写着系统声音"）；
  · 老配置里的 dual（双栏，已删）要能自动退回对话模式。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.subtitle import (DEFAULT_LAYOUT, DisplayItem,  # noqa: E402
                                SubtitleWindow, norm_layout)


def build(dialog: dict, layout: str = DEFAULT_LAYOUT) -> SubtitleWindow:
    styles: list[dict] = []
    w = SubtitleWindow(style={"layout": layout, "animate": False}, dialog=dialog,
                       on_style_change=styles.append)
    w.root.update()
    return w


def state(w: SubtitleWindow) -> dict:
    w.root.update_idletasks()
    w.root.update()
    time.sleep(0.05)
    w.root.update()
    out = {"panes": [], "widths": [], "align": {}, "titles": {}, "hint": False}
    for role, pane in w._panes.items():
        out["titles"][role] = pane.title_lab.cget("text")
        if pane.frame.winfo_ismapped():
            out["panes"].append(role)
            out["widths"].append(pane.frame.winfo_width())
            out["align"][role] = pane.align
    out["hint"] = bool(w._layout_hint.winfo_ismapped())
    return out


def feed(w: SubtitleWindow, kind: str, role: str, n: int = 2) -> None:
    """给某一路/某角色喂字幕（用于验证单界面模式只显示主角色那一条流）。"""
    for i in range(n):
        w.post(DisplayItem(kind=kind, label="会议", app="Zoom", text=f"第{i}句原文",
                           translation=f"line {i}", item_id=f"{role}-{kind}-{i}",
                           ts=time.time() + i, role=role))
    for _ in range(12):
        w._poll()
        w.root.update()
        time.sleep(0.03)


print("== 老配置 dual（双栏）应归一到对话 ==")
assert norm_layout("dual") == "dialog" and norm_layout("dialog") == "dialog"
assert norm_layout("") == "dialog" and norm_layout("internal") == "internal"
w0 = build({"self_source": "external", "other_source": "internal"}, layout="dual")
print("  配置里写 dual → 实际布局:", w0.style["layout"], "| 回调已落盘:", w0.style["layout"])
assert w0.style["layout"] == "dialog"
w0._close()

print("== 各用一路（你=麦克风 / 对方=系统声音）==")
w = build({"self_source": "external", "other_source": "internal"})
st = state(w)
print("  dialog   可见=", st["panes"], "宽=", st["widths"], "对齐=", st["align"])
print("           标题=", st["titles"])
assert st["panes"] == ["self", "other"], st
assert abs(st["widths"][0] - st["widths"][1]) <= 2, st["widths"]     # 两栏等宽
assert set(st["align"].values()) == {"left", "right"}, st["align"]   # 聊天窗式
assert st["titles"]["self"].startswith("你（自己）"), st["titles"]
for mode, want_zh, role in (("external", "麦克风", "self"), ("internal", "系统声音", "other")):
    w._set_layout(mode)
    st = state(w)
    print(f"  {mode:8} 可见={st['panes']} 宽={st['widths']} 标题={st['titles'][role]!r}")
    assert st["panes"] == [role], f"{mode} 应是单一界面（只有 {role} 一栏）: {st}"
    assert st["widths"][0] > 1000, "单界面应铺满"
    assert st["titles"][role].startswith(want_zh), st["titles"]
    assert "按「" not in st["titles"][role], "各用一路时不必标明按谁"
    assert not st["hint"]

print("== 两个角色同源（都是系统声音，用户真实配置）==")
w2 = build({"left_role": "self", "self_source": "internal",
            "other_source": "internal", "bidirectional": True})
w2._set_layout("dialog")
st = state(w2)
print("  dialog   可见=", st["panes"], "宽=", st["widths"])
assert st["panes"] == ["self", "other"], "对话模式始终两栏（角色视角）"
assert st["titles"]["self"].startswith("你（自己）"), st["titles"]
assert st["titles"]["other"].startswith("对方"), st["titles"]
w2._set_layout("internal")
st = state(w2)
print("  internal 可见=", st["panes"], "标题=", st["titles"]["self"])
assert st["panes"] == ["self"], f"内部应是单一界面（一个系统声音）: {st}"
assert st["titles"]["self"].startswith("系统声音"), st["titles"]
assert "按「你（自己）」" in st["titles"]["self"], "同源两人时标明按谁的方向"
w2._set_layout("external")
st = state(w2)
print("  external 可见=", st["panes"], "提示=",
      w2._layout_hint.cget("text").split("\n")[0])
assert st["panes"] == [] and st["hint"], "没人用麦克风时给提示"

# 单界面模式只显示主角色那一条流（同源另一角色的流不重复上屏）
feed(w2, "internal", "self")
feed(w2, "internal", "other")
shown = {r: len(p.label_refs) for r, p in w2._panes.items()}
print("  单界面下的上屏条数（self 为主角色）:", shown)
assert shown["self"] > 0 and shown["other"] == 0, "同一路只应显示主角色那一条流"
w2._close()

# 布局切换要落盘
w3 = build({"self_source": "external", "other_source": "internal"})
w3._set_layout("internal")
assert w3.style["layout"] == "internal"
w3._close()
print("[PASS] 三布局：对话=两栏聊天式（等宽）/ 外部·内部=单一来源界面 / dual 自动归一")
