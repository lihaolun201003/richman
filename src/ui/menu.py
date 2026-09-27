"""主菜单场景。

背景完全由程序绘制（棋盘剪影、城市天际线、漂浮的金币与骰子），
不依赖任何美术资源，因此不会出现空白界面。

v0.3 修正：
- 按钮图标改用矢量图标（原来用 `▶` `▮` 这类文字符号，
  在这些中文字体里没有字形，会渲染成豆腐块）；
- 右侧棋盘剪影提高对比度，让「这是一款大富翁」一眼可见；
- 增加「新手教程」与「局域网诊断」入口。
"""
from __future__ import annotations

import math
import random
from typing import Any

import pygame

from ..game.tile import TileType
from ..persistence import savegame
from ..utils.easing import clamp
from . import icons, theme
from .board_view import COLS, ROWS, grid_position
from .dialogs import MessageDialog
from .scene import Scene
from .widgets import Button


class FloatingDecor:
    """漂浮的装饰元素（骰子 / 金币）。"""

    def __init__(self, seed: int = 20240926) -> None:
        self.rng = random.Random(seed)
        self.items: list[dict[str, Any]] = []
        for _ in range(12):
            self.items.append({
                "x": self.rng.uniform(700, 1560),
                "y": self.rng.uniform(80, 860),
                "size": self.rng.uniform(20, 30),
                "speed": self.rng.uniform(6.0, 16.0),
                "phase": self.rng.uniform(0, math.tau),
                "rot": self.rng.uniform(0, 360),
                "rot_speed": self.rng.uniform(-24, 24),
                "kind": self.rng.choice(["coin", "coin", "die"]),
                "value": self.rng.randint(1, 6),
                "alpha": self.rng.randint(60, 120),
            })
        self.t = 0.0

    def update(self, dt: float) -> None:
        self.t += dt
        for item in self.items:
            item["y"] -= item["speed"] * dt
            item["rot"] += item["rot_speed"] * dt
            if item["y"] < -60:
                item["y"] = 960
                item["x"] = self.rng.uniform(700, 1560)

    def draw(self, surface: pygame.Surface) -> None:
        for item in self.items:
            x = item["x"] + math.sin(self.t * 0.6 + item["phase"]) * 16
            y = item["y"]
            alpha = item["alpha"]
            size = int(item["size"])
            box = pygame.Rect(int(x) - size // 2, int(y) - size // 2, size, size)
            if item["kind"] == "coin":
                icons.draw_icon(surface, "coin", box,
                                theme.color("accent", alpha),
                                theme.color("accent_dark", alpha))
            else:
                icons.draw_icon(surface, "dice", box,
                                theme.color("text", alpha),
                                theme.color("text_dim", alpha))


def draw_board_decor(surface: pygame.Surface, rect: pygame.Rect, t: float,
                     fonts: theme.FontManager, board) -> None:
    """在背景上画一张棋盘剪影（带片区色与呼吸感）。"""
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    cell = min(rect.width // COLS, rect.height // ROWS)
    origin = ((rect.width - cell * COLS) // 2, (rect.height - cell * ROWS) // 2)
    order: list[str] = []
    for tile in board:
        if tile.district and tile.district not in order:
            order.append(tile.district)
    palette = theme.district_palette(order)
    for tile in board:
        col, row = grid_position(tile.index)
        r = pygame.Rect(origin[0] + col * cell, origin[1] + row * cell, cell, cell)
        base = palette.get(tile.district) if tile.district else None
        if base is None:
            base = theme.tile_color(tile.type.value)
        pulse = 0.5 + 0.5 * math.sin(t * 1.2 + tile.index * 0.4)
        alpha = int(34 + 26 * pulse)
        theme.rounded_rect(layer, r.inflate(-5, -5), (*base, alpha), radius=8)
        theme.rounded_rect(layer, r.inflate(-5, -5), None, radius=8,
                           border=(*theme.lighten(base, 0.2), alpha + 30), border_width=1)
    # 中央压暗，形成「棋盘中央是桌子」的观感
    center = pygame.Rect(origin[0] + cell, origin[1] + cell,
                         cell * (COLS - 2), cell * (ROWS - 2))
    theme.rounded_rect(layer, center, (14, 20, 30, 150), radius=14)
    surface.blit(layer, rect.topleft)


class MenuScene(Scene):
    """主菜单。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.decor = FloatingDecor()
        self.board = None
        self.t = 0.0
        self._build()

    def _build(self) -> None:
        from ..game.setup import load_board

        if self.board is None:
            try:
                self.board = load_board()
            except Exception:
                self.board = None

        cx = 400
        width = 380
        top = 336
        step = 62
        self.widgets = [
            Button(pygame.Rect(cx - width // 2, top, width, 68), "单人游戏",
                   on_click=self._start_local, style="accent", icon="play",
                   subtitle="和电脑对战，立即开始"),
            Button(pygame.Rect(cx - width // 2, top + 78, width, 50), "创建局域网房间",
                   on_click=self._create_room, style="primary", icon="network"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step, width, 50), "加入房间",
                   on_click=self._join_room, style="secondary", icon="network"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 2, width, 50),
                   "新手教程", on_click=self._start_tutorial, style="secondary",
                   icon="book", tooltip="3 分钟学会怎么玩（用真实规则跑的短局）"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 3, width, 50), "读取存档",
                   on_click=self._load_save, style="ghost", icon="save"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 4, width, 50), "规则与图鉴",
                   on_click=lambda: self.app.scenes.switch_to("help", back="menu"),
                   style="ghost", icon="book"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 5, width, 50), "设置",
                   on_click=lambda: self.app.scenes.switch_to("settings", back="menu"),
                   style="ghost", icon="gear"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 6, width, 50),
                   "局域网诊断", on_click=self._open_diag, style="ghost", icon="network",
                   tooltip="连不上房间时先看这里：本机 IP / 端口 / 发现服务状态"),
            Button(pygame.Rect(cx - width // 2, top + 78 + step * 7, width, 50), "退出游戏",
                   on_click=self.app.quit, style="ghost", icon="exit"),
        ]

    # ------------------------------------------------------------ 动作

    def _start_local(self) -> None:
        self.app.scenes.switch_to("local_setup")

    def _create_room(self) -> None:
        self.app.scenes.switch_to("lan_setup", mode="host")

    def _join_room(self) -> None:
        self.app.scenes.switch_to("lan_setup", mode="join")

    def _start_tutorial(self) -> None:
        self.app.start_tutorial()

    def _open_diag(self) -> None:
        self.app.scenes.switch_to("net_diag", back="menu")

    def _load_save(self) -> None:
        slots = savegame.list_slots()
        if not slots:
            self.app.push_modal(MessageDialog(
                "没有可用存档",
                "还没有任何存档。开始一局单机游戏后会自动保存进度。",
                accent="warning"))
            return
        self.app.scenes.switch_to("save_browser")

    # ------------------------------------------------------------ 场景

    def on_enter(self, **kwargs: Any) -> None:
        self.app.audio.play_bgm("main")

    def update(self, dt: float) -> None:
        super().update(dt)
        self.t += dt
        self.decor.update(dt)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.quit()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._start_local()
                return
            if event.key == pygame.K_h:
                self.app.scenes.switch_to("help", back="menu")
                return
            if event.key == pygame.K_t:
                self._start_tutorial()
                return
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900),
                        (26, 36, 54), (14, 20, 30))

        if self.board is not None:
            draw_board_decor(surface, pygame.Rect(760, 70, 800, 760), self.t,
                             self.fonts, self.board)
        self.decor.draw(surface)

        # 左侧渐变遮罩，让文字更清晰
        layer = pygame.Surface((820, 900), pygame.SRCALPHA)
        for x in range(820):
            a = int(232 * (1.0 - x / 820.0) ** 1.4)
            pygame.draw.line(layer, theme.color("bg", a), (x, 0), (x, 900))
        surface.blit(layer, (0, 0))

        self._draw_title(surface)
        self.draw_widgets(surface)
        self._draw_footer(surface)

    def _draw_title(self, surface: pygame.Surface) -> None:
        cx = 400
        from .animations import draw_die

        bob = math.sin(self.t * 1.6) * 6
        die1 = pygame.Rect(0, 0, 64, 64)
        die1.center = (cx - 96, 132 + bob)
        die2 = pygame.Rect(0, 0, 64, 64)
        die2.center = (cx + 96, 142 - bob)
        theme.shadow_rect(surface, die1, radius=12, spread=5, alpha=110)
        theme.shadow_rect(surface, die2, radius=12, spread=5, alpha=110)
        draw_die(surface, die1, 6, radius=12)
        draw_die(surface, die2, 5, radius=12)

        theme.draw_text(surface, "RICHMAN", self.fonts.sized(74, True),
                        theme.color("accent"), (cx, 224), anchor="center", shadow=True)
        theme.draw_text(surface, "大 富 翁", self.fonts.sized(38, True),
                        theme.color("text"), (cx, 282), anchor="center")
        theme.draw_text(surface, "城市之光 · 同一 WiFi 就能联机", self.fonts.small(),
                        theme.color("text_dim"), (cx, 318), anchor="center")

    def _draw_footer(self, surface: pygame.Surface) -> None:
        theme.draw_text(surface,
                        "Enter 快速开始单机   ·   T 新手教程   ·   H 规则图鉴   ·   Esc 退出",
                        self.fonts.tiny(), theme.color("text_mute"), (400, 872),
                        anchor="center")
        from ..version import APP_VERSION, VERSION_LABEL
        theme.draw_text(surface, f"{APP_VERSION} · {VERSION_LABEL}",
                        self.fonts.tiny(), theme.color("text_mute"),
                        (1580, 880), anchor="bottomright")
