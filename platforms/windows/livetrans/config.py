"""配置加载（YAML -> dataclass），带默认值兜底。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from livetrans.langs import migrate_dialog
from livetrans.speaker import SpeakerConfig


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    mic_enabled: bool = True
    monitor_enabled: bool = True
    mic_device: int | None = None
    monitor_device: int | None = None
    monitor_source: str | None = None    # PulseAudio monitor 源名（parec 方案，推荐）


@dataclass
class ASRConfig:
    language: str | None = None          # None = 自动检测
    vad_aggressiveness: int = 3
    min_speech_ms: int = 200             # 判定"说话开始"的最短语音时长
    silence_ms: int = 550                # 静音多久判定一句话结束（越小越快，易碎句）
    max_segment_sec: float = 8.0         # 单段最长录音，超过强制切段（长语音不被憋住）
    max_words_per_segment: int = 14      # 转写结果超过该词数自动拆成多条（0=不限）
    pre_buffer_ms: int = 300


@dataclass
class TranslateConfig:
    provider: str = "deepseek"
    target_lang: str = "中文"
    source_lang: str | None = None      # 源语言名（中文/English...，提示词用）
    mode: str = "normal"                 # normal / professional
    context_window: int = 8
    llm_concurrency: int = 2             # 并行翻译线程数（1=串行；过大易触发限速）
    professional_domain: str = ""
    glossary_file: str = "glossary.yaml"
    temperature: float = 0.2
    auto_zh_to_en: bool = False          # 输入为中文时自动译为英文（双向场景）
    fallback_local: bool = True          # 云端 API 连续失败时自动切本地模型（弱网降级）


@dataclass
class ProviderConfig:
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""
    label: str = ""                     # 展示名（GUI 用，如 "DeepSeek"）
    type: str = "api"                   # api（云端）/ local（本地部署）
    models: list[str] = field(default_factory=list)  # 可选模型清单（GUI 下拉）


@dataclass
class SessionConfig:
    log_dir: str = "sessions"


@dataclass
class OverlayConfig:
    """字幕外挂（悬浮字幕窗）外观与行为。"""
    source: str = "both"                 # 音频来源：internal / external / both
    always_on_top: bool = True
    click_through: bool = False          # 鼠标穿透（默认关：字幕可直接拖动/操作）
    width_ratio: float = 0.8             # 宽度 = 屏宽 x ratio
    bottom_offset: float = 0.08          # 距屏幕底部比例（字幕安全区）
    font_size: int = 34                  # 译文正文字号
    opacity: float = 0.9                 # 背景透明度（窗口 alpha；1=不透明）
    text_opacity: float = 1.0            # 文字不透明度（1=纯白，低=变暗）
    text_color: str = "#ffffff"          # 译文文字颜色（hex；外挂 ⚙ 面板可调）
    bg_color: str = "#000000"            # 字幕背景底色（hex）
    panel_auto_hide: bool = True          # 控制面板：默认隐藏，鼠标悬停时出现
    margin_x: int = 36                   # 文字左右边距（px）
    margin_y: int = 16                   # 文字上下边距（px）
    show_source: bool = True             # 同时显示原文小字
    pos_x: int = -1                      # 记住的手动位置（-1 = 底部居中自动）
    pos_y: int = -1
    mirror: bool = False                 # 镜像模式：只显示主程序结果，不自己识别/翻译
    monitor: int = -1                    # 用哪块屏：-1 = 鼠标所在屏（自动）/ 0,1,…


@dataclass
class AssistantConfig:
    """会话总结 / 对话页面共用的模型选择（可与翻译后端不同）。

    provider 留空 = 跟随翻译后端；model 留空 = 用该服务商的默认模型。
    """
    source_type: str = "api"             # api / local
    provider: str = ""
    model: str = ""


@dataclass
class DialogConfig:
    """对话模式（字幕窗左右分栏）：两个角色各自的音频来源与翻译方向。

    - 角色：self=你（自己）、other=对方。**每个角色独立选择音频来源**
      （external=麦克风 / internal=系统声音），可以随时换、也可以两者相同；
    - 换来源不影响该角色的翻译方向与上下文：你说的内容上下文一路连续；
    - left_role：左栏显示哪个角色（另一个去右栏），只影响布局；
    - bidirectional=False 时两路都按「翻译」页的单向设置（旧行为）。
    """
    left_role: str = "self"              # 左栏显示谁：self=你 / other=对方
    self_source: str = "external"        # 「你」的音频来源（可随时换）
    other_source: str = "internal"       # 「对方」的音频来源（与「你」独立，可相同）
    self_src_lang: str = "自动"           # 自动 = 交给模型判断
    self_dst_lang: str = "English"
    other_src_lang: str = "自动"
    other_dst_lang: str = "中文"
    bidirectional: bool = True


@dataclass
class LocalConfig:
    """本地模型（Ollama）的显存管理：默认"手动卸载"，不自动占/放显存。

    - unload_others_on_start：启动时把**别的**已加载模型卸掉（换模型后旧模型
      仍会占着显存，这是 Ollama 自身行为；此项只清理你没在用的那些）；
    - auto_unload_min：空闲多少分钟后自动卸载当前模型（0 = 关闭，默认手动）；
    - unload_on_exit：停止字幕（或外挂）时卸载当前模型，释放显存。
    """
    unload_others_on_start: bool = True
    auto_unload_min: int = 0
    unload_on_exit: bool = False


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    session: SessionConfig = field(default_factory=SessionConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    speaker: SpeakerConfig = field(default_factory=SpeakerConfig)
    dialog: DialogConfig = field(default_factory=DialogConfig)
    local: LocalConfig = field(default_factory=LocalConfig)


def _merge(dc, data: dict | None) -> None:
    for k, v in (data or {}).items():
        if not hasattr(dc, k) or v is None:
            continue
        if k in ("mic_device", "monitor_device") and v in ("auto", "", "null", 0):
            v = None
        if k == "language" and v in ("auto", ""):
            v = None
        setattr(dc, k, v)


def load_config(path: str | None) -> AppConfig:
    cfg = AppConfig()
    if path and Path(path).is_file():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        _merge(cfg.audio, data.get("audio"))
        _merge(cfg.asr, data.get("asr"))
        _merge(cfg.translate, data.get("translate"))
        _merge(cfg.session, data.get("session"))
        _merge(cfg.assistant, data.get("assistant"))
        _merge(cfg.overlay, data.get("overlay"))
        _merge(cfg.speaker, data.get("speaker"))
        # 旧版把方向存在 left_*/right_*（跟着栏位），就近迁移到按音频路存
        _merge(cfg.dialog, migrate_dialog(data.get("dialog")))
        _merge(cfg.local, data.get("local"))
        for name, p in (data.get("providers") or {}).items():
            cfg.providers[name] = ProviderConfig(
                base_url=p.get("base_url", ""),
                api_key_env=p.get("api_key_env", ""),
                model=p.get("model", ""),
                label=p.get("label", ""),
                type=p.get("type", "api"),
                models=list(p.get("models") or []),
            )
    if not cfg.providers:  # 无配置文件时至少给一个可用默认值
        cfg.providers["deepseek"] = ProviderConfig(
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            model="deepseek-chat",
        )
    return cfg
