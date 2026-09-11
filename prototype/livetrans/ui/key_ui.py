"""服务商 / API Key 的界面逻辑（输入识别、归属纠错、历史记录）。"""

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
from livetrans.ui.widgets import (StatusToast, ToolTip,
                                  button)


class KeyUIMixin:

    def _provider_meta(self, name: str) -> dict:
        """config.yaml 与 example 合并后的 provider 元数据（example 补缺）。"""
        meta = dict(example_providers().get(name) or {})
        meta.update(self.cfg.get("providers", {}).get(name) or {})
        return meta

    def _key_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """(provider名->ENV名, ENV名->provider名)；无 env 名的后端（如 ollama）跳过。"""
        prov2env = {}
        for name in set(self.cfg.get("providers") or {}) | set(example_providers()):
            env = self._provider_meta(name).get("api_key_env", "")
            if env:
                prov2env[name] = env
        return prov2env, {v: k for k, v in prov2env.items()}

    def _load_provider_keys(self) -> dict[str, str]:
        """keys.env（ENV 名键）/ 旧 keys.yaml（provider 名键）-> {provider名: key}。

        并按 key 签名纠正错误归属：如 deepseek 名下存了智谱两段式 key，
        自动移到 glm 名下（旧版 launcher 的设计缺口造成的脏数据）。
        """
        prov2env, env2prov = self._key_maps()
        stored = load_any()
        keys: dict[str, str] = {}
        for k, v in stored.items():
            prov = env2prov.get(k) or (k if k in prov2env else None)
            if prov:
                keys[prov] = v
        for prov in list(keys):
            detected = detect_provider(keys[prov])
            if detected and detected != prov and detected in prov2env:
                keys[detected] = keys.pop(prov)
                moved_to = self._provider_meta(detected).get("label") or detected
                self._key_notice = f"已把 Key 归位到 {moved_to}（原存于 {prov} 名下）"
        return keys

    def _save_keys_now(self) -> None:
        """归属纠错 -> provider名转ENV名 -> 落盘 keys.env。"""
        prov2env, _ = self._key_maps()
        for prov in list(self.keys):
            detected = detect_provider(self.keys[prov])
            if detected and detected != prov and detected in prov2env:
                self.keys[detected] = self.keys.pop(prov)
        save_keys({prov2env[p]: k for p, k in self.keys.items() if p in prov2env})
        self._saved_keys = dict(self.keys)
        self._field_attribution = None
        self._record_history()
        self._refresh_history()

    def _on_key_type(self, _e=None) -> None:
        """键入防抖：停顿 300ms 统一处理，避免逐字符处理/粘贴双触发。"""
        if self._type_timer:
            try:
                self.after_cancel(self._type_timer)
            except tk.TclError:
                pass
        self._type_timer = self.after(300, lambda: self._handle_key_input(False))

    def _refresh_key_badge(self) -> None:
        name = self.provider_name()
        env = self._provider_meta(name).get("api_key_env", "")
        if not env:
            text, color = "", DIM          # 本地后端行内不写说明（提示里已说明）
        elif os.environ.get(env):
            text, color = f"✓ 环境变量 {env} 已设置", SIGNAL
        else:
            key = self.keys.get(name)
            detected = detect_provider(key) if key else None
            if key and detected and detected != name:
                other = self._provider_meta(detected).get("label") or detected
                text, color = f"⚠ Key 疑似 {other}，与服务商不符", WARN
            elif key and key != self._saved_keys.get(name):
                text, color = "● 已输入，保存后生效", WARN
            elif key:
                text, color = "✓ Key 已保存（keys.env）", SIGNAL
            elif self.key_entry.get().strip():
                text, color = "● 已输入，保存后生效", WARN
            else:
                text, color = "未配置 Key", BAD
        self.key_badge.config(text=text, foreground=color)

    def _handle_key_input(self, from_paste: bool) -> None:
        """Key 输入统一入口（粘贴/键入/失焦/防抖共用）。

        - 可识别签名（智谱两段式 / sk-ant- / AIza / xai-）-> 自动切换服务商并联动模型；
        - sk- 前缀多家共用（DeepSeek/Kimi/OpenAI/千问）无法自动识别：
          粘贴时弹窗让用户选择归属；键入时按当前下拉归属并提示；
        - 清空输入框：不删除已保存的 Key，下拉回退到基准服务商（撤销自动切换的临时态）。
        """
        if self._dialog_open:
            return
        val = self.key_entry.get().strip()
        if val.lower().startswith("bearer "):      # 容错：连 Bearer 头一起粘贴
            val = val[7:].strip()
            self.key_entry.delete(0, "end")
            self.key_entry.insert(0, val)
        name = self.provider_name()
        if (name, val) == self._last_processed:
            return
        self._last_processed = (name, val)
        if not val:
            # 撤销未保存的本次输入：临时归属的 key 一并移除（已保存的 key 不动）
            if self._field_attribution == name and name in self.keys:
                self.keys.pop(name, None)
            self._field_attribution = None
            if name != self._base_provider:       # 下拉回退基准服务商
                self._set_provider_dropdown(self._base_provider, refill=False)
                self._last_processed = (self._base_provider, "")
            self._refresh_key_badge()
            return
        detected = detect_provider(val)
        if detected and detected != name and detected in self._prov_by_label.values():
            self._auto_switch_provider(detected, val)
            return
        if (detected is None and from_paste and val.startswith("sk-")
                and len(val) >= 16):
            target = self._ask_ambiguous_provider()
            if target is None:                    # 取消 = 放弃本次输入
                self.key_entry.delete(0, "end")
                self._last_processed = None
                self._refresh_key_badge()
                return
            if target != name:
                self._auto_switch_provider(target, val, via="已按选择")
                return
            # 弹窗选的就是当前服务商 -> 落到下方按当前归属保存
        elif detected is None and val.startswith("sk-"):
            self.set_status("sk- 前缀多家共用（DeepSeek / Kimi / OpenAI / 通义千问），"
                            "已按当前服务商归属；如不符请先切换服务商", DIM)
        self.keys[name] = val
        self._field_attribution = name
        self._refresh_key_badge()

    def _ask_ambiguous_provider(self) -> str | None:
        """sk- 前缀多家共用：弹模态窗选择 Key 归属；取消返回 None。"""
        prov2env, _ = self._key_maps()
        cands = [n for n in self._prov_by_label.values()
                 if n not in ("claude", "gemini", "grok", "glm") and prov2env.get(n)]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        win = tk.Toplevel(self)
        win.title("选择 Key 归属")
        win.configure(bg=PANEL)
        win.resizable(False, False)
        win.transient(self)
        result: dict = {"choice": None}

        def pick(n: str) -> None:
            result["choice"] = n
            win.destroy()

        tk.Label(win, bg=PANEL, fg=INK, font=(self.UI, 10), justify="left",
                 text="该 Key 以 sk- 开头，以下服务商均使用这一前缀，无法自动识别。\n"
                      "请选择它的归属："
                 ).pack(padx=18, pady=(14, 10))
        grid = tk.Frame(win, bg=PANEL)
        grid.pack(padx=18)
        default = self.provider_name() if self.provider_name() in cands else cands[0]
        for i, n in enumerate(cands):
            text = self._provider_meta(n).get("label") or n
            b = button(grid, text, command=lambda n=n: pick(n))
            b.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="ew")
            if n == default:
                b.focus_set()
        button(win, "取消（放弃本次输入）", win.destroy,
               kind="ghost").pack(pady=(12, 14))
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.bind("<Escape>", lambda _e: win.destroy())

        self._dialog_open = True                  # 防重入（防抖/失焦回调不再进来）
        if self._type_timer:
            try:
                self.after_cancel(self._type_timer)
            except tk.TclError:
                pass
            self._type_timer = None
        win.grab_set()
        self.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{max(self.winfo_rootx() + (self.winfo_width() - w) // 2, 0)}"
                     f"+{max(self.winfo_rooty() + (self.winfo_height() - h) // 2, 0)}")
        win.wait_window()
        self._dialog_open = False
        return result["choice"]

    def _auto_switch_provider(self, target: str, key_val: str,
                              via: str = "检测到") -> None:
        """Key 归属确定 -> 切换服务商下拉 -> 模型列表联动 -> Key 归位。"""
        label = next((l for l, n in self._prov_by_label.items() if n == target), None)
        if not label:
            return
        self.provider.set(label)
        self._on_provider_change(refill=False)    # 模型列表联动 + key_entry 重置
        self.key_entry.insert(0, key_val)
        self.keys[target] = key_val
        self._field_attribution = target
        self._last_processed = (target, key_val)
        self._refresh_key_badge()
        self.set_status(f"{via} {label} 的 Key，已切换服务商并联动模型列表", SIGNAL)
        self.after(800, self._fetch_models, True)  # 有 Key 即自动获取模型列表

    def _toggle_key_show(self) -> None:
        self._key_shown = not self._key_shown
        self.key_entry.config(show="" if self._key_shown else "•")
        self.btn_show.config(text="隐藏" if self._key_shown else "显示")

    def _clear_key(self) -> None:
        """清除按钮 = 显式删除当前服务商的 Key（清空输入框不会删除）。"""
        name = self.provider_name()
        self.key_entry.delete(0, "end")
        self.keys.pop(name, None)
        try:
            self._save_keys_now()
        except OSError as e:
            messagebox.showerror("清除失败", str(e))
        self._field_attribution = None
        if name != self._base_provider:           # 下拉回退基准服务商
            self._set_provider_dropdown(self._base_provider, refill=False)
        else:
            self._last_processed = (name, "")
        self._refresh_key_badge()

    def _refresh_history(self) -> None:
        """历史下拉只显示当前服务商的记录（本地模型/各 API 互不混列）。"""
        items = load_history(self.provider_name())
        self._hist_entries = items
        self.hist_cb["values"] = [
            f"{mask_key(h.get('key', ''))} · {h.get('time', '')}" for h in items]
        if items:
            self.hist_cb.current(0)

    def _load_history_entry(self) -> None:
        """载入所选历史：切到对应服务商并回填 Key（用归属识别的切换通道）。"""
        idx = self.hist_cb.current()
        items = getattr(self, "_hist_entries", [])
        if idx < 0 or idx >= len(items):
            self.set_status("请先在下拉框选择一条历史记录", WARN)
            return
        h = items[idx]
        prov, key = h.get("provider", ""), h.get("key", "")
        if prov not in self._prov_by_label.values():
            self.set_status(f"历史记录的服务商 {prov} 不在当前配置中", BAD)
            return
        self._select_type_for(prov)               # 同步类型选择（api/local）
        if self.provider_name() == prov:
            self.key_entry.delete(0, "end")
            self.key_entry.insert(0, key)
            self.keys[prov] = key
            self._field_attribution = prov
            self._last_processed = (prov, key)
            self._refresh_key_badge()
        else:
            self._auto_switch_provider(prov, key, via="已载入历史")
        self.set_status(f"已载入历史 API（{prov}）✓ 保存后生效", SIGNAL)

    def _record_history(self) -> None:
        """把当前有效的 provider+key 记入历史（保存时调用）。"""
        for prov, key in self.keys.items():
            if prov in self._prov_by_label.values():
                add_history(prov, key)
