#!/usr/bin/env bash
# 把 LiveTrans 加到当前用户的应用菜单（无需 root）。
# 等价于图形化安装向导里的「添加到应用菜单」；.desktop 里的路径按本机实际位置生成，
# 所以仓库里不需要、也不应该提交任何写死路径的 .desktop 文件。
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/livetrans.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=LiveTrans
GenericName=实时语音翻译
Comment=捕获系统与麦克风音频，实时双语字幕、翻译与会话总结
Exec=$DIR/livetrans-gui.sh
Icon=$DIR/assets/livetrans.svg
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=livetrans;translate;subtitle;asr;翻译;字幕;语音;
StartupWMClass=livetrans
EOF
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS" || true
echo "已安装到应用菜单: $APPS/livetrans.desktop"
echo "在应用列表或按 Super 键搜索 “LiveTrans” 即可启动。"
