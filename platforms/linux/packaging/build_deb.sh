#!/usr/bin/env bash
# LiveTrans -> Debian/Ubuntu 安装包（.deb）
#
#   packaging/build_deb.sh                    # 默认不打包模型：首次运行联网自动下载
#   packaging/build_deb.sh --offline          # 完全离线包：连本地模型一起打包（约 +270MB）
#   WITH_MODELS=1 packaging/build_deb.sh      # 同上（等价写法）
#   VERSION=1.1.0 packaging/build_deb.sh      # 指定版本号
#
# 布局：
#   /opt/livetrans/…              程序与资源（代码目录，安装后只读）
#   /usr/bin/livetrans            启动器：把数据目录指到 ~/.local/share/livetrans
#   /usr/share/applications/…     应用菜单项（图标 assets/livetrans.svg）
#   /usr/share/doc/livetrans/…    依赖说明 + pip 依赖安装脚本
#
# 数据（config.yaml / keys.env / sessions / run.log）不进包，
# 由启动器通过 LIVETRANS_DATA_DIR 落到用户目录（源码运行则仍在项目目录里）。
# 模型：默认不打包（首次运行自动下载）；--offline 时打到
#   /usr/share/livetrans/models（系统只读目录，运行时按搜索路径直接读取）。
set -euo pipefail

WITH_MODELS="${WITH_MODELS:-0}"
for arg in "$@"; do
    case "$arg" in
        --offline|--with-models) WITH_MODELS=1 ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 版本号唯一来源：livetrans/__init__.py 的 __version__（可用 VERSION= 环境变量覆盖）
VERSION="${VERSION:-$(sed -n 's/^__version__ = "\(.*\)".*/\1/p' \
    "$ROOT/livetrans/__init__.py" | head -1)}"
VERSION="${VERSION:-1.0.0}"
DIST="$ROOT/dist"
PKG="$DIST/livetrans_${VERSION}_all"
DEB="$DIST/livetrans_${VERSION}_all.deb"

command -v dpkg-deb >/dev/null || {
    echo "需要 dpkg-deb：sudo apt install dpkg-dev" >&2; exit 1; }
command -v rsync >/dev/null || {
    echo "需要 rsync：sudo apt install rsync" >&2; exit 1; }

echo "== 准备目录 =="
rm -rf "$PKG" "$DEB"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/livetrans" "$PKG/usr/bin" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" \
         "$PKG/usr/share/doc/livetrans"

echo "== 复制程序（数据/缓存/密钥不进包）=="
# 绝不进包的东西：密钥、配置、会话内容、日志、历史、构建产物、编辑器目录
EXCLUDES=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.git'
          --exclude 'dist' --exclude 'sessions' --exclude 'run.log*'
          --exclude 'keys.env' --exclude 'keys.yaml' --exclude '*.env'
          --exclude 'api_history.yaml' --exclude 'api_history*'
          --exclude 'config.yaml' --exclude 'glossary.yaml'
          --exclude '.codebuddy' --exclude '.vscode' --exclude '.idea'
          --exclude '*.deb' --exclude '*.pyc')
# 模型永远不放进 /opt：那里是只读代码目录，运行时按 MODELS_DIR/BASE 找不到它。
# 离线包统一放到 /usr/share/livetrans/models（paths.SYSTEM_MODELS_DIR）供读取。
EXCLUDES+=(--exclude 'models')
rsync -a "${EXCLUDES[@]}" "$ROOT/" "$PKG/opt/livetrans/"
if [ "$WITH_MODELS" = "1" ]; then
    if [ ! -d "$ROOT/models/sensevoice" ]; then
        echo "缺少本地模型：请先 bash packaging/install.sh（或用 --no-models 不打包模型）" >&2
        exit 1
    fi
    mkdir -p "$PKG/usr/share/livetrans"
    rsync -a --delete "$ROOT/models/" "$PKG/usr/share/livetrans/models/"
    echo "== 已打入本地模型（离线可用）：$(du -sh "$PKG/usr/share/livetrans/models" | cut -f1)"
    rm -rf "$PKG/usr/share/livetrans/models/sensevoice/.cache"
fi

cp "$ROOT/packaging/install-python-deps.sh" "$PKG/usr/share/doc/livetrans/"
cp "$ROOT/packaging/install.sh" "$PKG/usr/share/doc/livetrans/"
# Debian 惯例：许可证放 /usr/share/doc/<pkg>/copyright
cp "$ROOT/../LICENSE" "$PKG/usr/share/doc/livetrans/copyright"
chmod 755 "$PKG/usr/share/doc/livetrans/install-python-deps.sh" \
         "$PKG/usr/share/doc/livetrans/install.sh"
cp "$ROOT/packaging/livetrans.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/installer_gui.py" "$PKG/usr/share/doc/livetrans/"
chmod 755 "$PKG/usr/share/doc/livetrans/installer_gui.py"
# 安装向导也进应用菜单（可随时补装/删除本地模型）
cat > "$PKG/usr/share/applications/livetrans-installer.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Version=1.0
Name=LiveTrans 安装向导
GenericName=安装向导
Comment=准备本地模型（离线识别/离线翻译），或删除它们
Exec=livetrans-installer
Icon=livetrans
Terminal=false
Categories=AudioVideo;Audio;Utility;Settings;
Keywords=livetrans;install;model;模型;安装;
StartupWMClass=livetrans-installer
DESK

