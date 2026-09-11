"""音频捕获层：通过 PortAudio 打开麦克风（外部音频），
通过 WASAPI loopback 捕获系统混音（内部音频）。

每路捕获输出 16kHz 单声道 float32 块。按应用拆流（R5）在 M2 实现：
Windows 用 Process Loopback，浏览器用扩展推流。

Windows 版与 Linux 版的差异（对外接口完全一致，调用方无需区分）：
- 麦克风：两边都是 sounddevice（PortAudio），代码通用。
- 系统音频：Linux 是 PulseAudio/PipeWire 的 monitor 源 + parec 子进程；
  Windows 没有 monitor 源概念，改用 WASAPI loopback（`soundcard` 库）。
  为了让 main.py / overlay.py / UI 零改动，本模块**保留同名符号**：
  `ParecCapture`（内部已换成 loopback 实现）、`list_monitor_sources()`、
  `default_monitor_source()`、`friendly_source_name()`。
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from dataclasses import dataclass

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError):  # PortAudio 缺失时不阻断其他模块导入
    sd = None

# WASAPI loopback（系统声音捕获）：仅 Windows 需要；Linux 走 parec。
try:
    import soundcard as sc
except (ImportError, OSError):  # 未安装 / 平台不支持
    sc = None

_IS_WIN = sys.platform == "win32"


def _require_portaudio() -> None:
    if sd is None:
        if _IS_WIN:
            raise RuntimeError(
                "未找到 PortAudio 运行库。请重新安装依赖: "
                "python -m pip install --force-reinstall sounddevice")
        raise RuntimeError(
            "未找到 PortAudio 库。请安装系统依赖: sudo apt install libportaudio2")


def _require_loopback() -> None:
    """确认 WASAPI loopback 可用（Windows 系统声音捕获）。"""
    if not _IS_WIN:
        raise RuntimeError("WASAPI loopback 仅在 Windows 上可用")
    if sc is None:
        raise RuntimeError(
            "未找到系统声音捕获组件。请安装依赖: python -m pip install soundcard")


def _com_init() -> bool:
    """在子线程里初始化 COM（WASAPI/MediaFoundation 的前提）。

    `soundcard` 底层走 Windows MediaFoundation，而 MediaFoundation 要求调用线程
    先初始化 COM。主线程通常已被 Python/其它库初始化过，但**我们新建的采集线程没有**，
    于是 `all_microphones()` 会抛 `RuntimeError: Error 0x800401f0`
    （`CO_E_NOTINITIALIZED`）。

    这个坑很隐蔽：同样的代码在主线程里跑得好好的，一放进 Thread 就炸。
    必须在每个采集线程入口调用本函数。
    返回 True 表示需要（且已）初始化；False 表示平台不符或依赖缺失。
    """
    if not _IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        ole32 = ctypes.windll.ole32
        # COINIT_MULTITHREADED = 0x0（推荐给后台线程；APARTMENTTHREADED 会要求消息泵）
        hr = ole32.CoInitializeEx(None, 0x0)
        # S_OK(0) / S_FALSE(1) = 成功；RPC_E_CHANGED_MODE(0x80010106) = 已以别的模式初始化
        if hr in (0, 1):
            return True
        if hr == 0x80010106:
            return True          # 已被初始化成另一模式，照样可用
        return False
    except Exception:  # noqa: BLE001 - 非致命：某些环境无需显式初始化
        return False


def _com_uninit() -> None:
    """与 `_com_init` 配对，线程退出时释放（失败无害）。"""
    if not _IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.ole32.CoUninitialize()
    except Exception:  # noqa: BLE001
        pass


@dataclass
class SourceInfo:
    """一路音频来源的元信息（贯穿 ASR/翻译/UI，用于来源标注）。"""
    key: str        # mic / monitor / tab-xxx（未来浏览器扩展推流）
    kind: str       # external(麦克风) / internal(系统内部音频)
    label: str      # UI 显示名
    app: str        # 来源应用标识（M1: microphone / system-mix；M2 起为具体应用名）


def resample(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样，语音场景足够。"""
    if src_rate == dst_rate or x.size == 0:
        return x.astype(np.float32, copy=False)
    n = max(1, int(round(x.size * dst_rate / src_rate)))
    idx = np.linspace(0.0, x.size - 1, n)
    return np.interp(idx, np.arange(x.size), x).astype(np.float32)


