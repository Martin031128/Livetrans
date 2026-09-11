"""M1 骨架自检：模块导入、配置加载、Key 存储、VAD 状态机、翻译层（不依赖 PortAudio/网络）。"""
import os
import sys
import tempfile
import traceback

ok = []

try:
    from livetrans.config import load_config
    cfg = load_config("config.example.yaml")
    assert len(cfg.providers) == 9, f"providers 数量异常: {len(cfg.providers)}"
    assert cfg.translate.provider == "deepseek"
    assert cfg.asr.max_words_per_segment == 14
    assert cfg.asr.silence_ms == 550
    assert cfg.providers["deepseek"].label == "DeepSeek"
    assert "deepseek-v4-flash" in cfg.providers["deepseek"].models
    ok.append(f"config: 9 个翻译后端加载 OK（含 label/models 元数据）")
except Exception:
    traceback.print_exc(); sys.exit(1)

try:
    # 长句按词数拆条（ASRWorker 复用同一函数）
    from livetrans.asr import split_by_words
    long_en = " ".join(f"w{i}" for i in range(40))
    parts = split_by_words(long_en, 14)
    assert len(parts) == 3 and all(len(p.split()) <= 14 for p in parts)
    cjk = split_by_words("这是一段没有标点的很长中文语音识别结果" * 3, 14)
    assert len(cjk) >= 2 and all(len(p) <= 28 for p in cjk)
    assert split_by_words("短句", 0) == ["短句"]
    ok.append("asr: 词数断句拆条 OK（拉丁按词 / CJK 按字符块）")
except Exception:
    traceback.print_exc(); sys.exit(1)

try:
    # API key 本地存储（重定向到临时目录，避免污染真实 keys.env）
    with tempfile.TemporaryDirectory() as td:
        os.environ["LIVETRANS_KEYS_DIR"] = td
        import importlib
        from livetrans import keys as keys_mod
        importlib.reload(keys_mod)

        # key 签名识别
        assert keys_mod.detect_provider("sk-ant-api03-FAKE-TEST-ONLY") == "claude"
        assert keys_mod.detect_provider("AIzaSyA1234567890abcdefghij") == "gemini"
        assert keys_mod.detect_provider("xai-abc123def456789") == "grok"
        assert keys_mod.detect_provider("a1b2c3d4e5f6a7b8.9c8d7e6f5a4b3c2d") == "glm"
        assert keys_mod.detect_provider("sk-THIS-IS-A-FAKE-TEST-KEY-000000") is None
        assert keys_mod.detect_provider("Bearer AIzaSyXYZabc12345") == "gemini"
        assert keys_mod.detect_provider("") is None

        # keys.env 环境变量格式存取 + 权限
        keys_mod.save_keys({"DEEPSEEK_API_KEY": " sk-test "})
        text = keys_mod.KEYS_PATH.read_text(encoding="utf-8")
        assert "export DEEPSEEK_API_KEY=sk-test" in text
        assert keys_mod.load_any() == {"DEEPSEEK_API_KEY": "sk-test"}
        # 文件权限 600：POSIX 专属；Windows 没有 POSIX 权限位，
        # os.stat().st_mode 恒为 0o666（与 chmod 无关）→ 仅 Linux 断言
        if os.name == "posix":
            assert (os.stat(keys_mod.KEYS_PATH).st_mode & 0o777) == 0o600

        # 旧 keys.yaml 兼容读取；保存时迁移并移除旧文件
        keys_mod.KEYS_PATH.unlink()
        keys_mod.LEGACY_PATH.write_text("deepseek: sk-old\n", encoding="utf-8")
        assert keys_mod.load_any() == {"deepseek": "sk-old"}
        keys_mod.save_keys({"GLM_API_KEY": "aaaaaaaa.bbbbbbbb"})
        assert not keys_mod.LEGACY_PATH.exists()
        assert keys_mod.load_any() == {"GLM_API_KEY": "aaaaaaaa.bbbbbbbb"}

        # overlay：ENV 名与旧 provider 名两种键均可命中；无 env 的后端跳过
        provs = {"deepseek": {"api_key_env": "DEEPSEEK_API_KEY"},
                 "glm": {"api_key_env": "GLM_API_KEY"},
                 "ollama": {"api_key_env": ""}}
        assert keys_mod.key_env_overlay(provs) == {"GLM_API_KEY": "aaaaaaaa.bbbbbbbb"}
        keys_mod.KEYS_PATH.unlink()
        keys_mod.LEGACY_PATH.write_text("deepseek: sk-old\n", encoding="utf-8")
        assert keys_mod.key_env_overlay(provs) == {"DEEPSEEK_API_KEY": "sk-old"}

        # 环境变量优先：已存在的环境变量不被覆盖
        assert keys_mod.merged_env(
            {"deepseek": {"api_key_env": "PATH"}})["PATH"] == os.environ["PATH"]

        keys_mod.save_keys({})  # 全空 -> 文件删除
        assert not keys_mod.KEYS_PATH.exists()
    ok.append("keys: env 格式存取/legacy 迁移/签名识别/overlay/env 注入 OK")
