"""本机资源监控（GPU 显存/利用率/温度、CPU、内存）——纯数据采集，无 UI。"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request


def _read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", encoding="ascii") as f:
        p = [int(x) for x in f.readline().split()[1:]]
    return sum(p), p[3] + p[4]        # (总 jiffies, idle+iowait)

def _cpu_pct(prev: tuple[int, int] | None,
             cur: tuple[int, int]) -> float | None:
    if not prev:
        return None
    dt, di = cur[0] - prev[0], cur[1] - prev[1]
    if dt <= 0:
        return None
    return max(0.0, min(100.0, (1 - di / dt) * 100))

def _mem_pct() -> float | None:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        return (1 - info["MemAvailable"] / info["MemTotal"]) * 100
    except (OSError, KeyError, ValueError):
        return None

def _gpu_stats() -> str:
    """NVIDIA GPU：显存 / 利用率 / 温度（无 nvidia-smi 返回空）。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total,utilization.gpu,"
                  "temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2).stdout
        mu, mt, util, temp = [x.strip()
                              for x in out.strip().splitlines()[0].split(",")]
        return (f"GPU {int(mu) / 1024:.1f}/{int(mt) / 1024:.0f}GB"
                f" · {int(util)}% · {int(temp)}°C")
    except Exception:  # noqa: BLE001 - 采集失败不影响主流程
        return ""

def _ollama_vram_gb() -> float:
    """ollama /api/ps：已加载模型占用的显存；服务不可达返回 -1。"""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps",
                                    timeout=1.5) as r:
            data = json.loads(r.read().decode())
        return sum(m.get("size_vram", 0)
                   for m in data.get("models", [])) / 1024 ** 3
    except Exception:  # noqa: BLE001
        return -1.0

OLLAMA_HOST = "http://localhost:11434"


def ollama_root(base_url: str | None = None) -> str:
    """OpenAI 兼容 base_url（http://host:port/v1）-> Ollama 原生根 http://host:port。"""
    if not base_url:
        return OLLAMA_HOST
    try:
        from urllib.parse import urlparse
        u = urlparse(base_url if "//" in base_url else f"http://{base_url}")
        if u.hostname:
            return f"{u.scheme or 'http'}://{u.hostname}:{u.port or 11434}"
    except ValueError:
        pass
    return OLLAMA_HOST


def ollama_loaded(base_url: str | None = None, timeout: float = 2.0) -> list[dict]:
    """当前常驻的模型（/api/ps）：[{name, size_vram, expires_at}]；服务不可达返回 []。"""
    try:
        req = urllib.request.Request(f"{ollama_root(base_url)}/api/ps")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return list(data.get("models") or [])
    except Exception:  # noqa: BLE001 - 服务没起/超时都按"没有常驻模型"处理
        return []


def ollama_unload(names: list[str] | None = None, base_url: str | None = None,
                  timeout: float = 20.0) -> dict[str, bool]:
    """把常驻模型从显存里卸下来（"取消挂载"）。

    - names 为空/None：卸掉**当前所有**已加载模型；
    - 原理：POST /api/generate {"model": x, "keep_alive": 0}，ollama 收到后立即释放
      （实测 4.4GB -> 1.3GB，立刻回收）；
    - 返回 {模型名: 是否成功}，失败原因只记在返回值里，不抛异常。
    """
    if names is None:
        names = [m.get("name", "") for m in ollama_loaded(base_url)]
    names = [n for n in names if n]
    root = ollama_root(base_url)
    out: dict[str, bool] = {}
    for name in names:
        body = json.dumps({"model": name, "keep_alive": 0}).encode()
        req = urllib.request.Request(f"{root}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
            out[name] = True
        except Exception:  # noqa: BLE001
            out[name] = False
    return out


def ollama_vram_gb(base_url: str | None = None) -> float:
    """当前常驻模型占用的显存 GB（用不上 GPU 时也能用来估算）；不可达 -1。"""
    try:
        loaded = ollama_loaded(base_url)
    except Exception:  # noqa: BLE001
        return -1.0
    return sum(m.get("size_vram", 0) for m in loaded) / 1024 ** 3


def collect_local_stats(cpu_prev) -> tuple[str, tuple[int, int] | None]:
    """一行本地资源概览文本 + 新的 CPU 采样点（供下次差分）。"""
    cur = _read_cpu_times()
    parts: list[str] = []
    up = True
    gpu = _gpu_stats()
    if gpu:
        parts.append(gpu)
    elif not shutil.which("nvidia-smi"):      # 无独显：用 ollama 汇报模型占用
        vram = _ollama_vram_gb()
        if vram < 0:
            up = False
        elif vram > 0:
            parts.append(f"模型显存 {vram:.1f}GB")
    cpu = _cpu_pct(cpu_prev, cur)
    if cpu is not None:
        parts.append(f"CPU {cpu:.0f}%")
    mem = _mem_pct()
    if mem is not None:
        parts.append(f"内存 {mem:.0f}%")
    text = " · ".join(parts) or "—"
    if not up:
        text = "⚠ 模型服务未运行 · " + text
    return text, cur
