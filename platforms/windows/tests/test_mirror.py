"""镜像模式跟随器：只读主程序会话 JSONL，不重复识别/翻译。"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 从 .mirror 导入（纯逻辑模块）——不经过 overlay，避免连累 GTK/Cairo 依赖。
# Windows 版：overlay.py 仍是 GTK 实现（W3 才换 Qt），此处解耦后镜像用例可独立运行。
from livetrans.mirror import SessionMirror  # noqa: E402


class FakeOverlay:
    def __init__(self):
        self.items = []
        self.status = ""
        self.paused = False

    def post(self, item):
        self.items.append(item)

    def set_status(self, text):
        self.status = text


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    log_dir = Path(td)
    first = log_dir / "session-20260911-000001.jsonl"
    first.write_text('{"text":"旧内容","translation":"不该显示"}\n',
                     encoding="utf-8")          # 启动前已有的内容：不重放
    ov = FakeOverlay()
    stop = threading.Event()
    mir = SessionMirror(log_dir, ov, stop)
    mir.start()
    time.sleep(1.2)
    print("启动后（应为空）:", len(ov.items), "| 状态:", ov.status)
    assert ov.items == []

    with first.open("a", encoding="utf-8") as f:      # 模拟主程序翻译完两句
        f.write(json.dumps({"kind": "internal", "label": "系统音频", "app": "",
                            "text": "hello", "translation": "你好",
                            "speaker": "S1", "role": "other"},
                           ensure_ascii=False) + "\n")
        f.write(json.dumps({"kind": "external", "label": "麦克风", "app": "",
                            "text": "再见", "translation": "bye"},
                           ensure_ascii=False) + "\n")
        f.write("{坏行}\n")                            # 坏行要跳过
    time.sleep(1.2)
    print("新行 -> 条目:", [(i.kind, i.text, i.translation, i.speaker, i.role)
                            for i in ov.items])
    assert len(ov.items) == 2, ov.items
    assert ov.items[0].translation == "你好" and ov.items[0].speaker == "S1"
    assert ov.items[0].role == "other" and ov.items[1].kind == "external"

    # 暂停时不显示（但继续推进文件位置）
    ov.paused = True
    with first.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"text": "paused", "translation": "暂停中"},
                           ensure_ascii=False) + "\n")
    time.sleep(1.0)
    assert len(ov.items) == 2, "暂停时不应上屏"
    ov.paused = False

    # 主程序重开会话（新文件）-> 自动跟随新文件
    second = log_dir / "session-20260911-000002.jsonl"
    second.write_text(json.dumps({"text": "新会话", "translation": "新会话译文"},
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    time.sleep(1.6)
    print("换会话后:", [(i.text, i.translation) for i in ov.items])
    assert any(i.translation == "新会话译文" for i in ov.items), ov.items
    stop.set()
    mir.join(timeout=3)
print("[PASS] 镜像模式：不重放历史 / 新行上屏 / 坏行跳过 / 暂停不上屏 / 换会话自动跟随")
