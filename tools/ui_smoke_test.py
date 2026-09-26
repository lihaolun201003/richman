"""UI 冒烟测试：用 dummy 显示驱动跑遍所有界面并截图。

用途：
- 在没有人工盯着屏幕时，自动发现 UI 层的崩溃、坐标越界、绘制异常；
- 产出三档分辨率的截图，供人工复核布局。

注意：dummy 驱动能验证「不崩溃」和「画出来」，但不能替代真实试玩。
真实试玩仍然必须做。

用法:
    python tools/ui_smoke_test.py
    python tools/ui_smoke_test.py --shots docs/reports/shots
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from src.game.commands import Command, CommandType  # noqa: E402
from src.game.phases import GamePhase  # noqa: E402
from src.ui.game_scene import ROLL_BUTTON_RECT, SessionView  # noqa: E402
from src.ui.layout import LOGICAL_SIZE, Viewport, translate_event  # noqa: E402

DT = 1.0 / 60.0
RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080)]


class SmokeReporter:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
        return ok

    def failures(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def summary(self) -> None:
        total = len(self.results)
        ok = total - self.failures()
        print("-" * 78)
        print(f"UI 冒烟测试：{ok}/{total} 通过")


def shot(surface: pygame.Surface, path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pygame.image.save(surface, path)
        return True
    except Exception as exc:
        print(f"    截图失败 {path}: {exc}")
        return False


def pump(app, frames: int = 3) -> None:
    """推进若干帧（含事件与绘制）。"""
    for _ in range(frames):
        for event in pygame.event.get():
            app.scenes.handle_event(translate_event(event, app.viewport))
        app.update(DT)
        app.scenes.draw(app.virtual)


def click(app, pos: tuple[int, int]) -> None:
    """模拟在逻辑坐标上点击。"""
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        ev = pygame.event.Event(kind, {"pos": pos, "button": 1})
        app.scenes.handle_event(ev)
    pump(app, 2)


def hover(app, pos: tuple[int, int]) -> None:
    ev = pygame.event.Event(pygame.MOUSEMOTION, {"pos": pos, "rel": (0, 0), "buttons": (0, 0, 0)})
    app.scenes.handle_event(ev)
    pump(app, 2)


def key(app, k: int) -> None:
    ev = pygame.event.Event(pygame.KEYDOWN, {"key": k, "mod": 0, "unicode": ""})
    app.scenes.handle_event(ev)
    pump(app, 2)


def main() -> int:
    ap = argparse.ArgumentParser(description="UI 冒烟测试")
    ap.add_argument("--shots", default="docs/reports/shots", help="截图输出目录")
    ap.add_argument("--turns", type=int, default=40, help="单机对局模拟的回合数")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shots = os.path.join(root, args.shots)

    from src.app import App
    from src.persistence.settings import Settings

    rep = SmokeReporter()
    settings = Settings()
    settings.set_fullscreen(False)
    settings.set_resolution(1600, 900)
    app = App(settings)
    app.running = True

    print("=" * 78)
    print("UI 冒烟测试（dummy 显示驱动）")
    print("=" * 78)

    # ---------------- 1. 主菜单
    print("\n[1] 主菜单")
    ok = True
    try:
        pump(app, 5)
        ok = shot(app.virtual, os.path.join(shots, "01_menu.png"))
    except Exception:
        traceback.print_exc()
        ok = False
    rep.check("主菜单渲染", ok)
    hover(app, (400, 330))
    rep.check("主菜单悬停无异常", True)

    # ---------------- 2. 设置
    print("\n[2] 设置界面")
    try:
        app.scenes.switch_to("settings", back="menu")
        pump(app, 5)
        shot(app.virtual, os.path.join(shots, "02_settings.png"))
        # 拖动音量滑块
        click(app, (400, 236))
        # 切换动画速度
        click(app, (900, 236))
        click(app, (1210, 456))
        pump(app, 3)
        shot(app.virtual, os.path.join(shots, "02b_settings_changed.png"))
        rep.check("设置界面可交互", True,
                  f"动画速度 {app.settings.animation_speed} · 字体 {app.settings.font_scale}")
        app.settings.set("ui", "animation_speed", 1.0)
        app.settings.set("ui", "font_scale", 1.0)
        app.apply_font_scale()
    except Exception:
        traceback.print_exc()
        rep.check("设置界面可交互", False)

    # ---------------- 3. 单机配置
    print("\n[3] 单人游戏配置")
    try:
        app.scenes.switch_to("local_setup")
        pump(app, 5)
        shot(app.virtual, os.path.join(shots, "03_local_setup.png"))
        # 切人数
        click(app, (300, 276))
        click(app, (500, 276))
        # 选角色
        click(app, (300, 400))
        pump(app, 3)
        shot(app.virtual, os.path.join(shots, "03b_local_setup_6p.png"))
        rep.check("单机配置可交互", True)
    except Exception:
        traceback.print_exc()
        rep.check("单机配置可交互", False)

    # ---------------- 4. 局域网配置
    print("\n[4] 局域网建房间 / 加入房间")
    try:
        app.scenes.switch_to("lan_setup", mode="host")
        pump(app, 5)
        shot(app.virtual, os.path.join(shots, "04_lan_host.png"))
        app.scenes.switch_to("lan_setup", mode="join")
        pump(app, 12)
        shot(app.virtual, os.path.join(shots, "04b_lan_join.png"))
        rep.check("局域网配置界面正常", True)
    except Exception:
        traceback.print_exc()
        rep.check("局域网配置界面正常", False)

    # ---------------- 5. 存档浏览
    print("\n[5] 存档浏览")
    try:
        app.scenes.switch_to("save_browser")
        pump(app, 5)
        shot(app.virtual, os.path.join(shots, "05_save_browser.png"))
        rep.check("存档界面渲染", True)
    except Exception:
        traceback.print_exc()
        rep.check("存档界面渲染", False)

    # ---------------- 6. 单机对局（真实引擎 + 模拟操作）
    print(f"\n[6] 单机对局（模拟 {args.turns} 回合）")
    specs = [
        {"id": "p1", "name": "测试玩家", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
        {"id": "p2", "name": "电脑甲", "character_id": "char_xiaoman",
         "color_id": "blue", "is_ai": True},
        {"id": "p3", "name": "电脑乙", "character_id": "char_laochen",
         "color_id": "green", "is_ai": True},
        {"id": "p4", "name": "电脑丙", "character_id": "char_nana",
         "color_id": "amber", "is_ai": True},
    ]
    try:
        app.settings.set("ui", "animation_speed", 6.0)
        app.start_local_game(specs)
        scene = app.scenes.current
        pump(app, 10)
        shot(app.virtual, os.path.join(shots, "06_game_start.png"))
        rep.check("进入对局场景", hasattr(scene, "session") and scene.session is not None)

        engine = app.local_engine
        clicked_roll = 0
        handled_decisions = 0
        modal_shots = 0
        deadline_frames = args.turns * 60 * 8
        frames = 0

        while frames < deadline_frames and not engine.state.game_over:
            frames += 1
            st = engine.state
            scene = app.scenes.current

            # 处理弹窗：点击第一个可用按钮
            if getattr(scene, "modal", None) is not None:
                modal = scene.modal
                if modal_shots < 4:
                    modal_shots += 1
                    shot(app.virtual, os.path.join(shots, f"06c_modal_{modal_shots}.png"))
                handled_decisions += 1
                button = next((b for b in modal.buttons if b.enabled), None)
                if button is not None:
                    click(app, button.rect.center)
                else:
                    modal.close()
                    scene.modal = None
                continue

            decision = scene.session.decision if hasattr(scene, "session") else None
            if decision is not None:
                # 掷骰：模拟点击侧栏掷骰按钮
                if decision.kind == "roll":
                    if clicked_roll < 3:
                        shot(app.virtual, os.path.join(shots, f"06b_wait_roll_{clicked_roll + 1}.png"))
                    clicked_roll += 1
                    click(app, ROLL_BUTTON_RECT.center)
                else:
                    opt = next((o for o in decision.options if o.enabled), None)
                    if opt is not None:
                        scene.session.submit(CommandType.RESOLVE_DECISION,
                                             {"option_id": opt.id}, decision.id)
                continue

            pump(app, 1)

            if frames % 400 == 0:
                hover(app, (300, 300))

        st = engine.state
        rep.check(f"对局推进到第 {st.turn_number} 回合",
                  st.turn_number > 10, f"轮次 {st.round_number}")
        rep.check(f"玩家（真人座位）实际掷骰 {clicked_roll} 次", clicked_roll > 3, "")
        rep.check(f"处理决策弹窗 {handled_decisions} 次", handled_decisions > 0, "")
        shot(app.virtual, os.path.join(shots, "06d_game_mid.png"))

        # 快进到结束（把动画速度拉到很高，并允许多次推进）
        app.settings.set("ui", "animation_speed", 30.0)
        app.apply_animation_speed()
        guard = 0
        max_guard = max(60000, args.turns * 60 * 40)
        while not engine.state.game_over and guard < max_guard:
            guard += 1
            scene = app.scenes.current
            if getattr(scene, "modal", None) is not None:
                modal = scene.modal
                button = next((b for b in modal.buttons if b.enabled), None)
                if button is not None:
                    click(app, button.rect.center)
                else:
                    modal.close()
                    scene.modal = None
                continue
            decision = scene.session.decision if hasattr(scene, "session") else None
            if decision is not None:
                if decision.kind == "roll":
                    click(app, ROLL_BUTTON_RECT.center)
                else:
                    opt = next((o for o in decision.options if o.enabled), None)
                    if opt is not None:
                        scene.session.submit(CommandType.RESOLVE_DECISION,
                                             {"option_id": opt.id}, decision.id)
                continue
            pump(app, 2)

        rep.check("对局能正常结束", engine.state.game_over,
                  f"轮次 {engine.state.round_number}，赢家 "
                  f"{engine.state.player(engine.state.winner_id).name if engine.state.winner_id else '无'}")
        # 让结算界面出现
        for _ in range(60):
            scene = app.scenes.current
            if getattr(scene, "modal", None) is not None:
                break
            pump(app, 1)
        pump(app, 5)
        shot(app.virtual, os.path.join(shots, "06e_game_over.png"))
        rep.check("结算界面出现",
                  getattr(app.scenes.current, "modal", None) is not None)

        # 从结算界面返回
        modal = getattr(app.scenes.current, "modal", None)
        if modal and modal.buttons:
            target = next((b for b in reversed(modal.buttons) if b.enabled), None)
            if target is not None:
                click(app, target.rect.center)
        pump(app, 5)
        rep.check("从结算返回不崩溃", True)
    except Exception:
        traceback.print_exc()
        rep.check("单机对局流程", False)

    # ---------------- 7. 三档分辨率
    print("\n[7] 三档分辨率布局")
    for (w, h) in RESOLUTIONS:
        try:
            app.settings.set_resolution(w, h)
            app.apply_display()
            pump(app, 4)
            shot(app.virtual, os.path.join(shots, f"07_res_{w}x{h}_menu.png"))
            # 重开一局看看棋盘
            app.start_local_game(specs)
            pump(app, 30)
            shot(app.virtual, os.path.join(shots, f"07_res_{w}x{h}_game.png"))
            rep.check(f"{w}×{h} 渲染正常", True)
        except Exception:
            traceback.print_exc()
            rep.check(f"{w}×{h} 渲染正常", False)

    # ---------------- 8. 字体
    print("\n[8] 中文字体")
    try:
        fonts = app.fonts
        probe = "大富翁 城市广场 星河商街 机遇 看守所 破产"
        img = fonts.body().render(probe, True, (255, 255, 255))
        w = img.get_width()
        rep.check("中文字体可用", w > 100 and not fonts.warning,
                  f"字体 {fonts.font_name}，宽度 {w}px")
        # 检测是否渲染成方块（宽度异常小说明缺字）
        rep.check("中文非方块显示", w >= len(probe) * 8, f"{w}px / {len(probe)} 字")
    except Exception:
        traceback.print_exc()
        rep.check("中文字体可用", False)

    rep.summary()
    pygame.quit()
    return 1 if rep.failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