cp "$ROOT/assets/livetrans.svg" \
   "$PKG/usr/share/icons/hicolor/scalable/apps/livetrans.svg"

echo "== 写启动器 =="
cat > "$PKG/usr/bin/livetrans" <<'SH'
#!/bin/sh
# LiveTrans 启动器：代码在 /opt/livetrans，配置/密钥/会话/日志/模型放用户目录
: "${LIVETRANS_DATA_DIR:=$HOME/.local/share/livetrans}"
export LIVETRANS_DATA_DIR
exec python3 /opt/livetrans/launcher.py "$@"
SH
chmod 755 "$PKG/usr/bin/livetrans"

cat > "$PKG/usr/bin/livetrans-installer" <<'SH'
#!/bin/sh
# LiveTrans 图形化安装向导：装/补装/删除本地模型
: "${LIVETRANS_DATA_DIR:=$HOME/.local/share/livetrans}"
export LIVETRANS_DATA_DIR
exec python3 /opt/livetrans/packaging/installer_gui.py "$@"
SH
chmod 755 "$PKG/usr/bin/livetrans-installer"

echo "== 安全检查（密钥 / 用户数据绝不允许进包）=="
BAD_FILES=$(find "$PKG" \( -name '*.env' -o -name 'keys.yaml' -o -name 'api_history*' \
                         -o -name 'config.yaml' -o -name 'glossary.yaml' \
                         -o -name '*.jsonl' -o -name 'run.log*' \) \
            -not -path '*/models/*' 2>/dev/null || true)
if [ -n "$BAD_FILES" ]; then
    echo "!! 包里出现了不该有的文件，已中止：" >&2
    echo "$BAD_FILES" >&2
    exit 1
fi
# 只扫数据类文件，避免把测试里的假 key 当命中（如 selfcheck 的 sk-xxxx 断言）
SECRET_SRC=(--include='*.yaml' --include='*.yml' --include='*.env'
            --include='*.json' --include='*.jsonl' --include='*.log'
            --include='*.txt' --include='*.ini' --include='*.conf')
SECRET_HITS=$(grep -rIl "${SECRET_SRC[@]}" \
    -E 'sk-[A-Za-z0-9_-]{16,}|[0-9a-f]{32}\.[A-Za-z0-9_-]{8,}|(api[_-]?key|token|secret)["'"'"' :=]+[A-Za-z0-9_-]{16,}' \
    "$PKG" 2>/dev/null | grep -v '/models/' || true)
if [ -n "$SECRET_HITS" ]; then
    echo "!! 疑似密钥进入安装包，已中止（确认后修掉再打包）：" >&2
    echo "$SECRET_HITS" >&2
    exit 1
fi
echo "  ✓ 无密钥、无用户配置/会话/日志"

echo "== 写控制信息 =="
INSTALLED=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: livetrans
Version: $VERSION
Section: sound
Priority: optional
Architecture: all
Installed-Size: $INSTALLED
Depends: python3 (>= 3.10), python3-tk, python3-numpy, python3-yaml, python3-gi, gir1.2-gtk-3.0, gir1.2-pango-1.0, python3-cairo, pulseaudio-utils, libportaudio2
Recommends: fonts-noto-cjk, python3-pip
Suggests: ollama
Maintainer: Martin031128 <Martin031128@users.noreply.github.com>
Homepage: https://github.com/Martin031128/Livetrans
Description: 实时语音翻译与双语字幕（麦克风 + 系统声音）
 LiveTrans 捕获麦克风与系统播放的声音，本地识别（SenseVoice）后经 LLM 翻译，
 在主字幕窗与悬浮字幕外挂上显示双语字幕；含图形控制台、会话总结与对话助手。
 翻译后端可用云端 API（DeepSeek/GLM/千问/Kimi/OpenAI/Gemini/Claude/Grok），
 也可用本地 Ollama（无需联网、无需 API Key）。
 可选完全离线版（含本地模型，用 packaging/build_deb.sh --offline 打包）。
EOF

cat > "$PKG/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
echo "LiveTrans 已安装：从应用菜单启动，或终端执行 livetrans"
echo "图形化安装向导（准备/删除本地模型）：livetrans-installer"
echo "首次运行若提示缺少 Python 依赖，执行："
echo "    /usr/share/doc/livetrans/install-python-deps.sh"
echo "    /usr/share/doc/livetrans/install.sh   # 按需准备本地模型 / 事后删除"
echo "（或 pip install --user -r /opt/livetrans/requirements.txt）"
exit 0
SH
chmod 755 "$PKG/DEBIAN/postinst"

cat > "$PKG/DEBIAN/postrm" <<'SH'
#!/bin/sh
# 卸载时不删用户数据（配置/密钥/会话/模型都在 ~/.local/share/livetrans）
exit 0
SH
chmod 755 "$PKG/DEBIAN/postrm"

echo "== 构建 .deb =="
dpkg-deb --build --root-owner-group "$PKG" "$DEB" >/dev/null
dpkg-deb -I "$DEB" | sed -n '1,14p'
echo
# 暂存目录（离线包时能到 240MB+）默认清掉：KEEP_STAGE=1 可保留以便检查包内布局
if [ "${KEEP_STAGE:-0}" = "1" ]; then
    echo "（已保留暂存目录：$PKG）"
else
    rm -rf "$PKG"
fi
echo "完成：$DEB  ($(du -h "$DEB" | cut -f1))"
echo "安装： sudo dpkg -i $DEB   （依赖缺失时 sudo apt -f install）"