def list_devices() -> None:
    """打印全部音频设备（--list-devices 排查用）。"""
    _require_portaudio()
    try:
        default_in, default_out = sd.default.device
    except Exception:
        default_in = default_out = None
    print(f"{'ID':>3}  {'IN':>2} {'OUT':>3}  NAME")
    for i, d in enumerate(sd.query_devices()):
        tags = []
        if i == default_in:
            tags.append("默认输入")
        if i == default_out:
            tags.append("默认输出")
        tag = ("  <- " + "，".join(tags)) if tags else ""
        print(f"{i:>3}  {d['max_input_channels']:>2} {d['max_output_channels']:>3}  "
              f"{d['name']}{tag}")

    # Windows：额外列出 WASAPI loopback 设备（系统声音来源，不在 PortAudio 列表里）
    if _IS_WIN and sc is not None:
        print("\nWASAPI loopback 设备（系统声音捕获点）:")
        try:
            for m in sc.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False):
                    print(f"      {m.name}")
        except Exception as e:  # noqa: BLE001
            print(f"      （枚举失败: {e}）")


def find_monitor_device():
    """查找系统混音设备。

    Windows 版返回 None —— Windows 没有 "monitor 设备" 这个概念（那是
    PulseAudio/PipeWire 的术语）。系统声音走 WASAPI loopback，
    由 `list_monitor_sources()` / `ParecCapture` 负责。
    保留此函数是为了兼容调用方（main.py / overlay.py 会先试 monitor 源，
    拿不到才回退到这里），返回 None 正好让它走到 loopback 分支。
    """
    return None


# ---------------- 系统音频源（内部音频捕获点） ----------------
#
# 对外契约（与 Linux 版完全一致，调用方无需区分平台）：
#   list_monitor_sources() -> [(源标识, 友好名), ...]
#   default_monitor_source() -> 源标识 | None
#   ParecCapture(source, 源标识, out_q, target_sr) -> 有 .start()/.stop()/.device_name
#
# Windows 实现：源标识 = WASAPI loopback 设备的名称（soundcard 用名字定位设备）。

def list_monitor_sources() -> list:
    """列出全部系统音频捕获点（Windows：WASAPI loopback 设备）。

    返回 [(源标识, 友好名), ...]。
    源标识用设备名（soundcard 按名字定位，跨进程/重启稳定）。
    """
    if not _IS_WIN:
        # 非 Windows 平台保留原 pactl 路径（本目录仅供 Windows 版使用，
        # 这段是为了让代码在别的平台上仍可被导入/自检，不参与实际运行）
        try:
            out = subprocess.run(
                ["pactl", "list", "sources", "short"], capture_output=True,
                text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001
            return []
        result = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].endswith(".monitor"):
                result.append((parts[1], friendly_source_name(parts[1])))
        return result

    if sc is None:
        return []
    # 可能在任意线程被调用（UI 刷新 / 采集线程）——COM 初始化是幂等的，这里保一次底
    _com_init()
    result = []
    try:
        for m in sc.all_microphones(include_loopback=True):
            if getattr(m, "isloopback", False):
                result.append((m.name, friendly_source_name(m.name)))
    except Exception:  # noqa: BLE001 - 无音频设备/未授权
        return []
    return result


def friendly_source_name(source: str) -> str:
    """把设备名转成友好显示名。

    输入可能是 Windows 的 loopback 设备名（如
    "Speakers (Realtek(R) Audio) [Loopback]"），也可能是 Linux 的
    PulseAudio 源名（如 "alsa_output.pci-0000.analog-stereo.monitor"）。
    两种都尽量转得好看。

    匹配**按优先级从具体到宽泛**排列，顺序很重要：
    - 蓝牙要在 USB 之前判（蓝牙免提设备名常含 "Hands-Free"/驱动名，且可能带 USB 字样）；
    - HDMI 要认 "nvidia"/"high definition audio"（HDMI 音频走显卡驱动，名字里常常
      没有 "hdmi" 三个字母，只看 "hdmi" 会漏判 → 落到兜底显示成设备型号）。
    """
    s = source.replace(" [Loopback]", "").replace(".monitor", "").strip()

    low = s.lower()
    # 按关键词归类（中英混排的驱动名很常见）
    # ① 蓝牙优先：`bthhfenum` 是 Windows 蓝牙免提驱动名，`hands-free` 也是
    if ("bluetooth" in low or "bthhfenum" in low or "蓝牙" in s
            or "hands-free" in low):
        return "系统音频(蓝牙)"
    # ② HDMI / DisplayPort 音频：显卡驱动（NVIDIA/AMD/Intel）的音频设备
    if ("hdmi" in low or "displayport" in low or "display" in low
            or "nvidia" in low or "high definition audio" in low):
        return "系统音频(HDMI)"
    # ③ 数字输出
    if "digital" in low or "spdif" in low or "s/pdif" in low:
        return "系统音频(数字输出)"
    # ④ USB 声卡/耳机
    if "usb" in low or "耳机" in s:
        return "系统音频(USB)"
    # ⑤ 内置/板载模拟输出（含中文"扬声器"与常见驱动名）
    if ("analog" in low or "speaker" in low or "realtek" in low
            or "senary" in low or "扬声器" in s):
        return "系统音频(扬声器)"

    # 兜底：截掉括号补充说明，过长时截断
    name = s.split("(")[0].strip() or s
    if len(name) > 18:
        name = name[:17] + "…"
    return f"系统音频({name})"


