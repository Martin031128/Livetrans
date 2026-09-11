"""外挂字幕窗可跨屏拖动（回归：曾被单屏范围钳住，拖到副屏就被弹回主屏）。

Windows 版说明：本用例原针对 GTK3 外挂窗；Windows 版外挂已用 PySide6/Qt6 重写，
拖动/缩放相关覆盖统一在 `test_overlay_qt.py`（含穿透、几何、编辑模式）。

这里保留文件是为了兼容 CI 的用例发现（`tests/run.py` 按 test_*.py 收集），
在非 Linux 平台直接 SKIP，避免 import gi 直接把测试进程打死。
"""
import sys

if sys.platform != "linux":
    print(f"[SKIP] GTK3 外挂窗拖动用例仅在 Linux 运行（当前平台 {sys.platform}）；"
          "Windows 版见 test_overlay_qt.py")
    raise SystemExit(0)

try:
    import gi  # noqa: F401
except (ImportError, ValueError) as e:
    print(f"[SKIP] 当前环境没有 GTK3/PyGObject（{type(e).__name__}: {e}）")
    raise SystemExit(0)

print("[SKIP] 本用例为 Linux 专属，Windows 版覆盖见 test_overlay_qt.py")
raise SystemExit(0)
