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
    ap.add_argument("--selftest", action="store_true",
                    help="启动自检：用虚拟显示跑几秒后退出（打包产物验证用）")
    ap.add_argument("--selftest-seconds", type=float, default=5.0)
    ap.add_argument("--version", action="store_true", help="显示版本并退出")
    return ap.parse_args()


def selftest(seconds: float) -> int:
    """不依赖真实窗口的启动自检：验证数据、引擎、UI 全链路可跑通。

    打包后用它确认「双击真的能启动」，而不是只看退出码。
    """
    import os as _os

    _os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    _os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import time as _time

    from src.app import App
    from src.persistence.settings import Settings
    from src.utils.paths import project_root
    from src.version import APP_VERSION, version_tuple

    print(f"{APP_VERSION} (version {version_tuple()})")
    print(f"项目根目录: {project_root()}")

    settings = Settings()
    settings.set_fullscreen(False)
    settings.set("ui", "animation_speed", 8.0)
    app = App(settings)
    app.running = True
    print(f"中文字体: {app.fonts.font_name}")

    from src.game.setup import available_maps, load_card_registry, preset_list

    maps = available_maps()
    print(f"地图: {', '.join(m['name'] + ' ' + str(m['tile_count']) + '格' for m in maps)}")
    print(f"预设: {', '.join(p['name'] for p in preset_list())}")
    print(f"道具: {len(load_card_registry().ids())} 种")

    specs = [
        {"id": "p1", "name": "自检玩家", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
        {"id": "p2", "name": "电脑甲", "character_id": "char_xiaoman",
         "color_id": "blue", "is_ai": True},
    ]
    app.start_local_game(specs)
    print("已进入对局，开始推进…")

    import pygame as _pg

    deadline = _time.time() + seconds
    frames = 0
    while _time.time() < deadline and app.running:
        for event in _pg.event.get():
            if event.type == _pg.QUIT:
                app.running = False
        app.update(1 / 60)
        if frames % 20 == 0:
            app._render()
        frames += 1

    st = app.local_engine.state if app.local_engine else None
    if st is None:
        print("[失败] 没有拿到游戏状态")
        return 1
    print(f"推进结果: {frames} 帧，第 {st.round_number} 轮 / {st.turn_number} 回合，"
          f"阶段 {st.phase.value}")
    print(f"资金流水 {len(st.ledger.entries)} 条，玩家 {len(st.players)} 人，"
          f"地产 {len(st.properties)} 处")
    alive = all(p.money >= 0 or p.bankrupt for p in st.players)
    print(f"状态检查: {'通过' if alive else '异常'}")
    app._teardown_network()
    _pg.quit()
    print("启动自检完成")
    return 0 if alive else 1


def main() -> int:
    args = parse_args()

    if args.version:
        from src.version import APP_VERSION, version_tuple

        print(f"{APP_VERSION} (version {version_tuple()})")
        return 0

    if args.selftest:
        return selftest(args.selftest_seconds)

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
