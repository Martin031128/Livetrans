"""音频捕获层：Linux 上通过 PortAudio 打开麦克风（外部音频），
通过 parec 子进程捕获 PulseAudio/PipeWire monitor 源（内部系统混音）。

每路捕获输出 16kHz 单声道 float32 块。按应用拆流（R5）在 M2 实现：
Linux 用 null-sink 路由，Windows 用 Process Loopback，浏览器用扩展推流。
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError):  # PortAudio 缺失时不阻断其他模块导入
    sd = None


def _require_portaudio() -> None:
    if sd is None:
        raise RuntimeError(
            "未找到 PortAudio 库。请安装系统依赖: sudo apt install libportaudio2"
            "（Windows 无需此步，安装 sounddevice 时自带）")


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
        if "monitor" in str(d["name"]).lower():
            tags.append("monitor=系统内部音频")
        tag = ("  <- " + "，".join(tags)) if tags else ""
        print(f"{i:>3}  {d['max_input_channels']:>2} {d['max_output_channels']:>3}  "
              f"{d['name']}{tag}")


def find_monitor_device():
    """查找 PulseAudio/PipeWire 的 monitor 设备（系统输出混音）。"""
    _require_portaudio()
    for i, d in enumerate(sd.query_devices()):
        if "monitor" in str(d["name"]).lower() and (
            d["max_input_channels"] > 0 or d["max_output_channels"] > 0
        ):
            return i
    return None


# ---------------- PulseAudio/PipeWire monitor 源（parec 方案） ----------------

def list_monitor_sources() -> list:
    """通过 pactl 列出全部 monitor 源（内部音频捕获点）。

    返回 [(源名, 友好名), ...]，如 ("alsa_output...monitor", "模拟输出")。
    """
    try:
        out = subprocess.run(
            ["pactl", "list", "sources", "short"], capture_output=True,
            text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - 无 pactl（非 PulseAudio 环境）
        return []
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].endswith(".monitor"):
            result.append((parts[1], friendly_source_name(parts[1])))
    return result


def friendly_source_name(source: str) -> str:
    """把 PulseAudio 源名转成友好显示名。"""
    s = source.replace(".monitor", "")
    if "hdmi" in s:
        return "系统音频(HDMI)"
    if "usb" in s:
        return "系统音频(USB)"
    if "analog" in s:
        return "系统音频(模拟输出)"
    return "系统音频(" + s.rsplit(".", 1)[-1] + ")"


def default_monitor_source() -> str | None:
    """默认 monitor 源 = 系统默认输出 sink 对应的 monitor。

    语义：内部音频 = 用户当前听到的一切（浏览器等应用的声音去向），
    因此跟随 pactl get-default-sink，而非硬编码偏好。
    优先级：默认 sink 的 monitor > 模拟输出 > USB > 任一。
    """
    sources = list_monitor_sources()
    if not sources:
        return None
    sink = _default_sink()
    if sink:
        want = sink if sink.endswith(".monitor") else sink + ".monitor"
        for name, _ in sources:
            if name == want:
                return name
    for keyword in ("analog", "usb"):
        for name, _ in sources:
            if keyword in name:
                return name
    return sources[0][0]


def _default_sink() -> str | None:
    """系统默认输出 sink 名（pactl get-default-sink）。"""
    try:
        out = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001 - 无 pactl / 非 PulseAudio 环境
        return None


class ParecCapture:
    """用 parec 子进程捕获一个 PulseAudio monitor 源。

    PortAudio 经常枚举不到 monitor 设备；parec 直接按源名捕获，最可靠。
    输出与 AudioCapture 一致：16kHz 单声道 float32 块推入队列。
    """

    def __init__(self, source: SourceInfo, pulse_source: str,
                 out_q: "queue.Queue[np.ndarray]", target_sr: int = 16000,
                 read_bytes: int = 800):
        if shutil.which("parec") is None:
            raise RuntimeError("未找到 parec 命令，请安装: sudo apt install pulseaudio-utils")
        self.source = source
        self.pulse_source = pulse_source
        self.target_sr = target_sr
        self.out_q = out_q
        self.read_bytes = max(read_bytes, 320)   # 320B=10ms @16k s16le 单声道
        self.device_name = friendly_source_name(pulse_source)
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        cmd = ["parec", "-d", self.pulse_source, "--format=s16le",
               "--rate=%d" % self.target_sr, "--channels=1", "--raw"]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._reader, daemon=True,
                         name=f"parec-{self.source.key}").start()

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            data = self.proc.stdout.read(self.read_bytes)  # 默认 25ms/块（低延迟）
            if not data:
                break  # EOF：进程退出
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if arr.size:
                self.out_q.put(arr)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


class AudioCapture:
    """打开一个输入设备，重采样为 16kHz 单声道 float32 后推入队列。

    monitor 设备在 PulseAudio/PipeWire 上是"输出设备"，但可作为输入流打开，
    部分实现里 max_input_channels 上报为 0，因此用输出声道数兜底。
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
