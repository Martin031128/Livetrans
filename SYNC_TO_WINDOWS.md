# Linux → Windows 同步清单（2026-09-18）

> 目标读者：Windows 侧开发者（或协助开发的 AI）。
> 本文档由 Linux 侧用户实测反馈触发，**第 1 项必须同步**；第 2 项是运维注意事项；
> 第 3 项仅供参考、无需行动。
> Linux 侧对应提交：`e923c4b`（分段规则）、`1ac2fcb`（可执行位）。

---

## 1. 分段规则定稿：只看时间间隔，取消 8 句硬上限 ★ 必须同步

### 1.1 问题（用户实测）

用户反馈"**句子还没讲完就分段了**"。排查结论：

- 两平台 12 个段落管线函数逐字节一致，排除移植走样；
- 真因是 **`MAX_PARA_SENTS = 8` 硬上限**：连续说话到第 8 句时 `_para_for`
  强制关段另起——与时间间隔无关，违背用户定稿的规则；
- run.log 实证：段落 `para-external-…968.610` 覆盖到 8 句后立刻另起新段
  （10:52:59 → 10:53:09 之间用户仍在连续说话）。

### 1.2 用户定稿的规则

**分段只看时间间隔**，共两个条件（都是时间）：

1. 定稿句之间的真实停顿 > `paragraph_gap_ms`（默认 3500ms，控制台可调）→ 分段；
2. 说话中 partial 静默超过同一阈值 → 预分段（不等定稿）。

**句数不设上限。** 原设计的"防长独白把修订输入撑爆"由 `revise_depth`
（修订频率）继续兜底；字幕句很短，长段落修订的实际输入规模可接受。

### 1.3 Linux 侧已改内容（提交 `e923c4b`）

| 文件 | 改动 |
|---|---|
| `livetrans/main.py` | 删除 `MAX_PARA_SENTS = 8` 常量；`_para_for` 的并段条件去掉 `len(p.srcs) < MAX_PARA_SENTS`；`_Para` docstring 与模块头注释同步 |
| `livetrans/main.py` | 分段原因写入 run.log：`分段：距上句停顿约 4.2s ≥ 阈值 3.5s（段落共 5 句）` / `预分段：说话停顿 3.8s ≥ 阈值 3.5s` —— 以后"为什么在这里分段"直接查日志 |
| `tests/test_paragraph.py` | 导入去掉 `MAX_PARA_SENTS`；新增 `test_no_sentence_cap`（**10 句短停顿必须仍是 1 个段落**） |

### 1.4 Windows 侧移植点

1. `platforms/windows/livetrans/main.py`：
   - 删除 `MAX_PARA_SENTS = 8`（模块级，`_join_parts` 与 `_text_weight` 之间）；
   - `_para_for` 中并段条件 `ev.gap_ms <= gap_thr and len(p.srcs) < MAX_PARA_SENTS`
     → 只留 `ev.gap_ms <= gap_thr`；关段处加同款分段原因日志；
   - `_Para` docstring 里"段长由 3.5s 停顿 + MAX_PARA_SENTS=8 双重限定"一句同步改掉；
   - 模块头（若有）关于分段规则的注释同步。
2. `platforms/windows/tests/test_paragraph.py`：导入去掉 `MAX_PARA_SENTS`；
   从 Linux 侧拷入 `test_no_sentence_cap`（平台无关，直接可用）。
3. 自测：`python selfcheck.py` + `python tests/run.py`（test_paragraph 应全过）。

> ⚠ 两平台在此处**有意保持一致**：本次 Linux 先行，Windows 同步后该分叉点消除。
> 同步完成请在本文件打勾并记入 TECH_ROADMAP 开发日志。

- [ ] Windows 侧已同步分段规则

---

## 2. 运维注意：脚本可执行位会在 Windows 文件系统上丢失

**事故**：Windows 侧分叉时仓库文件在 Windows 文件系统过手，全部 `.sh` 的
POSIX 可执行位丢失（git mode 100755 → 100644）。后果是 Linux 侧
`livetrans-gui.sh` 不可执行 → GNOME 认为"该启动器无法启动"→
**应用网格与 Super 搜索直接不收录、点击无反应**（用户报"桌面图标消失"）。

- Linux 侧已修复：`1ac2fcb` 按 `git ls-tree <分叉前>` 的 755 清单逐一恢复
  （6 个文件：install-desktop / livetrans-gui / packaging 三脚本 / tests/run.sh）。
- **防复发**（Windows 侧注意）：
  - 在 Windows 上改动仓库后，push 前用 WSL 或 `git ls-files -s` 检查
    `.sh` 的 mode 是否还是 100755；
  - CI 可加一步兜底校验（可选）：`test -x platforms/linux/livetrans-gui.sh`。

---

## 3. 仅供参考：Linux 近期其他变更（无需 Windows 行动）

- **configstore 统一配置写入层**已落地 Linux（此前三处"读-改-写"互相覆盖，
  外挂配色/位置丢失的病根）；Windows 侧本就有。
- **上下文连续段落管线 / 边讲边译 / 控制台三项设置**已完成 Linux 同步
  （即 `SYNC_TO_LINUX.md` A 类，2026-09-18 全量落地，详见 TECH_ROADMAP 当日日志）。
- **音频设备热切换跟随**（Linux 特有增强，对齐 Windows `1d84342` 语义）：
  pactl 轮询默认 sink/source，变化只重启对应捕获链。Windows 侧已有等价实现，无需行动。