except Exception:
    traceback.print_exc(); sys.exit(1)

try:
    from livetrans.translate import Glossary, ContextWindow
    g = Glossary("glossary.example.yaml")
    assert len(g.entries) == 3 and "latency" in g.render()
    c = ContextWindow(3)
    for i in range(5):
        c.append(f"s{i}", f"d{i}")
    assert len(c.items) == 3  # 滑动窗口只保留最近 3 条
    ok.append("translate: 术语表 + 滑动上下文窗口 OK")
except Exception:
    traceback.print_exc(); sys.exit(1)

try:
    from livetrans.asr import VadSegmenter
    import numpy as np
    seg = VadSegmenter(aggressiveness=3, min_speech_ms=90, silence_ms=90,
                       max_segment_sec=2.0, pre_buffer_ms=90)
    # 模拟：0.3s 静噪 + 0.6s 谐波调制波（模拟浊音）+ 0.5s 静音
    sr = 16000
    n_speech = int(0.6 * sr)
    t = np.arange(n_speech) / sr
    speech = (np.sin(2 * np.pi * 110 * t)
              + 0.5 * np.sin(2 * np.pi * 220 * t)
              + 0.3 * np.sin(2 * np.pi * 550 * t))
    speech *= (0.6 + 0.4 * np.sin(2 * np.pi * 6 * t))  # 基频调制，接近人声节奏
    sig = np.concatenate([
        np.random.RandomState(0).rand(int(0.3 * sr)) * 0.005,
        (speech * 0.4).astype(np.float32),
        np.zeros(int(0.5 * sr), np.float32),
    ]).astype(np.float32)
    segs = []
    pos = 0
    while pos < len(sig):  # 按块喂入，模拟流式
        segs += seg.feed(sig[pos:pos + 1600])
        pos += 1600
    total = sum(len(s) for s in segs)
    assert segs, "未切出任何语音段"
    assert all(len(s) <= 2.2 * sr for s in segs), "段长超过 max_segment_sec 限制"
    assert 0.25 * sr < total < 1.0 * sr, f"切出的语音总量异常: {total/sr:.2f}s"
    ok.append(f"asr: VAD 状态机切出 {len(segs)} 段（共 {total/sr:.2f}s），流式喂入/断句 OK")
except Exception:
    traceback.print_exc(); sys.exit(1)

