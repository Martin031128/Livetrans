#!/usr/bin/env python3
"""Windows 版打包脚本：PyInstaller one-dir -> 便携版 / 安装器素材。

与 Linux 版的 build_deb.sh 对应，产出 Windows 分发物：

    python packaging/build_windows.py --portable     # 便携版（dist/LiveTrans/）
    python packaging/build_windows.py --onedir       # 同上，显式命名
    python packaging/build_windows.py --check        # 只检查环境，不打包

产物结构（one-dir：启动快，模型文件可直接放旁边）：
    dist/LiveTrans/LiveTrans.exe          主入口（控制台 + 主字幕窗）
    dist/LiveTrans/_internal/...          PyInstaller 运行时
    dist/LiveTrans/models/                识别/声纹模型（若存在则一并拷入）

外挂窗（Qt）通过 --overlay 参数入口启动，不使用 `-m livetrans.overlay`
（PyInstaller 打包后模块入口失效）。

设计约束（见 WINDOWS_PORT_BRIEF.md）：
- 不重写业务逻辑；打包只做"收集依赖 + 生成 exe + 拷模型"。
- 模型目录可选：没有 models/ 时打出的是"首次启动联网下载"版。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent        # platforms/windows/
DIST = ROOT / "dist"
APP_NAME = "LiveTrans"

# 自定义 hook 目录（优先于 pyinstaller-hooks-contrib 自带的那份）
HOOKS_DIR = ROOT / "packaging" / "hooks"

# 打包时需要显式收集的包（PyInstaller 不一定能自动发现）
HIDDEN_IMPORTS = [
    "funasr_onnx",
    "sounddevice",
    "soundcard",
    "webrtcvad",
    "_webrtcvad",       # webrtcvad-wheels 的编译扩展（单文件模块 + .pyd）
    "yaml",
    "openai",
    "httpx",
    "sherpa_onnx",      # 声纹（可选：装了才收）
    "PySide6.QtCore",   # 外挂（可选：装了才收）
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

# 明确排除的包（避免把无关内容塞进包里）
#
# 背景：打包环境是 `conda create -n livetrans python=3.12`（只装 requirements.txt），
# 所以这里**不是为了对付 Anaconda 全家桶**（那个问题已随环境隔离解决），
# 而是两类务实目标：
#   A) 排除「依赖链牵连」的重型包 —— scipy/scikit-learn 会带出 astropy、
#      skimage 等一批用不到的东西（实测约 40MB）。
#   B) 排除「平台特定/不需要」的东西 —— 其它 GUI 绑定、测试框架等。
#
# ⚠ 经验（踩过）：**不要排除 setuptools** —— PyInstaller 的运行时钩子
# pyi_rth_pkgres 依赖 pkg_resources（进而 jaraco.text），排掉后 exe 启动即
# `ModuleNotFoundError: No module named 'jaraco.text'`。
EXCLUDES = [
    # ---- A. 依赖链牵连的大件（实测最有效，共省约 60MB）----
    # 深度学习框架：项目只用 onnxruntime 跑推理，不需要训练框架
    "torch", "torchvision", "torchaudio",
    "tensorflow", "tensorboard", "keras",
    "jax", "jaxlib",
    # 科学可视化 / 三维（VTK 系）
    "vtk", "vtkmodules", "pyvista", "mayavi",
    "panel", "bokeh", "holoviews", "datashader",
    # 计算机视觉（项目不做图像处理；scipy 会牵连 skimage）
    "cv2", "skimage", "scikit-image",
    # 天文/物理数据（scipy 依赖链带出）
    "astropy", "astropy_iers_data",
    # 其它科学计算与可视化（项目不做绘图）
    "matplotlib", "pandas", "seaborn", "plotly", "sympy",
    "sklearn", "scikit-learn", "statsmodels", "networkx",
    # 分布式/列式存储
    "distributed", "dask", "pyarrow", "polars", "duckdb",
    # 编译与 JIT（numba 依赖 llvmlite，体积不小）
    "llvmlite", "numba", "cython", "Cython",
    # HDF5（h5py 带的 hdf5.dll 约 3.5MB；项目不读 h5）
    "h5py", "hdf5plugin",
    # 音视频编解码重型依赖（项目只用 sounddevice/soundcard 采原始 PCM）
    "av", "moviepy", "imageio_ffmpeg",
    # 文档/编辑器/交互式环境
    "jupyter", "jupyterlab", "notebook", "nbformat", "nbconvert",
    "ipykernel", "ipython", "IPython", "ipywidgets", "traitlets",
    "sphinx", "docutils", "pygments", "jedi", "rope",
    "altair", "mypy", "pywt", "sqlalchemy",

    # ---- B. 平台特定 / 不需要 ----
    # 其它 GUI 绑定（只用 PySide6 与 tkinter）
    "PyQt5", "PyQt6", "PySide2", "wx",
    # 测试框架（打包产物不需要跑测试）
    "pytest", "pip", "wheel",
    "test", "tests", "unittest",
]


def _log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def conda_runtime_dlls() -> list[tuple[str, str]]:
    """收集 conda 环境 `Library/bin` 下的运行库 DLL。

    **为什么需要**：conda 把非 Python 的运行库（libffi、libssl、libcrypto、
    zlib 等）放在 `<env>\\Library\\bin\\`，而不是 `<env>\\DLLs\\` 或
    site-packages。PyInstaller 不会自动去那里找，于是就出现这类运行时报错：

        ImportError: DLL load failed while importing _ctypes: 找不到指定的模块

    —— `_ctypes.pyd` 打包进去了，但它动态依赖的 `ffi-8.dll` 没带上。

    只挑**明确需要的**几个，避免把整个 Library/bin（常有几百 MB）全塞进去。
    """
    if sys.platform != "win32":
        return []
    # 关键运行库。conda-forge 的命名和 PyPI wheel 不同（`ffi-8.dll` 而非
    # `libffi-8.dll`、`tk86t.dll` 而非 `tk86.dll`），所以这里都写上。
    wanted = {
        # ctypes / cffi（不带上 -> `DLL load failed while importing _ctypes`）
        "ffi-8.dll", "ffi-7.dll", "libffi-8.dll", "libffi-7.dll",
        # tkinter（不带上 -> `DLL load failed while importing _tkinter`）
        "tk86t.dll", "tk85t.dll", "tcl86t.dll", "tcl85t.dll",
        # ssl / hashlib
        "libssl-3-x64.dll", "libcrypto-3-x64.dll",
        # 压缩（tk/zlib 也依赖）
        "zlib.dll", "zlib1.dll", "libzlib.dll", "liblzma.dll",
        "libbz2.dll", "libbz2-1.dll",
        # sqlite3 / xml / uuid
        "sqlite3.dll", "libexpat.dll", "libexpat-1.dll",
        # 数学/FFT（numpy/scipy 的 conda 版可能需要）
        "libopenblas.dll", "libblas.dll", "liblapack.dll",
    }
    lower = {w.lower() for w in wanted}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for base in (Path(sys.prefix) / "Library" / "bin",
                 Path(sys.prefix) / "DLLs"):
        if not base.is_dir():
            continue
        for dll in base.glob("*.dll"):
            if dll.name.lower() in lower and dll.name.lower() not in seen:
                seen.add(dll.name.lower())
                out.append((str(dll), "."))
    return out


def check_env() -> bool:
    """检查打包环境是否齐备。"""
    ok = True
    try:
        import PyInstaller  # noqa: F401
        _log(f"PyInstaller 已就绪（{PyInstaller.__version__}）")
    except ImportError:
        _log("✖ 缺少 PyInstaller：python -m pip install pyinstaller")
        ok = False

    entry = ROOT / "run_livetrans.py"
    if not entry.is_file():
        _log(f"✖ 找不到打包入口：{entry}")
        ok = False
    else:
        _log("打包入口 run_livetrans.py 就绪（绝对导入，兼容 PyInstaller）")

    models = ROOT / "models"
    if models.is_dir():
        n = sum(1 for f in models.rglob("*") if f.is_file())
        size = sum(f.stat().st_size for f in models.rglob("*") if f.is_file())
        _log(f"检出 models/（{n} 文件，{size / 1024 ** 2:.0f} MB）→ 将随包分发")
    else:
        _log("未检出 models/ → 打出的是「首次启动联网下载」版")
    return ok


def build(portable: bool = True) -> int:
    """跑 PyInstaller 生成 one-dir 产物。"""
    if not check_env():
        return 2

    sep = ";" if sys.platform == "win32" else ":"     # PyInstaller 的 add-data 分隔符

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--onedir",                                    # one-dir：启动快、模型可放旁边
        "--windowed",                                  # 不弹黑框（GUI 程序）
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # 自定义 hook 目录优先：修掉 webrtcvad-wheels 与自带 hook 不兼容导致的打包失败
        "--additional-hooks-dir", str(HOOKS_DIR),
    ]

    # conda 运行库 DLL（libffi 等）：不显式带上，ctypes/cffi 在打包后
    # 会 `DLL load failed while importing _ctypes`（见 conda_runtime_dlls 注释）
    dlls = conda_runtime_dlls()
    if dlls:
        _log(f"从 conda Library/bin 收集 {len(dlls)} 个运行库: "
             + ", ".join(Path(s).name for s, _ in dlls))
    for src, dest in dlls:
        cmd += ["--add-binary", f"{src}{sep}{dest}"]

    # 附加数据：配置样例、术语表样例、图标
    for rel in ("config.example.yaml", "glossary.example.yaml"):
        p = ROOT / rel
        if p.is_file():
            cmd += ["--add-data", f"{p}{sep}."]
    icon = ROOT / "assets" / "livetrans.ico"          # Windows 需 .ico（Linux 版是 .svg）
    if icon.is_file():
        cmd += ["--icon", str(icon)]
    else:
        _log("⚠ 未找到 assets/livetrans.ico（Windows 图标），将使用默认图标")

    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]

    # 入口必须用 run_livetrans.py：
    # livetrans/main.py 是相对导入，被当成顶层脚本跑会
    # `ImportError: attempted relative import with no known parent package`。
    # 这个入口用绝对导入，让包结构正常。
    entry = ROOT / "run_livetrans.py"
    if not entry.is_file():
        _log(f"✖ 找不到打包入口：{entry}")
        return 4
    cmd.append(str(entry))

    _log("开始打包：" + " ".join(cmd[:8]) + " ...")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        _log(f"✖ PyInstaller 失败（exit {r.returncode}）")
        return r.returncode

    out = DIST / APP_NAME
    if not out.is_dir():
        _log(f"✖ 未生成预期目录：{out}")
        return 3

    # 模型随包（存在才拷；大件，放在 exe 旁边的 models/）
    src_models = ROOT / "models"
    if src_models.is_dir():
        dst = out / "models"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src_models, dst)
        _log(f"已拷入模型：{dst}")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    _log(f"✓ 打包完成：{out}（{size / 1024 ** 2:.0f} MB）")
    if portable:
        _log("便携版：整目录拷走即可运行（免安装）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="build_windows.py", description="LiveTrans Windows 版打包（PyInstaller one-dir）")
    ap.add_argument("--portable", action="store_true", help="生成便携版（默认行为）")
    ap.add_argument("--onedir", action="store_true", help="同 --portable（显式命名）")
    ap.add_argument("--check", action="store_true", help="只检查环境，不打包")
    ap.add_argument("--clean", action="store_true", help="仅清理 dist/ 与 build/")
    args = ap.parse_args(argv)

    if args.clean:
        for d in (DIST, ROOT / "build"):
            if d.exists():
                shutil.rmtree(d)
                _log(f"已删除 {d}")
        return 0
    if args.check:
        return 0 if check_env() else 2
    return build(portable=True)


if __name__ == "__main__":
    sys.exit(main())
