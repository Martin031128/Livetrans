#!/usr/bin/env python3
"""configstore：配置唯一写入层的回归测试。

守的坑：config.yaml 曾有三个独立"读-改-写"方（外挂/主程序/控制台），
后写者拿启动快照整份 dump，把别人刚存的键覆盖掉（外挂配色/位置丢失）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans import configstore                 # noqa: E402
from livetrans.overlay import persist_overlay     # noqa: E402


def test_patch_section_merges_and_keeps_other_sections():
    """patch 只动目标段，其余段原样保留。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        p.write_text("translate:\n  provider: cloud\n"
                     "subtitle:\n  font_size: 20\n", encoding="utf-8")
        configstore.patch_section(p, "overlay", {"pos_x": 10, "pos_y": 5})
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert raw["translate"]["provider"] == "cloud", raw
        assert raw["subtitle"]["font_size"] == 20, raw
        assert raw["overlay"]["pos_x"] == 10 and raw["overlay"]["pos_y"] == 5


def test_two_writers_do_not_lose_each_other():
    """模拟外挂/主程序交替写：后写者不得丢掉先写者的键。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        configstore.patch_section(p, "overlay", {"pos_x": 1, "pos_y": 2})
        configstore.patch_section(p, "subtitle", {"font_size": 30})
        configstore.patch_section(p, "overlay", {"opacity": 0.5})
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert raw["overlay"]["pos_x"] == 1, raw
        assert raw["overlay"]["opacity"] == 0.5, raw
        assert raw["subtitle"]["font_size"] == 30, raw


def test_patch_section_merges_into_existing_section():
    """段已存在时是合并而非替换（保留段里没被 patch 的键）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        configstore.patch_section(p, "overlay", {"pos_x": 3, "opacity": 0.9})
        configstore.patch_section(p, "overlay", {"opacity": 0.8})
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert raw["overlay"]["pos_x"] == 3, raw
        assert raw["overlay"]["opacity"] == 0.8, raw


def test_persist_overlay_delegates_to_configstore():
    """persist_overlay 兼容委托：写入行为与收口前一致；None 路径安全跳过。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        persist_overlay(p, {"pos_x": 7, "bg_color": "#112233"})
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert raw["overlay"]["pos_x"] == 7, raw
        assert raw["overlay"]["bg_color"] == "#112233", raw
    persist_overlay(None, {"pos_x": 7})          # 不抛即通过


def test_refresh_sections_pulls_disk_truth():
    """整份重写方保存前拉新：磁盘有的段覆盖快照，磁盘没有的保持原样。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        p.write_text("subtitle:\n  font_size: 44\n", encoding="utf-8")
        cfg = {"subtitle": {"font_size": 20}, "translate": {"provider": "x"}}
        configstore.refresh_sections(cfg, ("subtitle", "dialog"), p)
        assert cfg["subtitle"]["font_size"] == 44, cfg
        assert "dialog" not in cfg, cfg          # 磁盘没有的段不动
        assert cfg["translate"]["provider"] == "x"


if __name__ == "__main__":
    test_patch_section_merges_and_keeps_other_sections()
    test_two_writers_do_not_lose_each_other()
    test_patch_section_merges_into_existing_section()
    test_persist_overlay_delegates_to_configstore()
    test_refresh_sections_pulls_disk_truth()
    print("[PASS] configstore：合并/并发不丢键/兼容委托/拉新 全部通过")
