"""运行依赖自检：缺件时给出可直接复制的修复命令，而不是抛堆栈。

- 主程序（pip）：numpy / sounddevice / PyYAML / openai / funasr_onnx
- 字幕外挂：Windows = PySide6（pip）；Linux = python3-gi + GTK3 + cairo（系统包）
- 系统音频：Windows = soundcard（pip，WASAPI loopback）；Linux = parec（系统包）
- 声纹角色标注（可选）：sherpa-onnx（pip）+ models/speaker/*.onnx（28MB）

Windows 版与 Linux 版的差异就在 missing_overlay_deps() / missing_system_deps()：
外挂渲染层是 Qt 不是 GTK，系统声音走 WASAPI 不走 parec。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys

PIP_FIX = "pip install -r requirements.txt"
APT_FIX = ("sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-pango-1.0 "
           "python3-cairo pulseaudio-utils libportaudio2")


def overlay_fix_hint() -> str:
    """外挂缺件时的修复命令提示（按平台给对的命令，而不是堆栈）。"""
    if sys.platform == "win32":
        return PIP_FIX
    return APT_FIX

_PIP_NEEDS = {"numpy": "numpy", "sounddevice": "sounddevice",
              "yaml": "PyYAML", "openai": "openai",
              "funasr_onnx": "funasr_onnx", "webrtcvad": "webrtcvad-wheels"}


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def missing_pip_deps() -> list[str]:
    """主程序缺失的 pip 依赖（显示包名）。"""
    return [pkg for mod, pkg in _PIP_NEEDS.items() if not _has(mod)]


def missing_speaker_deps() -> list[str]:
    """声纹角色标注（可选功能）缺失项：只差 pip 包。

    模型（28MB）在首次启用时会自动下载，所以不算"缺失"，缺任一项都不影响
    识别/翻译，只是不显示说话人标签。
    """
    if not _has("sherpa_onnx"):
        return ["sherpa-onnx（pip install sherpa-onnx）"]
    return []


def missing_system_deps() -> list[str]:
    """系统级依赖：pip 包装好了，但底层动态库缺失时 import 才报错。

    find_spec 查不出来（包是存在的），必须真的 import 一次。
    """
    missing: list[str] = []
    try:
        import sounddevice  # noqa: F401
    except OSError:
        missing.append("PortAudio 运行库（sounddevice 底层）")
    except ImportError:
        pass                      # pip 包本身缺失由 missing_pip_deps 负责
    if sys.platform == "win32":
        try:
            import soundcard  # noqa: F401   # WASAPI loopback（MediaFoundation）
        except (ImportError, OSError):
            missing.append("soundcard（pip install soundcard）")
    return missing


def missing_overlay_deps() -> list[str]:
    """字幕外挂缺失的组件。

    Windows 版外挂是 PySide6/Qt6 实现（见 livetrans/overlay.py），检查项与
    Linux 版（GTK3 栈 + parec）完全不同——这里必须分平台，否则在 Windows 上
    会误报 "缺 python3-gi/pulseaudio-utils" 并给出 apt 命令（南辕北辙）。
    """
    missing: list[str] = []
    if sys.platform == "win32":
        if not _has("PySide6"):
            missing.append("PySide6（pip install PySide6）")
        else:
            try:
                from PySide6 import QtCore  # noqa: F401  # DLL 是否真的可加载
            except (ImportError, OSError):
                missing.append("PySide6（DLL 加载失败，重装："
                               "pip install --force-reinstall PySide6==6.8.3）")
        # 系统音频（WASAPI loopback）与 PortAudio 也影响外挂可用性
        missing.extend(missing_system_deps())
        return missing

    # ---- Linux 原逻辑（保持不变）----
    if not _has("gi"):
        missing.append("python3-gi")
    else:
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("PangoCairo", "1.0")
            from gi.repository import Gtk  # noqa: F401
        except (ImportError, ValueError):
            missing.append("gir1.2-gtk-3.0 / gir1.2-pango-1.0")
    if not _has("cairo"):
        missing.append("python3-cairo")
    if shutil.which("parec") is None:
        missing.append("pulseaudio-utils(parec)")
    return missing
