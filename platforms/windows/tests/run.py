#!/usr/bin/env python3
"""LiveTrans Windows 版测试入口：自检 + 全部专项测试。

用法：
    python tests/run.py            # 全跑
    python tests/run.py router     # 只跑名字匹配的用例
    python tests/run.py --list     # 只列出将要运行的用例

与 Linux 版 tests/run.sh 等价，但用 Python 实现——Windows 上 bash 不可靠。
判定规则与原脚本一致：
  - 退出码 0 且输出中无 Traceback          -> PASS
  - 输出含 [SKIP]                          -> SKIP（不算失败，退出码计 0）
  - 其余                                   -> FAIL
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK：用例输出里的中文/替换字符会直接抛 UnicodeEncodeError
# 把整个测试入口打死（比用例失败更糟——它掩盖了真实结果）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 单个用例的超时（秒）——与原 run.sh 的 timeout 240 对齐
CASE_TIMEOUT = 240


def _print_banner(title: str) -> None:
    print(f"\n== {title} ==")


def run_selfcheck() -> bool:
    """自检：selfcheck.py 必须通过。"""
    _print_banner("自检（selfcheck.py）")
    try:
        proc = subprocess.run(
            [sys.executable, "selfcheck.py"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"FAIL  selfcheck 超时（>{CASE_TIMEOUT}s）")
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and "Traceback" not in out:
        # 与 run.sh 一致：只回显最后一行摘要
        lines = [ln for ln in out.strip().splitlines() if ln.strip()]
        print(lines[-1] if lines else "PASS")
        return True
    print("FAIL")
    print("\n".join(out.strip().splitlines()[-20:]))
    return False


def discover_cases() -> list[Path]:
    return sorted(HERE.glob("test_*.py"))


def run_case(path: Path) -> str:
    """跑单个用例，返回 'PASS' / 'SKIP' / 'FAIL'。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CASE_TIMEOUT)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return "FAIL"

    # 与 run.sh 同款判定：退出码 0 且无 Traceback 才算通过
    if code == 0 and "Traceback" not in out:
        if "[SKIP]" in out:
            m = re.search(r"\[SKIP\].*", out)
            print(f"  SKIP  {m.group(0) if m else ''}")
            return "SKIP"
        n = out.count("[PASS]")
        print(f"  PASS  {n} 项断言")
        return "PASS"

    print(f"  FAIL (exit {code})")
    tail = out.strip().splitlines()[-8:]
    for ln in tail:
        print(f"    {ln}")
    # 在 GitHub Actions 里额外打一条注解：失败原因直接进 check-run 注解，
    # 注解是公开可读的（匿名 API 即可拉取），而日志本身要登录才能看
    if os.environ.get("GITHUB_ACTIONS"):
        msg = "%0A".join(
            ln.replace("%", "%25").replace("\r", "")
              .replace("::", "\\:\\:")
            for ln in out.strip().splitlines()[-15:])
        print(f"::error title=测试失败 {path.stem}::{msg}")
    return "FAIL"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    filter_kw = args[0] if args else ""

    cases = discover_cases()
    if filter_kw:
        cases = [c for c in cases if filter_kw in c.name]

    if "--list" in argv:
        print("将运行以下用例：")
        for c in cases:
            print(f"  {c.name}")
        return 0

    failed = 0
    if not run_selfcheck():
        failed += 1

    _print_banner("专项测试")
    if not cases:
        print("（没有匹配的用例）")
    for c in cases:
        print(f"{c.stem:<22}", end="", flush=True)
        if run_case(c) == "FAIL":
            failed += 1

    print()
    if failed == 0:
        print("全部测试通过 ✓")
    else:
        print(f"有测试失败 ✗（{failed} 项）")
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error title=测试套件失败::selfcheck 或专项测试未全部通过，"
                  "见上方各 FAIL 项的注解")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
