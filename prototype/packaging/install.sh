#!/usr/bin/env bash
# LiveTrans 一键安装（源码/开发环境）
#
# 默认帮你把「本地模型」也准备好（不想用本地模型就加 --no-models，云端 API 一样能跑）：
#   1) 检查系统依赖（Tk 界面 / GTK3 外挂 / parec 抓系统声音 / 中文字体）
#   2) 安装 Python 依赖（pip --user，不动系统 Python）
#   3) 下载本地识别模型 SenseVoice（约 240MB）+ 声纹模型（约 28MB）
#   4) 准备本地翻译服务：拉起 Ollama 并拉取本地模型（默认 qwen3:4b-instruct）
#
# 用法：
#   bash packaging/install.sh                  # 全部默认（本地模型 + Ollama 都装）
#   bash packaging/install.sh --no-models      # 只用云端 API：不下载任何本地模型
#   bash packaging/install.sh --no-asr         # 跳过识别模型（240MB）
#   bash packaging/install.sh --no-speaker     # 跳过声纹模型（28MB，可选功能）
#   bash packaging/install.sh --no-ollama      # 不碰 Ollama / 本地翻译模型
#   bash packaging/install.sh --ask            # 逐项询问（默认都是「是」，回车即同意）
#   bash packaging/install.sh --with-apt       # 缺系统包时顺手 sudo apt install
#   bash packaging/install.sh --install-ollama # 用官方脚本安装 Ollama（需要 sudo）
#   bash packaging/install.sh --dry-run        # 只打印计划，不动任何东西
#   bash packaging/install.sh --remove-local   # 事后清理：删已下载的本地模型
#
# 安装后随时可删本地模型（腾空间；下次要用会自动重新下载）：
#   bash packaging/install.sh --remove-local
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DO_ASR=1; DO_SPEAKER=1; DO_OLLAMA=1; DO_PIP=1
ASK=0; DRY=0; WITH_APT=0; INSTALL_OLLAMA=0; REMOVE=0; ASSUME_YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --no-models) DO_ASR=0; DO_SPEAKER=0; DO_OLLAMA=0 ;;
        --no-asr) DO_ASR=0 ;;
        --no-speaker) DO_SPEAKER=0 ;;
        --no-ollama) DO_OLLAMA=0 ;;
        --no-pip) DO_PIP=0 ;;
        --ask) ASK=1 ;;
        --dry-run) DRY=1 ;;
        --with-apt) WITH_APT=1 ;;
        --install-ollama) INSTALL_OLLAMA=1 ;;
        --remove-local) REMOVE=1 ;;
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$1（--help 看用法）" >&2; exit 2 ;;
    esac
    shift
done

BOLD=""; DIM=""; OFF=""
if [ -t 1 ]; then BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'; fi
step() { printf "\n%s== %s ==%s\n" "$BOLD" "$1" "$OFF"; }
info() { printf "  %s\n" "$1"; }
note() { printf "  %s%s%s\n" "$DIM" "$1" "$OFF"; }
ok()   { printf "  ✓ %s\n" "$1"; }
warn() { printf "  ! %s\n" "$1"; }

# 交互确认（--ask 时生效；默认同意 = 直接回车即「是」）
confirm() {
    [ "$ASK" = 1 ] || return 0
    local ans
    read -r -p "   $1 [Y/n] " ans
    case "$ans" in [nN]*) return 1 ;; *) return 0 ;; esac
}
# 执行（--dry-run 只打印）
run() {
    if [ "$DRY" = 1 ]; then note "[dry-run] $*"; return 0; fi
    "$@"
}

py() { python3 -c "$1" 2>/dev/null; }

# 本地模型目录与本地翻译模型名（都从程序自己的代码里取，避免写死）
MODELS_DIR="$(cd "$ROOT" && py 'from livetrans.paths import MODELS_DIR; print(MODELS_DIR)')"
[ -n "$MODELS_DIR" ] || MODELS_DIR="$ROOT/models"
LOCAL_MODEL="$(py '
from livetrans.config import load_config
from livetrans.translate import is_local_base_url
cfg = load_config(None)
print(next((p.model for p in cfg.providers.values()
            if is_local_base_url(getattr(p, "base_url", ""))), ""))')"
