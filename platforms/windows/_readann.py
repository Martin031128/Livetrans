"""临时:拉取两个失败 job 的 ::error 注解(验证后删除)。"""
import json
import os
import urllib.request

temp = os.environ.get("TEMP", r"C:\Users\majia\AppData\Local\Temp")
for fid in (103480635239, 103480644272):
    url = (f"https://api.github.com/repos/Martin031128/Livetrans/"
           f"check-runs/{fid}/annotations")
    req = urllib.request.Request(url, headers={"User-Agent": "livetrans-debug"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    print(f"===== job {fid}: {len(data)} 条注解 =====")
    for a in data:
        if a.get("annotation_level") == "failure":
            print(a.get("message", "")[:3000])
    print()
