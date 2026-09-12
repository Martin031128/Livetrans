"""对话模式路由：同源两角色各翻一条 / 按角色暂停 / 无人使用则丢弃 / 上下文隔离。

不需要网络与音频：用假翻译器 + 假窗口，直接驱 TranslatorWorker 的单句任务。
"""
import queue
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.asr import SegmentEvent                     # noqa: E402
from livetrans.main import DialogRouter, TranslatorWorker  # noqa: E402
from livetrans.subtitle import DisplayItem                 # noqa: E402


class FakeClient:
    def close(self):
        pass


class FakeTr:
    def __init__(self, tag):
        self.tag, self.client = tag, FakeClient()
        self.backend, self.model = "fake", tag
        self.cfg = SimpleNamespace(llm_concurrency=2)
        self.ctx = SimpleNamespace(items=[])
        self.ctx.append = lambda s, d: self.ctx.items.append((s, d))

    def translate_stream(self, text, on_delta=None, raw_hook=None):
        if on_delta:
            on_delta(f"<{self.tag}>")
        if raw_hook:
            raw_hook(f"raw:{self.tag}")
        return f"[{self.tag}]{text}"


class FakeWin:
    def __init__(self, dialog):
        self.dialog, self.paused_roles = dialog, set()
        self.items, self.updates = [], []

    def post(self, item):
        self.items.append(item)

    def update_translation(self, iid, text, llm_ms=None):
        self.updates.append((iid, text))


def run_events(dialog, events, paused=()):
    """手动喂事件（不启线程）：返回 (窗口, 角色翻译器表)。"""
    win = FakeWin(dialog)
    win.paused_roles = set(paused)
    primary = FakeTr("primary")
    trs = {"self": FakeTr("self-tr"), "other": FakeTr("other-tr")}
    router = DialogRouter(win, trs, primary)
    seg_q: "queue.Queue" = queue.Queue()
    stop = threading.Event()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        log_path = Path(td) / "s.jsonl"
        worker = TranslatorWorker(seg_q, win, primary, log_path, stop, router=router)
        log = open(log_path, "a", encoding="utf-8")
        for ev in events:
            seg_q.put(ev)
        while not seg_q.empty():
            ev = seg_q.get()
            for role in worker._roles_for(ev):
                seq = worker._seq
                worker._seq += 1
                iid = f"{ev.source_key}-{ev.ts:.3f}-{seq}" + (f"-{role}" if role else "")
                win.post(DisplayItem(kind=ev.kind, label=ev.label, app=ev.app,
                                     text=ev.text, translation=None, item_id=iid,
                                     ts=ev.ts, asr_ms=ev.asr_ms, role=role or ""))
                worker._translate_task(seq, ev, iid, log, role)
        log.close()
    return win, trs


ev = SegmentEvent(source_key="mic", kind="external", label="l", app="",
                  text="你好", ts=1.0, asr_ms=20)

# 1) 两个角色都听麦克风 -> 同一句各翻一条（各自翻译器）
win, trs = run_events({"self_source": "external", "other_source": "external",
                       "self_dst_lang": "English", "other_dst_lang": "中文"}, [ev])
roles = sorted(i.role for i in win.items)
print("同源两条:", roles)
assert len(win.items) == 2 and roles == ["other", "self"]
finals = {u[0]: u[1] for u in win.updates if u[1].startswith("[")}
assert finals["mic-1.000-0-self"] == "[self-tr]你好"
assert finals["mic-1.000-1-other"] == "[other-tr]你好"

# 2) 暂停「对方」-> 只出「你」那条
win, _ = run_events({"self_source": "external", "other_source": "external"},
                    [ev], paused=("other",))
print("暂停 other ->", [i.role for i in win.items])
assert len(win.items) == 1 and win.items[0].role == "self"

# 3) 两个角色都听系统声音 -> 麦克风的话没人要，直接丢弃
win, _ = run_events({"self_source": "internal", "other_source": "internal"}, [ev])
print("麦克风无人使用 ->", len(win.items), "条")
assert win.items == [] and win.updates == []

# 4) 上下文按角色隔离
win, trs = run_events({"self_source": "external", "other_source": "internal"}, [ev])
assert trs["self"].ctx.items == [("你好", "[self-tr]你好")]
assert trs["other"].ctx.items == []

# 5) 换来源后同一角色继续用原翻译器（上下文不断）
win2, trs2 = run_events({"self_source": "internal", "other_source": "external"},
                        [ev, SegmentEvent(source_key="mon", kind="internal",
                                          label="l", app="", text="Hi",
                                          ts=2.0, asr_ms=20)])
print("换来源后 self ctx:", trs2["self"].ctx.items)
assert trs2["self"].ctx.items == [("Hi", "[self-tr]Hi")]

print("[PASS] 路由：同源各翻一条 / 按角色暂停 / 无人使用丢弃 / 上下文按角色隔离")
