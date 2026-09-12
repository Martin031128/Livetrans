"""临时:读取两个失败 check-run 的 summary(验证后删除)。"""
import json
import os

temp = os.environ.get("TEMP", r"C:\Users\majia\AppData\Local\Temp")
for fid in (103477742547, 103477751368):
    p = os.path.join(temp, f"cr_{fid}.json")
    d = json.load(open(p, encoding="utf-8"))
    if "message" in d and "output" not in d:
        print(f"===== {fid}: API 拒绝 -> {d['message']}")
        continue
    print(f"===== {fid} {d.get('name')} [{d.get('conclusion')}] =====")
    summary = (d.get("output") or {}).get("summary")
    print(summary or "(summary 为空)")
    print()
