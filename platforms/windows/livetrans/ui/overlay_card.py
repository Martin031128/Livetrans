"""「字幕外挂」卡片的行为：启动/停止外挂、清理残留实例。"""

from __future__ import annotations

import os
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sounddevice as sd
import yaml

from livetrans.asr import preload_model, sensevoice_ready
from livetrans.capture import list_monitor_sources
from livetrans.config import load_config
from livetrans.deps import missing_overlay_deps, overlay_fix_hint
from livetrans.keys import (KEYS_PATH, add_history, detect_provider,
                            key_env_overlay, load_any, load_history,
                            mask_key, merged_env, save_keys)
from livetrans.langs import (CODE_TO_LANG, LANG_CODES, LANGUAGES,
                             MAX_WORDS_DEFAULT, OV_SRC_LABELS,
                             OV_SRC_VALUES, SOURCE_LANGS, TARGET_LANGS)
from livetrans.paths import (BASE, CONFIG_PATH,
                             EXAMPLE_PATH, LOG_PATH, SESSIONS_DIR,
                             app_command)
from livetrans.providers import example_providers, fetch_model_list, load_yaml
from livetrans.sysmon import collect_local_stats
from livetrans.translate import (LLMTranslator, ensure_local_backend,
                                 is_local_base_url)
from livetrans.ui.theme import (BAD, BG, BORDER, DIM, EXTERNAL, FIELD, INK,
                                INTERNAL, ON_SIGNAL, PANEL, PANEL_2, SIGNAL,
                                WARN, pick_font)
from livetrans.ui.widgets import StatusToast, ToolTip


