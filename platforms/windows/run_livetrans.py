#!/usr/bin/env python3
"""LiveTrans Windows 版统一入口（供 PyInstaller 打包使用）。

**为什么需要这个文件**：
`livetrans/main.py` 内部用的是相对导入（`from .asr import ...`），
源码运行时通过 `python -m livetrans.main` 启动没问题；但 PyInstaller 把入口
脚本当作**顶层脚本**执行，此时 `main.py` 没有父包，相对导入直接失败：

    ImportError: attempted relative import with no known parent package

所以打包必须从这个文件进入：它用**绝对导入** `livetrans.main`，
让包结构正常建立。同时它也统一转发 `--overlay`（字幕外挂）。

用法：
    源码：python run_livetrans.py [--overlay] [--list-devices] ...
    打包：LiveTrans.exe [--overlay] [--list-devices] ...
"""
import sys
from pathlib import Path

# 打包后（one-dir）本文件与 livetrans/ 同级，已随包收集，无需改 sys.path；
# 源码运行时把当前目录加进去，保证 `import livetrans` 能找到。
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


def main() -> int:
    argv = sys.argv[1:]

    # 字幕外挂：转给 overlay 自己的入口
    if "--overlay" in argv:
        i = argv.index("--overlay")
        rest = argv[:i] + argv[i + 1:]
        from livetrans.overlay import main as overlay_main
        return overlay_main(rest)

    from livetrans.main import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
