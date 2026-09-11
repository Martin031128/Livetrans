"""会话总结 CLI：python3 -m livetrans.summarize <session.jsonl>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .translate import LLMTranslator, ensure_local_backend, is_local_base_url


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="livetrans.summarize", description="LiveTrans 会话总结")
    ap.add_argument("session", help="会话 JSONL 文件路径")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-o", "--output", help="输出 Markdown 文件（默认打印到终端）")
    ap.add_argument("--provider", help="总结用的服务商（默认取配置的翻译后端）")
    ap.add_argument("--model", help="总结用的模型（默认取该服务商配置）")
    args = ap.parse_args(argv)

    cfg = load_config(args.config if Path(args.config).is_file() else None)
    if args.provider:
        if args.provider not in cfg.providers:
            print(f"未知服务商: {args.provider}，可选: "
                  f"{', '.join(cfg.providers)}")
            return 1
        cfg.translate.provider = args.provider
    if args.model:
        cfg.providers[cfg.translate.provider].model = args.model
    records = []
    with open(args.session, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if not records:
        print("会话为空，无需总结。")
        return 1

    _prov = cfg.providers[cfg.translate.provider]
    if is_local_base_url(_prov.base_url):          # 本地后端：先确保服务在
        ensure_local_backend(_prov.base_url, _prov.model, print)
    translator = LLMTranslator(cfg.translate, cfg.providers)
    print(f"使用 {translator.backend}/{translator.model} 总结 {len(records)} 条记录 ...")
    md = translator.summarize(records)
    if args.output:
        Path(args.output).write_text(f"# 会话总结\n\n{md}\n", encoding="utf-8")
        print(f"已写入 {args.output}")
    else:
        print("\n" + md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
