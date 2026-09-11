"""API key 本地存储：GUI 填写 -> keys.env（环境变量风格，权限 0600）。

文件内容形如（可直接 source 到 shell，供 curl 等复用）：
    export GLM_API_KEY=xxxxxxxx.yyyyyyyy
    export DEEPSEEK_API_KEY=sk-xxxxxxxx

优先级：已 export 的环境变量 > keys.env > 启动报错。
兼容读取旧版 keys.yaml（{provider名: key}）；保存时统一写 keys.env 并移除旧文件。
键名归一/归属纠错由 launcher 依据 providers 配置完成。
历史记录：api_history.yaml（同目录，600）记录用过的 {provider, key, time}
（最新在前、按 (provider, key) 去重、上限 20 条），GUI 一键载入换 API。
可用 LIVETRANS_KEYS_DIR 重定向目录（供测试）。

detect_provider()：按 key 格式签名识别归属服务商，用于 GUI 粘贴 key 时
自动切换服务商 -> 联动模型列表，避免 key 存错归属。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import yaml

from .paths import DATA_DIR

KEYS_DIR = Path(os.environ.get("LIVETRANS_KEYS_DIR") or DATA_DIR)
KEYS_PATH = KEYS_DIR / "keys.env"
LEGACY_PATH = KEYS_DIR / "keys.yaml"

# 智谱 key 的两段式签名：xxxxx.yyyyy（两段均 >= 8 位字母数字）
_GLM_RE = re.compile(r"^[0-9A-Za-z]{8,}\.[0-9A-Za-z]{8,}$")


def detect_provider(key: str) -> str | None:
    """按 key 外观识别归属（返回 providers 键名）；识别不了返回 None。

    - sk-ant-...  -> claude（Anthropic 专属前缀）
    - AIza...     -> gemini（Google API key 专属前缀）
    - xai-...     -> grok（xAI 专属前缀）
    - xxxxx.yyyyy -> glm（智谱两段式，主流厂商中唯一用该格式）
    - sk-...      -> None（DeepSeek/OpenAI/Kimi/千问同为 sk- 前缀，无法可靠区分，
                     按用户当前选择的服务商保存）
    """
    k = (key or "").strip()
    if k.lower().startswith("bearer "):          # 容错：连 Bearer 头一起粘贴
        k = k[7:].strip()
    k = k.strip("\"'")
    if not k or " " in k:
        return None
    if k.startswith("sk-ant-"):
        return "claude"
    if k.startswith("AIza"):
        return "gemini"
    if k.startswith("xai-"):
        return "grok"
    if not k.startswith("sk-") and _GLM_RE.match(k):
        return "glm"
    return None


def _parse_env_file(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip("\"'")
        if k.strip() and v:
            out[k.strip()] = v
    return out


def load_any() -> dict[str, str]:
    """读取 keys.env（{ENV名: key}）；不存在时回退旧 keys.yaml（{provider名: key}）。"""
    try:
        if KEYS_PATH.is_file():
            return _parse_env_file(KEYS_PATH.read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        if LEGACY_PATH.is_file():
            data = yaml.safe_load(LEGACY_PATH.read_text(encoding="utf-8")) or {}
            return {str(k): str(v).strip() for k, v in data.items() if v}
    except (OSError, yaml.YAMLError):
        pass
    return {}


def save_keys(env_keys: dict[str, str]) -> None:
    """写 keys.env（权限 0600）；全空则删除文件；并移除旧 keys.yaml 防混淆。"""
    env_keys = {k: v.strip() for k, v in env_keys.items() if v and v.strip()}
    LEGACY_PATH.unlink(missing_ok=True)
    if not env_keys:
        KEYS_PATH.unlink(missing_ok=True)
        return
    lines = [
        "# LiveTrans API keys（由控制台生成，勿分享/勿提交）",
        "# 终端复用：source 本文件后，$GLM_API_KEY 等变量即可直接使用",
    ] + [f"export {env}={key}" for env, key in sorted(env_keys.items())]
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        KEYS_PATH.chmod(0o600)
    except OSError:
        pass


def key_env_overlay(providers: dict) -> dict[str, str]:
    """providers -> {环境变量名: key}。文件键为 ENV 名（新版）或 provider 名（旧版）均可命中。"""
    overlay: dict[str, str] = {}
    stored = load_any()
    for name, p in (providers or {}).items():
        env = p.get("api_key_env", "") if isinstance(p, dict) else getattr(p, "api_key_env", "")
        if not env:
            continue
        key = stored.get(env) or stored.get(name)
        if key:
            overlay[env] = key
    return overlay


def merged_env(providers: dict) -> dict[str, str]:
    """os.environ + keys.env（环境变量优先），供 Popen(env=...)。"""
    env = dict(os.environ)
    for env_name, key in key_env_overlay(providers).items():
        if not env.get(env_name):
            env[env_name] = key
    return env


# ---------------- 历史记录（换 API 方便回切） ----------------

HISTORY_PATH = KEYS_DIR / "api_history.yaml"
HISTORY_MAX = 20


def mask_key(key: str) -> str:
    """key 打码显示：sk-9f3a...91a（保留首尾）。"""
    k = (key or "").strip()
    if len(k) > 14:
        return f"{k[:8]}...{k[-4:]}"
    return "***"


def load_history(provider: str | None = None) -> list[dict]:
    """读取历史 [{provider, key, time}, ...]，最新在前。

    传 provider 则只返回该服务商的历史（本地模型与各 API 互不混列）。
    """
    try:
        data = yaml.safe_load(HISTORY_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    items = data.get("history") or []
    return [h for h in items if isinstance(h, dict) and h.get("key")
            and (provider is None or h.get("provider") == provider)]


def add_history(provider: str, key: str) -> None:
    """记录/置顶一条使用历史（按 key 去重，上限 HISTORY_MAX），权限 600。"""
    provider, key = (provider or "").strip(), (key or "").strip()
    if not provider or not key:
        return
    items = [h for h in load_history()
             if h.get("key") != key or h.get("provider") != provider]
    items.insert(0, {"provider": provider, "key": key,
                     "time": time.strftime("%Y-%m-%d %H:%M")})
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        "# LiveTrans API 使用历史（由控制台生成，勿分享/勿提交）\n"
        + yaml.safe_dump({"history": items[:HISTORY_MAX]}, allow_unicode=True,
                         sort_keys=False),
        encoding="utf-8")
    try:
        HISTORY_PATH.chmod(0o600)
    except OSError:
        pass


def remove_history(provider: str, key: str) -> None:
    """删除一条历史（载入处可选删除时用）。"""
    items = [h for h in load_history()
             if not (h.get("provider") == provider and h.get("key") == key)]
    try:
        if items:
            HISTORY_PATH.write_text(
                yaml.safe_dump({"history": items}, allow_unicode=True,
                               sort_keys=False), encoding="utf-8")
        else:
            HISTORY_PATH.unlink(missing_ok=True)
    except OSError:
        pass
