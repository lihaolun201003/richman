"""开局前的配置场景：单机配置、局域网建房/加入、存档浏览。

v0.3 修正的真实问题：
- **局域网界面的输入框之前根本没注册进 `widgets`**，因此看不见、点不到、
  打不了字 —— 玩家在图形界面上无法输入房主 IP，联机主流程直接断掉。
  现在所有输入框都加入 `widgets`，并且画了标签、提示与格式校验；
- 点击「发现的房间」之前只会吞掉点击，现在会真的填入并直接连接；
- 地图 / 规则选择器与角色网格不再重叠（原来选择器压在角色卡上）；
- 角色选择升级为「网格 + 完整能力卡」，玩家能看懂每个角色擅长什么。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.setup import character_by_id, load_characters, palette
from ..network.discovery import DiscoveryListener
from ..persistence import savegame
from ..utils.clipboard import copy_text
from . import icons, theme
from .character_cards import CharacterGallery, draw_character_card
from .dialogs import MessageDialog
from .player_panel import _color_of
from .scene import Scene
from .widgets import (
    Button,
    IconButton,
    ScrollPanel,
    SegmentedControl,
    TextInput,
    draw_tooltip,
)


# ==================================================================== 地图 / 规则

class MapPresetSelector:
    """地图与规则预设的横向选择器（只允许房主改动）。"""

    def __init__(self, rect: pygame.Rect, on_change=None) -> None:
        self.rect = pygame.Rect(rect)
        self.on_change = on_change
        self.map_index = 0
        self.preset_index = 0
        self.maps: list[dict] = []
        self.presets: list[dict] = []
        self.editable = True
        self.reload()

    def reload(self) -> None:
        try:
            from ..game.setup import available_maps, preset_list

            self.maps = available_maps()
            self.presets = preset_list()
        except Exception:
            self.maps, self.presets = [], []

    # ---- 数据
    @property
    def current_map(self) -> dict:
        return self.maps[self.map_index % max(1, len(self.maps))] if self.maps else {}

    @property
    def current_preset(self) -> dict:
        return self.presets[self.preset_index % max(1, len(self.presets))] if self.presets else {}

    def set_map(self, map_file: str) -> None:
        for i, item in enumerate(self.maps):
            if item["file"] == map_file:
                self.map_index = i
                return

    def set_preset(self, preset: str) -> None:
        for i, item in enumerate(self.presets):
            if item["key"] == preset:
                self.preset_index = i
                return

    # ---- 交互
    def _rects(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect]:
        r = self.rect
        half = r.width // 2
        left = pygame.Rect(r.x, r.y, half - 8, r.height)
        right = pygame.Rect(r.x + half + 8, r.y, half - 8, r.height)
        size = 36
        return (pygame.Rect(left.right - size - 8, left.centery - size // 2, size, size),
                pygame.Rect(left.x + 8, left.centery - size // 2, size, size),
                pygame.Rect(right.right - size - 8, right.centery - size // 2, size, size),
                pygame.Rect(right.x + 8, right.centery - size // 2, size, size))

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.editable or event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        prev_m, next_m, prev_p, next_p = self._rects()
        pos = event.pos
        if prev_m.collidepoint(pos):
            self.map_index = (self.map_index - 1) % max(1, len(self.maps))
        elif next_m.collidepoint(pos):
            self.map_index = (self.map_index + 1) % max(1, len(self.maps))
        elif prev_p.collidepoint(pos):
            self.preset_index = (self.preset_index - 1) % max(1, len(self.presets))
        elif next_p.collidepoint(pos):
            self.preset_index = (self.preset_index + 1) % max(1, len(self.presets))
        else:
            return self.rect.collidepoint(pos)
        if self.on_change is not None:
            self.on_change(self.current_map, self.current_preset)
        return True

    def draw(self, surface: pygame.Surface, fonts) -> None:
        prev_m, next_m, prev_p, next_p = self._rects()
        half = self.rect.width // 2
        left = pygame.Rect(self.rect.x, self.rect.y, half - 8, self.rect.height)
        right = pygame.Rect(self.rect.x + half + 8, self.rect.y, half - 8, self.rect.height)

        for box, title, item, prev_r, next_r, icon_name in (
            (left, "地图", self.current_map, prev_m, next_m, "map"),
            (right, "规则", self.current_preset, prev_p, next_p, "rules"),
        ):
            theme.panel(surface, box, fill="bg_alt", radius=theme.RADIUS["lg"])
            theme.draw_text(surface, title, fonts.tiny(), theme.color("text_mute"),
                            (box.x + 12, box.y + 8))
            icons.draw_icon(surface, icon_name,
                            pygame.Rect(box.x + 12, box.centery - 9, 18, 18),
                            theme.color("accent"), theme.color("shadow"))
            name = item.get("name", "—")
            theme.draw_text(surface, name, fonts.h3(), theme.color("text"),
                            (box.centerx + 8, box.centery + 2), anchor="center")
            if item.get("starting_money"):
                sub = (f"起始 {item['starting_money'] / 10000:.1f} 万 · "
                       f"{item.get('max_rounds', 0)} 轮")
            else:
                sub = f"{item.get('tile_count', 0)} 格 · {item.get('property_count', 0)} 地产"
            theme.draw_text(surface, theme.truncate(sub, fonts.micro(), box.width - 84),
                            fonts.micro(), theme.color("text_dim"),
                            (box.centerx + 8, box.bottom - 20), anchor="center")

            if self.editable:
                for r, glyph in ((prev_r, "arrow_left"), (next_r, "arrow_right")):
                    hovered = r.collidepoint(pygame.mouse.get_pos())
                    theme.rounded_rect(surface, r,
                                       theme.color("panel_hi" if hovered else "panel"),
                                       radius=theme.RADIUS["sm"])
                    icons.draw_icon(surface, glyph, r.inflate(-12, -12),
                                    theme.color("accent" if hovered else "text_dim"),
                                    theme.color("shadow"))
            else:
                theme.chip(surface, fonts,
                           pygame.Rect(box.right - 80, box.y + 6, 68, 18),
                           "由房主选择", "text_mute")


# ==================================================================== 座位

def draw_seat(surface: pygame.Surface, fonts: theme.FontManager, rect: pygame.Rect,
              index: int, seat: dict[str, Any], *, is_me: bool, ai_label: str) -> None:
    from .character_cards import draw_avatar

    char = character_by_id(seat.get("character_id", "")) or {}
    col = theme.hex_to_rgb(_color_of(seat.get("color_id", "")))
    theme.panel(surface, rect, fill="bg_alt", radius=theme.RADIUS["lg"])
    theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, 5, rect.height), col, radius=3)
    avatar = pygame.Rect(rect.x + 14, rect.centery - 20, 40, 40)
    draw_avatar(surface, avatar, char, fonts)
    theme.draw_text(surface, "你" if is_me else ai_label, fonts.body(),
                    theme.color("text"), (avatar.right + 12, rect.y + 10))
    theme.draw_text(surface,
                    f"{char.get('name_cn', '?')} · {char.get('perk', {}).get('desc', '')}",
                    fonts.micro(), theme.color("text_dim"),
                    (avatar.right + 12, rect.y + 32))


class LocalSetupScene(Scene):
    """单机开局配置：人数、座位类型、角色、地图、规则。"""

    SUBTITLE = "选人数、角色、地图与规则，其余座位交给电脑"

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.player_count = 4
        self.seats: list[dict[str, Any]] = []
        self.character_id = app.settings.character_id
        self._layout_key: tuple = ()
        self.card_rect = pygame.Rect(140, 500, 660, 200)
        self.seat_panel = pygame.Rect(832, 344, 628, 200)
        self.rules_panel = pygame.Rect(832, 560, 628, 132)
        self._build()

    def _build(self) -> None:
        """按字号算出统一的内容区坐标，再用它排布所有控件。

        这样字体大小（设置里可调）变化时只需要重建一次布局，
        不会出现「标题画在 96、控件也放在 96」的叠字问题。
        """
        fonts = self.app.fonts
        key = (fonts.scale,)
        if key == self._layout_key:
            return
        self._layout_key = key
        y0 = theme.page_content_top(fonts, self.SUBTITLE)

        self.nick_input = TextInput(
            pygame.Rect(140, y0 + 22, 320, 46), self.app.settings.nickname,
            placeholder="输入你的昵称", max_length=12,
            on_change=lambda v: self.app.settings.set_nickname(v))
        self.count_select = SegmentedControl(
            pygame.Rect(500, y0 + 22, 520, 46),
            [(f"{n} 人", n) for n in range(2, 7)],
            value=self.player_count, on_change=self._set_count,
            label="参与人数（含你自己）")

        gallery_y = y0 + 92
        # 12 位角色一次看全：4 列 × 3 行
        self.gallery = CharacterGallery(pygame.Rect(140, gallery_y, 800, 240),
                                        self.character_id, self._set_character,
                                        columns=4, card_h=72)
        self.card_rect = pygame.Rect(140, gallery_y + 252, 800, 176)

        self.selector = MapPresetSelector(pygame.Rect(968, gallery_y, 492, 84),
                                          self._on_map_or_preset)
        self.selector.set_map(self.app.settings.get("game", "map_file", "default_map.json"))
        self.selector.set_preset(self.app.settings.get("game", "preset", "standard"))

        self.seat_panel = pygame.Rect(968, gallery_y + 100, 492, 212)
        self.rules_panel = pygame.Rect(968, gallery_y + 324, 492, 140)
        self.widgets = [
            self.nick_input,
            self.count_select,
            Button(pygame.Rect(140, 806, 260, 56), "开始游戏", on_click=self._start,
                   style="accent", icon="play"),
            Button(pygame.Rect(420, 806, 190, 56), "返回",
                   on_click=lambda: self.app.scenes.switch_to("menu"), style="ghost"),
            Button(pygame.Rect(630, 806, 190, 56), "电脑玩家",
                   on_click=self._add_ai, style="secondary", font_size=16, icon="plus"),
            Button(pygame.Rect(830, 806, 190, 56), "移除电脑",
                   on_click=self._remove_ai, style="ghost", font_size=16, icon="minus"),
            Button(pygame.Rect(1030, 806, 190, 56), "角色图鉴",
                   on_click=lambda: self.app.scenes.push("help", back="local_setup"),
                   style="ghost", font_size=16, icon="book"),
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

    def _on_map_or_preset(self, map_info: dict, preset: dict) -> None:
        self.app.settings.set("game", "map_file", map_info.get("file", "default_map.json"))
        self.app.settings.set("game", "preset", preset.get("key", "standard"))

    def _start(self) -> None:
        name = (self.nick_input.text or "玩家").strip()[:12] or "玩家"
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
        self._build()
        self.nick_input.set_text(self.app.settings.nickname)
        self.gallery.selected = self.app.settings.character_id
        self._set_character(self.gallery.selected)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.switch_to("menu")
            return
        if self.gallery.handle_event(event):
            return
        if self.selector.handle_event(event):
            return
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        self._build()
        content_y = theme.page_title(surface, self.fonts, "单人游戏", self.SUBTITLE)

        theme.draw_text(surface, "你的昵称", self.fonts.small(), theme.color("text_dim"),
                        (140, content_y + 4))
        theme.draw_text(surface, "选择角色（12 位，各有被动能力，鼠标悬停看详情）",
                        self.fonts.small(), theme.color("text_dim"), (140, content_y + 74))
        mouse = pygame.mouse.get_pos()
        tips: list[str] = []
        self.gallery.draw(surface, self.fonts, tips)

        self._draw_seat_panel(surface)
        self.selector.draw(surface, self.fonts)
        self._draw_rules_panel(surface)
        draw_character_card(surface, self.fonts, self.card_rect, self.character_id)

        self.draw_widgets(surface)
        if tips and self.app.settings.show_tooltips:
            draw_tooltip(surface, self.fonts, tips[-1], mouse, bounds=(1600, 900))

    def _draw_seat_panel(self, surface: pygame.Surface) -> None:
        panel = self.seat_panel
        theme.panel(surface, panel, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(panel.x + 16, panel.y + 10, panel.width - 32, 22),
                             "座位预览", icon="person")
        for i, seat in enumerate(self.seats[:6]):
            col, row = i % 2, i // 2
            card = pygame.Rect(panel.x + 14 + col * 240, panel.y + 44 + row * 54,
                               232, 50)
            draw_seat(surface, self.fonts, card, i, seat, is_me=(i == 0),
                      ai_label=f"电脑 {i}")

    def _draw_rules_panel(self, surface: pygame.Surface) -> None:
        """本局速览：全部来自选中的规则预设，不是写死的文案。"""
        panel = self.rules_panel
        theme.panel(surface, panel, fill="panel", radius=theme.RADIUS["xl"])
        preset = self.selector.current_preset or {}
        theme.section_header(surface, self.fonts,
                             pygame.Rect(panel.x + 16, panel.y + 10, panel.width - 32, 22),
                             "本局速览", icon="rules", note=preset.get("name", ""))
        rows = [
            ("初始资金", f"{preset.get('starting_money', 0):,}"),
            ("经过起点", f"+{preset.get('pass_start_bonus', 0):,}"),
            ("轮数上限", f"{preset.get('max_rounds', 0)} 轮"),
            ("初始手牌", f"{preset.get('starting_cards', 0)} 张"),
        ]
        x = panel.x + 18
        for label, value in rows:
            theme.stat(surface, self.fonts, x, panel.y + 48, label, value,
                       color_name="accent", value_key="h3")
            x += 120
        theme.draw_text(surface, "地产最高 3 级 · 集齐整个片区租金翻倍 · 最后存活者获胜",
                        self.fonts.tiny(), theme.color("text_mute"),
                        (panel.x + 20, panel.bottom - 28))


# ==================================================================== 局域网

class LanSetupScene(Scene):
    """局域网建房间 / 加入房间。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.mode = "host"
        self.listener: DiscoveryListener | None = None
        self.rooms: list[Any] = []
        self.selected_room = -1
        self.character_id = app.settings.character_id
        self.status = ""
        self._layout_key: tuple = ()
        self.gallery = CharacterGallery(pygame.Rect(140, 300, 620, 240),
                                        self.character_id, self._set_character,
                                        columns=3, card_h=72)
        self.room_list = ScrollPanel(pygame.Rect(880, 300, 580, 300))
        self._room_rows: list[pygame.Rect] = []
        self._build()

    @property
    def subtitle(self) -> str:
        if self.mode == "host":
            return "房间开在本机上；把 IP 和端口告诉朋友，他们在同一个 WiFi 下就能加入"
        return "输入房主的局域网 IP，或直接点右侧搜到的房间"

    def _build(self) -> None:
        fonts = self.app.fonts
        key = (fonts.scale, self.mode)
        if key == self._layout_key:
            return
        self._layout_key = key
        s = self.app.settings
        y0 = theme.page_content_top(fonts, self.subtitle)

        self.name_input = TextInput(pygame.Rect(140, y0 + 22, 320, 46), s.nickname,
                                    placeholder="你的昵称", max_length=12)
        self.room_input = TextInput(pygame.Rect(500, y0 + 22, 360, 46),
                                    s.last_room_name or f"{s.nickname} 的房间",
                                    placeholder="房间名称", max_length=16)
        self.port_host_input = TextInput(pygame.Rect(900, y0 + 22, 160, 46), "28080",
                                         placeholder="端口", max_length=5, numeric=True)
        self.host_input = TextInput(pygame.Rect(140, y0 + 22, 360, 46), s.last_host,
                                    placeholder="房主 IP，例如 192.168.1.5", max_length=40)
        self.port_input = TextInput(pygame.Rect(540, y0 + 22, 160, 46), str(s.last_port),
                                    placeholder="端口", max_length=5, numeric=True)
        self.find_button = Button(
            pygame.Rect(740, y0 + 22, 200, 46), "搜索局域网房间", on_click=self._rescan,
            style="secondary", font_size=16, icon="refresh")

        self.gallery = CharacterGallery(pygame.Rect(140, y0 + 100, 620, 240),
                                        self.character_id, self._set_character,
                                        columns=3, card_h=72)
        self.card_rect = pygame.Rect(140, y0 + 352, 620, 160)
        self.panel_rect = pygame.Rect(880, y0 + 100, 580, 412)

        # 关键修正：所有输入框都必须进入 widgets，否则既画不出来也点不到
        self.widgets = [
            self.name_input, self.room_input, self.port_host_input,
            self.host_input, self.port_input, self.find_button,
            Button(pygame.Rect(140, 806, 240, 56), "返回", on_click=self._back,
                   style="ghost"),
            Button(pygame.Rect(400, 806, 300, 56), "创建房间", on_click=self._submit,
                   style="accent", icon="play"),
            Button(pygame.Rect(880, y0 + 530, 260, 50), "复制连接信息",
                   on_click=self._copy_hint, style="ghost", font_size=16, icon="copy"),
        ]
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        """按模式显示/隐藏字段，并调整按钮文案。"""
        for widget in (self.room_input, self.port_host_input):
            widget.visible = self.mode == "host"
        for widget in (self.host_input, self.port_input, self.find_button):
            widget.visible = self.mode == "join"
        self.widgets[6].label = "返回"
        self.widgets[7].label = "创建房间" if self.mode == "host" else "连接房主"
        self.widgets[7].icon = "network" if self.mode == "host" else "play"
        self.widgets[8].visible = self.mode == "host"

    def _set_character(self, char_id: str) -> None:
        self.character_id = char_id
        self.app.settings.set_character(char_id)

    def _back(self) -> None:
        self._stop_listener()
        self.app.scenes.switch_to("menu")

    def on_enter(self, **kwargs: Any) -> None:
        mode = kwargs.get("mode", "host")
        if mode != self.mode:
            self.mode = mode
            self._layout_key = ()          # 副标题不同 → 重新排版
        self._build()
        self.character_id = self.app.settings.character_id
        self.gallery.selected = self.character_id
        self._sync_visibility()
        if self.mode == "join" and self.app.settings.auto_discovery:
            self._start_listener()
        else:
            self._stop_listener()

    def on_exit(self) -> None:
        self._stop_listener()

    def _start_listener(self) -> None:
        if self.listener is None:
            self.listener = DiscoveryListener(28081)
            ok = self.listener.start()
            if not ok:
                self.status = self.listener.error or "自动发现不可用，请手动输入房主 IP"
            else:
                self.status = "正在搜索局域网内的房间…"

    def _stop_listener(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def _rescan(self) -> None:
        self._stop_listener()
        self._start_listener()
        self.notify("重新搜索局域网房间…", "info")

    def _copy_hint(self) -> None:
        from ..game.setup import available_maps

        text = ""
        if self.mode == "host":
            try:
                from ..network.transport import local_ip_addresses

                ips = local_ip_addresses()
                port = int(self.port_host_input.text or "28080")
                text = f"{ips[0]}:{port}" if ips else "127.0.0.1"
            except Exception:
                text = ""
        if not text:
            self.notify("没有可复制的连接信息", "warning")
            return
        if copy_text(text):
            self.notify(f"已复制「{text}」，发给朋友即可", "success")
        else:
            self.notify(f"请手动记下：{text}", "info")

    def _submit(self) -> None:
        name = (self.name_input.text or "玩家").strip()[:12] or "玩家"
        self.app.settings.set_nickname(name)
        char = self.character_id or self.app.settings.character_id
        if self.mode == "host":
            room = (self.room_input.text or "").strip()[:16] or f"{name} 的房间"
            try:
                port = int(self.port_host_input.text or "28080")
            except ValueError:
                self.notify("端口必须是数字，已使用默认 28080", "warning")
                port = 28080
            self.app.settings.set("network", "last_room_name", room)
            self.app.start_host(room, name, char, port)
        else:
            host = (self.host_input.text or "").strip()
            if not host:
                self.app.push_modal(MessageDialog(
                    "缺少 IP 地址",
                    "请输入房主的局域网 IP 地址。\n"
                    "房主在大厅里能看到自己的 IP（通常是 192.168.x.x）。\n"
                    "如果搜到了房间，直接点右边列表里的房间也能加入。",
                    accent="warning"))
                return
            port = self._port_value()
            self.app.settings.remember_server(host, port)
            self.app.join_host(host, port, name, char)

    def _port_value(self) -> int:
        try:
            return int(self.port_input.text or "28080")
        except ValueError:
            self.notify("端口必须是数字，已使用默认 28080", "warning")
            return 28080

    # ---- 发现的房间
    def _room_row_rects(self) -> list[pygame.Rect]:
        rows = []
        for i, _room in enumerate(self.rooms[:12]):
            rows.append(pygame.Rect(self.room_list.rect.x, 0, self.room_list.rect.width - 12,
                                    54))
            rows[-1].y = self.room_list.rect.y + 12 + i * 60 - self.room_list.offset()
        return rows

    def _join_discovered(self, index: int) -> None:
        """点击发现的房间 → 填入并直接连接。"""
        if index >= len(self.rooms):
            return
        room = self.rooms[index]
        if not room.joinable:
            reason = "该房间已经开局" if room.phase != "lobby" else "该房间人数已满"
            self.notify(f"{reason}，无法加入", "warning")
            return
        self.host_input.set_text(room.ip)
        self.port_input.set_text(str(room.port))
        self._submit()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._back()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._submit()
                return
        if self.room_list.handle_event(event):
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.mode == "join":
                for i, rect in enumerate(self._room_row_rects()):
                    if rect.collidepoint(event.pos):
                        self.selected_room = i
                        self._join_discovered(i)
                        return
        if self.gallery.handle_event(event):
            return
        super().handle_event(event)

    def update(self, dt: float) -> None:
        super().update(dt)
        if self.listener is not None:
            self.rooms = self.listener.snapshot()
            self.room_list.set_content_height(len(self.rooms) * 60 + 12)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        self._build()
        title = "创建局域网房间" if self.mode == "host" else "加入局域网房间"
        content_y = theme.page_title(surface, self.fonts, title, self.subtitle)

        self._draw_form_labels(surface, content_y)
        theme.draw_text(surface, "选择角色（鼠标悬停看被动能力）", self.fonts.small(),
                        theme.color("text_dim"), (140, content_y + 82))
        tips: list[str] = []
        self.gallery.draw(surface, self.fonts, tips)
        draw_character_card(surface, self.fonts, self.card_rect,
                            self.character_id, show_ai=False)

        if self.mode == "host":
            self._draw_host_hint(surface)
        else:
            self._draw_room_list(surface)

        self.draw_widgets(surface)
        mouse = pygame.mouse.get_pos()
        if tips and self.app.settings.show_tooltips:
            draw_tooltip(surface, self.fonts, tips[-1], mouse, bounds=(1600, 900))

    def _draw_form_labels(self, surface: pygame.Surface, content_y: int) -> None:
        y = content_y + 4
        if self.mode == "host":
            theme.draw_text(surface, "你的昵称", self.fonts.small(),
                            theme.color("text_dim"), (140, y))
            theme.draw_text(surface, "房间名称", self.fonts.small(),
                            theme.color("text_dim"), (500, y))
            theme.draw_text(surface, "端口（默认 28080）", self.fonts.small(),
                            theme.color("text_dim"), (900, y))
        else:
            theme.draw_text(surface, "房主 IP（大厅里显示的那个 192.168.x.x）",
                            self.fonts.small(), theme.color("text_dim"), (140, y))
            theme.draw_text(surface, "端口", self.fonts.small(),
                            theme.color("text_dim"), (540, y))
            theme.draw_text(surface, "你的昵称", self.fonts.small(),
                            theme.color("text_dim"), (140, y - 46))

    def _draw_host_hint(self, surface: pygame.Surface) -> None:
        rect = self.panel_rect
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "怎么让朋友加入？", icon="help")
        lines = [
            "1. 点下方「创建房间」",
            "2. 大厅里会大字显示本机局域网 IP（例如 192.168.1.5）",
            "3. 点大厅的「复制连接信息」，把 IP:端口发给朋友",
            "4. 朋友打开同一个 EXE → 「加入房间」→ 粘贴进去",
            "",
            "· 两台电脑要在同一个 WiFi / 局域网下",
            "· Windows 防火墙首次弹窗要勾选「专用网络」并允许",
            "· 搜不到房间不影响联机，手动输入 IP 即可",
        ]
        y = rect.y + 54
        for line in lines:
            theme.draw_text(surface, line, self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 20, y))
            y += 27
        theme.draw_text(surface, "连不上？主菜单里有「局域网诊断」，会告诉你本机状态。",
                        self.fonts.tiny(), theme.color("accent"), (rect.x + 20, rect.bottom - 30))

    def _draw_room_list(self, surface: pygame.Surface) -> None:
        rect = self.panel_rect
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "局域网内发现的房间", icon="network",
                             note=f"{len(self.rooms)} 个")
        clip = surface.get_clip()
        surface.set_clip(rect.inflate(0, -46).move(0, 22))
        if not self.rooms:
            theme.draw_text(surface, self.status or "正在搜索…", self.fonts.body(),
                            theme.color("text_mute"), (rect.centerx, rect.centery - 10),
                            anchor="center")
            theme.draw_text(surface, "搜不到不影响联机：手动输入 IP 即可。", self.fonts.small(),
                            theme.color("text_mute"), (rect.centerx, rect.centery + 20),
                            anchor="center")
        mouse = pygame.mouse.get_pos()
        for i, (row_rect, room) in enumerate(zip(self._room_row_rects(), self.rooms[:12])):
            active = room.joinable
            hovered = row_rect.collidepoint(mouse)
            fill = "panel_hi" if (active and hovered) else ("bg_alt" if active else "bg")
            theme.rounded_rect(surface, row_rect, theme.color(fill),
                               radius=theme.RADIUS["md"])
            theme.rounded_rect(surface, row_rect, None, radius=theme.RADIUS["md"],
                               border=theme.color("success" if active else "border_soft"),
                               border_width=2 if (active and hovered) else 1)
            icons.draw_icon(surface, "network",
                            pygame.Rect(row_rect.x + 12, row_rect.centery - 10, 20, 20),
                            theme.color("success" if active else "text_mute"),
                            theme.color("shadow"))
            theme.draw_text(surface, theme.truncate(room.room_name, self.fonts.h3(), 220),
                            self.fonts.h3(), theme.color("text"),
                            (row_rect.x + 42, row_rect.y + 8))
            detail = (f"{room.ip}:{room.port}　·　{room.players}/{room.max_players} 人"
                      f"　·　{room.map_name}")
            theme.draw_text(surface, theme.truncate(detail, self.fonts.tiny(),
                                                    row_rect.width - 200),
                            self.fonts.tiny(), theme.color("text_dim"),
                            (row_rect.x + 42, row_rect.y + 32))
            label = "点击加入" if active else ("已开局" if room.phase != "lobby" else "已满")
            theme.chip(surface, self.fonts,
                       pygame.Rect(row_rect.right - 92, row_rect.centery - 11, 80, 22),
                       label, "success" if active else "text_mute", font_key="micro")
        surface.set_clip(clip)
        self.room_list.draw_bar(surface, len(self.rooms) * 60 + 12)
        theme.draw_text(surface, self.status, self.fonts.tiny(), theme.color("text_mute"),
                        (rect.x + 20, rect.bottom - 26))


