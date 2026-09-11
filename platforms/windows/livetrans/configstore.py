"""config.yaml 的唯一写入层。

历史上外挂（livetrans.overlay.persist_overlay）、主程序（main._persist_
subtitle_style / _persist_dialog）、控制台（launcher._apply_and_save）各自
"读-改-写"整份 config.yaml：控制台常驻、外挂/主程序独立进程，后写者拿
启动时的旧快照整份 dump，会把别人刚存的键覆盖掉（"外挂配色/位置丢失"
即此通病）。收口后所有写盘走本模块：

- patch_section：读 → 合并单段 → 写回（写前重读磁盘基线，跨进程不互踩）；
- refresh_sections：整份重写方保存前，用磁盘最新覆盖自己没动过的段。

线程锁只保护进程内并发；跨进程靠"写前重读基线"把竞态窗口收窄到最小。
"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

_LOCK = threading.Lock()

_HEADER = ("# LiveTrans 配置（完整注释见 config.example.yaml；"
           "各段由对应界面自动维护）\n")


def read_section(cfg_path: str | Path | None, section: str) -> dict:
    """读 config.yaml 的某个段（文件缺失/损坏/段缺失返回空 dict）。"""
    if not cfg_path:
        return {}
    p = Path(cfg_path)
    try:
        if not p.is_file():
            return {}
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        sec = raw.get(section)
        return dict(sec) if isinstance(sec, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def patch_section(cfg_path: str | Path | None, section: str,
                  patch: dict) -> None:
    """读 → 合并 section 段 → 写回（只动这一段，其余段原样保留）。

    写前重读磁盘基线：外挂/主程序/控制台三个进程并发写不会互相覆盖。
    异常（OSError/YAMLError）向上抛，由调用方按各自语境记日志。
    """
    if not cfg_path or not patch:
        return
    p = Path(cfg_path)
    with _LOCK:
        raw: dict = {}
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        sec = raw.get(section)
        merged = dict(sec) if isinstance(sec, dict) else {}
        merged.update(patch)
        raw[section] = merged
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_HEADER
                     + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")


def refresh_sections(cfg: dict, sections: tuple[str, ...],
                     cfg_path: str | Path | None) -> None:
    """用磁盘最新覆盖 cfg 里指定的段（供整份重写方保存前拉新）。

    只覆盖磁盘上真实存在的段——控制台没管过、而外挂/主程序运行中写过的
    段（如 subtitle/dialog/overlay），不会被控制台的启动快照冲回旧值。
    """
    for s in sections:
        disk = read_section(cfg_path, s)
        if disk:
            cfg[s] = disk
