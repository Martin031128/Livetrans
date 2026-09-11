"""PyInstaller hook：webrtcvad（来自 webrtcvad-wheels）。

**为什么需要这个文件**：
`pyinstaller-hooks-contrib` 自带一个 `hook-webrtcvad.py`，它假设 webrtcvad 是
一个包含子模块的包；但 `webrtcvad-wheels` 实际装出来是：

    site-packages/webrtcvad.py                      单文件 Python 模块
    site-packages/_webrtcvad.cp312-win_amd64.pyd    编译扩展（真正干活的）

于是自带 hook 加载时抛 `ImportErrorWhenRunningHook: Failed to import module
__PyInstaller_hooks_0_webrtcvad`，整个打包直接失败。

我们的 hook 用 `--additional-hooks-dir` 优先加载（会覆盖自带的同名 hook），
只做真正必要的事：把 `_webrtcvad` 扩展收进去。
"""
from PyInstaller.utils.hooks import collect_dynamic_libs

# 真正需要的是编译扩展 `_webrtcvad`（webrtcvad.py 会 import 它）
hiddenimports = ["_webrtcvad"]
binaries = collect_dynamic_libs("_webrtcvad")
