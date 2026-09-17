# Windows → Linux 同步清单（2026-09-16 ~ 09-17）

> 目标读者：在 Linux 侧做对应修改的开发者。
> Windows 侧这些改动已全部提交到 `main`（未改版本号、未打标签），
> 涉及提交：`1d84342` `dd71910` `694aa08` `5158574` `170459f`
> 及更早的结构重构（configstore / Tooltip 收口 / import 清理 / 字体栈）。
> Linux 目录仍是冻结基线，以下按"可移植性"分级列出需要同步的内容。

## A. 平台无关 —— 建议优先移植

### 1. 上下文连续段落模式（`dd71910` `694aa08`）★ 最大项

字幕不再"一句一段"：短停顿不分段（≥3.5s 才分段），段落尾部由 LLM
持续修订（合并碎句、纠正识别错误、润色连贯）。默认开启。

| 文件 | 改动 |
|------|------|
| `config.py` | `TranslateConfig` 新增 `contextual=True` / `paragraph_gap_ms=3500` / `revise_depth=2`；`ASRConfig.silence_ms` 550→400 |
| `asr.py` | `SegmentEvent.gap_ms`；ASRWorker 估算真实停顿（相邻段完成时刻差 − 上段音频时长 + 尾部计入静音）；同段拆分的多句 gap=0 |
| `main.py` | `_Para` 段落状态机 + `PARTIAL_WORDS=5` / `MAX_PARA_SENTS=8`；`TranslatorWorker`：`feed_partial`/`partial_q`/`_drain_partials`/`_open_para`/`_close_para`/`_para_for`/`_partial_translate_task`/`_live_show`/`_maybe_revise`/`_revise_task`；`_join_parts`（CJK 直连/西文空格）`_text_weight` `_para_text` `_para_src` |
| `translate.py` | `SYSTEM_REVISE` 提示词 + `LLMTranslator.revise(sources, current, anchor)`（整段修订，非流式，ThinkFilter 兜底剥离） |
| `subtitle.py` | `_Pane.src_refs` + `_Pane.update_source`；`SubtitleWindow.update_source`（`sq` 队列，原文原地增长） |
| `ui/page_backend.py` `launcher.py` | 「翻译」页新增三项设置：上下文连续段落开关 / 分段停顿(秒) / 修订(句)；`_apply_and_save` 与 `_load_vals` 同步读写 |
| `config.example.yaml` | translate 段新增三键；asr.silence_ms 400 |
| `selfcheck.py` | silence_ms 断言同步为 400（**改默认值别忘了它**，这次踩过） |

设计要点（移植时别走弯路）：
- **整段修订而非尾部切片**：切片方案（tail 区间）在测试中暴露"区间前移
  丢内容"缺陷（合并后的文本拆不回句子），整段重写 + 段长上限（停顿 + 8 句）
  语义简单且上下文最完整；
- `revise_depth` 语义 = 每消化 N 句做一次修订（控制调用量，≈1.5x 而非 2x）；
  关段时**强制补一次修订**（末句必须被并进连贯文本）并留锚点给下一段衔接；
- 会话 jsonl **仍按句落盘**（摘要/导出/声纹不受影响），段落只是显示层概念；
- 修订/临时译文都可能乱序完成：段落渲染永远从状态推导（`_para_text`），
  不做增量字符串拼接。

### 2. 边讲边译（`170459f`）

partial 识别流（说话中每 0.8s 的增量快照，原先只喂"识别中"行）接入段落管线：
- 新增 ≥5 词（CJK 按字/西文按词，`_text_weight`）就翻一次**增量**，
  译文作为段落**临时尾句**（`_Para.live_*` 字段）流式显示；
- VAD 定稿句到达：`live_gen += 1` 作废在途 partial 翻译、清空临时尾句、
  正式句入段（partial 快照可能改写前面的字，由定稿替换兜底，原文不重复）；
- 长停顿后 partial 触发**预分段**（不等定稿，按 `last_activity` 判定）；
- 接线：`on_partial` 按模式分流——段落模式 → `worker.feed_partial`，
  旧模式 → `window.set_partial`；翻译线程未就绪时走旧行为。
  Linux 侧在 `main.py` 的 ASRWorker 构造处做同样分流。

