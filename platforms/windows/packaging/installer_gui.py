#!/usr/bin/env python3
"""LiveTrans 图形化安装向导（Tkinter）。

把「安装/补装/清理」本地模型这件事做成可视化：勾选要装的东西 → 看进度 → 完事。
默认勾选 = 都装（识别模型 + 声纹 + Ollama 本地翻译模型）；不想装就取消勾选，
只用云端 API 的话全部取消也能继续（等于只装 Python 依赖）。

用法：
    python3 packaging/installer_gui.py                 # 打开向导
    python3 packaging/installer_gui.py --plan          # 只打印安装计划（不动任何东西）
    python3 packaging/installer_gui.py --dry-run       # 打开向导并走一遍"假装安装"
    python3 packaging/installer_gui.py --only asr,desktop
"""
from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

def _bootstrap_path() -> None:
    """让向导在三种位置都能跑：源码（packaging/ 的上一级）、/opt/livetrans、
    以及被拷到 /usr/share/doc/livetrans/ 的副本（找系统安装位置）。"""
    here = Path(__file__).resolve()
    for cand in (here.parent.parent,
                 Path(os.environ.get("LIVETRANS_HOME", "/opt/livetrans"))):
        if (cand / "livetrans" / "paths.py").is_file():
            sys.path.insert(0, str(cand))
            return
    sys.exit("找不到 livetrans 代码目录：请用 /usr/bin/livetrans-installer，"
             "或在源码目录里运行 packaging/installer_gui.py")


_bootstrap_path()

from livetrans.paths import (BASE, DATA_DIR, MODELS_DIR,  # noqa: E402
                             SYSTEM_MODELS_DIR)
from livetrans.winsub import nowin  # noqa: E402
from livetrans.ui.theme import (BAD, BG, BORDER, DIM, INK, PANEL,  # noqa: E402
                                PANEL_2, SIGNAL, WARN, apply_theme,
                                pick_fonts)
