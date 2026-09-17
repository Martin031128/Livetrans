"""LLM 翻译层：OpenAI 兼容协议，一套代码切换
DeepSeek / GLM / 千问 / Kimi / GPT / Gemini / Claude / Grok / Ollama(本地)。

包含：普通/专业模式、术语表注入、滑动上下文窗口、流式翻译（边生成边显示）、
长会话分批总结。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .config import ProviderConfig, TranslateConfig
from .paths import resolve_data

# 思考模型（Qwen3 / DeepSeek-R1 类）输出自带 <think>...</think> 思维链，剥离之

# 思考禁用参数的厂商风格（移植 LiveTranslate translator.py 的实现）：
# 思考不关闭时模型会把整个 max_tokens 预算烧在思维链上，译文为空或严重滞后
_NESTED_THINKING_MODELS = ("deepseek", "glm")
_NESTED_THINKING_ENDPOINTS = ("deepseek", "volces", "api.z.ai", "bigmodel")
_PARAMLESS_ENDPOINTS = ("api.openai.com", "api.x.ai", "api.anthropic.com")


# ---------------- 本地模型后端（Ollama）就绪检查 ----------------

def is_local_base_url(base_url: str) -> bool:
    host = (urlparse(base_url or "").hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "[::1]")


def _ollama_root(base_url: str) -> str:
    """http://localhost:11434/v1 -> http://localhost:11434（原生 API 根）。"""
    return base_url.rstrip("/").removesuffix("/v1")


def _probe_ollama(base_url: str, timeout: float = 1.5) -> bool:
    try:
        import httpx
        r = httpx.get(f"{_ollama_root(base_url)}/api/version", timeout=timeout)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 - 探测失败即视为不可用
        return False