try:
    # 会话导出：SRT 时间轴推算 / 单调不重叠 / 双语两行 / 失败译文丢弃
    import json
    import re
    from pathlib import Path
    from livetrans.export import export, build_cues, format_ts, to_srt
    assert format_ts(0) == "00:00:00,000"
    assert format_ts(3661.234) == "01:01:01,234"
    assert format_ts(-5) == "00:00:00,000"

    with tempfile.TemporaryDirectory() as td:
        sess = os.path.join(td, "session-demo.jsonl")
        rows = [
            # 相邻两句仅隔 0.6s，且各自耗时较长 -> 时间轴必须被压成不重叠
            {"ts": "2026-09-10T10:00:00", "kind": "internal", "text": "hello",
             "translation": "你好", "asr_ms": 120, "llm_ms": 700},
            {"ts": "2026-09-10T10:00:00.600", "kind": "external", "text": "hi",
             "translation": "嗨", "asr_ms": 100, "llm_ms": 600},
            {"ts": "2026-09-10T10:00:05", "kind": "internal",
             "text": "bad line", "translation": "[翻译失败:APIConnectionError]",
             "asr_ms": 100, "llm_ms": 100},
        ]
        with open(sess, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write("{坏行 not json}\n")          # 坏行必须被跳过而不是崩掉
        srt = os.path.join(td, "out.srt")
        dest = export(sess, out=srt)
        text = Path(dest).read_text(encoding="utf-8")
        blocks = re.findall(
            r"(\d+)\n(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)\n"
            r"([^\n]*)\n?([^\n]*)\n", text)
        # 3 条：双语模式下译文失败那句保留原文（只丢译文），转录稿不缺句
        assert len(blocks) == 3, f"SRT 块数异常: {len(blocks)}"
        assert [b[0] for b in blocks] == ["1", "2", "3"]
        assert blocks[2][4] == "", "失败译文不应出现在文件里"

        def secs(hms: str) -> float:
            h, m, rest = hms.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        spans = [(secs(b[1]), secs(b[2])) for b in blocks]
        assert spans[0][0] == 0.0, "首条应对齐到 0"
        assert all(e > s for s, e in spans), "存在空/负时长字幕"
        assert spans[1][0] >= spans[0][1] - 1e-6, "时间轴重叠"
        assert blocks[0][3] == "hello" and blocks[0][4] == "你好", \
            f"双语内容/顺序异常（应为原文行在前、译文行在后）: {blocks[0]}"
        # 仅译文模式：失败译文那条被丢弃，只剩 2 条有意义内容 -> 实际 2 条
        cues = build_cues([rows[0]], mode="dst")
        assert [c.lines for c in cues] == [["你好"]]
        assert "翻译失败" not in to_srt(build_cues(rows, mode="bilingual"))
        # TXT：双语用 -> 连接
        from livetrans.export import to_txt
        assert to_txt(rows, mode="bilingual").splitlines()[0] == "hello → 你好"
    ok.append("export: SRT 时间轴推算/单调不重叠/双语对齐/坏行跳过 OK")
except Exception:
    traceback.print_exc(); sys.exit(1)


try:
    # 声纹角色标注：配色映射 / 未启用零开销 / 短段跳过 / 模型可用时同段同标签
    import numpy as np
    from livetrans.speaker import (SPEAKER_COLORS, SpeakerConfig, build_tracker,
                                   color_for, speaker_ready)
    assert color_for("S1") == SPEAKER_COLORS[0]
    assert color_for("S2") == SPEAKER_COLORS[1]
    assert color_for("") == SPEAKER_COLORS[0]
    assert build_tracker(None) is None
    assert build_tracker(SpeakerConfig(enabled=False)) is None
    spk_cfg = SpeakerConfig(enabled=True)
    if not speaker_ready(spk_cfg):
        print("[SKIP] 声纹：未安装 sherpa-onnx 或缺模型（可选功能），跳过真模型实测")
        ok.append("speaker: 配色映射 / 未启用零开销（可选依赖缺失，模型实测跳过）")
    else:
        tr = build_tracker(spk_cfg, lambda _m: None)
        sr = 16000
        t = np.arange(int(1.2 * sr)) / sr
        tone = (0.3 * np.sin(2 * np.pi * 140 * t)
                * (0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t))).astype(np.float32)
        assert tr.assign(tone) == "S1"                  # 首次 -> 新建 S1
        assert tr.assign(tone) == "S1"                  # 同段重复 -> 命中 S1
        assert tr.assign(np.zeros(int(0.2 * sr), np.float32)) == ""   # 太短不判定
        assert len(tr.speakers) == 1, tr.speakers
        tr.reset()
        assert tr.speakers == []
        ok.append("speaker: 配色/零开销/短段跳过 + 真模型同段同标签/reset OK")
except Exception:
    traceback.print_exc(); sys.exit(1)


