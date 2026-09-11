"""安装脚本回归：语法正确、dry-run 不动任何东西、开关生效、清理模式是"安全默认"。

用户要求：本地模型/Ollama 作为**默认同意**的选项提供，且安装后能删掉 ——
所以这里重点守两条：① dry-run 绝不产生副作用；② --remove-local 不带 --dry-run 时才算数。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "install.sh"
assert SCRIPT.is_file(), SCRIPT


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def models_state() -> tuple[bool, int]:
    """本地模型目录是否存在 + 文件数（用来证明 dry-run 没删东西）。"""
    d = ROOT / "models"
    if not d.is_dir():
        return False, 0
    return True, sum(1 for f in d.rglob("*") if f.is_file())


# ① 语法
r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
print("bash -n:", r.returncode, r.stderr.strip())
assert r.returncode == 0, r.stderr

# ② --help 说清用法
r = run("--help")
assert r.returncode == 0 and "install.sh" in r.stdout
assert "--no-models" in r.stdout and "--remove-local" in r.stdout
print("--help 覆盖关键开关 ✓")

# ③ 未知参数要报错（别静默忽略）
r = run("--whatever")
assert r.returncode == 2 and "未知参数" in r.stderr, r.stderr

# ④ dry-run：打印计划、退出 0、**不产生任何副作用**
before = models_state()
for args in (["--dry-run"], ["--no-models", "--dry-run"],
             ["--no-ollama", "--no-speaker", "--dry-run"]):
    r = run(*args)
    assert r.returncode == 0, (args, r.stderr[-300:])
    assert "[dry-run]" in r.stdout, args
    print(f"{' '.join(args):34} → 计划已打印，退出 0")
assert models_state() == before, "dry-run 修改了本地模型目录！"

# ⑤ --no-models 真的不装本地模型（计划里明确跳过）
r = run("--no-models", "--dry-run")
for kw in ("跳过识别模型", "跳过声纹", "跳过 Ollama"):
    assert kw in r.stdout, (kw, r.stdout[:400])
print("--no-models：识别/声纹/Ollama 全部跳过 ✓")

# ⑥ 清理模式：带 --dry-run 时绝不能删（脚本是破坏性的，这条最重要）
assert models_state()[0], "本地模型不在，无法验证清理安全"
r = run("--remove-local", "--dry-run")
assert r.returncode == 0, r.stderr
assert models_state() == before, "--remove-local --dry-run 竟然删除了模型！"
assert "未真正删除" in r.stdout, r.stdout[-300:]
print(f"--remove-local --dry-run：模型完好（{models_state()[1]} 个文件）✓")

# ⑦ 控制台里的"占用 + 可删除"提示指向同一条命令
page = (ROOT / "livetrans/ui/page_backend.py").read_text(encoding="utf-8")
assert "--remove-local" in page and "_refresh_local_disk" in page
print("控制台已显示占用并提供删除入口 ✓")

print("[PASS] 安装脚本：dry-run 零副作用 / 开关生效 / 清理需显式执行 / 控制台可发现")
