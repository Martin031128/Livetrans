"""LiveTrans 控制台：可视化设置 + Key 管理 + 一键启动 + 会话总结。

本文件只保留"外壳"：窗口骨架、页面装配、配置读写、启动控制。
具体内容已拆分：
  - livetrans/ui/theme.py          配色/字体/ttk 样式
  - livetrans/ui/widgets.py        悬停提示、状态气泡
  - livetrans/ui/page_backend.py   「翻译后端」页
  - livetrans/ui/key_ui.py          服务商与 API Key 交互
  - livetrans/ui/page_audio.py     「音频源」页
  - livetrans/ui/page_sessions.py  「会话总结」页
  - livetrans/ui/page_chat.py      「对话」页
  - livetrans/ui/overlay_card.py   字幕外挂的启停
  - livetrans/providers.py         服务商数据层（模型列表等）
  - livetrans/paths.py             路径常量   livetrans/langs.py 语言表
  - livetrans/sysmon.py            本机资源监控
用法：
  - 开发：python3 launcher.py（建议从 prototype 目录运行）
  - 桌面：应用菜单中的 LiveTrans
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sounddevice as sd
import yaml

from livetrans.capture import list_monitor_sources
from livetrans import configstore
from livetrans.config import load_config
from livetrans.deps import (APT_FIX, PIP_FIX, missing_pip_deps,
                            missing_system_deps)
from livetrans.keys import (KEYS_PATH, add_history, detect_provider,
                            key_env_overlay, load_any, load_history,
                            mask_key, merged_env, save_keys)
from livetrans.langs import (CODE_TO_LANG, LANG_CODES, LANGUAGES,
                             MAX_WORDS_DEFAULT, OV_SRC_LABELS,
                             OV_SRC_VALUES, SOURCE_LANGS, TARGET_LANGS)
from livetrans.paths import (BASE, CONFIG_PATH, DATA_DIR,
                             EXAMPLE_PATH, LOG_PATH, SESSIONS_DIR,
                             ensure_data_files)
from livetrans.providers import example_providers, fetch_model_list, load_yaml
from livetrans.sysmon import collect_local_stats
from livetrans.translate import LLMTranslator
from livetrans.ui.key_ui import KeyUIMixin
from livetrans.ui.overlay_card import OverlayCardMixin
from livetrans.ui.page_audio import AudioPageMixin
from livetrans.ui.page_backend import BackendPageMixin
from livetrans.ui.page_chat import ChatPageMixin
from livetrans.ui.page_sessions import SessionsPageMixin
from livetrans.ui.theme import (BAD, BG, BORDER, DIM, EXTERNAL, FIELD, INK,
                                INTERNAL, ON_SIGNAL, PANEL, PANEL_2, SIGNAL,
                                WARN, apply_theme, pick_fonts)
from livetrans.ui.widgets import (StatusToast, ToolTip,
                                  button)


class Launcher(KeyUIMixin, BackendPageMixin, AudioPageMixin,
               SessionsPageMixin, ChatPageMixin, OverlayCardMixin, tk.Tk):
    """控制台主窗口（各页面逻辑见 livetrans/ui/ 下对应模块）。"""

    def _setup_style(self) -> None:
        """挑字体 + 应用深色主题（实现见 livetrans/ui/theme.py）。"""
        self.UI, self.MONO = pick_fonts()
        self.style = apply_theme(self, self.UI, self.MONO)

    def __init__(self):
        super().__init__(className="livetrans")   # 供 .desktop StartupWMClass 匹配
        self.title("LiveTrans · 控制台")
        self.configure(bg=BG)
        self.resizable(False, False)

        self.proc: subprocess.Popen | None = None
        self._log_file = None
        self._log_path: Path = LOG_PATH
        self._key_shown = False
        self._dl_model_running = False
        self._speed_running = False
        self._fetch_inflight = False
        self.ov_proc: subprocess.Popen | None = None
        self._ov_log_file = None
        self._wave_t = 0.0
        self._key_notice = ""                     # 启动期归属纠错提示
        self._dialog_open = False                 # 归属确认弹窗打开中（防重入）
        self._last_processed: tuple | None = None  # 最近处理过的 (服务商, 输入值)，去重
        self._type_timer: str | None = None       # 键入防抖定时器 id
        self._base_provider = "deepseek"          # 基准服务商：清空/清除后下拉回退目标
        self._shown_provider: str | None = None   # 下拉当前展示的服务商
        self._field_attribution: str | None = None  # 输入框内容的临时归属（未保存）

        self._seeded = ensure_data_files()   # 只读安装时首次播种配置
        self.cfg = load_yaml()
        # {provider名: key}：从 keys.env（ENV 名键）/旧 keys.yaml（provider 名键）
        # 归一而来，并按 key 签名自动纠正错误归属
        self.keys: dict[str, str] = self._load_provider_keys()
        self._saved_keys: dict[str, str] = dict(self.keys)

        self._setup_style()
        self.toast = StatusToast(self, font=(self.UI, 9))
        self._build_header()
        # 顺序要紧：底部操作栏先 pack（side="bottom" 先占位），记事本再 fill/expand；
        # 反过来的话窗口一矮，操作栏就被记事本挤没了（真实 bug）
        self._build_actionbar()
        self._build_pages()
        # 高度跟随当前页：各页卡片数差别大（翻译后端 1265px vs 会话总结 446px），
        # 固定用最高页会把矮页撑出大片空白、还会在小屏上顶出屏幕外
        self.minsize(760, 520)
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._fit_window())
        self.after(60, self._fit_window)
        self._load_into_ui()
        self._watchdog()
        self._animate_wave()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._key_notice:
            self.after(300, lambda: self.set_status(self._key_notice, WARN))

    def _on_close(self) -> None:
        """关窗前取消定时器，避免 Tcl after 残留报错。"""
        for job in (getattr(self, "_wave_job", None),
                    getattr(self, "_watch_job", None),
                    self._type_timer):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self.toast.hide()
        self.destroy()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=20, pady=(16, 2))

        # 声波：外(麦克风绿)-内(系统蓝)-信号(青) 的来源语义
        self.wave = tk.Canvas(header, bg=BG, width=92, height=46,
                              highlightthickness=0)
        self.wave.pack(side="left", padx=(0, 14))
        colors = [EXTERNAL, INTERNAL, SIGNAL, SIGNAL, INTERNAL, EXTERNAL]
        bases = [7, 11, 15, 15, 11, 7]
        speeds = [1.1, 0.9, 1.3, 1.3, 0.9, 1.1]
        phases = [0.0, 1.3, 2.1, 4.2, 5.0, 2.4]
        self._wave_bars = []
        for i in range(6):
            cx = 9 + i * 15
            ln = self.wave.create_line(cx, 23, cx, 23, fill=colors[i],
                                       width=7, capstyle="round")
            self._wave_bars.append((ln, bases[i], speeds[i], phases[i]))

        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="LiveTrans", bg=BG, fg=INK,
                 font=(self.UI, 16, "bold")).pack(anchor="w")
        tk.Label(titles, text="实时语音翻译 · 双声道字幕", bg=BG, fg=DIM,
                 font=(self.UI, 9)).pack(anchor="w")

    def _animate_wave(self) -> None:
        self._wave_t += 0.09
        for ln, base, speed, phase in self._wave_bars:
            h = base * (0.5 + 0.5 * math.sin(self._wave_t * speed + phase))
            cx = self.wave.coords(ln)[0]
            self.wave.coords(ln, cx, 23 - h, cx, 23 + h)
        self._wave_job = self.after(45, self._animate_wave)

    def _fit_window(self) -> None:
        """把窗口高度收成"当前页需要的高度"（宽度保持不变，避免换页时左右跳）。"""
        try:
            self.update_idletasks()
            nb = self.nb
            tabs = [nb.nametowidget(nb.tabs()[i]) for i in range(len(nb.tabs()))]
            cur = nb.nametowidget(nb.select())
            tallest = max(t.winfo_reqheight() for t in tabs)
            chrome = self.winfo_reqheight() - nb.winfo_reqheight()   # 头尾 + 边距
            nb_chrome = nb.winfo_reqheight() - tallest               # 页签条 + 边框
            want = chrome + nb_chrome + cur.winfo_reqheight()
            cap = self.winfo_screenheight() - 90                     # 别顶出屏幕
            want = max(self.minsize()[1], min(want, cap))
            self.geometry(f"{self.winfo_width()}x{want}")
        except (tk.TclError, ValueError):
            pass

    def _build_pages(self) -> None:
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(6, 0))
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(4, 0))

        self._page_backend()
        self._page_audio()
        self._page_sessions()
        self._page_chat()

    def _ui_call(self, fn) -> None:
        """后台线程回到 UI 线程执行（窗口已关闭/解释器收摊就静默丢弃）。

        直接 self.after(0, ...) 在关窗瞬间会抛 TclError/RuntimeError，满屏 traceback。
        """
        try:
            self.after(0, fn)
        except (tk.TclError, RuntimeError):
            pass

    def _scroll_page(self, title: str) -> tk.Frame:
        """把页内容放进可滚动容器：高页（翻译后端 1250px）在小屏上也能看全。

        内容不高时滚动条自动隐藏；滚轮只在指针位于本页时接管，
        不影响其它页的输入控件。
        """
        page = ttk.Frame(self.nb)
        self.nb.add(page, text=title)
        canvas = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync(_e=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            # 画布请求高度跟着内容走：窗口高度才能按内容自适应
            # （不够高时由 _fit_window 的屏幕上限压回来，滚动条随后出现）
            canvas.configure(height=inner.winfo_reqheight())
            need = inner.winfo_reqheight() > canvas.winfo_height()
            if need and not bar.winfo_ismapped():
                bar.pack(side="right", fill="y")
            elif not need and bar.winfo_ismapped():
                bar.pack_forget()
            canvas.itemconfigure(win, width=canvas.winfo_width())

        inner.bind("<Configure>", _sync)
        canvas.bind("<Configure>", _sync)

        def _wheel(e) -> None:
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

        seqs = ("<MouseWheel>", "<Button-4>", "<Button-5>")
        canvas.bind("<Enter>", lambda _e: [canvas.bind_all(q, _wheel)
                                           for q in seqs])
        canvas.bind("<Leave>", lambda _e: [canvas.unbind_all(q) for q in seqs])
        return inner

    def _card(self, parent: ttk.Notebook | tk.Frame, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe",
                              padding=(14, 10))

    def _label(self, parent, text: str, style: str = "Panel.TLabel") -> ttk.Label:
        return ttk.Label(parent, text=text, style=style)

    def tip(self, widget, text: str) -> None:
        """给控件挂悬停提示（收纳小字注释）。"""
        ToolTip(widget, text, font=(self.UI, 9))
        widget._tip_text = text        # 便于自检/统一维护（不影响 Tk 行为）

    def _build_actionbar(self) -> None:
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", side="bottom")

        self.btn_start = button(bar, "▶  保存并启动", self._toggle_run,
                                kind="primary")
        self.btn_start.pack(side="left", padx=(16, 0), pady=(12, 6))
        self.btn_save_only = button(bar, "仅保存设置", self._save_only)
        self.btn_save_only.pack(side="left", padx=8, pady=(12, 6))
        self.btn_logs = button(bar, "运行日志", self._open_logs)
        self.btn_logs.pack(side="left", padx=8, pady=(12, 6))
        self.tip(self.btn_start,
                 "把当前设置写入 config.yaml 并启动字幕程序\n"
                 "运行中再点一次即停止")
        self.tip(self.btn_save_only, "只保存配置不启动（改完设置随手保存）")
        self.tip(self.btn_logs,
                 "打开 run.log：启动过程、识别/翻译耗时、报错都在里面\n"
                 "（每次启动覆盖上一次）")

    def set_status(self, text: str, color: str = DIM) -> None:
        """状态消息 -> 右下角 toast（错误停留更久，普通消息几秒后自动消失）。"""
        duration = {BAD: 10000, WARN: 7000}.get(color, 5000)
        self.toast.show(text, color, duration)

    def _apply_and_save(self) -> bool:
        self.cfg.setdefault("audio", {})
        self.cfg["audio"]["mic_enabled"] = self.mic_var.get()
        self.cfg["audio"]["monitor_enabled"] = self.mon_var.get()
        self.cfg["audio"]["mic_device"] = self._mic_ids[self.mic_dev.current()]
        self.cfg["audio"]["monitor_source"] = self._mon_names[self.mon_dev.current()]

        self.cfg.setdefault("translate", {})
        self.cfg["translate"]["provider"] = self.provider_name()
        self.cfg["translate"]["target_lang"] = self.target_lang.get()
        try:
            self.cfg["translate"]["context_window"] = int(self.ctx_var.get() or 8)
            self.cfg["translate"]["llm_concurrency"] = max(
                1, min(8, int(self.conc_var.get() or 2)))
        except ValueError:
            self.cfg["translate"]["context_window"] = 8
        self.cfg["translate"]["mode"] = self.mode_var.get()
        self.cfg["translate"]["auto_zh_to_en"] = bool(self.auto_zh_en_var.get())
        self.cfg["translate"]["fallback_local"] = bool(self.fallback_var.get())

        # 字幕外挂设置
        ov_prev = self.cfg.get("overlay", {}) or {}
        self.cfg["overlay"] = {
            "source": OV_SRC_VALUES.get(self.ov_src_type.get(), "both"),
            "always_on_top": bool(self.ov_top_var.get()),
            "click_through": bool(self.ov_ct_var.get()),
            "show_source": bool(self.ov_src_var.get()),
            "width_ratio": ov_prev.get("width_ratio", 0.8),
            "bottom_offset": ov_prev.get("bottom_offset", 0.08),
            "pos_x": ov_prev.get("pos_x", -1),      # 保留外挂拖过的位置
            "pos_y": ov_prev.get("pos_y", -1),
            "font_size": int(float(self.ov_font_var.get() or 34)),
            "opacity": float(self.ov_op_var.get() or 0.9),
            "text_opacity": float(self.ov_tx_var.get() or 1.0),
            "panel_auto_hide": bool(self.ov_panel_var.get()),
            "margin_x": int(float(self.ov_mx_var.get() or 36)),
            "margin_y": int(float(self.ov_my_var.get() or 16)),
            "mirror": bool(self.ov_mirror_var.get()),
            "monitor": self._monitor_value(),
        }
        # 外挂托管的键以磁盘最新为准（self.cfg 是控制台启动时的快照，外挂
        # 运行中拖动/保存的最新值不在里面，否则控制台一保存就冲回旧值）：
        # 位置/宽度/偏移/配色永远取磁盘；字号与两根透明度双端都可调，
        # 仅当控制台这边没动过（与启动快照一致）时才取磁盘最新
        _disk_ov = configstore.read_section(CONFIG_PATH, "overlay")
        for _k in ("text_color", "bg_color", "pos_x", "pos_y",
                   "width_ratio", "bottom_offset"):
            if _disk_ov.get(_k) is not None:
                self.cfg["overlay"][_k] = _disk_ov[_k]
        for _k in ("font_size", "opacity", "text_opacity"):
            if (_disk_ov.get(_k) is not None
                    and self.cfg["overlay"].get(_k) == ov_prev.get(_k)):
                self.cfg["overlay"][_k] = _disk_ov[_k]

        # 总结/对话 模型选择（provider 存内部名，不存展示 label）
        self.cfg["assistant"] = {
            "source_type": self.asst_type_var.get(),
            "provider": getattr(self, "_asst_prov_by_label", {}).get(
                self.asst_provider.get(), ""),
            "model": (self.asst_model.get() or "").strip(),
        }
        if self.mode_var.get() == "professional":
            self.cfg["translate"]["professional_domain"] = self.domain.get()

        # 模型选择写回对应后端
        name = self.provider_name()
        model = self.model_cb.get().strip()
        self.cfg.setdefault("providers", {}).setdefault(name, {})
        if model:
            self.cfg["providers"][name]["model"] = model

        self.cfg.setdefault("asr", {})
        self.cfg["asr"].pop("engine", None)          # Whisper 已移除，清理遗留字段
        self.cfg["asr"].pop("model_size", None)
        self.cfg["asr"].pop("device", None)
        self.cfg["asr"].pop("compute_type", None)
        try:
            self.cfg["asr"]["max_words_per_segment"] = max(
                0, int(self.max_words_var.get() or MAX_WORDS_DEFAULT))
            self.cfg["asr"]["silence_ms"] = max(
                300, min(1200, int(self.sil_var.get() or 550)))
        except ValueError:
            self.cfg["asr"]["max_words_per_segment"] = MAX_WORDS_DEFAULT
        self.cfg["asr"]["language"] = LANG_CODES.get(
            self.source_lang.get(), None) or "auto"

        # 本地模型显存管理（默认手动：不自动占、也不自动放）
        self.cfg["local"] = {
            "auto_unload_min": (int(self.local_min_var.get() or 0)
                                if self.local_auto_var.get() else 0),
            "unload_others_on_start": bool(self.local_others_var.get()),
            "unload_on_exit": bool(self.local_exit_var.get()),
        }

        # 声纹角色标注（模型路径与最短段长沿用已存配置）
        spk_prev = self.cfg.get("speaker") or {}
        try:
            spk_max = max(2, min(8, int(self.spk_max.get() or 6)))
        except (ValueError, tk.TclError):
            spk_max = 6
        self.cfg["speaker"] = {
            "enabled": bool(self.spk_var.get()),
            "threshold": round(float(self.spk_th.get() or 0.55), 2),
            "max_speakers": spk_max,
            "min_segment_sec": float(spk_prev.get("min_segment_sec", 0.6)),
            "model": spk_prev.get("model", ""),
            "num_threads": int(spk_prev.get("num_threads", 2)),
        }

        try:
            self._save_keys_now()
            # subtitle/dialog 段归主程序字幕窗维护：整份重写前从磁盘拉新，
            # 避免用启动快照把它们冲回旧值
            configstore.refresh_sections(
                self.cfg, ("subtitle", "dialog"), CONFIG_PATH)
            CONFIG_PATH.write_text(
                "# 由 launcher.py 生成（完整注释见 config.example.yaml）\n"
                + yaml.safe_dump(self.cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
        except OSError as e:
            messagebox.showerror("保存失败", str(e))
            return False
        self._base_provider = self.provider_name()   # 保存后以当前选择为基准
        self._refresh_key_badge()
        return True

    def _load_into_ui(self) -> None:
        # 麦克风设备（PortAudio 输入设备）
        mic_items = ["默认输入设备"]
        self._mic_ids = [None]
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    mic_items.append(f"[{i}] {d['name']}")
                    self._mic_ids.append(i)
        except Exception:  # noqa: BLE001
            pass
        self.mic_dev["values"] = mic_items
        cfg_mic = self.cfg.get("audio", {}).get("mic_device")
        self.mic_dev.current(self._mic_ids.index(cfg_mic)
                             if cfg_mic in self._mic_ids else 0)

        # monitor 源（pactl）
        mon_items = ["自动选择（推荐）"]
        self._mon_names = [None]
        for name, label in list_monitor_sources():
            mon_items.append(f"{label} ({name.rsplit('.', 2)[0].split('.')[-1]})")
            self._mon_names.append(name)
        self.mon_dev["values"] = mon_items
        cfg_mon = self.cfg.get("audio", {}).get("monitor_source")
        self.mon_dev.current(self._mon_names.index(cfg_mon)
                             if cfg_mon in self._mon_names else 0)

        # 本地模型显存管理（读回上次设置）
        loc = self.cfg.get("local") or {}
        if hasattr(self, "local_auto_var"):
            minutes = int(loc.get("auto_unload_min") or 0)
            self.local_auto_var.set(minutes > 0)
            self.local_min_var.set(str(minutes or 10))
            self.local_others_var.set(bool(loc.get("unload_others_on_start", True)))
            self.local_exit_var.set(bool(loc.get("unload_on_exit", False)))

        # 声纹卡片状态（依赖/模型是否齐备）
        if hasattr(self, "spk_status"):
            self._spk_hint()

        # 服务商（label 显示）+ 类型分类（api / local）+ 联动
        self._prov_by_label: dict[str, str] = {}
        self._prov_type: dict[str, str] = {}
        for name in (self.cfg.get("providers") or {}):
            label = self._provider_meta(name).get("label") or name
            self._prov_by_label[label] = name
            self._prov_type[name] = self._provider_meta(name).get("type", "api")
        cur = self.cfg.get("translate", {}).get("provider", "")
        cur_type = self._prov_type.get(cur, "api")
        self.src_type_var.set(cur_type)
        self._refresh_provider_list(cur_type)
        cur_label = next((l for l, n in self._prov_by_label.items() if n == cur), "")
        self.provider.set(cur_label)
        self._on_provider_change()
        self._base_provider = self.provider_name()

        self.source_lang.set(CODE_TO_LANG.get(
            self.cfg.get("asr", {}).get("language"), "自动识别"))
        self.target_lang.set(self.cfg.get("translate", {}).get("target_lang", "中文"))
        self.ctx_var.set(str(self.cfg.get("translate", {}).get("context_window", 8)))
        self.conc_var.set(str(self.cfg.get("translate", {}).get("llm_concurrency", 2)))
        self.mode_var.set(self.cfg.get("translate", {}).get("mode", "normal"))
        self.fallback_var.set(bool(self.cfg.get("translate", {}).get(
            "fallback_local", True)))
        self.auto_zh_en_var.set(bool(self.cfg.get("translate", {}).get(
            "auto_zh_to_en", False)))

        # 字幕外挂
        ov = self.cfg.get("overlay", {}) or {}
        self.ov_top_var.set(bool(ov.get("always_on_top", True)))
        self.ov_ct_var.set(bool(ov.get("click_through", True)))
        self.ov_src_var.set(bool(ov.get("show_source", True)))
        self.ov_font_var.set(str(ov.get("font_size", 34)))
        self.ov_op_var.set(float(ov.get("opacity", 0.9)))
        self.ov_op_lab.config(text=f"{float(ov.get('opacity', 0.9)):.2f}")
        self.ov_tx_var.set(float(ov.get("text_opacity", 1.0)))
        self.ov_tx_lab.config(text=f"{float(ov.get('text_opacity', 1.0)):.2f}")
        self.ov_panel_var.set(bool(ov.get("panel_auto_hide", True)))
        self.ov_mirror_var.set(bool(ov.get("mirror", False)))
        _mon = int(ov.get("monitor", -1))
        self.ov_mon_cb.set({-1: "自动（鼠标所在屏）", 0: "主屏"}.get(
            _mon, f"屏幕 {_mon}" if _mon > 0 else "自动（鼠标所在屏）"))
        self.ov_src_type.set(OV_SRC_LABELS.get(
            str(ov.get("source", "both")), "两者"))
        self.ov_mx_var.set(str(ov.get("margin_x", 36)))
        self.ov_my_var.set(str(ov.get("margin_y", 16)))

        # 总结/对话 模型选择
        asst = self.cfg.get("assistant", {}) or {}
        self.asst_type_var.set(asst.get("source_type", "api"))
        self._on_asst_type_change()
        saved_prov = asst.get("provider", "")
        if saved_prov:
            label = self._provider_meta(saved_prov).get("label") or saved_prov
            if label in (self.asst_provider["values"] or []):
                self.asst_provider.set(label)
        self._on_asst_provider_change()
        if asst.get("model"):
            self.asst_model.set(asst["model"])
        self.domain.insert(0, self.cfg.get("translate", {}).get("professional_domain", ""))
        self._toggle_mode()

        # ASR
        self.max_words_var.set(str(self.cfg.get("asr", {}).get(
            "max_words_per_segment", MAX_WORDS_DEFAULT)))
        self.sil_var.set(str(self.cfg.get("asr", {}).get("silence_ms", 550)))
        self._refresh_model_btn()
        self._refresh_history()

        # 模型列表默认从 API 自动获取（有 Key 才发请求；失败静默回退静态清单）
        self.after(800, self._fetch_models, True)

        # 术语表提示
        self._refresh_glossary_hint()

    def _toggle_run(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.set_status("正在停止 ...", WARN)
            return
        if not self._apply_and_save():
            return
        missing = missing_pip_deps() + missing_system_deps()   # 依赖自检：给修复命令
        if missing:
            self.set_status(f"缺少 Python 依赖：{'、'.join(missing)}", BAD)
            messagebox.showerror(
                "无法启动（缺少 Python 依赖）",
                f"缺少：{'、'.join(missing)}\n\n修复：\n{PIP_FIX}")
            return
        self.btn_start.config(text="■  停止", state="disabled")
        self.set_status("启动中（首次会下载模型）...", INK)
        # 主程序 stdout/stderr 重定向到运行日志；启动前轮转，保留最近几份
        self._rotate_log()
        self._log_file = open(LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115
        self._log_path = LOG_PATH
        self._log_file.write(f"# LiveTrans 运行日志 {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        self._log_file.flush()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "livetrans.main"], cwd=str(BASE),
            env=merged_env(self.cfg.get("providers") or {}),
            stdout=self._log_file, stderr=subprocess.STDOUT)
        self.after(1500, lambda: self.btn_start.config(state="normal"))
        self.after(1600, lambda: self.set_status(
            f"运行中 · 字幕窗已弹出 · 日志 {LOG_PATH.name}", SIGNAL))

    MONITOR_VALUES = {0: "自动（鼠标所在屏）", 1: "主屏", 2: "屏幕 1",
                      3: "屏幕 2", 4: "屏幕 3"}

    def _monitor_value(self) -> int:
        """屏幕下拉 -> overlay.monitor（-1 自动 / 0 主屏 / 1..N 指定序号）。"""
        label = self.ov_mon_cb.get()
        for value, text in self.MONITOR_VALUES.items():
            if text == label:
                return -1 if value == 0 else value - 1
        return -1

    LOG_KEEP = 5          # 保留的历史日志份数（run.log.1 … run.log.5）

    def _rotate_log(self) -> None:
        """启动前轮转运行日志：run.log -> run.log.1 -> …（最多留 LOG_KEEP 份）。

        之前每次启动直接覆盖，出问题想回看上一轮就没日志了。
        """
        try:
            for i in range(self.LOG_KEEP - 1, 0, -1):
                src = LOG_PATH.with_name(f"{LOG_PATH.name}.{i}")
                dst = LOG_PATH.with_name(f"{LOG_PATH.name}.{i + 1}")
                if src.is_file():
                    dst.unlink(missing_ok=True)
                    src.rename(dst)
            if LOG_PATH.is_file():
                LOG_PATH.rename(LOG_PATH.with_name(f"{LOG_PATH.name}.1"))
        except OSError as e:  # noqa: BLE001 - 轮转失败不影响启动
            self.set_status(f"日志轮转失败（忽略）: {e}", WARN)

    def _watchdog(self) -> None:
        if self.ov_proc is not None and self.ov_proc.poll() is not None:
            self.ov_proc = None
            self.btn_overlay.config(text="启动外挂", state="normal")
            if self._ov_log_file is not None:
                self._ov_log_file.close()
                self._ov_log_file = None
            self.set_status("字幕外挂已退出", DIM)
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.btn_start.config(text="▶  保存并启动")
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            if code == 0:
                self.set_status("已结束", DIM)
            else:
                tail = self._log_tail()
                self.set_status(
                    f"异常退出（码 {code}），详情见 run.log", BAD)
                if tail:
                    try:
                        messagebox.showerror(
                            "LiveTrans 异常退出",
                            f"退出码 {code}\n\n—— 运行日志末尾 ——\n{tail}\n\n"
                            f"完整日志: {self._log_path}")
                    except tk.TclError:       # 窗口已关闭
                        pass
        self._watch_job = self.after(500, self._watchdog)

    def _log_tail(self, n: int = 1200) -> str:
        """读当前运行日志的末尾（异常退出时展示给用户）。"""
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
            return text.strip()[-n:]
        except (OSError, AttributeError):
            return ""

    def _open_logs(self) -> None:
        if not LOG_PATH.is_file():
            self.set_status("还没有运行日志（点「保存并启动」一次即生成）", WARN)
            return
        webbrowser.open(LOG_PATH.resolve().as_uri())

    def _save_only(self) -> None:
        if self._apply_and_save():
            self.set_status(f"已保存 {CONFIG_PATH.name} 与 Key ✓", SIGNAL)

if __name__ == "__main__":
    Launcher().mainloop()
