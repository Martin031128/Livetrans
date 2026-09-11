"""外挂字幕窗可跨屏拖动（回归：曾被单屏范围钳住，拖到副屏就被弹回主屏）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

from livetrans.config import OverlayConfig      # noqa: E402
from livetrans.overlay import OverlayWindow     # noqa: E402

ov = OverlayWindow(OverlayConfig(), config_path=None)
mon = ov.mon
print(f"当前主屏: {mon.width}x{mon.height} @({mon.x},{mon.y})")

# 1) 真实的桌面并集：单屏时应等于这一块屏
d = ov._desktop_bounds()
print(f"桌面并集: {d.width}x{d.height} @({d.x},{d.y})，显示器数={Gdk.Display.get_default().get_n_monitors()}")
assert d.width >= mon.width and d.height >= mon.height

# 2) 模拟双屏（主屏右侧再来一块同尺寸屏）：手动位置落在副屏上必须被保留
real_bounds = ov._desktop_bounds


def two_screens():
    r = Gdk.Rectangle()
    r.x, r.y = min(0, mon.x), min(0, mon.y)
    r.width = mon.width * 2
    r.height = mon.height
    return r


ov._desktop_bounds = two_screens
ov.width, ov.height = 1200, 120
two = two_screens()
# 注意：断言一律基于"模拟出来的桌面范围 two"，不基于鼠标所在的那块屏——
# 鼠标在哪块屏会让 ov.mon 变来变去，那种断言会随环境飘（曾经在双屏机上误报）
inside = (two.x + two.width - ov.width - 50, two.y + 300)      # 桌面内（靠右）
ov.manual_pos = inside
ov._place()
# 用 _place() 里算出的目标位置断言（窗口未跑主循环时 move() 是异步的，
# get_position() 可能还是旧值）
x, y = ov._last_geom[0], ov._last_geom[1]
print(f"拖到桌面右侧 -> 目标位置 ({x},{y})，期望 {inside}")
assert (x, y) == inside, "落在桌面内的手动位置必须原样保留"
assert x > two.x + two.width // 2, "应停在桌面的右半边（跨过原屏边界）"

# 3) 拖到桌面之外仍要钳回（不能拖丢）
ov.manual_pos = (two.x + two.width + 3000, two.y - 5000)
ov._place()
x, y = ov._last_geom[0], ov._last_geom[1]
print(f"拖出桌面 -> 钳回 ({x},{y})")
assert two.x <= x <= max(two.x, two.x + two.width - ov.width)
assert two.y <= y <= max(two.y, two.y + two.height - ov.height)

# 4) 回到真实桌面范围：越界仍钳在"所有屏的并集"内
ov._desktop_bounds = real_bounds
d = real_bounds()
ov.manual_pos = (d.x + d.width + 9999, d.y - 9000)
ov._place()
x, y = ov._last_geom[0], ov._last_geom[1]
print(f"越界钳制 -> ({x},{y})，桌面 {d.width}x{d.height} @({d.x},{d.y})")
assert d.x <= x <= max(d.x, d.x + d.width - ov.width)
assert d.y <= y <= max(d.y, d.y + d.height - ov.height)

ov.destroy()
print("[PASS] 外挂拖动：跨屏位置保留（不再被单屏范围弹回）+ 越界钳回 + 单屏行为不变")
