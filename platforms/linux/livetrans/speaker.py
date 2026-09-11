"""声纹角色标注（说话人区分）：sherpa-onnx 说话人向量 + 在线聚类。

【为什么可行】ASR 的每一段都是 VAD 切出来的**完整句子**（纯语音、无静音），
正好是说话人向量提取需要的输入，不需要额外的切分/滑窗。

【判定规则】每段抽一个说话人向量（CAM++ / 3D-Speaker，28MB ONNX），与已有
说话人比对（余弦相似度）：
- 最相似者 >= threshold -> 判为同一个人（返回既有标签 S1/S2…）；
- 否则新建标签；说话人已满（max_speakers）则归入最相似者；
- 太短的段（< min_segment_sec）不做判定，返回空标签（不可靠）。

标签只用于渲染配色与"换人标注"，不参与翻译、不进 TTS、不上传。

【可选依赖】sherpa-onnx（pip）+ models/speaker/*.onnx。缺失时 assign() 返回
空串，其它功能完全不受影响（声纹是增强项，不是必需项）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from livetrans.paths import MODELS_DIR, models_search_dirs

MODEL_DIR = MODELS_DIR / "speaker"
# 优先 zh+en 双语模型（本项目主要中英互译）
PREFERRED = ("3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
             "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
             "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")

# 说话人配色（相邻标签对比度高；Tkinter 与 GTK3 共用）
SPEAKER_COLORS = ["#a6e3a1", "#89b4fa", "#f9e2af", "#f38ba8",
                  "#cba6f7", "#94e2d5", "#fab387", "#74c7ec"]


@dataclass
class SpeakerConfig:
    enabled: bool = False
    threshold: float = 0.55        # 余弦相似度阈值（越高越容易判成新说话人）
    max_speakers: int = 6          # 上限之外归入最相似者
    min_segment_sec: float = 0.6   # 太短的段不判定（不可靠）
    model: str = ""                # 留空 = 自动找 models/speaker/*.onnx
    num_threads: int = 2


def speaker_model_path(cfg: SpeakerConfig | None = None) -> Path | None:
    """定位声纹模型：配置指定优先，否则按 PREFERRED 顺序找，最后取任意 *.onnx。

    查找范围包含随 .deb 安装的系统目录（/usr/share/livetrans/models/speaker），
    所以离线包装上后无需再下载。
    """
    if cfg is not None and cfg.model:
        p = Path(cfg.model)
        if not p.is_absolute():
            p = BASE / p
        return p if p.is_file() else None
    for base in models_search_dirs():
        d = base / "speaker"
        if not d.is_dir():
            continue
        for name in PREFERRED:
            p = d / name
            if p.is_file():
                return p
        found = sorted(d.glob("*.onnx"))
        if found:
            return found[0]
    return None


# 首选模型（CAM++ 中英双语，192 维，28MB）——缺失时自动下载
SPEAKER_URL = ("https://hf-mirror.com/csukuangfj/speaker-embedding-models/"
               "resolve/main/3dspeaker_speech_campplus_sv_zh_en_16k-common_"
               "advanced.onnx")


def ensure_speaker_model(cfg: SpeakerConfig | None = None,
                         log=None, timeout: float = 600.0) -> Path | None:
    """声纹模型缺失时自动下载（约 28MB，走 hf-mirror）；失败返回 None。

    这样"系统安装（deb）"首次启用声纹也能直接用，不需要手动放模型文件。
    """
    path = speaker_model_path(cfg)
    if path is not None:
        return path
    dest = MODEL_DIR / Path(SPEAKER_URL).name
    tmp = dest.with_suffix(".part")
    try:
        import shutil
        import urllib.request
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if log:
            log(f"声纹模型不存在，正在下载（约 28MB）: {dest.name}")
        with urllib.request.urlopen(SPEAKER_URL, timeout=timeout) as r, \
                open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 256 * 1024)
        tmp.rename(dest)
        if log:
            log(f"声纹模型就绪: {dest.name}（{dest.stat().st_size / 1e6:.0f}MB）")
        return dest
    except Exception as e:  # noqa: BLE001 - 下载失败只影响声纹
        try:
            tmp.unlink()
        except OSError:
            pass
        if log:
            log(f"⚠ 声纹模型下载失败（{type(e).__name__}: {e}）→ 本次不启用声纹")
        return None


def speaker_ready(cfg: SpeakerConfig | None = None) -> bool:
    """声纹可用：sherpa-onnx 已装 + 模型文件存在。"""
    try:
        import importlib.util
        if importlib.util.find_spec("sherpa_onnx") is None:
            return False
    except (ImportError, ValueError):
        return False
    return speaker_model_path(cfg) is not None


def color_for(name: str) -> str:
    """说话人标签 -> 配色（S1 起；无法解析时用兜底色）。"""
    idx = 0
    if name.startswith("S") and name[1:].isdigit():
        idx = int(name[1:]) - 1
    return SPEAKER_COLORS[idx % len(SPEAKER_COLORS)]


class SpeakerTracker:
    """在线说话人聚类：一段音频 -> 说话人标签（S1/S2…），线程安全。

    模型懒加载（首次 assign 才载入，避免没用声纹时白花钱）；加载失败永久降级
    为"不可用"，不影响识别与翻译。
    """

    def __init__(self, cfg: SpeakerConfig | None = None, log=None):
        self.cfg = cfg or SpeakerConfig()
        self.log = log or (lambda _m: None)
        self._lock = threading.Lock()        # 两路 ASR 线程共享同一跟踪器
        self._ex = None                      # SpeakerEmbeddingExtractor
        self._mgr = None                     # SpeakerEmbeddingManager
        self._failed = False
        self._loaded_ms = 0.0

    # ---- 模型 ----

    def _ensure(self) -> bool:
        """载入模型与说话人管理器；失败即永久降级（只记一次日志）。"""
        if self._ex is not None:
            return True
        if self._failed:
            return False
        try:
            import time
            import sherpa_onnx
            path = ensure_speaker_model(self.cfg, self.log)   # 缺失则自动下载
            if path is None:
                self._failed = True
                self.log("声纹：模型不可用（下载失败或路径不对），本次不启用")
                return False
            t0 = time.monotonic()
            conf = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(path), num_threads=max(1, self.cfg.num_threads),
                provider="cpu")
            ex = sherpa_onnx.SpeakerEmbeddingExtractor(conf)
            if ex.dim <= 0:
                raise RuntimeError("模型未就绪（文件损坏或格式不符）")
            self._ex = ex
            self._mgr = sherpa_onnx.SpeakerEmbeddingManager(ex.dim)
            self._loaded_ms = (time.monotonic() - t0) * 1000
            self.log(f"声纹就绪：{path.name}（dim={ex.dim}，"
                     f"载入 {self._loaded_ms:.0f}ms，阈值 {self.cfg.threshold}）")
            return True
        except Exception as e:  # noqa: BLE001 - 声纹失败不影响主流程
            self._failed = True
            self.log(f"声纹不可用（已跳过）：{type(e).__name__}: {e}")
            return False

    # ---- 判定 ----

    @property
    def available(self) -> bool:
        return self._ex is not None and not self._failed

    @property
    def speakers(self) -> list[str]:
        return list(self._mgr.all_speakers) if self._mgr is not None else []

    def preload(self) -> bool:
        """提前载入模型（避免第一句话额外等约 230ms 的载入时间）。"""
        with self._lock:
            return self._ensure()

    def reset(self) -> None:
        """清空说话人（新会话开始时调用，避免跨会话串人）。"""
        with self._lock:
            if self._mgr is not None:
                for name in list(self._mgr.all_speakers):
                    self._mgr.remove(name)

    def assign(self, audio, sample_rate: int = 16000) -> str:
        """一段语音 -> 说话人标签；不可用/太短/异常时返回 ""（调用方按无名处理）。"""
        import numpy as np
        if not self.cfg.enabled:
            return ""
        if audio is None:
            return ""
        secs = len(audio) / float(sample_rate or 16000)
        if secs < self.cfg.min_segment_sec:
            return ""
        with self._lock:
            if not self._ensure():
                return ""
            try:
                stream = self._ex.create_stream()
                stream.accept_waveform(sample_rate, np.asarray(audio, np.float32))
                stream.input_finished()
                emb = np.asarray(self._ex.compute(stream), np.float32)
                if emb.size == 0:
                    return ""
                norm = float(np.linalg.norm(emb))
                if norm <= 1e-6:
                    return ""
                emb = emb / norm           # 官方示例要求 L2 归一后再 add/search
                name = self._mgr.search(emb, threshold=self.cfg.threshold)
                if name:                                  # 命中已有说话人
                    return name
                if self._mgr.num_speakers < max(1, self.cfg.max_speakers):
                    name = f"S{self._mgr.num_speakers + 1}"
                    self._mgr.add(name, emb)
                    self.log(f"声纹：新增说话人 {name}（当前 "
                             f"{self._mgr.num_speakers} 人）")
                    return name
                name = self._mgr.search(emb, threshold=0.0)   # 满了：归最相似者
                return name or ""
            except Exception as e:  # noqa: BLE001 - 单段失败不降级整体
                self.log(f"声纹判定失败（本段忽略）：{type(e).__name__}: {e}")
                return ""


def build_tracker(cfg: SpeakerConfig | None, log=None) -> SpeakerTracker | None:
    """按配置构建跟踪器：未启用或依赖/模型缺失时返回 None（零开销）。"""
    if cfg is None or not cfg.enabled:
        return None
    return SpeakerTracker(cfg, log)
