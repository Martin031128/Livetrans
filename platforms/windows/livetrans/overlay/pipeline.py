"""外挂管线：音频源管理 + ASR/翻译 worker 装配 + 镜像模式 + 命令行入口。"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..asr import ASRWorker, SenseVoiceASR
from ..capture import (AudioCapture, ParecCapture, SourceInfo,
                       default_monitor_source, find_monitor_device,
                       friendly_source_name)
from ..config import AppConfig, load_config
from ..main import TranslatorWorker, _make_fallback_builder
from ..mirror import SessionMirror
from ..paths import ensure_data_files
from ..speaker import build_tracker
from ..translate import (LLMTranslator, ensure_local_backend,
                         is_local_base_url)
from ._common import _log
from .persist import persist_overlay
from .window import OverlayWindow


class SourceManager:
    """外挂音频源管理：启停/切换/暂停，与 GUI 解耦（可注入假源做测试）。

    - asr_box["asr"] 由 boot 线程异步装载，未就绪时 start_source 直接跳过；
    - sink 是字幕窗的鸭子类型（set_level / set_partial / set_status）；
    - source_factory 测试注入点：kind -> (SourceInfo, 采集工厂) | (None, None)；
    - persist 可选回调：来源变化时收到 {"source": want}（落盘与否由调用方定）。
    """

    def __init__(self, audio_cfg, asr_cfg, seg_q, sink, asr_box, *,
                 mirror: bool = False, log=None, persist=None,
                 source_factory=None):
        self.audio = audio_cfg
        self.asr_cfg = asr_cfg
        self.seg_q = seg_q
        self.sink = sink
        self.asr_box = asr_box
        self.mirror = mirror
        self._log = log or _log
        self._persist = persist
        self._source_factory = source_factory      # 测试注入点
        self.running: dict[str, dict] = {}         # kind -> {cap, stop, pause}

    def _source_of(self, kind: str):
        """返回 (SourceInfo, 采集工厂)；不可用返回 (None, None)。"""
        if self._source_factory is not None:       # 测试注入优先
            return self._source_factory(kind)
        if kind == "mic":
            return (SourceInfo("mic", "external", "麦克风", "microphone"),
                    lambda s, q, sr: AudioCapture(s, self.audio.mic_device, q,
                                                  target_sr=sr))
        mon = self.audio.monitor_source or default_monitor_source()
        if mon:
            return (SourceInfo("monitor", "internal",
                               friendly_source_name(mon), mon),
                    lambda s, q, sr: ParecCapture(s, mon, q, target_sr=sr))
        dev = self.audio.monitor_device or find_monitor_device()
        if dev:
            return (SourceInfo("monitor", "internal", "系统音频", "system-mix"),
                    lambda s, q, sr: AudioCapture(s, dev, q, target_sr=sr))
        return None, None

    def start_source(self, kind: str) -> None:
        if kind in self.running or self.asr_box.get("asr") is None:
            return
        src, factory = self._source_of(kind)
        if src is None:
            self._log(f"⚠ 音频源不可用（{kind}），跳过")
            return
        cap_q: "queue.Queue" = queue.Queue()
        cap = factory(src, cap_q, self.audio.sample_rate)
        cap.start()
        ev, pv = threading.Event(), threading.Event()
        ASRWorker(src, cap_q, self.asr_box["asr"], self.asr_cfg, self.seg_q,
                  ev, on_level=self.sink.set_level,
                  on_partial=self.sink.set_partial,
                  pause_event=pv, speaker=self.asr_box.get("speaker")).start()
        self.running[kind] = {"cap": cap, "stop": ev, "pause": pv}
        self._log(f"音频源就绪: {src.label}（{cap.device_name}）")

    def stop_source(self, kind: str) -> None:
        info = self.running.pop(kind, None)
        if info is None:
            return
        info["stop"].set()
        try:
            info["cap"].stop()
        except Exception:  # noqa: BLE001
            pass
        self._log(f"已停止音频源: {kind}")

    def set_source(self, want: str) -> None:
        """切换音频来源（运行时）：内部/外部/两者。"""
        if self.mirror:
            self.sink.set_status("镜像模式：声音来源跟随主程序设置")
            return
        if want not in ("internal", "external", "both"):
            want = "both"
        self._log(f"外挂音频来源: {want}")
        wish = {"mic": self.audio.mic_enabled and want in ("external", "both"),
                "monitor": self.audio.monitor_enabled
                and want in ("internal", "both")}
        for kind, on in wish.items():
            (self.start_source if on else self.stop_source)(kind)
        if not self.running:
            self.sink.set_status("没有可用音频源，外挂仅显示占位")
        if self._persist is not None:
            self._persist({"source": want})

    def set_paused(self, paused: bool) -> None:
        for info in self.running.values():
            (info["pause"].set if paused else info["pause"].clear)()

    def stop_all(self) -> None:
        for kind in list(self.running):
            self.stop_source(kind)


def run_overlay(cfg: AppConfig, config_path: Path | None = None) -> int:
    """外挂模式：悬浮字幕窗 + 音频 → ASR → 翻译（复用主程序 worker）。

    音频来源可在运行时切换（控制面板下拉 / 配置）：每路采集与 ASR worker 有
    各自的停止事件，切换 = 停掉不需要的 + 起需要的，不重启外挂。
    """
    ensure_data_files()
    app = QApplication.instance() or QApplication(sys.argv)
    # 界面/字幕统一现代中文字体：Qt 在简中 Windows 的默认 CJK 回退是宋体
    # （衬线、小字号发虚显旧）。QFont() 默认构造会继承此字体，
    # 字幕绘制（_fonts）与全部控件一起受益；缺失时 Qt 自动按字形回退
    _ui_font = app.font()
    _ui_font.setFamily("Microsoft YaHei UI")
    if _ui_font.pointSize() <= 0:
        _ui_font.setPointSize(9)
    app.setFont(_ui_font)
    stop = threading.Event()
    overlay = OverlayWindow(cfg.overlay, config_path=config_path)
    mirror_mode = bool(getattr(cfg.overlay, "mirror", False))
    seg_q: "queue.Queue" = queue.Queue()
    asr_box: dict = {}                     # SenseVoice 实例（boot 里加载）
    sources = SourceManager(
        cfg.audio, cfg.asr, seg_q, overlay, asr_box,
        mirror=mirror_mode, log=_log,
        persist=(lambda patch: persist_overlay(config_path, patch))
        if config_path else None)

    def on_pause(paused: bool) -> None:
        if mirror_mode:
            _log(f"外挂字幕{'暂停' if paused else '继续'}"
                 "（镜像模式：只停显示，不影响主程序）")
            return
        sources.set_paused(paused)
        _log(f"外挂字幕{'暂停' if paused else '继续'}")

    overlay.on_pause = on_pause

    def set_source(want: str) -> None:
        """来源变化：先同步到字幕窗配置，再交给 SourceManager 启停。"""
        overlay.cfg.source = want if want in ("internal", "external",
                                              "both") else "both"
        sources.set_source(want)

    overlay.on_source_change = set_source

    def quit_app() -> None:
        stop.set()
        sources.stop_all()

    overlay.on_exit = quit_app

    def boot() -> None:
        if mirror_mode:
            # 镜像模式：不加载 ASR/翻译模型，只跟随主程序的会话日志
            overlay.set_mirror(True)
            _log("镜像模式：只显示主程序（保存并启动）的字幕，"
                 "外挂不再自己识别/翻译（省 API 与算力）")
            SessionMirror(Path(cfg.session.log_dir), overlay, stop).start()
            return
        # keys.env 里的 key 注入当前进程环境变量。
        # launcher 拉起外挂时会用 merged_env() 传 env；但**用户直接跑 exe/模块**时
        # 没人做这件事，LLMTranslator 会报"需要环境变量 XXX"。这里兜底注入
        # （已存在的环境变量优先，不覆盖用户显式设置）。
        try:
            import os
            from ..keys import key_env_overlay
            _injected = 0
            for env_name, key in key_env_overlay(cfg.providers).items():
                if not os.environ.get(env_name):
                    os.environ[env_name] = key
                    _injected += 1
            if _injected:
                _log(f"已从 keys.env 注入 {_injected} 个 API key 环境变量")
        except Exception as e:  # noqa: BLE001 - 注入失败只影响后端初始化报错提示
            _log(f"注入 keys.env 失败（忽略）：{type(e).__name__}: {e}")

        try:
            overlay.set_status("加载 SenseVoice 模型 ...")
            t0 = time.monotonic()
            asr_box["asr"] = SenseVoiceASR(cfg.asr.language)
            _log(f"SenseVoice 就绪（{time.monotonic() - t0:.1f}s）")

            # 声纹（可选，与外挂共用同一套说话人聚类）
            tracker = build_tracker(getattr(cfg, "speaker", None), _log)
            if tracker is not None:
                asr_box["speaker"] = tracker
                threading.Thread(target=tracker.preload, daemon=True,
                                 name="speaker-preload").start()

            set_source(getattr(cfg.overlay, "source", "both"))

            prov = cfg.providers[cfg.translate.provider]
            if is_local_base_url(prov.base_url):
                ensure_local_backend(prov.base_url, prov.model, _log)
                local_cfg = getattr(cfg, "local", None)
                if local_cfg is not None and local_cfg.unload_others_on_start:
                    from ..sysmon import ollama_loaded, ollama_unload
                    others = [m.get("name", "")
                              for m in ollama_loaded(prov.base_url)
                              if m.get("name") and m.get("name") != prov.model]
                    if others:
                        ollama_unload(others, prov.base_url)
                        _log(f"释放显存：已卸载其它模型 {', '.join(others)}")
            translator = LLMTranslator(cfg.translate, cfg.providers)
            log_dir = Path(cfg.session.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"overlay-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
            TranslatorWorker(seg_q, overlay, translator, log_path, stop,
                             fallback_builder=_make_fallback_builder(
                                 cfg, _log)).start()
            _log(f"翻译后端: {translator.backend}/{translator.model}"
                 f" · 会话日志 {log_path.name} · 就绪，开始说话吧")
        except Exception as e:  # noqa: BLE001
            _log(f"外挂启动失败: {type(e).__name__}: {e}")
            overlay.set_status(f"启动失败: {e}")

    threading.Thread(target=boot, daemon=True, name="overlay-boot").start()
    try:
        return app.exec()
    finally:
        stop.set()
        sources.stop_all()
        _log("外挂结束")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="livetrans.overlay",
                                 description="LiveTrans 字幕外挂（PySide6/Qt6 悬浮字幕）")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config) if Path(args.config).is_file() else None
    cfg = load_config(str(cfg_path) if cfg_path else None)
    _log("字幕外挂启动（PySide6/Qt6）")
    try:
        return run_overlay(cfg, config_path=cfg_path)
    except KeyboardInterrupt:
        return 0
