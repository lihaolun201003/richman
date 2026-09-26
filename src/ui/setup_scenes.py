"""开局前的配置场景：单机配置、局域网建房/加入、存档浏览。"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.setup import character_by_id, load_characters, palette
from ..network.discovery import DiscoveryListener
from ..persistence import savegame
from . import theme
from .dialogs import MessageDialog
from .player_panel import _color_of
from .scene import Scene
from .widgets import Button, Label, Panel, SegmentedControl, TextInput


class CharacterPicker:
    """角色选择条：横向排列 8 个角色。"""

    def __init__(self, rect: pygame.Rect, selected: str, on_change) -> None:
        self.rect = pygame.Rect(rect)
        self.selected = selected
        self.on_change = on_change
        self.chars = load_characters()["characters"]
        self.hover: int | None = None

    def _item_rects(self) -> list[pygame.Rect]:
        n = len(self.chars)
        gap = 10
        w = (self.rect.width - gap * (n - 1)) // n
        out = []
        for i in range(n):
            out.append(pygame.Rect(self.rect.x + i * (w + gap), self.rect.y, w, self.rect.height))
        return out

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hover = None
            for i, r in enumerate(self._item_rects()):
                if r.collidepoint(event.pos):
                    self.hover = i
                    break
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, r in enumerate(self._item_rects()):
                if r.collidepoint(event.pos):
                    self.selected = self.chars[i]["id"]
                    self.on_change(self.selected)
                    return True
        return False

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        for i, (rect, char) in enumerate(zip(self._item_rects(), self.chars)):
            active = char["id"] == self.selected
            col = theme.hex_to_rgb(char["theme_color"])
            fill = theme.mix(theme.color("panel"), col, 0.34 if active else 0.10)
            theme.rounded_rect(surface, rect, fill, radius=10)
            theme.rounded_rect(surface, rect, None, radius=10,
                               border=col if active else theme.color("border_soft"),
                               border_width=3 if active else 1)
            avatar_r = 20
            center = (rect.centerx, rect.y + 30)
            pygame.draw.circle(surface, col, center, avatar_r)
            theme.draw_text(surface, char["name_cn"][0], fonts.sized(20, True), (255, 255, 255),
                            center, anchor="center")
            theme.draw_text(surface, char["name_cn"], fonts.small(), theme.color("text"),
                            (rect.centerx, rect.y + 58), anchor="midtop")
            theme.draw_wrapped(surface, char["perk"]["desc"], fonts.micro(),
                               theme.color("text_dim"),
                               pygame.Rect(rect.x + 6, rect.y + 78, rect.width - 12, 30))


def draw_character_summary(surface: pygame.Surface, fonts: theme.FontManager,
                           rect: pygame.Rect, char_id: str) -> None:
    char = character_by_id(char_id)
    if char is None:
        return
    col = theme.hex_to_rgb(char["theme_color"])
    theme.rounded_rect(surface, rect, theme.color("bg_alt"), radius=12)
    theme.rounded_rect(surface, rect, None, radius=12, border=col, border_width=2)
    pygame.draw.circle(surface, col, (rect.x + 36, rect.centery), 22)
    theme.draw_text(surface, char["name_cn"][0], fonts.sized(22, True), (255, 255, 255),
                    (rect.x + 36, rect.centery), anchor="center")
    theme.draw_text(surface, f"{char['name_cn']} · {char['title']}", fonts.h3(),
                    theme.color("text"), (rect.x + 70, rect.y + 14))
    theme.draw_text(surface, char["perk"]["desc"], fonts.small(), col,
                    (rect.x + 70, rect.y + 40))
    theme.draw_text(surface, char["bio"], fonts.micro(), theme.color("text_dim"),
                    (rect.x + 70, rect.y + 62))


class LocalSetupScene(Scene):
    """单机开局配置：人数、座位类型、角色。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.player_count = 4
        self.seats: list[dict[str, Any]] = []
        self.character_id = app.settings.character_id
        self._build()

    def _build(self) -> None:
        self.nick_input = TextInput(
            pygame.Rect(120, 168, 300, 44), self.app.settings.nickname,
            placeholder="输入你的昵称", max_length=12,
            on_change=lambda v: self.app.settings.set_nickname(v))
        self.count_select = SegmentedControl(
            pygame.Rect(120, 254, 460, 44),
            [(f"{n} 人", n) for n in range(2, 7)],
            value=self.player_count,
            on_change=self._set_count, label="参与人数（含你自己）")
        self.picker = CharacterPicker(pygame.Rect(120, 350, 900, 116),
                                      self.character_id, self._set_character)

        self.widgets = [
            self.nick_input,
            self.count_select,
            Button(pygame.Rect(120, 700, 240, 56), "开始游戏",
                   on_click=self._start, style="accent", icon="▶"),
            Button(pygame.Rect(380, 700, 200, 56), "返回",
                   on_click=lambda: self.app.scenes.switch_to("menu"), style="ghost"),
            Button(pygame.Rect(1120, 254, 120, 44), "+ AI", on_click=self._add_ai,
                   style="secondary"),
            Button(pygame.Rect(1256, 254, 120, 44), "- AI", on_click=self._remove_ai,
                   style="ghost"),
        ]
        self._reset_seats()

    def _reset_seats(self) -> None:
        chars = load_characters()["characters"]
        pal = palette()
        self.seats = [{
            "is_ai": False,
            "character_id": self.character_id,
            "color_id": pal[0]["id"],
        }]
        for i in range(1, self.player_count):
            self.seats.append({
                "is_ai": True,
                "character_id": chars[i % len(chars)]["id"],
                "color_id": pal[i % len(pal)]["id"],
            })

    def _set_count(self, value: int) -> None:
        self.player_count = int(value)
        self._reset_seats()

    def _set_character(self, char_id: str) -> None:
        self.character_id = char_id
        self.seats[0]["character_id"] = char_id
        self.app.settings.set_character(char_id)

    def _add_ai(self) -> None:
        if self.player_count >= 6:
            self.notify("最多 6 名参与者", "warning")
            return
        self.player_count += 1
        chars = load_characters()["characters"]
        pal = palette()
        self.seats.append({
            "is_ai": True,
            "character_id": chars[self.player_count % len(chars)]["id"],
            "color_id": pal[(self.player_count - 1) % len(pal)]["id"],
        })
        self.count_select.set_value(self.player_count)

    def _remove_ai(self) -> None:
        if self.player_count <= 2:
            self.notify("至少需要 2 名参与者", "warning")
            return
        self.player_count -= 1
        self.seats.pop()
        self.count_select.set_value(self.player_count)

    def _start(self) -> None:
        name = (self.nick_input.text or "玩家").strip()[:12]
        self.app.settings.set_nickname(name)
        specs = []
        for i, seat in enumerate(self.seats):
            specs.append({
                "id": f"p{i + 1}",
                "name": name if i == 0 else f"电脑{i}",
                "character_id": seat["character_id"],
                "color_id": seat["color_id"],
                "is_ai": seat["is_ai"],
                "is_host": i == 0,
            })
        self.app.start_local_game(specs)

    def on_enter(self, **kwargs: Any) -> None:
        self.nick_input.set_text(self.app.settings.nickname)
        self.picker.selected = self.app.settings.character_id
        self._set_character(self.picker.selected)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.switch_to("menu")
            return
        if self.picker.handle_event(event):
            return
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.draw_text(surface, "单人游戏", self.fonts.h1(), theme.color("text"), (120, 96))
        theme.draw_text(surface, "选择人数与角色，其余座位由 AI 接管",
                        self.fonts.small(), theme.color("text_dim"), (122, 132))
        pygame.draw.line(surface, theme.color("border_soft"), (120, 156), (1480, 156), 1)

        # 座位预览
        seat_panel = pygame.Rect(620, 160, 860, 200)
        theme.rounded_rect(surface, seat_panel, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, seat_panel, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, "座位预览", self.fonts.h3(), theme.color("text"),
                        (seat_panel.x + 16, seat_panel.y + 10))
        for i, seat in enumerate(self.seats[:6]):
            col, row = i % 3, i // 3
            card = pygame.Rect(seat_panel.x + 16 + col * 278, seat_panel.y + 46 + row * 74,
                               266, 66)
            self._draw_seat(surface, card, i, seat)

        self.picker.draw(surface, self.fonts)

        # 选中的角色详情
        draw_character_summary(surface, self.fonts, pygame.Rect(1060, 350, 420, 88),
                               self.character_id)

        # 玩法说明
        info = pygame.Rect(1060, 460, 420, 176)
        theme.rounded_rect(surface, info, theme.color("panel"), radius=14)
        theme.draw_text(surface, "本局规则", self.fonts.h3(), theme.color("text"),
                        (info.x + 16, info.y + 12))
        lines = [
            "初始资金 15,000",
            "经过起点 +2,000",
            "地产可升级 3 级，整区垄断租金翻倍",
            "踩到他人地产需付租，不够则变卖资产",
            "破产即退出，最后存活者获胜",
        ]
        y = info.y + 46
        for line in lines:
            theme.draw_text(surface, "· " + line, self.fonts.tiny(), theme.color("text_dim"),
                            (info.x + 16, y))
            y += 26

        self.draw_widgets(surface)

    def _draw_seat(self, surface: pygame.Surface, rect: pygame.Rect, index: int,
                   seat: dict[str, Any]) -> None:
        is_me = index == 0
        col = theme.hex_to_rgb(_color_of(seat["color_id"]))
        theme.rounded_rect(surface, rect, theme.color("bg_alt"), radius=10)
        theme.rounded_rect(surface, rect, None, radius=10, border=col, border_width=2)
        pygame.draw.circle(surface, col, (rect.x + 26, rect.centery), 16)
        char = character_by_id(seat["character_id"]) or {}
        theme.draw_text(surface, (char.get("name_cn") or "?")[0], self.fonts.sized(16, True),
                        (255, 255, 255), (rect.x + 26, rect.centery), anchor="center")
        theme.draw_text(surface, "你" if is_me else f"电脑 {index}",
                        self.fonts.body(), theme.color("text"), (rect.x + 52, rect.y + 10))
        theme.draw_text(surface, f"{char.get('name_cn', '')} · {char.get('perk', {}).get('desc', '')}",
                        self.fonts.micro(), theme.color("text_dim"), (rect.x + 52, rect.y + 34))


