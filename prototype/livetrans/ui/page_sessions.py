"""「会话总结」页：会话列表、总结/对话模型选择、生成总结。"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sounddevice as sd
import yaml

from livetrans.asr import preload_model, sensevoice_ready
from livetrans.capture import list_monitor_sources
from livetrans.config import load_config
from livetrans.keys import (KEYS_PATH, add_history, detect_provider,
                            key_env_overlay, load_any, load_history,
                            mask_key, merged_env, save_keys)
from livetrans.langs import (CODE_TO_LANG, LANG_CODES, LANGUAGES,
                             MAX_WORDS_DEFAULT, OV_SRC_LABELS,
                             OV_SRC_VALUES, SOURCE_LANGS, TARGET_LANGS)
from livetrans.paths import (BASE, CONFIG_PATH,
                             EXAMPLE_PATH, LOG_PATH, SESSIONS_DIR)
from livetrans.providers import example_providers, fetch_model_list, load_yaml
from livetrans.sysmon import collect_local_stats
from livetrans.translate import (LLMTranslator, ensure_local_backend,
                                 is_local_base_url)
from livetrans.ui.theme import (BAD, BG, BORDER, DIM, EXTERNAL, FIELD, INK,
                                INTERNAL, ON_SIGNAL, PANEL, PANEL_2, SIGNAL,
                                WARN, pick_font)
from livetrans.ui.widgets import StatusToast, ToolTip


EXPORT_MODES = {"原文+译文": "bilingual", "仅译文": "dst", "仅原文": "src"}
EXPORT_CHANNELS = {"全部音频": None, "系统声音": "internal", "麦克风": "external"}


class SessionsPageMixin:

    def _page_sessions(self) -> None:
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="会话总结")

        card = self._card(page, "会话日志与总结")
        card.pack(fill="x", padx=6, pady=6)

        self.session_file = ttk.Combobox(card, width=44, state="readonly",
                                         postcommand=self._refresh_sessions)
        self.session_file.grid(row=0, column=0, columnspan=2, sticky="w")
        self.btn_sum = ttk.Button(card, text="生成总结", command=self._summarize)
        self.btn_sum.grid(row=0, column=2, padx=(10, 0))
        self.btn_open_sess = ttk.Button(card, text="打开会话目录",
                                        command=self._open_sessions)
        self.btn_open_sess.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.tip(self.btn_open_sess, "在文件管理器里打开 sessions 目录（原始 JSONL）")
        self.tip(self.session_file,
                 "选择要回顾的会话（每次录音/翻译结束都会自动保存一份）\n"
                 "展开下拉即可刷新列表")
        self.tip(self.btn_sum,
                 "用下方选择的模型总结这场会话：主题、要点、行动项\n"
                 "完成后自动打开结果；长会话也能处理")

        # 导出字幕（SRT / TXT）：内容与时间轴对齐，可直接用于播放器/剪辑
        ttk.Separator(card, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=8)
        self._label(card, "导出内容").grid(row=3, column=0, sticky="w")
        self.export_mode = ttk.Combobox(card, width=12, state="readonly",
                                        values=list(EXPORT_MODES))
        self.export_mode.set("原文+译文")
        self.export_mode.grid(row=3, column=1, sticky="w", padx=(8, 0))
        self._label(card, "声道").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.export_channel = ttk.Combobox(card, width=12, state="readonly",
                                           values=list(EXPORT_CHANNELS))
        self.export_channel.set("全部音频")
        self.export_channel.grid(row=4, column=1, sticky="w", padx=(8, 0),
                                 pady=(6, 0))
        self.btn_srt = ttk.Button(card, text="导出 SRT",
                                  command=self._export_subtitle)
        self.btn_srt.grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.btn_txt = ttk.Button(card, text="导出 TXT",
                                  command=lambda: self._export_subtitle(txt=True))
        self.btn_txt.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        self.tip(self.export_mode,
                 "导出内容：原文+译文（双语两条）/ 仅译文 / 仅原文")
        self.tip(self.export_channel,
                 "只导出某一路音频的字幕：系统声音（视频/会议）或麦克风（自己）")
        self.tip(self.btn_srt,
                 "把选中的会话导成 SRT 字幕：时间轴由每句完成时刻与翻译耗时推算，\n"
                 "严格单调不重叠，可直接挂到播放器/VLC/剪辑软件")
        self.tip(self.btn_txt, "导出纯文本，便于贴进笔记或再加工")

        # 总结 / 对话 共用模型选择（可与翻译后端不同：API 或本地模型）
        acard = self._card(page, "总结 / 对话模型")
        acard.pack(fill="x", padx=6, pady=6)
        self.asst_type_var = tk.StringVar(value="api")
        self._label(acard, "模型来源").grid(row=0, column=0, sticky="w")
        self.asst_api_rb = ttk.Radiobutton(
            acard, text="API（云端）", value="api",
            variable=self.asst_type_var, style="Card.TRadiobutton",
            command=self._on_asst_type_change)
        self.asst_api_rb.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.asst_local_rb = ttk.Radiobutton(
            acard, text="本地模型", value="local",
            variable=self.asst_type_var, style="Card.TRadiobutton",
            command=self._on_asst_type_change)
        self.asst_local_rb.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.tip(self.asst_api_rb,
                 "用云端 API 做总结/对话（共用「翻译后端」页已填的 Key）")
        self.tip(self.asst_local_rb,
                 "用本机模型做总结/对话：不联网、无额外费用，适合长会话")
        self._label(acard, "服务商").grid(row=1, column=0, sticky="w",
                                          pady=(8, 0))
        self.asst_provider = ttk.Combobox(acard, width=20, state="readonly")
        self.asst_provider.grid(row=1, column=1, sticky="w", padx=(8, 0),
                                pady=(8, 0))
        self.asst_provider.bind("<<ComboboxSelected>>",
                                self._on_asst_provider_change)
        self._label(acard, "模型").grid(row=2, column=0, sticky="w",
                                        pady=(8, 0))
        self.asst_model = ttk.Combobox(acard, width=28)   # 可输入清单外模型
        self.asst_model.grid(row=2, column=1, columnspan=2, sticky="w",
                             padx=(8, 0), pady=(8, 0))
        self.tip(self.asst_provider,
                 "总结/对话用的服务商（可与翻译后端不同）")
        self.tip(self.asst_model,
                 "总结/对话用的模型，可手动输入清单外的模型名\n"
                 "「生成总结」与「对话」页共用这里的设置")

    def _refresh_sessions(self) -> None:
        files = sorted(SESSIONS_DIR.glob("*.jsonl")) if SESSIONS_DIR.is_dir() else []
        names = [f.name for f in reversed(files)]
        self.session_file["values"] = names
        for cb in (getattr(self, "chat_session", None),):
            if cb is not None:
                keep = cb.get()
                cb["values"] = names
                if keep in names:
                    cb.set(keep)
                elif names:
                    cb.set(names[0])

    def _open_sessions(self) -> None:
        SESSIONS_DIR.mkdir(exist_ok=True)
        webbrowser.open(SESSIONS_DIR.resolve().as_uri())

    def _on_asst_type_change(self) -> None:
        """按来源类型（API/本地）过滤服务商列表。"""
        want = self.asst_type_var.get()
        names = [n for n in (self.cfg.get("providers") or {})
                 if self._provider_meta(n).get("type", "api") == want]
        self._asst_prov_by_label = {
            (self._provider_meta(n).get("label") or n): n for n in names}
        self.asst_provider["values"] = list(self._asst_prov_by_label)
        if names:
            self.asst_provider.set(
                self._provider_meta(names[0]).get("label") or names[0])
        else:
            self.asst_provider.set("")
        self._on_asst_provider_change()

    def _on_asst_provider_change(self, *_args) -> None:
        name = getattr(self, "_asst_prov_by_label", {}).get(
            self.asst_provider.get(), "")
        meta = self._provider_meta(name) if name else {}
        self.asst_model["values"] = list(meta.get("models") or [])
        if not self.asst_model.get():
            self.asst_model.set(meta.get("model") or "")
        self._refresh_chat_model_hint()

    def _assistant_target(self):
        """(provider, model, 显示名)；未选择返回 None 并提示。"""
        name = getattr(self, "_asst_prov_by_label", {}).get(
            self.asst_provider.get(), "")
        if not name:
            self.set_status("请先在「会话总结」页选择总结/对话用的服务商", WARN)
            return None
        meta = self._provider_meta(name)
        model = (self.asst_model.get() or "").strip() or meta.get("model", "")
        label = f"{meta.get('label') or name} / {model or '默认模型'}"
        return name, model, label

    def _refresh_chat_model_hint(self) -> None:
        if not hasattr(self, "chat_model_lab"):
            return
        t = self._assistant_target()
        self.chat_model_lab.config(
            text=f"当前对话模型：{t[2]}" if t else "尚未选择对话模型")

    def _summarize(self) -> None:
        name = self.session_file.get()
        if not name:
            messagebox.showinfo("提示", "请先在下拉框选择一个会话文件")
            return
        target = self._assistant_target()
        if target is None:
            return
        prov, model, label = target
        src = SESSIONS_DIR / name
        out = src.with_suffix(".summary.md")
        self.set_status(f"总结生成中（{label}）...", INK)
        self.btn_sum.config(state="disabled")
        threading.Thread(target=self._summarize_bg, args=(src, out, prov, model),
                         daemon=True).start()

    def _summarize_bg(self, src: Path, out: Path, prov: str = "",
                      model: str = "") -> None:
        cmd = [sys.executable, "-m", "livetrans.summarize", str(src),
               "-o", str(out)]
        if prov:
            cmd += ["--provider", prov]
        if model:
            cmd += ["--model", model]
        r = subprocess.run(
            cmd, cwd=str(BASE), capture_output=True, text=True,
            env=merged_env(self.cfg.get("providers") or {}))

        def done():
            self.btn_sum.config(state="normal")
            if r.returncode == 0:
                self.set_status(f"总结完成 {out.name} ✓", SIGNAL)
                webbrowser.open(out.resolve().as_uri())
            else:
                self.set_status("总结失败，请检查 API Key", BAD)
                messagebox.showerror("总结失败", r.stderr[-800:] or r.stdout[-800:])
        self.after(0, done)

    # ---- 导出字幕（SRT / TXT） ----

    def _export_subtitle(self, txt: bool = False) -> None:
        """把选中会话导出为 SRT（或 TXT）：文件选择 + 后台线程执行。"""
        name = self.session_file.get()
        if not name:
            messagebox.showinfo("提示", "请先在下拉框选择一个会话文件")
            return
        src = SESSIONS_DIR / name
        if not src.is_file():
            messagebox.showinfo("提示", f"会话文件不存在：{name}")
            return
        mode = EXPORT_MODES.get(self.export_mode.get(), "bilingual")
        channel = EXPORT_CHANNELS.get(self.export_channel.get())
        ext = ".txt" if txt else ".srt"
        path = filedialog.asksaveasfilename(
            title="导出字幕", initialdir=str(SESSIONS_DIR),
            initialfile=src.stem + ext, defaultextension=ext,
            filetypes=[("文本文件", "*.txt")] if txt else
                      [("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if not path:
            return
        self.set_status(f"导出中（{name}）...", INK)
        threading.Thread(
            target=self._export_bg,
            args=(src, Path(path), mode, channel, txt), daemon=True,
            name="export").start()

    def _export_bg(self, src: Path, dest: Path, mode: str,
                   channel: str | None, txt: bool) -> None:
        from livetrans.export import export
        try:
            out = export(src, out=dest, mode=mode, channel=channel, as_txt=txt)
        except Exception as e:  # noqa: BLE001 - 导出失败只提示，不影响其它功能
            msg = str(e)
            self.after(0, lambda: self.set_status(f"导出失败：{msg}", BAD))
            return
        self.after(0, lambda: self._export_done(out))

    def _export_done(self, out: Path) -> None:
        try:
            size = out.stat().st_size
        except OSError:
            size = 0
        self.set_status(f"已导出 {out.name}（{size / 1024:.0f} KB）✓", SIGNAL)