def default_monitor_source() -> str | None:
    """默认系统音频来源 = 当前默认播放设备的 loopback。

    语义：内部音频 = 用户当前听到的一切（浏览器等应用的声音去向），
    因此跟随系统默认输出设备，而非硬编码偏好。
    """
    sources = list_monitor_sources()
    if not sources:
        return None

    if _IS_WIN and sc is not None:
        # soundcard 能直接问出「默认扬声器」，用它精确匹配
        _com_init()
        try:
            spk = sc.default_speaker()
            if spk is not None:
                for name, _ in sources:
                    if name == spk.name:
                        return name
        except Exception:  # noqa: BLE001
            pass
    else:
        sink = _default_sink()
        if sink:
            want = sink if sink.endswith(".monitor") else sink + ".monitor"
            for name, _ in sources:
                if name == want:
                    return name

    # 回退：扬声器 > USB > 任一
    for keyword in ("扬声器", "speaker", "analog", "usb"):
        for name, _ in sources:
            if keyword in name.lower():
                return name
    return sources[0][0]


def _default_sink() -> str | None:
    """系统默认输出 sink 名（Linux 的 pactl get-default-sink；Windows 用不到）。"""
    try:
        out = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001 - 无 pactl / 非 PulseAudio 环境
        return None


class ParecCapture:
    """捕获系统混音（内部音频）。

    类名沿用 Linux 版（`main.py`/`overlay.py`/`diagnose.py` 都按这个名字引用），
    但 Windows 上内部实现是 **WASAPI loopback**，而非 parec 子进程。

    为什么用 loopback（而不是 PortAudio）：
    - Windows 上 PortAudio 枚举不到"系统混音"这种设备（那是 PulseAudio 的概念）；
    - WASAPI loopback 由系统提供"把扬声器输出再喂回来"的能力，正是我们要的语义；
    - `soundcard` 库对 loopback 的封装最省事，且能直接 `default_speaker()` 找到默认设备。

    输出契约与 `AudioCapture` 一致：16kHz 单声道 float32 块推入队列。
    两个关键坑（见 WINDOWS_PORT_BRIEF 2.2）：
    1. **静音时无数据回调** —— 不能只等回调，必须用固定块长 + 超时补零，
       否则 VAD 状态机会永久卡在"说话中"。
    2. **设备热插拔** —— 耳机/扩展坞切换会让 loopback 失效，读取循环里要
       捕获异常并尝试重新打开，而不是直接退出线程。
    """

    def __init__(self, source: SourceInfo, loopback_name: str,
                 out_q: "queue.Queue[np.ndarray]", target_sr: int = 16000,
                 read_bytes: int = 800):
        self.source = source
        self.device_key = loopback_name          # 设备名（soundcard 按名字定位）
        self.target_sr = target_sr
        self.out_q = out_q
        self.read_bytes = max(read_bytes, 320)
        self.device_name = friendly_source_name(loopback_name)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 采集参数：以设备原生采样率打开，再重采样到 16k（与 AudioCapture 同策略）
        self.block_ms = 40
        if _IS_WIN:
            _require_loopback()

    # ---- 设备定位 ----

    def _open_mic(self):
        """打开 loopback 设备（按名字匹配；失败则回退到默认扬声器）。"""
        target = None
        for m in sc.all_microphones(include_loopback=True):
            if getattr(m, "isloopback", False) and m.name == self.device_key:
                target = m
                break
        if target is None:
            # 名字对不上（设备重命名/热插拔）→ 回退到默认扬声器的 loopback
            spk = sc.default_speaker()
            if spk is not None:
                target = sc.get_microphone(spk.name, include_loopback=True)
        if target is None:
            raise RuntimeError(f"找不到系统音频设备: {self.device_key}")
        return target

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True,
                                        name=f"loopback-{self.source.key}")
        self._thread.start()

    # ---- 采集循环 ----

    def _reader(self) -> None:
        """采集循环：固定块长读取，设备掉线自动重连。

        关键实现要点（都是踩过的坑）：
        1. **必须在本线程初始化 COM** —— 否则 `soundcard` 抛
           `Error 0x800401f0`（CO_E_NOTINITIALIZED）。主线程能用不代表线程能用。
        2. **`record()` 的阻塞语义** —— WASAPI loopback 在静音时仍会按块返回
           静音数据（不是"阻塞到有声音"），所以固定块长循环读即可保证时间轴连续；
           若驱动真的长时间不返回，用 `_stop.wait` 保证能退出。
        3. **设备热插拔** —— 耳机/扩展坞切换会让 loopback 失效，捕获异常后
           关掉重开，而不是让线程静默退出。
        """
        _com_init()
        mic = None
        recorder = None
        native_sr = 48000
        try:
            while not self._stop.is_set():
                # 打开设备（首次，或设备掉线后重连）
                if recorder is None:
                    try:
                        mic = self._open_mic()
                        # soundcard 的 _Microphone 没有 samplerate 属性（只有 channels），
                        # 所以取不到就用 48k（WASAPI 共享模式的通用值）
                        native_sr = int(getattr(mic, "samplerate", 0) or 48000)
                        recorder = mic.recorder(samplerate=native_sr,
                                                channels=None)   # None=取设备最大声道
                        recorder.__enter__()
                        self.device_name = friendly_source_name(mic.name)
                    except Exception:  # noqa: BLE001 - 设备不可用：退避后重试
                        recorder = None
                        if self._stop.wait(1.0):
                            break
                        continue

                frames = int(native_sr * self.block_ms / 1000)
                try:
                    data = recorder.record(numframes=frames)
                    block = self._to_mono(data)
                    out = resample(block, native_sr, self.target_sr)
                    if out.size:
                        self.out_q.put(out)
                except Exception:  # noqa: BLE001 - 掉线：关掉重开
                    try:
                        recorder.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass
                    recorder = None
                    if self._stop.wait(0.5):
                        break
        finally:
            if recorder is not None:
                try:
                    recorder.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            _com_uninit()

    @staticmethod
    def _to_mono(data: np.ndarray) -> np.ndarray:
        """多声道 -> 单声道 float32。"""
        if data.ndim == 1:
            return data.astype(np.float32, copy=False)
        if data.shape[1] == 1:
            return data[:, 0].astype(np.float32, copy=False)
        return data.mean(axis=1).astype(np.float32, copy=False)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None


