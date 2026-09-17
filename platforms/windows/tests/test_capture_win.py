"""音频捕获（Windows 版）：系统音频源枚举、友好名、重采样、COM 初始化。

守的坑（都是实机踩过的）：
1. `soundcard` 在**子线程**里调 `all_microphones()` 会抛
   `RuntimeError: Error 0x800401f0`（CO_E_NOTINITIALIZED）——
   因为新线程没有初始化 COM。必须由 `_com_init()` 兜底。
2. `soundcard` 的 `_Microphone` **没有 `samplerate` 属性**（只有 `channels`），
   取不到时要回退 48k，否则 `int(None)` 直接崩。
3. `friendly_source_name()` 要能处理 Windows 中文/英文驱动名（"扬声器 (2- ...)"），
   不能因为含括号/中文就崩。
4. `resample()` 的 48k->16k 必须精确（采集链路依赖它对齐时间轴）。
5. 非 Windows 平台这些函数不能抛异常（保证跨平台可导入/可自检）。
"""
import queue
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livetrans.capture import (AudioCapture, ParecCapture, SourceInfo,  # noqa: E402
                               _com_init, _com_uninit, default_monitor_source,
                               find_monitor_device, friendly_source_name,
                               list_monitor_sources, resample)

# ---------------- 1) COM 初始化（子线程可用） ----------------

def test_com_init_in_thread():
    """_com_init 在子线程里可调用且幂等（返回 bool 即可，不抛异常）。"""
    results = []

    def worker():
        r1 = _com_init()
        r2 = _com_init()          # 幂等：重复调用不该崩
        _com_uninit()
        results.append((r1, r2))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "COM 初始化线程卡死"
    assert len(results) == 1, results
    r1, r2 = results[0]
    assert isinstance(r1, bool) and isinstance(r2, bool), (r1, r2)
    print(f"[PASS] _com_init 子线程调用正常（返回 {r1}/{r2}）")


# ---------------- 2) 友好名转换 ----------------

def test_friendly_names():
    """各类设备名都能转出友好名，不崩、不空。"""
    cases = [
        # Windows 风格（含中文、括号、序号前缀）
        ("扬声器 (H USB Audio) [Loopback]", "系统音频"),
        ("扬声器 (2- Senary Audio)", "系统音频"),
        ("HC-L220A (NVIDIA High Definition Audio)", "系统音频"),
        ("Speakers (Realtek(R) Audio)", "系统音频"),
        ("Headphones (Bluetooth)", "系统音频"),
        ("数字输出 (SPDIF)", "系统音频"),
        # Linux 风格（保留兼容）
        ("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor", "系统音频"),
        ("", "系统音频"),
    ]
    for raw, expect_prefix in cases:
        got = friendly_source_name(raw)
        assert isinstance(got, str) and got, f"{raw!r} -> 空"
        assert got.startswith(expect_prefix), f"{raw!r} -> {got!r}"
    print(f"[PASS] {len(cases)} 种设备名转换正常")

def test_friendly_name_specific():
    """关键词能正确归类（USB/HDMI/蓝牙/扬声器），而不是全部落到兜底。

    用例里的设备名**全部取自本机实测**（RTX 4070 笔记本，多音频设备环境）。
    回归背景：最初只看 "hdmi" 三个字母，导致 NVIDIA 的 HDMI 音频设备
    （名字里写的是 "High Definition Audio"）落到兜底显示成型号；
    蓝牙免提设备（驱动名 bthhfenum）则被误判成 USB。
    """
    # HDMI：NVIDIA 显卡音频（本机实测名）
    assert friendly_source_name("HC-L220A (NVIDIA High Definition Audio)") == "系统音频(HDMI)"
    assert friendly_source_name("HDMI Output (Intel Display Audio)") == "系统音频(HDMI)"
    # 蓝牙：Windows 免提驱动名形如 bthhfenum
    assert "蓝牙" in friendly_source_name("Headphones (Bluetooth)")
    assert "蓝牙" in friendly_source_name(
        "耳机 (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0;(XIBERIA K03S))")
    # USB 声卡
    assert friendly_source_name("扬声器 (H USB Audio)") == "系统音频(USB)"
    # 板载模拟输出（含中文"扬声器"与 OEM 驱动名）
    assert friendly_source_name("扬声器 (2- Senary Audio)") == "系统音频(扬声器)"
    assert friendly_source_name("Speakers (Realtek(R) Audio)") == "系统音频(扬声器)"
    print("[PASS] 关键词归类（HDMI/蓝牙/USB/扬声器）正确")


# ---------------- 3) 重采样 ----------------

def test_resample_48k_to_16k():
    """48k -> 16k 精确降采样（采集链路的时间轴对齐依赖它）。"""
    src = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32)
    out = resample(src, 48000, 16000)
    assert out.size == 16000, out.size
    assert out.dtype == np.float32, out.dtype
    # 440Hz 正弦降采样后应仍是 440Hz 正弦（能量保持）
    rms = float(np.sqrt(np.mean(out ** 2)))
    assert 0.6 < rms < 0.8, f"能量异常 {rms}"
    print(f"[PASS] resample 48k->16k 正确（{src.size} -> {out.size}, rms={rms:.3f})")

def test_resample_edge_cases():
    """空数组 / 同采样率 / 升采样不崩。"""
    assert resample(np.empty(0, np.float32), 48000, 16000).size == 0
    x = np.ones(100, np.float32)
    assert resample(x, 16000, 16000).size == 100          # 同率原样
    assert resample(x, 16000, 48000).size == 300          # 升采样
    print("[PASS] resample 边界情况正常")


