"""v0.4 界面回归截图：把每个关键界面渲染成 PNG，人工逐个复核。

与 ui_smoke_test.py 的分工：
- smoke 只保证「不崩溃」；本工具负责产出**特定界面状态**的截图，
  改造 UI 时逐张比对（主菜单 / 建房 / 加入 / 大厅 / 诊断 / 对局各阶段 /
  资产 / 债务 / 掉线 / 重连 / 破产 / 结算 / 时间线 / 本局记录 / 引导）。

用法:
    python tools/v04_shots.py                     # 全部
    python tools/v04_shots.py --scene menu,game    # 只跑指定场景
    python tools/v04_shots.py --out docs/reports/shots_v04
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
        settings.set("ui", "ai_speed", 1.0)
        settings.set("ui", "show_guide", True)
        settings.set("ui", "guide_done", False)
        settings.set("ui", "show_tooltips", True)
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
                for _ in range(30):
                    modal.update(DT)
                    self.app.scenes.draw(self.app.virtual)

    def save(self, name: str) -> None:
        shot(self.app.virtual, os.path.join(self.shots, name))

    def game_scene(self):
        scene = self.app.scenes.current
        from src.ui.game_scene import GameScene

        return scene if isinstance(scene, GameScene) else None


# ==================================================================== 菜单与设置

def render_menu(h: Harness) -> None:
    h.app.scenes.switch_to("menu")
    h.pump(24)
    h.save("v04_01_menu.png")
    scene = h.app.scenes.current
    scene._toggle_net()
    h.pump(8)
    h.save("v04_02_menu_lan_group.png")


def render_settings(h: Harness) -> None:
    h.app.scenes.switch_to("settings", back="menu")
    h.pump(8)
    h.save("v04_03_settings.png")


def render_local_setup(h: Harness) -> None:
    h.app.scenes.switch_to("local_setup")
    h.pump(8)
    h.save("v04_04_local_setup.png")
    scene = h.app.scenes.current
    scene.count_select.set_value(6)
    scene._set_count(6)
    # 切到聚会局，展示人数自适应建议
    scene.selector.set_preset("party")
    h.pump(8)
    h.save("v04_05_party_mode.png")


# ==================================================================== 局域网

class _FakeListener:
    """假发现器：只为了截出「搜到房间」的界面，不参与任何真实网络行为。"""

    def __init__(self, rooms: list) -> None:
        self._rooms = rooms
        self.error = ""

    def snapshot(self) -> list:
        return self._rooms

    def stop(self) -> None:
        pass


def render_lan(h: Harness) -> None:
    h.app.scenes.switch_to("lan_setup", mode="host")
    h.pump(10)
    h.save("v04_06_lan_host.png")
    h.app.scenes.switch_to("lan_setup", mode="join")
    h.pump(10)
    # 伪造几条发现结果，展示房间卡片长什么样（不依赖真的有人在广播）
    scene = h.app.scenes.current
    from src.network.discovery import RoomInfo

    rooms = [
        RoomInfo({"room_name": "321 宿舍局", "host_name": "阿明", "port": 28080,
                  "players": 2, "max_players": 6, "phase": "lobby",
                  "map_name": "城市之光", "preset_name": "聚会局",
                  "protocol_version": 3, "app_version": "Richman v0.4.0-rc1"},
                 "192.168.1.23"),
        RoomInfo({"room_name": "老张的房间", "host_name": "老张", "port": 28080,
                  "players": 6, "max_players": 6, "phase": "lobby",
                  "map_name": "海滨假日", "preset_name": "标准局",
                  "protocol_version": 3, "app_version": "Richman v0.4.0-rc1"},
                 "192.168.1.31"),
        RoomInfo({"room_name": "版本不一致的房间", "host_name": "小李", "port": 28080,
                  "players": 1, "max_players": 6, "phase": "lobby",
                  "map_name": "城市之光", "preset_name": "标准局",
                  "protocol_version": 2, "app_version": "Richman v0.3.0"},
                 "192.168.1.44"),
    ]
    scene._stop_listener()
    scene.listener = _FakeListener(rooms)
    scene.status = "正在搜索同一局域网内的房间…"
    h.pump(6)
    h.save("v04_07_lan_join.png")


def render_lobby(h: Harness) -> None:
    """房主视角大厅：含一个掉线玩家（AI 接管）与一个未准备玩家。"""
    try:
        h.app.start_host("321 宿舍局", "阿明", "char_ajin", 28190)
    except Exception:
        traceback.print_exc()
        return
    host = h.app.host
    host.lobby.add_ai(character_id="char_nana", color_id="green")
    host.broadcast_lobby()
    h.pump(10)
    h.save("v04_08_lobby_host.png")

    # 伪造一个「已准备」与一个「掉线 + AI 接管」的客户端座位，展示状态差异
    from src.network.session import PlayerSession, new_player_id
    from src.network.transport import TcpConnection
    import socket as _sock

    a, b = _sock.socketpair()
    conn = TcpConnection(a, ("192.168.1.23", 51000), name="demo")
    session = PlayerSession(new_player_id(), "小满", character_id="char_xiaoman",
                            color_id="blue", slot=2)
    session.conn = conn
    session.ready = True
    host.lobby.sessions.append(session)

    a2, b2 = _sock.socketpair()
    conn2 = TcpConnection(a2, ("192.168.1.31", 51001), name="demo2")
    session2 = PlayerSession(new_player_id(), "老陈", character_id="char_laochen",
                             color_id="amber", slot=3)
    session2.conn = conn2
    session2.mark_disconnected()
    session2.bot_takeover = True
    host.lobby.sessions.append(session2)
    host.broadcast_lobby()
    h.pump(8)
    h.save("v04_09_lobby_states.png")

    # 清掉伪造座位，避免影响后续
    conn.close("demo")
    conn2.close("demo")
    host.lobby.sessions = [s for s in host.lobby.sessions if s.is_host or s.is_ai]
    host.broadcast_lobby()
    h.pump(4)


def render_net_diag(h: Harness) -> None:
    h.app.scenes.switch_to("net_diag", back="menu")
    h.pump(10)
    h.save("v04_10_net_diag.png")

    # 展示「测试连接」的失败分类结果
    from src.network import diagnose

    scene = h.app.scenes.current
    scene.last_test = diagnose.TestResult(
        status=diagnose.TEST_TIMEOUT, host="192.168.1.31", port=28080,
        message="192.168.1.31:28080 在 3 秒内没有响应", elapsed_ms=3050,
        advice=diagnose.TEST_ADVICE[diagnose.TEST_TIMEOUT])
    scene.tester = None
    h.pump(6)
    h.save("v04_11_net_diag_test.png")

    scene.last_test = diagnose.TestResult(
        status=diagnose.TEST_OK, host="192.168.1.23", port=28080,
        message="找到房间「321 宿舍局」（2/6 人，可加入）", elapsed_ms=12,
        room={"room_name": "321 宿舍局", "players": 2, "max_players": 6,
              "map_name": "城市之光"},
        advice=diagnose.TEST_ADVICE[diagnose.TEST_OK])
    h.pump(6)
    h.save("v04_12_net_diag_ok.png")


# ==================================================================== 对局

def _start_game(h: Harness, players: int = 4, *, guide: bool = False,
                preset: str = "standard"):
    specs = [
        {"id": "p1", "name": "阿明", "character_id": "char_ajin",
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
    h.app.settings.set("ui", "show_guide", guide)
    h.app.settings.set("ui", "guide_done", not guide)
    h.app._last_local_options = {"map_file": "default_map.json", "preset": preset}
    h.app.start_local_game(specs)
    h.pump(6)
    return h.app.local_engine


def _advance_to_decision(h: Harness, engine, frames: int = 900,
                         shot_name: str = "") -> bool:
    """推进到「我有决策」或弹窗出现；返回是否命中。"""
    from src.ui.game_scene import ROLL_BUTTON_RECT

    for _ in range(frames):
        h.pump(1)
        scene = h.game_scene()
        if scene is None:
            return False
        if getattr(scene, "modal", None) is not None:
            if shot_name:
                h.pump(20)
                h.save(shot_name)
            return True
        decision = scene.session.decision if scene.session else None
        if decision is not None and decision.kind == "roll":
            if shot_name:
                h.save(shot_name)
            return True
        # 自动按第一种可用选项，让流程继续
        if decision is not None:
            opt = next((o for o in decision.options if o.enabled), None)
            if opt is not None:
                scene.session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                     decision.id)
    return False


def render_game(h: Harness) -> None:
    engine = _start_game(h, 4, guide=True)
    h.pump(10)
    h.save("v04_13_guide.png")

    # 关掉引导，进入正常对局
    scene = h.game_scene()
    if scene is not None and scene._guide is not None:
        scene._guide.finish(skip_all=False)
    h.pump(6)
    h.save("v04_14_game_start.png")

    from src.game.phases import GamePhase
    from src.ui.game_scene import ROLL_BUTTON_RECT

    # 掷骰 → 移动
    for _ in range(600):
        h.pump(1)
        scene = h.game_scene()
        if scene is None:
            break
        decision = scene.session.decision if scene.session else None
        if decision is not None and decision.kind == "roll":
            for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                h.app.scenes.handle_event(
                    pygame.event.Event(kind, {"pos": ROLL_BUTTON_RECT.center,
                                              "button": 1}))
        if engine.state.phase is GamePhase.MOVING:
            h.pump(2)
            h.save("v04_15_game_moving.png")
            break
        if decision is not None:
            opt = next((o for o in decision.options if o.enabled), None)
            if opt is not None:
                scene.session.submit("RESOLVE_DECISION", {"option_id": opt.id},
                                     decision.id)

    # 逐个界面：买地弹窗 / 商店 / 事件卡 / 道具选目标 / 资产面板 / 债务面板
    # 用「先跑够帧数，遇到就截图」的方式，尽量把每种弹窗都抓到
    wanted = {
        "DecisionDialog": "v04_16_decision_buy.png",
        "ShopDialog": "v04_18_shop.png",
        "ChanceCardDialog": "v04_19_chance_card.png",
        "CardTargetDialog": "v04_20_card_target.png",
        "AssetPanel": "v04_17_asset_panel.png",
    }
    seen: dict[str, str] = {}
    frames = 0
    for _ in range(20000):
        h.pump(1)
        frames += 1
        scene = h.game_scene()
        if scene is None:
            break
        if engine.state.game_over:
            break
        modal = getattr(scene, "modal", None)
        if modal is not None:
            name = type(modal).__name__
            want = wanted.get(name)
            if want and name not in seen:
                seen[name] = want
                h.pump(20)
                h.save(want)
                # 债务处理面板是资产面板的 debt 模式，单独留一张
                if name == "AssetPanel" and getattr(modal, "debt", None):
                    h.save("v04_17b_debt_panel.png")
            _auto_modal(h, scene, modal)
            continue
        # 主动打开资产面板（玩家平时就会点它），顺带把债务模式也抓下来
        if frames % 240 == 0 and "AssetPanel" not in seen:
            scene.open_asset_panel()
            continue
        # 主动用一张道具（有目标的会弹选择框）
        if frames % 400 == 0 and "CardTargetDialog" not in seen:
            scene._use_card_by_index(0)
            continue
        decision = scene.session.decision if scene.session else None
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
        if len(seen) >= len(wanted):
            break

    # 本局记录
    h.app.settings.set("ui", "animation_speed", 4.0)
    h.app.apply_animation_speed()
    for _ in range(60):
        h.pump(1)
        scene = h.game_scene()
        if scene is not None and getattr(scene, "modal", None) is None:
            break
    scene = h.game_scene()
    if scene is not None:
        scene.open_player_log()
        h.pump(24)
        h.save("v04_21_player_log.png")
        scene.modal = None

    # 破产 / 掉线 / 重连三种演出（用真实演出类构造，位置与对局内一致）
    _render_network_events(h, scene)

    # 跑到结算
    h.app.settings.set("ui", "animation_speed", 6.0)
    h.app.apply_animation_speed()
    for _ in range(200000):
        if engine.state.game_over:
            break
        scene = h.game_scene()
        if scene is None:
            break
        modal = getattr(scene, "modal", None)
        if modal is not None:
            if type(modal).__name__ == "AssetPanel":
                _auto_modal(h, scene, modal)
            else:
                btn = next((b for b in modal.buttons if b.enabled), None)
                if btn is not None:
                    btn.on_click()
                else:
                    modal.close()
                    scene.modal = None
            h.pump(1)
            continue
        decision = scene.session.decision if scene.session else None
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
    for _ in range(120):
        h.pump(1)
    h.pump(30, modal_wait=False)
    h.save("v04_22_game_over.png")
    scene = h.game_scene()
    if scene is not None and getattr(scene, "modal", None) is not None:
        modal = scene.modal
        if hasattr(modal, "_set_view"):
            modal._set_view("timeline")
            h.pump(10)
            h.save("v04_23_timeline.png")
        modal._set_view("summary") if hasattr(modal, "_set_view") else None


def _render_network_events(h: Harness, scene) -> None:
    """破产演出 / 掉线覆盖层 / 重连成功提示。"""
    if scene is None:
        return
    from src.ui.presentation import BankruptcyBanner

    scene.presenter.push_bankruptcy(BankruptcyBanner(
        "电脑2", debt=8420, assets=6150, properties=4, creditor="阿明",
        reason="租金不足"))
    h.pump(12)
    h.save("v04_24_bankrupt.png")
    scene.presenter.bankruptcy = None

    # 掉线覆盖层：直接构造真实的 ReconnectDialog（参数与产品一致：150 秒 / 50 次）
    from src.ui.dialogs import ReconnectDialog

    dialog = ReconnectDialog("与房主的连接已中断（网络不可达）", grace_sec=150.0,
                             max_attempts=50, takeover_after=30.0)
    dialog.sync("正在尝试重新连接…", 2, 132.4, 0.12,
                "房主会为你保留座位 30 秒；超时后由 AI 接管，本局不会中断。"
                "你可以继续等，也可以点「立即重试」。")
    scene.modal = dialog
    h.pump(20)
    h.save("v04_25_disconnected.png")

    # 超过宽限期的样子：文案会变成「座位现在由 AI 临时接管」
    dialog.sync("正在尝试重新连接…", 15, 68.0, 0.55, "")
    h.pump(6)
    h.save("v04_25b_disconnected_takeover.png")
    scene.modal = None

    # 重连成功提示（app 级居中提示）
    h.app.show_status("已重新连接到房间", "第 2 次尝试成功", "success", 6.0)
    h.pump(10)
    h.save("v04_26_reconnected.png")
    h.app.status_overlay = None


def _auto_modal(h: Harness, scene, modal) -> None:
    """把弹窗按「玩家会做的正常选择」处理掉，让对局继续。"""
    name = type(modal).__name__
    if name == "AssetPanel":
        if getattr(modal, "busy", False):
            h.pump(1)
            return
        card, rect = modal.find_action("upgrade")
        if rect is not None:
            for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                h.app.scenes.handle_event(
                    pygame.event.Event(kind, {"pos": rect.center, "button": 1}))
            return
        if modal.on_declare is not None:
            rect = modal._declare_button_rect()
            for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                h.app.scenes.handle_event(
                    pygame.event.Event(kind, {"pos": rect.center, "button": 1}))
            return
        modal.close()
        scene.modal = None
        return
    if name == "ShopDialog":
        btn = next((b for b in modal.buttons if b.enabled), None)
        modal.close()
        scene.modal = None
        if btn is not None:
            btn.on_click()
        return
    btn = next((b for b in modal.buttons if b.enabled), None)
    if btn is not None:
        btn.on_click()
    else:
        modal.close()
        scene.modal = None


def render_net_events(h: Harness) -> None:
    """只截「破产 / 掉线 / 重连」这几张演出图（不用跑完整局）。"""
    _start_game(h, 4, guide=False)
    h.pump(20)
    scene = h.game_scene()
    _render_network_events(h, scene)


def render_tutorial(h: Harness) -> None:
    h.app.settings.set("ui", "show_guide", False)
    h.app.settings.set("ui", "guide_done", True)
    h.app.start_tutorial()
    h.pump(20)
    h.save("v04_27_tutorial.png")


SCENES = {
    "menu": render_menu,
    "settings": render_settings,
    "local_setup": render_local_setup,
    "lan": render_lan,
    "lobby": render_lobby,
    "diag": render_net_diag,
    "game": render_game,
    "netevents": render_net_events,
    "tutorial": render_tutorial,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="v0.4 界面回归截图")
    ap.add_argument("--shots", "--out", dest="shots",
                    default="docs/reports/shots_v04")
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
    print(f"截图完成：{shots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