class SaveBrowserScene(Scene):
    """存档选择。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.slots: list[dict[str, Any]] = []
        self.widgets = [
            Button(pygame.Rect(140, 790, 240, 54), "返回",
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
            out.append(pygame.Rect(140, 200 + i * 72, 1320, 62))
        return out

    def _load(self, slot: dict[str, Any]) -> None:
        if not slot.get("ok"):
            self.notify(f"存档损坏：{slot.get('error', '')}", "error")
            return
        self.app.load_saved_game(slot["path"])

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        content_y = theme.page_title(surface, self.fonts, "读取存档",
                                     "点击任意存档继续游戏（自动存档在每个回合结束后写入）")
        if not self.slots:
            theme.draw_text(surface, "还没有存档", self.fonts.h2(), theme.color("text_mute"),
                            (800, 400), anchor="center")
        mouse = pygame.mouse.get_pos()
        for rect, slot in zip(self._row_rects(), self.slots):
            hovered = rect.collidepoint(mouse)
            theme.panel(surface, rect,
                        fill="panel_alt" if hovered else "panel",
                        border="accent" if hovered else "border_soft",
                        radius=theme.RADIUS["lg"])
            icons.draw_icon(surface, "save",
                            pygame.Rect(rect.x + 16, rect.centery - 12, 24, 24),
                            theme.color("accent" if hovered else "text_dim"),
                            theme.color("shadow"))
            name = slot.get("path", "").replace("\\", "/").split("/")[-1]
            theme.draw_text(surface, name, self.fonts.h3(), theme.color("text"),
                            (rect.x + 52, rect.centery - 14))
            detail = (f"第 {slot.get('round', 0)} 轮 · "
                      f"{slot.get('player_count', 0)} 人 · {slot.get('map', '')} · "
                      f"{slot.get('saved_at_text', '')}")
            theme.draw_text(surface, detail, self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 52, rect.centery + 8))
            players = "、".join(slot.get("players", [])[:6])
            theme.draw_text(surface, players, self.fonts.small(), theme.color("text_mute"),
                            (rect.right - 16, rect.centery - 14), anchor="topright")
            if not slot.get("ok"):
                theme.draw_text(surface, "读取失败", self.fonts.small(),
                                theme.color("danger"),
                                (rect.right - 16, rect.centery + 8), anchor="topright")

        self.draw_widgets(surface)
