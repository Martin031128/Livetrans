#!/usr/bin/env bash
# LiveTrans 测试入口：自检 + 全部专项测试（不需要网络/音频；GUI 类用例需图形界面）
#
#   tests/run.sh            # 全跑
#   tests/run.sh router     # 只跑名字匹配的用例
#
# 用例清单：
#   test_router.py       对话模式路由（同源各翻一遍/按角色暂停/上下文隔离）
#   test_mirror.py       外挂镜像模式（跟随会话日志：不重放/坏行跳过/暂停/换会话）
#   test_fallback.py     弱网降级（连续失败切本地、恢复回切、启用条件）
#   test_e2e_dialog.py   真窗口+真路由+本地模型端到端（无 Ollama 自动跳过）
#   test_layout.py       三种布局（对话/外部/内部）+ 两栏等宽 + dual 归一
#   test_pane_render.py  栏内渲染（长句换行/缩窄重排/栏头不溢出）
#   test_ui_theme.py     控制台 UI 规范（按钮两档同高/输入域同高/窗口高度跟随页）
#   test_overlay_drag.py 外挂跨屏拖动（不被单屏范围弹回）
#   test_installer.py    安装脚本（dry-run 零副作用 / --remove-local 不误删）
#   test_installer_gui.py 图形向导（默认全勾选 / 线程不碰 Tk / 删除需确认）
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
FILTER="${1:-}"
cd "$ROOT" || exit 1

fail=0
echo "== 自检（selfcheck.py）=="
if python3 selfcheck.py >/tmp/lt-selfcheck.log 2>&1; then
    tail -1 /tmp/lt-selfcheck.log
else
    echo "FAIL"; tail -20 /tmp/lt-selfcheck.log; fail=1
fi

echo
echo "== 专项测试 =="
for t in "$HERE"/test_*.py; do
    name="$(basename "$t")"
    if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then
        continue
    fi
    printf "%-22s" "${name%.py}"
    out="$(timeout 240 python3 -u "$t" 2>&1)"
    code=$?
    if [ $code -eq 0 ] && ! grep -q "Traceback" <<<"$out"; then
        if grep -q "\[SKIP\]" <<<"$out"; then
            echo "SKIP  $(grep -m1 '\[SKIP\]' <<<"$out")"
        else
            echo "PASS  $(grep -c '\[PASS\]' <<<"$out") 项断言"
        fi
    else
        echo "FAIL (exit $code)"
        echo "$out" | tail -8
        fail=1
        # 在 GitHub Actions 里额外打一条注解：失败原因能直接在 API/Run 页面读到
        # （日志本身要登录才能拉，注解是公开可读的，方便定位）
        if [ -n "${GITHUB_ACTIONS:-}" ]; then
            msg="$(echo "$out" | tail -15 | sed 's/%/%25/g; s/\r//g' | paste -sd'|' - \
                   | sed 's/|/%0A/g; s/::/\\:\\:/g')"
            echo "::error title=测试失败 ${name%.py}::${msg}"
        fi
    fi
done

if [ -n "${GITHUB_ACTIONS:-}" ] && [ "$fail" != "0" ]; then
    echo "::error title=测试套件失败::selfcheck 或专项测试未全部通过，见上方 FAIL 行"
fi

echo
if [ $fail -eq 0 ]; then
    echo "全部测试通过 ✓"
else
    echo "有测试失败 ✗"
fi
exit $fail
