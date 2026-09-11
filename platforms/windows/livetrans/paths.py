"""项目路径与桌面集成模板（供各模块共享）。

**代码目录与数据目录解耦**，两种运行形态都成立：
- 源码运行（BASE 可写）：数据就放 BASE，跟以前完全一致；
- 系统安装（BASE 只读，如 PyInstaller 打出的只读目录）：数据落到用户目录
  （`LIVETRANS_DATA_DIR` > `%LOCALAPPDATA%\\LiveTrans`（platformdirs）），
  首次运行从随包的 `config.example.yaml` 播种 config.yaml / glossary.yaml。

Windows 版与 Linux 版的差异：
- 数据目录：`platformdirs.user_data_dir("LiveTrans")`（Windows 上是 `%LOCALAPPDATA%\\LiveTrans`）
  替代 Linux 的 XDG（`$XDG_DATA_HOME/livetrans`）。
- 系统只读模型目录：Windows 没有 `/usr/share`，改为 BASE/models（随包）——
  离线包里模型就放在 exe 旁边。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _base_dir() -> Path:
    """代码/资源目录。

    - 源码运行：`platforms/windows/`（本文件的上上级）
    - PyInstaller 打包（one-dir）：**exe 所在目录**（模型/配置就放在 exe 旁边），
      而不是 `sys._MEIPASS`（那是只读的临时解包目录，写不进去）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE = _base_dir()

EXAMPLE_PATH = BASE / "config.example.yaml"


def app_command(mode: str = "overlay",
                extra_args: list[str] | None = None) -> list[str]:
    """启动本程序另一个实例（`main` 主程序 / `overlay` 字幕外挂）的命令行。

    关键：**打包后 `python -m livetrans.xxx` 不可用**（没有解释器、没有包结构），
    必须直接用 exe；源码运行时才用 `-m`。

    两种模式的差异：
    - 打包（frozen）：`LiveTrans.exe [--overlay]` —— exe 内部按参数分派到对应模块
      （`-m` 入口在打包后失效，这正是 Windows 版保留 `--overlay` 参数的原因）。
    - 源码：`python -m livetrans.overlay` —— 直接跑模块，**不能再带 --overlay**
      （overlay 自己的 argparse 不认识它，会报错退出）。要传参数（如 --config）
      走 extra_args。

    用法：
        app_command("overlay")                    # 字幕外挂
        app_command("main", ["--config", "c.yaml"])  # 主程序
    """
    extra = list(extra_args or [])
    frozen = getattr(sys, "frozen", False)
    if mode == "main":
        return [sys.executable, *extra] if frozen \
            else [sys.executable, "-m", "livetrans.main", *extra]
    # overlay（默认）
    if frozen:
        return [sys.executable, "--overlay", *extra]
    return [sys.executable, "-m", "livetrans.overlay", *extra]


def _writable(path: Path) -> bool:
    """目录可写？（顺带把目录建出来；任何 OSError 都算不可写）"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".livetrans-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_dir() -> Path:
    env = os.environ.get("LIVETRANS_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if _writable(BASE):
        return BASE                                # 源码运行：原地不动
    try:
        # Windows: %LOCALAPPDATA%\LiveTrans（Linux 上则是 ~/.local/share/LiveTrans）
        from platformdirs import user_data_dir
        return Path(user_data_dir("LiveTrans", appauthor=False))
    except ImportError:                            # 没装 platformdirs 时的兜底
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
            return Path(local) / "LiveTrans"
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        return Path(xdg) / "livetrans"


DATA_DIR = _data_dir()
if not _writable(DATA_DIR):                        # 尽力而为；失败仍可运行（只读）
    pass

CONFIG_PATH = DATA_DIR / "config.yaml"

SESSIONS_DIR = DATA_DIR / "sessions"

LOG_PATH = DATA_DIR / "run.log"    # 运行日志（启动前轮转，保留近 5 份）

# 模型目录：源码运行放 BASE/models；只读安装放数据目录（首次运行自动下载）
MODELS_DIR = (BASE / "models") if _writable(BASE / "models") \
    else (DATA_DIR / "models")

# 随包安装的只读模型目录。Linux 版是 /usr/share/livetrans/models（离线 .deb 装的）；
# Windows 没有这个约定，离线包的模型就放在 exe 旁边（BASE/models），
# 所以这里默认指向 BASE/models。仍可用环境变量覆盖，便于测试与自定义镜像路径。
SYSTEM_MODELS_DIR = Path(os.environ.get("LIVETRANS_SYSTEM_MODELS")
                         or (BASE / "models"))


def models_search_dirs() -> list[Path]:
    """按优先级列出"可以找到模型"的目录（存在且是目录才算）。

    1. MODELS_DIR：用户目录 / 源码目录（也是下载写入的地方，优先级最高，
       这样用户自行下载或替换的模型会覆盖随包版本）
    2. BASE/models：源码运行时的项目目录
    3. SYSTEM_MODELS_DIR：随 .deb 安装的系统只读副本（离线可用）
    """
    dirs: list[Path] = []
    for d in (MODELS_DIR, BASE / "models", SYSTEM_MODELS_DIR):
        try:
            if d.is_dir() and d not in dirs:
                dirs.append(d)
        except OSError:
            continue
    return dirs

GLOSSARY_PATH = DATA_DIR / "glossary.yaml"


def ensure_data_files() -> list[str]:
    """首次运行播种配置文件（只补缺失的，绝不覆盖已有文件）。

    返回新建的文件名列表，便于启动日志说明"数据目录在哪、播了什么"。
    """
    created: list[str] = []
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            src = (BASE / "config.yaml") if (BASE / "config.yaml").is_file() \
                else EXAMPLE_PATH
            if src.is_file():
                shutil.copyfile(src, CONFIG_PATH)
                created.append(CONFIG_PATH.name)
        if not GLOSSARY_PATH.exists():
            src = BASE / "glossary.example.yaml"
            if src.is_file():
                shutil.copyfile(src, GLOSSARY_PATH)
                created.append(GLOSSARY_PATH.name)
    except OSError:
        pass
    return created


def resolve_data(p: str | None) -> Path | None:
    """把配置里的相对路径解析成"数据目录优先"的绝对路径。"""
    if not p:
        return None
    path = Path(p).expanduser()
    if path.is_absolute():
        return path
    for base in (DATA_DIR, BASE):
        cand = base / path
        if cand.is_file():
            return cand
    return DATA_DIR / path

