"""镜像模式：跟随主程序写出的会话 JSONL，只显示、不重复识别/翻译。

从 overlay.py 抽出（Windows 版）——这段逻辑与 GUI 工具包无关，
放在 overlay.py 里会让「只测 JSONL 跟随」的用例被迫依赖 GTK/Cairo，
在没装 GTK 的机器上直接 ImportError。

抽出的好处：
- 纯逻辑可独立测试（tests/test_mirror.py 不再需要 GTK）；
- W3 用 Qt 重写 overlay 时，这段逻辑原样复用，不随 GUI 一起重写。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from .subtitle import DisplayItem


def _log(msg: str) -> None:
    """带时间戳打印（与 overlay.py 的日志格式保持一致）。"""
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class SessionMirror(threading.Thread):
    """镜像模式：跟随主程序写出的会话 JSONL，只显示、不重复识别/翻译。

    主程序（`保存并启动`）每翻译完一句就往 `sessions/session-*.jsonl` 追加一行，
    这里只读新行并上屏 —— 省掉一整条 API/算力开销（外挂自己跑管线时是双份消耗）。
    会话文件换新的（主程序重启）会自动跟着切。
    """

    POLL = 0.4

    def __init__(self, log_dir: Path, overlay, stop: threading.Event):
        super().__init__(daemon=True, name="session-mirror")
        self.log_dir = Path(log_dir)
        self.overlay = overlay
        self.stop = stop
        self._fh = None                  # 当前跟随的文件句柄
        self._path: Path | None = None
        self._n = 0

    # ---- 文件跟随 ----

    def _newest(self) -> Path | None:
        """最新一场主程序会话（忽略 overlay-*.jsonl 自己写的）。"""
        if not self.log_dir.is_dir():
            return None
        files = [f for f in self.log_dir.glob("session-*.jsonl")
                 if not f.name.startswith("overlay-")]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _open(self, path: Path, at_end: bool) -> bool:
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError as e:  # noqa: BLE001
            _log(f"镜像模式：打不开 {path.name}: {e}")
            return False
        if at_end:
            fh.seek(0, 2)                # 首次跟随：只看之后新增的行
        self._close()
        self._fh, self._path = fh, path
        _log(f"镜像模式：跟随 {path.name}")
        self.overlay.set_status("镜像模式：显示主程序（保存并启动）的字幕")
        return True

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None

    # ---- 主循环 ----

    def run(self) -> None:
        while not self.stop.is_set():
            newest = self._newest()
            if newest is None:
                self.overlay.set_status("镜像模式：等待主程序启动（保存并启动）…")
                if self.stop.wait(self.POLL * 2):
                    break
                continue
            if self._path is None or newest != self._path:
                # 首次跟随看文件尾（不重放历史）；换会话则从头读
                self._open(newest, at_end=self._path is None)
            if self._fh is not None:
                self._drain()
            if self.stop.wait(self.POLL):
                break
        self._close()

    def _drain(self) -> None:
        for line in self._fh:
            line = line.strip()
            if not line or getattr(self.overlay, "paused", False):
                continue                      # 暂停：只推进不显示
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = row.get("translation") or row.get("text") or ""
            if not src:
                continue
            self._n += 1
            self.overlay.post(DisplayItem(
                kind=row.get("kind") or "internal",
                label=row.get("label", ""), app=row.get("app", ""),
                text=row.get("text", ""), translation=row.get("translation"),
                item_id=f"mirror-{self._n}", ts=time.time(),
                role=row.get("role", ""), speaker=row.get("speaker", "")))
