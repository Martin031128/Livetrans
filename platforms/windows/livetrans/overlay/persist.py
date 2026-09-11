"""外挂配置落盘：统一走 livetrans.configstore 写入层。"""
from __future__ import annotations

from pathlib import Path

from .. import configstore
from ._common import _log


def persist_overlay(cfg_path: Path | None, patch: dict) -> None:
    """把外挂的几何/透明度写回 config.yaml 的 overlay 段（统一走配置写入层）。"""
    try:
        configstore.patch_section(cfg_path, "overlay", patch)
    except (OSError, yaml.YAMLError) as e:  # noqa: BLE001
        _log(f"保存外挂设置失败: {e}")