from livetrans.ui.widgets import ToolTip, button  # noqa: E402

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Name=LiveTrans
GenericName=实时语音翻译
Comment=捕获系统与麦克风音频，实时双语字幕、翻译与会话总结
Exec={script}
Icon={icon}
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=livetrans;translate;subtitle;asr;翻译;字幕;语音;
StartupWMClass=livetrans
"""


# ---------------------------------------------------------------- 安装计划
@dataclass
class Step:
    key: str
    title: str
    detail: str
    size_mb: float = 0.0            # 预计下载体积（0 = 不用下载）
    ready: bool = False             # 已经装好（安装时会跳过）
    default: bool = True            # 向导里默认勾选
    run: object = None              # callable(log, dry) -> bool


def _missing_pip() -> list[str]:
    from livetrans.deps import missing_pip_deps
    return missing_pip_deps()


def _asr_ready() -> bool:
    from livetrans.asr import sensevoice_ready
    return bool(sensevoice_ready())


def _speaker_ready() -> bool:
    from livetrans.speaker import speaker_ready
    return bool(speaker_ready())


def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _local_model_name() -> str:
    try:
        from livetrans.config import load_config
        from livetrans.translate import is_local_base_url
        cfg = load_config(str(BASE / "config.yaml")
                          if (BASE / "config.yaml").is_file() else None)
        for p in cfg.providers.values():
            if is_local_base_url(getattr(p, "base_url", "")):
                return p.model or "qwen3:4b-instruct"
    except Exception:  # noqa: BLE001 - 拿不到就用默认名
        pass
    return "qwen3:4b-instruct"


def _ollama_model_ready(name: str) -> bool:
    if not _ollama_installed():
        return False
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True,
                             timeout=10, **nowin()).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    base = name.split(":")[0]
    return any(line.split()[0].split(":")[0] == base
               for line in out.splitlines()[1:] if line.split())


def _desktop_installed() -> bool:
    return (Path.home() / ".local/share/applications/livetrans.desktop").is_file()


# ---- 每个步骤的实际动作（Tk-free，可被 --dry-run / 测试直接调用）----

def step_pip(log, dry: bool) -> bool:
    if dry:
        log("[dry-run] pip install --user -r requirements.txt")
        return True
    missing = _missing_pip()
    if not missing:
        log("Python 依赖已齐备")
        return True
    log(f"安装 Python 依赖：{'、'.join(missing)}")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user",
                        "-r", str(BASE / "requirements.txt")], **nowin())
    return r.returncode == 0


def step_asr(log, dry: bool) -> bool:
    if dry:
        log(f"[dry-run] 下载识别模型 SenseVoice → {MODELS_DIR / 'sensevoice'}")
        return True
    from livetrans.asr import preload_model
    try:
        p = preload_model(progress_cb=lambda pct, done, total, rate:
                          log(f"  识别模型下载 {pct}% ({done}/{total}, {rate})")
                          if pct % 10 == 0 else None)
        log(f"识别模型就绪：{p}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"识别模型安装失败：{type(e).__name__}: {e}")
        return False


def step_speaker(log, dry: bool) -> bool:
    if dry:
        log("[dry-run] pip install --user sherpa-onnx")
        log(f"[dry-run] 下载声纹模型 → {MODELS_DIR / 'speaker'}")
        return True
    try:
        import importlib.util
        if importlib.util.find_spec("sherpa_onnx") is None:
            log("安装声纹依赖 sherpa-onnx ...")
            subprocess.run([sys.executable, "-m", "pip", "install", "--user",
                            "sherpa-onnx"], check=False, **nowin())
        from livetrans.speaker import ensure_speaker_model, speaker_ready
        p = ensure_speaker_model(log=log)
        if p is None and not speaker_ready():
            log("声纹模型未就绪（不影响识别与翻译）")
            return False
        log(f"声纹就绪：{p or '已存在'}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"声纹安装失败（可选功能）：{type(e).__name__}: {e}")
        return False


def step_ollama(log, dry: bool) -> bool:
    name = _local_model_name()
    if dry:
        log(f"[dry-run] 启动 ollama serve 并 ollama pull {name}")
        return True
    if not _ollama_installed():
        log("未安装 Ollama：请到 https://ollama.com/download/windows 下载安装")
        log("（不想装也没关系：控制台里选云端服务商 + 填 Key 即可）")
        return False
    try:
        from livetrans.config import load_config
        from livetrans.translate import ensure_local_backend, is_local_base_url
        cfg = load_config(str(BASE / "config.yaml")
                          if (BASE / "config.yaml").is_file() else None)
        p = next((p for p in cfg.providers.values()
                  if is_local_base_url(getattr(p, "base_url", ""))), None)
        if p is not None:
            ensure_local_backend(p.base_url, p.model, log)
    except Exception as e:  # noqa: BLE001 - 服务没起来也让 pull 试一次
        log(f"启动本地服务时提示：{e}")
    log(f"拉取本地翻译模型 {name}（首次约 2.5GB）...")
    r = subprocess.run(["ollama", "pull", name], **nowin())
    if r.returncode == 0:
        log(f"本地翻译模型就绪：{name}")
        return True
    log("模型拉取失败（可稍后重试；也可只用云端 API）")
    return False


def step_desktop(log, dry: bool) -> bool:
    target = Path.home() / ".local/share/applications/livetrans.desktop"
    if dry:
        log(f"[dry-run] 写入应用菜单项 {target}")
        return True
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DESKTOP_TEMPLATE.format(
            script=str(BASE / "livetrans-gui.sh"),
            icon=str(BASE / "assets" / "livetrans.svg")), encoding="utf-8")
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(target.parent)],
                           capture_output=True, timeout=10)
        log(f"已加入应用菜单：{target}")
        return True
    except OSError as e:
        log(f"写入菜单项失败：{e}")
        return False


def build_steps() -> list[Step]:
    """安装计划（顺序即执行顺序）。"""
    name = _local_model_name()
    return [
        Step("pip", "Python 依赖（必需）",
             "sounddevice / funasr_onnx / openai / PyYAML 等",
             ready=not _missing_pip(), run=step_pip),
        Step("asr", "本地识别模型 SenseVoice", "离线识别（中英日韩粤），约 240MB",
             size_mb=240, ready=_asr_ready(), run=step_asr),
        Step("speaker", "声纹角色标注（可选）",
             "多人会议按说话人标 S1/S2，pip 包 + 约 28MB 模型",
             size_mb=28, ready=_speaker_ready(), run=step_speaker),
        Step("ollama", f"本地翻译模型 Ollama（{name}）",
             "完全离线翻译，无需 API Key；未装 Ollama 会给出安装指引",
             size_mb=2500, ready=_ollama_model_ready(name), run=step_ollama),
        Step("desktop", "添加到应用菜单", "装完可用 Super 搜索 LiveTrans 启动",
             ready=_desktop_installed(), run=step_desktop),
    ]


def remove_local_models(log) -> bool:
    """删除本地模型（识别 + 声纹 + Ollama 里的本地翻译模型）。"""
    ok = True
    for d in (MODELS_DIR, SYSTEM_MODELS_DIR):
        if d.is_dir():
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d)
                log(f"已删除 {d}（{size / 1e6:.0f} MB）")
            except OSError as e:
                ok = False
                log(f"删除 {d} 失败：{e}")
    name = _local_model_name()
    if _ollama_installed() and _ollama_model_ready(name):
        r = subprocess.run(["ollama", "rm", name], capture_output=True,
                           text=True, **nowin())
        log(f"已移除 Ollama 模型 {name}" if r.returncode == 0
            else f"移除 {name} 失败：{r.stderr.strip()[:120]}")
    log("下次要用会自动重新下载；云端 API 不受影响。")
    return ok


def print_plan() -> int:
    """--plan：只打印计划（无 Tk，CI/脚本可用）。"""
    print("LiveTrans 安装计划")
    print(f"  项目目录：{BASE}")
    print(f"  模型写入：{MODELS_DIR}")
    print(f"  系统模型：{SYSTEM_MODELS_DIR}"
          f"{'（存在，可直接用）' if SYSTEM_MODELS_DIR.is_dir() else '（不存在）'}")
    print(f"  数据目录：{DATA_DIR}")
    total = 0.0
    for st in build_steps():
        state = "已就绪（跳过）" if st.ready else (
            f"需要下载约 {st.size_mb:.0f} MB" if st.size_mb else "待执行")
        print(f"  [{'x' if st.default else ' '}] {st.title}：{state}")
        if not st.ready:
            total += st.size_mb
    print(f"  预计新下载：{total:.0f} MB")
    return 0


# ---------------------------------------------------------------- 图形界面
class Wizard(tk.Tk):
    """三步式向导：选项 → 进度 → 完成。"""

    def __init__(self, dry_run: bool = False, only: list[str] | None = None):
        super().__init__(className="livetrans-installer")
        self.dry_run = dry_run
        self.title("LiveTrans 安装向导")
        self.configure(bg=BG)
        self.geometry("760x600")
        self.minsize(700, 540)
        ui, mono = pick_fonts()
        self.UI, self.MONO = ui, mono
        apply_theme(self, ui, mono)
        self.steps = build_steps()
        self.vars: dict[str, tk.BooleanVar] = {}
        self._running = False
        self._cancel = False
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._only = only
        self._build_header()
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(8, 0))
        # 底部按钮条常驻：每一页只换里面的按钮（否则上/下页按钮会叠在一起）
        self.bottom = tk.Frame(self, bg=BG)
        self.bottom.pack(fill="x", padx=18, pady=14)
        self._build_options()

    # ---- 顶部 ----
    def _build_header(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 0))
        tk.Label(head, text="LiveTrans 安装向导", bg=BG, fg=INK,
                 font=(self.UI, 15, "bold")).pack(anchor="w")
        tk.Label(head, text="按需准备本地模型（离线识别 / 离线翻译）；不想要随时可删",
                 bg=BG, fg=DIM, font=(self.UI, 9)).pack(anchor="w")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(10, 0))

    def _clear_bottom(self) -> None:
        """清空常驻底部栏（换页时重建按钮，避免上下页按钮重叠）。"""
        for w in self.bottom.winfo_children():
            w.destroy()

    # ---- 第一步：选项 ----
    def _build_options(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        card = ttk.LabelFrame(self.body, text="要安装的内容（默认全部勾选）",
                              style="Card.TLabelframe", padding=(14, 10))
        card.pack(fill="x")
        for i, st in enumerate(self.steps):
            # 默认勾选（"默认同意"）：已就绪的也勾着，旁边标明"会跳过"，
            # 这样用户一眼看到默认意图；不想装哪个就取消哪个
            var = tk.BooleanVar(value=st.default)
            if self._only is not None:
                var.set(st.key in self._only)
            self.vars[st.key] = var
            row = ttk.Frame(card)
            row.grid(row=i, column=0, sticky="ew", pady=4)
            cb = ttk.Checkbutton(row, text=st.title, variable=var,
                                 style="Card.TCheckbutton")
            cb.pack(side="left")
            state = ("✓ 已就绪（会跳过）" if st.ready
                     else (f"↓ 约 {st.size_mb:.0f} MB" if st.size_mb else ""))
            tk.Label(row, text=state, bg=PANEL,
                     fg=SIGNAL if st.ready else WARN,
                     font=(self.UI, 9)).pack(side="left", padx=(10, 0))
            tk.Label(card, text=st.detail, bg=PANEL, fg=DIM,
                     font=(self.UI, 9)).grid(row=i, column=1, sticky="w",
                                             padx=(16, 0))
            ToolTip(cb, f"{st.title}\n{st.detail}")
        card.grid_columnconfigure(1, weight=1)

        self.plan_lab = tk.Label(self.body, text="", bg=BG, fg=INK,
                                 font=(self.UI, 10, "bold"), justify="left")
        self.plan_lab.pack(anchor="w", pady=(12, 0))
        tk.Label(self.body, bg=BG, fg=DIM, font=(self.UI, 9), justify="left",
                 text=("· 只用云端 API（DeepSeek/GLM/千问…）的话，把「本地识别模型」"
                       "「声纹」「Ollama」三项取消即可\n"
                       "· 装完随时可删：向导最后一页有「删除本地模型」")
                 ).pack(anchor="w", pady=(4, 0))

        self._clear_bottom()
        button(self.bottom, "开始安装", self.on_start,
               kind="primary").pack(side="left")
        button(self.bottom, "退出", self.destroy, kind="ghost").pack(
            side="left", padx=8)
        button(self.bottom, "只看计划", self.on_show_plan,
               kind="ghost").pack(side="right")
        self._refresh_plan()

    def _refresh_plan(self) -> None:
        picked = [st for st in self.steps if self.vars[st.key].get()]
        total = sum(st.size_mb for st in picked if not st.ready)
        need = [st.title for st in picked if not st.ready]
        if not picked:
            self.plan_lab.config(text="未勾选任何项目（不会改动系统）", fg=DIM)
        elif not need:
            self.plan_lab.config(text="所选项都已就绪，无需下载", fg=SIGNAL)
        else:
            self.plan_lab.config(text=f"将执行 {len(need)} 项，预计下载约 "
                                      f"{total:.0f} MB", fg=INK)

    def on_show_plan(self) -> None:
        lines = [f"· {st.title}：" + ("已就绪（跳过）" if st.ready else
                                     (f"下载约 {st.size_mb:.0f} MB" if st.size_mb
                                      else "待执行"))
                 for st in self.steps if self.vars[st.key].get()]
        messagebox.showinfo("安装计划", "\n".join(lines) or "未勾选任何项目")

    # ---- 第二步：进度 ----
    def on_start(self) -> None:
        picked = [st for st in self.steps if self.vars[st.key].get()]
        if not picked:
            messagebox.showinfo("无需操作", "没有勾选任何项目。")
            return
        for w in self.body.winfo_children():
            w.destroy()
        box = tk.Frame(self.body, bg=BG)
        box.pack(fill="both", expand=True)
        self.status = tk.Label(box, text="准备中…", bg=BG, fg=SIGNAL,
                               font=(self.UI, 10, "bold"))
        self.status.pack(anchor="w")
        self.bar = ttk.Progressbar(box, mode="determinate",
                                   maximum=max(1, len(picked)))
        self.bar.pack(fill="x", pady=(6, 8))
        wrap = tk.Frame(box, bg=PANEL)
        wrap.pack(fill="both", expand=True)
        self.log_txt = tk.Text(wrap, bg=PANEL, fg=INK, font=(self.MONO, 9),
                               wrap="word", bd=0, highlightthickness=0,
                               padx=10, pady=8)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_txt.pack(side="left", fill="both", expand=True)
        self.log_txt.configure(state="disabled")
        self._clear_bottom()
        button(self.bottom, "停止（跑完当前这步就停）", self.on_cancel,
               kind="ghost").pack(side="right")
        self._running = True
        threading.Thread(target=self._worker, args=(picked,), daemon=True,
                         name="installer").start()
        self.after(120, self._pump)

    def on_cancel(self) -> None:
        self._cancel = True
        self.status.config(text="将在当前步骤结束后停止…", fg=WARN)

    def log(self, line: str) -> None:
        """任意线程可调：只把日志投进队列（Tk 控件只在主线程碰）。

        注意：工作线程里**不能**调 self.after()（Tkinter 会抛
        "main thread is not in main loop" 直接把线程打死）。
        """
        self._q.put(("log", {"text": line}))

    def post(self, kind: str, **kw) -> None:
        """线程安全地请求 UI 动作（主线程 _pump 里执行）。"""
        self._q.put((kind, kw))

    def _pump(self) -> None:
        """主线程消费队列：日志/状态/进度/收尾。"""
        finished = None
        while True:
            try:
                kind, kw = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_txt.configure(state="normal")
                self.log_txt.insert("end", kw["text"].rstrip() + "\n")
                self.log_txt.see("end")
                self.log_txt.configure(state="disabled")
            elif kind == "status":
                self.status.config(text=kw["text"], fg=SIGNAL)
            elif kind == "bar":
                self.bar.configure(value=kw["value"])
            elif kind == "finish":
                finished = kw["results"]
        if finished is not None:
            self._running = False
            self._finish(finished)          # 重建页面 → 停止轮询
            return
        if self._running:
            self.after(120, self._pump)

    def _worker(self, picked: list[Step]) -> None:
        """安装线程：只干活 + 投消息，绝不直接碰 Tk。"""
        results: list[tuple[str, bool]] = []
        for i, st in enumerate(picked, 1):
            self.log("")
            self.log(f"== [{i}/{len(picked)}] {st.title} ==")
            self.post("status", text=f"[{i}/{len(picked)}] {st.title}")
            ok = bool(st.run(self.log, self.dry_run))
            results.append((st.title, ok))
            self.post("bar", value=i)
            if self._cancel:
                self.log("已按要求停止（后续步骤未执行）")
                break
        self.post("finish", results=results)

    # ---- 第三步：完成 ----
    def _finish(self, results: list[tuple[str, bool]]) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ok_n = sum(1 for _, ok in results if ok)
        head = tk.Label(self.body, bg=BG, fg=SIGNAL if ok_n == len(results) else WARN,
                        font=(self.UI, 12, "bold"), justify="left",
                        text=f"完成：{ok_n}/{len(results)} 项成功"
                             + ("（dry-run：什么都没改）" if self.dry_run else ""))
        head.pack(anchor="w", pady=(4, 8))
        for title, ok in results:
            tk.Label(self.body, text=("✓ " if ok else "! ") + title, bg=BG,
                     fg=INK if ok else WARN, font=(self.UI, 10),
                     justify="left").pack(anchor="w")
        tk.Label(self.body, bg=BG, fg=DIM, font=(self.UI, 9), justify="left",
                 text="\n下一步：启动控制台 →「翻译后端」选服务商（或本地模型）→"
                      "「音频源」选来源 → 「保存并启动」").pack(anchor="w",
                                                              pady=(10, 0))
        self._clear_bottom()
        button(self.bottom, "启动控制台", self.on_launch_console,
               kind="primary").pack(side="left")
        button(self.bottom, "删除本地模型…", self.on_remove_local,
               kind="secondary").pack(side="left", padx=8)
        button(self.bottom, "关闭", self.destroy, kind="ghost").pack(
            side="right")

    def on_launch_console(self) -> None:
        # start_new_session 是 POSIX 专有；Windows 用 CREATE_NO_WINDOW 等效
        kwargs: dict = {"cwd": str(BASE)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen([sys.executable, str(BASE / "launcher.py")],
                             **kwargs)
            self.destroy()
        except OSError as e:
            messagebox.showerror("启动失败", str(e))

    def on_remove_local(self) -> None:
        """删除本地模型（二次确认；删完给出说明）。"""
        size = 0
        for d in (MODELS_DIR, SYSTEM_MODELS_DIR):
            if d.is_dir():
                size += sum(f.stat().st_size for f in d.rglob("*")
                            if f.is_file())
        if size == 0 and not _ollama_model_ready(_local_model_name()):
            messagebox.showinfo("没有可删的", "没有找到已下载的本地模型。")
            return
        if not messagebox.askyesno(
                "确认删除本地模型",
                f"将删除约 {size / 1e6:.0f} MB 的本地模型"
                f"（识别/声纹），并移除 Ollama 里的本地翻译模型。\n\n"
                f"删除后：云端 API 完全不受影响；下次要用会自动重新下载。\n\n"
                f"确定删除？", icon="warning", default="no"):
            return
        out: list[str] = []
        remove_local_models(out.append)
        messagebox.showinfo("已删除", "\n".join(out[-6:]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="livetrans-installer",
                                description="LiveTrans 图形化安装向导")
    ap.add_argument("--plan", action="store_true", help="只打印安装计划后退出")
    ap.add_argument("--dry-run", action="store_true", help="走一遍流程但不改动系统")
    ap.add_argument("--only", help="只处理这些步骤（逗号分隔：pip,asr,speaker,ollama,desktop）")
    args = ap.parse_args(argv)

    if args.plan:
        return print_plan()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    wiz = Wizard(dry_run=args.dry_run, only=only)
    if args.dry_run:
        wiz.after(300, wiz.on_start)          # dry-run：自动走一遍
    wiz.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:          # 输出被 head/管道截断：安静退出
        try:
            sys.stdout.close()
        except OSError:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
