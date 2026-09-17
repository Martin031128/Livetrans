"""上下文连续段落模式：分段判定 / 段落渲染 / 尾部修订 / 管线集成。

守的坑：
1. 段落 = 一个显示条目，句子流式合入；修订把最近 N 句合并成连贯文本，
   新句到达后必须正确地"并进修订区"或"留在区外单独显示"（tail 区间管理）；
2. 长停顿（gap_ms > 阈值）必须分段——否则上下文跨话题黏成一坨；
3. 会话 jsonl 仍按句落盘（摘要/导出依赖），段落只是显示层概念；
4. 外挂独立管线复用 TranslatorWorker，段落模式对它自动生效（无需改 overlay）。
"""
import json
import queue
import threading
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.asr import SegmentEvent  # noqa: E402
from livetrans.main import (_join_parts, _para_text, _Para,  # noqa: E402
                            MAX_PARA_SENTS, TranslatorWorker)


# ---------------- 1) 拼接规则 ----------------

def test_join_parts():
    assert _join_parts(["依赖一本书", "和你", "我们是作者"]) == "依赖一本书和你我们是作者"
    assert _join_parts(["we're the author", "of your story"]) == \
        "we're the author of your story"
    assert _join_parts([]) == ""
    assert _join_parts(["…", "好"]) == "…好"        # 占位符不参与语言判定
    print("[PASS] 段落拼接：CJK 直连 / 西文空格")


# ---------------- 2) 段落渲染与修订区间 ----------------

def _mk_para() -> _Para:
    return _Para(para_id="p1", ts=1.0, kind="internal", label="l", app="a",
                 role="", srcs=[], dsts=[])


def test_para_render_and_revise():
    p = _mk_para()
    # 三句依次翻译完成（未修订：按句显示）
    for src, dst in [("依赖一本书", "T1"), ("和你", "T2"), ("我们是作者", "T3")]:
        p.srcs.append(src)
        p.dsts.append(dst)
    assert _para_text(p) == "T1 T2 T3"
    # 未完成句用 … 占位
    p.dsts[2] = None
    assert _para_text(p) == "T1 T2 …"
    p.dsts[2] = "T3"
    # 整段修订：结果原地替换全部 3 句
    p.revised, p.revised_cover = "REV123", 3
    assert _para_text(p) == "REV123"
    # 修订之后新增的句子仍按句显示（流式 … / 最终值）
    p.srcs.append("第四句")
    p.dsts.append(None)
    assert _para_text(p) == "REV123 …"
    p.dsts[3] = "T4"
    assert _para_text(p) == "REV123 T4"
    # 下一次修订把新句并进去（整段重写）
    p.revised, p.revised_cover = "REV1234", 4
    assert _para_text(p) == "REV1234"
    print("[PASS] 段落渲染/整段修订：追加-占位-原地替换 状态机正确")


def test_segment_event_gap_default():
    ev = SegmentEvent(source_key="k", kind="internal", label="l", app="a",
                      text="x", ts=1.0)
    assert ev.gap_ms == 0.0
    print("[PASS] SegmentEvent.gap_ms 默认 0（旧调用方兼容）")


# ---------------- 3) 管线集成（假窗口/假翻译器） ----------------

class FakeWin:
    def __init__(self):
        self._lock = threading.Lock()
        self.items = []
        self.src = {}
        self.dst = {}

    def post(self, item):
        with self._lock:
            self.items.append(item)

    def update_translation(self, item_id, text, llm_ms=None):
        with self._lock:
            self.dst[item_id] = text

    def update_source(self, item_id, text):
        with self._lock:
            self.src[item_id] = text

    def set_status(self, _s):
        pass


class FakeTranslator:
    class _Client:
        def close(self):
            pass

    class _Cfg:
        contextual = True
        paragraph_gap_ms = 3500
        revise_depth = 2
        llm_concurrency = 1
        source_lang = None
        target_lang = "中文"

    def __init__(self):
        self.cfg = self._Cfg()
        self.client = self._Client()
        self.backend, self.model = "fake", "fake"
        self.revise_calls = []

    def translate_stream(self, text, on_delta=None, raw_hook=None):
        out = f"T[{text}]"
        if on_delta is not None:
            on_delta(out)
        return out

    def revise(self, sources, current, anchor=""):
        self.revise_calls.append((list(sources), current, anchor))
        return "R[" + "|".join(sources) + "]"


def _ev(text: str, ts: float, gap_ms: float = 0.0) -> SegmentEvent:
    return SegmentEvent(source_key="sys", kind="internal", label="系统音频",
                        app="", text=text, ts=ts, gap_ms=gap_ms)


def test_worker_paragraph_pipeline(tmp: Path):
    """集成：同段合段 / 长停顿分段 / 尾部修订 / 会话日志仍按句落盘。"""
    win = FakeWin()
    tr = FakeTranslator()
    log_path = tmp / "session.jsonl"
    stop = threading.Event()
    seg_q: "queue.Queue[SegmentEvent]" = queue.Queue()
    w = TranslatorWorker(seg_q, win, tr, log_path, stop)
    w.start()
    try:
        # 段 1：两句短停顿合段；段 2：4s 长停顿后另起一段，同样两句
        seg_q.put(_ev("依赖一本书", ts=100.0, gap_ms=0.0))
        seg_q.put(_ev("和你", ts=103.0, gap_ms=800.0))
        seg_q.put(_ev("我们是作者", ts=110.0, gap_ms=4000.0))   # > 3.5s → 分段
        seg_q.put(_ev("和主角", ts=112.0, gap_ms=800.0))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with win._lock:
                n_para = len(win.items)
                revised = [d for d in win.dst.values()
                           if isinstance(d, str) and d.startswith("R[")]
            done = (n_para == 2 and len(revised) >= 2
                    and len(tr.revise_calls) >= 2)
            if done:
                break
            time.sleep(0.05)
        assert time.monotonic() < deadline, "段落管线未在超时前完成"

        with win._lock:
            para_ids = [i.item_id for i in win.items]
            assert len(para_ids) == 2, f"应有 2 个段落条目: {len(win.items)}"
            assert win.src[para_ids[0]] == "依赖一本书和你", win.src
            assert win.src[para_ids[1]] == "我们是作者和主角", win.src
            # 段落译文被修订结果替换（假翻译器把两句合并）
            assert win.dst[para_ids[0]].startswith("R["), win.dst[para_ids[0]]
            assert win.dst[para_ids[1]].startswith("R["), win.dst[para_ids[1]]
        # 修订调用内容：第一段的修订输入应是那两句的原始识别
        assert tr.revise_calls[0][0] == ["依赖一本书", "和你"], tr.revise_calls
        # 会话日志：仍按句落盘（4 句）
        recs = [json.loads(line) for line in
                log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(recs) == 4, f"会话日志应 4 条（按句）: {len(recs)}"
        assert [r["text"] for r in recs] == \
            ["依赖一本书", "和你", "我们是作者", "和主角"]
    finally:
        stop.set()
        w.join(timeout=5)
        assert not w.is_alive(), "worker 未退出"
    print("[PASS] 管线集成：合段/分段/修订/日志按句落盘")


if __name__ == "__main__":
    import tempfile
    test_join_parts()
    test_para_render_and_revise()
    test_segment_event_gap_default()
    with tempfile.TemporaryDirectory() as d:
        test_worker_paragraph_pipeline(Path(d))
    print("ALL PASS")