class OverlayCardMixin:

    def _build_overlay_card(self, page) -> None:
        """「字幕外挂」卡片：外观设置 + 启动/停止按钮。"""
        # 卡片 C：字幕外挂（悬浮字幕窗）
        ovf = self._card(page, "字幕外挂（悬浮字幕窗）")
        ovf.pack(fill="x", padx=6, pady=6)

        self.ov_top_var = tk.BooleanVar(value=True)
        self.ov_ct_var = tk.BooleanVar(value=False)
        self.ov_src_var = tk.BooleanVar(value=True)
        self.ov_top_cb = ttk.Checkbutton(ovf, text="置顶", variable=self.ov_top_var,
                                         style="Card.TCheckbutton")
        self.ov_top_cb.grid(row=0, column=0, sticky="w")
        self.ov_ct_cb = ttk.Checkbutton(ovf, text="鼠标穿透",
                                        variable=self.ov_ct_var,
                                        style="Card.TCheckbutton")
        self.ov_ct_cb.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.ov_src_cb = ttk.Checkbutton(ovf, text="显示原文",
                                         variable=self.ov_src_var,
                                         style="Card.TCheckbutton")
        self.ov_src_cb.grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.tip(self.ov_top_cb, "字幕窗始终浮在其它窗口之上")
        self.tip(self.ov_ct_cb,
                 "开启后字幕完全不拦截鼠标（点击会穿透到背后的播放器/网页）\n"
                 "关闭时可直接用鼠标拖动字幕窗口")
        self.tip(self.ov_src_cb, "译文上方同时显示一行较小的原文")

        self._label(ovf, "字号").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.ov_font_var = tk.StringVar(value="34")
        self.ov_font_sp = ttk.Spinbox(ovf, from_=12, to=96, width=4,
                                      textvariable=self.ov_font_var)
        self.ov_font_sp.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.tip(self.ov_font_sp, "译文文字大小（px）；原文小字按 55% 自动缩放")
        self._label(ovf, "背景透明度").grid(row=1, column=2, sticky="w",
                                            padx=(12, 0), pady=(8, 0))
        self.ov_op_var = tk.DoubleVar(value=0.9)
        op_wrap = tk.Frame(ovf, bg=PANEL)
        op_wrap.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.ov_op_lab = tk.Label(op_wrap, text="0.90", bg=PANEL, fg=SIGNAL,
                                  font=(self.UI, 9), width=4)
        self.ov_op_sc = tk.Scale(op_wrap, from_=0.2, to=1.0, resolution=0.05,
                                 orient="horizontal", length=140, width=14,
                                 variable=self.ov_op_var, bg=PANEL, fg=INK,
                                 highlightthickness=0, bd=0,
                                 troughcolor="#5b6785",
                                 activebackground=SIGNAL, sliderrelief="raised",
                                 command=lambda v: self.ov_op_lab.config(
                                     text=f"{float(v):.2f}"))
        self.ov_op_sc.pack(side="left")
        self.ov_op_lab.pack(side="left", padx=(6, 0))
        self.tip(self.ov_op_sc,
                 "黑色圆角底的透明度：越低越能看清背后的画面（0.2 近乎全透）")

        self._label(ovf, "文字透明度").grid(row=2, column=2, sticky="w",
                                            padx=(12, 0), pady=(6, 0))
        self.ov_tx_var = tk.DoubleVar(value=1.0)
        tx_wrap = tk.Frame(ovf, bg=PANEL)
        tx_wrap.grid(row=2, column=3, sticky="w", pady=(6, 0))
        self.ov_tx_lab = tk.Label(tx_wrap, text="1.00", bg=PANEL, fg=SIGNAL,
                                  font=(self.UI, 9), width=4)
        self.ov_tx_sc = tk.Scale(tx_wrap, from_=0.3, to=1.0, resolution=0.05,
                                 orient="horizontal", length=140, width=14,
                                 variable=self.ov_tx_var, bg=PANEL, fg=INK,
                                 highlightthickness=0, bd=0,
                                 troughcolor="#5b6785",
                                 activebackground=SIGNAL, sliderrelief="raised",
                                 command=lambda v: self.ov_tx_lab.config(
                                     text=f"{float(v):.2f}"))
        self.ov_tx_sc.pack(side="left")
        self.ov_tx_lab.pack(side="left", padx=(6, 0))
        self.tip(self.ov_tx_sc,
                 "文字的透明度，与背景独立：背景拉到很透时文字仍可保持纯白清晰")

        self._label(ovf, "音频来源").grid(row=4, column=0, sticky="w",
                                          pady=(8, 0))
        self.ov_src_type = ttk.Combobox(
            ovf, width=16, state="readonly",
            values=["两者", "内部（系统音频）", "外部（麦克风）"])
        self.ov_src_type.grid(row=4, column=1, sticky="w", pady=(8, 0))
        self.tip(self.ov_src_type,
                 "外挂字幕只识别选定的音频来源（不会内外混在一条字幕里）：\n"
                 "内部=电脑播放的声音（看视频）；外部=麦克风（自己说话）\n"
                 "运行中也能在悬浮窗的控制面板里切换")

        self._label(ovf, "边距").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.ov_mx_var = tk.StringVar(value="36")
        self.ov_mx_sp = ttk.Spinbox(ovf, from_=0, to=200, width=4,
                                    textvariable=self.ov_mx_var)
        self.ov_mx_sp.grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.ov_my_var = tk.StringVar(value="16")
        self.ov_my_sp = ttk.Spinbox(ovf, from_=0, to=100, width=4,
                                    textvariable=self.ov_my_var)
        self.ov_my_sp.grid(row=3, column=2, sticky="w", padx=(12, 0),
                           pady=(8, 0))
        self.tip(self.ov_mx_sp, "文字左右边距（px）：留白越大，黑底越宽")
        self.tip(self.ov_my_sp, "文字上下边距（px）：影响字幕条的高度")

        self.ov_panel_var = tk.BooleanVar(value=True)
        self.ov_panel_cb = ttk.Checkbutton(
            ovf, text="控制面板悬停显示（默认隐藏）",
            variable=self.ov_panel_var, style="Card.TCheckbutton")
        self.ov_panel_cb.grid(row=5, column=0, columnspan=4, sticky="w",
                              pady=(8, 0))
        self.tip(self.ov_panel_cb,
                 "鼠标移到字幕上时浮出控制面板，移开自动收起\n"
                 "关闭此项则面板常显（面板里可「固定」临时钉住）")

        # 用哪块屏（副屏适配）：-1 自动（鼠标所在屏）/ 0 主屏 / 1..N 指定
        self._label(ovf, "屏幕").grid(row=3, column=2, sticky="e",
                                      padx=(16, 4), pady=(8, 0))
        self.ov_mon_cb = ttk.Combobox(
            ovf, width=14, state="readonly",
            values=["自动（鼠标所在屏）", "主屏", "屏幕 1", "屏幕 2", "屏幕 3"])
        self.ov_mon_cb.set("自动（鼠标所在屏）")
        self.ov_mon_cb.grid(row=3, column=3, sticky="w", pady=(8, 0))
        self.tip(self.ov_mon_cb,
                 "外挂显示在哪块屏：默认跟随鼠标（多显示器时最省事）\n"
                 "也可固定到主屏或第 1/2/3 块屏；拖动过位置后会记住")

        self.ov_mirror_var = tk.BooleanVar(value=False)
        self.ov_mirror_cb = ttk.Checkbutton(
            ovf, text="镜像主程序结果（不重复识别/翻译）",
            variable=self.ov_mirror_var, style="Card.TCheckbutton")
        self.ov_mirror_cb.grid(row=5, column=2, columnspan=2, sticky="w",
                               pady=(8, 0))
        self.tip(self.ov_mirror_cb,
                 "开启后外挂不再自己识别+翻译，只显示「保存并启动」那个主程序的字幕\n"
                 "两个一起用时不再重复消耗 API/算力（代价：无流式逐字，整句出现）\n"
                 "需要主程序在运行；音频来源、语言等设置都以主程序为准")

        self.btn_overlay = ttk.Button(ovf, text="启动外挂",
                                      command=self._toggle_overlay)
        self.btn_overlay.grid(row=6, column=0, columnspan=2, sticky="w",
                              pady=(10, 0))
        self.btn_ov_save = ttk.Button(ovf, text="保存设置",
                                      command=self._save_only)
        self.btn_ov_save.grid(row=6, column=2, sticky="w", pady=(10, 0))
        self.tip(self.btn_overlay,
                 "启动悬浮字幕；可与主程序同时使用，也可以单独用它\n"
                 "再点一次即停止（也可在字幕面板上点「退出」）\n"
                 "翻译模型跟随首页「翻译后端」的当前选择（启动外挂时读取并保存）；\n"
                 "外挂运行中改了首页模型，停止再启动才会生效")
        self.tip(self.btn_ov_save, "只保存外挂的外观设置，不启动悬浮字幕")

    @staticmethod
    def _overlay_pids() -> list[int]:
        """系统里正在运行的外挂进程 PID（含非本控制台启动的残留实例）。

        Windows 没有 pgrep，用 psutil 遍历进程命令行匹配。
        匹配词同时认「-m livetrans.overlay」与打包后的「LiveTrans.exe --overlay」。
        """
        me = os.getpid()
        found: list[int] = []
        try:
            import psutil
        except ImportError:
            return found
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == me:
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                name = (p.info.get("name") or "").lower()
                if ("livetrans.overlay" in cmd or "--overlay" in cmd
                        or (name.startswith("livetrans") and "--overlay" in cmd)):
                    found.append(p.info["pid"])
            except Exception:  # noqa: BLE001 - 进程可能已退出/无权限
                continue
        return found

    @staticmethod
    def _kill_pids(pids: list[int]) -> int:
        """终止给定 PID（返回成功数）。Windows 上没有 pkill，用 psutil。"""
        n = 0
        try:
            import psutil
        except ImportError:
            return n
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
                n += 1
            except Exception:  # noqa: BLE001
                continue
        return n

    def _toggle_overlay(self) -> None:
        """启动/停止字幕外挂（独立进程，复用当前配置）。"""
        if self.ov_proc is not None and self.ov_proc.poll() is None:
            self.ov_proc.terminate()
            self.btn_overlay.config(text="启动外挂")
            self.set_status("正在停止字幕外挂 ...", WARN)
            return
        # 停止：控制台没记录进程、但系统里还有外挂实例（上次残留/手动启动）
        stale = self._overlay_pids() if self.ov_proc is None else []
        if stale:
            n = self._kill_pids(stale)
            self.btn_overlay.config(text="启动外挂")
            if n:
                self.set_status(f"已停止残留的外挂实例（PID {stale}）", WARN)
            else:
                self.set_status(f"发现残留外挂但无法终止（PID {stale}）", BAD)
            return
        if not self._apply_and_save():
            return
        missing = missing_overlay_deps()          # 依赖自检：给命令而不是堆栈
        if missing:
            hint = f"缺少: {'、'.join(missing)}\n\n修复：\n{overlay_fix_hint()}"
            self.set_status(f"外挂依赖缺失：{'、'.join(missing)}", BAD)
            messagebox.showerror("无法启动字幕外挂（缺少运行组件）", hint)
            return
        self._ov_log_file = open(LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
        self._ov_log_file.write(
            f"# 字幕外挂启动 {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        self._ov_log_file.flush()
        # 打包后不能用 `-m livetrans.overlay`，app_command 会自动选 exe 形式
        self.ov_proc = subprocess.Popen(
            app_command(), cwd=str(BASE),
            env=merged_env(self.cfg.get("providers") or {}),
            stdout=self._ov_log_file, stderr=subprocess.STDOUT)
        self.btn_overlay.config(text="停止外挂")
        self.set_status("字幕外挂已启动：字幕在屏幕底部居中，鼠标移上去出控制面板；"
                        "停止请点本按钮或面板「退出」", SIGNAL)
