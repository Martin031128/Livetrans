#!/usr/bin/env python3
"""打包契约回归：PyInstaller 入口 / overlay 双入口 / app_command 冻结语义。

守的坑：结构重构（如 overlay.py 拆包）不能破坏打包链路——
run_livetrans.py 入口、`python -m livetrans.overlay` 源码入口、
frozen 模式下的 exe 参数分派（--overlay）、build_windows.py 的素材齐全。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def test_pyi_entry_exists_and_forwards_overlay():
    """PyInstaller 入口 run_livetrans.py 存在，且转发 --overlay 到 overlay.main。"""
    entry = ROOT / "run_livetrans.py"
    assert entry.is_file(), f"缺打包入口 {entry}"
    src = entry.read_text(encoding="utf-8")
    assert '"--overlay"' in src, "入口未处理 --overlay 参数"
    assert "from livetrans.overlay import main" in src, \
        "入口应绝对导入 livetrans.overlay（包化后的 re-export）"
    print("[PASS] run_livetrans.py 入口与 --overlay 转发就绪")


def test_overlay_module_entry_kept():
    """源码模式 `python -m livetrans.overlay` 依赖包内 __main__.py。"""
    assert (ROOT / "livetrans" / "overlay" / "__main__.py").is_file(), \
        "overlay 包缺 __main__.py —— python -m 入口会失效"
    # 冒烟：入口符号可导入（re-export 链路完好）
    from livetrans.overlay import main, run_overlay  # noqa: F401
    print("[PASS] overlay 包 -m 入口与 re-export 就绪")


def test_build_windows_assets():
    """build_windows.py 引用的素材齐全：入口脚本 / hooks / 隐藏导入表。"""
    b = ROOT / "packaging" / "build_windows.py"
    assert b.is_file(), "缺打包脚本"
    entry = ROOT / "run_livetrans.py"
    assert entry.is_file(), "build_windows.check_env 依赖的入口缺失"
    hooks = ROOT / "packaging" / "hooks"
    assert hooks.is_dir() and any(hooks.glob("*.py")), "自定义 hooks 目录为空"
    ns: dict = {}
    exec(compile(b.read_text(encoding="utf-8"), str(b), "exec"),
         {"__name__": "build_windows_cfg", "__file__": str(b)}, ns)
    assert ns["HIDDEN_IMPORTS"], "HIDDEN_IMPORTS 为空"
    assert ns["APP_NAME"] == "LiveTrans"
    # 打包脚本里不应出现对 overlay 单文件路径的引用（已拆包）
    assert "livetrans/overlay.py" not in b.read_text(encoding="utf-8"), \
        "build_windows.py 仍引用拆包前的 overlay.py 路径"
    print(f"[PASS] 打包素材齐全（hiddenimports {len(ns['HIDDEN_IMPORTS'])} 项）")


def test_app_command_frozen_semantics():
    """app_command：源码用 -m 入口，frozen 用 exe + --overlay 参数分派。"""
    from livetrans import paths

    cmd = paths.app_command("overlay", ["--config", "c.yaml"])
    assert cmd[1:3] == ["-m", "livetrans.overlay"], cmd
    assert cmd[-1] == "c.yaml" and "--overlay" not in cmd, \
        "源码模式走 -m，不能再带 --overlay（overlay 自己的 argparse 会拒收）"

    real_frozen = getattr(sys, "frozen", False)
    try:
        sys.frozen = True
        cmd = paths.app_command("overlay", ["--config", "c.yaml"])
        assert cmd[0] == sys.executable and cmd[1] == "--overlay", cmd
        cmd_main = paths.app_command("main")
        assert cmd_main == [sys.executable], cmd_main
    finally:
        if real_frozen:
            sys.frozen = True
        else:
            del sys.frozen
    print("[PASS] app_command：源码 -m / frozen exe--overlay 双语义正确")


if __name__ == "__main__":
    test_pyi_entry_exists_and_forwards_overlay()
    test_overlay_module_entry_kept()
    test_build_windows_assets()
    test_app_command_frozen_semantics()
    print("[PASS] 打包契约：入口/双模式/素材/冻结语义 全部就绪")
