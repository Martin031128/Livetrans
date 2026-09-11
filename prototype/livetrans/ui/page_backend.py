"""「翻译后端」页：服务商、Key 区、翻译参数、测速、模型拉取。"""

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
from livetrans.paths import (BASE, CONFIG_PATH, EXAMPLE_PATH, LOG_PATH, SESSIONS_DIR)
from livetrans.providers import example_providers, fetch_model_list, load_yaml
from livetrans.sysmon import collect_local_stats
from livetrans.translate import (LLMTranslator, ensure_local_backend,
                                 is_local_base_url)
from livetrans.ui.theme import (BAD, BG, BORDER, DIM, EXTERNAL, FIELD, INK,
                                INTERNAL, ON_SIGNAL, PANEL, PANEL_2, SIGNAL,
                                WARN, pick_font)
from livetrans.ui.widgets import (StatusToast, ToolTip,
                                  button)


class BackendPageMixin:

    def _page_backend(self) -> None:
        page = self._scroll_page("翻译后端")      # 卡片多：小屏上可滚动看全

        # 卡片 A：服务商与密钥
        svc = self._card(page, "服务商与 API Key")
        svc.pack(fill="x", padx=6, pady=6)

        self._label(svc, "模型来源").grid(row=0, column=0, sticky="w")
        self.src_type_var = tk.StringVar(value="api")
        self.type_api = ttk.Radiobutton(svc, text="API（云端）", value="api",
                                        variable=self.src_type_var,
                                        command=self._on_source_type_change)
        self.type_local = ttk.Radiobutton(svc, text="本地模型", value="local",
                                          variable=self.src_type_var,
                                          command=self._on_source_type_change)
        self.type_api.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.type_local.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.tip(self.type_api,
                 "云端服务商（DeepSeek / GLM / OpenAI…）：速度与质量更好，"
                 "需要填写对应 Key")
        self.tip(self.type_local,
                 "本机模型服务（Ollama）：无需 Key，隐私好，速度取决于显卡")

        self._label(svc, "服务商").grid(row=1, column=0, sticky="w")
        self.provider = ttk.Combobox(svc, width=20, state="readonly")
        self.provider.grid(row=1, column=1, sticky="w", padx=(8, 12))
        self.key_badge = ttk.Label(svc, text="", style="Badge.TLabel")
        self.key_badge.grid(row=1, column=2, sticky="w")
        self.btn_speed = ttk.Button(svc, text="测速", command=self._speed_test)
        self.btn_speed.grid(row=1, column=3, sticky="w", padx=(10, 0))
        self.tip(self.btn_speed,
                 "用一句话实测当前模型的翻译速度（出字时间 / 总耗时 / 每秒字数）\n"
                 "出字很慢？多半是深度思考模型或网络太远，换普通模型可解\n"
                 "本地模型第一次测速要先把模型载入内存（1-2 分钟），按钮上会显示秒表")
        self.provider.bind("<<ComboboxSelected>>", self._on_user_select_provider)

        # 本地模型资源监控（显存/CPU/内存），仅本地服务商时显示
        self.local_stats = ttk.Label(svc, text="", style="Badge.TLabel")
        self.local_stats.grid(row=1, column=4, sticky="w", padx=(14, 0))
        self._stats_job: str | None = None
        self._cpu_prev: tuple[int, int] | None = None

        self.hist_lab = self._label(svc, "历史")
        self.hist_lab.grid(row=3, column=0, sticky="w")
        self.hist_cb = ttk.Combobox(svc, width=34, state="readonly",
                                    postcommand=self._refresh_history)
        self.hist_cb.grid(row=3, column=1, columnspan=2, sticky="w",
                          padx=(8, 8), pady=(8, 0))
        self.btn_hist = ttk.Button(svc, text="载入", command=self._load_history_entry)
        self.btn_hist.grid(row=3, column=3, sticky="w", pady=(8, 0))
        self.tip(self.hist_cb, "用过的 API 记录（api_history.yaml，key 打码显示）")
        self.tip(self.btn_hist, "载入所选历史：切到对应服务商并回填 Key")

        self._key_lab = self._label(svc, "API Key")
        self._key_lab.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.key_entry = ttk.Entry(svc, width=36, show="•")
        self.key_entry.grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(8, 0))
        self.key_entry.bind("<KeyRelease>", self._on_key_type)
        self.key_entry.bind("<FocusOut>", lambda _e: self._handle_key_input(False))
        # 粘贴（Ctrl+V / Shift+Ins / X11 中键）：内容落位后到 idle 再检测（含归属弹窗）
        for _ev in ("<<Paste>>", "<<PasteSelection>>"):
            self.key_entry.bind(_ev, lambda _e: self.after_idle(
                lambda: self._handle_key_input(True)))
        self.btn_show = button(svc, "显示", self._toggle_key_show, kind="chip")
        self.btn_show.grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.btn_clear = button(svc, "清除", self._clear_key, kind="chip")
        self.btn_clear.grid(row=2, column=3, sticky="w", padx=(6, 0), pady=(8, 0))
        # API 专属行（本地服务商时整行隐藏，grid_remove 保留布局参数）
        self._key_row = [self._key_lab, self.key_entry, self.btn_show,
                         self.btn_clear]
        self._hist_row = [self.hist_lab, self.hist_cb, self.btn_hist]

        self.tip(self.key_entry,
                 "在这里粘贴服务商的 API Key（会保存在项目目录，便于下次直接用）\n"
                 "粘贴时自动识别属于哪家；多家共用前缀时会弹窗让你确认\n"
                 "清空输入框 = 撤销本次输入；彻底删除用右侧「清除」")
        self.tip(self.key_badge,
                 "Key 状态：先用系统已设置的环境变量，其次用本程序保存的 Key\n"
                 "本地模型后端不需要 Key（这里为空是正常的）")
        self.tip(self.provider,
                 "全部走 OpenAI 兼容协议，切换只改配置\n"
                 "本地部署：Ollama + qwen3:4b-instruct（q4 约 2.5GB 显存）")

        # 卡片 B：翻译
        tr = self._card(page, "翻译")
        tr.pack(fill="x", padx=6, pady=6)

        self._label(tr, "模型").grid(row=0, column=0, sticky="w")
        self.model_cb = ttk.Combobox(tr, width=28)          # editable：可输入清单外模型
        self.model_cb.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.btn_fetch = ttk.Button(tr, text="从 API 获取",
                                    command=lambda: self._fetch_models(False))
        self.btn_fetch.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.tip(self.model_cb,
                 "下拉为内置静态清单（config.example.yaml），可自由输入任意模型名；\n"
                 "随服务商切换自动更新")
        self.tip(self.btn_fetch,
                 "从当前服务商的 GET /models 接口拉取真实可用模型列表\n"
                 "（需已配置该家 Key；Ollama 需在本机运行），结果随保存持久化")

        self._label(tr, "输入语言").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.source_lang = ttk.Combobox(tr, width=12, values=SOURCE_LANGS,
                                        state="readonly")
        self.source_lang.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        self.tip(self.source_lang,
                 "语音识别的源语言\n"
                 "自动识别 = 引擎自动检测（默认，适合语言混杂场景）\n"
                 "指定语言可略微提升识别准确率与速度")

        self._label(tr, "输出语言").grid(row=1, column=2, sticky="w", padx=(18, 0),
                                         pady=(8, 0))
        self.target_lang = ttk.Combobox(tr, width=12, values=TARGET_LANGS,
                                        state="readonly")
        self.target_lang.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.tip(self.target_lang,
                 "字幕译文的目标语言（写入 LLM 翻译提示词）\n"
                 "注意：字幕窗「对话」模式若开着「双向同传」，两侧方向由\n"
                 "字幕窗顶栏「对话设置」决定，这里作为单侧/单向时的设置")

        self._label(tr, "上下文").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.ctx_var = tk.StringVar()
        self.ctx_n = ttk.Combobox(tr, width=5, textvariable=self.ctx_var,
                                  values=[str(i) for i in range(0, 31)])
        self.ctx_n.grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(tr, text="句", style="Panel.DimSmall.TLabel"
                  ).grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.tip(self.ctx_n,
                 "注入最近 N 句（原文+译文）帮助 LLM 理解上下文\n0 = 关闭（延迟最低）")

        self._label(tr, "并发").grid(row=2, column=3, sticky="w", padx=(14, 0),
                                     pady=(8, 0))
        self.conc_var = tk.StringVar()
        self.conc_n = ttk.Combobox(tr, width=5, textvariable=self.conc_var,
                                   values=["1", "2", "3", "4"])
        self.conc_n.grid(row=2, column=4, sticky="w", pady=(8, 0))
        ttk.Label(tr, text="路", style="Panel.DimSmall.TLabel"
                  ).grid(row=2, column=5, sticky="w", pady=(8, 0))
        self.tip(self.conc_n,
                 "并行翻译线程数：2（默认）= 显示当前句时已在后台翻下一句\n"
                 "调大堆压消化更快，但过大会触发 API 限速；1 = 纯串行")

        self.mode_var = tk.StringVar(value="normal")
        self._label(tr, "模式").grid(row=3, column=0, sticky="w")
        self.mode_normal = ttk.Radiobutton(
            tr, text="普通", value="normal", variable=self.mode_var,
            style="Card.TRadiobutton", command=self._toggle_mode)
        self.mode_normal.grid(row=3, column=1, sticky="w")
        self.mode_pro = ttk.Radiobutton(
            tr, text="专业", value="professional", variable=self.mode_var,
            style="Card.TRadiobutton", command=self._toggle_mode)
        self.mode_pro.grid(row=3, column=2, columnspan=2, sticky="w")
        self.tip(self.mode_normal, "逐句直接翻译：延迟最低，适合看视频/听讲")
        self.tip(self.mode_pro,
                 "专业模式：注入领域提示与术语表，专有名词更准（略慢）\n"
                 "选中后下方展开「专业领域」与「术语表」设置")

        # 双向场景：中文入 -> 英文出（开启后覆盖上面的输出语言）
        self.auto_zh_en_var = tk.BooleanVar(value=False)
        self.auto_zh_en_cb = ttk.Checkbutton(
            tr, text="中文语音自动译为英文", variable=self.auto_zh_en_var,
            style="Card.TCheckbutton")
        self.auto_zh_en_cb.grid(row=3, column=4, columnspan=2, sticky="w",
                                padx=(14, 0))
        self.tip(self.auto_zh_en_cb,
                 "开启后：识别到中文就译成英文；非中文仍按上面的「输出语言」翻译\n"
                 "适合双向对话（中英混说）的场合")

        # 专业模式专属区：选中「专业」时才出现
        self.pro_frame = tk.Frame(tr, bg=PANEL)
        self.pro_frame.grid(row=4, column=0, columnspan=4, sticky="w",
                            pady=(8, 0))
        tk.Label(self.pro_frame, text="专业领域", bg=PANEL, fg=INK,
                 font=(self.UI, 10)).grid(row=0, column=0, sticky="w")
        self.domain = ttk.Entry(self.pro_frame, width=18)
        self.domain.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.btn_glossary = ttk.Button(self.pro_frame, text="术语表…",
                                       command=self._pick_glossary)
        self.btn_glossary.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.glossary_hint = tk.Label(self.pro_frame, text="", bg=PANEL, fg=DIM,
                                      font=(self.UI, 9))
        self.glossary_hint.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.tip(self.domain, '领域提示，如 "医学" / "法律" / "金融"，影响选词与表达习惯')
        self.tip(self.btn_glossary, "选择 YAML 术语表文件；翻译时译名优先遵循术语表")

        # 弱网降级：云端失败自动切本地模型（默认开；没有本地后端时不生效）
        self.fallback_var = tk.BooleanVar(
            value=bool((self.cfg.get("translate") or {}).get("fallback_local", True)))
        self.fallback_cb = ttk.Checkbutton(
            tr, text="云端失败自动切本地模型", variable=self.fallback_var,
            style="Card.TCheckbutton")
        self.fallback_cb.grid(row=4, column=0, columnspan=3, sticky="w",
                              pady=(8, 0))
        self.tip(self.fallback_cb,
                 "云端 API 连续失败 2 句后，自动改用本机模型继续翻译（弱网/断网时不中断）\n"
                 "每 60 秒探一次云端，恢复后自动切回；没有本地后端（Ollama）时此项不生效\n"
                 "本地模型冷启约 1-2 分钟，第一句会慢一点")

        # 卡片：本地模型显存（默认手动：不自动占、也不自动放）
        lm = self._card(page, "本地模型显存（Ollama）")
        lm.pack(fill="x", padx=6, pady=6)
        lc = self.cfg.get("local") or {}
        self.mem_status = ttk.Label(lm, text="检测中…", style="Badge.TLabel")
        self.mem_status.grid(row=0, column=0, columnspan=4, sticky="w")
        self.tip(self.mem_status,
                 "当前常驻显存的模型（Ollama 载入后不会自己退出，直到空闲超时或手动卸载）\n"
                 "换模型时旧模型**不会**自动停止：用「启动时清理其它已加载模型」或手动释放")
        self.btn_unload = ttk.Button(lm, text="释放显存（取消挂载）",
                                     command=self._unload_models)
        self.btn_unload.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.tip(self.btn_unload,
                 "立刻把常驻模型从显存里卸下来（实测可立刻回收 3GB+）\n"
                 "代价：下次翻译要重新载入（本地模型冷启约 1-2 分钟）")
        self.local_auto_var = tk.BooleanVar(
            value=int(lc.get("auto_unload_min") or 0) > 0)
        self.local_min_var = tk.StringVar(
            value=str(int(lc.get("auto_unload_min") or 0) or 10))
        self.local_auto_cb = ttk.Checkbutton(
            lm, text="空闲自动卸载（默认关闭）", variable=self.local_auto_var,
            style="Card.TCheckbutton")
        self.local_auto_cb.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.local_min_sp = ttk.Spinbox(lm, from_=1, to=240, width=4,
                                        textvariable=self.local_min_var,
                                        state="readonly",
                                        values=[5, 10, 15, 20, 30, 60])
        self.local_min_sp.grid(row=2, column=1, sticky="w", padx=(8, 4),
                               pady=(8, 0))
        self._label(lm, "分钟无翻译后释放").grid(row=2, column=2, sticky="w",
                                                 pady=(8, 0))
        self.tip(self.local_auto_cb,
                 "开启后：连续这么多分钟没有翻译请求就自动卸载模型释放显存\n"
                 "默认关闭：模型一直驻留，下次说话秒级响应（推荐）\n"
                 "想要「说完就腾显卡」再打开，注意之后第一句会等 1-2 分钟冷启")

        self.local_others_var = tk.BooleanVar(
            value=bool(lc.get("unload_others_on_start", True)))
        self.local_others_cb = ttk.Checkbutton(
            lm, text="启动时清理其它已加载模型", variable=self.local_others_var,
            style="Card.TCheckbutton")
        self.local_others_cb.grid(row=3, column=0, columnspan=3, sticky="w",
                                  pady=(6, 0))
        self.tip(self.local_others_cb,
                 "只卸载「你没在用的旧模型」（换模型后旧模型仍占着显存，\n"
                 "实测会同时驻留两张：3.18GB + 0.48GB）\n"
                 "当前正在使用的模型不受影响；8G 显存上建议保持开启")
        self.local_exit_var = tk.BooleanVar(
            value=bool(lc.get("unload_on_exit", False)))
        self.local_exit_cb = ttk.Checkbutton(
            lm, text="停止字幕时卸载当前模型", variable=self.local_exit_var,
            style="Card.TCheckbutton")
        self.local_exit_cb.grid(row=4, column=0, columnspan=3, sticky="w",
                                pady=(6, 0))
        self.tip(self.local_exit_cb,
                 "关掉字幕程序的同时释放显存；默认关闭（下次启动秒级就绪）")

        # 本地模型磁盘占用 + "随时可删"（安装脚本 --remove-local 一键清）
        self.disk_size = self._label(lm, "", style="Panel.DimSmall.TLabel")
        self.disk_size.grid(row=5, column=0, columnspan=3, sticky="w",
                            pady=(10, 0))
        self.btn_wizard = button(lm, "安装向导…", self._open_installer,
                                 kind="chip")
        self.btn_wizard.grid(row=5, column=3, sticky="e", pady=(10, 0))
        self.tip(self.btn_wizard,
                 "打开图形化安装向导：按需准备本地模型（离线识别 / 离线翻译）\n"
                 "也可以在向导最后一页一键删除本地模型（腾空间）")
        self.tip(self.disk_size,
                 "本地识别/声纹模型占用的磁盘（Ollama 的翻译模型在它自己的目录里，\n"
                 "用 ollama rm <模型> 删）\n"
                 "随时可删：bash packaging/install.sh --remove-local\n"
                 "删掉后下次要用会自动重新下载；只用云端 API 的话完全不受影响")
        self._refresh_local_disk()

        self._build_overlay_card(page)

        # 卡片 D：语音识别（SenseVoice）
        asr_f = self._card(page, "语音识别 · SenseVoice（本地）")
        asr_f.pack(fill="x", padx=6, pady=6)
        self.btn_dl_model = ttk.Button(asr_f, text="下载模型",
                                       command=self._download_model)
        self.btn_dl_model.grid(row=0, column=0, sticky="w")
        self.tip(self.btn_dl_model,
                 "下载语音识别模型（约 233MB），下载后启动无需联网\n"
                 "已有模型文件时无需再点（放到项目 models/sensevoice/ 即可）\n"
                 "国内网络慢：先设环境变量 HF_ENDPOINT=https://hf-mirror.com 再下载")
        self._label(asr_f, "断句词数").grid(row=1, column=0, sticky="w",
                                             pady=(8, 0))
        self.max_words_var = tk.StringVar()
        self.max_words = ttk.Spinbox(asr_f, from_=0, to=60, width=4,
                                     textvariable=self.max_words_var)
        self.max_words.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.tip(self.max_words,
                 "一句话超过该词数就自动拆成多条字幕先上屏（0 = 不限）\n"
                 "调小更跟手，调大句子更完整")
        self._label(asr_f, "断句间隔").grid(row=1, column=2, sticky="w",
                                             padx=(16, 0), pady=(8, 0))
        self.sil_var = tk.StringVar()
        self.sil_n = ttk.Spinbox(asr_f, from_=300, to=1200, increment=50,
                                 width=6, textvariable=self.sil_var)
        self.sil_n.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.tip(self.sil_n,
                 "停顿超过该时长就判定一句话结束（ms）\n"
                 "越小越跟手但句子更碎；越大越完整但延迟更高")

    def _on_provider_change(self, *_args, refill: bool = True) -> None:
        name = self.provider_name()
        meta = self._provider_meta(name)
        self.model_cb["values"] = list(meta.get("models") or [])
        self.model_cb.set(meta.get("model") or "")
        self.key_entry.delete(0, "end")
        if refill and self.keys.get(name):
            self.key_entry.insert(0, self.keys[name])
            self._last_processed = (name, self.keys[name])
        else:
            self._last_processed = None
        self._shown_provider = name
        self._refresh_key_badge()
        self._refresh_history()               # 历史列表跟随服务商切换
        self._sync_local_stats()              # 资源监控跟随本地/云端切换

    def _on_source_type_change(self) -> None:
        """切换 API/本地模型：过滤服务商列表并选中该类第一个。"""
        self._refresh_provider_list(self.src_type_var.get())
        if self.provider.get():
            self._on_provider_change()
            self._base_provider = self.provider_name()

    def _refresh_provider_list(self, src_type: str) -> None:
        """按模型来源类型（api/local）过滤服务商下拉。"""
        labels = [l for l, n in self._prov_by_label.items()
                  if self._prov_type.get(n, "api") == src_type]
        self.provider["values"] = labels
        if labels and self.provider.get() not in labels:
            self.provider.set(labels[0])

    def _select_type_for(self, name: str) -> None:
        """载入历史/切换到指定服务商时，同步类型选择。"""
        t = self._prov_type.get(name, "api")
        if self.src_type_var.get() != t:
            self.src_type_var.set(t)
            self._refresh_provider_list(t)

    def _on_user_select_provider(self, *_args) -> None:
        """手动切换服务商：输入框里的新 Key 保留并随切换重新归属；
        已存 Key 则换显新服务商的存量 Key。"""
        prev = self._shown_provider
        field_val = self.key_entry.get().strip()
        prev_stored = self.keys.get(prev) or ""
        # 该 Key 是刚输入、尚未保存的临时归属 -> 随手动切换移到新服务商名下
        provisional = (self._field_attribution == prev and field_val
                       and field_val == prev_stored)
        keep = bool(field_val) and (field_val != prev_stored or provisional)
        if provisional:
            self.keys.pop(prev, None)
        self._base_provider = self.provider_name()
        self._on_provider_change(refill=not keep)
        if keep:
            self.key_entry.insert(0, field_val)
            self._handle_key_input(False)
        self.after(800, self._fetch_models, True)   # 有存量 Key 的新服务商自动拉模型

    def _set_provider_dropdown(self, name: str, refill: bool = True) -> None:
        label = next((l for l, n in self._prov_by_label.items() if n == name), None)
        if label and self.provider.get() != label:
            self.provider.set(label)
            self._on_provider_change(refill=refill)

    def provider_name(self) -> str:
        """当前选择的后端键名（下拉显示的是 label）。"""
        label = self.provider.get()
        return self._prov_by_label.get(label, label)

    def _toggle_mode(self) -> None:
        if self.mode_var.get() == "professional":
            self.pro_frame.grid()               # 展开：专业领域 + 术语表设置
            self.domain.config(state="normal")
        else:
            self.domain.delete(0, "end")
            self.domain.config(state="disabled")
            self.pro_frame.grid_remove()        # 收起

    def _pick_glossary(self) -> None:
        p = filedialog.askopenfilename(
            parent=self, title="选择术语表（YAML）", initialdir=str(BASE),
            filetypes=[("YAML", "*.yaml *.yml"), ("所有文件", "*")])
        if not p:
            return
        f = Path(p)
        name = f.name if f.parent.resolve() == BASE.resolve() else str(f)
        self.cfg.setdefault("translate", {})["glossary_file"] = name
        self._refresh_glossary_hint()

    def _refresh_glossary_hint(self) -> None:
        g = BASE / self.cfg.get("translate", {}).get("glossary_file", "glossary.yaml")
        if g.is_file():
            self.glossary_hint.config(text=f"术语表 {g.name} ✓", fg=SIGNAL)
        else:
            self.glossary_hint.config(
                text="术语表缺失（cp glossary.example.yaml glossary.yaml）", fg=WARN)

    def _fetch_models(self, auto: bool = False) -> None:
        """从当前服务商的 OpenAI 兼容 /models 端点拉取真实模型列表。

        auto=True：启动/切换服务商时自动触发——失败静默保留静态清单，不吵用户。
        """
        name = self.provider_name()
        meta = self._provider_meta(name)
        base_url = meta.get("base_url", "")
        if not base_url:
            if not auto:
                self.set_status("该后端未配置 base_url", WARN)
            return
        env = meta.get("api_key_env", "")
        api_key = (os.environ.get(env) or self.keys.get(name) or "") if env else ""
        if env and not api_key:
            if not auto:
                self.set_status(f"请先在上方填写 {meta.get('label') or name} 的 API Key", WARN)
            return
        if self._fetch_inflight:
            return
        self._fetch_inflight = True
        # Anthropic 原生 /v1/models 需 x-api-key 头（Bearer 同时发送，其他家无影响）
        extra = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} \
            if (name == "claude" and api_key) else None
        label = meta.get("label") or name
        if not auto:
            self.btn_fetch.config(state="disabled")
        self.set_status(f"正在从 {label} API 获取模型列表 ...", INK)
        threading.Thread(target=self._fetch_models_bg, daemon=True,
                         args=(base_url, api_key, extra, name, label, auto)).start()

    def _fetch_models_bg(self, base_url: str, api_key: str, extra: dict | None,
                         name: str, label: str, auto: bool) -> None:
        try:
            ids = fetch_model_list(base_url, api_key, extra)
            err = None
        except RuntimeError as e:
            ids, err = [], str(e)

        def done():
            self._fetch_inflight = False
            if not auto:
                self.btn_fetch.config(state="normal")
            if err:
                if not auto:                  # 自动模式失败：静默保留静态清单
                    self.set_status(f"获取失败: {err}", BAD)
                return
            # 拉到的真实清单写回 cfg（随下次保存持久化到 config.yaml）
            self.cfg.setdefault("providers", {}).setdefault(name, {})["models"] = ids
            if self.provider_name() == name:          # 期间没切走才刷新下拉
                self.model_cb["values"] = ids
                if self.model_cb.get() not in ids:
                    self.model_cb.set(ids[0] if ids else "")
            self.set_status(f"已从 {label} API 获取 {len(ids)} 个模型 ✓"
                            f"{'（自动）' if auto else '（保存后持久化）'}",
                            DIM if auto else SIGNAL)
        self._ui_call(done)

    def _refresh_model_btn(self) -> None:
        """刷新下载按钮：SenseVoice 模型已就绪则显示 ✓。"""
        self.btn_dl_model.config(text="✓ 已在本地" if sensevoice_ready()
                                 else "下载模型")
        self._refresh_local_disk()

    def _open_installer(self) -> None:
        """打开图形化安装向导（独立进程；装/补装/删除本地模型）。"""
        import subprocess
        import sys as _sys
        script = BASE / "packaging" / "installer_gui.py"
        if not script.is_file():
            self.set_status(f"找不到安装向导：{script}", WARN)
            return
        try:
            subprocess.Popen([_sys.executable, str(script)], cwd=str(BASE),
                             start_new_session=True)
            self.set_status("已打开安装向导（独立窗口）", SIGNAL)
        except OSError as e:
            self.set_status(f"打开安装向导失败：{e}", BAD)

    def _refresh_local_disk(self) -> None:
        """本地模型磁盘占用（识别/声纹；Ollama 翻译模型在它自己的目录里）。

        文案里直接给出"随时可删"的入口，省得用户翻文档。
        """
        from livetrans.paths import MODELS_DIR
        try:
            total = sum(f.stat().st_size for f in MODELS_DIR.rglob("*")
                        if f.is_file())
        except OSError:
            total = 0
        text = (f"本地模型占用：{total / 1e6:.0f} MB（识别 + 声纹）"
                f" · 删除：bash packaging/install.sh --remove-local"
                if total else "本地模型：未下载（用到时自动下载，也可现在装）")
        w = getattr(self, "disk_size", None)
        if w is None:
            return
        try:
            w.config(text=text)
        except tk.TclError:
            pass

    def _download_model(self) -> None:
        """提前下载 SenseVoice 模型（后台线程，进度实时显示在状态栏/按钮上）。"""
        if self._dl_model_running:
            return
        self._dl_model_running = True
        self.btn_dl_model.config(state="disabled", text="下载中…")
        self.set_status("下载 SenseVoice 模型（约 233MB）...", INK)
        threading.Thread(target=self._download_model_bg, daemon=True,
                         name="dl-model").start()

    def _download_model_bg(self) -> None:
        err: Exception | None = None
        try:
            def on_progress(pct: int, done: str, total: str, rate: str) -> None:
                def upd() -> None:
                    self.btn_dl_model.config(text=f"下载中 {pct}%")
                    self.set_status(
                        f"下载模型 sensevoice: {pct}%（{done}/{total} · {rate}）",
                        INK)
                self._ui_call(upd)
            preload_model(progress_cb=on_progress)
        except Exception as e:  # noqa: BLE001
            err = e

        def done() -> None:
            self._dl_model_running = False
            self.btn_dl_model.config(state="normal")
            if err is not None:
                self.btn_dl_model.config(text="下载模型")
                self.set_status(f"模型下载失败: {err}", BAD)
            else:
                self.set_status("模型已保存到 models/sensevoice/ ✓"
                                "（启动时直接加载，无需联网）", SIGNAL)
            self._refresh_model_btn()
        try:
            self._ui_call(done)
        except tk.TclError:                   # 窗口已关闭
            pass

    def _speed_test(self) -> None:
        """一句话流式实测：TTFT / 总耗时 / 吐字速度。

        先保存当前设置与 Key（与「启动」同款行为）——否则 GUI 里刚填的
        Key 还在内存、后台线程读文件拿不到，会误报"请先 export"。
        """
        if self._speed_running:
            return
        name = self.provider_name()
        meta = self._provider_meta(name)
        env = meta.get("api_key_env", "")
        if env and not (os.environ.get(env) or self.keys.get(name)):
            self.set_status(f"请先在上方填写 {meta.get('label') or name} 的 API Key", WARN)
            return
        if not self._apply_and_save():            # 落盘 keys.env + config.yaml
            return
        model_sel = (self.model_cb.get() or "").strip()
        self._speed_running = True
        self._speed_t0 = time.monotonic()
        self.btn_speed.config(state="disabled", text="测速中… 0s")
        if self._prov_type.get(name) == "local":
            self.set_status("本地模型：首次测速需把模型载入内存（可能 1-2 分钟），"
                            "按钮上显示秒表，请稍候", WARN)
        self._speed_tick()
        self.set_status(f"测速 {meta.get('label') or name} / "
                        f"{model_sel or meta.get('model', '')} ...", INK)
        threading.Thread(target=self._speed_test_bg, args=(name, model_sel),
                         daemon=True, name="speed-test").start()

    def _speed_tick(self) -> None:
        """测速秒表：本地模型冷加载可能分钟级，按钮文字每秒刷新给进度感。"""
        if not self._speed_running:
            return
        self.btn_speed.config(
            text=f"测速中… {int(time.monotonic() - self._speed_t0)}s")
        self._speed_tick_id = self.after(1000, self._speed_tick)

    def _speed_test_bg(self, name: str, model_sel: str = "") -> None:
        res: dict = {"err": None}
        try:
            # LLMTranslator 需要 ProviderConfig 对象：用正规 load_config 加载
            # （self.cfg 是原始 yaml dict，直接传会 'dict' object has no attribute）
            app_cfg = load_config(str(CONFIG_PATH) if CONFIG_PATH.is_file()
                                  else None)
            app_cfg.translate.provider = name
            # 从刚落盘的 keys.env 注入（GUI 填写的 Key 此刻已在文件里）
            for env_name, key in key_env_overlay(app_cfg.providers).items():
                if not os.environ.get(env_name):
                    os.environ[env_name] = key
            prov = app_cfg.providers[name]
            model = model_sel.strip() or prov.model
            if is_local_base_url(prov.base_url):
                # 与「启动」同款预检：服务没起先拉起，避免测速莫名连接失败
                def _plog(m: str) -> None:
                    try:
                        self._ui_call(lambda mm=m: self.set_status(mm, INK))
                    except tk.TclError:
                        pass
                ensure_local_backend(prov.base_url, model, _plog)
            translator = LLMTranslator(app_cfg.translate, app_cfg.providers)
            translator.model = model
            sent = "The quick brown fox jumps over the lazy dog."
            ttft = None
            chars = 0
            t0 = time.monotonic()

            def on_delta(p: str) -> None:
                nonlocal ttft, chars
                if ttft is None:
                    ttft = (time.monotonic() - t0) * 1000
                chars = len(p)

            final = translator.translate_stream(sent, on_delta=on_delta)
            res.update(ttft=ttft or 0.0,
                       total=(time.monotonic() - t0) * 1000,
                       chars=len(final), model=model,
                       sample=final[:60])
        except Exception as e:  # noqa: BLE001
            res["err"] = f"{type(e).__name__}: {e}"

        def done() -> None:
            self._speed_running = False
            if getattr(self, "_speed_tick_id", None):
                try:
                    self.after_cancel(self._speed_tick_id)
                except tk.TclError:
                    pass
                self._speed_tick_id = None
            self.btn_speed.config(state="normal", text="测速")
            if res.get("err"):
                self.set_status(f"测速失败: {res['err']}", BAD)
                messagebox.showerror("翻译测速失败", res["err"])
                return
            ttft, total = res["ttft"], res["total"]
            self.set_status(f"测速 {res['model']} ✓ TTFT {ttft:.0f}ms · "
                            f"总 {total/1000:.1f}s", SIGNAL)
            self._show_speed_window(res, ttft, total)
        try:
            self._ui_call(done)
        except tk.TclError:                       # 窗口已关闭
            pass

    def _show_speed_window(self, res: dict, ttft: float, total: float) -> None:
        """测速结果：正常尺寸小窗，信息简洁（模型/首字/总耗时/吐字/样例）。"""
        try:
            old = getattr(self, "_speed_win", None)
            if old is not None and old.winfo_exists():
                old.destroy()
            win = tk.Toplevel(self)
            win.title("翻译测速")
            win.configure(bg=PANEL, padx=22, pady=18)
            win.resizable(False, False)
            win.transient(self)
            self._speed_win = win

            rows = [("模型", res["model"]),
                    ("首字时间", f"{ttft/1000:.2f} s"),
                    ("总耗时", f"{total/1000:.2f} s"),
                    ("吐字速度", f"{res['chars'] / (total/1000):.0f} 字/秒"),
                    ("译文样例", res["sample"])]
            for i, (k, v) in enumerate(rows):
                tk.Label(win, text=k, bg=PANEL, fg=DIM, font=(self.UI, 10),
                         anchor="w").grid(row=i, column=0, sticky="nw", pady=3)
                tk.Label(win, text=str(v), bg=PANEL,
                         fg=INK if k == "模型" else SIGNAL,
                         font=((self.MONO, 10) if k != "译文样例"
                               else (self.UI, 10)),
                         wraplength=300, justify="left", anchor="w")\
                    .grid(row=i, column=1, sticky="w", pady=3, padx=(14, 0))
            win.grid_columnconfigure(1, weight=1)
            button(win, "关闭", win.destroy)\
                .grid(row=len(rows), column=1, sticky="e", pady=(14, 0))
            win.bind("<Escape>", lambda _e: win.destroy())
            self.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry(f"{w}x{h}+"
                         f"{max(self.winfo_rootx() + (self.winfo_width() - w) // 2, 0)}+"
                         f"{max(self.winfo_rooty() + (self.winfo_height() - h) // 2, 0)}")
        except tk.TclError:                       # 窗口已关闭
            pass

    def _sync_local_stats(self) -> None:
        """本地服务商 -> 启动轮询 + 隐藏 API 专属行；云端 -> 反之。"""
        is_local = self._prov_type.get(self.provider_name()) == "local"
        # API Key / 历史两行仅 API 服务商需要（本地后端无 Key 概念）
        for w in self._key_row + self._hist_row:
            (w.grid_remove if is_local else w.grid)()
        if is_local and not self._stats_job:
            self.local_stats.config(text="资源监控启动中…")
            self._stats_job = self.after(300, self._local_stats_poll)
        elif not is_local and self._stats_job:
            self.after_cancel(self._stats_job)
            self._stats_job = None
            self.local_stats.config(text="")

    def _local_stats_poll(self) -> None:
        self._stats_job = None
        if self._prov_type.get(self.provider_name()) != "local":
            return
        threading.Thread(target=self._local_stats_bg, daemon=True,
                         name="local-stats").start()
        self._stats_job = self.after(2000, self._local_stats_poll)

    def _local_stats_bg(self) -> None:
        prev = self._cpu_prev
        text, cur = collect_local_stats(prev)
        self._cpu_prev = cur
        mem_text = self._local_mem_text()
        try:
            self._ui_call(lambda: self.local_stats.config(text=text))
            self._ui_call(lambda: self.mem_status.config(text=mem_text))
        except (tk.TclError, AttributeError):
            pass

    # ---- 本地模型显存（取消挂载 / 自动释放） ----

    @staticmethod
    def _local_mem_text() -> str:
        """显存卡片的状态行：常驻了哪些模型、占多少、何时到期。"""
        from livetrans.sysmon import ollama_loaded
        loaded = ollama_loaded()
        if not loaded:
            return "当前没有模型常驻显存（未占用显卡）"
        parts = []
        for m in loaded:
            name = m.get("name", "?")
            gb = m.get("size_vram", 0) / 1024 ** 3
            exp = str(m.get("expires_at") or "")[11:16]
            parts.append(f"{name} {gb:.1f}GB"
                         + (f"（{exp} 前空闲即自动回收）" if exp else ""))
        total = sum(m.get("size_vram", 0) for m in loaded) / 1024 ** 3
        return f"常驻 {len(loaded)} 个模型 · 共 {total:.1f}GB：" + "；".join(parts)

    def _unload_models(self) -> None:
        """手动"取消挂载"：把 Ollama 常驻模型全部卸载，立刻回收显存。"""
        self.btn_unload.config(state="disabled", text="释放中…")
        self.set_status("正在卸载本地模型（释放显存）...", WARN)

        def work() -> None:
            from livetrans.sysmon import ollama_loaded, ollama_unload
            loaded = ollama_loaded()
            names = [m.get("name", "") for m in loaded if m.get("name")]
            before = sum(m.get("size_vram", 0) for m in loaded) / 1024 ** 3
            res = ollama_unload(names) if names else {}
            ok = [n for n, good in res.items() if good]

            def done() -> None:
                try:
                    self.btn_unload.config(state="normal",
                                           text="释放显存（取消挂载）")
                except tk.TclError:
                    return
                if not names:
                    self.set_status("当前没有常驻显存的模型（无需卸载）", DIM)
                elif ok:
                    self.set_status(f"已卸载 {', '.join(ok)}，"
                                    f"释放显存约 {before:.1f}GB ✓", SIGNAL)
                else:
                    self.set_status("卸载失败：模型服务不可达？", BAD)
                self.mem_status.config(text=self._local_mem_text())

            self._ui_call(done)

        threading.Thread(target=work, daemon=True, name="unload").start()
