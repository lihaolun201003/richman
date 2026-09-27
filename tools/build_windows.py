"""Windows 打包脚本：把游戏打成一个免安装目录，并生成可直接发给朋友的 zip。

用法:
    python tools/build_windows.py            # 打包 + 自检
    python tools/build_windows.py --clean    # 先清理旧的 build/dist（推荐）
    python tools/build_windows.py --onefile  # 打成单个 exe（启动稍慢）
    python tools/build_windows.py --zip      # 额外生成发布用 zip

产物:
    dist/Richman/Richman.exe
    dist/Richman/_internal/…     （依赖与游戏数据，必须一起复制）
    dist/Richman-v0.3.0-windows.zip   （--zip 或默认生成：只含运行所需内容）

zip 里**不包含**日志、存档、缓存、源码与开发工具，
玩家解压后双击 Richman.exe 即可运行。

所有资源路径都通过 src/utils/paths.py 解析，程序在冻结环境下会以
可执行文件所在目录作为项目根目录，因此不会出现绝对路径问题。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = ";" if os.name == "nt" else ":"

# 让脚本能 import src/*（版本号、路径工具），无论从哪个目录调用
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 打进 zip 时排除的内容（运行产物 / 开发文件 / 缓存）
ZIP_EXCLUDE_DIRS = {"logs", "saves", "__pycache__", "build", "dist"}
ZIP_EXCLUDE_FILES = {"user_settings.json"}
ZIP_EXCLUDE_SUFFIX = (".log", ".pyc", ".pyo", ".tmp", ".pdb")


def which_pyinstaller() -> str | None:
    """优先用当前解释器里的 PyInstaller 模块。"""
    try:
        import PyInstaller  # noqa: F401

        return sys.executable
    except ImportError:
        return None


def clean() -> None:
    for name in ("build", "dist", "Richman.spec"):
        path = os.path.join(ROOT, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            print(f"  已删除 {name}/")
        elif os.path.isfile(path):
            os.remove(path)
            print(f"  已删除 {name}")


def build(onefile: bool = False, console: bool = False) -> int:
    python = which_pyinstaller()
    if python is None:
        print("[错误] 当前环境没有安装 PyInstaller。")
        print("       请先执行：.venv\\Scripts\\python.exe -m pip install pyinstaller")
        return 1

    args = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "Richman",
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", ROOT,
    ]
    args.append("--onefile" if onefile else "--onedir")
    args.append("--console" if console else "--windowed")

    # 游戏数据与配置随包发布
    for folder, pattern in (("data", "*.json"), ("config", "*.json")):
        src = os.path.join(ROOT, folder)
        if os.path.isdir(src):
            args += ["--add-data", f"{src}{SEP}{folder}"]

    # 音频：有文件才带上（没有也能跑，音效是程序合成的）
    audio = os.path.join(ROOT, "assets", "audio")
    if os.path.isdir(audio) and any(os.scandir(audio)):
        args += ["--add-data", f"{audio}{SEP}assets{os.sep}audio"]

    # 字体等资源
    fonts = os.path.join(ROOT, "assets", "fonts")
    if os.path.isdir(fonts) and any(os.scandir(fonts)):
        args += ["--add-data", f"{fonts}{SEP}assets{os.sep}fonts"]

    # 不打包用不到的重型依赖，能显著减小体积
    for mod in ("numpy", "scipy", "pandas", "matplotlib", "PIL", "tkinter",
                "test", "unittest", "pydoc_data"):
        args += ["--exclude-module", mod]

    args.append(os.path.join(ROOT, "main.py"))

    print("=" * 74)
    print("开始打包 Richman")
    print("=" * 74)
    print("  PyInstaller:", python)
    print("  模式:", "onefile" if onefile else "onedir")
    print()
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("\n[错误] 打包失败，返回码", result.returncode)
        return result.returncode

    out_dir = os.path.join(ROOT, "dist", "Richman")
    if onefile:
        exe = os.path.join(ROOT, "dist", "Richman.exe")
    else:
        exe = os.path.join(out_dir, "Richman.exe")
    if not os.path.isfile(exe):
        print(f"\n[错误] 没有找到产物 {exe}")
        return 1

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"\n打包完成：{exe}")
    print(f"  主程序大小：{size_mb:.1f} MB")
    if not onefile:
        total = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _dn, fn in os.walk(out_dir) for f in fn)
        print(f"  整个目录：{total / (1024 * 1024):.1f} MB")
    print("\n自检：")
    print(f'  "{exe}" --help')
    return 0


def make_zip(out_dir: str, version: str) -> str:
    """把 onedir 产物打成发布 zip（只含运行所需内容）。"""
    from src.version import version_tuple

    major, minor, patch = version_tuple()
    name = f"Richman-v{major}.{minor}.{patch}-windows.zip"
    target = os.path.join(ROOT, "dist", name)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for folder, dirs, files in os.walk(out_dir):
            dirs[:] = [d for d in dirs if d not in ZIP_EXCLUDE_DIRS]
            for filename in files:
                if filename in ZIP_EXCLUDE_FILES:
                    continue
                if filename.lower().endswith(ZIP_EXCLUDE_SUFFIX):
                    continue
                full = os.path.join(folder, filename)
                rel = os.path.relpath(full, os.path.dirname(out_dir))
                zf.write(full, rel)
                count += 1
    size_mb = os.path.getsize(target) / (1024 * 1024)
    print(f"\n发布包已生成：{target}")
    print(f"  {count} 个文件，{size_mb:.1f} MB")
    print("  解压后双击 Richman.exe 即可运行（整个文件夹一起用，不要只拿 exe）")
    return target


def smoke_test(exe: str) -> int:
    """启动打包后的 exe 并确认它能跑起来。

    两段检查：
    1. `--version`：确认能启动、能读版本号；
    2. `--selftest`：真进一局，验证数据 / 引擎 / UI 全链路（**不是只看退出码**）。
    """
    print("\n" + "=" * 74)
    print("运行打包产物自检")
    print("=" * 74)
    if not os.path.isfile(exe):
        print(f"[错误] 找不到 {exe}")
        return 1
    workdir = os.path.dirname(exe)

    try:
        result = subprocess.run([exe, "--version"], capture_output=True, timeout=120,
                                cwd=workdir)
    except subprocess.TimeoutExpired:
        print("[错误] 产物启动超时")
        return 1
    text = (result.stdout or b"").decode("utf-8", "replace")
    if "Richman" not in text:
        print("  [失败] --version 输出异常")
        print("  stdout:", text[:300])
        print("  stderr:", (result.stderr or b"").decode("utf-8", "replace")[:300])
        return 1
    print(f"  版本：{text.strip()}")

    try:
        result = subprocess.run([exe, "--selftest", "--selftest-seconds", "6"],
                                capture_output=True, timeout=300, cwd=workdir)
    except subprocess.TimeoutExpired:
        print("[错误] 产物自检超时")
        return 1
    out = (result.stdout or b"").decode("utf-8", "replace")
    print(out.strip()[:1200])
    ok = result.returncode == 0 and "启动自检完成" in out and "状态检查: 通过" in out
    if ok:
        print("\n  [OK] 打包产物真的启动、读到了数据、进入了对局、状态检查通过")
        return 0
    print("\n  [失败] 自检未通过（见上面的输出）")
    err = (result.stderr or b"").decode("utf-8", "replace")
    if err:
        print("  stderr:", err[:400])
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Windows 打包")
    ap.add_argument("--onefile", action="store_true", help="打成单个 exe")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（调试用）")
    ap.add_argument("--clean", action="store_true", help="先清理旧产物")
    ap.add_argument("--no-smoke", action="store_true", help="跳过产物自检")
    ap.add_argument("--no-zip", action="store_true", help="不生成发布 zip")
    args = ap.parse_args()

    if args.clean:
        print("清理旧产物")
        clean()
        print()

    code = build(onefile=args.onefile, console=args.console)
    if code != 0:
        return code

    if not args.no_smoke:
        exe = (os.path.join(ROOT, "dist", "Richman.exe") if args.onefile
               else os.path.join(ROOT, "dist", "Richman", "Richman.exe"))
        code = smoke_test(exe)
        if code != 0:
            return code

    if not args.no_zip and not args.onefile:
        make_zip(os.path.join(ROOT, "dist", "Richman"), "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
