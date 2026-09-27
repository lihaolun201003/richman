"""界面巡检截图工具：把任意界面状态渲染成 PNG，供人工 / 自动复核。

与 ui_smoke_test.py 的区别：
- smoke 只保证「不崩溃」；本工具专门产出**特定界面状态**的截图，
  方便在改造 UI 时逐个界面比对（主菜单 / 大厅 / 对局各阶段 / 资产 / 债务 /
  商店 / 机遇 / 结算 / 图鉴 / 设置 / 教程）。
- 支持 --scene 只渲染单个场景，迭代时更快。

用法:
    python tools/ui_shots.py                    # 全部场景
    python tools/ui_shots.py --scene game --shots docs/reports/shots_v03
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

DT = 1.0 / 60.0


def shot(surface: pygame.Surface, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pygame.image.save(surface, path)
    print(f"  saved {os.path.relpath(path)}")


class Harness:
    def __init__(self, shots: str) -> None:
        self.shots = shots
        from src.app import App
        from src.persistence.settings import Settings

        settings = Settings()
        settings.set_fullscreen(False)
        settings.set_resolution(1600, 900)
        settings.set("ui", "animation_speed", 1.0)
        settings.set("ui", "full_render", True)
        self.app = App(settings)
        self.app.running = True

    def pump(self, frames: int = 3, modal_wait: bool = False) -> None:
        for _ in range(frames):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.app.running = False
            self.app.update(DT)
            self.app.scenes.draw(self.app.virtual)
        if modal_wait:
            scene = self.app.scenes.current
            modal = getattr(scene, "modal", None)
            if modal is not None:
                # 把弹窗弹出动画跑完，避免截到动画中途
                for _ in range(40):
                    modal.update(DT)
                    self.app.scenes.draw(self.app.virtual)

    def save(self, name: str) -> None:
        shot(self.app.virtual, os.path.join(self.shots, name))


def render_menu(h: Harness) -> None:
    h.app.scenes.switch_to("menu")
    h.pump(20)
    h.save("v03_01_menu.png")


def render_settings(h: Harness) -> None:
    h.app.scenes.switch_to("settings", back="menu")
    h.pump(6)
    h.save("v03_02_settings.png")


def render_local_setup(h: Harness) -> None:
    h.app.scenes.switch_to("local_setup")
    h.pump(6)
    h.save("v03_03_local_setup.png")


def render_lan(h: Harness) -> None:
    h.app.scenes.switch_to("lan_setup", mode="host")
    h.pump(6)
    h.save("v03_04_lan_host.png")
    h.app.scenes.switch_to("lan_setup", mode="join")
    h.pump(6)
    h.save("v03_04b_lan_join.png")


def render_help(h: Harness) -> None:
    h.app.scenes.switch_to("help", back="menu")
    h.pump(6)
    h.save("v03_05_help.png")


def _start_game(h: Harness, players: int = 4):
    specs = [
        {"id": "p1", "name": "李昊伦", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
    ]
    chars = ["char_xiaoman", "char_laochen", "char_nana", "char_tiedan", "char_zhou"]
    colors = ["blue", "green", "amber", "purple", "orange"]
    for i in range(1, players):
        specs.append({
            "id": f"p{i + 1}", "name": f"电脑{i}",
            "character_id": chars[(i - 1) % len(chars)],
            "color_id": colors[(i - 1) % len(colors)],
            "is_ai": True,
        })
    h.app.settings.set("ui", "animation_speed", 1.0)
    h.app.start_local_game(specs)
    h.pump(4)
    return h.app.local_engine


def render_game(h: Harness) -> None:
    engine = _start_game(h)
    from src.game.phases import GamePhase
    from src.ui.game_scene import ROLL_BUTTON_RECT

    h.save("v03_06_game_start.png")

    # 推进直到自己该掷骰
    for _ in range(600):
        h.pump(1)
        scene = h.app.scenes.current
        if getattr(scene, "modal", None) is not None:
            modal = scene.modal
            for _ in range(40):
                modal.update(DT)
                h.app.scenes.draw(h.app.virtual)
            h.save("v03_06b_decision_dialog.png")
            break
        decision = scene.session.decision if hasattr(scene, "session") else None
        if decision is not None and decision.kind == "roll":
            h.save("v03_06b_wait_roll.png")
            break

    # 掷骰 → 骰子动画中
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        h.app.scenes.handle_event(
            pygame.event.Event(kind, {"pos": ROLL_BUTTON_RECT.center, "button": 1}))
    for _ in range(8):
        h.pump(1)
        if engine.state.phase is GamePhase.ROLLING:
            h.save("v03_06c_rolling.png")
            break
    for _ in range(20):
        h.pump(1)
        if engine.state.phase is GamePhase.MOVING:
            h.save("v03_06d_moving.png")
            break

    # 跑一段，随机截几张中期画面 + 资产面板 + 商店 + 机遇
    seen = set()
    for step in range(4000):
        h.pump(1)
        scene = h.app.scenes.current
        st = engine.state
        modal = getattr(scene, "modal", None)
        if modal is not None:
            name = type(modal).__name__
            if name not in seen and name in ("AssetPanel", "ShopDialog", "DecisionDialog",
                                            "ChanceCardDialog", "CardTargetDialog"):
                seen.add(name)
                for _ in range(40):
                    modal.update(DT)
                    h.app.scenes.draw(h.app.virtual)
                h.save(f"v03_07_{name}.png")
            # 处理资产/商店类面板，避免卡住
            if name == "AssetPanel":
                if getattr(modal, "busy", False):
                    continue
                card, rect = modal.find_action("upgrade")
                if card is None:
                    card, rect = modal.find_action("mortgage")
                if rect is not None:
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        h.app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": rect.center, "button": 1}))
                elif modal.on_declare is not None:
                    rect = modal._declare_button_rect()
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        h.app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": rect.center, "button": 1}))
                else:
                    modal.close()
                    scene.modal = None
            elif name == "ShopDialog":
                btn = next((b for b in modal.buttons if b.enabled), None)
                modal.close()
                scene.modal = None
                if btn is not None:
                    btn.on_click()
            else:
                btn = next((b for b in modal.buttons if b.enabled), None)
                if btn is not None:
                    btn.on_click()
                else:
                    modal.close()
                    scene.modal = None
            continue
        decision = scene.session.decision if hasattr(scene, "session") else None
        if decision is not None:
            opt = next((o for o in decision.options if o.enabled), None)
            if opt is not None:
                if decision.kind == "roll":
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        h.app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": ROLL_BUTTON_RECT.center,
                                                      "button": 1}))
                else:
                    scene.session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                         decision.id)
            continue
        if st.game_over:
            break
        if len(seen) >= 5:
            break

    # 中期画面
    h.app.settings.set("ui", "animation_speed", 8.0)
    h.app.apply_animation_speed()
    for _ in range(400):
        h.pump(1)
        scene = h.app.scenes.current
        if getattr(scene, "modal", None) is None:
            decision = getattr(scene, "session", None)
            if decision is not None and decision.decision is None:
                h.save("v03_06e_game_mid.png")
                break
        break
    h.save("v03_06e_game_mid.png")

    # 跑到结算
    for _ in range(200000):
        if engine.state.game_over:
            break
        scene = h.app.scenes.current
        modal = getattr(scene, "modal", None)
        if modal is not None:
            btn = next((b for b in modal.buttons if b.enabled), None)
            if btn is not None:
                btn.on_click()
            else:
                modal.close()
                scene.modal = None
            h.pump(1)
            continue
        decision = scene.session.decision if hasattr(scene, "session") else None
        if decision is not None:
            opt = next((o for o in decision.options if o.enabled), None)
            if opt is not None:
                if decision.kind == "roll":
                    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                        h.app.scenes.handle_event(
                            pygame.event.Event(kind, {"pos": ROLL_BUTTON_RECT.center,
                                                      "button": 1}))
                else:
                    scene.session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                         decision.id)
        h.pump(1)
    for _ in range(200):
        h.pump(1)
        scene = h.app.scenes.current
        if getattr(scene, "modal", None) is not None:
            break
    h.pump(40, modal_wait=True)
    h.save("v03_08_game_over.png")


def render_lobby(h: Harness) -> None:
    """开一个真实房间，看看大厅长什么样。"""
    try:
        h.app.start_host("李昊伦 的房间", "李昊伦", "char_ajin", 28190)
    except Exception:
        traceback.print_exc()
        return
    host = h.app.host
    host.lobby.add_ai(character_id="char_nana", color_id="green")
    host.lobby.add_ai(character_id="char_tiedan", color_id="amber")
    host.broadcast_lobby()
    h.pump(8)
    h.save("v03_09_lobby_host.png")


def render_lan_diag(h: Harness) -> None:
    h.app.scenes.switch_to("net_diag", back="menu")
    h.pump(6)
    h.save("v03_10_net_diag.png")


def render_tutorial(h: Harness) -> None:
    h.app.scenes.switch_to("tutorial")
    h.pump(8)
    h.save("v03_11_tutorial.png")
    for i in range(1, 5):
        h.app.scenes.current.advance()
        h.pump(6)
        h.save(f"v03_11_tutorial_{i}.png")


SCENES = {
    "menu": render_menu,
    "settings": render_settings,
    "local_setup": render_local_setup,
    "lan": render_lan,
    "help": render_help,
    "game": render_game,
    "lobby": render_lobby,
    "diag": render_lan_diag,
    "tutorial": render_tutorial,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="界面巡检截图")
    ap.add_argument("--shots", default="docs/reports/shots_v03")
    ap.add_argument("--scene", default="", help="只渲染指定场景（逗号分隔）")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shots = os.path.join(root, args.shots)

    names = [n.strip() for n in args.scene.split(",") if n.strip()] or list(SCENES)
    h = Harness(shots)
    print("=" * 70)
    for name in names:
        fn = SCENES.get(name)
        if fn is None:
            print(f"  [跳过] 未知场景 {name}")
            continue
        print(f"[{name}]")
        try:
            fn(h)
        except Exception:
            traceback.print_exc()
    h.app._teardown_network()
    pygame.quit()
    print("=" * 70)
    print("截图完成：" + shots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
