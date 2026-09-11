"""Windows 子进程黑框抑制（薄工具层，无其它依赖）。

**问题背景**：GUI 从 pythonw（无控制台）运行时，凡是 spawn 一个**控制台子系统**
的外部程序（nvidia-smi / pip / ollama …），Windows 都会给它新建一个控制台窗口
——表现为黑色终端一闪而过。资源状态条每几秒轮询一次 nvidia-smi，黑框就反复闪。

**用法**：给 subprocess.run / Popen 追加 `**nowin()`：

    subprocess.run([exe, ...], **nowin())

非 Windows 平台返回空 dict，调用点无需分支。
"""
from __future__ import annotations

import subprocess
import sys


def nowin() -> dict:
    """返回抑制控制台窗口的 kwargs（非 Windows 为空）。"""
    if sys.platform == "win32":
        # CREATE_NO_WINDOW = 0x08000000；getattr 兜底防极端精简环境缺常量
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
