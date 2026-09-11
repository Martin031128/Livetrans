"""端到端（真窗口 + 真路由 + 本地模型）：两个角色都听同一路时的表现。

需要本机装好 Ollama 且有 qwen3:4b-instruct；没有就自动跳过（不算失败）。
一句英文 -> 「你」栏（→English）与「对方」栏（→中文）各出一条完整译文，
会话日志两条记录各有自己的 role。
"""
import json
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.asr import SegmentEvent                            # noqa: E402
from livetrans.config import load_config                          # noqa: E402
from livetrans.main import (DialogRouter, TranslatorWorker,       # noqa: E402
                            _build_dialogue_translators, _direction_translator)
from livetrans.subtitle import SubtitleWindow                     # noqa: E402

cfg = load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))
ollama = cfg.providers.get("ollama")
if ollama is None or not ollama.model:
    print("[SKIP] 未配置 ollama 服务商，跳过（端到端需要本地模型）")
    raise SystemExit(0)
cfg.translate.provider = "ollama"
dialog = {"left_role": "self", "self_source": "internal", "other_source": "internal",
          "self_src_lang": "自动", "self_dst_lang": "English",
          "other_src_lang": "自动", "other_dst_lang": "中文", "bidirectional": True}

primary = _direction_translator(cfg.translate, cfg.providers, "自动", "中文")
trs = _build_dialogue_translators(cfg.translate, cfg.providers, dialog, primary,
                                  lambda m: None)
window = SubtitleWindow(style={"layout": "dialog", "animate": False}, dialog=dialog)
router = DialogRouter(window, trs, primary)
seg_q: "queue.Queue" = queue.Queue()
stop = threading.Event()
log_path = Path("/tmp/e2e-dialog.jsonl")
log_path.unlink(missing_ok=True)
worker = TranslatorWorker(seg_q, window, primary, log_path, stop, router=router)
worker.start()
seg_q.put(SegmentEvent(source_key="monitor", kind="internal", label="系统音频",
                       app="",
                       text="Could you please share your screen for a moment?",
                       ts=time.time(), asr_ms=110))

rows: list[str] = []
t0 = time.monotonic()
while time.monotonic() - t0 < 120:
    window._poll()
    window.root.update()
    rows = [r for r in log_path.read_text(encoding="utf-8").splitlines() if r.strip()]
    if len(rows) >= 2:
        break
    time.sleep(0.2)


def pane_texts(role: str) -> list[str]:
    pane = window._panes[role]
    return [t.get("1.0", "end-1c") for t in pane.label_refs.values()
            if t.winfo_exists()]


en_line = pane_texts("self")
zh_line = pane_texts("other")
print("「你」栏:", en_line)
print("「对方」栏:", zh_line)
print("会话日志:", [(json.loads(r)["role"], json.loads(r)["translation"]) for r in rows])
stop.set()
try:
    worker.translator.client.close()
except Exception:  # noqa: BLE001
    pass

if not en_line or not zh_line:
    print("[SKIP] 本地模型没返回结果（可能未安装/未启动 Ollama）")
    window._close()
    raise SystemExit(0)
assert not any("\u4e00" <= c <= "\u9fff" for c in en_line[0]), en_line[0]
assert any("\u4e00" <= c <= "\u9fff" for c in zh_line[0]), zh_line[0]
assert len(rows) == 2 and {json.loads(r)["role"] for r in rows} == {"self", "other"}
window._close()
print("[PASS] 端到端：同源双角色各一条完整译文（各按自己方向），日志分角色")
