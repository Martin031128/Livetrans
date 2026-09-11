"""会话导出：把 sessions/*.jsonl 转成 SRT 字幕 / TXT 文本（时间轴与内容对齐）。

【时间轴怎么来的】
JSONL 每条只记「该句完成时刻」ts（ISO 秒级），没有开始时间。推算规则：
    end   = ts
    start = end - max(min_cue, (asr_ms + llm_ms) / 1000)    # 该句端到端耗时
再强制单调不重叠（start = max(start, 上一句 end)），避免播放器时间轴倒退跳字。

【导出模式】
    bilingual  一条字幕两行：原文 + 译文（默认，适合对照/校对）
    dst        只要译文（给不看原文的人）
    src        只要原文（当听写稿用）

翻译失败的占位（"[翻译失败:...]"）在导出时自动丢弃，不会污染字幕文件。

【用法】
    python3 -m livetrans.export sessions/session-xxx.jsonl          # 同名 .srt
    python3 -m livetrans.export sessions/session-xxx.jsonl --mode dst
    python3 -m livetrans.export sessions/session-xxx.jsonl --txt    # 导纯文本
    python3 -m livetrans.export sessions/ --all                     # 批量（跳过 overlay-*）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MIN_CUE_SEC = 0.8          # 单条字幕最短显示时长（秒）
MODES = ("bilingual", "dst", "src")
FAIL_MARKS = ("[翻译失败", "[失败")


# ---------------- 数据读取 ----------------

def parse_ts(value) -> float:
    """记录里的 ts（ISO 字符串 / epoch 数值）-> epoch 秒；无法解析返回 0。"""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def _clean_dst(s) -> str:
    """译文清洗：翻译失败占位视为无效（返回空）。"""
    t = _clean(s)
    return "" if t.startswith(FAIL_MARKS) else t


def read_records(path: Path) -> list[dict]:
    """读会话 JSONL：逐行解析，坏行跳过（不因一行异常丢掉整场会话）。"""
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8",
                                     errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and _clean(rec.get("text")):
            out.append(rec)
    return out


# ---------------- 时间轴 ----------------

def format_ts(sec: float) -> str:
    """SRT 时间戳：HH:MM:SS,mmm（负值截断为 0，超 100 小时不回绕）。"""
    ms_total = max(0, int(round(sec * 1000)))
    h, rem = divmod(ms_total, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]

    def block(self, index: int) -> str:
        body = "\n".join(l for l in self.lines if l)
        return (f"{index}\n{format_ts(self.start)} --> {format_ts(self.end)}\n"
                f"{body}\n")


def build_cues(records: list[dict], mode: str = "bilingual",
               channel: str | None = None, min_cue: float = MIN_CUE_SEC,
               offset: float = 0.0) -> list[Cue]:
    """记录 -> 字幕条目（按时间排序、时间轴单调不重叠、时长由耗时推算）。

    每条记录被看作一段语音区间 [ts - 耗时, ts]（耗时 = 识别 + 翻译）。整条
    时间轴整体平移，使**第一句的起点**落在 offset（默认 0），句间停顿按原始
    时间戳保留；区间重叠时后一句顺延（时间轴只前进，播放器不跳字）。
    bilingual 模式下译文失败的那句仍保留原文（只丢译文），转录稿不缺句。
    """
    if mode not in MODES:
        raise ValueError(f"未知导出模式 {mode!r}（可选 {MODES}）")
    rows = [r for r in records if not channel or r.get("kind") == channel]
    rows.sort(key=lambda r: parse_ts(r.get("ts")))
    if not rows:
        return []

    def dur_of(r: dict) -> float:
        return max(min_cue, (float(r.get("asr_ms") or 0)
                             + float(r.get("llm_ms") or 0)) / 1000.0)

    base = parse_ts(rows[0].get("ts")) - dur_of(rows[0])   # 首句起点 -> offset
    cues: list[Cue] = []
    prev_end = 0.0
    for r in rows:
        src, dst = _clean(r.get("text")), _clean_dst(r.get("translation"))
        if mode == "dst":
            lines = [dst]
        elif mode == "src":
            lines = [src]
        else:
            lines = [src, dst]
        if not any(lines):
            continue
        dur = dur_of(r)
        end = parse_ts(r.get("ts")) - base + offset
        start = max(end - dur, prev_end, 0.0)               # 顺延，不重叠
        if end <= start:
            end = start + max(min_cue, 0.2)
        cues.append(Cue(start=start, end=end, lines=lines))
        prev_end = end
    return cues


def to_srt(cues: list[Cue]) -> str:
    return "".join(c.block(i) + "\n" for i, c in enumerate(cues, start=1))


def to_txt(records: list[dict], mode: str = "bilingual",
           channel: str | None = None) -> str:
    """纯文本：一行一句（双语用 "原文 → 译文"），便于贴进笔记/翻译工具。"""
    rows = [r for r in records if not channel or r.get("kind") == channel]
    rows.sort(key=lambda r: parse_ts(r.get("ts")))
    out: list[str] = []
    for r in rows:
        src, dst = _clean(r.get("text")), _clean_dst(r.get("translation"))
        if mode == "dst":
            line = dst
        elif mode == "src":
            line = src
        else:
            line = f"{src} → {dst}" if dst else src
        if line:
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")


# ---------------- 落盘 ----------------

def default_out(session: Path, suffix: str) -> Path:
    """默认输出：与会话同目录同名（与「总结」的 .summary.md 风格一致）。"""
    p = Path(session)
    return p.with_suffix(suffix)


def export(session: Path, out: Path | None = None, mode: str = "bilingual",
           channel: str | None = None, as_txt: bool = False,
           offset: float = 0.0) -> Path:
    """导出单个会话；返回写出的文件路径（无内容时抛 RuntimeError）。"""
    session = Path(session)
    records = read_records(session)
    if not records:
        raise RuntimeError(f"会话文件为空或无法解析：{session.name}")
    if as_txt:
        text = to_txt(records, mode=mode, channel=channel)
        dest = Path(out) if out else default_out(session, ".txt")
    else:
        text = to_srt(build_cues(records, mode=mode, channel=channel,
                                 offset=offset))
        dest = Path(out) if out else default_out(session, ".srt")
    if not text.strip():
        raise RuntimeError("所选模式/声道下没有可导出的内容")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


# ---------------- CLI ----------------

def _iter_sessions(target: Path, all_files: bool) -> list[Path]:
    if target.is_dir():
        files = sorted(target.glob("*.jsonl"))
        if not all_files:                       # 默认跳过 overlay-*（独立会话）
            files = [f for f in files if not f.name.startswith("overlay-")]
        return files
    return [target]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m livetrans.export",
        description="把会话 JSONL 导出为 SRT 字幕 / TXT 文本")
    ap.add_argument("target", help="会话 jsonl 文件，或 sessions 目录")
    ap.add_argument("-o", "--out", help="输出路径（仅单文件时可用）")
    ap.add_argument("--mode", choices=MODES, default="bilingual",
                    help="bilingual 原文+译文（默认）/ dst 仅译文 / src 仅原文")
    ap.add_argument("--channel", choices=("internal", "external"),
                    help="只导出某一路音频：internal 系统声音 / external 麦克风")
    ap.add_argument("--txt", action="store_true", help="导出纯文本而非 SRT")
    ap.add_argument("--all", action="store_true",
                    help="target 是目录时包含 overlay-*.jsonl")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="首条字幕起始秒数（默认 0，即把首句对齐到 0）")
    args = ap.parse_args(argv)

    files = _iter_sessions(Path(args.target), args.all)
    if not files:
        print("没找到任何 .jsonl 会话文件", file=sys.stderr)
        return 1
    if args.out and len(files) > 1:
        print("批量导出时不能指定 -o", file=sys.stderr)
        return 2
    failed = 0
    for f in files:
        try:
            dest = export(f, out=args.out, mode=args.mode, channel=args.channel,
                          as_txt=args.txt, offset=args.offset)
            recs = read_records(f)                  # 报实际导出条数（含过滤/丢弃）
            if args.txt:
                n = len([l for l in to_txt(recs, mode=args.mode,
                                           channel=args.channel).splitlines()
                         if l.strip()])
            else:
                n = len(build_cues(recs, mode=args.mode, channel=args.channel,
                                   offset=args.offset))
            print(f"✓ {f.name} -> {dest.name}（{n} 条）")
        except (RuntimeError, OSError) as e:
            failed += 1
            print(f"✗ {f.name}: {e}", file=sys.stderr)
    return 1 if failed and failed == len(files) else 0


if __name__ == "__main__":
    raise SystemExit(main())
