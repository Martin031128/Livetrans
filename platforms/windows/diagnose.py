"""内部音源诊断探针：系统音频捕获 -> VAD 切段 -> SenseVoice 转写，逐段打印。

用途：网页/应用播放声音时，验证"系统输出 -> WASAPI loopback -> 识别"链路是否通畅。
没有字幕时先跑它：能转写出文本 = 链路 OK（问题在别处）；overall_rms≈0 =
系统输出设备上根本没有声音（查音量/静音/播放设备选择）。

用法（platforms/windows 目录下）：
  python diagnose.py [秒数=20] [语言=en|zh|auto]
示例：
  python diagnose.py 20 en      # 浏览器播放英文视频时运行，20 秒后出结论
"""
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from livetrans.asr import SenseVoiceASR, VadSegmenter         # noqa: E402
from livetrans.capture import (ParecCapture, SourceInfo,     # noqa: E402
                               default_monitor_source)


def main() -> int:
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    lang = (sys.argv[2] if len(sys.argv) > 2 else "en")
    lang = None if lang == "auto" else lang

    mon = default_monitor_source()
    if not mon:
        print("未找到系统音频捕获设备（确认已安装 soundcard："
              "python -m pip install soundcard，且系统存在可用的播放设备）")
        return 1
    print(f"监听: {mon}（{dur:.0f}s，语言={lang or '自动'}）——现在播放声音 ...")

    asr = SenseVoiceASR(lang)
    seg = VadSegmenter(aggressiveness=3, min_speech_ms=250, silence_ms=700,
                       max_segment_sec=15.0, pre_buffer_ms=300)
    q: "queue.Queue" = queue.Queue()
    cap = ParecCapture(SourceInfo("probe", "internal", "probe", "probe"),
                       mon, q, 16000)
    cap.start()

    total, energy, n, segs = 0, 0.0, 0, 0
    t0 = time.time()
    while time.time() - t0 < dur:
        try:
            chunk = q.get(timeout=0.5)
        except queue.Empty:
            continue
        total += chunk.size
        energy += float(np.sum(chunk.astype(np.float64) ** 2))
        n += chunk.size
        for s in seg.feed(chunk):
            segs += 1
            text = asr.transcribe(s)
            rms = float(np.sqrt(np.mean(s.astype(np.float64) ** 2)))
            print(f"  [段{segs}] {s.size/16000:.1f}s rms={rms:.3f} "
                  f"识别: {text!r}")
    cap.stop()

    rms = float(np.sqrt(energy / n)) if n else 0.0
    print(f"捕获 {total/16000:.1f}s，整体 RMS={rms:.4f}，切出 {segs} 段")
    if rms < 0.002:
        print("=> 捕获设备上没有声音：检查播放器/系统音量与静音、"
              "声音是否输出了别的设备、网页是否真的在播放。")
        return 1
    if segs == 0:
        print("=> 有声音但 VAD 未切出语音段（音量太小或非语音内容）。")
        return 1
    print("=> 链路正常：系统音频能收到声音并识别出文本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
