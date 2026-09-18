"""LiveTrans M1 入口：多路音频 -> VAD+ASR -> LLM 翻译 -> 字幕悬浮窗。

用法：
  python3 -m livetrans.main --list-devices          # 查看音频设备
  python3 -m livetrans.main                          # 麦克风 + 系统音频双路
  python3 -m livetrans.main --no-mic                 # 仅系统音频（内部）
  python3 -m livetrans.main --provider glm --mode professional --domain 医学

启动顺序设计：字幕窗先弹出（置顶），SenseVoice 模型加载 / 音频源启动 / 翻译后端
连接全部在后台线程完成，进度与错误实时显示在字幕窗状态栏，并打印到 stdout
（由控制台重定向到 logs/run-*.log，形成运行日志）。
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

from dataclasses import dataclass, field

from .asr import ASRWorker, SegmentEvent, SenseVoiceASR
from . import configstore
from .capture import (AudioCapture, ParecCapture, SourceInfo,
                      default_audio_device, default_monitor_source,
                      find_monitor_device, friendly_source_name, list_devices)
from .config import AppConfig, DialogConfig, TranslateConfig, load_config
from .paths import CONFIG_PATH, ensure_data_files
from .langs import (LANG_CODES, dialog_audio_short, dialog_direction,
                    dialog_directions, dialog_role_kinds, dialog_role_label,
                    migrate_dialog, roles_for_kind)
from .speaker import build_tracker
from .subtitle import STYLE_DEFAULTS, DisplayItem, SubtitleWindow
from .translate import (LLMTranslator, ensure_local_backend,  # noqa: F401
                        is_local_base_url)


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _load_subtitle_style(config_path: str | None) -> dict:
    """从 config.yaml 读字幕样式（缺失项用默认值补齐）。"""
    style = dict(STYLE_DEFAULTS)
    try:
        p = Path(config_path) if config_path else \
            CONFIG_PATH
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            style.update(raw.get("subtitle") or {})
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"读取字幕样式失败（用默认）: {e}")
    return style


def _persist_subtitle_style(config_path: str | None, style: dict) -> None:
    """字幕样式写回 config.yaml 的 subtitle: 段（统一走配置写入层）。"""
    try:
        configstore.patch_section(config_path or CONFIG_PATH, "subtitle", style)
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"保存字幕样式失败: {e}")


def _load_dialog(config_arg: str | None) -> dict:
    """从 config.yaml 读对话模式设置（缺失项用默认值补齐）。"""
    dialog = {k: v for k, v in vars(DialogConfig()).items()}
    try:
        p = Path(config_arg) if config_arg else \
            CONFIG_PATH
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for k, v in migrate_dialog(raw.get("dialog")).items():
                if k in dialog and v is not None:
                    dialog[k] = v
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"读取对话模式设置失败（用默认）: {e}")
    return dialog


def _persist_dialog(config_path: str | None, dialog: dict) -> None:
    """对话模式设置写回 config.yaml 的 dialog: 段（统一走配置写入层）。"""
    try:
        configstore.patch_section(config_path or CONFIG_PATH, "dialog", dialog)
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"保存对话模式设置失败: {e}")


def _direction_translator(tcfg: TranslateConfig, providers: dict,
                          src_lang: str, dst_lang: str) -> LLMTranslator:
    """按指定方向复制一份翻译配置（独立上下文/术语表，共用后端与凭证）。"""
    from dataclasses import replace
    target, auto_zh_en = dialog_direction(dst_lang)
    return LLMTranslator(replace(
        tcfg,
        source_lang=None if src_lang in ("", "自动", "自动识别") else src_lang,
        target_lang=target,
        auto_zh_to_en=auto_zh_en,     # 目标选「自动（中↔英）」时为双向自适应
    ), providers)


def _same_direction(tcfg: TranslateConfig, src_lang: str, dst_lang: str) -> bool:
    """该侧方向是否与「翻译」页的单向设置一致（一致就复用主翻译器）。

    「自动」源视为通配：源语言栏是 auto/语言代码/未设置时都算匹配。
    """
    src = None if src_lang in ("", "自动", "自动识别") else src_lang
    cur_src = getattr(tcfg, "source_lang", None)
    if src is None:
        ok_src = cur_src in (None, "auto", *LANG_CODES.values())
    else:
        ok_src = cur_src == src
    target, auto_zh_en = dialog_direction(dst_lang)
    return (ok_src and tcfg.target_lang == target
            and bool(getattr(tcfg, "auto_zh_to_en", False)) == auto_zh_en)


def _build_dialogue_translators(tcfg: TranslateConfig, providers: dict,
                                dialog: dict, primary: LLMTranslator,
                                log) -> dict[str, LLMTranslator]:
    """对话模式：按**角色**构建 role -> 翻译器（方向跟角色走，不跟音频路走）。

    - 某角色方向与「翻译」页设置相同 -> 直接复用主翻译器（不重复建连接）
    - bidirectional=False -> 返回空映射，两路都走「翻译」页的单向设置
    """
    if not dialog.get("bidirectional", True):
        log("对话模式：单向（两路都按「翻译」页设置翻译）")
        return {}
    role_kinds = dialog_role_kinds(dialog)
    out: dict[str, LLMTranslator] = {}
    for role, (src, dst) in dialog_directions(dialog).items():
        who = f"{dialog_role_label(role)}（{dialog_audio_short(role_kinds[role])}）"
        if _same_direction(tcfg, src, dst):
            out[role] = primary
            log(f"对话模式 {who}：{src} → {dst}（复用主翻译器）")
            continue
        out[role] = _direction_translator(tcfg, providers, src, dst)
        log(f"对话模式 {who}：{src} → {dst}")
    return out


def _unload_local_models(provider, keep: str, log, reason: str = "") -> float:
    """卸载本地模型释放显存；keep 为要保留的模型名（空串 = 全都卸）。

    返回本次回收的显存估算（GB）。Ollama 换模型时**不会**自动卸载旧的，
    所以"用哪个留哪个、其余清掉"要靠这里显式调用。
    """
    try:
        from .sysmon import ollama_loaded, ollama_unload
        loaded = ollama_loaded(provider.base_url)
        names = [m.get("name", "") for m in loaded
                 if m.get("name") and m.get("name") != keep]
        if not names:
            return 0.0
        freed = sum(m.get("size_vram", 0) for m in loaded
                    if m.get("name") in names) / 1024 ** 3
        res = ollama_unload(names, provider.base_url)
        ok = [n for n, good in res.items() if good]
        log(f"释放显存{('（' + reason + '）') if reason else ''}: "
            f"已卸载 {', '.join(ok) or '（无）'}，回收约 {freed:.1f}GB")
        return freed
    except Exception as e:  # noqa: BLE001 - 卸载失败不影响翻译
        log(f"⚠ 卸载本地模型失败: {type(e).__name__}: {e}")
        return 0.0


def _make_fallback_builder(cfg: AppConfig, log):
    """云端翻译失败时的本地兜底：返回 (src, dst, auto) -> LLMTranslator 的构造器。

    - 只在「翻译后端是云端」且存在 local 类型服务商、开关打开时启用；
    - 按需建（第一次真的失败才建连接），方向沿用原翻译器。
    """
    if not getattr(cfg.translate, "fallback_local", True):
        return None
    cur = cfg.providers.get(cfg.translate.provider)
    if cur is None or getattr(cur, "type", "api") == "local":
        return None                        # 本来就用本地：无需兜底
    local_name = next((n for n, p in cfg.providers.items()
                       if getattr(p, "type", "api") == "local"), None)
    if local_name is None:
        return None
    from dataclasses import replace

    def build(src, dst, auto) -> LLMTranslator:
        tcfg = replace(cfg.translate, provider=local_name,
                       source_lang=None if src in (None, "", "自动", "自动识别")
                       else src,
                       target_lang=dst or "中文", auto_zh_to_en=bool(auto))
        tr = LLMTranslator(tcfg, cfg.providers)
        log(f"本地兜底翻译器就绪: {local_name} / {tr.model}")
        return tr

    return build


class DialogRouter:
    """对话模式的实时路由：音频路 -> 需要它的角色 -> 该角色的翻译器 / 暂停态。

    来源可以随时换（面板里改），路由每句话都现查一次，所以切换来源**不会**
    重建翻译器、也不会打断该角色的上下文。
    """

    def __init__(self, window, translators: dict[str, LLMTranslator],
                 primary: LLMTranslator):
        self.window = window
        self.primary = primary
        self._lock = threading.Lock()
        self._tr: dict[str, LLMTranslator] = dict(translators or {})

    def set_translators(self, mapping: dict[str, LLMTranslator]) -> None:
        with self._lock:
            old = self._tr
            self._tr = dict(mapping or {})
            keep = {id(t) for t in self._tr.values()} | {id(self.primary)}
        for t in old.values():
            if id(t) not in keep:
                try:
                    t.client.close()
                except Exception:  # noqa: BLE001
                    pass

    def roles_for(self, kind: str) -> list[str]:
        """这一路音频当前有几个角色在用（0/1/2 个）。"""
        return roles_for_kind(getattr(self.window, "dialog", {}), kind)

    def translator_for(self, role: str) -> LLMTranslator:
        with self._lock:
            return self._tr.get(role) or self.primary

    def is_paused(self, role: str) -> bool:
        paused = getattr(self.window, "paused_roles", None)
        return bool(paused and role in paused)


def _join_parts(parts: list[str]) -> str:
    """段落内拼接：以 CJK 为主直接相连，西文以空格相连。"""
    if not parts:
        return ""
    sample = next((p for p in parts if p and p != "…"), "")
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    sep = "" if sample and cjk * 2 >= len(sample) else " "
    return sep.join(p for p in parts if p)


PARTIAL_WORDS = 5       # 边讲边译：partial 新增词数达到该值就翻译一次增量

# 分段规则（2026-09-18 用户定稿）：**只看时间间隔**——
#   ① 定稿句之间的真实停顿 > paragraph_gap_ms（默认 3.5s）→ 分段；
#   ② 说话中 partial 静默超同一阈值 → 预分段（不等定稿）。
# 不设句数上限：原 8 句硬上限会在连续说话中途强制切段（"还没讲完就分段"），
# 违背该规则；代价是长独白的修订输入变大——修订频率仍由 revise_depth 控制，
# 且字幕句短，输入规模实际可接受。（与 Windows 版此处有意不一致，见开发日志）


def _text_weight(s: str) -> int:
    """近似"词数"：CJK 按字计，西文按空白词计（partial 翻译节流用）。"""
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + len([w for w in s.split() if w])


@dataclass
class _Para:
    """上下文连续段落模式：一段 = 一个显示条目，句句追加、整段持续修订。

    - srcs：段落内每句的原始识别文本（按时间序）；
    - dsts：每句的译文（流式 partial / 最终值），未修订时的显示内容；
    - revised/revised_cover：最近一次整段修订的结果与当时覆盖的句数——
      修订之后的句子仍按句显示，下一次修订再把它们并进去。
    为什么整段重写而不是只重写尾部：尾部区间的切片无法跨"上次修订"边界
    （合并后的文本拆不回句子），整段重写语义简单且上下文最完整；
    段落长度只由停顿阈值限定（句数不设上限，见模块头分段规则）。
    """
    para_id: str
    ts: float
    kind: str
    label: str
    app: str
    role: str
    srcs: list[str] = field(default_factory=list)
    dsts: list[str | None] = field(default_factory=list)
    revised: str | None = None
    revised_cover: int = 0
    pending_since_revise: int = 0        # 上次修订以来新翻完的句数
    revising: bool = False
    revise_again: bool = False
    # 边讲边译：VAD 定稿前的 partial 识别文本，作为"临时尾句"挂在段落尾部
    # （live_src=当前说话的完整 partial 原文；live_dst=其中已翻译部分的临时译文；
    #   live_upto=已送去翻译的字符位置；live_gen=代次——定稿清空时 +1，
    #   让在途的 partial 翻译结果作废，不把旧内容写回新状态）
    live_src: str = ""
    live_dst: str = ""
    live_upto: int = 0
    live_busy: bool = False
    live_gen: int = 0
    last_activity: float = 0.0           # monotonic；长停顿后 partial 触发预分段


def _para_text(p: _Para) -> str:
    """段落的当前显示文本（未完成句用 … 占位；临时尾句附在最后）。"""
    if p.revised is None:
        parts = [(t or "…") for t in p.dsts]
    else:
        parts = [p.revised]
        parts += [(p.dsts[i] or "…")
                  for i in range(p.revised_cover, len(p.dsts))]
    if p.live_dst:
        parts.append(p.live_dst)
    return _join_parts(parts)


def _para_src(p: _Para) -> str:
    """段落的当前显示原文（含正在说话的 partial 文本）。"""
    return _join_parts([*p.srcs, p.live_src])


class TranslatorWorker(threading.Thread):
    """消费 SegmentEvent：原文立即上屏 -> 并行翻译 -> **先完成先上屏**（乱序）。

    - 原文不等翻译（体感延迟 ≈ 断句+转写）；
    - 译文**乱序上屏**：每句独立显示，先翻完的先补全——长句不再阻塞后句，
      消除"持续翻译中"的队头等待（流式 partial 也按句独立推进，互不干扰）；
    - 仅"上下文记忆 + 会话 JSONL"仍按提交顺序（seq）落盘：显示乱序不影响
      翻译质量的上下文顺序，日志顺序与对话一致。
    """

    def __init__(self, seg_q, window: SubtitleWindow, translator: LLMTranslator,
                 log_path: Path, stop_event: threading.Event,
                 translators: dict[str, LLMTranslator] | None = None,
                 router: "DialogRouter | None" = None,
                 fallback_builder=None):
        super().__init__(daemon=True, name="translator")
        self.seg_q = seg_q
        self.window = window
        self.translator = translator
        self.log_path = log_path
        self.stop = stop_event
        # 对话模式：role(角色) -> 该角色专用翻译器（方向按角色，来源可随时换）
        self.router = router
        # 兼容旧路径：kind(音频路) -> 翻译器（无 router 时使用）
        self._translators: dict[str, LLMTranslator] = dict(translators or {})
        self._tr_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self.last_active = time.monotonic()   # 最近一次翻译活动（空闲卸载用）
        # 弱网降级：云端连续失败 -> 切本地兜底；恢复后自动回切
        self._fallback_builder = fallback_builder
        self._fb_cache: dict[tuple, LLMTranslator] = {}
        self._fail_streak = 0
        self._degraded = False
        self._probe_thread: threading.Thread | None = None
        self.FAIL_THRESHOLD = 2            # 连续失败几句才切（避免偶发抖动）
        self.PROBE_INTERVAL = 60.0         # 降级后多久探一次云端是否恢复
        self._seq = 0                          # 提交顺序号（仅用于上下文/日志排序）
        self._next_ctx = 0                     # 下一个写入上下文/日志的顺序号
        self._ctx_pending: dict[int, dict] = {}  # seq -> 已完成的上下文槽位
        self._gate_lock = threading.Lock()
        # 上下文连续段落模式：每个 (音频路, 角色) 一个开放段落
        self._paras: dict[tuple, _Para] = {}
        self._para_anchor: dict[tuple, str] = {}   # key -> 上一段的定稿译文（衔接上下文）
        self._pool: ThreadPoolExecutor | None = None
        # 边讲边译：partial 识别流（ASRWorker on_partial 直灌，UI 线程外消费）
        self.partial_q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def pick(self, kind: str) -> LLMTranslator:
        """该路音频用哪个翻译器（兼容旧路径；对话模式走 router）。"""
        with self._tr_lock:
            return self._translators.get(kind) or self.translator

    def _roles_of(self, kind: str) -> list[str | None]:
        """该音频路当前要产出字幕的角色（无 router=单向 [None]；暂停的角色跳过）。"""
        if self.router is None:
            return [None]
        roles = self.router.roles_for(kind)
        if not roles:
            return []                      # 这一路没有角色在用：不翻、不上屏
        return [r for r in roles if not self.router.is_paused(r)]

    def _roles_for(self, ev: SegmentEvent) -> list[str | None]:
        """这句话要产出几条字幕（同 _roles_of，见那里说明）。"""
        return self._roles_of(ev.kind)

    def feed_partial(self, kind: str, text: str) -> None:
        """边讲边译入口（任意线程可调）：partial 识别文本灌入段落管线。"""
        if text:
            self.partial_q.put((kind, text))

    def set_translators(self, mapping: dict[str, LLMTranslator]) -> None:
        """运行时切换（对话模式改设置）：换掉旧映射并关闭不再使用的连接。"""
        with self._tr_lock:
            old = self._translators
            self._translators = dict(mapping or {})
            keep = {id(t) for t in self._translators.values()} | {id(self.translator)}
        for t in old.values():
            if id(t) not in keep:
                try:
                    t.client.close()
                except Exception:  # noqa: BLE001
                    pass

    def run(self) -> None:
        n_workers = max(1, min(8, int(getattr(self.translator.cfg,
                                              "llm_concurrency", 2))))
        _log(f"翻译并发数: {n_workers}")
        self._ctx_mode = bool(getattr(self.translator.cfg, "contextual", True))
        if self._ctx_mode:
            _log(f"上下文连续段落模式：开（分段停顿 "
                 f"{int(getattr(self.translator.cfg, 'paragraph_gap_ms', 3500))}ms，"
                 f"尾部修订 {max(1, int(getattr(self.translator.cfg, 'revise_depth', 2)))} 句）")
        log = open(self.log_path, "a", encoding="utf-8")
        pool = ThreadPoolExecutor(max_workers=n_workers,
                                  thread_name_prefix="llm")
        self._pool = pool
        try:
            while not self.stop.is_set():
                self._drain_partials()        # 边讲边译：partial 优先消化
                try:
                    ev: SegmentEvent = self.seg_q.get(timeout=0.3)
                except queue.Empty:
                    continue
                if self.stop.is_set():        # 停止瞬间取到的积压事件：丢弃
                    break
                roles = self._roles_for(ev)
                if not roles:                  # 这一路没有角色在用（或都已暂停）
                    continue
                for role in roles:
                    # 序号按"产出的每一条"分配（同段多角色各自一个）：
                    #  ① item_id 唯一：词数拆分的多段共享同一 ts，不带序号会撞车
                    #     -> label_refs 互相顶掉 -> 前几段永远"… 翻译中"（真实 bug）
                    #  ② 上下文/日志按序落盘的槽位也不会互相覆盖（同段两角色）
                    seq = self._seq
                    self._seq += 1
                    item_id = f"{ev.source_key}-{ev.ts:.3f}-{seq}"
                    if role:
                        item_id += f"-{role}"
                    para = self._para_for(ev, role) if self._ctx_mode else None
                    if para is not None:
                        # 段落模式：不按句建显示条目，句子进段落 + 尾部修订
                        slot = len(para.dsts) - 1
                        pool.submit(self._translate_task, seq, ev, item_id,
                                    log, role, para, slot)
                    else:
                        self.window.post(DisplayItem(
                            kind=ev.kind, label=ev.label, app=ev.app,
                            text=ev.text, translation=None, item_id=item_id,
                            ts=ev.ts, asr_ms=ev.asr_ms, speaker=ev.speaker,
                            role=role or ""))
                        pool.submit(self._translate_task, seq, ev, item_id,
                                    log, role)
        finally:
            # 彻底停止：关闭底层连接中断"正在跑"的请求（本地模型可能分钟级），
            # 取消排队中的任务。ollama 服务本身保留（下次启动秒级就绪，
            # 且其空闲 5 分钟会自动卸载模型释放内存）
            _log("停止翻译：中断进行中与排队中的请求")
            with self._tr_lock:
                translators = list({id(t): t
                                    for t in [self.translator,
                                              *self._translators.values()]}.values())
            for t in translators:
                try:
                    t.client.close()
                except Exception:  # noqa: BLE001
                    pass
            pool.shutdown(wait=False, cancel_futures=True)
            log.close()

    def _close_para(self, key: tuple, p: _Para) -> None:
        """关段：留锚点供下一段衔接；有未修订的末句则补一次修订。"""
        self._para_anchor[key] = p.revised or _para_text(p)
        if p.pending_since_revise > 0 and len(p.dsts) >= 2:
            # 关段前补一次修订：末句必须被并进连贯文本（否则永按句显示）
            tr = self._resolve_translator(p.role or None, p.kind)
            self._maybe_revise(p, tr, p.role or None,
                               max(1, int(getattr(self.translator.cfg,
                                                  "revise_depth", 2))),
                               force=True)

    def _open_para(self, kind: str, role: str | None, label: str, app: str,
                   ts: float, first_text: str = "") -> _Para:
        """开新段（段落显示条目；first_text 非空 = 由定稿句开段）。"""
        para_id = f"para-{kind}-{ts:.3f}-{self._seq}"
        if role:
            para_id += f"-{role}"
        p = _Para(para_id=para_id, ts=ts, kind=kind, label=label, app=app,
                  role=role or "")
        if first_text:
            p.srcs.append(first_text)
            p.dsts.append(None)
        p.last_activity = time.monotonic()
        self.window.post(DisplayItem(
            kind=kind, label=label, app=app,
            text=first_text, translation=None, item_id=para_id,
            ts=ts, role=role or ""))
        return p

    def _para_for(self, ev: SegmentEvent, role: str | None) -> _Para:
        """取/开该 (音频路, 角色) 的段落：仅当停顿超阈值才分段（不设句数上限）。"""
        gap_thr = float(getattr(self.translator.cfg, "paragraph_gap_ms", 3500))
        key = (ev.kind, role or "")
        p = self._paras.get(key)
        if p is not None and ev.gap_ms <= gap_thr:
            # 并入当前段落：定稿句替换临时尾句（partial 只到刚才为止）
            p.live_gen += 1                     # 作废在途的 partial 翻译
            p.live_src = p.live_dst = ""
            p.live_upto = 0
            p.live_busy = False
            p.srcs.append(ev.text)
            p.dsts.append(None)
            p.last_activity = time.monotonic()
            self.window.update_source(p.para_id, _para_src(p))
            return p
        if p is not None:                      # 关段：留锚点供下一段衔接
            _log(f"分段：距上句停顿约 {ev.gap_ms / 1000:.1f}s ≥ 阈值 "
                 f"{gap_thr / 1000:.1f}s（段落共 {len(p.srcs)} 句）")
            self._close_para(key, p)
        p = self._open_para(ev.kind, role, ev.label, ev.app, ev.ts, ev.text)
        self._paras[key] = p
        return p

    def _drain_partials(self) -> None:
        """消化 partial 识别流：更新段落原文临时尾句；攒够词数就翻增量。"""
        while True:
            try:
                kind, text = self.partial_q.get_nowait()
            except queue.Empty:
                return
            if self.stop.is_set():
                return
            gap_thr = float(getattr(self.translator.cfg,
                                    "paragraph_gap_ms", 3500)) / 1000.0
            depth = max(1, int(getattr(self.translator.cfg, "revise_depth", 2)))
            for role in self._roles_of(kind):
                key = (kind, role or "")
                p = self._paras.get(key)
                now = time.monotonic()
                if p is not None and now - p.last_activity > gap_thr:
                    # 长停顿后重新开口：预分段（定稿句还没来，停顿已可判定）
                    _log(f"预分段：说话停顿 {now - p.last_activity:.1f}s ≥ "
                         f"阈值 {gap_thr:.1f}s")
                    self._close_para(key, p)
                    p = None
                if p is None:
                    # 说话中先开"预段落"：原文即时可见，译文随 partial 翻译跟进
                    p = self._open_para(kind, role, "", "", time.time())
                    self._paras[key] = p
                p.live_src = text
                p.last_activity = now
                self.window.update_source(p.para_id, _para_src(p))
                # 节流：新增 ≥ PARTIAL_WORDS 词才翻一次增量；同段一次一个在途
                if p.live_busy or _text_weight(text) - _text_weight(
                        text[:p.live_upto]) < PARTIAL_WORDS:
                    continue
                delta = text[p.live_upto:]
                if not delta.strip():
                    continue
                p.live_upto = len(text)
                p.live_busy = True
                tr = self._resolve_translator(role, kind)
                if self._pool is not None:
                    self._pool.submit(self._partial_translate_task, p, delta,
                                      tr, role, depth)

    def _partial_translate_task(self, p: _Para, delta: str,
                                translator: LLMTranslator, role: str | None,
                                depth: int) -> None:
        """翻译 partial 增量 → 临时尾句（gen 已变的作废；定稿句会替换它）。"""
        gen = p.live_gen
        prefix = p.live_dst
        try:
            try:
                out = translator.translate_stream(
                    delta, on_delta=lambda part: self._live_show(p, gen, prefix,
                                                                 part))
                if p.live_gen == gen and not self.stop.is_set():
                    p.live_dst = _join_parts([prefix, out])
                    self.window.update_translation(p.para_id, _para_text(p))
            except Exception as e:  # noqa: BLE001 - partial 翻译失败静默
                _log(f"partial 翻译失败（忽略）: {type(e).__name__}: {e}")
        finally:
            p.live_busy = False
            # 定稿句可能一直不来（持续说话）：pending 攒够也照常修订
            if p.live_gen == gen and p.pending_since_revise >= max(1, depth) \
                    and len(p.dsts) >= 2:
                self._maybe_revise(p, translator, role, depth)

    def _live_show(self, p: _Para, gen: int, prefix: str, part: str) -> None:
        """partial 翻译流式上屏（gen 不符=已定稿清空，丢弃）。"""
        if p.live_gen != gen or self.stop.is_set():
            return
        p.live_dst = _join_parts([prefix, part])
        self.window.update_translation(p.para_id, _para_text(p))

    def _maybe_revise(self, p: _Para, translator: LLMTranslator,
                      role: str | None, depth: int, force: bool = False) -> None:
        """句子翻完触发整段修订；每消化 depth 句做一次（同段一次只跑一个）。"""
        if len(p.dsts) < 2:                    # 单句没有"上文"可修
            return
        if not force and p.pending_since_revise < max(1, depth):
            return                             # 攒够 depth 句再修订（控制调用量）
        if p.revising:
            p.revise_again = True
            return
        p.pending_since_revise = 0
        p.revising = True
        if self._pool is not None:
            self._pool.submit(self._revise_task, p, translator, role, depth)

    def _revise_task(self, p: _Para, translator: LLMTranslator,
                     role: str | None, depth: int) -> None:
        """整段修订：全段识别+现译文 → 合并纠错为连贯文本（原地替换显示）。"""
        try:
            try:
                key = (p.kind, p.role)
                anchor = self._para_anchor.get(key, "")
                revised = translator.revise(list(p.srcs), _para_text(p),
                                            anchor=anchor)
                if revised:
                    p.revised = revised
                    p.revised_cover = len(p.dsts)   # 修订后新增的句子仍按句显示
                    self.window.update_translation(p.para_id, _para_text(p))
                    _log(f"段落修订完成 para={p.para_id[:28]} "
                         f"覆盖 {p.revised_cover} 句 -> {revised[:40]!r}")
            except Exception as e:  # noqa: BLE001 - 修订失败保留现有译文
                _log(f"段落修订失败（保留现有译文）: {type(e).__name__}: {e}")
        finally:
            p.revising = False
            if p.revise_again and not self.stop.is_set():
                p.revise_again = False
                self._maybe_revise(p, translator, role, depth)

    def _translate_task(self, seq: int, ev: SegmentEvent, item_id: str, log,
                        role: str | None = None, para: _Para | None = None,
                        slot: int = -1) -> None:
        """单句翻译（worker 线程）：流式增量与最终译文均独立乱序上屏。

        段落模式（para 非 None）：显示更新打到所属段落上（句子合入段落尾部），
        翻完触发尾部修订；item_id 仍按句分配，供上下文/会话日志按序落盘。
        """
        ttft_ms: float | None = None
        t_llm0 = time.monotonic()
        self.last_active = t_llm0          # 供"空闲自动卸载"判断是否还在翻译
        # 对话模式：按角色取翻译器（方向与上下文都按角色，与当前输入来源无关）
        translator = self._resolve_translator(role, ev.kind)

        def _show(text: str, ms: float | None = None) -> None:
            if para is None:
                self.window.update_translation(item_id, text, ms)
            else:
                self.window.update_translation(para.para_id, _para_text(para), ms)

        def on_delta(partial: str) -> None:
            nonlocal ttft_ms
            if ttft_ms is None:
                ttft_ms = (time.monotonic() - t_llm0) * 1000
            # 直接上屏：本句原文已先于本句任何 partial 入显示队列，乱序安全
            if para is not None:
                # 已被整段修订覆盖的句子：迟到的 partial 不回写（以修订结果为准）
                if para.revised is not None and slot < para.revised_cover:
                    return
                para.dsts[slot] = partial
                _show(_para_text(para))
            else:
                self.window.update_translation(item_id, partial)

        raw_parts: list[str] = []

        def on_raw(raw: str) -> None:
            raw_parts.append(raw)              # 模型原始输出（含思维链，供日志）

        _log(f"LLM 请求 seq={seq} kind={ev.kind}"
             f"{' role=' + role if role else ''} "
             f"backend={getattr(translator, 'backend', '?')} "
             f"model={getattr(translator, 'model', '?')} text={ev.text[:40]!r}")
        try:
            translation = translator.translate_stream(ev.text,
                                                      on_delta=on_delta,
                                                      raw_hook=on_raw)
            self._fail_streak = 0
        except Exception as e:  # noqa: BLE001 - 失败不中断管线（重试通常同样失败）
            ename = type(e).__name__
            hint = ""
            if "Connection" in ename or "Connect" in ename:
                local = getattr(translator, "base_url", "") and \
                    ("11434" in translator.base_url
                     or "127.0.0.1" in translator.base_url
                     or "localhost" in translator.base_url)
                hint = ("（本地模型服务连接失败：确认 ollama 已运行）" if local
                        else "（云端连接失败：检查网络或后端地址）")
            _log(f"LLM 失败 seq={seq}: {ename}: {e}")
            fb = self._maybe_degrade(translator, ename)
            if fb is None:
                translation = f"[翻译失败:{ename}]{hint}"
            else:
                # 刚降级：这一句立刻用本地模型补上，不让用户看到失败
                translator = fb
                try:
                    translation = translator.translate_stream(
                        ev.text, on_delta=on_delta, raw_hook=on_raw)
                    _log(f"本地兜底成功 seq={seq}: {translation[:40]!r}")
                except Exception as e2:  # noqa: BLE001
                    translation = f"[翻译失败:{type(e2).__name__}]（本地兜底也失败）"
                    _log(f"本地兜底失败 seq={seq}: {type(e2).__name__}: {e2}")
        llm_ms = (time.monotonic() - t_llm0) * 1000
        raw = (raw_parts[-1] if raw_parts else "")[:2000]
        _log(f"LLM 完成 seq={seq} ttft={ttft_ms and round(ttft_ms)}ms "
             f"total={round(llm_ms)}ms raw[:120]={raw[:120]!r}")
        if para is None:
            self.window.update_translation(item_id, translation, llm_ms)  # 先完成先上屏
        else:
            para.dsts[slot] = translation       # 覆盖流式 partial 为最终值
            _show(_para_text(para), llm_ms)
            para.pending_since_revise += 1
            self._maybe_revise(para, translator, role,
                               max(1, int(getattr(self.translator.cfg,
                                                  "revise_depth", 2))))
        if self.stop.is_set():                # 停止后被中断的任务：不写会话日志
            return
        self._ctx_commit(seq, ev, item_id, translation, llm_ms, ttft_ms, raw,
                         log, translator, role or "")

    # ---- 弱网降级（API 失败 -> 本地兜底 -> 恢复回切） ----

    def _resolve_translator(self, role, kind) -> LLMTranslator:
        """当前该用哪个翻译器（降级期间用本地兜底）。"""
        tr = (self.router.translator_for(role)
              if (self.router is not None and role) else self.pick(kind))
        if self._degraded:
            return self._fallback_for(tr) or tr
        return tr

    def _fallback_for(self, tr: LLMTranslator) -> LLMTranslator | None:
        """按这个翻译器的方向，拿一个本地兜底翻译器（按需建、缓存）。"""
        if self._fallback_builder is None:
            return None
        cfg = getattr(tr, "cfg", None)
        key = (getattr(cfg, "source_lang", None), getattr(cfg, "target_lang", ""),
               bool(getattr(cfg, "auto_zh_to_en", False)))
        with self._tr_lock:
            if key in self._fb_cache:
                return self._fb_cache[key]
        try:
            fb = self._fallback_builder(*key)
        except Exception as e:  # noqa: BLE001 - 建不出来就保持原样
            _log(f"⚠ 本地兜底翻译器创建失败: {type(e).__name__}: {e}")
            return None
        with self._tr_lock:
            self._fb_cache[key] = fb
        return fb

    def _maybe_degrade(self, translator: LLMTranslator,
                       ename: str) -> LLMTranslator | None:
        """连续失败达到阈值就切本地兜底；返回兜底翻译器（本次立即重试用）。"""
        if self._fallback_builder is None:
            return None
        fb = self._fallback_for(translator)
        if fb is None:
            return None
        self._fail_streak += 1
        if self._fail_streak < self.FAIL_THRESHOLD:
            return None
        if not self._degraded:
            self._degraded = True
            _log(f"⚠ 云端连续 {self._fail_streak} 句失败（{ename}）"
                 f"→ 已自动降级为本地模型（每 {int(self.PROBE_INTERVAL)}s 探一次云端）")
            self.window.set_status("云端翻译失败，已自动切到本地模型（恢复后自动回切）")
            self._start_probe()
        return fb

    def _start_probe(self) -> None:
        """降级后后台探测云端是否恢复，恢复即回切（不打断当前句子）。"""
        if self._probe_thread is not None and self._probe_thread.is_alive():
            return

        def loop() -> None:
            while not self.stop.is_set() and self._degraded:
                time.sleep(self.PROBE_INTERVAL)
                if self.stop.is_set() or not self._degraded:
                    return
                try:
                    self.translator.translate_stream("ok")
                except Exception:  # noqa: BLE001 - 还没恢复，继续等
                    continue
                self._degraded = False
                self._fail_streak = 0
                _log("云端已恢复 → 自动回切 API 翻译")
                self.window.set_status("云端已恢复，已自动切回 API 翻译")
                return

        self._probe_thread = threading.Thread(target=loop, daemon=True,
                                              name="llm-probe")
        self._probe_thread.start()

    def _ctx_commit(self, seq: int, ev: SegmentEvent, item_id: str,
                    translation: str, llm_ms: float, ttft_ms: float | None,
                    raw: str, log, translator=None, role: str = "") -> None:
        """完成的句子进入上下文/日志缓冲，按提交顺序落盘（显示不等这里）。"""
        with self._gate_lock:
            self._ctx_pending[seq] = {
                "ev": ev, "item_id": item_id, "final": translation,
                "llm_ms": llm_ms, "ttft_ms": ttft_ms, "raw": raw,
                "translator": translator, "role": role,
            }
            while self._next_ctx in self._ctx_pending:
                slot = self._ctx_pending.pop(self._next_ctx)
                self._next_ctx += 1
                ev2: SegmentEvent = slot["ev"]
                # 上下文按角色累积（每个角色自己的翻译器各有一份），按提交顺序
                ctx = getattr(slot.get("translator") or self.translator, "ctx", None)
                if ctx is not None:
                    ctx.append(ev2.text, slot["final"])
                with self._log_lock:
                    log.write(json.dumps({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "source": ev2.source_key, "kind": ev2.kind, "app": ev2.app,
                        "role": slot.get("role", ""),      # 对话模式角色
                        "label": ev2.label, "text": ev2.text,
                        "translation": slot["final"],
                        "speaker": ev2.speaker,            # 声纹标签（未启用为空串）
                        "asr_ms": round(ev2.asr_ms),
                        "llm_ms": round(slot["llm_ms"] or 0),
                        "llm_ttft_ms": slot.get("ttft_ms"),
                        "llm_raw": slot.get("raw", ""),   # 模型原始输出（含思维链）
                    }, ensure_ascii=False) + "\n")
                    log.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="livetrans", description="LiveTrans 实时语音翻译 M1（Linux 桌面）")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    ap.add_argument("--no-mic", action="store_true", help="禁用外部音频（麦克风）")
    ap.add_argument("--no-monitor", action="store_true", help="禁用内部音频（系统混音）")
    ap.add_argument("--mode", choices=["normal", "professional"])
    ap.add_argument("--provider", help="翻译后端（deepseek/glm/qwen/kimi/...）")
    ap.add_argument("--domain", help="专业模式领域提示，如 医学/法律/金融")
    args = ap.parse_args(argv)

    if args.list_devices:
        list_devices()
        return 0

    _log(f"LiveTrans 启动（Python {sys.version.split()[0]}）")
    seeded = ensure_data_files()          # 只读安装时首次播种配置
    if seeded:
        _log(f"已创建用户配置: {', '.join(seeded)}（位于数据目录）")
    cfg: AppConfig = load_config(args.config if Path(args.config).is_file() else None)

    # keys.env 里的 key 注入当前进程环境变量。
    # launcher 拉起主程序时会用 merged_env() 传 env；但用户**直接跑模块**时
    # 没人做这件事，翻译后端会报"需要环境变量 XXX"。这里兜底
    # （已存在的环境变量优先，不覆盖用户显式设置）。
    try:
        from .keys import key_env_overlay
        _n = 0
        for _env_name, _key in key_env_overlay(cfg.providers).items():
            if not os.environ.get(_env_name):
                os.environ[_env_name] = _key
                _n += 1
        if _n:
            _log(f"已从 keys.env 注入 {_n} 个 API key 环境变量")
    except Exception as _e:  # noqa: BLE001
        _log(f"注入 keys.env 失败（忽略）：{type(_e).__name__}: {_e}")
    if args.mode:
        cfg.translate.mode = args.mode
    if args.provider:
        cfg.translate.provider = args.provider
    if args.domain:
        cfg.translate.professional_domain = args.domain
    cfg.audio.mic_enabled = cfg.audio.mic_enabled and not args.no_mic
    cfg.audio.monitor_enabled = cfg.audio.monitor_enabled and not args.no_monitor

    stop = threading.Event()
    seg_q: "queue.Queue[SegmentEvent]" = queue.Queue()
    captures: list = []
    pause_events: dict[str, threading.Event] = {}   # kind -> 置位=该路暂停
    chains: dict[str, dict] = {}   # kind -> {"cap","stop","pause"}（boot 线程填充，
                                   #   主流程 finally 据此停各链的 ASR worker）
    config_arg = args.config if Path(args.config).is_file() else None

    def _on_pause_toggle(role: str, paused: bool) -> None:
        """字幕窗暂停的是**角色**：只停该角色的翻译与上屏，音频仍供另一角色。

        （线下两人共用麦克风时，各自用本栏的暂停键轮流说话。）
        若某一路音频已经没有任何未暂停的角色在用，就顺手停掉该路采集省 CPU。
        """
        who = dialog_role_label(role)
        _log(f"「{who}」{'暂停（本角色停翻停上屏）' if paused else '恢复'}")
        _recompute_capture_pause()

    def _on_style_change(style: dict) -> None:
        _persist_subtitle_style(config_arg, style)

    # 1) 字幕窗立即弹出：加载进度/错误实时可见，不再"黑等"
    _log("打开字幕窗")
    dialog = _load_dialog(config_arg)          # 对话模式：两侧来源与翻译方向
    worker_box: dict = {}                      # 翻译线程（字幕窗回调要能触达）

    def _on_partial(kind: str, text: str) -> None:
        """partial 分流：上下文连续段落模式喂给翻译线程（边讲边译），
        译文作为段落临时尾句，定稿句再替换；否则走字幕窗"识别中"行（旧行为）。
        翻译线程未就绪（启动头几秒）时也走旧行为，避免丢字。"""
        w = worker_box.get("worker")
        if w is not None and getattr(cfg.translate, "contextual", True):
            w.feed_partial(kind, text)
        else:
            window.set_partial(kind, text)

    def _direction_sig() -> tuple:
        """翻译方向指纹：只在这个变了才需要重建翻译器（换来源/换位置不需要）。"""
        return tuple((role, src, dst, bool(dialog.get("bidirectional", True)))
                     for role, (src, dst) in dialog_directions(dialog).items())

    def _start_unload_watchdog(minutes: int, provider) -> None:
        """空闲自动卸载（默认关闭）：连续 N 分钟没有翻译请求就卸下模型。

        卸下后下一次翻译要重新载入（本地模型冷启 1-2 分钟），所以默认不开。
        """
        def loop() -> None:
            _log(f"空闲自动卸载已开启：连续 {minutes} 分钟无翻译则释放显存")
            while not stop.is_set():
                time.sleep(5)
                worker = worker_box.get("worker")
                if worker is None or stop.is_set():
                    continue
                idle = time.monotonic() - getattr(worker, "last_active", 0.0)
                if idle < minutes * 60:
                    continue
                from .sysmon import ollama_loaded
                if not ollama_loaded(provider.base_url):
                    continue
                _unload_local_models(provider, keep="", log=_log,
                                     reason=f"空闲 {minutes} 分钟")
                worker.last_active = time.monotonic()   # 避免下一轮重复卸载

        threading.Thread(target=loop, daemon=True, name="unload-watchdog").start()

    def _recompute_capture_pause() -> None:
        """采集是否还需要：某一路音频若已无「未暂停的角色」在用，就停掉采集省 CPU。"""
        if not pause_events:
            return
        role_kinds = dialog_role_kinds(dialog)
        for kind, ev_obj in pause_events.items():
            in_use = any(role_kinds.get(role) == kind and role not in window.paused_roles
                         for role in ("self", "other"))
            (ev_obj.clear if in_use else ev_obj.set)()

    def _on_dialog_change(new_dialog: dict) -> None:
        """字幕窗「对话设置」改动：落盘；只在方向变了才重建该角色翻译器。

        换输入来源（麦克风↔系统声音）时**不重建**，所以该角色的上下文一路连续。
        """
        before = _direction_sig()
        dialog.clear()
        dialog.update(new_dialog)
        _persist_dialog(config_arg, dict(dialog))
        router = worker_box.get("router")
        if router is None:                     # 还没启动（设置阶段改动）
            return
        if before == _direction_sig():
            _log("对话模式：输入来源/布局已更新（翻译器与上下文保持不变）")
            _recompute_capture_pause()
            return
        try:
            mapping = _build_dialogue_translators(cfg.translate, cfg.providers,
                                                  dialog, router.primary, _log)
        except Exception as e:  # noqa: BLE001 - 重建失败保持原翻译器可用
            _log(f"⚠ 对话模式翻译器重建失败（沿用原设置）: {type(e).__name__}: {e}")
            return
        router.set_translators(mapping)
        _log("对话模式：翻译方向已更新（运行时生效）")

    window = SubtitleWindow(status="启动中 ...", stop_event=stop,
                            style=_load_subtitle_style(config_arg),
                            dialog=dialog,
                            on_pause_toggle=_on_pause_toggle,
                            on_style_change=_on_style_change,
                            on_dialog_change=_on_dialog_change)

    def _sigint(_sig, _frame):
        stop.set()
        try:
            window.root.quit()
        except Exception:  # noqa: BLE001
            pass

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    # 2) 后台启动链：SenseVoice -> 音频源 -> 翻译后端 -> 转录 worker
    def _fail(msg: str) -> None:
        _log(f"错误: {msg}")
        window.set_status(f"✖ {msg}（详见运行日志；关闭窗口退出）")

    def boot() -> None:
        try:
            _log("加载 SenseVoice 模型（项目 models/sensevoice/）")
            window.set_status("加载 SenseVoice 模型 ...")
            t0 = time.monotonic()
            asr = SenseVoiceASR(cfg.asr.language)
            _log(f"SenseVoice 就绪: {asr.loaded_from}"
                 f"（耗时 {time.monotonic() - t0:.1f}s）")

            # 声纹角色标注（可选）：模型后台预热，避免第一句话多等载入时间
            tracker = build_tracker(getattr(cfg, "speaker", None), _log)
            if tracker is not None:
                tracker.reset()
                window.set_status("声纹模型载入中 ...")
                threading.Thread(target=tracker.preload, daemon=True,
                                 name="speaker-preload").start()

            # 音频源：每路独立 捕获线程 + ASR worker（内外部音频可同时翻译）。
            # 每条链有独立的 stop 事件：设备热切换时只重启那一路，
            # 另一路与翻译线程不动（会话/上下文/段落保持连续）。
            # chains 字典在 main 作用域（boot 线程填充，finally 消费）。
            def spawn(source: SourceInfo, capture) -> None:
                cap_q: "queue.Queue" = queue.Queue()
                cap = capture(source, cap_q, target_sr=cfg.audio.sample_rate)
                cap.start()
                prev = chains.get(source.kind)
                pause_ev = threading.Event()
                if prev is not None and prev["pause"].is_set():
                    pause_ev.set()                 # 重启链保留暂停状态
                pause_events[source.kind] = pause_ev
                chain_stop = threading.Event()
                ASRWorker(source, cap_q, asr, cfg.asr, seg_q, chain_stop,
                          on_level=window.set_level,
                          on_partial=_on_partial,
                          pause_event=pause_ev, speaker=tracker).start()
                captures.append(cap)
                chains[source.kind] = {"cap": cap, "stop": chain_stop,
                                       "pause": pause_ev}
                _log(f"音频源就绪: {source.label}（外部/内部={source.kind}，"
                     f"设备: {cap.device_name}）")

            def _restart_chain(kind: str) -> None:
                """设备热切换后重开一路捕获（旧流绑着旧设备，只会采到静音）。"""
                ch = chains.pop(kind, None)
                if ch is None:
                    return                          # 该路本来就没在跑
                ch["stop"].set()
                ch["cap"].stop()
                try:
                    if kind == "mic":
                        spawn(SourceInfo("mic", "external", "麦克风", "microphone"),
                              lambda s, q, target_sr: AudioCapture(
                                  s, cfg.audio.mic_device, q,
                                  target_sr=target_sr))
                    else:
                        mon_src = cfg.audio.monitor_source or \
                            default_monitor_source()
                        if not mon_src:
                            _log("⚠ 默认输出切换后未找到 monitor 源，"
                                 "内部音频暂不可用")
                            return
                        spawn(SourceInfo("monitor", "internal",
                                         friendly_source_name(mon_src), mon_src),
                              lambda s, q, target_sr: ParecCapture(
                                  s, mon_src, q, target_sr))
                    _log(f"音频源已跟随系统设备切换重开（{kind}）")
                except Exception as e:  # noqa: BLE001
                    _log(f"⚠ 重开音频源失败（{kind}）: {type(e).__name__}: {e}")

            def _watch_default_devices() -> None:
                """跟随系统默认音频设备切换（接耳机/换麦克风无需重启字幕）。

                monitor 源绑定"启动那一刻的默认输出"、麦克风 auto 绑定"打开
                时的默认输入"——热切换后旧流只采到静音（Windows 侧同类问题
                见 1d84342）。这里每 2s 轮询 pactl，变了就只重启对应那一路。
                显式配置了 monitor_source / mic_device 的不跟随（以配置为准）。
                """
                last = {"sink": default_audio_device("sink"),
                        "source": default_audio_device("source")}
                if last["sink"] is None and last["source"] is None:
                    return                          # 无 pactl：无法探测，不跟随
                while not stop.is_set():
                    time.sleep(2.0)
                    if stop.is_set():
                        return
                    sink = default_audio_device("sink")
                    if sink and sink != last["sink"]:
                        last["sink"] = sink
                        if cfg.audio.monitor_enabled and \
                                not cfg.audio.monitor_source:
                            _log(f"系统默认输出已切换 → {sink}，重开内部音频")
                            _restart_chain("monitor")
                    src = default_audio_device("source")
                    if src and src != last["source"]:
                        last["source"] = src
                        if cfg.audio.mic_enabled and \
                                cfg.audio.mic_device is None:
                            _log(f"系统默认输入已切换 → {src}，重开麦克风")
                            _restart_chain("mic")

            threading.Thread(target=_watch_default_devices, daemon=True,
                             name="audio-dev-watch").start()

            if cfg.audio.mic_enabled:
                window.set_status("打开麦克风 ...")
                try:
                    spawn(SourceInfo("mic", "external", "麦克风", "microphone"),
                          lambda s, q, target_sr: AudioCapture(
                              s, cfg.audio.mic_device, q, target_sr=target_sr))
                except Exception as e:  # noqa: BLE001
                    _fail(f"麦克风启动失败: {e}")
            if cfg.audio.monitor_enabled:
                window.set_status("连接系统音频 monitor 源 ...")
                mon_src = cfg.audio.monitor_source or default_monitor_source()
                if mon_src:
                    _log(f"使用 monitor 源: {mon_src}")
                    try:
                        # 首选：parec 直接捕获 PulseAudio monitor 源（最可靠）
                        spawn(SourceInfo("monitor", "internal",
                                         friendly_source_name(mon_src), mon_src),
                              lambda s, q, target_sr: ParecCapture(
                                  s, mon_src, q, target_sr))
                    except Exception as e:  # noqa: BLE001
                        _fail(f"系统音频捕获失败: {e}")
                else:
                    # 后备：PortAudio 枚举 monitor 设备
                    mon = cfg.audio.monitor_device
                    if mon is None:
                        mon = find_monitor_device()
                    if mon is None:
                        _log("⚠ 未找到系统音频 monitor 源，跳过内部音频"
                             "（确认桌面使用 PulseAudio/PipeWire，"
                             "且已安装 pulseaudio-utils）")
                        window.set_status(
                            "⚠ 未找到系统音频 monitor 源，仅麦克风可用")
                    else:
                        try:
                            spawn(SourceInfo("monitor", "internal", "系统音频",
                                             "system-mix"),
                                  lambda s, q, target_sr: AudioCapture(
                                      s, mon, q, target_sr=target_sr))
                        except Exception as e:  # noqa: BLE001
                            _fail(f"系统音频捕获失败: {e}")

            if not captures:
                _fail("没有任何可用音频源，无法开始（关闭窗口退出）")
                return

            # 翻译后端 + 会话日志
            window.set_status(f"连接翻译后端 {cfg.translate.provider} ...")
            try:
                cfg.translate.source_lang = cfg.asr.language  # 提示词源语言
                prov = cfg.providers[cfg.translate.provider]
                if is_local_base_url(prov.base_url):   # 本地模型：先确保服务在
                    window.set_status("检查本地模型服务 ...")
                    ensure_local_backend(prov.base_url, prov.model, _log)
                translator = LLMTranslator(cfg.translate, cfg.providers)
            except Exception as e:  # noqa: BLE001
                _fail(f"翻译后端初始化失败: {e}")
                return
            _log(f"翻译后端就绪: {translator.backend} / {translator.model} / "
                 f"{'专业' if cfg.translate.mode == 'professional' else '普通'}模式")
            window.set_lang_labels(f"{cfg.translate.source_lang or '自动'} → "
                                   f"{cfg.translate.target_lang}")

            # 本地模型显存管理：清掉别的模型 / 可选空闲自动卸载
            local_cfg = getattr(cfg, "local", None)
            if is_local_base_url(prov.base_url) and local_cfg is not None:
                if local_cfg.unload_others_on_start:
                    # Ollama 换模型不会自动卸载旧的（实测两张模型同时占显存），
                    # 启动时把没在用的先清掉，8G 卡上很关键
                    _unload_local_models(prov, keep=prov.model, log=_log,
                                         reason="启动时清理其它模型")
                if int(local_cfg.auto_unload_min or 0) > 0:
                    _start_unload_watchdog(int(local_cfg.auto_unload_min), prov)

            log_dir = Path(cfg.session.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
            _log(f"会话日志: {log_path}")
            window.set_session_path(log_path)      # 顶栏「导出」的数据源
            # 对话模式：按角色建翻译器 + 实时路由（来源可随时换，上下文按角色保留）
            dialogue_tr = _build_dialogue_translators(
                cfg.translate, cfg.providers, dialog, translator, _log)
            router = DialogRouter(window, dialogue_tr, translator)
            worker = TranslatorWorker(seg_q, window, translator, log_path, stop,
                                      router=router,
                                      fallback_builder=_make_fallback_builder(
                                          cfg, _log))
            worker_box["worker"] = worker
            worker_box["router"] = router
            worker.start()

            status = (f"{translator.backend} · {translator.model} · "
                      f"{'专业' if cfg.translate.mode == 'professional' else '普通'}模式"
                      f" · {len(captures)} 路音频 · 就绪，开始说话吧（关闭窗口或 Ctrl+C 结束）")
            if is_local_base_url(prov.base_url):
                # 本地模型预热：首次请求要把模型整个载入内存（实测 ~107s），
                # 不预热的话说的第一句话会"翻译中"挂满整个加载时长
                _log("正在预热本地模型（首次需载入内存，约 1-2 分钟）...")
                window.set_status("正在预热本地模型（首次约 1-2 分钟），其余功能已就绪")

                def _warmup() -> None:
                    t0 = time.monotonic()
                    try:
                        translator.translate_stream("hello")
                        _log(f"本地模型预热完成（耗时 {time.monotonic() - t0:.0f}s）"
                             "，后续翻译恢复正常速度")
                    except Exception as e:  # noqa: BLE001 - 预热失败不阻塞使用
                        _log(f"⚠ 本地模型预热失败: {type(e).__name__}: {e}")
                    if not stop.is_set():
                        window.set_status(status)

                threading.Thread(target=_warmup, daemon=True,
                                 name="warmup").start()
            else:
                window.set_status(status)
            _log("一切就绪，开始说话吧。")
        except Exception as e:  # noqa: BLE001 - boot 内部兜底
            _fail(f"启动失败: {type(e).__name__}: {e}")

    threading.Thread(target=boot, daemon=True, name="boot").start()

    try:
        window.run()
    finally:
        stop.set()
        for ch in chains.values():               # 各链独立 stop：让 ASR worker 退出
            ch["stop"].set()
        for c in captures:
            c.stop()
        # 停止字幕时卸载本地模型（默认关闭：默认保持驻留，下次启动秒级就绪）
        local_cfg = getattr(cfg, "local", None)
        if local_cfg is not None and local_cfg.unload_on_exit:
            try:
                _unload_local_models(cfg.providers[cfg.translate.provider], keep="",
                                     log=_log, reason="退出时释放")
            except Exception as e:  # noqa: BLE001
                _log(f"⚠ 退出时卸载本地模型失败: {type(e).__name__}: {e}")
    _log("会话结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