def ensure_local_backend(base_url: str, model: str, log) -> None:
    """启动前确保本机模型服务（Ollama）可用。

    - 未运行则自动拉起 `ollama serve`；未安装给出安装与拉模型指引；
    - 模型未 pull 时提示（OpenAI 兼容层会 404）。
    避免逐句报 "[翻译失败:APIConnectionError]" 这种没有头绪的失败。

    Windows 差异（与 Linux 版的两处不同）：
    - 安装指引：Linux 用 `curl | sh` 一行命令；Windows 只能给下载页。
    - 进程创建：Linux 用 `start_new_session=True` 脱离终端；
      Windows 该参数无效，改用 `CREATE_NO_WINDOW` 避免弹出控制台黑框
      （否则每次自动拉起 ollama 都会跳一个窗口）。
    """
    if _probe_ollama(base_url):
        log("本地模型服务已就绪（ollama）")
    else:
        exe = shutil.which("ollama")
        if not exe:
            # Windows 上 ollama 由安装器放进 PATH（%LOCALAPPDATA%\Programs\Ollama）
            how_to_install = (
                "请访问 https://ollama.com/download/windows 下载安装"
                if sys.platform == "win32" else
                "请先安装（curl -fsSL https://ollama.com/install.sh | sh）")
            raise RuntimeError(
                f"本地模型服务不可用：未安装 ollama。{how_to_install}，"
                f"并拉取模型（ollama pull {model}）")
        log("本地模型服务未运行，正在自动启动 ollama serve ...")
        kwargs: dict = {"stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            # 不弹黑框；ollama 自己会常驻后台
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([exe, "serve"], **kwargs)
        for _ in range(20):
            time.sleep(0.5)
            if _probe_ollama(base_url):
                log("ollama serve 已启动")
                break
        else:
            raise RuntimeError(
                "ollama serve 启动超时（10s）："
                "请手动运行 'ollama serve' 查看报错")
    # 模型存在性检查（服务在但模型没 pull 过时，OpenAI 兼容层会 404）
    try:
        import httpx
        r = httpx.get(f"{_ollama_root(base_url)}/api/tags", timeout=2.0)
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
        base = model.split(":")[0]
        if names and not any(n == model or n.split(":")[0] == base
                             for n in names):
            log(f"⚠ 本地模型 '{model}' 尚未拉取，请运行: ollama pull {model}")
    except Exception:  # noqa: BLE001 - 检查失败不阻塞启动
        pass


class ThinkFilter:
    """流式思维链剥离器（跨 chunk 安全的状态机）。

    - 见到 <think> 后丢弃内容直到 </think>（未闭合时保留尾部残片，
      防止 "</thin" 跨 chunk 被误输出）；
    - 其他 "<...>" 文本原样保留，不误伤；
    - final() 在流结束时调用：未闭合的 think 整段丢弃，否则 flush 残片。
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.in_think = False

    def feed(self, delta: str) -> str:
        self.buf += delta
        out: list[str] = []
        while True:
            if not self.in_think:
                i = self.buf.find("<")
                if i == -1:
                    out.append(self.buf)
                    self.buf = ""
                    break
                out.append(self.buf[:i])
                self.buf = self.buf[i:]
                j = self.buf.find(">")
                if j == -1:
                    break                          # 标签未完整，等待更多
                tag = self.buf[:j + 1].lower()
                self.buf = self.buf[j + 1:]
                if tag == self.OPEN:
                    self.in_think = True
                else:
                    out.append(tag)                # 非 think 标签原样保留
            else:
                # in_think：思维链内容只消费不输出
                j = self.buf.find(self.CLOSE)
                if j != -1:
                    self.buf = self.buf[j + len(self.CLOSE):]   # 丢弃思维链（含闭合标签）
                    self.in_think = False
                    continue                 # 继续处理闭合后的正常文本
                # 未找到闭合标签：丢弃确认内容，仅保留尾部可能是
                # "</think>" 前缀的残片（rfind('<')：正文里的 '<' 保留无害）
                i = self.buf.rfind("<")
                self.buf = self.buf[i:] if i != -1 else ""
                if len(self.buf) > 200:            # 防 in_think 永不闭合时 buf 滚雪球
                    self.buf = self.buf[-16:]
                break
        return "".join(out)

    def final(self) -> str:
        out = ""
        if not self.in_think and self.buf:         # 未闭合 think 整段丢弃
            out = self.buf
        self.buf = ""
        return out

# 提示词参考 LiveTranslate（TheDeathDragon/LiveTranslate, MIT）的 DEFAULT_PROMPT：
# 规则式英文（LLM 遵循度高）+ ASR 纠错 + 专有名词保留 + 单一最佳译文。
SYSTEM_NORMAL = (
    "You are a real-time subtitle translator. Translate {source} into {target}.\n"
    "Rules:\n"
    "- Output ONLY one single best translation, nothing else.\n"
    "- Never include alternatives, parenthetical options, annotations, or explanations.\n"
    "- Keep proper nouns, names, and brand names untranslated.\n"
    "- Translate repeated expressions concisely, not mechanically word-for-word.\n"
    "- Keep subtitles fluent and natural; avoid overly literal or stiff phrasing.\n"
    "- Auto-correct likely ASR errors based on context and common sense.\n"
    "- The source text comes from speech recognition and may lack punctuation; "
    "recover sentence boundaries from context."
)

SYSTEM_PRO = (
    "\n\n[Domain mode] Domain: {domain}. Use standard terminology and phrasing of "
    "this domain; glossary entries MUST take precedence over your own choices."
    "\n\nGlossary:\n{glossary}"
)

# 上下文连续段落模式：段落尾部修订（合并最近几句 + 纠正识别错误 + 润色）
SYSTEM_REVISE = (
    "You are polishing live-subtitle output. The user gives recent raw "
    "speech-recognition segments (error-prone, often unpunctuated) together "
    "with their current translations. Merge ALL given segments into ONE "
    "coherent passage in {target}:\n"
    "- Fix likely recognition mistakes using the context.\n"
    "- Remove duplicates and filler; use natural punctuation and sentence flow.\n"
    "- Keep the original meaning; do NOT add new information or explanations.\n"
    "- Output ONLY the corrected merged {target} text."
)

SUMMARY_SYSTEM = (
    "你是会议/媒体内容分析助手。对给定的双语实时转录做总结，用中文输出 Markdown，包含：\n"
    "## 主题\n## 要点（涉及多个音频来源时请按来源分组）\n## 行动项/待办（如无则省略）"
)


class Glossary:
    """专业模式术语表（YAML）。"""

    def __init__(self, path: str | None):
        self.entries: list = []
        if path and Path(path).is_file():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            self.entries = data.get("terms", []) or []

    def render(self) -> str:
        lines = []
        for e in self.entries:
            zh = e.get("zh") or e.get("translation") or ""
            line = f"- {e.get('term', '')} -> {zh}"
            if e.get("note"):
                line += f"（{e['note']}）"
            lines.append(line)
        return "\n".join(lines)


class ContextWindow:
    """滑动上下文窗口：保留最近 K 句（原文+译文），供 LLM 理解指代与省略。"""

    def __init__(self, k: int = 8):
        self.items: deque = deque(maxlen=max(1, k))

    def append(self, src: str, dst: str) -> None:
        self.items.append((src, dst))

    def render(self) -> str:
        return "\n".join(f"- {s} => {d}" for s, d in self.items)


def _is_chinese(text: str) -> bool:
    """判断文本是否以中文为主（CJK 字符占比高且非英文句）。"""
    if not text:
        return False
    han = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in text if c.isascii() and c.isalpha())
    return han >= 2 and han >= letters * 0.5


class LLMTranslator:
    """翻译/总结统一入口。"""

    def __init__(self, tcfg: TranslateConfig, providers: dict):
        if tcfg.provider not in providers:
            raise KeyError(
                f"未知翻译后端: {tcfg.provider}，可选: {', '.join(providers)}")
        p: ProviderConfig = providers[tcfg.provider]
        api_key = os.environ.get(p.api_key_env, "") if p.api_key_env else ""
        if p.api_key_env and not api_key:
            raise RuntimeError(
                f"后端 {tcfg.provider} 需要环境变量 {p.api_key_env}，请先 export。")
        from openai import OpenAI
        self.client = OpenAI(base_url=p.base_url, api_key=api_key or "local",
                             timeout=60)
        self.base_url = p.base_url
        self.model = p.model
        self.backend = tcfg.provider
        self.cfg = tcfg
        self.glossary = Glossary(str(resolve_data(tcfg.glossary_file) or ""))
        self.ctx = ContextWindow(tcfg.context_window)

    # ---- 内部 ----

    def _system(self, text: str = "") -> str:
        lang_map = {"中文": "Chinese", "English": "English", "日本語": "Japanese",
                    "한국어": "Korean", "Français": "French", "Deutsch": "German",
                    "Русский": "Russian", "Español": "Spanish"}
        source = lang_map.get(
            getattr(self.cfg, "source_lang", None) or "", "the source language")
        target_name = self.cfg.target_lang
        if getattr(self.cfg, "auto_zh_to_en", False) and _is_chinese(text):
            source, target_name = "中文", "English"   # 中文入 -> 英文出（双向）
        target = lang_map.get(target_name, target_name)
        s = SYSTEM_NORMAL.format(source=source, target=target)
        if self.cfg.mode == "professional":
            s += SYSTEM_PRO.format(
                domain=self.cfg.professional_domain or "general professional",
                glossary=self.glossary.render() or "(empty)",
            )
        return s

    def _messages(self, text: str) -> list:
        """上下文按多轮对话注入（user/assistant 交替，LiveTranslate 同款），
        比纯文本拼接对 LLM 更友好。"""
        msgs = [{"role": "system", "content": self._system(text)}]
        for src, dst in list(self.ctx.items)[-self.cfg.context_window:]:
            msgs.append({"role": "user", "content": src})
            msgs.append({"role": "assistant", "content": dst})
        msgs.append({"role": "user", "content": text})
        return msgs

    def _thinking_style(self) -> str:
        """按 model id 与 endpoint 自动推断思考禁用风格（LiveTranslate 同款）。"""
        model_id = (self.model or "").lower()
        endpoint = (self.base_url or "").lower()
        if any(m in model_id for m in _NESTED_THINKING_MODELS) or any(
                e in endpoint for e in _NESTED_THINKING_ENDPOINTS):
            return "deepseek"
        if any(e in endpoint for e in _PARAMLESS_ENDPOINTS):
            return "off"
        return "qwen"

    def _thinking_disable(self) -> dict | None:
        """关闭深度思考的 extra_body（None = 不发，端点拒绝未知参数时省事）。

        思考不关闭时模型把 max_tokens 预算烧在思维链上，译文空/严重滞后。
        请求被拒时由 _create_stream 自动降级重试（不带该参数）。
        """
        style = self._thinking_style()
        if style == "deepseek":
            return {"thinking": {"type": "disabled"}}          # DeepSeek/GLM 官方
        if style == "qwen":
            return {"enable_thinking": False}                  # DashScope/兼容端点
        if style == "vllm":
            return {"chat_template_kwargs": {"enable_thinking": False}}
        if style == "openai":
            return {"reasoning_effort": "none"}
        return None                                            # off：不发

    def _create_stream(self, messages: list):
        """发起流式请求；思考禁用参数不被接受（HTTPError）时自动降级重试。"""
        disable = self._thinking_disable()
        if disable:
            try:
                return self.client.chat.completions.create(
                    model=self.model, messages=messages,
                    temperature=self.cfg.temperature, stream=True,
                    extra_body=disable)
            except Exception:  # noqa: BLE001 - 参数不被接受：降级重试
                pass
        return self.client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=self.cfg.temperature, stream=True)

    def chat(self, system: str, history: list) -> str:
        """多轮对话（供「对话」页面使用）；history = [{role, content}, ...]。

        取最近 12 轮控制上下文长度；与翻译共用同一后端/Key 解析。
        """
        msgs = [{"role": "system", "content": system}] + list(history)[-12:]
        resp = self.client.chat.completions.create(
            model=self.model, messages=msgs, temperature=0.3)
        return (resp.choices[0].message.content or "").strip()

    def _chat(self, system: str, user: str, temperature: float) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()

    # ---- 对外 ----

    def translate_stream(self, text: str, on_delta=None, raw_hook=None) -> str:
        """流式翻译：on_delta(partial_text) 随生成逐步回调（约 80ms 节流）。

        - ThinkFilter 跨 chunk 安全剥离思考模型的 <think>...</think> 思维链
          （思维链阶段不上屏，字幕保持"… 翻译中"）；
        - raw_hook(raw_text)：流结束时回调**模型原始输出**（含思维链，供日志
          排查——用户要求后端内容全部可查）。失败时抛异常由调用方处理。
        """
        raw_chunks: list[str] = []
        emitted: list[str] = []
        last_emit = 0.0
        tf = ThinkFilter()
        stream = self._create_stream(self._messages(text))
        for ev in stream:
            if not getattr(ev, "choices", None):
                continue
            delta = getattr(ev.choices[0].delta, "content", None) or ""
            if not delta:
                continue
            raw_chunks.append(delta)
            visible = tf.feed(delta)
            if visible:
                emitted.append(visible)
                if on_delta is not None:
                    now = time.monotonic()
                    if now - last_emit >= 0.08:
                        last_emit = now
                        on_delta("".join(emitted).strip())
        tail = tf.final()
        if tail:
            emitted.append(tail)
        out = "".join(emitted).strip()
        if on_delta is not None and out:
            on_delta(out)                       # 收尾：确保最终态上屏
        if raw_hook is not None:
            try:
                raw_hook("".join(raw_chunks))
            except Exception:  # noqa: BLE001 - 日志失败不影响翻译
                pass
        return out

    # ---- 对外 ----

    def revise(self, sources: list[str], current: str, anchor: str = "") -> str:
        """上下文连续段落：修订段落尾部（合并最近几句 + 纠正识别错误）。

        - sources：参与修订的最近几句**原始识别文本**（按时间顺序）；
        - current：这几句当前的合并译文（可能是流式/未修订状态）；
        - anchor：更早的已定稿上下文（供指代与语气衔接，不会被输出重复）。
        非流式（输出短、整体替换尾部）；失败抛异常由调用方保留现译文。
        """
        if not sources:
            return ""
        segs = "\n".join(f"- {s}" for s in sources)
        user = f"Earlier context (already finalized):\n{anchor}\n\n" if anchor else ""
        user += (f"Recent raw segments:\n{segs}\n\n"
                 f"Current merged translation:\n{current or '（空）'}\n\n"
                 "Return the corrected merged translation only.")
        out = self._chat(SYSTEM_REVISE.format(target=self.cfg.target_lang),
                         user, 0.2)
        # 思考模型（<think>…</think>）兜底剥离：_chat 是非流式一次性返回
        tf = ThinkFilter()
        visible = tf.feed(out)
        tail = tf.final()
        return (visible + tail).strip()

    def translate(self, text: str) -> str:
        """一次性翻译（独立场景用）。实时管线请用 translate_stream + 顺序门，
        上下文由调用方按显示顺序记录（见 TranslatorWorker）。"""
        try:
            out = self.translate_stream(text)
            self.ctx.append(text, out)
            return out
        except Exception as e:  # noqa: BLE001 - 翻译失败不中断管线
            out = f"[翻译失败:{type(e).__name__}]"
            self.ctx.append(text, out)
            return out

    def summarize(self, records: list) -> str:
        """records: 会话 JSONL 逐条 dict。长会话自动分批摘要再汇总。"""
        lines = [
            f"[{r.get('ts', '')}]（{r.get('label', r.get('source', ''))}）"
            f"{r.get('text', '')} => {r.get('translation', '')}"
            for r in records
        ]
        if not lines:
            return "（无转录内容）"
        batch = 60
        if len(lines) <= batch:
            return self._chat(SUMMARY_SYSTEM, "\n".join(lines), 0.3)
        notes = []
        for i in range(0, len(lines), batch):
            part = "\n".join(lines[i:i + batch])
            notes.append(self._chat(
                SUMMARY_SYSTEM + "\n（本批为长会话的一部分，仅输出该批要点）",
                part, 0.3))
        return self._chat(
            SUMMARY_SYSTEM + "\n（以下是对长会话各部分的分段摘要，请汇总为最终总结）",
            "\n\n".join(notes), 0.3)
