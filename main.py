"""Richman 大富翁 · 城市之光 —— 程序入口。

用法:
    python main.py
    .venv\Scripts\python.exe main.py

命令行参数（可选）:
    --host            启动后直接创建局域网房间
    --join <IP>       启动后直接加入指定 IP 的房间
    --port <端口>     指定端口
    --name <昵称>     指定昵称
    --fullscreen      全屏启动
    --windowed        窗口启动
"""
from __future__ import annotations

import argparse
import os
import sys

# 保证以「python main.py」和「python -m」两种方式都能正确导入 src
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Richman 大富翁 · 城市之光")
    ap.add_argument("--host", action="store_true", help="直接创建局域网房间")
    ap.add_argument("--join", metavar="IP", default="", help="直接加入指定 IP 的房间")
    ap.add_argument("--port", type=int, default=0, help="端口")
    ap.add_argument("--name", default="", help="昵称")
    ap.add_argument("--room", default="", help="房间名称")
    ap.add_argument("--fullscreen", action="store_true", help="全屏启动")
    ap.add_argument("--windowed", action="store_true", help="窗口启动")
    ap.add_argument("--anim-speed", type=float, default=0.0, help="动画速度 0.5/1.0/1.5/2.0")
    ap.add_argument("--seed", type=int, default=0, help="固定随机种子（调试用）")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    from src.app import App
    from src.persistence.settings import Settings

    settings = Settings.load()
    if args.fullscreen:
        settings.set_fullscreen(True)
    if args.windowed:
        settings.set_fullscreen(False)
    if args.name:
        settings.set_nickname(args.name)
    if args.anim_speed > 0:
        settings.set("ui", "animation_speed", args.anim_speed)

    app = App(settings)
    if args.host:
        name = settings.nickname
        app.start_host(args.room or f"{name} 的房间", name,
                       settings.character_id, args.port or settings.last_port)
    elif args.join:
        app.join_host(args.join, args.port or settings.last_port,
                      settings.nickname, settings.character_id)
    return app.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        raise SystemExit(130)
