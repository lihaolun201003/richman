"""房间大厅：单机 / 局域网房主 / 局域网客户端共用。

- 单机模式：只有房主 + AI，没有网络；
- 房主模式：可添加/移除 AI、踢人、开始游戏；
- 客户端模式：可改角色、准备/取消准备、离开。

v0.4 的重点是**把「现在什么状态」讲清楚**：

- 每张玩家卡都能一眼看见：头像 / 昵称 / 角色 / 房主身份 / 准备状态 / 连接状态
  （未准备 · 已准备 · 房主 · 电脑 · 掉线 N 秒 · AI 接管中）；
- 房主不需要准备，卡片上直接写「房主」而不是「已准备」；
- 开始按钮不再只是灰掉，而是写明**在等谁**（例如「等待 2 名玩家准备」）；
- 房间信息里给出连接地址大字 + 游戏版本，室友能不能连、版本是否一致一眼可见；
- 多网卡时列出「推荐地址 + 备选地址」，虚拟网卡折叠成一行说明。

大厅只读网络与引擎状态，任何修改都通过 Host 的消息接口发出。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.setup import character_by_id, palette
from ..network import netinfo
from ..utils.clipboard import copy_text
from ..version import APP_VERSION, short_version
from . import icons, theme
from .character_cards import CharacterGallery, draw_avatar, draw_character_card
from .player_panel import _color_of
from .scene import Scene
from .setup_scenes import MapPresetSelector, connection_share_text
from .widgets import Button, draw_tooltip

PAGE_X = 100
INFO_X = 800
INFO_W = 700


def presence_color(state: str) -> str:
    """状态码 → 颜色名（与 session.presence_state() 对应）。"""
    return {
        "ready": "success",
        "host": "accent",
        "ai": "text_mute",
        "disconnected": "danger",
        "ai_takeover": "warning",
        "idle": "text_dim",
        "reconnecting": "warning",
    }.get(state, "text_dim")


class LobbyScene(Scene):
    """房间大厅。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.mode = "local"          # local / host / client
        self.character_id = app.settings.character_id
        self.color_id = app.settings.color_id
        self.hover_player: str | None = None
        self.status = ""
        self._adapters: list[Any] = []
        self._adapters_at = 0.0
        self.gallery = CharacterGallery(pygame.Rect(PAGE_X, 470, 660, 168),
                                        self.character_id, self._set_character,
                                        columns=4, card_h=76)
        self.selector = MapPresetSelector(pygame.Rect(INFO_X, 572, INFO_W, 84), None)
        self.room_panel = pygame.Rect(INFO_X, 176, INFO_W, 240)
        self.addr_panel = pygame.Rect(INFO_X, 428, INFO_W, 132)
        self._build()

    def _build(self) -> None:
        self.widgets = [
            Button(pygame.Rect(PAGE_X, 820, 220, 54), "离开房间",
                   on_click=self._leave, style="ghost", icon="exit"),
            Button(pygame.Rect(1240, 820, 260, 54), "开始游戏",
                   on_click=self._start, style="accent", icon="play"),
        ]
        self.add_ai_button = Button(
            pygame.Rect(INFO_X, 672, 340, 50), "电脑玩家", on_click=self._add_ai,
            style="secondary", font_size=16, icon="plus",
            tooltip="加一个电脑对手（房主可随时增减）")
        self.ready_button = Button(
            pygame.Rect(INFO_X, 672, 340, 50), "我准备好了（R）", on_click=self._toggle_ready,
            style="primary", font_size=16, icon="check",
            tooltip="准备好之后，房主才能开始游戏")
        self.copy_button = Button(
            pygame.Rect(INFO_X + INFO_W - 196, 428 + 74, 180, 44), "复制连接信息",
            on_click=self._copy_ip, style="ghost", font_size=15, icon="copy",
            tooltip="复制成一段带说明的文字，直接粘到群里")
        self.kick_button = Button(
            pygame.Rect(INFO_X + 356, 672, 344, 50), "移出房间",
            on_click=self._kick_selected, style="danger", font_size=15, icon="cross",
            tooltip="先点一个玩家卡片选中，再点这里把他移出房间")
        self.widgets += [self.add_ai_button, self.ready_button, self.copy_button,
                         self.kick_button]

    # ------------------------------------------------------------ 生命周期

    def on_enter(self, **kwargs: Any) -> None:
        self.character_id = self.app.settings.character_id
        self.color_id = self.app.settings.color_id
        self.gallery.selected = self.character_id

    def update(self, dt: float) -> None:
        super().update(dt)
        # 地址枚举 1.5 秒一次足够，不需要每帧调系统 API
        import time as _time

        if self._is_host_mode() and _time.time() - self._adapters_at > 1.5:
            self._adapters_at = _time.time()
            self._adapters = netinfo.lan_endpoints()

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
        ip = netinfo.best_ip()
        text = connection_share_text(host.lobby.room_name, ip, host.port)
        if copy_text(text):
            self.notify(f"已复制连接信息（{ip}:{host.port}），粘给室友即可", "success")
        else:
            self.notify(f"复制失败，请手动记下：{ip}:{host.port}", "warning")

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
        self.hover_player = None
        host.broadcast_lobby()
        self.notify(f"{session.name} 已被移出房间", "warning")

    def _remove_selected_ai(self) -> None:
        host = self._host()
        if host is None or self.hover_player is None:
            return
        session = host.lobby.session(self.hover_player)
        if session is not None and session.is_ai:
            host.lobby.remove(self.hover_player)
            self.hover_player = None
            host.broadcast_lobby()

    def _select_player(self, pid: str) -> None:
        """点卡片 = 选中；点「电脑」卡片 = 直接移除（房主操作）。"""
        session = None
        host = self._host()
        if host is not None:
            session = host.lobby.session(pid)
        if session is not None and session.is_ai:
            if self._is_room_host():
                self.hover_player = pid
                self._remove_selected_ai()
            return
        self.hover_player = pid

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
                self._select_player(pid)
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
            rect = pygame.Rect(PAGE_X + col * 344, 176 + row * 92, 328, 84)
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
        self._draw_connect_address(surface, data)
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
        self.kick_button.visible = bool(self._host())
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
            blockers = list(data.get("start_blockers") or [])
            button.set_enabled(can, blockers[0] if blockers else "")
            button.label = self._start_button_label(can, blockers)
        else:
            button.visible = False

    @staticmethod
    def _start_button_label(can_start: bool, blockers: list[str]) -> str:
        """开始按钮上的字：能开始就写「开始游戏」，不能就把原因写出来。"""
        if can_start:
            return "开始游戏"
        text = blockers[0] if blockers else "还不能开始"
        if text.startswith("等待 ") and "准备" in text:
            count = text.split("等待 ")[1].split(" ")[0]
            return f"等待 {count} 名玩家准备"
        if text.startswith("还需要"):
            return text.replace("还需要 ", "还差 ").split("（")[0]
        return "还不能开始"

    def _draw_room_info(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        rect = self.room_panel
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 18, rect.y + 12, rect.width - 36, 22),
                             "房间信息", icon="info",
                             note=APP_VERSION)

        rows: list[tuple[str, str]] = []
        if self._is_host_mode():
            host = self._host()
            if host is not None:
                rows.append(("房间名称", host.lobby.room_name))
                rows.append(("房主", host.lobby.host_name() or "你"))
                rows.append(("游戏版本", short_version()))
                rows.append(("地图 / 规则", f"{host.lobby.map_name} · "
                                            f"{host.lobby.preset_label()}"))
                rows.append(("玩家人数", f"{len(data.get('players') or [])} / "
                                         f"{data.get('max_players', 6)}"))
        else:
            client = self._client()
            if client is not None:
                rows.append(("房间名称", data.get("room_name", "")))
                rows.append(("房主", data.get("host_name") or client.host_address))
                rows.append(("游戏版本", data.get("app_version", APP_VERSION)))
                rows.append(("地图 / 规则", f"{data.get('map_name', '')} · "
                                            f"{data.get('preset_name', '')}"))
                rows.append(("玩家人数", f"{len(data.get('players') or [])} / "
                                         f"{data.get('max_players', 6)}"))
                rows.append(("连接状态", client.describe()))

        y = rect.y + 52
        for label, value in rows:
            theme.kv_row(surface, self.fonts, pygame.Rect(rect.x + 18, y, rect.width - 36, 20),
                         y, label, value, value_color="text", row_h=26)
            y += 26

        blockers = list(data.get("start_blockers") or [])
        if blockers:
            theme.draw_text(surface, theme.truncate("还不能开始：" + blockers[0],
                                                    self.fonts.tiny(), rect.width - 40),
                            self.fonts.tiny(), theme.color("warning"),
                            (rect.x + 18, rect.bottom - 24))

    def _draw_connect_address(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        """连接地址：房主把这一行发给室友，客户端看自己连的是谁。"""
        rect = self.addr_panel
        theme.panel(surface, rect, fill="panel_alt", radius=theme.RADIUS["xl"])
        host = self._host()

        if host is not None:
            physical = [a for a in (self._adapters or netinfo.lan_endpoints())
                        if not a.is_virtual]
            ip = physical[0].ip if physical else netinfo.best_ip()
            theme.draw_text(surface, "把下面这一行发给室友（同一个 WiFi 下就能加入）",
                            self.fonts.tiny(), theme.color("text_dim"),
                            (rect.x + 18, rect.y + 14))
            big = self.fonts.sized(theme.FONT["h1"], True)
            addr = f"{ip}:{host.port}"
            theme.draw_text(surface, addr, big, theme.color("accent"),
                            (rect.x + 18, rect.y + 34))
            if physical and physical[0].name:
                theme.draw_text(surface, physical[0].name, self.fonts.tiny(),
                                theme.color("text_mute"),
                                (rect.x + 24 + big.size(addr)[0], rect.y + 54))
            extra = physical[1:3]
            detail = ""
            if extra:
                detail = "其它可用地址：" + "、".join(a.ip for a in extra)
            elif len(physical) <= 1:
                detail = "检测到 1 个网络地址"
            if detail:
                theme.draw_text(surface, theme.truncate(detail, self.fonts.tiny(),
                                                        rect.width - 240),
                                self.fonts.tiny(), theme.color("text_mute"),
                                (rect.x + 18, rect.bottom - 24))
            if len(physical) > 1:
                theme.draw_text(surface, "第一个连不上时，把备选地址也发给他",
                                self.fonts.tiny(), theme.color("warning"),
                                (rect.x + 18, rect.bottom - 24))
        else:
            client = self._client()
            theme.draw_text(surface, "你正在连接的主机", self.fonts.tiny(),
                            theme.color("text_dim"), (rect.x + 18, rect.y + 14))
            big = self.fonts.sized(theme.FONT["h1"], True)
            theme.draw_text(surface, client.host_address if client else "—", big,
                            theme.color("accent"), (rect.x + 18, rect.y + 34))
            if client is not None:
                ping = (f"延迟约 {client.latency_ms:.0f} ms"
                        if client.latency_ms > 1 else "延迟正常")
                theme.draw_text(surface, f"{client.describe()}　·　{ping}",
                                self.fonts.tiny(), theme.color("text_mute"),
                                (rect.x + 18, rect.bottom - 24))

    def _draw_seats(self, surface: pygame.Surface) -> None:
        """玩家卡：头像 / 昵称 / 角色 / 身份 / 准备 / 连接状态，一屏全看得见。"""
        players = self._players()
        my_id = self._my_id()
        is_host = self._is_room_host()
        for rect, p in self._card_rects():
            char = character_by_id(p.get("character_id", "")) or {}
            col = theme.hex_to_rgb(_color_of(p.get("color_id", "")))
            is_me = p["player_id"] == my_id
            is_ai = bool(p.get("is_ai"))
            selected = p["player_id"] == self.hover_player
            presence = p.get("presence", "ai" if is_ai else "idle")
            fill = "panel_alt" if selected else "panel"
            border = "accent" if selected else (
                "danger" if presence == "disconnected" else "border_soft")
            theme.panel(surface, rect, fill=fill, border=border,
                        radius=theme.RADIUS["lg"])

            avatar = pygame.Rect(rect.x + 14, rect.centery - 22, 44, 44)
            draw_avatar(surface, avatar, char, self.fonts)
            pygame.draw.circle(surface, col, (rect.x + 18, rect.y + 14), 6)
            pygame.draw.circle(surface, theme.darken(col, 0.4), (rect.x + 18, rect.y + 14),
                               6, 1)

            theme.draw_text(surface, theme.truncate(p.get("name", "?"), self.fonts.h3(), 150),
                            self.fonts.h3(), theme.color("text"),
                            (avatar.right + 12, rect.y + 12))
            role = "房主" if p.get("is_host") else ("电脑" if is_ai else "玩家")
            theme.draw_text(surface, f"{role} · {char.get('name_cn', '')}",
                            self.fonts.small(), theme.color("text_dim"),
                            (avatar.right + 12, rect.y + 38))
            if char.get("perk"):
                theme.draw_text(surface, theme.truncate(char["perk"]["desc"],
                                                        self.fonts.micro(),
                                                        rect.width - 110),
                                self.fonts.micro(), theme.color("text_mute"),
                                (avatar.right + 12, rect.y + 58))

            label = p.get("presence_label") or ("电脑" if is_ai else "未准备")
            if presence == "disconnected":
                label = f"掉线 {int(p.get('disconnected_seconds', 0))} 秒"
            theme.chip(surface, self.fonts,
                       pygame.Rect(rect.right - 100, rect.bottom - 28, 88, 20),
                       label, presence_color(presence), font_key="micro")

            if is_me:
                theme.draw_text(surface, "（你）", self.fonts.tiny(), theme.color("primary"),
                                (rect.right - 16, rect.y + 10), anchor="topright")
            if selected and is_host and not is_ai:
                theme.draw_text(surface, "已选中，可点「移出房间」", self.fonts.micro(),
                                theme.color("accent"), (avatar.right + 12, rect.bottom - 22))

    def _draw_hint(self, surface: pygame.Surface, data: dict[str, Any]) -> None:
        if self._is_room_host():
            blockers = list(data.get("start_blockers") or [])
            if not blockers:
                text, color = "按 Enter 或点右下角「开始游戏」", "text_dim"
            else:
                # 只显示最靠前的一条原因：并列四五行会让玩家一眼看不完
                text = f"还不能开始：{blockers[0]}"
                if len(blockers) > 1:
                    text += f"（另有 {len(blockers) - 1} 项）"
                color = "warning"
        else:
            players = self._players()
            client = self._client()
            me = next((p for p in players if client and p["player_id"] == client.player_id),
                      None)
            if (me or {}).get("ready"):
                text, color = "已准备，等待房主开始游戏…", "success"
            else:
                text, color = "选好角色后按 R 准备，房主才能开始", "text_dim"
        theme.draw_text(surface, theme.truncate(text, self.fonts.small(), 1400),
                        self.fonts.small(), theme.color(color), (800, 872), anchor="center")
