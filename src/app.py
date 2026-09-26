"""应用主体：窗口、主循环、场景装配、网络会话管理。

职责边界：
- App 负责「把东西装起来」：字体、设置、音频、网络、场景；
- 场景负责「一屏之内的事」；
- 引擎负责规则，网络负责传输，UI 只发送 Command。
"""
from __future__ import annotations

import os
import sys
import traceback
from typing import Any

import pygame

from .audio.manager import audio
from .controllers.base import LocalController
from .game.engine import GameEngine
from .game.setup import create_engine
from .network.client import ClientError, GameClient
from .network.discovery import DiscoveryBroadcaster
from .network.host import GameHost, HostError
from .persistence import savegame
from .persistence.settings import Settings
from .ui import theme
from .ui.game_scene import GameScene, SessionView
from .ui.layout import LOGICAL_HEIGHT, LOGICAL_SIZE, LOGICAL_WIDTH, Viewport, translate_event
from .ui.lobby import LobbyScene
from .ui.menu import MenuScene
from .ui.scene import SceneManager
from .ui.settings_scene import SettingsScene
from .ui.setup_scenes import LanSetupScene, LocalSetupScene, SaveBrowserScene
from .ui.toast import ToastManager
from .utils.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

#: 每帧最大步长，避免窗口拖动后逻辑跳跃
MAX_DT = 0.1


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

        # 网络与对局
        self.host: GameHost | None = None
        self.client: GameClient | None = None
        self.local_engine: GameEngine | None = None
        self.broadcaster: DiscoveryBroadcaster | None = None
        self.local_specs: list[dict[str, Any]] = []
        self._in_game = False
        self._last_local_specs: list[dict[str, Any]] = []

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
        self.scenes.register("settings", SettingsScene)
        self.scenes.register("lobby", LobbyScene)
        self.scenes.register("game", GameScene)
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
        speed = self.settings.animation_speed
        if self.host is not None and self.host.engine is not None:
            self.host.engine.set_anim_speed(speed)
            self.host.anim_speed = speed
        if self.local_engine is not None:
            self.local_engine.set_anim_speed(speed)
        scene = self.scenes.current
        if isinstance(scene, GameScene):
            scene.anim.set_speed(speed)

    # ================================================================ 对局入口

    def start_local_game(self, specs: list[dict[str, Any]]) -> None:
        """开始一局单机游戏。specs 中 is_ai=False 的座位由本机多人轮流操作。"""
        self._teardown_network()
        engine = create_engine(specs, anim_speed=self.settings.animation_speed)
        self.local_engine = engine
        self._last_local_specs = [dict(s) for s in specs]

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
            self.push_modal(_message("连接失败", str(exc), "error"))
            return
        except Exception as exc:
            self.client = None
            log.exception("连接异常")
            self.push_modal(_message("连接失败", f"{exc}", "error"))
            return

        self.client = client
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
        scene = self.scenes.switch_to("game")
        if isinstance(scene, GameScene):
            scene.bind(session)
        self._in_game = True

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
            if not self.client.connected and self.client.disconnected_reason:
                reason = self.client.disconnected_reason
                self.client.disconnected_reason = ""
                self.toast(reason, "error")
                self.push_modal(_message("连接已断开", reason, "error",
                                         on_close=self._after_disconnect))
        if self.local_engine is not None:
            self.local_engine.update(dt)

        self._check_game_start()
        self.scenes.update(dt)
        self.toasts.update(dt)

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
        self.toasts.draw(self.virtual, self.fonts, center_x=800, bottom_y=872)
        if self.session_error:
            pass
        self.viewport.blit(self.virtual, self.screen)
        pygame.display.flip()

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
