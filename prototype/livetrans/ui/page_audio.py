"""「音频源」页：麦克风与系统声音的开关与设备选择。"""

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
from livetrans.ui.widgets import StatusToast, ToolTip


class AudioPageMixin:

    def _page_audio(self) -> None:
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="音频源")

        card = self._card(page, "双路捕获（可同时开启）")
        card.pack(fill="x", padx=12, pady=(8, 0))

        self.mic_cb = ttk.Checkbutton(card, text="外部音频 · 麦克风",
                                      style="Ext.TCheckbutton",
                                      variable=self._init_var("mic_var", True))
        self.mic_cb.grid(row=0, column=0, sticky="w")
        self._label(card, "设备").grid(row=0, column=1, sticky="e", padx=(16, 4))
        self.mic_dev = ttk.Combobox(card, width=32, state="readonly")
        self.mic_dev.grid(row=0, column=2, sticky="w")

        self.mon_cb = ttk.Checkbutton(card, text="内部音频 · 系统声音",
                                      style="Int.TCheckbutton",
                                      variable=self._init_var("mon_var", True))
        self.mon_cb.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._label(card, "来源").grid(row=1, column=1, sticky="e",
                                        padx=(16, 4), pady=(8, 0))
        self.mon_dev = ttk.Combobox(card, width=32, state="readonly")
        self.mon_dev.grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.tip(self.mic_cb, "采集麦克风（自己说话）：字幕窗里以绿色标识")
        self.tip(self.mon_cb,
                 "采集电脑正在播放的声音（看视频/开会的场景），字幕里以蓝色标识")
        self.tip(self.mic_dev, "选择麦克风设备（auto = 系统默认输入）")
        self.tip(self.mon_dev, "选择要采集的系统声音来源")

        self.tip(card, "两路可同时开启：字幕窗会左右分栏显示（绿=麦克风，蓝=系统声音）")

        # 声纹角色标注（多人会议区分说话人：CAM++ 说话人向量 + 在线聚类）
        spk = self._card(page, "声纹角色标注（多人会议）")
        spk.pack(fill="x", padx=12, pady=(10, 0))
        spk_cfg = (self.cfg.get("speaker") or {})
        self.spk_on = ttk.Checkbutton(
            spk, text="启用声纹：按说话人给字幕标注 S1/S2… 并配色",
            variable=self._init_var("spk_var", bool(spk_cfg.get("enabled", False))))
        self.spk_on.grid(row=0, column=0, columnspan=3, sticky="w")
        self.tip(self.spk_on,
                 "对每一句（VAD 切出的完整语音段）抽说话人向量并在线聚类，\n"
                 "换人时在字幕上标 S1/S2…；同一人连续说话不重复标注\n"
                 "（只用于区分与配色，不改翻译内容、不上传）")

        # 判定阈值：主题滑块 + 数值（原来是经典 tk.Scale，样式与其它控件不搭）
        self._label(spk, "判定阈值").grid(row=1, column=0, sticky="w",
                                          pady=(10, 0))
        self.spk_th = ttk.Scale(spk, from_=0.35, to=0.80, orient="horizontal",
                                length=170, command=lambda _v: self._spk_hint())
        self.spk_th.set(float(spk_cfg.get("threshold", 0.55)))
        self.spk_th.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        self.spk_th_val = ttk.Label(spk, text="", style="Mono.TLabel", width=5)
        self.spk_th_val.grid(row=1, column=2, sticky="w", pady=(10, 0))
        # 最多人数：原来 width=4 的 Spinbox 太小 → 与"设备/来源"同款下拉，尺寸一致
        self._label(spk, "最多人数").grid(row=1, column=3, sticky="e",
                                          padx=(24, 6), pady=(10, 0))
        self.spk_max = ttk.Combobox(spk, width=4, state="readonly",
                                    values=[str(i) for i in range(2, 9)])
        self.spk_max.set(str(int(spk_cfg.get("max_speakers", 6))))
        self.spk_max.grid(row=1, column=4, sticky="w", pady=(10, 0))
        self.tip(self.spk_th_val, "当前判定阈值（0.35~0.80，步进 0.05）")
        self.tip(self.spk_th,
                 "余弦相似度阈值：越高越容易判成「新说话人」\n"
                 "0.55 为官方示例常用值（实测同人 0.72 / 异人 0.28，余量充足）\n"
                 "把同一个人拆成两个 → 调低；不同人被合并 → 调高")
        self.tip(self.spk_max, "最多区分几个人，超出后归入最相似者")
        self.spk_status = self._label(spk, "")
        self.spk_status.grid(row=2, column=0, columnspan=5, sticky="w",
                             pady=(10, 0))
        self.tip(self.spk_status,
                 "声纹模型：models/speaker/*.onnx（CAM++ 中英双语，28MB）\n"
                 "改动后需「保存并启动」重启生效")

    def _init_var(self, name: str, value) -> tk.Variable:
        v = tk.BooleanVar(value=value)
        setattr(self, name, v)
        return v

    def _spk_hint(self) -> None:
        """声纹卡片状态行：依赖/模型是否齐备 + 当前阈值（重启生效）。

        缺失项直接用 deps 的检查结果，界面提示与"运行依赖自检"保持一处口径。
        """
        from livetrans.deps import missing_speaker_deps
        from livetrans.speaker import SpeakerConfig, speaker_model_path
        th = float(self.spk_th.get())
        missing = missing_speaker_deps()
        model = speaker_model_path(SpeakerConfig(threshold=th))
        if missing:
            text, col = f"⚠ 缺少 {'、'.join(missing)}", WARN
        elif model is None:
            text, col = "首次启用会自动下载模型（约 28MB，中英声纹）", DIM
        elif self.spk_var.get():
            text, col = f"就绪：{model.name} · 阈值 {th:.2f}（重启后生效）", SIGNAL
        else:
            text, col = f"已关闭（模型可用：{model.name}）", DIM
        # 注意：ttk.Scale.set() 会**立刻**触发 command，此时下面的两个标签
        # 可能还没创建（本函数在创建顺序上早于它们）→ 逐个取、缺了就跳过
        for attr, kw in (("spk_status", {"text": text, "foreground": col}),
                         ("spk_th_val", {"text": f"{th:.2f}"})):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                w.config(**kw)
            except tk.TclError:
                pass
