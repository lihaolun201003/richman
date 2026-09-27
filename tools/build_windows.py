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


def make_zip(out_dir: str, version: str = "") -> str:
    """把 onedir 产物打成发布 zip（只含运行所需内容）。

    zip 里会额外放一份「先读我.txt」：玩家解压后第一眼看到的就是怎么玩、
    连不上怎么办。不需要他去翻仓库里的 README。
    """
    from src.version import short_version

    tag = short_version() if not version else version
    name = f"Richman-v{tag}-windows.zip"
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
        zf.writestr(f"Richman/先读我.txt", _readme_for_zip(tag))
        count += 1
    size_mb = os.path.getsize(target) / (1024 * 1024)
    print(f"\n发布包已生成：{target}")
    print(f"  {count} 个文件，{size_mb:.1f} MB")
    print("  解压后双击 Richman.exe 即可运行（整个文件夹一起用，不要只拿 exe）")
    verify_zip(target)
    return target


def _readme_for_zip(tag: str) -> str:
    """给玩家的第一屏说明（放在 zip 里，纯文本，双击就能看）。"""
    return f"""Richman 大富翁 {tag} · 先读我
========================================

怎么开始
--------
1. 把这个文件夹解压到任意位置（不要只把 exe 拖出来）
2. 双击 Richman.exe
3. 一个人点：局域网联机 → 创建房间
4. 其他人点：局域网联机 → 加入房间，输入房主发来的地址
5. 房主点「开始游戏」

联机三步
--------
房主：创建房间 → 大厅显示一行大字（例如 192.168.1.5:28080）
      → 点「复制连接信息」，发给其他人
其他人：加入房间 → 粘贴地址（或直接点右侧搜到的房间卡片）→ 按 R 准备
房主：看到「已准备」→ 点「开始游戏」

搜不到房间？
------------
很正常，不影响联机。部分校园网 / 访客 WiFi 会屏蔽广播。
让房主把「复制连接信息」的内容发给你，粘到「房主发来的地址」框里即可。

连不上？
--------
主菜单 → 局域网联机 → 联机诊断：
  · 它会告诉你本机应该把哪个地址发给别人
  · 里面有「测试连接」，会分类告诉你失败原因
    （被拒绝 / 超时 / 版本不一致 / 房间已满 / 已经开局 …）
  · 还不行就点「导出联机诊断」，把生成的 zip 发给开发者

Windows 防火墙
--------------
第一次开房间时系统会弹窗，必须勾选「专用网络」并允许。
误点了取消，就在管理员 PowerShell 里执行：

  New-NetFirewallRule -DisplayName "Richman 大富翁" -Direction Inbound `
    -Protocol TCP -LocalPort 28080 -Action Allow -Profile Private
  New-NetFirewallRule -DisplayName "Richman 自动发现" -Direction Inbound `
    -Protocol UDP -LocalPort 28081 -Action Allow -Profile Private

掉线了？
--------
不会毁掉这一局：
  · 掉线的一方会看到「正在重新连接…」和倒计时
  · 房主那边游戏继续，掉线玩家卡片上会写「掉线 N 秒」
  · 超过 30 秒由 AI 临时接管，本局不中断
  · 掉线的人随时可以连回来，连上后会自动交还控制权

存档与设置
----------
config\\user_settings.json   设置
saves\\*.json                存档（每个回合自动保存）
logs\\richman.log            运行日志（出问题时看这里）
diag\\*.zip                  导出的联机诊断包

第一次玩建议点主菜单的「新手教程」，3 分钟走一遍核心玩法。
"""


def verify_zip(path: str) -> int:
    """检查发布包里没有不该有的东西（这是给玩家的包，不是开发目录）。"""
    problems: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for n in names:
            low = n.lower()
            if low.endswith(".py") or "/src/" in low or "\\src\\" in low:
                problems.append(f"包含源码：{n}")
            if "/.git/" in low or "/.venv/" in low:
                problems.append(f"包含版本库/虚拟环境：{n}")
            if low.endswith((".log", ".pyc", ".pyo", ".tmp")):
                problems.append(f"包含日志或缓存：{n}")
            if "user_settings.json" in low or "/saves/" in low or "\\saves\\" in low:
                problems.append(f"包含用户数据：{n}")
            if low.endswith(".pdb"):
                problems.append(f"包含调试符号：{n}")
        # 本机绝对路径（打包时不该混进去）
        for n in names:
            if "C:\\Users\\" in n or "/home/" in n:
                problems.append(f"包含本机路径：{n}")
        has_exe = any(n.endswith("Richman.exe") for n in names)
        has_internal = any("_internal/" in n.replace("\\", "/") for n in names)
    print(f"\n发布包检查（共 {len(names)} 项）：")
    if not has_exe:
        problems.append("缺少 Richman.exe")
    if not has_internal:
        problems.append("缺少 _internal/（依赖与游戏数据）")
    if problems:
        for p in problems[:12]:
            print(f"  [问题] {p}")
        print(f"  → 共 {len(problems)} 个问题")
        return 1
    root_files = [n for n in names if n.count("/") + n.count("\\") == 1]
    print(f"  [OK] 有 Richman.exe 与 _internal/，无源码 / 日志 / 存档 / 缓存 / 本机路径")
    print(f"  根目录文件：{', '.join(sorted(os.path.basename(n) for n in root_files))}")
    return 0


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
