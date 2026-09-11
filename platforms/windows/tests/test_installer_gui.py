"""图形化安装向导回归：默认全勾选、dry-run 不动系统、线程绝不碰 Tk、删除要二次确认。

守的坑：① 工作线程里调 self.after() 会抛 "main thread is not in main loop"
把安装线程直接打死（表现为进度页卡在第一步）；② 删除本地模型必须二次确认。

Windows 版说明：本用例守的是 Linux 版向导（含「添加到应用菜单」等 Linux 专属项），
Windows 版走 packaging/build_windows.py 与独立用例；非 Linux 跳过。
"""
import queue
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "linux":
    print(f"[SKIP] Linux 版图形化安装向导（当前平台 {sys.platform}）")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import installer_gui as ig  # noqa: E402

SCRIPT = ROOT / "packaging" / "installer_gui.py"
SRC = SCRIPT.read_text(encoding="utf-8")

# ① --plan：无 Tk 也能给出计划
r = subprocess.run([sys.executable, str(SCRIPT), "--plan"],
                   capture_output=True, text=True, timeout=120)
print(r.stdout.strip())
assert r.returncode == 0, r.stderr
for kw in ("本地识别模型", "声纹", "Ollama", "添加到应用菜单", "预计新下载"):
    assert kw in r.stdout, kw

# ② 默认"全部同意" + 尺寸信息齐备
steps = ig.build_steps()
assert [s.key for s in steps] == ["pip", "asr", "speaker", "ollama", "desktop"]
assert all(s.default for s in steps), "所有项目都应默认勾选（默认同意）"
sizes = {s.key: s.size_mb for s in steps}
print("默认勾选:", {s.key: s.default for s in steps}, "| 体积(MB):", sizes)
assert sizes["asr"] >= 200 and sizes["ollama"] >= 1000  # 让用户知道要下多少

# ③ 线程安全：安装线程里不许出现 self.after / 直接操作控件
worker = SRC[SRC.index("    def _worker("):SRC.index("    # ---- 第三步")]
for bad in ("self.after(", ".config(", ".insert("):
    assert bad not in worker, f"_worker 里出现了 {bad}（必须只投递消息）"
assert "self.post(" in worker and "queue.Queue" in SRC
print("安装线程只投递消息、不碰 Tk ✓")

# ④ 删除本地模型必须二次确认
rm = SRC[SRC.index("    def on_remove_local"):SRC.index("def main(argv=None)")]
assert "askyesno" in rm, "删除本地模型没有二次确认"
assert rm.index("askyesno") < rm.index("remove_local_models")
print("删除本地模型有二次确认 ✓")

# ⑤ 向导跑一遍 dry-run：不产生任何副作用，5 步全过
models_before = sorted(p.name for p in (ROOT / "models").iterdir()) \
    if (ROOT / "models").is_dir() else []
wiz = ig.Wizard(dry_run=True)
log_seen: list[str] = []
orig_log = wiz.log


def spy(line: str) -> None:
    log_seen.append(line)
    orig_log(line)


wiz.log = spy
wiz.on_start()
deadline = time.time() + 30
while wiz._running and time.time() < deadline:
    wiz.update()
    time.sleep(0.05)
for _ in range(10):
    wiz.update()
    time.sleep(0.05)
assert not wiz._running, "dry-run 没跑完（线程是否又被打死了？）"
text = "\n".join(log_seen)
print(text.strip()[-400:])
for kw in ("[1/5] Python 依赖", "[2/5] 本地识别模型", "[3/5] 声纹",
           "[4/5] 本地翻译模型", "[5/5] 添加到应用菜单"):
    assert kw in text, kw
assert ig.__dict__  # 保持引用
models_after = sorted(p.name for p in (ROOT / "models").iterdir()) \
    if (ROOT / "models").is_dir() else []
assert models_before == models_after, "dry-run 动了本地模型目录！"
try:
    wiz.destroy()
except Exception:  # noqa: BLE001
    pass

# ⑥ 主题一致性：向导用到的进度条/滚动条都已主题化
theme = (ROOT / "livetrans/ui/theme.py").read_text(encoding="utf-8")
assert "TProgressbar" in theme and "TScrollbar" in theme
print("进度条 / 滚动条已主题化 ✓")

print("[PASS] 安装向导：默认全勾选 / --plan 可用 / dry-run 零副作用 / 线程只投递 / 删除需确认")
