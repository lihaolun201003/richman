"""房间大厅：单机 / 局域网房主 / 局域网客户端共用。

- 单机模式：只有房主 + AI，没有网络；
- 房主模式：可添加/移除 AI、踢人、开始游戏；
- 客户端模式：可改角色、准备/取消准备、离开。

v0.3 的变化：
- 房间信息升级为「大字 IP + 一键复制」，房主不用手抄；
- 踢人从前是「点卡片就踢」（危险且容易误触），现在需要点明确的按钮；
- 角色选择复用统一的角色网格与能力卡；
- 客户端会明确看到「等待房主开始」的状态与自己的准备进度。

大厅只读网络与引擎状态，任何修改都通过 Host 的消息接口发出。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.setup import character_by_id, palette
from ..network.transport import local_ip_addresses
from ..utils.clipboard import copy_text
from . import icons, theme
from .character_cards import CharacterGallery, draw_avatar, draw_character_card
from .player_panel import _color_of
from .scene import Scene
from .setup_scenes import MapPresetSelector
from .widgets import Button, draw_tooltip

PAGE_X = 100


class LobbyScene(Scene):
    """房间大厅。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.mode = "local"          # local / host / client
        self.character_id = app.settings.character_id
        self.color_id = app.settings.color_id
        self.hover_player: str | None = None
        self.status = ""
        self.gallery = CharacterGallery(pygame.Rect(PAGE_X, 470, 660, 168),
                                        self.character_id, self._set_character,
                                        columns=4, card_h=76)
        self.selector = MapPresetSelector(pygame.Rect(800, 500, 700, 84), None)
        self._build()

    def _build(self) -> None:
        self.widgets = [
            Button(pygame.Rect(PAGE_X, 820, 220, 54), "离开房间",
                   on_click=self._leave, style="ghost", icon="exit"),
            Button(pygame.Rect(1240, 820, 260, 54), "开始游戏",
                   on_click=self._start, style="accent", icon="play"),
        ]
        self.add_ai_button = Button(
            pygame.Rect(800, 432, 340, 50), "电脑玩家", on_click=self._add_ai,
            style="secondary", font_size=16, icon="plus")
        self.ready_button = Button(
            pygame.Rect(800, 432, 340, 50), "我准备好了（R）", on_click=self._toggle_ready,
            style="primary", font_size=16, icon="check")
        self.copy_button = Button(
            pygame.Rect(1152, 432, 348, 50), "复制连接信息", on_click=self._copy_ip,
            style="ghost", font_size=16, icon="copy")
        self.kick_button = Button(
            pygame.Rect(800, 600, 700, 46), "把选中的玩家移出房间",
            on_click=self._kick_selected, style="danger", font_size=16, icon="cross")
        self.widgets += [self.add_ai_button, self.ready_button, self.copy_button]

    # ------------------------------------------------------------ 生命周期

    def on_enter(self, **kwargs: Any) -> None:
        self.character_id = self.app.settings.character_id
        self.color_id = self.app.settings.color_id
        self.gallery.selected = self.character_id

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
        self.notify("已准备，等待房主开始" if ready else "已取消准备", "info")

    def _add_ai(self) -> None:
        host = self._host()
        if host is None:
            return
        ok, err = host.lobby.add_ai()
        if err:
            self.notify({"room_full": "房间已满", "game_started": "游戏已经开始了"}.get(err, err),
                        "warning")
        host.broadcast_lobby()

    def _copy_ip(self) -> None:
        host = self._host()
        if host is None:
            self.notify("只有房主需要分享连接信息", "info")
            return
        ips = host.ip_addresses()
        text = f"{ips[0]}:{host.port}" if ips else str(host.port)
        if copy_text(text):
            self.notify(f"已复制「{text}」，发给朋友即可", "success")
        else:
            self.notify(f"复制失败，请手动记下：{text}", "warning")

    def _kick_selected(self) -> None:
        host = self._host()
        if host is None or self.hover_player is None:
            self.notify("先点一个玩家卡片，再点这个按钮", "info")
            return
        session = host.lobby.session(self.hover_player)
        if session is None or session.is_host or session.is_ai:
            self.notify("房主与电脑玩家不能在这里移除", "warning")
            return
        if session.conn is not None:
            session.conn.send_message("KICK", {"reason": "你被房主移出房间"})
            session.conn.close("kicked")
        host.lobby.remove(self.hover_player)
        host.broadcast_lobby()
        self.notify(f"{session.name} 已被移出房间", "warning")

    def _remove_selected_ai(self) -> None:
        host = self._host()
        if host is None or self.hover_player is None:
            return
        session = host.lobby.session(self.hover_player)
        if session is not None and session.is_ai:
            host.lobby.remove(self.hover_player)
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
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pid = self._hit_card(event.pos)
            if pid is not None:
                session = None
                host = self._host()
                if host is not None:
                    session = host.lobby.session(pid)
                if session is not None and session.is_ai and self._is_room_host():
                    self._remove_selected_ai()
                    return
                self.hover_player = pid
                return
        if self.gallery.handle_event(event):
            return
        if self.selector.handle_event(event):
            return
        super().handle_event(event)

    def _card_rects(self) -> list[tuple[pygame.Rect, dict[str, Any]]]:
        players = self._players()
        out = []
        cols = 2
        for i, p in enumerate(players):
            col, row = i % cols, i // cols
            rect = pygame.Rect(PAGE_X + col * 336, 176 + row * 84, 320, 80)
            out.append((rect, p))
        return out

    def _hit_card(self, pos) -> str | None:
        for rect, p in self._card_rects():
            if rect.collidepoint(pos):
                return p["player_id"]
        return None

    def _sync_selector(self, data: dict) -> None:
        """把选择器与房间状态对齐；客户端只读。"""
        sel = self.selector
        host = self._is_room_host()
        sel.editable = host
        sel.on_change = self._on_map_or_preset if host else None
        sel.set_map(data.get('map_file', 'default_map.json'))
        sel.set_preset(data.get('preset', 'standard'))

    def _on_map_or_preset(self, map_info: dict, preset: dict) -> None:
        host = self._host()
        if host is None:
            return
        host.lobby.set_map(map_info.get('file', 'default_map.json'))
        host.lobby.set_preset(preset.get('key', 'standard'))
        host.broadcast_lobby()

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
        theme.draw_text(surface, theme.truncate(room_name, self.fonts.h1(), 700),
                        self.fonts.h1(), theme.color("text"), (PAGE_X, 56))
        theme.chip(surface, self.fonts, pygame.Rect(PAGE_X, 104, 150, 24), mode_label,
                   "accent" if self.mode != "client" else "info", font_key="tiny", radius=6)

        self._sync_selector(data)
        self._draw_room_info(surface, data)
        self._draw_seats(surface)

        theme.draw_text(surface, "选择你的角色", self.fonts.small(), theme.color("text_dim"),
                        (PAGE_X, 444))
        tips: list[str] = []
        self.gallery.draw(surface, self.fonts, tips)
        draw_character_card(surface, self.fonts, pygame.Rect(PAGE_X, 652, 660, 150),
                            self.character_id, show_ai=False)

        self.selector.draw(surface, self.fonts)
        self._update_buttons(data)
        self.draw_widgets(surface)
        self._draw_hint(surface, data)
        mouse = pygame.mouse.get_pos()
        if tips and self.app.settings.show_tooltips:
            draw_tooltip(surface, self.fonts, tips[-1], mouse, bounds=(1600, 900))

    def _update_buttons(self, data: dict) -> None:
        is_host = self._is_room_host()
        self.add_ai_button.visible = is_host
        self.copy_button.visible = bool(self._host())
        self.ready_button.visible = not is_host
        if not is_host:
            client = self._client()
            me = next((p for p in self._players()
                       if client and p["player_id"] == client.player_id), None)
            ready = bool((me or {}).get("ready"))
            self.ready_button.label = "取消准备（R）" if ready else "我准备好了（R）"
            self.ready_button.style = "success" if ready else "primary"
        button = self.widgets[1]
        if is_host:
            button.visible = True
            can = bool(data.get("can_start"))
            button.set_enabled(can, data.get("start_reason", ""))
            button.label = "开始游戏" if can else "等待准备…"
        else:
            button.visible = False

    def _draw_room_info(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        rect = pygame.Rect(800, 176, 700, 246)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "房间信息", icon="info")

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
                rows.append(("规则", host.lobby.preset_label()))
        else:
            client = self._client()
            if client is not None:
                rows.append(("房主", client.host_address))
                rows.append(("状态", client.describe()))
                rows.append(("地图", data.get("map_name", "")))
                rows.append(("规则", data.get("preset_name", "")))
        rows.append(("人数", f"{len(data.get('players') or [])} / {data.get('max_players', 6)}"))

        y = rect.y + 52
        for label, value in rows:
            theme.kv_row(surface, self.fonts, pygame.Rect(rect.x + 16, y, rect.width - 32, 20),
                         y, label, value, value_color="accent" if label == "本机 IP" else "text",
                         row_h=28)
            y += 28

        if self._is_host_mode() and self._host() is not None:
            theme.draw_text(surface, "把「IP:端口」发给朋友，他们在「加入房间」里填进去",
                            self.fonts.micro(), theme.color("accent"),
                            (rect.x + 16, rect.bottom - 30))

    def _draw_seats(self, surface: pygame.Surface) -> None:
        players = self._players()
        my_id = self._my_id()
        is_host = self._is_room_host()
        mouse = pygame.mouse.get_pos()
        for rect, p in self._card_rects():
            char = character_by_id(p.get("character_id", "")) or {}
            col = theme.hex_to_rgb(_color_of(p.get("color_id", "")))
            is_me = p["player_id"] == my_id
            is_ai = bool(p.get("is_ai"))
            selected = p["player_id"] == self.hover_player
            fill = "panel_alt" if selected else "panel"
            theme.panel(surface, rect, fill=fill,
                        border="accent" if selected else "border_soft",
                        radius=theme.RADIUS["lg"])

            avatar = pygame.Rect(rect.x + 16, rect.centery - 22, 44, 44)
            draw_avatar(surface, avatar, char, self.fonts)
            pygame.draw.circle(surface, col, (rect.x + 20, rect.y + 16), 6)
            pygame.draw.circle(surface, theme.darken(col, 0.4), (rect.x + 20, rect.y + 16), 6, 1)

            tag = "房主" if p.get("is_host") else ("电脑" if is_ai else "玩家")
            theme.draw_text(surface, theme.truncate(p.get("name", "?"), self.fonts.h3(), 160),
                            self.fonts.h3(), theme.color("text"),
                            (avatar.right + 12, rect.y + 14))
            theme.draw_text(surface, f"{tag} · {char.get('name_cn', '')}", self.fonts.small(),
                            theme.color("text_dim"), (avatar.right + 12, rect.y + 42))
            if char.get("perk"):
                theme.draw_text(surface, theme.truncate(char["perk"]["desc"], self.fonts.micro(),
                                                        rect.width - 90),
                                self.fonts.micro(), theme.color("text_mute"),
                                (avatar.right + 12, rect.y + 60))

            ready = p.get("ready")
            if is_ai:
                label, color_name = "电脑", "text_mute"
            elif ready:
                label, color_name = "已准备", "success"
            else:
                label, color_name = "未准备", "warning"
            theme.chip(surface, self.fonts,
                       pygame.Rect(rect.right - 76, rect.bottom - 28, 64, 20),
                       label, color_name, font_key="micro")
            if p.get("disconnected"):
                theme.chip(surface, self.fonts,
                           pygame.Rect(rect.right - 76, rect.bottom - 50, 64, 20),
                           "已掉线", "danger", font_key="micro")
            if is_me:
                theme.draw_text(surface, "（你）", self.fonts.tiny(), theme.color("primary"),
                                (rect.right - 16, rect.y + 12), anchor="topright")
            if selected and is_host and not is_ai:
                theme.draw_text(surface, "已选中", self.fonts.micro(), theme.color("accent"),
                                (rect.x + 16, rect.bottom - 22))

    def _draw_hint(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        if self._is_room_host():
            reason = data.get("start_reason", "")
            text = ("按 Enter 或点右下角「开始游戏」" if data.get("can_start")
                    else f"还不能开始：{reason}")
        else:
            players = self._players()
            client = self._client()
            me = next((p for p in players if client and p["player_id"] == client.player_id),
                      None)
            text = ("已准备，等待房主开始游戏…" if (me or {}).get("ready")
                    else "选好角色后按 R 准备，房主才能开始")
        theme.draw_text(surface, text, self.fonts.small(), theme.color("text_dim"),
                        (800, 892), anchor="center")
