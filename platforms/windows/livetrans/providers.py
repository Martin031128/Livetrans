"""服务商数据层：示例配置解析、模型列表拉取（不含任何 UI 逻辑）。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from livetrans.paths import CONFIG_PATH, EXAMPLE_PATH


def fetch_model_list(base_url: str, api_key: str = "",
                     extra_headers: dict | None = None,
                     timeout: float = 10.0) -> list[str]:
    """GET {base_url}/models（OpenAI 兼容标准端点），返回排序后的模型 id 列表。

    - 兼容 data[].id（OpenAI 格式）与 models[].name（Ollama tags 格式）
    - Gemini 兼容端点返回的 id 可能带 "models/" 前缀，统一剥掉
    - 失败抛 RuntimeError（含可读原因）
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for k, v in (extra_headers or {}).items():
        headers[k] = v
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hint = "（Key 无效或未授权）" if e.code in (401, 403) else ""
        raise RuntimeError(f"HTTP {e.code}{hint}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 {url}（{e.reason}）") from e
    except (ValueError, OSError) as e:
        raise RuntimeError(f"响应解析失败: {e}") from e

    items = data.get("data") or data.get("models") or []
    ids: set[str] = set()
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or m.get("name") or "").strip()
        if mid.startswith("models/"):
            mid = mid[len("models/"):]
        if mid:
            ids.add(mid)
    if not ids:
        raise RuntimeError("API 返回的模型列表为空")
    return sorted(ids)


def load_yaml() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.is_file() else EXAMPLE_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def example_providers() -> dict:
    """example 里的 providers 元数据（label/models），用于补齐旧 config.yaml。"""
    try:
        return (yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
                or {}).get("providers") or {}
    except (OSError, yaml.YAMLError):
        return {}