try:
    # 对话模式：左右栏音频映射 / 每侧方向解析 / 配置默认值与一致性
    from livetrans.langs import (AUTO_DST, DIALOG_DEFAULTS, dialog_direction,
                                 dialog_directions, dialog_kinds,
                                 dialog_lang_pair, dialog_role_kinds,
                                 dialog_roles, migrate_dialog, roles_for_kind)
    from livetrans.config import DialogConfig
    assert dialog_direction(AUTO_DST) == ("中文", True)      # 目标自动：中文入译英
    assert dialog_direction("English") == ("English", False)
    assert dialog_direction("") == ("中文", False)
    assert dialog_direction("日本語") == ("日本語", False)
    assert vars(DialogConfig()).keys() == DIALOG_DEFAULTS.keys(), "默认值字段不一致"
    base = dict(DIALOG_DEFAULTS)
    # 角色 -> 音频路：两个角色**各自独立**，可指向同一路
    assert dialog_role_kinds(base) == {"self": "external", "other": "internal"}
    same = {**base, "other_source": "external"}            # 两人共用麦克风
    assert dialog_role_kinds(same) == {"self": "external", "other": "external"}
    assert roles_for_kind(same, "external") == ["self", "other"]   # 同一句两个角色都要
    assert roles_for_kind(same, "internal") == []
    assert roles_for_kind(base, "internal") == ["other"]
    assert dialog_role_kinds({"self_source": "乱填"}) == {
        "self": "external", "other": "internal"}          # 非法值回退
    # 方向跟角色走（不随来源/位置变化）
    roles = dialog_roles({**base, "other_source": "external", "other_dst_lang": "中文"})
    assert roles["self"]["source"] == "external"
    assert roles["other"]["source"] == "external"
    assert roles["self"]["dst_lang"] == "English" and roles["other"]["dst_lang"] == "中文"
    assert dialog_directions(base) == {"self": ("自动", "English"),
                                       "other": ("自动", "中文")}
    # 左右栏只由 left_role 决定（纯布局）
    assert dialog_kinds(base) == {"left": "external", "right": "internal"}
    assert dialog_kinds({**base, "left_role": "other"}) == {
        "left": "internal", "right": "external"}
    assert dialog_lang_pair({**base, "left_role": "other"}, "left") == ("自动", "中文")
    # 迁移：v1（按栏）-> v2（按音频路）-> v3（角色，来源互斥）-> v4（来源独立）
    m1 = migrate_dialog({"left_source": "internal", "left_dst_lang": "日本語",
                         "right_dst_lang": "English"})
    assert m1["self_dst_lang"] == "English" and m1["other_dst_lang"] == "日本語"
    assert m1["self_source"] == "external" and m1["left_role"] == "other"
    m2 = migrate_dialog({"left_source": "external", "external_dst_lang": "English",
                         "internal_dst_lang": "日本語"})
    assert m2["self_dst_lang"] == "English" and m2["other_dst_lang"] == "日本語"
    assert m2["left_role"] == "self"
    m3 = migrate_dialog({"self_source": "internal", "self_dst_lang": "English"})
    assert m3["other_source"] == "external", m3           # v3 互斥语义 -> 补齐另一路
    assert migrate_dialog({"self_source": "external", "other_source": "external"}) == {
        "self_source": "external", "other_source": "external"}    # v4 原样
    ok.append("dialog: 角色各自选来源(可同源)/方向跟角色/左右只换位置/三级迁移 OK")
except Exception:
    traceback.print_exc(); sys.exit(1)


try:
    # 本地模型显存管理：地址解析 / 服务不可达时不炸 / 默认设置
    from livetrans.config import LocalConfig
    from livetrans.sysmon import ollama_loaded, ollama_root, ollama_unload
    assert ollama_root() == "http://localhost:11434"
    assert ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert ollama_root("localhost:11434/v1") == "http://localhost:11434"
    dead = "http://127.0.0.1:1"                    # 不可达：按"没有常驻模型"处理
    assert ollama_loaded(dead) == []
    assert ollama_unload(["x"], base_url=dead) == {"x": False}
    lc = LocalConfig()
    assert lc.unload_others_on_start is True       # 只清"别的"模型
    assert lc.auto_unload_min == 0                 # 默认手动卸载
    assert lc.unload_on_exit is False              # 默认不随退出卸载
    ok.append("local: Ollama 地址解析/卸载容错 + 显存管理默认(手动) OK")
except Exception:
    traceback.print_exc(); sys.exit(1)


try:
    from livetrans.subtitle import DisplayItem
    ok.append("subtitle: 模块导入 OK（窗口需图形界面运行时验证）")
except Exception:
    traceback.print_exc(); sys.exit(1)

print("\n".join(f"[PASS] {m}" for m in ok))
print(f"\n全部 {len(ok)} 项自检通过。")
