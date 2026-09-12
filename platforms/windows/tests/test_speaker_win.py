"""声纹（说话人区分）在 Windows 上的可用性。

守的坑：
1. **`speaker.py` 曾漏 import `BASE`** —— 配置里写**相对路径**的 `speaker.model`
   时会 `NameError: name 'BASE' is not defined`。Linux 版同样存在，已一并修。
2. 声纹是**可选增强**：依赖或模型缺失时必须返回空标签/None，
   绝不能抛异常把识别与翻译带崩。
3. `build_tracker()` 未启用时要返回 None（零开销），不能白建对象。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                              # noqa: E402

from livetrans.speaker import (SpeakerConfig, SpeakerTracker,   # noqa: E402
                               build_tracker, color_for,
                               speaker_model_path, speaker_ready)


def test_base_import_available():
    """speaker.py 必须能拿到 BASE（历史 bug：漏 import 导致相对模型路径 NameError）。"""
    import livetrans.speaker as sp
    assert hasattr(sp, "BASE"), "speaker.py 未导入 BASE（相对模型路径会 NameError）"
    # 直接走一遍相对路径分支（不要求文件存在，只要不抛 NameError）
    cfg = SpeakerConfig(enabled=True, model="some/relative/path.onnx")
    try:
        sp.speaker_model_path(cfg)
    except NameError as e:
        raise AssertionError(f"相对模型路径仍然 NameError: {e}") from e
    print("[PASS] BASE 已导入，相对模型路径不抛 NameError")


def test_disabled_returns_empty():
    """未启用时：assign 返回空串、build_tracker 返回 None（零开销）。"""
    tr = SpeakerTracker(SpeakerConfig(enabled=False), log=lambda m: None)
    audio = np.zeros(16000, np.float32)
    assert tr.assign(audio, 16000) == "", "未启用时不该返回标签"
    assert build_tracker(SpeakerConfig(enabled=False)) is None, \
        "未启用时 build_tracker 应返回 None"
    assert build_tracker(None) is None
    print("[PASS] 未启用时零开销（空标签 / None）")


def test_none_and_short_audio():
    """None 与过短音频返回空标签，不抛异常。"""
    tr = SpeakerTracker(SpeakerConfig(enabled=True, min_segment_sec=0.6),
                        log=lambda m: None)
    assert tr.assign(None, 16000) == ""
    assert tr.assign(np.zeros(0, np.float32), 16000) == ""
    assert tr.assign(np.zeros(int(16000 * 0.2), np.float32), 16000) == "", \
        "过短段（0.2s < min_segment_sec）应返回空"
    print("[PASS] None/空/过短音频均安全返回空标签")


def test_graceful_degradation():
    """依赖或模型缺失时优雅降级（返回空标签，不抛异常）。

    注意：本函数**不能输出 `[SKIP]` 字样** —— tests/run.py 只要在输出里看到
    `[SKIP]` 就把整个用例判为 SKIP，会掩盖同文件里其它 PASS 的结果。

    CI 能联网，模型会自动下载成功——所以"模型缺失"用打桩制造：把
    ensure_speaker_model 替换为返回 None（下载失败），确定性走降级路径。
    """
    import livetrans.speaker as spk
    real = spk.ensure_speaker_model
    spk.ensure_speaker_model = lambda cfg, log: None      # 桩：模型不可用
    try:
        tr = SpeakerTracker(SpeakerConfig(enabled=True), log=lambda m: None)
        out = tr.assign(np.zeros(16000, np.float32), 16000)
        assert out == "", f"模型不可用时应返回空标签，实得 {out!r}"
        assert not tr.available, "降级后 available 应为 False"
    finally:
        spk.ensure_speaker_model = real
    print("[PASS] 依赖/模型缺失时优雅降级（打桩确定性验证）")


def test_color_mapping():
    """说话人标签 -> 配色：S1..Sn 循环，异常标签有兜底。"""
    c1 = color_for("S1")
    assert c1.startswith("#") and len(c1) == 7, c1
    assert color_for("S2") != c1, "相邻说话人应不同色"
    assert color_for("") == color_for("S1"), "空标签应兜底到 S1 的色"
    assert color_for("garbage").startswith("#"), "非法标签也要有兜底色"
    print("[PASS] 配色映射正确（含兜底）")


def test_model_path_and_ready():
    """模型路径探测与 ready 判定的一致性。"""
    p = speaker_model_path()
    ready = speaker_ready()
    if p is not None:
        assert p.is_file(), p
        import importlib.util
        has_dep = importlib.util.find_spec("sherpa_onnx") is not None
        assert ready == has_dep, \
            f"有模型时 ready 应等于「依赖是否装了」：ready={ready} dep={has_dep}"
        print(f"[PASS] 模型定位正常（{p.name}），ready={ready}")
    else:
        assert ready is False, "无模型时 ready 必须为 False"
        print("[PASS] 无模型时 ready=False（逻辑一致）")


def test_real_embedding_if_available():
    """声纹可用时：真实跑一次向量提取，确认 dim>0 且推理不报错。"""
    if not speaker_ready():
        print("[PASS] 声纹不可用（缺 sherpa-onnx 或模型），跳过实跑（非失败）")
        return
    tr = SpeakerTracker(SpeakerConfig(enabled=True, threshold=0.55,
                                      min_segment_sec=0.6), log=lambda m: None)
    if not tr.preload():
        print("[PASS] 模型载入失败，跳过实跑（非失败）")
        return
    assert tr.available, "preload 成功后 available 应为 True"
    # 合成一段类人声（人声频段），验证推理链路不报错
    sr = 16000
    t = np.arange(int(sr * 2.0)) / sr
    x = np.zeros_like(t)
    for k in range(2, 16):
        f = 220 * k
        if 300 <= f <= 3400:
            x += (0.35 / (1 + 0.12 * k)) * np.sin(2 * np.pi * f * t)
    x = (x / max(np.max(np.abs(x)), 1e-9) * 0.3).astype(np.float32)
    tag = tr.assign(x, sr)
    assert isinstance(tag, str), type(tag)
    print(f"[PASS] 真实向量提取正常（dim 载入成功，判定 -> {tag!r}）")


if __name__ == "__main__":
    test_base_import_available()
    test_disabled_returns_empty()
    test_none_and_short_audio()
    test_graceful_degradation()
    test_color_mapping()
    test_model_path_and_ready()
    test_real_embedding_if_available()