class LanSetupScene(Scene):
    """局域网建房间 / 加入房间。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.mode = "host"
        self.listener: DiscoveryListener | None = None
        self.rooms: list[Any] = []
        self.selected_room = 0
        self.character_id = app.settings.character_id
        self.picker = CharacterPicker(pygame.Rect(120, 470, 900, 116),
                                      self.character_id, self._set_character)
        self._build()

    def _build(self) -> None:
        s = self.app.settings
        self.name_input = TextInput(pygame.Rect(120, 180, 320, 44), s.nickname,
                                    placeholder="你的昵称", max_length=12)
        self.host_input = TextInput(pygame.Rect(120, 300, 320, 44), s.last_host,
                                    placeholder="房主 IP，例如 192.168.1.5", max_length=40)
        self.port_input = TextInput(pygame.Rect(460, 300, 140, 44), str(s.last_port),
                                    placeholder="端口", max_length=5, numeric=True)
        self.room_input = TextInput(pygame.Rect(460, 180, 300, 44),
                                    s.last_room_name or f"{s.nickname} 的房间",
                                    placeholder="房间名称", max_length=16)
        self.port_host_input = TextInput(pygame.Rect(780, 180, 140, 44), "28080",
                                         placeholder="端口", max_length=5, numeric=True)

        self.widgets = [
            Button(pygame.Rect(120, 780, 260, 56), "返回",
                   on_click=self._back, style="ghost"),
            Button(pygame.Rect(400, 780, 300, 56), "连接",
                   on_click=self._submit, style="accent"),
        ]

    def _set_character(self, char_id: str) -> None:
        self.character_id = char_id

    def _back(self) -> None:
        self._stop_listener()
        self.app.scenes.switch_to("menu")

    def on_enter(self, **kwargs: Any) -> None:
        self.mode = kwargs.get("mode", "host")
        self.character_id = self.app.settings.character_id
        self.picker.selected = self.character_id
        if self.mode == "join":
            self._start_listener()
        else:
            self._stop_listener()

    def on_exit(self) -> None:
        self._stop_listener()

    def _start_listener(self) -> None:
        if self.listener is None:
            self.listener = DiscoveryListener(28081)
            self.listener.start()

    def _stop_listener(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def _submit(self) -> None:
        name = (self.name_input.text or "玩家").strip()[:12] or "玩家"
        self.app.settings.set_nickname(name)
        char = self.character_id or self.app.settings.character_id
        if self.mode == "host":
            room = (self.room_input.text or "").strip()[:16] or f"{name} 的房间"
            try:
                port = int(self.port_host_input.text or "28080")
            except ValueError:
                port = 28080
            self.app.settings.set("network", "last_room_name", room)
            self.app.start_host(room, name, char, port)
        else:
            host = (self.host_input.text or "").strip()
            if not host:
                self.app.push_modal(MessageDialog("缺少 IP 地址",
                                                  "请输入房主的局域网 IP 地址。\n"
                                                  "房主在大厅里可以看到自己的 IP。",
                                                  accent="warning"))
                return
            try:
                port = int(self.port_input.text or "28080")
            except ValueError:
                port = 28080
            self.app.settings.remember_server(host, port)
            self.app.join_host(host, port, name, char)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._back()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._submit()
                return
        if self.picker.handle_event(event):
            return
        if self.mode == "join" and self._handle_room_list(event):
            return
        super().handle_event(event)

    def _handle_room_list(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, _ in self._room_row_rects():
                if rect.collidepoint(event.pos):
                    return True
        return False

    def _room_row_rects(self):
        base = pygame.Rect(960, 220, 500, 40)
        out = []
        for i in range(6):
            out.append((pygame.Rect(base.x, base.y + i * 46, base.width, 40), i))
        return out

    def update(self, dt: float) -> None:
        super().update(dt)
        if self.listener is not None:
            self.rooms = self.listener.snapshot()

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        title = "创建局域网房间" if self.mode == "host" else "加入局域网房间"
        theme.draw_text(surface, title, self.fonts.h1(), theme.color("text"), (120, 96))
        theme.draw_text(surface,
                        "同一局域网内无需联网即可游戏；房主建好后把 IP 告诉其他人"
                        if self.mode == "host" else
                        "输入房主的局域网 IP，或从右侧自动发现的房间中选择",
                        self.fonts.small(), theme.color("text_dim"), (122, 132))
        pygame.draw.line(surface, theme.color("border_soft"), (120, 156), (1480, 156), 1)

        # 表单标签
        if self.mode == "host":
            self._label(surface, "房间名称", (120, 156 + 6))
            self._label(surface, "端口（默认 28080）", (780, 156 + 6))
        else:
            self._label(surface, "昵称", (120, 156 + 6))
            self._label(surface, "房主 IP", (120, 276))
            self._label(surface, "端口", (460, 276))

        self.picker.draw(surface, self.fonts)
        draw_character_summary(surface, self.fonts, pygame.Rect(1060, 470, 420, 88),
                               self.character_id)

        if self.mode == "join":
            self._draw_room_list(surface)
        else:
            self._draw_host_hint(surface)

        self.draw_widgets(surface)

    def _label(self, surface: pygame.Surface, text: str, pos: tuple[int, int]) -> None:
        theme.draw_text(surface, text, self.fonts.small(), theme.color("text_dim"), pos)

    def _draw_host_hint(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(960, 220, 500, 210)
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, "怎么让朋友加入？", self.fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 12))
        lines = [
            "1. 点击下方「创建房间」",
            "2. 在大厅里看到本机的局域网 IP",
            "3. 让朋友在同网段的电脑上打开游戏",
            "4. 选择「加入房间」，填入那个 IP 与端口",
            "",
            "提示：Windows 防火墙可能拦截，",
            "首次启动时请允许访问专用网络。",
        ]
        y = rect.y + 46
        for line in lines:
            theme.draw_text(surface, line, self.fonts.tiny(), theme.color("text_dim"),
                            (rect.x + 16, y))
            y += 22

    def _draw_room_list(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(960, 190, 500, 400)
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, "局域网内发现的房间", self.fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 12))
        if not self.rooms:
            theme.draw_text(surface, "正在搜索…（也可直接手动输入 IP）",
                            self.fonts.small(), theme.color("text_mute"),
                            (rect.centerx, rect.y + 140), anchor="center")
        for (row_rect, i), room in zip(self._room_row_rects(), self.rooms[:6]):
            active = room.joinable
            fill = theme.color("bg_alt") if active else theme.color("bg")
            theme.rounded_rect(surface, row_rect, fill, radius=8)
            theme.rounded_rect(surface, row_rect, None, radius=8,
                               border=theme.color("success" if active else "border_soft"),
                               border_width=1)
            theme.draw_text(surface, theme.truncate(room.room_name, self.fonts.body(),
                                                    row_rect.width - 180),
                            self.fonts.body(), theme.color("text"),
                            (row_rect.x + 12, row_rect.centery), anchor="midleft")
            theme.draw_text(surface, f"{room.ip}:{room.port}  {room.players}/{room.max_players}",
                            self.fonts.tiny(), theme.color("text_dim"),
                            (row_rect.right - 12, row_rect.centery), anchor="midright")
            if active and row_rect.collidepoint(pygame.mouse.get_pos()):
                self.host_input.set_text(room.ip)
                self.port_input.set_text(str(room.port))


class SaveBrowserScene(Scene):
    """存档选择。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.slots: list[dict[str, Any]] = []
        self.widgets = [
            Button(pygame.Rect(120, 800, 240, 54), "返回",
                   on_click=lambda: self.app.scenes.switch_to("menu"), style="ghost"),
        ]

    def on_enter(self, **kwargs: Any) -> None:
        self.slots = savegame.list_slots()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.switch_to("menu")
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self._row_rects()):
                if rect.collidepoint(event.pos) and i < len(self.slots):
                    self._load(self.slots[i])
                    return
        super().handle_event(event)

    def _row_rects(self) -> list[pygame.Rect]:
        out = []
        for i in range(8):
            out.append(pygame.Rect(120, 200 + i * 72, 1360, 62))
        return out

    def _load(self, slot: dict[str, Any]) -> None:
        if not slot.get("ok"):
            self.notify(f"存档损坏：{slot.get('error', '')}", "error")
            return
        self.app.load_saved_game(slot["path"])

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.draw_text(surface, "读取存档", self.fonts.h1(), theme.color("text"), (120, 96))
        theme.draw_text(surface, "点击任意存档继续游戏", self.fonts.small(),
                        theme.color("text_dim"), (122, 132))
        pygame.draw.line(surface, theme.color("border_soft"), (120, 156), (1480, 156), 1)

        if not self.slots:
            theme.draw_text(surface, "还没有存档", self.fonts.h2(), theme.color("text_mute"),
                            (800, 400), anchor="center")
        for rect, slot in zip(self._row_rects(), self.slots):
            hovered = rect.collidepoint(pygame.mouse.get_pos())
            theme.rounded_rect(surface, rect,
                               theme.color("panel_alt") if hovered else theme.color("panel"),
                               radius=12)
            theme.rounded_rect(surface, rect, None, radius=12,
                               border=theme.color("accent" if hovered else "border_soft"),
                               border_width=2 if hovered else 1)
            name = slot.get("path", "").split("\\")[-1].split("/")[-1]
            theme.draw_text(surface, name, self.fonts.h3(), theme.color("text"),
                            (rect.x + 16, rect.centery), anchor="midleft")
            detail = (f"第 {slot.get('round', 0)} 轮 · "
                      f"{slot.get('player_count', 0)} 人 · {slot.get('map', '')} · "
                      f"{slot.get('saved_at_text', '')}")
            theme.draw_text(surface, detail, self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 16, rect.centery + 20))
            players = "、".join(slot.get("players", [])[:6])
            theme.draw_text(surface, players, self.fonts.small(), theme.color("text_mute"),
                            (rect.right - 16, rect.centery), anchor="midright")
            if not slot.get("ok"):
                theme.draw_text(surface, "读取失败", self.fonts.small(), theme.color("danger"),
                                (rect.right - 16, rect.centery + 20), anchor="midright")

        self.draw_widgets(surface)
