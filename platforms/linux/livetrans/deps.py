"""运行依赖自检：缺件时给出可直接复制的修复命令，而不是抛堆栈。

- 主程序（pip）：numpy / sounddevice / PyYAML / openai / funasr_onnx
- 字幕外挂（系统包）：python3-gi + gir1.2-gtk-3.0 + python3-cairo + pango
- 系统音频（命令行）：parec（pulseaudio-utils）
- 声纹角色标注（可选）：sherpa-onnx（pip）+ models/speaker/*.onnx（28MB）
"""
from __future__ import annotations

import importlib.util
import shutil

PIP_FIX = "pip install -r requirements.txt"
APT_FIX = ("sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-pango-1.0 "
           "python3-cairo pulseaudio-utils libportaudio2")

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

    目前只有一项：PortAudio（sounddevice 的底层库，属 libportaudio2）。
    find_spec 查不出来（包是存在的），必须真的 import 一次。
    """
    missing: list[str] = []
    try:
        import sounddevice  # noqa: F401
    except OSError:
        missing.append("libportaudio2（sounddevice 底层库）")
    except ImportError:
        pass                      # pip 包本身缺失由 missing_pip_deps 负责
    return missing


def missing_overlay_deps() -> list[str]:
    """字幕外挂缺失的系统组件（GTK3 栈 + 音频命令行工具）。"""
    missing: list[str] = []
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