[ -n "$LOCAL_MODEL" ] || LOCAL_MODEL="qwen3:4b-instruct"

du_mb() {  # 人类可读体积
    du -sh "$1" 2>/dev/null | cut -f1
}

# ---------------------------------------------------------------- 清理模式
if [ "$REMOVE" = 1 ]; then
    step "清理本地模型（可随时重装）"
    if [ -d "$MODELS_DIR" ]; then
        info "本地模型目录：$MODELS_DIR （$(du_mb "$MODELS_DIR")）"
        if [ "$ASSUME_YES" = 0 ] && [ "$DRY" = 0 ]; then
            read -r -p "   确认删除？[y/N] " a
            case "$a" in [yY]*) ;; *) echo "   已取消。"; exit 0 ;; esac
        fi
        run rm -rf "$MODELS_DIR"
        if [ "$DRY" = 1 ]; then
            note "[dry-run] 未真正删除"
        else
            ok "已删除（识别/声纹模型会在下次用到时自动重新下载）"
        fi
    else
        info "没有找到本地模型目录（$MODELS_DIR），无需清理"
    fi
    if command -v ollama >/dev/null 2>&1; then
        if ollama list 2>/dev/null | awk '{print $1}' | grep -q "^${LOCAL_MODEL%%:*}"; then
            info "Ollama 里的本地翻译模型：$LOCAL_MODEL"
            if [ "$DRY" = 1 ]; then
                note "[dry-run] ollama rm $LOCAL_MODEL"
            else
                ollama rm "$LOCAL_MODEL" && ok "已移除 $LOCAL_MODEL"
            fi
        fi
        note "（如需卸载 Ollama 本体：sudo rm /usr/local/bin/ollama 或按官网说明）"
    fi
    step "完成"
    info "保留：Python 依赖（pip 包，很小）。要重装本地模型再跑一次本脚本即可。"
    exit 0
fi

# ---------------------------------------------------------------- 安装模式
printf "%sLiveTrans 安装%s\n" "$BOLD" "$OFF"
note "项目目录：$ROOT"
[ "$DRY" = 1 ] && note "（dry-run：只打印计划，不会改动任何东西）"
echo
echo "将要执行："
info "$( [ "$DO_PIP" = 1 ] && echo "· 安装 Python 依赖（pip --user）" || echo "· 跳过 Python 依赖" )"
info "$( [ "$DO_ASR" = 1 ] && echo "· 下载本地识别模型 SenseVoice（约 240MB）" || echo "· 跳过识别模型（改用云端/已有模型）" )"
info "$( [ "$DO_SPEAKER" = 1 ] && echo "· 安装声纹（sherpa-onnx）+ 下载声纹模型（约 28MB）" || echo "· 跳过声纹" )"
info "$( [ "$DO_OLLAMA" = 1 ] && echo "· 准备本地翻译模型 Ollama（$LOCAL_MODEL）" || echo "· 跳过 Ollama（用云端 API 翻译）" )"
note "以上都可以稍后单独安装或删除：bash packaging/install.sh --remove-local"

# ---- 1) 系统依赖 ----
step "1/4 系统依赖检查"
SYS_MISS="$(py '
from livetrans.deps import missing_overlay_deps
d = missing_overlay_deps()
print(" ".join(d))' )"
if [ -z "$SYS_MISS" ]; then
    ok "Tk / GTK3 / parec / 字体等系统组件齐备"
else
    warn "缺少系统组件：$SYS_MISS"
    info "安装命令：sudo apt install python3-tk python3-gi gir1.2-gtk-3.0 \\"
    info "            gir1.2-pango-1.0 python3-cairo pulseaudio-utils fonts-noto-cjk"
    if [ "$WITH_APT" = 1 ]; then
        run sudo apt install -y python3-tk python3-gi gir1.2-gtk-3.0 \
            gir1.2-pango-1.0 python3-cairo pulseaudio-utils fonts-noto-cjk
    else
        note "（加 --with-apt 可让本脚本自动执行上面这条）"
    fi
fi

