"""语言表与外挂音频来源等界面共用常量（配置域数据，不含 UI 逻辑）。"""
from __future__ import annotations


LANGUAGES = ["中文", "English", "日本語", "한국어", "Français", "Deutsch",
             "Русский", "Español"]

LANG_CODES = {"中文": "zh", "English": "en", "日本語": "ja", "한국어": "ko",
              "Français": "fr", "Deutsch": "de", "Русский": "ru", "Español": "es"}

CODE_TO_LANG = {v: k for k, v in LANG_CODES.items()}

SOURCE_LANGS = ["自动识别"] + LANGUAGES     # 输入：多一个“自动识别”

TARGET_LANGS = LANGUAGES                    # 输出

MAX_WORDS_DEFAULT = 14   # 断句词数默认值（0=不限）

OV_SRC_LABELS = {"internal": "内部（系统音频）", "external": "外部（麦克风）",
                 "both": "两者"}

OV_SRC_VALUES = {v: k for k, v in OV_SRC_LABELS.items()}

# ---- 对话模式：两个「角色」各自挑音频来源与翻译方向 ----
# 角色：你（自己）/ 对方。每个角色都能独立选择用麦克风还是系统声音，
# 各自还能配自己的源语言与目标语言；左右栏只是显示位置（显示谁由 left_role 定）。

DIALOG_ROLES = ("self", "other")
DIALOG_ROLE_LABELS = {"self": "你（自己）", "other": "对方"}

DIALOG_AUDIO_LABELS = {
    "external": "麦克风（自己的声音）",
    "internal": "系统声音（电脑播放）",
}
DIALOG_AUDIO_SHORT = {"external": "麦克风", "internal": "系统声音"}
DIALOG_AUDIO_VALUES = {v: k for k, v in DIALOG_AUDIO_LABELS.items()}
DIALOG_ROLE_VALUES = {v: k for k, v in DIALOG_ROLE_LABELS.items()}

DIALOG_SRC_LANGS = ["自动"] + LANGUAGES      # 源语言：自动 = 交给模型判断
AUTO_DST = "自动（中↔英）"                    # 目标「自动」：中文入 -> 英文出，其余 -> 中文出
DIALOG_DST_LANGS = [AUTO_DST] + list(LANGUAGES)

# 默认值（与 config.DialogConfig 的字段默认一致）
DIALOG_DEFAULTS = {
    "left_role": "self",           # 左栏显示谁（另一角色去右栏）
    "self_source": "external",     # 「你」的音频来源（可随时换：麦克风 / 系统声音）
    "other_source": "internal",    # 「对方」的音频来源（与「你」相互独立，可相同）
    "self_src_lang": "自动",
    "self_dst_lang": "English",
    "other_src_lang": "自动",
    "other_dst_lang": "中文",
    "bidirectional": True,         # 关 = 两路都按「翻译」页的单向设置
}


def dialog_audio_short(kind: str) -> str:
    """音频路 -> 短标签（栏标题用，如「麦克风」）。"""
    return DIALOG_AUDIO_SHORT.get(kind, kind or "—")


def dialog_role_label(role: str) -> str:
    """角色 -> 中文标签（你（自己）/ 对方）。"""
    return DIALOG_ROLE_LABELS.get(role, role or "—")


def other_kind(kind: str) -> str:
    """另一路音频。"""
    return "internal" if kind == "external" else "external"


def _norm_kind(kind, fallback: str) -> str:
    kind = str(kind or "").strip()
    return kind if kind in DIALOG_AUDIO_LABELS else fallback


def dialog_role_kinds(dialog: dict | None) -> dict[str, str]:
    """角色 -> 音频路：**两个角色各自独立**（可指向同一路，也可以随时换）。

    例：「你」用麦克风说一段、再切到系统声音放一段，上下文仍按「你」连续；
    线下两人共用麦克风时，两个角色都可以指向麦克风，用各栏的暂停键轮流用。
    """
    d = dialog or {}
    return {"self": _norm_kind(d.get("self_source"), "external"),
            "other": _norm_kind(d.get("other_source"), "internal")}


def roles_for_kind(dialog: dict | None, kind: str) -> list[str]:
    """这一路音频有哪几个角色在用（可能是 0 / 1 / 2 个），按显示顺序返回。"""
    role_kinds = dialog_role_kinds(dialog)
    order = [dialog_left_role(dialog)]
    order.append("other" if order[0] == "self" else "self")
    return [role for role in order if role_kinds[role] == kind]