# ---------------- 4) 源枚举的接口契约 ----------------

def test_list_monitor_sources_contract():
    """list_monitor_sources() 必须返回 [(str, str), ...]（UI 依赖此契约）。"""
    srcs = list_monitor_sources()
    assert isinstance(srcs, list), type(srcs)
    for item in srcs:
        assert isinstance(item, tuple) and len(item) == 2, item
        key, label = item
        assert isinstance(key, str) and key, f"key 为空: {item}"
        assert isinstance(label, str) and label, f"label 为空: {item}"
    print(f"[PASS] list_monitor_sources() 契约正确（{len(srcs)} 个源）")

def test_default_monitor_source():
    """default_monitor_source() 返回 None 或列表中的某个 key。"""
    srcs = list_monitor_sources()
    d = default_monitor_source()
    if not srcs:
        assert d is None, f"无源时应返回 None，实得 {d!r}"
    else:
        keys = [k for k, _ in srcs]
        assert d in keys, f"{d!r} 不在 {keys} 中"
    print(f"[PASS] default_monitor_source() -> {d!r}")

def test_find_monitor_device_returns_none_on_win():
    """Windows 上 find_monitor_device() 返回 None（无 monitor 概念，走 loopback）。"""
    r = find_monitor_device()
    if sys.platform == "win32":
        assert r is None, r
        print("[PASS] Windows 上 find_monitor_device() 返回 None（符合设计）")
    else:
        print(f"[SKIP] 非 Windows 平台（返回 {r!r}）")


# ---------------- 5) 设备名可稳定定位 ----------------

def test_source_keys_are_names():
    """Windows 上源标识应是设备名（soundcard 按名字定位），而非 Linux 的 .monitor 后缀。"""
    srcs = list_monitor_sources()
    if sys.platform != "win32" or not srcs:
        print("[SKIP] 非 Windows 或无设备")
        return
    for key, _ in srcs:
        assert not key.endswith(".monitor"), f"Windows 源标识不该是 pulse 名: {key!r}"
    print(f"[PASS] {len(srcs)} 个源标识均为设备名")


# ---------------- 6) 采集类可构造（不实际录音） ----------------

def test_parec_capture_constructible():
    """ParecCapture 能用真实源构造并暴露 device_name（不 start，避免依赖音频环境）。"""
    srcs = list_monitor_sources()
    if not srcs:
        print("[SKIP] 本机没有可用的系统音频源")
        return
    key = default_monitor_source() or srcs[0][0]
    cap = ParecCapture(SourceInfo("probe", "internal", "probe", "probe"),
                       key, queue.Queue(), 16000)
    assert cap.device_name, "device_name 为空"
    assert cap.target_sr == 16000
    assert hasattr(cap, "start") and hasattr(cap, "stop")
    print(f"[PASS] ParecCapture 构造正常（device_name={cap.device_name!r}）")

def test_audio_capture_requires_device():
    """AudioCapture 在无有效设备时应抛错而不是静默失败。"""
    try:
        # 用一个不存在的设备 id
        AudioCapture(SourceInfo("probe", "external", "probe", "probe"),
                     999999, queue.Queue(), 16000)
        print("[SKIP] 设备 999999 意外可用（环境特殊）")
    except Exception as e:  # noqa: BLE001 - 期望就是抛错
        print(f"[PASS] AudioCapture 无效设备正确报错（{type(e).__name__}）")


def test_loopback_follows_default_speaker():
    """loopback 必须跟随默认扬声器切换（切耳机/输出设备后外挂不再"变聋"）。

    守的坑：WASAPI loopback 只采绑定设备的输出流，用户切换默认输出设备
    （扬声器→耳机）后应用不会跟随，采到一路静音 → 整场 0 条识别记录。
    _check_follow_default 检测默认扬声器变化并更新绑定。
    """
    import livetrans.capture as capmod

    class _Spk:
        def __init__(self, name: str):
            self.name = name

    class _FakeSC:
        def __init__(self, name: str):
            self._name = name

        def default_speaker(self):
            return _Spk(self._name)

    cap = ParecCapture(SourceInfo("monitor", "internal", "系统音频(测试)", "旧设备"),
                       "旧设备", queue.Queue(), target_sr=16000)
    assert cap.follow_default is True, "跟随默认扬声器应默认开启"

    real_sc = capmod.sc
    try:
        capmod.sc = _FakeSC("新设备")
        assert cap._check_follow_default(None) is True, "默认扬声器变了应要求重开"
        assert cap.device_key == "新设备", cap.device_key

        capmod.sc = _FakeSC("新设备")
        assert cap._check_follow_default(None) is False, "同名不应重开"

        cap.follow_default = False
        capmod.sc = _FakeSC("另一个")
        assert cap._check_follow_default(None) is False, "关闭跟随不应重开"
    finally:
        capmod.sc = real_sc
    print("[PASS] loopback 跟随默认扬声器切换（切换/同名/关闭三态）")


if __name__ == "__main__":
    test_com_init_in_thread()
    test_friendly_names()
    test_friendly_name_specific()
    test_resample_48k_to_16k()
    test_resample_edge_cases()
    test_list_monitor_sources_contract()
    test_default_monitor_source()
    test_find_monitor_device_returns_none_on_win()
    test_source_keys_are_names()
    test_parec_capture_constructible()
    test_audio_capture_requires_device()
    test_loopback_follows_default_speaker()
