"""「对话」页：以某场会话的原文/译文为素材与模型多轮问答。"""

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


CHAT_SYSTEM = """你是 LiveTrans 的会话助手。下面是本次实时翻译会话的记录
（[原文] 为识别到的语音，[译文] 为对应翻译）：
{context}

用户会针对这些内容提问（追问细节、总结要点、解释表达、改写等）。
要求：用中文回答；只依据上面的记录作答，记录中没有的信息不要编造；
引用内容保持简洁，必要时给出原文与译文的对照。"""


class ChatPageMixin:

    def _page_chat(self) -> None:
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="对话")

        card = self._card(page, "对话素材")
        card.pack(fill="x", padx=6, pady=6)
        self.chat_session = ttk.Combobox(card, width=40, state="readonly",
                                         postcommand=self._refresh_sessions)
        self.chat_session.grid(row=0, column=0, sticky="w")
        self.btn_chat_clear = ttk.Button(card, text="清空对话",
                                         command=self._chat_clear)
        self.btn_chat_clear.grid(row=0, column=1, padx=(10, 0))
        self.chat_model_lab = ttk.Label(card, text="",
                                        style="Panel.DimSmall.TLabel")
        self.chat_model_lab.grid(row=1, column=0, columnspan=2, sticky="w",
                                 pady=(8, 0))
        self.tip(self.chat_session,
                 "选一份会话日志作为对话素材：把其中的原文/译文喂给模型\n"
                 "（可追问细节、总结要点、解释表达）")
        self.tip(self.btn_chat_clear, "清空当前对话记录（保留素材选择）")

        body = tk.Frame(page, bg=PANEL)
        body.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.chat_view = tk.Text(body, wrap="word", bd=0, highlightthickness=0,
                                 bg=PANEL, fg=INK, font=(self.UI, 10),
                                 state="disabled", cursor="arrow",
                                 padx=10, pady=8)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.chat_view.yview)
        self.chat_view.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat_view.pack(side="left", fill="both", expand=True)
        self.chat_view.tag_configure("user", foreground=SIGNAL)
        self.chat_view.tag_configure("ai", foreground=INK)
        self.chat_view.tag_configure("sys", foreground=DIM)

        row = tk.Frame(page, bg=PANEL)
        row.pack(fill="x", padx=6, pady=(0, 8))
        self.chat_in = tk.Text(row, height=3, wrap="word", bd=0, relief="flat",
                               bg=BORDER, fg=INK, insertbackground=INK,
                               font=(self.UI, 10), padx=8, pady=6)
        self.chat_in.pack(side="left", fill="both", expand=True)
        self.chat_in.bind("<Control-Return>",
                          lambda _e: (self._chat_send(), "break")[1])
        side = tk.Frame(row, bg=PANEL)
        side.pack(side="left", padx=(8, 0), fill="y")
        self.btn_chat = ttk.Button(side, text="发送", style="Accent.TButton",
                                   command=self._chat_send)
        self.btn_chat.pack()
        self.tip(self.btn_chat, "发送提问（快捷键 Ctrl+Enter）")
        self._chat_hist: list[dict] = []
        self._chat_busy = False
        self._chat_append("选择会话后即可针对其中的原文/译文提问。", "sys")

    def _chat_append(self, text: str, tag: str = "ai") -> None:
        self.chat_view.config(state="normal")
        self.chat_view.insert("end", text + "\n", tag)
        self.chat_view.see("end")
        self.chat_view.config(state="disabled")

    def _chat_clear(self) -> None:
        self._chat_hist = []
        self.chat_view.config(state="normal")
        self.chat_view.delete("1.0", "end")
        self.chat_view.config(state="disabled")
        self._chat_append("已清空。", "sys")

    def _chat_send(self) -> None:
        if self._chat_busy:
            return
        msg = self.chat_in.get("1.0", "end").strip()
        if not msg:
            return
        target = self._assistant_target()     # 线程启动前读全部 tk 值
        if target is None:
            return
        prov, model, label = target
        session = self.chat_session.get().strip()
        self.chat_in.delete("1.0", "end")
        self._chat_append(f"你：{msg}", "user")
        self._chat_hist.append({"role": "user", "content": msg})
        self._chat_busy = True
        self.btn_chat.config(state="disabled", text="思考中…")
        self.set_status(f"对话中（{label}）...", INK)
        threading.Thread(target=self._chat_bg, daemon=True, name="chat",
                         args=(prov, model, session, list(self._chat_hist))).start()

    def _chat_bg(self, provider: str, model: str, session: str,
                 hist: list) -> None:
        err, reply = None, ""
        try:
            app_cfg = load_config(str(CONFIG_PATH) if CONFIG_PATH.is_file()
                                  else None)
            app_cfg.translate.provider = provider
            for env_name, key in key_env_overlay(app_cfg.providers).items():
                if not os.environ.get(env_name):
                    os.environ[env_name] = key
            prov_cfg = app_cfg.providers[provider]
            model = model or prov_cfg.model
            if is_local_base_url(prov_cfg.base_url):
                ensure_local_backend(
                    prov_cfg.base_url, model,
                    lambda m: self.after(0, lambda: self.set_status(m, INK)))
            translator = LLMTranslator(app_cfg.translate, app_cfg.providers)
            translator.model = model
            ctx = self._session_context(session) if session else ""
            reply = translator.chat(
                CHAT_SYSTEM.format(context=ctx or "（未选择会话，仅凭提问回答）"),
                hist)
        except Exception as e:  # noqa: BLE001 - 失败提示到界面
            err = f"{type(e).__name__}: {e}"

        def done() -> None:
            self._chat_busy = False
            self.btn_chat.config(state="normal", text="发送")
            if err:
                self._chat_append(f"[出错] {err}", "sys")
                self.set_status(f"对话失败: {err}", BAD)
                return
            self._chat_hist.append({"role": "assistant", "content": reply})
            self._chat_append(f"AI：{reply}\n", "ai")
            self.set_status("对话完成", SIGNAL)
        try:
            self.after(0, done)
        except tk.TclError:
            pass

    @staticmethod
    def _session_context(name: str, max_records: int = 150) -> str:
        """把会话 jsonl 渲染成上下文文本（原文+译文，最近 N 条，限长）。"""
        p = SESSIONS_DIR / name
        if not p.is_file():
            return ""
        rows: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        text = "\n".join(
            f"[原文] {r.get('text', '')}\n[译文] {r.get('translation', '')}"
            for r in rows[-max_records:])
        return text[:8000]