# ---- 2) Python 依赖 ----
step "2/4 Python 依赖"
if [ "$DO_PIP" = 1 ] && confirm "安装 Python 依赖（pip --user）？"; then
    if ! python3 -m pip --version >/dev/null 2>&1; then
        warn "没有 pip：sudo apt install python3-pip"
    else
        run python3 -m pip install --user -r requirements.txt
        ok "Python 依赖完成"
    fi
else
    note "已跳过"
fi

# ---- 3) 本地识别模型 / 声纹 ----
step "3/4 本地模型（识别 + 声纹）"
if [ "$DO_ASR" = 1 ] && confirm "下载本地识别模型 SenseVoice（约 240MB）？"; then
    if [ "$DRY" = 1 ]; then
        note "[dry-run] 下载 SenseVoice 模型到 $MODELS_DIR/sensevoice"
    else
        info "下载中（首次约 240MB，进度见下方百分比）..."
        if python3 -c 'from livetrans.asr import preload_model; preload_model()'; then
            ok "识别模型就绪：$MODELS_DIR/sensevoice"
        else
            warn "识别模型下载失败（可稍后重跑本脚本，或直接用云端 API）"
        fi
    fi
else
    note "已跳过识别模型"
fi

if [ "$DO_SPEAKER" = 1 ] && confirm "安装声纹角色标注（28MB 模型，多人会议区分说话人）？"; then
    run python3 -m pip install --user sherpa-onnx || true
    if [ "$DRY" = 1 ]; then
        note "[dry-run] 下载声纹模型到 $MODELS_DIR/speaker"
    else
        python3 -c '
from livetrans.speaker import ensure_speaker_model
p = ensure_speaker_model(log=print)
print(f"声纹模型: {p}" if p else "声纹模型未就绪（不影响识别/翻译）")'
    fi
else
    note "已跳过声纹"
fi

# ---- 4) 本地翻译模型（Ollama）----
step "4/4 本地翻译模型（Ollama）"
if [ "$DO_OLLAMA" = 1 ] && confirm "准备本地翻译模型（$LOCAL_MODEL）？"; then
    if ! command -v ollama >/dev/null 2>&1; then
        warn "未安装 Ollama"
        info "安装：curl -fsSL https://ollama.com/install.sh | sh"
        if [ "$INSTALL_OLLAMA" = 1 ]; then
            run sh -c 'curl -fsSL https://ollama.com/install.sh | sh'
        else
            note "（加 --install-ollama 可让本脚本自动安装；装好后重跑本脚本拉模型）"
        fi
    else
        if [ "$DRY" = 1 ]; then
            note "[dry-run] 启动 ollama serve 并 ollama pull $LOCAL_MODEL"
        else
            python3 -c '
from livetrans.config import load_config
from livetrans.translate import ensure_local_backend, is_local_base_url
cfg = load_config(None)
p = next((p for p in cfg.providers.values()
          if is_local_base_url(getattr(p, "base_url", ""))), None)
if p is not None:
    ensure_local_backend(p.base_url, p.model, print)' || true
            info "拉取模型 $LOCAL_MODEL（首次约 2.5GB）..."
            ollama pull "$LOCAL_MODEL" && ok "本地翻译模型就绪：$LOCAL_MODEL"
            note "（用不上可删：ollama rm $LOCAL_MODEL）"
        fi
    fi
else
    note "已跳过本地翻译模型（控制台里选云端服务商 + 填 Key 即可）"
fi

# ---- 汇总 ----
step "完成"
if [ "$DRY" = 0 ]; then
    [ -d "$MODELS_DIR" ] && info "本地模型占用：$(du_mb "$MODELS_DIR")  （$MODELS_DIR）"
fi
echo
echo "下一步："
info "1) 启动控制台：python3 launcher.py"
info "2) 「翻译后端」页：云端选服务商 + 填 API Key；或切「本地模型」用 Ollama"
info "3) 「音频源」页选要识别的来源 → 点底部「保存并启动」"
echo
note "不想要本地模型了：bash packaging/install.sh --remove-local"
note "（识别/声纹模型随用随下，删掉不影响云端 API 的使用）"
