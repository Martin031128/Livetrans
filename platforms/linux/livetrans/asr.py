"""ASR 层（仅 SenseVoice 引擎）：webrtcvad 切段 + SenseVoiceSmall ONNX 转写。

流式策略（M1）：VAD 状态机按句切段 -> 段级转写 -> 词数上限拆条。
M3 升级为 partial 增量字幕（sherpa-onnx 流式识别）。
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import webrtcvad
except ImportError:  # 允许仅做语法检查的环境
    webrtcvad = None

from .capture import SourceInfo
from .config import ASRConfig

# 本地模型目录（源码运行 = prototype/models；只读安装 = 数据目录/models）
# 读取时还会看随 .deb 安装的 /usr/share/livetrans/models（完全离线可用）
from .paths import MODELS_DIR, models_search_dirs  # noqa: E402

SV_REPO = "haixuantao/SenseVoiceSmall-onnx"          # funasr_onnx 兼容布局
SV_REQUIRED = ("model_quant.onnx", "config.yaml", "am.mvn",
               "chn_jpn_yue_eng_ko_spectok.bpe.model")


def sensevoice_model_dir() -> Path:
    """识别模型所在目录：优先用户目录/源码目录，其次随包的系统目录。

    都找不到时返回**下载目标**（用户可写的 MODELS_DIR/sensevoice）。
    """
    for base in models_search_dirs():
        d = base / "sensevoice"
        if all((d / f).is_file() for f in SV_REQUIRED):
            return d
    return MODELS_DIR / "sensevoice"


def sensevoice_ready() -> bool:
    d = sensevoice_model_dir()
    return all((d / f).is_file() for f in SV_REQUIRED)


@dataclass
class SegmentEvent:
    """一路音频产生的一句话转录事件。"""
    source_key: str
    kind: str          # internal / external
    label: str
    app: str
    text: str
    ts: float
    asr_ms: float = 0.0    # 该段转写耗时（字幕/日志展示延迟用）
    speaker: str = ""      # 声纹说话人标签 S1/S2…（未启用或未判定时为空）
    gap_ms: float = 0.0    # 距上一句的真实停顿（上下文连续段落的分段依据；首句=0）


class VadSegmenter:
    """webrtcvad 状态机：静音 <-> 语音，输出完整语音段（16kHz float32）。

    - 段前 pre_buffer 防止截掉句首
    - 静音 silence_ms 判定句子结束
    - 超过 max_segment_sec 强制切分，保证实时性
    """

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 3,
                 min_speech_ms: int = 250, silence_ms: int = 700,
                 max_segment_sec: float = 15.0, pre_buffer_ms: int = 300):
        if webrtcvad is None:
            raise RuntimeError("缺少依赖 webrtcvad-wheels，请 pip install -r requirements.txt")
        self.sample_rate = sample_rate
        self.frame_ms = 30
        self.vad = webrtcvad.Vad(aggressiveness)
        self.frame_size = sample_rate * self.frame_ms // 1000
        self.trigger_frames = max(1, min_speech_ms // self.frame_ms)
        self.silence_frames = max(1, silence_ms // self.frame_ms)
        self.max_frames = int(max_segment_sec * 1000 / self.frame_ms)
        pre = max(0, pre_buffer_ms // self.frame_ms)
        self._pre: deque = deque(maxlen=pre or 1)
        self._tail = np.empty(0, dtype=np.float32)
        self._seg: list = []
        self._speech_run = 0
        self._silence_run = 0
        self._in_speech = False

    def feed(self, chunk: np.ndarray) -> list:
        self._tail = np.concatenate([self._tail, chunk.astype(np.float32, copy=False)])
        done = []
        while self._tail.size >= self.frame_size:
            frame = self._tail[:self.frame_size]
            self._tail = self._tail[self.frame_size:]
            seg = self._handle(frame)
            if seg is not None and seg.size:
                done.append(seg)
        return done

    def _handle(self, frame: np.ndarray):
        pcm = (np.clip(frame, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        try:
            speech = self.vad.is_speech(pcm, self.sample_rate)
        except Exception:  # noqa: BLE001 - VAD 异常时保守当作语音
            speech = True

        if not self._in_speech:
            self._pre.append(frame)
            if speech:
                self._speech_run += 1
                if self._speech_run >= self.trigger_frames:
                    self._in_speech = True
                    self._seg = list(self._pre)
                    self._speech_run = 0
                    self._silence_run = 0
            else:
                self._speech_run = 0
            return None

        self._seg.append(frame)
        if speech:
            self._silence_run = 0
            if len(self._seg) >= self.max_frames:
                return self._flush()
            return None
        self._silence_run += 1
        if self._silence_run >= self.silence_frames:
            return self._flush()
        return None

    def _flush(self) -> np.ndarray:
        seg = np.concatenate(self._seg) if self._seg else np.empty(0, np.float32)
        self._in_speech = False
        self._seg = []
        self._pre.clear()
        self._speech_run = 0
        self._silence_run = 0
        return seg

    @property
    def in_speech(self) -> bool:
        """当前是否有进行中的语音段（供流式 partial 转写）。"""
        return self._in_speech

    def current_speech(self) -> np.ndarray | None:
        """进行中语音段的快照（副本）；无进行段返回 None。"""
        if not self._in_speech or not self._seg:
            return None
        return np.concatenate(self._seg)


class _StderrTee:
    """捕获模型下载进度（huggingface_hub 的 tqdm 进度条写在 stderr）并转发回调。

    - 解析形如 ` 45%|████▌ | 231M/465M [00:12<00:15, 19.2MB/s]` 的行；
    - 原文同时透传到真实 stderr（CLI/日志文件仍保留完整输出）；
    - 声称自己是 tty，避免 tqdm 在重定向场景自动禁用进度条。
    回调签名：cb(pct:int, done:str, total:str, rate:str)。
    """

    _TQDM_RE = re.compile(
        r"(?P<pct>\d+)%\|[^\r\n|]*\|\s*(?P<done>[\d.]+\s*[kMG]?B?)\s*/\s*"
        r"(?P<total>[\d.]+\s*[kMG]?B?)\s*\[[^\]]*?"
        r"(?P<rate>[\d.]+\s*[kMG]?B/s)\]")

    def __init__(self, cb, min_interval: float = 0.5):
        self._cb = cb
        self._orig = sys.stderr
        self._buf = ""
        self._last_t = 0.0
        self._min_interval = min_interval

    def restore(self):
        """返回原始 stderr（供 finally 恢复）。"""
        return self._orig

    def write(self, s: str) -> int:
        try:
            self._orig.write(s)
        except Exception:  # noqa: BLE001
            pass
        self._buf += s
        while True:                          # tqdm 用 \r 刷新，按 \r/\n 切行
            m = re.search(r"[\r\n]", self._buf)
            if not m:
                break
            line, self._buf = self._buf[:m.start()], self._buf[m.end():]
            self._feed(line)
        return len(s)

    def flush(self) -> None:
        try:
            self._orig.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self) -> bool:
        return True                         # 让 tqdm 认为是终端，保持输出进度条

    def _feed(self, line: str) -> None:
        if not self._cb:
            return
        m = self._TQDM_RE.search(line)
        if not m:
            return
        pct = int(m.group("pct"))
        if pct < 100 and (time.monotonic() - self._last_t) < self._min_interval:
            return                          # 节流；100% 始终放行
        self._last_t = time.monotonic()
        try:
            self._cb(pct, m.group("done").strip(), m.group("total").strip(),
                     m.group("rate").strip())
        except Exception:  # noqa: BLE001
            pass


_TAG_RE = re.compile(r"<\|[^|>]*\|>")
# SenseVoice 支持的语言码（超出部分回退 auto）
SV_SUPPORTED = {"zh", "en", "yue", "ja", "ko", "nospeech"}


class SenseVoiceASR:
    """funasr_onnx.SenseVoiceSmall 封装（非自回归，CPU 快、中英日韩粤）。

    与旧 WhisperASR 同接口：transcribe(np16k float32) -> str。
    模型只从项目 models/sensevoice/ 加载（先点「下载模型」或手动放置），
    不做隐式联网下载。
    """

    def __init__(self, language: str | None = None, progress_cb=None,
                 quantize: bool = True):
        try:
            from funasr_onnx import SenseVoiceSmall
        except ImportError as e:
            raise RuntimeError(
                "SenseVoice 引擎需要 funasr_onnx：pip install funasr_onnx") from e
        if not sensevoice_ready():
            raise RuntimeError(
                f"SenseVoice 模型未就绪：请在控制台点「下载模型」，或运行 "
                f"python3 -c \"from livetrans.asr import preload_model; "
                f"preload_model()\"")
        self.language = language or "auto"
        if self.language not in SV_SUPPORTED:
            self.language = "auto"          # 不支持的语言回退自动检测
        self.loaded_from = str(sensevoice_model_dir())
        self._lock = threading.Lock()       # partial 与 final 并发转写串行化
        tee = _StderrTee(progress_cb) if progress_cb else None
        if tee is not None:
            sys.stderr = tee
        try:
            self.model = SenseVoiceSmall(model_dir=self.loaded_from,
                                         quantize=quantize,
                                         intra_op_num_threads=4)
        finally:
            if tee is not None:
                sys.stderr = tee.restore()

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < 1600:                # <0.1s 忽略
            return ""
        with self._lock:
            res = self.model(audio, language=self.language, use_itn=True)
        text = res[0] if isinstance(res, list) and res else ""
        return _TAG_RE.sub("", str(text)).strip()


def preload_model(progress_cb=None) -> Path:
    """把 SenseVoice ONNX 模型（约 233MB）下载到项目 models/sensevoice/。

    - 已就绪时秒回，不联网；
    - 经 HF 兼容端点 snapshot_download（HF_ENDPOINT 镜像可用，Xet 统一禁用）；
    - 下载期间捕获 stderr 的 tqdm 进度转发 progress_cb(pct, done, total, rate)。
    返回模型目录路径。
    """
    from huggingface_hub import snapshot_download

    dest = sensevoice_model_dir()
    if sensevoice_ready():
        return dest
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")   # 镜像端点不兼容 Xet（401）
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tee = _StderrTee(progress_cb) if progress_cb else None
    if tee is not None:
        sys.stderr = tee
    try:
        snapshot_download(repo_id=SV_REPO, local_dir=str(dest),
                          allow_patterns=list(SV_REQUIRED) + ["tokens.json"])
    finally:
        if tee is not None:
            sys.stderr = tee.restore()
    if not sensevoice_ready():
        raise RuntimeError(f"下载完成但 {dest} 缺少必要文件，请重试")
    return dest


def split_by_words(text: str, max_words: int) -> list[str]:
    """把转写文本按词数上限拆成多条（长句及时上屏，不再等整段翻完）。

    - 拉丁语系按空格分词；CJK（无空格）按字符块（词数 x2）切，尽量停在标点；
    - max_words <= 0 表示不限制，返回原文单条。
    """
    text = text.strip()
    if max_words <= 0 or not text:
        return [text] if text else []
    tokens = text.split()
    if len(tokens) > 1:                      # 有空格：按词分组
        if len(tokens) <= max_words:
            return [text]
        return [" ".join(tokens[i:i + max_words])
                for i in range(0, len(tokens), max_words)]
    size = max(6, max_words * 2)             # 无空格（中日韩）：按字符块
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= size and ch in "，。！？；、,.!?;:： ":
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    if len(parts) == 1:                      # 无标点可停：硬切
        parts = [text[i:i + size] for i in range(0, len(text), size)]
    return parts


class ASRWorker(threading.Thread):
    """一路音频一个 worker：capture 队列 -> VAD 切段 -> 转写 -> SegmentEvent。

    - on_level(kind, rms)：约每 100ms 上报该路实时音量（字幕窗电平条）；
    - on_partial(kind, text)：说话进行中每 0.8s 对增量快照转写一次，实时推
      "识别中"文本（原文流式上屏）；段结束/流终止时清空。partial 在独立
      线程转写（SenseVoice 快且与 final 共用锁串行化），不阻塞 VAD 喂帧。
    """

    PARTIAL_INTERVAL = 0.8

    def __init__(self, source: SourceInfo, capture_q, asr: SenseVoiceASR,
                 asr_cfg: ASRConfig, out_q, stop_event: threading.Event,
                 on_level=None, on_partial=None,
                 pause_event: threading.Event | None = None,
                 speaker=None):
        super().__init__(daemon=True, name=f"asr-{source.key}")
        self.source = source
        self.q = capture_q
        self.asr = asr
        self.asr_cfg = asr_cfg
        self.out_q = out_q
        self.stop = stop_event
        self.on_level = on_level
        self.on_partial = on_partial
        self.pause_event = pause_event       # 置位 = 该路暂停（丢弃音频）
        self.speaker = speaker               # SpeakerTracker（可选，已启用声纹时）
        self._last_level_t = 0.0
        self._last_partial_t = 0.0
        self._partial_thread: threading.Thread | None = None
        self._prev_emit: tuple[float, float] | None = None   # (ts, 音频时长ms)
        self.segmenter = VadSegmenter(
            aggressiveness=asr_cfg.vad_aggressiveness,
            min_speech_ms=asr_cfg.min_speech_ms,
            silence_ms=asr_cfg.silence_ms,
            max_segment_sec=asr_cfg.max_segment_sec,
            pre_buffer_ms=asr_cfg.pre_buffer_ms,
        )

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                chunk = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.pause_event is not None and self.pause_event.is_set():
                continue                     # 暂停：丢弃该路音频
            try:
                now = time.monotonic()
                if self.on_level is not None and \
                        now - self._last_level_t >= 0.1:
                    self._last_level_t = now
                    rms = float(np.sqrt(np.mean(
                        chunk.astype(np.float64) ** 2)))
                    self.on_level(self.source.kind, min(rms, 1.0))
                self._maybe_partial(now)
                for seg in self.segmenter.feed(chunk):
                    t0 = time.monotonic()
                    text = self.asr.transcribe(seg)
                    if text:
                        asr_ms = (time.monotonic() - t0) * 1000
                        ts = time.time()
                        # 距上一句的真实停顿 = 相邻两段完成时刻之差 - 上一段
                        # 音频时长 + 上一段尾部计入的静音（VAD 在静音达到阈值
                        # 时切句，尾静音算进了上一段）。供"上下文连续段落"
                        # 判断是否分段；ASR 耗时误差 ±数百 ms，对 3.5s 阈值无碍
                        audio_ms = seg.size / 16000.0 * 1000.0
                        if self._prev_emit is None:
                            gap_ms = 0.0
                        else:
                            pts, pms = self._prev_emit
                            gap_ms = max(0.0, (ts - pts) * 1000.0 - pms
                                         + self.asr_cfg.silence_ms)
                        self._prev_emit = (ts, audio_ms)
                        # 声纹：整段（VAD 切出的完整句子）抽说话人向量并归类，
                        # 单段约 30ms，与识别同线程串行；失败/未启用时为空串
                        speaker = (self.speaker.assign(seg)
                                   if self.speaker is not None else "")
                        for j, part in enumerate(split_by_words(
                                text, self.asr_cfg.max_words_per_segment)):
                            if part:
                                # 同段拆出的多句属同一次说话：gap=0（同段）
                                self.out_q.put(SegmentEvent(
                                    source_key=self.source.key,
                                    kind=self.source.kind,
                                    label=self.source.label,
                                    app=self.source.app,
                                    text=part, ts=ts,
                                    asr_ms=asr_ms,
                                    speaker=speaker,
                                    gap_ms=gap_ms if j == 0 else 0.0,
                                ))
                    if self.on_partial is not None:
                        self.on_partial(self.source.kind, "")   # 段已定稿，清"识别中"
            except Exception as e:  # noqa: BLE001 - 单段失败不终止管线
                print(f"[asr-{self.source.key}] 段处理失败: {e}")

    def _maybe_partial(self, now: float) -> None:
        """说话进行中，到间隔就转写一次增量快照（独立线程，不阻塞喂帧）。"""
        if self.on_partial is None or not self.segmenter.in_speech:
            return
        if now - self._last_partial_t < self.PARTIAL_INTERVAL:
            return
        if self._partial_thread is not None and self._partial_thread.is_alive():
            return
        snap = self.segmenter.current_speech()
        if snap is None or snap.size < 8000:      # <0.5s 不值得转写
            return
        self._last_partial_t = now
        asr, kind = self.asr, self.source.kind

        def work() -> None:
            try:
                text = asr.transcribe(snap)
                if text and self.on_partial and not self.stop.is_set():
                    self.on_partial(kind, text)
            except Exception:  # noqa: BLE001 - partial 失败静默
                pass

        self._partial_thread = threading.Thread(target=work, daemon=True,
                                                name=f"partial-{self.source.key}")
        self._partial_thread.start()
