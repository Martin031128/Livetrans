"""弱网降级：云端连续失败 -> 本地兜底 -> 云端恢复自动回切。"""
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livetrans.asr import SegmentEvent  # noqa: E402
from livetrans.main import TranslatorWorker, _make_fallback_builder  # noqa: E402


class FakeClient:
    def close(self):
        pass


class FakeTr:
    def __init__(self, tag, fail=False):
        self.tag, self.fail, self.client = tag, fail, FakeClient()
        self.cfg = SimpleNamespace(source_lang=None, target_lang="中文",
                                   auto_zh_to_en=False, llm_concurrency=2)
        self.backend, self.model = tag, tag
        self.ctx = SimpleNamespace(items=[])
        self.ctx.append = lambda s, d: self.ctx.items.append((s, d))
        self.calls = 0

    def translate_stream(self, text, on_delta=None, raw_hook=None):
        self.calls += 1
        if self.fail:
            raise ConnectionError("模拟云端断网")
        if on_delta:
            on_delta(f"<{self.tag}>")
        return f"[{self.tag}]{text}"


class FakeWin:
    def __init__(self):
        self.status, self.updates = [], []

    def set_status(self, text):
        self.status.append(text)

    def update_translation(self, iid, text, llm_ms=None):
        self.updates.append((iid, text))


primary = FakeTr("api", fail=True)
fb = FakeTr("local")
win = FakeWin()
stop = threading.Event()
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    log_path = Path(td) / "s.jsonl"
    log = open(log_path, "a", encoding="utf-8")
    w = TranslatorWorker(None, win, primary, log_path, stop,
                         fallback_builder=lambda src, dst, auto: fb)
    w.PROBE_INTERVAL = 1.0
    ev = SegmentEvent(source_key="mic", kind="external", label="l", app="",
                      text="hello", ts=1.0, asr_ms=10)

    w._translate_task(0, ev, "i0", log)                     # 第 1 句失败
    print("第1句:", win.updates[-1][1], "| 失败计数:", w._fail_streak,
          "| 已降级:", w._degraded)
    assert win.updates[-1][1].startswith("[翻译失败") and not w._degraded

    w._translate_task(1, ev, "i1", log)                     # 第 2 句触发降级并本地补上
    print("第2句:", win.updates[-1][1], "| 已降级:", w._degraded)
    assert w._degraded and win.updates[-1][1] == "[local]hello", win.updates[-1]
    assert any("已自动切到本地模型" in s for s in win.status), win.status

    w._translate_task(2, ev, "i2", log)                     # 后续都用本地
    print("第3句:", win.updates[-1][1])
    assert win.updates[-1][1] == "[local]hello"
    assert primary.calls == 2, primary.calls                # 降级后不再打云端

    # 云端恢复 -> 探测线程回切
    primary.fail = False
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8 and w._degraded:
        time.sleep(0.2)
    print("恢复探测耗时 %.1fs | 已降级: %s | 状态: %s"
          % (time.monotonic() - t0, w._degraded, win.status[-1]))
    assert not w._degraded, "云端恢复后应自动回切"
    assert any("已自动切回 API" in s for s in win.status), win.status
    w._translate_task(3, ev, "i3", log)
    print("回切后:", win.updates[-1][1])
    assert win.updates[-1][1] == "[api]hello"
    log.close()
    stop.set()

# _make_fallback_builder 的启用条件
from livetrans.config import AppConfig, ProviderConfig  # noqa: E402
cfg = AppConfig()
cfg.providers = {
    "deepseek": ProviderConfig(base_url="https://api.deepseek.com",
                               api_key_env="DEEPSEEK_API_KEY", model="x",
                               type="api"),
    "ollama": ProviderConfig(base_url="http://localhost:11434/v1",
                             model="qwen3:4b-instruct", type="local"),
}
cfg.translate.provider = "deepseek"
print("云端后端 -> 构造器:", "有" if _make_fallback_builder(cfg, print) else "无")
assert _make_fallback_builder(cfg, print) is not None
cfg.translate.provider = "ollama"
assert _make_fallback_builder(cfg, print) is None, "本来就是本地后端，不需要兜底"
cfg.translate.provider = "deepseek"
cfg.translate.fallback_local = False
assert _make_fallback_builder(cfg, print) is None, "开关关闭时不应有兜底"
print("[PASS] 降级：连续2句失败切本地并补翻 / 恢复自动回切 / 启用条件正确")
