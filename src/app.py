"""应用主体：窗口、主循环、场景装配、网络会话管理。

职责边界：
- App 负责「把东西装起来」：字体、设置、音频、网络、场景；
- 场景负责「一屏之内的事」；
- 引擎负责规则，网络负责传输，UI 只发送 Command。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Any

import pygame

from .audio.manager import audio
from .controllers.base import LocalController
from .game.engine import GameEngine
from .game.setup import create_engine
from .network.client import ClientError, GameClient
from .network.discovery import DiscoveryBroadcaster
from .network.host import RECONNECT_GRACE_SEC, GameHost, HostError
from .network.reconnect import STATUS_EXHAUSTED, STATUS_RECOVERED, AutoReconnector
from .persistence import savegame
from .persistence.settings import Settings
from .ui import theme
from .ui.game_scene import GameScene, SessionView
from .ui.help_scene import HelpScene
from .ui.layout import LOGICAL_HEIGHT, LOGICAL_SIZE, LOGICAL_WIDTH, Viewport, translate_event
from .ui.lobby import LobbyScene
from .ui.menu import MenuScene
from .ui.net_diag_scene import NetDiagScene
from .ui.scene import SceneManager
from .ui.settings_scene import SettingsScene
from .ui.setup_scenes import LanSetupScene, LocalSetupScene, SaveBrowserScene
from .ui.toast import ToastManager
from .ui.tutorial import TutorialOverlay
from .utils.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

#: 每帧最大步长，避免窗口拖动后逻辑跳跃
MAX_DT = 0.1

#: 客户端的重连总时长。刻意比房主宽限期（RECONNECT_GRACE_SEC = 30 秒）长得多：
#: 房主 30 秒后交给 AI 接管，而玩家可能正在重启 Wi-Fi、换网线、关掉重开客户端。
#: 如果两边都是 30 秒，玩家回来时客户端早已放弃 —— 「AI 接管后接回控制权」
#: 这条路径就永远走不到（v0.3 的实际问题）。
CLIENT_RECONNECT_WINDOW = 150.0


class App:
    """游戏应用。"""

    def __init__(self, settings: Settings | None = None) -> None:
        setup_logging()
        pygame.init()
        try:
            pygame.display.set_caption("Richman 大富翁 · 城市之光")
        except Exception:
            pass

        self.settings = settings or Settings.load()
        self.audio = audio
        self.running = True
        self.clock = pygame.time.Clock()
        self.virtual = pygame.Surface(LOGICAL_SIZE)
        self.viewport = Viewport()
        self.fonts = theme.FontManager(self.settings.font_scale)
        self.toasts = ToastManager()
        self.session_error: str = ""

        self._init_display()
        audio.init()
        audio.apply_settings(self.settings)

        from .ui import widgets as ui_widgets

        ui_widgets.set_hover_sound(lambda: audio.play_sfx("hover", 0.5))

        # 网络与对局
        self.host: GameHost | None = None
        self.client: GameClient | None = None
        self.local_engine: GameEngine | None = None
        self.broadcaster: DiscoveryBroadcaster | None = None
        self.local_specs: list[dict[str, Any]] = []
        self._in_game = False
        self._last_local_specs: list[dict[str, Any]] = []
        #: 断线重连调度器（仅客户端）
        self.reconnector: AutoReconnector | None = None
        self._reconnect_dialog: Any = None
        self._client_token = ""
        self._client_address = ("", 0)
        #: 重要的瞬时状态提示（重连成功等）：居中淡入淡出，不打断操作
        self.status_overlay: dict[str, Any] | None = None
        #: 上一次连接尝试的结果（诊断页与加入页都会读它）
        self.last_connection_result: dict[str, Any] = {}

        # 场景
        self.scenes = SceneManager(self)
        self._register_scenes()

    # ================================================================ 初始化

    def _init_display(self) -> None:
        flags = pygame.RESIZABLE
        size = (LOGICAL_WIDTH, LOGICAL_HEIGHT)
        if self.settings.fullscreen:
            flags |= pygame.FULLSCREEN
            size = (0, 0)
        else:
            size = (self.settings.width, self.settings.height)
        try:
            self.screen = pygame.display.set_mode(size, flags)
        except pygame.error:
            self.screen = pygame.display.set_mode((LOGICAL_WIDTH, LOGICAL_HEIGHT), pygame.RESIZABLE)
        self.viewport.update(self.screen.get_size())

    def _register_scenes(self) -> None:
        self.scenes.register("menu", MenuScene)
        self.scenes.register("local_setup", LocalSetupScene)
        self.scenes.register("lan_setup", LanSetupScene)
        self.scenes.register("save_browser", SaveBrowserScene)
        self.scenes.register("help", HelpScene)
        self.scenes.register("settings", SettingsScene)
        self.scenes.register("lobby", LobbyScene)
        self.scenes.register("game", GameScene)
        self.scenes.register("net_diag", NetDiagScene)
        self.scenes.switch_to("menu")

    # ================================================================ 设置

    def apply_settings(self) -> None:
        audio.apply_settings(self.settings)
        self.apply_font_scale()
        self.apply_animation_speed()
        self.apply_display()

    def apply_display(self) -> None:
        self._init_display()
        self.scenes.resize()

    def apply_font_scale(self) -> None:
        self.fonts.set_scale(self.settings.font_scale)

    def apply_animation_speed(self) -> None:
        """把「动画速度」与「AI 演出速度」同步到所有正在运行的引擎。

        两个设置都**只影响演出**：动画速度改阶段时长，AI 演出速度改
        「等 AI 发呆」的时间，规则结果完全一致。
        """
        speed = self.settings.animation_speed
        ai_speed = self.settings.ai_speed
        if self.host is not None and self.host.engine is not None:
            self.host.engine.set_anim_speed(speed)
            self.host.engine.set_ai_think_scale(ai_speed)
            self.host.anim_speed = speed
        if self.local_engine is not None:
            self.local_engine.set_anim_speed(speed)
            self.local_engine.set_ai_think_scale(ai_speed)
        scene = self.scenes.current
        if isinstance(scene, GameScene):
            scene.anim.set_speed(speed)

    # ================================================================ 教程

    def start_tutorial(self) -> None:
        """新手教程：用**真实引擎**跑一局短局，额外叠一层教练面板。

        这里不创建任何「教程专用规则」——玩家看到的就是真正的对局，
        只是旁边多了一个小面板告诉他现在该做什么。
        """
        self._teardown_network()
        name = self.settings.nickname or "玩家"
        specs = [
            {"id": "p1", "name": name, "character_id": self.settings.character_id,
             "color_id": "red", "is_ai": False, "is_host": True},
            {"id": "p2", "name": "陪练电脑", "character_id": "char_xiaoman",
             "color_id": "blue", "is_ai": True},
        ]
        engine = create_engine(
            specs, anim_speed=self.settings.animation_speed,
            map_file="default_map.json", preset="quick",
        )
        self.local_engine = engine
        self._last_local_specs = [dict(s) for s in specs]
        engine.bind_controller("p1", LocalController("p1"))
        engine.start()
        overlay = TutorialOverlay()
        self._enter_game(SessionView(self, engine=engine, my_player_id="p1",
                                     tutorial=overlay))
        self.toast("新手教程开始 · 跟着右下角的提示走", "success")

    # ================================================================ 对局入口

    def start_local_game(self, specs: list[dict[str, Any]]) -> None:
        """开始一局单机游戏。specs 中 is_ai=False 的座位由本机多人轮流操作。"""
        self._teardown_network()
        opts = getattr(self, "_last_local_options", None) or {}
        engine = create_engine(
            specs, anim_speed=self.settings.animation_speed,
            map_file=opts.get("map_file") or self.settings.get(
                "game", "map_file", "default_map.json"),
            preset=opts.get("preset") or self.settings.get(
                "game", "preset", "standard"),
        )
        self.local_engine = engine
        self._last_local_specs = [dict(s) for s in specs]
        self._last_local_options = {
            "map_file": self.settings.get("game", "map_file", "default_map.json"),
            "preset": self.settings.get("game", "preset", "standard"),
        }

        host_local_ids = [s["id"] for s in specs if not s.get("is_ai")]
        for pid in host_local_ids:
            engine.bind_controller(pid, LocalController(pid))

        engine.start()
        self._enter_game(SessionView(self, engine=engine, my_player_id=specs[0]["id"]))
        self.toast("单机对局已开始", "success")

    def start_host(self, room_name: str, name: str, character: str, port: int) -> None:
        """创建局域网房间并进入大厅。"""
        self._teardown_network()
        try:
            self.host = GameHost(
                room_name=room_name,
                host_name=name,
                host_character=character,
                port=port,
                anim_speed=self.settings.animation_speed,
                map_file=self.settings.get("game", "map_file",
                                           "default_map.json"),
                preset=self.settings.get("game", "preset", "standard"),
            )
            self.host.start()
        except HostError as exc:
            self.host = None
            self.push_modal(_message("无法创建房间", str(exc), "error"))
            return
        except Exception as exc:
            self.host = None
            log.exception("创建房间失败")
            self.push_modal(_message("无法创建房间", f"{exc}", "error"))
            return

        self._start_broadcaster()
        self.scenes.switch_to("lobby")
        lobby = self.scenes.current
        if isinstance(lobby, LobbyScene):
            lobby.mode = "host"
        ips = self.host.ip_addresses()
        self.toast(f"房间已创建 · 端口 {self.host.port} · IP {ips[0] if ips else '?'}",
                   "success")

    def _start_broadcaster(self) -> None:
        if self.host is None:
            return
        port = 28081
        self.broadcaster = DiscoveryBroadcaster(
            port, lambda: {
                "room_name": self.host.lobby.room_name,
                **self.host.lobby.to_discovery_dict(),
                "port": self.host.port,
            })
        self.broadcaster.start()

    def join_host(self, host_ip: str, port: int, name: str, character: str) -> None:
        """加入局域网房间。"""
        self._teardown_network()
        client = GameClient()
        try:
            client.connect(host_ip, port, name, character)
        except ClientError as exc:
            self.client = None
            self.push_modal(_message("无法连接主机", str(exc), "error"))
            return
        except Exception as exc:
            self.client = None
            log.exception("连接异常")
            self.push_modal(_message("无法连接主机", f"{exc}", "error"))
            return

        self.client = client
        self._client_address = (host_ip, int(port))
        self.scenes.switch_to("lobby")
        lobby = self.scenes.current
        if isinstance(lobby, LobbyScene):
            lobby.mode = "client"
        self.toast(f"正在连接 {host_ip}:{port} …", "info")

    def load_saved_game(self, path: str) -> None:
        """读取存档并继续（单机）。"""
        self._teardown_network()
        try:
            engine = savegame.load_engine(path,
                                          anim_speed=self.settings.animation_speed)
        except Exception as exc:
            self.push_modal(_message("读取存档失败", str(exc), "error"))
            return
        self.local_engine = engine
        for p in engine.state.players:
            if not p.is_ai:
                engine.bind_controller(p.id, LocalController(p.id))
        # 保证 AI 有控制器（存档里可能包含被 AI 接管的玩家）
        from .controllers.ai import AIController

        for p in engine.state.players:
            if p.is_ai or p.bot_controlled:
                engine.bind_controller(p.id, AIController(p.id))
        my_id = next((p.id for p in engine.state.players if not p.is_ai), "")
        if not my_id and engine.state.players:
            my_id = engine.state.players[0].id
            engine.bind_controller(my_id, LocalController(my_id))
        self._last_local_specs = []
        self._enter_game(SessionView(self, engine=engine, my_player_id=my_id))
        self.toast("已读取存档，继续对局", "success")

    def restart_current_game(self) -> None:
        """再来一局。"""
        if self.host is not None:
            self.host.return_to_lobby()
            self._in_game = False
            self.scenes.switch_to("lobby")
            lobby = self.scenes.current
            if isinstance(lobby, LobbyScene):
                lobby.mode = "host"
            ok, reason = self.host.try_start_game()
            if not ok:
                self.toast(reason, "warning")
            return
        if self._last_local_specs:
            self.start_local_game(self._last_local_specs)
            return
        self.return_to_lobby()

    def return_to_lobby(self) -> None:
        """从对局返回（大厅或主菜单）。"""
        self._in_game = False
        if self.host is not None:
            self.host.return_to_lobby()
            self.scenes.switch_to("lobby")
            lobby = self.scenes.current
            if isinstance(lobby, LobbyScene):
                lobby.mode = "host"
            return
        if self.client is not None:
            self.scenes.switch_to("lobby")
            lobby = self.scenes.current
            if isinstance(lobby, LobbyScene):
                lobby.mode = "client"
            return
        self.local_engine = None
        self.scenes.switch_to("menu")

    def leave_room(self) -> None:
        """离开房间 / 结束对局回到主菜单。"""
        self._teardown_network()
        self.local_engine = None
        self._in_game = False
        self.scenes.switch_to("menu")

    def _teardown_network(self) -> None:
        self.reconnector = None
        self._reconnect_dialog = None
        if self.broadcaster is not None:
            self.broadcaster.stop()
            self.broadcaster = None
        if self.host is not None:
            self.host.stop()
            self.host = None
        if self.client is not None:
            self.client.close()
            self.client = None
        self.local_engine = None
        self._in_game = False

    def _enter_game(self, session: SessionView) -> None:
        # 第一次进对局：给一个 6 步的轻量引导（高亮真实控件，可跳过、可永久关闭）
        if (session.guide is None and session.tutorial is None
                and self.settings.show_guide and not self.settings.guide_done):
            session.guide = self._make_guide()
        scene = self.scenes.switch_to("game")
        if isinstance(scene, GameScene):
            scene.bind(session)
        self._in_game = True

    def _make_guide(self) -> Any:
        from .ui.guide import GuideOverlay

        def _done(skip_all: bool) -> None:
            self.settings.set("ui", "guide_done", True)
            if skip_all:
                self.settings.set("ui", "show_guide", False)
                self.toast("已关闭新手引导（设置里可以重新打开）", "info")
            else:
                self.toast("引导结束，祝你好运！", "success")
            self.settings.save()

        return GuideOverlay(on_finish=_done)

    # ================================================================ 主循环

    def run(self) -> int:
        log.info("进入主循环")
        while self.running:
            dt = self.clock.tick(60) / 1000.0
            dt = min(dt, MAX_DT)
            try:
                self._process_events()
                self.update(dt)
                self._render()
            except Exception:
                log.exception("主循环异常")
                self.session_error = traceback.format_exc()
                self.toast("发生内部错误，已记录到 logs/", "error")
        self._shutdown()
        return 0

    def _process_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit()
                return
            if event.type == pygame.VIDEORESIZE:
                if not self.settings.fullscreen:
                    self.settings.set_resolution(event.w, event.h)
                    self.viewport.update(self.screen.get_size())
                    self.scenes.resize()
                continue
            translated = translate_event(event, self.viewport)
            self._handle_global_key(translated)
            self.scenes.handle_event(translated)

    def _handle_global_key(self, event: pygame.event.Event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        mods = pygame.key.get_mods()
        if event.key == pygame.K_F11:
            self.settings.set_fullscreen(not self.settings.fullscreen)
            self.apply_display()
        elif event.key == pygame.K_F2:
            muted = audio.toggle_mute()
            self.settings.set("audio", "muted", muted)
            self.toast("已静音" if muted else "已取消静音", "info")
        elif event.key == pygame.K_F3:
            speeds = [0.5, 1.0, 1.5, 2.0]
            current = self.settings.animation_speed
            idx = (speeds.index(current) + 1) % len(speeds) if current in speeds else 0
            self.settings.set("ui", "animation_speed", speeds[idx])
            self.apply_animation_speed()
            self.toast(f"动画速度 {speeds[idx]}x", "info")

    def update(self, dt: float) -> None:
        # 网络与引擎
        if self.host is not None:
            self.host.update(dt)
            for notice in self.host.drain_notices():
                self.toast(notice, "info")
            for error in self.host.drain_errors():
                self.toast(error, "warning")
        if self.client is not None:
            self.client.update(dt)
            self._update_client_connection()
        # 注意：单机引擎由 GameScene（通过 SessionView）驱动，
        # 这里不能再推进一次，否则游戏会以两倍速前进、阶段计时也会错乱。
        # LAN 模式下 Host/Client 的驱动在上面，GameScene 不会重复推进。

        self._check_game_start()
        self.scenes.update(dt)
        self.toasts.update(dt)

    # ================================================================ 断线重连

    def _update_client_connection(self) -> None:
        """客户端连接状态机：掉线 → 自动重连 → 成功 / 超时。"""
        client = self.client
        if client is None:
            return
        if client.connected:
            if self.reconnector is not None:
                attempts = self.reconnector.attempts
                self._close_reconnect_dialog()
                detail = (f"第 {attempts} 次尝试成功" if attempts else "连接已恢复")
                self.show_status("已重新连接到房间", detail, "success", 3.0)
                self.toast("已重新连接，操作已恢复", "success")
            return

        if not client.disconnected_reason and self.reconnector is None:
            return

        # 第一次发现掉线：能重连就重连，否则走原来的「提示 + 回主菜单」
        if self.reconnector is None:
            if self._can_reconnect(client):
                self._begin_reconnect(client)
            else:
                self._handle_fatal_disconnect(client, client.disconnected_reason)
            return

        status = self.reconnector.tick(client)
        self._sync_reconnect_dialog()
        if status == STATUS_EXHAUSTED:
            reason = (f"重连超时（已尝试 {self.reconnector.attempts} 次）。"
                      "房主那边可能已经关闭房间，或网络仍然不通。")
            self._close_reconnect_dialog()
            self._handle_fatal_disconnect(client, reason)

    def _can_reconnect(self, client: GameClient) -> bool:
        """只有「在大厅/对局中掉线」且拿得到 token 时才自动重连。"""
        if not client.reconnect_token:
            return False
        if client.kicked:
            return False
        host, port = self._client_address
        if not host or not port:
            try:
                host, port_text = client.host_address.rsplit(":", 1)
                port = int(port_text)
            except (ValueError, AttributeError):
                return False
        return True

    def _begin_reconnect(self, client: GameClient) -> None:
        host, port = self._client_address
        self.reconnector = AutoReconnector(host, port, client.reconnect_token,
                                           grace_sec=CLIENT_RECONNECT_WINDOW)
        reason = client.disconnected_reason or "与房主的连接已中断"
        client.disconnected_reason = ""
        attempts = max(1, int(CLIENT_RECONNECT_WINDOW
                              / max(0.5, self.reconnector.interval)))
        dialog = _reconnect_dialog(reason, CLIENT_RECONNECT_WINDOW,
                                   on_give_up=self._abandon_reconnect,
                                   on_retry=self._retry_now,
                                   max_attempts=attempts,
                                   takeover_after=RECONNECT_GRACE_SEC)
        self._reconnect_dialog = dialog
        self.push_modal(dialog)
        self.toast("连接中断，正在尝试自动重连…", "warning")

    def _retry_now(self) -> None:
        """【立即重试】：不等下一轮，马上再试一次。"""
        rec = self.reconnector
        client = self.client
        if rec is None or client is None:
            return
        rec._next_attempt = 0.0        # 只提前节奏，不改状态机语义
        self.toast("正在立即重试…", "info")

    def _sync_reconnect_dialog(self) -> None:
        dialog = self._reconnect_dialog
        rec = self.reconnector
        if dialog is None or rec is None:
            return
        dialog.sync(rec.status_text(), rec.attempts, rec.remaining, rec.progress,
                    rec.hint_text())

    def _close_reconnect_dialog(self) -> None:
        dialog = self._reconnect_dialog
        self._reconnect_dialog = None
        self.reconnector = None
        scene = self.scenes.current
        if dialog is not None and scene is not None and getattr(scene, "modal", None) is dialog:
            scene.modal = None

    def _abandon_reconnect(self) -> None:
        """玩家主动放弃重连：断开并回主菜单。"""
        self._close_reconnect_dialog()
        if self.client is not None:
            self.client.close(notify=False)
        self._teardown_network()
        self.scenes.switch_to("menu")
        self.toast("已放弃重连", "info")

    def _handle_fatal_disconnect(self, client: GameClient, reason: str) -> None:
        if client.connected:
            return
        client.disconnected_reason = ""
        self.toast(reason, "error")
        self.push_modal(_message("连接已断开", reason, "error",
                                 on_close=self._after_disconnect))

    def _after_disconnect(self) -> None:
        if self.client is not None:
            self.client.close(notify=False)
        self._teardown_network()
        self.scenes.switch_to("menu")

    def _check_game_start(self) -> None:
        """大厅 → 对局的自动切换。"""
        if self._in_game:
            return
        scene = self.scenes.current
        if not isinstance(scene, LobbyScene):
            return

        if self.host is not None and self.host.engine is not None:
            session = SessionView(self, engine=self.host.engine,
                                  my_player_id=self.host.host_player_id, host=self.host)
            self._enter_game(session)
            return
        if self.client is not None and self.client.state is not None:
            session = SessionView(self, client=self.client,
                                  my_player_id=self.client.player_id)
            self._enter_game(session)

    def _render(self) -> None:
        self.scenes.draw(self.virtual)
        self._draw_status_overlay(self.virtual)
        self.toasts.draw(self.virtual, self.fonts, center_x=800, bottom_y=872)
        if self.session_error:
            pass
        self.viewport.blit(self.virtual, self.screen)
        pygame.display.flip()

    def _draw_status_overlay(self, surface: Any) -> None:
        """居中状态提示（重连成功等）：淡入 → 停留 → 淡出。"""
        overlay = self.status_overlay
        if not overlay:
            return
        now = time.time()
        total = float(overlay.get("seconds", 2.6))
        elapsed = now - float(overlay.get("at", now))
        if elapsed >= total:
            self.status_overlay = None
            return
        from .ui import icons as ui_icons

        appear = min(1.0, elapsed / 0.22)
        fade = min(1.0, max(0.0, (total - elapsed) / 0.45))
        alpha = int(255 * min(appear, fade))
        if alpha <= 4:
            return
        fonts = self.fonts
        font = fonts.h2()
        sub_font = fonts.small()
        text = str(overlay.get("text", ""))
        sub = str(overlay.get("sub", ""))
        color_name = str(overlay.get("color", "success"))
        w = max(font.size(text)[0], sub_font.size(sub)[0] if sub else 0) + 96
        h = 74 if sub else 56
        rect = pygame.Rect(0, 0, int(w), h)
        rect.center = (800, 300 - int((1.0 - appear) * 18))
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        theme.rounded_rect(layer, layer.get_rect(), theme.color("panel_alt", 244), radius=14)
        theme.rounded_rect(layer, layer.get_rect(), None, radius=14,
                           border=theme.color(color_name), border_width=2)
        ui_icons.draw_icon(layer, "check" if color_name == "success" else "network",
                           pygame.Rect(18, 16, 26, 26), theme.color(color_name),
                           theme.color("shadow"))
        theme.draw_text(layer, text, font, theme.color("text"),
                        (56, 12 if sub else (rect.height - font.get_height()) // 2))
        if sub:
            theme.draw_text(layer, sub, sub_font, theme.color("text_dim"), (58, 40))
        layer.set_alpha(alpha)
        surface.blit(layer, rect.topleft)

    def show_status(self, text: str, sub: str = "", color: str = "success",
                    seconds: float = 2.6) -> None:
        self.status_overlay = {"text": text, "sub": sub, "color": color,
                               "seconds": seconds, "at": time.time()}

    def _shutdown(self) -> None:
        try:
            self.settings.save()
        except Exception:
            pass
        self._teardown_network()
        audio.stop_bgm()
        pygame.quit()
        log.info("已退出")

    # ================================================================ 工具

    def quit(self) -> None:
        self.running = False

    def toast(self, text: str, kind: str = "info") -> None:
        self.toasts.push(text, kind)

    def push_modal(self, modal: Any) -> None:
        scene = self.scenes.current
        if scene is not None and hasattr(scene, "modal"):
            scene.modal = modal

    def net_status(self) -> str:
        if self.host is not None:
            return f"房主 {self.host.port} · {self.host.lobby.player_count} 人"
        if self.client is not None:
            return f"客户端 {self.client.host_address} · rev {self.client.revision}"
        if self.local_engine is not None:
            return "单机"
        return "未联机"


def _message(title: str, message: str, accent: str = "info", on_close=None):
    from .ui.dialogs import MessageDialog

    return MessageDialog(title, message, accent=accent, on_close=on_close)


def _reconnect_dialog(reason: str, grace: float, on_give_up=None,
                      on_retry=None, max_attempts: int = 10,
                      takeover_after: float = 30.0):
    from .ui.dialogs import ReconnectDialog

    return ReconnectDialog(reason, grace_sec=grace, on_give_up=on_give_up,
                           on_retry=on_retry, max_attempts=max_attempts,
                           takeover_after=takeover_after)
