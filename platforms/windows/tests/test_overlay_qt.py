"""字幕外挂（PySide6/Qt6）：接口契约、穿透开关、几何与内容渲染。

守的坑：
1. **穿透开关不能把 WS_EX_LAYERED 一起清掉** —— LAYERED 是透明背景的前提，
   清掉后字幕会变成一块不透明黑矩形（视觉上完全坏掉，但不报错，很难查）。
2. **接口必须与 SubtitleWindow 一致** —— TranslatorWorker/ASRWorker 直接调用
   post/update_translation/set_partial/set_level/set_status，少一个就崩。
3. **不能抢焦点**（WS_EX_NOACTIVATE）—— 否则看视频时字幕窗会把焦点从播放器抢走。
4. 长时间文本要能自动增高，不能把文字裁掉。
5. `_current()` 的声纹标注规则：只在**换人**时加 [Sx]，同一人连续说话不重复。

Windows 专属用例；非 Windows 或未装 PySide6 时整体 SKIP。
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform != "win32":
    print(f"[SKIP] Qt 外挂用例仅在 Windows 运行（当前平台 {sys.platform}）")
    raise SystemExit(0)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QPushButton,
                                   QSlider, QWidget)
except ImportError as e:
    print(f"[SKIP] 未安装 PySide6（{e}）")
    raise SystemExit(0)

from livetrans.config import load_config                       # noqa: E402
from livetrans.overlay import (GWL_EXSTYLE, WS_EX_LAYERED,      # noqa: E402
                               WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                               WS_EX_TRANSPARENT, OverlayWindow,
                               persist_overlay, _hwnd)
from livetrans.subtitle import DisplayItem                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_cfg = load_config(str(ROOT / "config.yaml"))
_app = QApplication.instance() or QApplication(sys.argv)
_ov = OverlayWindow(_cfg.overlay, config_path=None)
user32 = ctypes.windll.user32


def _ext_style() -> int:
    return user32.GetWindowLongW(_hwnd(_ov), GWL_EXSTYLE)


def _pump(ms: int = 120) -> None:
    """跑一会儿事件循环（让定时器/绘制有机会执行）。"""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_pipeline_interface():
    """必须实现与 SubtitleWindow 相同的接口（管线要调用）。"""
    need = ["post", "update_translation", "set_partial", "set_level",
            "set_status", "toggle_visible", "toggle_pause", "prev", "next",
            "jump_latest", "quit", "set_opacity", "set_text_opacity",
            "toggle_edit_mode", "set_click_through", "set_mirror", "set_source",
            "persist_geom"]
    missing = [m for m in need if not hasattr(_ov, m)]
    assert not missing, f"缺少接口: {missing}"
    print(f"[PASS] 管线接口齐全（{len(need)} 个）")


def test_click_through_toggle():
    """穿透开关：TRANSPARENT 正确增删，且不动 LAYERED（透明背景前提）。"""
    _ov.set_click_through(True)
    s_on = _ext_style()
    assert s_on & WS_EX_TRANSPARENT, "开穿透失败：WS_EX_TRANSPARENT 未设置"
    assert s_on & WS_EX_LAYERED, "开穿透时丢了 WS_EX_LAYERED"

    _ov.set_click_through(False)
    s_off = _ext_style()
    assert not (s_off & WS_EX_TRANSPARENT), "关穿透失败：WS_EX_TRANSPARENT 仍在"
    assert s_off & WS_EX_LAYERED, \
        "关穿透时误删 WS_EX_LAYERED —— 透明背景会失效（字幕变黑块）"
    print("[PASS] 穿透开关正确，且 LAYERED 始终保留")


def test_no_activate_and_toolwindow():
    """不抢焦点 + 不进任务栏（看视频时不能把焦点从播放器抢走）。"""
    s = _ext_style()
    assert s & WS_EX_NOACTIVATE, "缺少 WS_EX_NOACTIVATE（会抢焦点）"
    assert s & WS_EX_TOOLWINDOW, "缺少 WS_EX_TOOLWINDOW（会出现在任务栏）"
    assert _ov.focusPolicy() == Qt.NoFocus, "focusPolicy 应为 NoFocus"
    print("[PASS] 不抢焦点 + 不进任务栏")


def test_window_flags():
    """无边框 + 透明背景 + 置顶（逐像素透明的三个前提）。"""
    flags = _ov.windowFlags()
    assert flags & Qt.FramelessWindowHint, "缺少无边框标志"
    assert _ov.testAttribute(Qt.WA_TranslucentBackground), \
        "缺少 WA_TranslucentBackground —— 无法做到背景透明文字不透明"
    if _cfg.overlay.always_on_top:
        assert flags & Qt.WindowStaysOnTopHint, "缺少置顶标志"
    print("[PASS] 无边框 + 逐像素透明 + 置顶标志正确")


def test_geometry_and_growth():
    """窗口尺寸随内容增长（长文本不能被裁掉），且宽度=屏宽x比例。"""
    _ov.post(DisplayItem(kind="internal", label="t", app="", text="hi",
                         translation="短", item_id="g1", ts=time.time()))
    _pump(150)
    h1 = _ov.height_

    long_text = ("This is a deliberately long sentence used to verify the "
                 "overlay grows in height and wraps text without clipping. " * 4)
    _ov.post(DisplayItem(kind="internal", label="t", app="", text=long_text,
                         translation=long_text, item_id="g2", ts=time.time()))
    _pump(200)
    h2 = _ov.height_
    assert h2 > h1, f"长文本未撑高窗口：{h1} -> {h2}"

    expect_w = int(_ov.mon_rect.width() * _cfg.overlay.width_ratio)
    assert abs(_ov.width_ - expect_w) <= 2, f"宽度不符: {_ov.width_} vs {expect_w}"
    print(f"[PASS] 高度随内容增长（{h1} -> {h2}），宽度={_ov.width_} 符合比例")


def test_current_and_history():
    """历史与 _current()：跟随最新、翻页、回最新。"""
    before = len(_ov.hist)
    assert before >= 2, before
    assert _ov.follow and _ov.idx == len(_ov.hist) - 1, "应默认跟随最新"

    _ov.prev()
    assert _ov.idx == len(_ov.hist) - 2, _ov.idx
    assert not _ov.follow, "翻页后应停止跟随"
    _ov.next()
    assert _ov.follow and _ov.idx == len(_ov.hist) - 1, "回到底后应恢复跟随"
    txt, src = _ov._current()
    assert isinstance(txt, str) and isinstance(src, str), (txt, src)
    print(f"[PASS] 历史跟随/翻页正确（共 {len(_ov.hist)} 条）")


def test_speaker_tag_only_on_change():
    """声纹标注：只在换人时加 [Sx]（同一人连续说话不重复）。"""
    for i, spk in enumerate(["S1", "S1", "S2"]):
        _ov.post(DisplayItem(kind="internal", label="t", app="", text=f"t{i}",
                             translation=f"译文{i}", item_id=f"sp{i}",
                             ts=time.time(), speaker=spk))
    _pump(200)
    # 找到这三条在历史里的位置
    base = len(_ov.hist) - 3
    results = []
    for off in range(3):
        _ov.idx = base + off
        _ov.follow = False
        results.append(_ov._current()[0])
    assert results[0].startswith("[S1]"), results[0]
    assert not results[1].startswith("["), f"同一人连续说话不该重复标注: {results[1]}"
    assert results[2].startswith("[S2]"), results[2]
    print("[PASS] 声纹标注只在换人时出现")


def test_translation_backfill():
    """update_translation 能按 item_id 回填译文（LLM 异步返回的场景）。"""
    _ov.post(DisplayItem(kind="internal", label="t", app="", text="orig",
                         translation=None, item_id="bf1", ts=time.time()))
    _pump(120)
    _ov.idx = len(_ov.hist) - 1
    assert _ov._current()[0] == "orig", _ov._current()
    _ov.update_translation("bf1", "回填的译文")
    _pump(150)
    assert _ov._current()[0] == "回填的译文", _ov._current()
    print("[PASS] 译文回填生效")


def test_opacity_independent():
    """背景与文字透明度完全独立（GTK 版的核心能力，Qt 版必须保住）。"""
    _ov.set_opacity(0.25)
    _ov.set_text_opacity(0.9)
    assert abs(_ov.cfg.opacity - 0.25) < 1e-6
    assert abs(_ov.cfg.text_opacity - 0.9) < 1e-6
    # 边界clamp
    _ov.set_opacity(-1.0)
    assert _ov.cfg.opacity == 0.0
    _ov.set_opacity(2.0)
    assert _ov.cfg.opacity == 1.0
    _ov.set_opacity(float(_cfg.overlay.opacity))
    _ov.set_text_opacity(float(_cfg.overlay.text_opacity))
    print("[PASS] 背景/文字透明度独立且边界正确")


def test_edit_mode_toggles_passthrough():
    """进「位置」模式必须关穿透（否则收不到鼠标，没法拖动）。"""
    _ov.set_click_through(True)
    assert _ext_style() & WS_EX_TRANSPARENT
    on = _ov.toggle_edit_mode()
    assert on is True
    assert not (_ext_style() & WS_EX_TRANSPARENT), \
        "编辑模式下仍穿透，用户无法拖动窗口"
    off = _ov.toggle_edit_mode()
    assert off is False
    assert _ext_style() & WS_EX_TRANSPARENT, "退出编辑后应恢复原穿透状态"
    _ov.set_click_through(False)
    print("[PASS] 位置模式自动关闭穿透、退出后恢复")


def test_visible_toggle():
    """隐藏/显示切换不崩，且状态一致。"""
    assert _ov.visible is True
    _ov.toggle_visible()
    assert _ov.visible is False
    _ov.toggle_visible()
    assert _ov.visible is True
    print("[PASS] 隐藏/显示切换正常")


def test_persist_overlay_writes_yaml():
    """几何持久化：写回 config.yaml 的 overlay 段且不破坏其它字段。"""
    import tempfile
    import yaml as _yaml
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.yaml"
        p.write_text("audio:\n  sample_rate: 16000\noverlay:\n  opacity: 0.5\n",
                     encoding="utf-8")
        persist_overlay(p, {"pos_x": 111, "pos_y": 222, "opacity": 0.42})
        raw = _yaml.safe_load(p.read_text(encoding="utf-8"))
        assert raw["overlay"]["pos_x"] == 111
        assert raw["overlay"]["pos_y"] == 222
        assert abs(raw["overlay"]["opacity"] - 0.42) < 1e-6
        assert raw["audio"]["sample_rate"] == 16000, "其它字段被破坏"
    print("[PASS] 持久化写回正确且不破坏其它配置")


def test_no_position_button_and_gear_present():
    """「位置」按钮已删（关穿透即可直接拖动），⚙ 外观设置按钮存在。"""
    assert not hasattr(_ov.panel, "btn_edit"), "「位置」按钮应已删除"
    assert hasattr(_ov.panel, "btn_gear"), "缺少 ⚙ 外观设置按钮"
    # 方法保留（测试/程序化调用兼容），只是不再有 UI 入口
    assert callable(getattr(_ov, "toggle_edit_mode", None))
    print("[PASS] 面板按钮：无「位置」、有 ⚙")


def test_hover_tips_bubble():
    """面板/设置弹窗控件悬停有深色提示气泡（对齐控制台 ToolTip）。

    守的坑：面板是永不激活的窗（不抢焦点），原生 tooltip 触发不可靠，
    用户实测"悬停无任何提示"→ 自绘 TipBubble 接管：提示文本仍存各控件
    setToolTip（单一数据源），Enter 延迟弹出、离开/点击/隐藏即收起。
    """
    from livetrans.overlay import TipBubble

    # ① 面板每个交互控件都配了提示文本（数据源齐全）
    # （QComboBox 的内部弹出容器 QFrame 属实现细节，不在要求之列）
    controls = [w for w in _ov.panel.findChildren(QWidget)
                if isinstance(w, (QPushButton, QLabel, QSlider, QComboBox))]
    missing = [f"{type(w).__name__}«{(w.text() or '')[:6]}»" for w in controls
               if not w.toolTip()]
    assert not missing, f"面板有控件缺悬停提示: {missing}"

    # ② 显示路径：文本一致、不拦鼠标、可收起（真实窗口上可见性可验证）
    btn = _ov.panel.btn_pt
    TipBubble.show_for(btn)
    bubble = TipBubble.instance()
    assert bubble.isVisible(), "提示气泡未显示"
    assert bubble.text() == btn.toolTip(), bubble.text()
    assert bubble.testAttribute(Qt.WA_TransparentForMouseEvents), \
        "气泡会拦鼠标——悬停面板会闪烁"
    TipBubble.hide_now()
    assert not bubble.isVisible(), "气泡未收起"

    # ③ schedule/cancel：延迟弹出可被取消（离开即取消，不残留）
    TipBubble.schedule(btn)
    TipBubble.cancel()
    assert not bubble.isVisible()
    print(f"[PASS] 悬停提示气泡：面板 {len(controls)} 个控件全覆盖、"
          "显示/收起/取消正常")


def test_long_text_height_capped():
    """超长句封顶 55% 屏高 + 溢出标志（底对齐绘制，最新译文可见）。"""
    monster = ("This is an extremely long sentence that keeps going and going "
               "far beyond any reasonable subtitle length. " * 12)
    _ov.post(DisplayItem(kind="internal", label="t", app="", text=monster,
                         translation=monster, item_id="cap1", ts=time.time()))
    _ov.jump_latest()          # 前面的用例把 follow 留成了 False，必须先回到最新
    _pump(250)
    max_h = _ov._max_height()
    assert _ov.height_ <= max_h + 2, f"高度未封顶: {_ov.height_} > {max_h}"
    assert _ov._overflow is True, "超长内容应处于溢出状态"
    print(f"[PASS] 高度封顶（{_ov.height_} <= {max_h}），溢出底对齐生效")


def test_color_fields_and_settings_popup():
    """新增的 text_color/bg_color 字段 + ⚙ 设置弹窗（字号/宽度滑条实时生效）。"""
    from livetrans.overlay import SettingsPopup
    assert hasattr(_ov.cfg, "text_color") and hasattr(_ov.cfg, "bg_color"), \
        "OverlayConfig 缺少颜色字段"

    _ov.panel.toggle_settings()
    sp = getattr(_ov.panel, "_settings_popup", None)
    assert isinstance(sp, SettingsPopup) and sp.isVisible(), "⚙ 弹窗未打开"

    old_font = _ov.cfg.font_size
    sp.font_scale.setValue(50)
    assert _ov.cfg.font_size == 50, "字号滑条未实时生效"
    sp.font_scale.setValue(old_font)

    old_w = _ov.width_
    sp.w_scale.setValue(60)
    assert abs(_ov.cfg.width_ratio - 0.60) < 1e-6, "宽度滑条未实时生效"
    assert abs(_ov.width_ - int(_ov.mon_rect.width() * 0.6)) <= 2
    sp.w_scale.setValue(int(float(_cfg.overlay.width_ratio) * 100))

    # 再点一次 = 关闭（单例切换）
    _ov.panel.toggle_settings()
    assert not sp.isVisible(), "第二次 toggle 应隐藏弹窗"
    print("[PASS] 颜色字段存在；⚙ 弹窗滑条实时生效、可开关")


def test_settings_popup_draggable_and_labels():
    """⚙ 设置弹窗可用鼠标拖动 + 「声音来源/背景透明度/文字透明度」文案。

    守的坑：弹窗是无边框 + 不激活窗，没有标题栏也没实现拖动 → 用户点开设置
    之后窗口挪不了地方；面板文案「音频/背景/文字」与控制台叫法不一致。
    """
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from livetrans.overlay import SettingsPopup

    # 面板文案（与控制台外挂卡片的叫法一致）
    assert _ov.panel.src_lab.text() == "声音来源", _ov.panel.src_lab.text()
    assert _ov.panel.op_lab.text().startswith("背景透明度"), \
        _ov.panel.op_lab.text()
    assert _ov.panel.tx_lab.text().startswith("文字透明度"), \
        _ov.panel.tx_lab.text()

    # 文字层级样式钩子：灰标签 + 青色数值、退出按钮危险色（见 _qss）
    assert _ov.panel.src_lab.property("role") == "dim", "标签应有 dim 角色"
    assert _ov.panel.op_val.property("role") == "val", "数值应有 val 角色"
    assert _ov.panel.op_val.text() == f"{float(_ov.cfg.opacity):.2f}", \
        _ov.panel.op_val.text()
    assert _ov.panel.btn_exit.objectName() == "exitBtn", "退出按钮应有危险色样式"

    _ov.panel.toggle_settings()
    sp = getattr(_ov.panel, "_settings_popup", None)
    assert isinstance(sp, SettingsPopup) and sp.isVisible(), "⚙ 弹窗未打开"

    old = sp.geometry().topLeft()
    center = QPointF(sp.width() / 2, sp.height() / 2)
    g0 = QPointF(old.x() + center.x(), old.y() + center.y())

    def ev(etype: QEvent.Type, gp: QPointF, btn, btns) -> QMouseEvent:
        return QMouseEvent(etype, center, gp, btn, btns, Qt.NoModifier)

    sp.mousePressEvent(ev(QEvent.MouseButtonPress, g0,
                          Qt.LeftButton, Qt.LeftButton))
    sp.mouseMoveEvent(ev(QEvent.MouseMove, g0 + QPointF(40, 25),
                         Qt.NoButton, Qt.LeftButton))
    new = sp.geometry().topLeft()
    assert abs(new.x() - (old.x() + 40)) <= 2 and abs(new.y() - (old.y() + 25)) <= 2, \
        f"设置弹窗拖不动: {old} -> {new}"
    sp.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, g0 + QPointF(40, 25),
                            Qt.LeftButton, Qt.NoButton))
    _ov.panel.toggle_settings()
    print(f"[PASS] ⚙ 弹窗可拖动（{old.x()},{old.y()} -> {new.x()},{new.y()}）；"
          "文案=声音来源/背景透明度/文字透明度")


def test_bg_color_change_reaches_paint():
    """背景颜色修改必须实时到达绘制（用户反馈"背景颜色调整了没反应"）。

    取色器返回的 hex（col.name()）写入 cfg.bg_color 后，paintEvent 的背景
    圆角矩形必须用新色重绘；用 grab() 像素采样验证。
    注意起点色不写死：用户可能已在 ⚙ 弹窗里调过背景色并持久化，
    先归零再改色，结束后还原用户当前配色。
    """
    _ov.set_opacity(0.9)
    orig = str(_ov.cfg.bg_color)
    probe = (5, max(4, _ov.height_ // 2))

    def sample() -> str:
        _ov.update()
        _pump(80)
        return _ov.grab().toImage().pixelColor(*probe).name()

    _ov.cfg.bg_color = "#000000"
    before = sample()
    _ov.cfg.bg_color = "#ff0000"
    after = sample()
    assert before == "#000000" and after == "#ff0000", (before, after)
    _ov.cfg.bg_color = orig
    _ov.update()
    print(f"[PASS] 背景颜色修改实时到达绘制（{before} -> {after} -> 还原 {orig}）")


def test_scheme_persist_and_reset():
    """配色方案：颜色不随几何落盘；「保存方案」才写盘；「恢复默认」复位。

    守的坑：用户要求"颜色重启回默认，或用「保存方案」显式保留"——
    若 persist_geom 仍携带颜色，拖动窗口/调字号就会把试出来的配色固化；
    另外控制台保存 overlay 段时不得冲掉外挂已保存的方案（快照过期问题）。
    """
    import tempfile

    import yaml as _yaml

    old_path = _ov.config_path
    old_tx, old_bg = str(_ov.cfg.text_color), str(_ov.cfg.bg_color)
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.yaml"
            p.write_text("overlay:\n  opacity: 0.5\n", encoding="utf-8")
            _ov.config_path = p
            _ov.cfg.text_color = "#123456"
            _ov.cfg.bg_color = "#654321"

            _ov.persist_geom()                # 几何/字号落盘：不得携带配色
            raw = _yaml.safe_load(p.read_text(encoding="utf-8"))["overlay"]
            assert "text_color" not in raw and "bg_color" not in raw, raw

            _ov.panel.toggle_settings()
            sp = getattr(_ov.panel, "_settings_popup", None)
            assert sp is not None and sp.isVisible()
            assert sp.btn_save.text() == "保存方案"
            assert sp.btn_reset.text() == "恢复默认"

            sp._save_scheme()                 # 保存方案 → 颜色写盘
            raw = _yaml.safe_load(p.read_text(encoding="utf-8"))["overlay"]
            assert raw["text_color"] == "#123456", raw
            assert raw["bg_color"] == "#654321", raw

            sp._reset_scheme()                # 恢复默认 → 白字黑底并覆盖方案
            assert _ov.cfg.text_color == "#ffffff"
            assert _ov.cfg.bg_color == "#000000"
            raw = _yaml.safe_load(p.read_text(encoding="utf-8"))["overlay"]
            assert raw["text_color"] == "#ffffff", raw
            assert raw["bg_color"] == "#000000", raw
            _ov.panel.toggle_settings()
    finally:
        _ov.config_path = old_path
        _ov.cfg.text_color, _ov.cfg.bg_color = old_tx, old_bg
        _ov.update()
    print("[PASS] 配色方案：几何不带色、保存/恢复默认正确落盘")


def test_panel_popup_background_painted():
    """面板/设置弹窗必须有不透明圆角底（顶层背景由 paintEvent 手绘）。

    守的坑：曾改用 QSS QWidget 背景 + WA_TranslucentBackground，实机上
    设置弹窗整体变透明（控件像浮在画面上）——QSS 顶层背景在半透明 Tool
    窗上不可靠。现由 _TipHost.paintEvent 手绘圆角矩形，像素采样守住：
    边缘空白处必须是不透明 #1c2230，四角（圆角外）必须透出。
    """
    _ov.panel.toggle_settings()
    sp = getattr(_ov.panel, "_settings_popup", None)
    assert sp is not None and sp.isVisible(), "⚙ 弹窗未打开"
    for w, tag in ((_ov.panel, "面板"), (sp, "设置弹窗")):
        img = w.grab().toImage()
        px = img.pixelColor(w.width() // 2, 4)      # 顶边空白（圆角内、无控件）
        assert (px.alpha(), px.red(), px.green(), px.blue()) == (255, 28, 34, 48), \
            f"{tag}底色缺失: ({px.red()},{px.green()},{px.blue()},{px.alpha()})"
        corner = img.pixelColor(1, 1)                # 圆角外应透明
        assert corner.alpha() == 0, f"{tag}圆角未生效: a{corner.alpha()}"
    _ov.panel.toggle_settings()
    print("[PASS] 面板/设置弹窗圆角底正确（#1c2230 不透明、四角透出画面）")


if __name__ == "__main__":
    test_pipeline_interface()
    test_click_through_toggle()
    test_no_activate_and_toolwindow()
    test_window_flags()
    test_geometry_and_growth()
    test_current_and_history()
    test_speaker_tag_only_on_change()
    test_translation_backfill()
    test_opacity_independent()
    test_edit_mode_toggles_passthrough()
    test_visible_toggle()
    test_persist_overlay_writes_yaml()
    test_no_position_button_and_gear_present()
    test_hover_tips_bubble()
    test_long_text_height_capped()
    test_color_fields_and_settings_popup()
    test_settings_popup_draggable_and_labels()
    test_bg_color_change_reaches_paint()
    test_scheme_persist_and_reset()
    test_panel_popup_background_painted()
    _ov.quit()
