#!/bin/sh
# 安装 LiveTrans 的 Python 依赖（pip 侧）。
#
# deb 只声明 apt 里有系统包依赖；sounddevice / funasr_onnx / openai 这些
# pip 包在发行版仓库里没有对应包，所以单独用这个脚本装到用户目录（--user），
# 不动系统 Python。
set -e

REQ="${LIVETRANS_REQUIREMENTS:-/opt/livetrans/requirements.txt}"
if [ ! -f "$REQ" ]; then
    # 源码运行时直接用仓库里的清单
    REQ="$(cd "$(dirname "$0")/.." && pwd)/requirements.txt"
fi

echo "== 安装 Python 依赖（--user）：$REQ =="
python3 -m pip install --user -r "$REQ"

echo
echo "可选组件（本地模型；只想用云端 API 可以不装）："
echo "  一键准备（识别模型 / 声纹 / Ollama 本地翻译模型）："
echo "    bash packaging/install.sh"
echo "  只想用云端："
echo "    bash packaging/install.sh --no-models"
echo "  装完想删掉："
echo "    bash packaging/install.sh --remove-local"