def dialog_kinds(dialog: dict | None) -> dict[str, str]:
    """左/右栏各自显示哪一路音频（位置由 left_role 决定；两角色同源时两侧相同）。"""
    role_kinds = dialog_role_kinds(dialog)
    left_role = dialog_left_role(dialog)
    right_role = "other" if left_role == "self" else "self"
    return {"left": role_kinds[left_role], "right": role_kinds[right_role]}


def dialog_role_of_side(dialog: dict | None, side: str) -> str:
    """某一栏显示哪个角色。"""
    left_role = dialog_left_role(dialog)
    if side == "left":
        return left_role
    return "other" if left_role == "self" else "self"


def dialog_left_role(dialog: dict | None) -> str:
    role = str((dialog or {}).get("left_role", "self") or "self")
    return role if role in DIALOG_ROLES else "self"


def dialog_roles(dialog: dict | None) -> dict[str, dict]:
    """每个角色的完整设置：{role: {source, src_lang, dst_lang}}。"""
    d = dialog or {}
    kinds = dialog_role_kinds(d)
    out: dict[str, dict] = {}
    for role in DIALOG_ROLES:
        out[role] = {
            "source": kinds[role],
            "src_lang": str(d.get(f"{role}_src_lang") or "自动"),
            "dst_lang": str(d.get(f"{role}_dst_lang")
                            or ("English" if role == "self" else "中文")),
        }
    return out


def dialog_directions(dialog: dict | None) -> dict[str, tuple[str, str]]:
    """角色 -> (源语言, 目标语言)：翻译方向跟**角色**走（不是跟音频路）。

    所以把「你」的输入从麦克风换成系统声音，你的方向与上下文都不变。
    """
    return {role: (cfg["src_lang"], cfg["dst_lang"])
            for role, cfg in dialog_roles(dialog).items()}


def dialog_lang_pair(dialog: dict | None, side: str) -> tuple[str, str]:
    """某一栏（left/right）显示的**角色**的翻译方向（标题用）。"""
    return dialog_directions(dialog)[dialog_role_of_side(dialog, side)]


def dialog_direction(dst_lang: str) -> tuple[str, bool]:
    """目标语言选项 -> (实际目标语言, 中文输入是否改译英文)。"""
    if dst_lang == AUTO_DST:
        return "中文", True
    return (dst_lang or "中文"), False


_LEGACY_BY_SIDE = ("left_src_lang", "left_dst_lang",
                   "right_src_lang", "right_dst_lang")
_LEGACY_BY_KIND = ("external_src_lang", "external_dst_lang",
                   "internal_src_lang", "internal_dst_lang", "left_source")


def _v1_to_v2(d: dict) -> dict:
    """v1（方向按左/右栏存）-> v2（方向按音频路存）。

    注意：这里必须直接从 v1 的 left_source 推左右对应的音频路，
    不能调 dialog_kinds()——那个函数按当前（v3）字段解析，读不到 left_source。
    """
    if not any(k in d for k in _LEGACY_BY_SIDE):
        return d
    left_source = str(d.get("left_source", "external") or "external")
    if left_source not in DIALOG_AUDIO_LABELS:
        left_source = "external"
    out = dict(d)
    for side, kind in (("left", left_source),
                       ("right", other_kind(left_source))):
        for field in ("src_lang", "dst_lang"):
            old = f"{side}_{field}"
            if old in out:
                out.setdefault(f"{kind}_{field}", out.pop(old))
    return out


def _v2_to_v3(d: dict) -> dict:
    """v2（方向按音频路 + left_source）-> v3（角色：你/对方 各自挑来源与方向）。

    就近约定：麦克风这一路归「你」、系统声音这一路归「对方」；
    左栏显示谁按原 left_source 换算。
    """
    if "self_source" in d or "left_role" in d:
        return d
    if not any(k in d for k in _LEGACY_BY_KIND):
        return d
    out = dict(d)
    left_source = str(out.pop("left_source", "external") or "external")
    for kind, role in (("external", "self"), ("internal", "other")):
        for field in ("src_lang", "dst_lang"):
            v = out.pop(f"{kind}_{field}", None)
            if v is not None:
                out[f"{role}_{field}"] = v
    out["self_source"] = "external"
    out["left_role"] = "self" if left_source == "external" else "other"
    return out


def _v3_to_v4(d: dict) -> dict:
    """v3（只有 self_source，角色互斥占两路）-> v4（两角色来源独立）。"""
    if "other_source" in d or "self_source" not in d:
        return d
    out = dict(d)
    out["other_source"] = other_kind(_norm_kind(out.get("self_source"), "external"))
    return out


def migrate_dialog(dialog: dict | None) -> dict:
    """把任意旧格式的 dialog 段迁移到当前格式（角色制）。"""
    return _v3_to_v4(_v2_to_v3(_v1_to_v2(dict(dialog or {}))))
