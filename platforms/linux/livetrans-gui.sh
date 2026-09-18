#!/usr/bin/env bash
# LiveTrans 控制台启动脚本（供 .desktop / 应用菜单调用，也可手动执行）
cd "$(dirname "$(readlink -f "$0")")"
exec python3 launcher.py
