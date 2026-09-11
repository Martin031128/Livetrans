#!/usr/bin/env python3
"""SourceManager（外挂音频源管理）回归：注入假源测切换/暂停/清退。

不需要真实音频设备：source_factory 注入假采集，ASRWorker 替换为只等
停止事件的空线程——验证启停/切换/暂停/镜像守卫/来源持久化语义。
"""
from __future__ import annotations

import queue
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.capture import SourceInfo               # noqa: E402
import livetrans.overlay.pipeline as _pipeline          # noqa: E402
from livetrans.overlay.pipeline import SourceManager    # noqa: E402


class _FakeASRWorker(threading.Thread):
    """替身 ASR worker：只等停止事件，不碰任何模型/设备。"""

    def __init__(self, source, capture_q, asr, asr_cfg, out_q, stop_event,
                 **kw):
        super().__init__(daemon=True, name=f"fake-asr-{source.key}")
        self.stop_event = stop_event

    def run(self) -> None:
        self.stop_event.wait()


# 模块级替换：本进程内 SourceManager 起的都是替身 worker
_pipeline.ASRWorker = _FakeASRWorker


class FakeCapture:
    """假采集：记录启停状态。"""

    def __init__(self, src, q, sr):
        self.src, self.q, self.sr = src, q, sr
        self.device_name = f"fake-{src.key}"
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeSink:
    """字幕窗鸭子类型替身：只记录 status。"""

    def __init__(self):
        self.status: list[str] = []

    def set_status(self, s: str) -> None:
        self.status.append(s)

    def set_partial(self, kind, text) -> None:
        pass

    def set_level(self, kind, rms) -> None:
        pass


def make_manager(caps: list, unavailable=(), mirror: bool = False,
                 persist=None, asr_ready: bool = True):
    """构造注入假源的 SourceManager（audio/asr 配置用 SimpleNamespace 顶替）。"""
    audio = types.SimpleNamespace(
        mic_enabled=True, monitor_enabled=True, mic_device=None,
        monitor_source=None, monitor_device=None, sample_rate=16000)
    asr_cfg = types.SimpleNamespace(language="auto")

    def factory(kind: str):
        if kind in unavailable:
            return None, None
        src = SourceInfo(kind, "external" if kind == "mic" else "internal",
                         f"假源-{kind}", f"fake-{kind}")

        def make(s, q, sr):
            cap = FakeCapture(s, q, sr)
            caps.append(cap)
            return cap

        return src, make

    logs: list[str] = []
    asr_box = {"asr": object()} if asr_ready else {}
    mgr = SourceManager(audio, asr_cfg, queue.Queue(), FakeSink(), asr_box,
                        mirror=mirror, log=logs.append, persist=persist,
                        source_factory=factory)
    return mgr, logs


def test_switch_starts_and_stops_by_kind():
    """切换来源 = 停掉不需要的 + 起需要的，互不干扰。"""
    caps: list = []
    mgr, _ = make_manager(caps)
    mgr.set_source("external")
    assert [c.src.key for c in caps] == ["mic"], caps
    assert caps[0].started and not caps[0].stopped
    mgr.set_source("internal")                 # 切换：停 mic、起 monitor
    assert [c.src.key for c in caps] == ["mic", "monitor"], caps
    assert caps[0].stopped and caps[1].started
    assert set(mgr.running) == {"monitor"}, mgr.running
    mgr.set_source("both")                     # monitor 仍在跑，补起 mic
    assert set(mgr.running) == {"mic", "monitor"}, mgr.running
    print("[PASS] 来源切换：按 kind 启停、运行表正确")


def test_bad_kind_normalized_and_persisted():
    """非法来源归一化为 both，且来源变化交给 persist 回调落盘。"""
    caps: list = []
    saved: list = []
    mgr, _ = make_manager(caps, persist=saved.append)
    mgr.set_source("bogus")
    assert saved and saved[-1] == {"source": "both"}, saved
    assert set(mgr.running) == {"mic", "monitor"}, mgr.running
    print(f"[PASS] 非法来源归一化 + persist 收到 {saved[-1]}")


def test_pause_and_stop_all():
    """暂停/继续作用于所有运行中的 pause 事件；stop_all 全部清退。"""
    caps: list = []
    mgr, _ = make_manager(caps)
    mgr.set_source("both")
    mgr.set_paused(True)
    assert all(i["pause"].is_set() for i in mgr.running.values())
    mgr.set_paused(False)
    assert not any(i["pause"].is_set() for i in mgr.running.values())
    mgr.stop_all()
    assert not mgr.running, mgr.running
    assert all(c.stopped for c in caps), caps
    print("[PASS] 暂停/继续/stop_all 语义正确")


def test_unavailable_source_skipped_and_reported():
    """某路源不可用：跳过并记日志，另一路照常。"""
    caps: list = []
    mgr, logs = make_manager(caps, unavailable=("monitor",))
    mgr.set_source("internal")
    assert not mgr.running and not caps, (mgr.running, caps)
    assert any("不可用" in s for s in logs), logs
    mgr.set_source("both")
    assert set(mgr.running) == {"mic"}, mgr.running
    print("[PASS] 不可用源：跳过 + 状态提示")


def test_no_source_at_all_reports_placeholder():
    """两路都不可用：给出"没有可用音频源"占位提示。"""
    caps: list = []
    mgr, _ = make_manager(caps, unavailable=("mic", "monitor"))
    mgr.set_source("both")
    assert not mgr.running
    assert any("没有可用音频源" in s for s in mgr.sink.status), mgr.sink.status
    print("[PASS] 无可用源：占位提示")


def test_asr_not_ready_noop_and_mirror_guard():
    """ASR 未装载：start_source 空转；镜像模式：来源切换被守卫。"""
    caps: list = []
    mgr, _ = make_manager(caps, asr_ready=False)
    mgr.set_source("both")
    assert not mgr.running and not caps, (mgr.running, caps)
    caps2: list = []
    mgr2, _ = make_manager(caps2, mirror=True)
    mgr2.set_source("external")
    assert not mgr2.running and not caps2, (mgr2.running, caps2)
    assert any("镜像" in s for s in mgr2.sink.status), mgr2.sink.status
    print("[PASS] ASR 未就绪跳过 + 镜像模式守卫")


if __name__ == "__main__":
    test_switch_starts_and_stops_by_kind()
    test_bad_kind_normalized_and_persisted()
    test_pause_and_stop_all()
    test_unavailable_source_skipped_and_reported()
    test_no_source_at_all_reports_placeholder()
    test_asr_not_ready_noop_and_mirror_guard()
    print("[PASS] SourceManager：切换/归一化/暂停/清退/守卫 全部通过")
