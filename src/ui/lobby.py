"""房间大厅：单机 / 局域网房主 / 局域网客户端共用。

- 单机模式：只有房主 + AI，没有网络；
- 房主模式：可添加/移除 AI、踢人、开始游戏；
- 客户端模式：可改角色、准备/取消准备、离开。

大厅只读网络与引擎状态，任何修改都通过 Host 的消息接口发出。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.setup import character_by_id, load_characters, palette
from ..network.transport import local_ip_addresses
from . import theme
from .dialogs import MessageDialog
from .player_panel import _color_of
from .scene import Scene
from .setup_scenes import CharacterPicker
from .widgets import Button, Label, Panel


class LobbyScene(Scene):
    """房间大厅。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.mode = "local"          # local / host / client
        self.character_id = app.settings.character_id
        self.color_id = app.settings.color_id
        self.picker: CharacterPicker | None = None
        self.hover_player: str | None = None
        self._build()

    def _build(self) -> None:
        self.widgets = [
            Button(pygame.Rect(120, 800, 220, 54), "离开房间",
                   on_click=self._leave, style="ghost"),
            Button(pygame.Rect(1220, 800, 260, 54), "开始游戏",
                   on_click=self._start, style="accent", icon="▶"),
        ]

    # ------------------------------------------------------------ 生命周期

    def on_enter(self, **kwargs: Any) -> None:
        self.character_id = self.app.settings.character_id
        self.color_id = self.app.settings.color_id
        if self.picker is None:
            self.picker = CharacterPicker(pygame.Rect(120, 620, 900, 116),
                                          self.character_id, self._set_character)

    def on_exit(self) -> None:
        pass

    # ------------------------------------------------------------ 数据

    def _host(self):
        return self.app.host

    def _client(self):
        return self.app.client

    def _is_host_mode(self) -> bool:
        return self.mode in ("local", "host")

    def _lobby_data(self) -> dict[str, Any]:
        if self._is_host_mode():
            host = self._host()
            if host is not None:
                return host.lobby.to_dict()
        client = self._client()
        if client is not None:
            return client.lobby
        return {}

    def _players(self) -> list[dict[str, Any]]:
        return list(self._lobby_data().get("players") or [])

    def _my_id(self) -> str:
        if self._is_host_mode():
            host = self._host()
            return host.host_player_id if host else ""
        client = self._client()
        return client.player_id if client else ""

    def _is_room_host(self) -> bool:
        if self._is_host_mode():
            return True
        client = self._client()
        return bool(client and client.is_host)

    # ------------------------------------------------------------ 操作

    def _set_character(self, char_id: str) -> None:
        self.character_id = char_id
        self.app.settings.set_character(char_id)
        if self._is_host_mode():
            host = self._host()
            if host is not None:
                for s in host.lobby.sessions:
                    if s.player_id == host.host_player_id:
                        s.character_id = char_id
                host.broadcast_lobby()
        else:
            client = self._client()
            if client is not None:
                client.send_character(char_id)

    def _leave(self) -> None:
        self.app.leave_room()

    def _start(self) -> None:
        if not self._is_room_host():
            self.notify("只有房主可以开始游戏", "warning")
            return
        host = self._host()
        if host is None:
            self.notify("房间已失效", "error")
            return
        ok, reason = host.try_start_game()
        if not ok:
            self.notify(reason, "warning")

    def _toggle_ready(self) -> None:
        client = self._client()
        if client is None:
            return
        me = next((p for p in self._players() if p["player_id"] == client.player_id), None)
        ready = not (me or {}).get("ready", False)
        client.send_ready(ready)

    def _add_ai(self) -> None:
        host = self._host()
        if host is None:
            return
        ok, err = host.lobby.add_ai()
        if err:
            self.notify(
                {"room_full": "房间已满", "game_started": "游戏已经开始了"}.get(err, err),
                "warning")
        host.broadcast_lobby()

    def _remove_ai(self, player_id: str) -> None:
        host = self._host()
        if host is None:
            return
        session = host.lobby.session(player_id)
        if session is not None and session.is_ai:
            host.lobby.remove(player_id)
            host.broadcast_lobby()

    def _kick(self, player_id: str) -> None:
        host = self._host()
        if host is None:
            return
        session = host.lobby.session(player_id)
        if session is None or session.is_host or session.is_ai:
            return
        if session.conn is not None:
            session.conn.send_message("KICK", {"reason": "你被房主移出房间"})
            session.conn.close("kicked")
        host.lobby.remove(player_id)
        host.broadcast_lobby()

    # ------------------------------------------------------------ 输入

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._leave()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self._is_room_host():
                self._start()
                return
            if event.key == pygame.K_r and not self._is_room_host():
                self._toggle_ready()
                return
        if event.type == pygame.MOUSEMOTION:
            self.hover_player = self._hit_card(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pid = self._hit_card(event.pos)
            if pid is not None:
                self._on_card_clicked(pid)
                return
            for rect, action in self._button_rects():
                if rect.collidepoint(event.pos):
                    action()
                    return
        if self.picker is not None and self.picker.handle_event(event):
            return
        super().handle_event(event)

    def _on_card_clicked(self, player_id: str) -> None:
        if not self._is_room_host():
            return
        host = self._host()
        if host is None:
            return
        session = host.lobby.session(player_id)
        if session is None or session.is_host:
            return
        if session.is_ai:
            self._remove_ai(player_id)
        else:
            self._kick(player_id)

    def _card_rects(self) -> list[tuple[pygame.Rect, dict[str, Any]]]:
        players = self._players()
        out = []
        cols = 3
        for i, p in enumerate(players):
            col, row = i % cols, i // cols
            rect = pygame.Rect(120 + col * 300, 190 + row * 130, 280, 118)
            out.append((rect, p))
        return out

    def _hit_card(self, pos) -> str | None:
        for rect, p in self._card_rects():
            if rect.collidepoint(pos):
                return p["player_id"]
        return None

    def _button_rects(self):
        out = []
        if self._is_room_host():
            rect = pygame.Rect(1020, 190, 460, 54)
            out.append((rect, self._add_ai))
        else:
            rect = pygame.Rect(1020, 190, 460, 54)
            out.append((rect, self._toggle_ready))
        return out

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        data = self._lobby_data()
        room_name = data.get("room_name", "房间")
        mode_label = {
            "local": "单人 · 本地对局",
            "host": "局域网房主",
            "client": "局域网客户端",
        }.get(self.mode, "")
        theme.draw_text(surface, room_name, self.fonts.h1(), theme.color("text"), (120, 90))
        theme.draw_text(surface, mode_label, self.fonts.small(), theme.color("text_dim"),
                        (124, 128))
        pygame.draw.line(surface, theme.color("border_soft"), (120, 152), (1480, 152), 1)

        self._draw_room_info(surface, data)
        self._draw_seats(surface)
        if self.picker is not None:
            self.picker.draw(surface, self.fonts)
        theme.draw_text(surface, "选择你的角色", self.fonts.small(), theme.color("text_dim"),
                        (120, 596))

        self.widgets[0].label = "离开房间"
        self._update_start_button()
        self.draw_widgets(surface)
        self._draw_extra_buttons(surface)
        self._draw_hint(surface, data)

    def _update_start_button(self) -> None:
        button = self.widgets[1]
        if self._is_room_host():
            button.visible = True
            data = self._lobby_data()
            can = bool(data.get("can_start"))
            button.set_enabled(can, data.get("start_reason", ""))
            if not can:
                button.label = "等待准备…"
            else:
                button.label = "开始游戏"
        else:
            button.visible = False

    def _draw_room_info(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        rect = pygame.Rect(1020, 240, 460, 190)
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, "房间信息", self.fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 12))

        rows: list[tuple[str, str]] = []
        if self._is_host_mode():
            host = self._host()
            if host is not None:
                ips = host.ip_addresses()
                rows.append(("本机 IP", ips[0] if ips else "127.0.0.1"))
                if len(ips) > 1:
                    rows.append(("其它 IP", " / ".join(ips[1:3])))
                rows.append(("端口", str(host.port)))
                rows.append(("地图", host.lobby.map_name))
        else:
            client = self._client()
            if client is not None:
                rows.append(("房主", client.host_address))
                rows.append(("状态", client.describe()))
                rows.append(("地图", data.get("map_name", "")))

        rows.append(("人数", f"{len(data.get('players') or [])} / {data.get('max_players', 6)}"))

        y = rect.y + 48
        for label, value in rows:
            theme.draw_text(surface, label, self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 16, y))
            theme.draw_text(surface, theme.truncate(value, self.fonts.small(), rect.width - 130),
                            self.fonts.small(), theme.color("text"),
                            (rect.right - 16, y), anchor="topright")
            y += 26

    def _draw_seats(self, surface: pygame.Surface) -> None:
        players = self._players()
        my_id = self._my_id()
        is_host = self._is_room_host()
        for rect, p in self._card_rects():
            col = theme.hex_to_rgb(_color_of(p.get("color_id", "")))
            is_me = p["player_id"] == my_id
            hovered = p["player_id"] == self.hover_player and is_host and not is_me
            fill = theme.color("panel_alt") if hovered else theme.color("panel")
            theme.rounded_rect(surface, rect, fill, radius=12)
            theme.rounded_rect(surface, rect, None, radius=12,
                               border=theme.color("danger" if hovered else "border_soft"),
                               border_width=2 if hovered else 1)

            char = character_by_id(p.get("character_id", "")) or {}
            pygame.draw.circle(surface, col, (rect.x + 38, rect.y + 40), 24)
            theme.draw_text(surface, (char.get("name_cn") or p.get("name", "?"))[0],
                            self.fonts.sized(22, True), (255, 255, 255),
                            (rect.x + 38, rect.y + 40), anchor="center")

            tag = "房主" if p.get("is_host") else ("AI" if p.get("is_ai") else "玩家")
            name = p.get("name", "?")
            theme.draw_text(surface, theme.truncate(name, self.fonts.h3(), 150),
                            self.fonts.h3(), theme.color("text"), (rect.x + 76, rect.y + 16))
            theme.draw_text(surface, f"{tag} · {char.get('name_cn', '')}",
                            self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 76, rect.y + 44))
            if char.get("perk"):
                theme.draw_text(surface, char["perk"]["desc"], self.fonts.micro(),
                                theme.color("text_mute"), (rect.x + 76, rect.y + 66))

            # 准备状态
            ready = p.get("ready")
            badge = pygame.Rect(rect.right - 92, rect.bottom - 32, 78, 22)
            if p.get("is_ai"):
                label, color_name = "电脑", "text_mute"
            elif ready:
                label, color_name = "已准备", "success"
            else:
                label, color_name = "未准备", "warning"
            theme.rounded_rect(surface, badge, theme.color(color_name, 40), radius=6)
            theme.rounded_rect(surface, badge, None, radius=6,
                               border=theme.color(color_name), border_width=1)
            theme.draw_text(surface, label, self.fonts.micro(), theme.color(color_name),
                            badge.center, anchor="center")

            if p.get("disconnected"):
                theme.draw_text(surface, "已掉线", self.fonts.tiny(), theme.color("warning"),
                                (rect.x + 76, rect.y + 88))
            if is_me:
                theme.draw_text(surface, "（你）", self.fonts.tiny(), theme.color("primary"),
                                (rect.right - 18, rect.y + 14), anchor="topright")
            if hovered:
                theme.draw_text(surface, "点击移出", self.fonts.micro(), theme.color("danger"),
                                (rect.x + 10, rect.bottom - 20))

    def _draw_extra_buttons(self, surface: pygame.Surface) -> None:
        if self._is_room_host():
            rect = pygame.Rect(1020, 190, 460, 54)
            hovered = rect.collidepoint(pygame.mouse.get_pos())
            theme.rounded_rect(surface, rect,
                               theme.color("panel_hi") if hovered else theme.color("panel_alt"),
                               radius=12)
            theme.rounded_rect(surface, rect, None, radius=12, border=theme.color("border"),
                               border_width=1)
            theme.draw_text(surface, "+ 添加电脑玩家", self.fonts.body(),
                            theme.color("text"), rect.center, anchor="center")
        else:
            rect = pygame.Rect(1020, 190, 460, 54)
            players = self._players()
            client = self._client()
            me = next((p for p in players if client and p["player_id"] == client.player_id), None)
            ready = bool((me or {}).get("ready"))
            style_color = "success" if ready else "primary"
            theme.rounded_rect(surface, rect, theme.color(style_color), radius=12)
            theme.draw_text(surface,
                            "取消准备（R）" if ready else "我准备好了（R）",
                            self.fonts.body(), theme.color("text"), rect.center,
                            anchor="center")

    def _draw_hint(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        text = ""
        if self._is_room_host():
            reason = data.get("start_reason", "")
            text = "按 Enter 或点击右下角开始游戏" if data.get("can_start") else \
                f"还不能开始：{reason}"
        else:
            text = "等待房主开始游戏 · 可按 R 切换准备状态"
        theme.draw_text(surface, text, self.fonts.small(), theme.color("text_dim"),
                        (800, 862), anchor="center")
