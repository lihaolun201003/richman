"""真实窗口启动自检。

与 ui_smoke_test.py 不同：这里使用真实的 SDL 视频驱动创建真实窗口，
验证「双击运行游戏」这条路径真的能跑起来（不是 dummy 驱动下的假启动）。

会依次做：
    创建窗口 → 渲染主菜单 → 进入单机配置 → 开始对局 → 推进若干回合 → 截图 → 退出

用法:
    python tools/launch_check.py
    python tools/launch_check.py --seconds 12 --shots docs/reports/shots
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

DT = 1.0 / 60.0


def main() -> int:
    ap = argparse.ArgumentParser(description="真实窗口启动自检")
    ap.add_argument("--seconds", type=float, default=10.0, help="总运行时长")
    ap.add_argument("--shots", default="docs/reports/shots", help="截图目录")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--full-game", action="store_true",
                    help="在真实窗口里跑完整整一局直到结算（会加速动画）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shots = os.path.join(root, args.shots)
    os.makedirs(shots, exist_ok=True)

    print("=" * 70)
    print("真实窗口启动自检")
    print("=" * 70)

    from src.app import App
    from src.persistence.settings import Settings
    from src.ui.layout import translate_event as _translate

    settings = Settings()
    settings.set_fullscreen(False)
    settings.set_resolution(1600, 900)
    settings.set("ui", "animation_speed", 4.0)

    try:
        app = App(settings)
    except Exception:
        print("[FAIL] 窗口创建失败：")
        traceback.print_exc()
        return 1

    driver = pygame.display.get_driver()
    print(f"  视频驱动 : {driver}")
    print(f"  窗口尺寸 : {app.screen.get_size()}")
    print(f"  逻辑分辨率: {app.virtual.get_size()}")
    print(f"  中文字体 : {app.fonts.font_name}")

    ok = True
    deadline = time.time() + args.seconds

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
                app.scenes.handle_event(_translate(event, app.viewport))
            app.update(DT)
            app._render()
            time.sleep(DT)

    try:
        print("\n[1] 主菜单（真实窗口）")
        pump(2.0)
        pygame.image.save(app.screen, os.path.join(shots, "launch_01_menu.png"))
        print("  已截图 launch_01_menu.png")

        print("[2] 进入单机对局（真实窗口）")
        specs = [
            {"id": "p1", "name": "本机玩家", "character_id": "char_ajin",
             "color_id": "red", "is_ai": False, "is_host": True},
            {"id": "p2", "name": "电脑甲", "character_id": "char_xiaoman",
             "color_id": "blue", "is_ai": True},
            {"id": "p3", "name": "电脑乙", "character_id": "char_laochen",
             "color_id": "green", "is_ai": True},
            {"id": "p4", "name": "电脑丙", "character_id": "char_nana",
             "color_id": "amber", "is_ai": True},
        ]
        app.start_local_game(specs)
        pump(3.0)
        pygame.image.save(app.screen, os.path.join(shots, "launch_02_game.png"))
        print("  已截图 launch_02_game.png")

        print("[3] 推进对局（真实窗口，模拟点击掷骰）")
        from src.ui.game_scene import ROLL_BUTTON_RECT

        rolls = 0
        frames = 0
        while time.time() < deadline and frames < 60 * 30:
            frames += 1
            scene = app.scenes.current
            modal = getattr(scene, "modal", None)
            if modal is not None:
                button = next((b for b in modal.buttons if b.enabled), None)
                if button is not None:
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": button.rect.center, "button": 1}))
                else:
                    modal.close()
                    scene.modal = None
            else:
                session = getattr(scene, "session", None)
                decision = session.decision if session else None
                if decision is not None:
                    if decision.kind == "roll":
                        rolls += 1
                        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                            app.scenes.handle_event(
                                pygame.event.Event(kind,
                                                   {"pos": ROLL_BUTTON_RECT.center, "button": 1}))
                    else:
                        opt = next((o for o in decision.options if o.enabled), None)
                        if opt is not None:
                            session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                           decision.id)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
            app.update(DT)
            app._render()
            time.sleep(DT)

        st = app.local_engine.state if app.local_engine else None
        if st is not None:
            print(f"  对局推进至第 {st.turn_number} 回合（第 {st.round_number} 轮），"
                  f"真人掷骰 {rolls} 次")
        pygame.image.save(app.screen, os.path.join(shots, "launch_03_progress.png"))
        print("  已截图 launch_03_progress.png")

        if args.full_game:
            print("\n[4] 真实窗口下跑完整局直到结算")
            # 真实窗口下每帧都要真的渲染（~60fps），因此整局需要的时间比
            # dummy 驱动长得多：把动画拉到最快，并给足 12 分钟预算。
            app.settings.set("ui", "animation_speed", 60.0)
            app.apply_animation_speed()
            t0 = time.time()
            guard = 0
            max_guard = 600000
            budget = 720.0
            game_over_shown = False
            last_report = 0.0
            while guard < max_guard and time.time() - t0 < budget:
                guard += 1
                scene = app.scenes.current
                modal = getattr(scene, "modal", None)
                if modal is not None:
                    from src.ui.dialogs import GameOverDialog

                    if isinstance(modal, GameOverDialog):
                        game_over_shown = True
                        break
                    button = next((b for b in modal.buttons if b.enabled), None)
                    if button is not None:
                        for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                            app.scenes.handle_event(
                                pygame.event.Event(kind, {"pos": button.rect.center,
                                                          "button": 1}))
                    else:
                        modal.close()
                        scene.modal = None
                else:
                    session = getattr(scene, "session", None)
                    decision = session.decision if session else None
                    if decision is not None:
                        if decision.kind == "roll":
                            for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                                app.scenes.handle_event(
                                    pygame.event.Event(kind,
                                                       {"pos": ROLL_BUTTON_RECT.center,
                                                        "button": 1}))
                        else:
                            opt = next((o for o in decision.options if o.enabled), None)
                            if opt is not None:
                                session.submit("RESOLVE_DECISION",
                                               {"option_id": opt.id}, decision.id)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        app.running = False
                app.update(DT)
                app._render()
                # 每 30 秒报一次进度，避免长时间没有输出让人以为卡住了
                if time.time() - last_report > 30:
                    last_report = time.time()
                    st_now = app.local_engine.state if app.local_engine else None
                    if st_now is not None:
                        print(f"    …第 {st_now.round_number} 轮 / "
                              f"{st_now.turn_number} 回合，"
                              f"破产 {sum(1 for p in st_now.players if p.bankrupt)} 人",
                              flush=True)
                time.sleep(0.001)

            st = app.local_engine.state if app.local_engine else None
            if st is not None:
                winner = st.player(st.winner_id)
                print(f"  对局结束：{st.game_over}，共 {st.round_number} 轮 / "
                      f"{st.turn_number} 回合，赢家 {winner.name if winner else '无'}")
                print(f"  破产人数 {sum(1 for p in st.players if p.bankrupt)}，"
                      f"渲染帧数 {guard}，耗时 {time.time() - t0:.1f}s")
            pygame.image.save(app.screen, os.path.join(shots, "launch_04_finished.png"))
            print("  已截图 launch_04_finished.png")
            if not game_over_shown:
                ok = False
                print("  [FAIL] 未在真实窗口中看到结算界面")
            else:
                print("  [OK] 真实窗口下完整跑完一局并弹出结算界面")

        print("\n[5] 关闭窗口")
        app._teardown_network()
        pygame.quit()
        print("  已正常退出")
    except Exception:
        ok = False
        print("\n[FAIL] 运行中出现异常：")
        traceback.print_exc()
        try:
            pygame.quit()
        except Exception:
            pass

    print("=" * 70)
    print("真实窗口启动自检：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