# 更准确的别名：Windows 上这是 WASAPI loopback 采集（soundcard 库），并非 parec
# 子进程；ParecCapture 是 PulseAudio 时代的历史名，main.py / overlay.pipeline.py /
# diagnose.py 都按这个名字引用——保留为规范名，新代码建议用 WasapiLoopbackCapture。
WasapiLoopbackCapture = ParecCapture


class AudioCapture:
    """打开一个 PortAudio 输入设备（麦克风），重采样为 16kHz 单声道 float32 后推入队列。

    这段逻辑**跨平台通用**（sounddevice/PortAudio 在 Windows 与 Linux 上同一套 API），
    Windows 版无需改动。

    用输出声道数兜底是为了兼容某些把"输入"上报成 0 声道的设备（Linux 上的
    monitor 设备常见；Windows 上偶见于虚拟声卡）。
    """

    def __init__(self, source: SourceInfo, device,
                 out_q: "queue.Queue[np.ndarray]", target_sr: int = 16000,
                 block_ms: int = 40):
        _require_portaudio()
        self.source = source
        self.target_sr = target_sr
        self.out_q = out_q
        self._stream = None

        if device is None:
            device = sd.default.device[0]
        dev = sd.query_devices(device)
        self.device = device
        self.device_name = dev["name"]
        ch_in = dev.get("max_input_channels", 0) or 0
        ch_out = dev.get("max_output_channels", 0) or 0
        self.channels = min(max(ch_in, ch_out), 2)
        if self.channels <= 0:
            raise RuntimeError(f"设备 {device}({self.device_name}) 没有可用声道")
        self.native_sr = int(dev.get("default_samplerate", 48000) or 48000)
        self.blocksize = int(self.native_sr * block_ms / 1000)

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0] if indata.shape[1] == 1 else indata.mean(axis=1)
        data = resample(mono.astype(np.float32, copy=False),
                        self.native_sr, self.target_sr)
        if data.size:
            self.out_q.put(data)

    def start(self) -> None:
        last_err: Exception | None = None
        channel_options = [self.channels] if self.channels == 1 else [1, 2]
        for ch in channel_options:
            try:
                self._stream = sd.InputStream(
                    samplerate=self.native_sr, channels=ch, dtype="float32",
                    blocksize=self.blocksize, device=self.device,
                    callback=self._callback,
                )
                self._stream.start()
                self.channels = ch
                return
            except Exception as e:  # noqa: BLE001 - 记录后尝试下一档
                last_err = e
        raise RuntimeError(
            f"无法打开音频设备 {self.device}({self.device_name}): {last_err}")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