### 3. 统一配置写入层 configstore（结构重构批次）

`livetrans/configstore.py`（新模块）：`patch_section` / `read_section` /
`refresh_sections`。此前 config.yaml 有三个独立"读-改-写"方（外挂
`persist_overlay`、主程序 `_persist_subtitle_style/_persist_dialog`、控制台
`_apply_and_save`），整文件重写互相覆盖——颜色丢失只是该通病的一个实例。
Linux 侧同样存在多处写 config.yaml 的话，建议一并收口。

### 4. Tooltip 收口

`subtitle.py` 的 `_Tooltip` 已删除，统一用 `ui/widgets.ToolTip`
（450ms 延迟 / Enter-Leave / 定位策略一致）。Linux 的 `subtitle.py`
若是平行拷贝，同样替换（12 处调用点 `_Tooltip(` → `ToolTip(`）。

### 5. 配色方案语义（外挂）

颜色从"改动即落盘"改为"会话生效 + 显式保存"：`persist_geom` **不再携带**
`text_color/bg_color`（拖动窗口/调字号不会固化试出来的配色）；
新增 `persist_scheme()` 只写颜色；⚙ 弹窗「保存方案 / 恢复默认」两键。
Linux 的 `overlay.py`（Tk 版）有平行的落盘逻辑，语义建议对齐。

### 6. 字体栈与色值统一

`ui/theme.py`：`pick_fonts()` 候选表双平台化（Windows 雅黑 UI/Cascadia
在前，Linux 原候选保留）；新增 `ui_family()` 懒解析助手（**导入期不能
枚举字体族**，需 Tk root 就绪后调用）；`DIM` 统一 #8b93a7。
Linux 侧主要收益是 DIM 统一与 `ui_family()` 收口散点写死字体。

### 7. 杂项

- 字幕窗拉高后画布跟随增高：`_apply_layout` 里窗格 grid 缺
  `grid_rowconfigure(0, weight=1)`（一行修复，Linux 布局代码若有同构
  问题一并补）；
- ui 六文件（page_audio/page_backend/page_chat/page_sessions/
  overlay_card/key_ui）未使用 import 清理（约 42 行）——Linux 平行文件
  可用同款脚本清理；
- `test_paragraph.py` 平台无关，可直接拷进 Linux tests/（自动发现）。

## B. Windows 专用 —— 不需要移植

- **loopback 跟随默认扬声器**（`1d84342`）：WASAPI loopback 只采绑定
  设备的输出流，切换默认扬声器后采到静音。Linux 的 parec/PulseAudio
  monitor 跟随默认 sink，无此问题；**可选**的对等增强：轮询
  `pactl get-default-sink` 变化时重开 monitor（Linux 目前每次启动时
  解析默认 sink，运行期不跟随）；
- `data discontinuity` 警告静默（soundcard/媒体基金会特定噪音）；
- 外挂 Qt 侧全部改动（悬停提示气泡 TipBubble、设置弹窗拖动/布局、
  UI 打磨、`overlay/` 拆包）——Linux 外挂是 Tk 实现，不需要；
- `OverlayConfig` 相关 configstore 补齐逻辑中针对外挂快照的部分。

## C. 版本与发布

- Windows 侧**未改版本号、未打标签**，所有改动在 `main`；
- Linux 侧同步完成并自测后，按你们的流程自行 bump 版本 + 打标签发版；
- 移植后建议先跑：`python selfcheck.py` + `python tests/run.py`
  （test_paragraph 应直接通过；Linux 特有断言如需调整在 Linux 侧改）。

## D. 移植顺序建议

1. `configstore.py` + 三处写入方收口（地基，其他改动都受益）；
2. `asr.py` gap_ms + `translate.py` revise + `config.py` 新键；
3. `main.py` 段落管线（最大的单块，建议整块搬）+ `subtitle.py` 显示层；
4. `on_partial` 分流（边讲边译）；
5. 控制台设置项 + example/selfcheck 同步；
6. Tooltip 收口 / 字体 / 杂项。
