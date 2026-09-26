"""主菜单场景。

背景完全由程序绘制（棋盘格、城市天际线、漂浮的金币与骰子），
不依赖任何美术资源，因此不会出现空白界面。
"""
from __future__ import annotations

import math
import random
from typing import Any

import pygame

from ..game.tile import TileType
from ..persistence import savegame
from ..utils.easing import clamp
from . import theme
from .board_view import COLS, ROWS, grid_position
from .dialogs import MessageDialog
from .scene import Scene
from .widgets import Button, Panel


class FloatingDecor:
    """漂浮的装饰元素（骰子 / 金币）。"""

    def __init__(self, seed: int = 20240926) -> None:
        self.rng = random.Random(seed)
        self.items: list[dict[str, Any]] = []
        for _ in range(14):
            self.items.append({
                "x": self.rng.uniform(60, 1540),
                "y": self.rng.uniform(120, 820),
                "size": self.rng.uniform(18, 34),
                "speed": self.rng.uniform(6.0, 18.0),
                "phase": self.rng.uniform(0, math.tau),
                "rot": self.rng.uniform(0, 360),
                "rot_speed": self.rng.uniform(-24, 24),
                "kind": self.rng.choice(["coin", "coin", "die"]),
                "value": self.rng.randint(1, 6),
                "alpha": self.rng.randint(26, 62),
            })
        self.t = 0.0

    def update(self, dt: float) -> None:
        self.t += dt
        for item in self.items:
            item["y"] -= item["speed"] * dt
            item["rot"] += item["rot_speed"] * dt
            if item["y"] < -60:
                item["y"] = 960
                item["x"] = self.rng.uniform(60, 1540)

    def draw(self, surface: pygame.Surface) -> None:
        for item in self.items:
            x = item["x"] + math.sin(self.t * 0.6 + item["phase"]) * 16
            y = item["y"]
            alpha = item["alpha"]
            size = int(item["size"])
            color = (*theme.color("accent"), alpha) if item["kind"] == "coin" else (
                *theme.color("text"), alpha)
            if item["kind"] == "coin":
                pygame.draw.circle(surface, color, (int(x), int(y)), size // 2, 2)
                pygame.draw.circle(surface, (*theme.color("accent_soft"), alpha // 2),
                                   (int(x), int(y)), size // 3)
            else:
                rect = pygame.Rect(0, 0, size, size)
                rect.center = (int(x), int(y))
                theme.rounded_rect(surface, rect, (*theme.color("primary"), alpha // 2), radius=4)
                theme.rounded_rect(surface, rect, None, radius=4, border=color, border_width=2)


def draw_board_decor(surface: pygame.Surface, rect: pygame.Rect, t: float,
                     fonts: theme.FontManager, board) -> None:
    """在背景上画一张半透明棋盘剪影。"""
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    cell = min(rect.width // COLS, rect.height // ROWS)
    for tile in board:
        col, row = grid_position(tile.index)
        r = pygame.Rect(col * cell, row * cell, cell, cell)
        accent = theme.color(theme.TILE_TYPE_COLORS.get(tile.type.value, "t_property"))
        pulse = 0.5 + 0.5 * math.sin(t * 1.2 + tile.index * 0.4)
        alpha = int(14 + 16 * pulse)
        theme.rounded_rect(layer, r.inflate(-6, -6), (*accent, alpha), radius=8)
        theme.rounded_rect(layer, r.inflate(-6, -6), None, radius=8,
                           border=(*accent, alpha + 18), border_width=1)
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

        # 布局按 1600×900 逻辑分辨率排：标题区 150-330，按钮区 360-712
        cx = 400
        width = 372
        top = 360
        self.widgets = [
            Button(pygame.Rect(cx - width // 2, top, width, 68), "单人游戏",
                   on_click=lambda: self._start_local(), style="accent", icon="▶",
                   subtitle="和电脑对战，立即开始"),
            Button(pygame.Rect(cx - width // 2, top + 82, width, 52), "创建局域网房间",
                   on_click=lambda: self._create_room(), style="primary", icon="◆"),
            Button(pygame.Rect(cx - width // 2, top + 140, width, 52), "加入房间",
                   on_click=lambda: self._join_room(), style="secondary", icon="▶"),
            Button(pygame.Rect(cx - width // 2, top + 198, width, 52), "读取存档",
                   on_click=lambda: self._load_save(), style="ghost", icon="▮"),
            Button(pygame.Rect(cx - width // 2, top + 256, width, 52), "设置",
                   on_click=lambda: self.app.scenes.switch_to("settings", back="menu"),
                   style="ghost", icon="○"),
            Button(pygame.Rect(cx - width // 2, top + 314, width, 52), "退出游戏",
                   on_click=self.app.quit, style="ghost"),
        ]

    # ------------------------------------------------------------ 动作

    def _start_local(self) -> None:
        self.app.scenes.switch_to("local_setup")

    def _create_room(self) -> None:
        self.app.scenes.switch_to("lan_setup", mode="host")

    def _join_room(self) -> None:
        self.app.scenes.switch_to("lan_setup", mode="join")

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
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        # 背景
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900),
                        (26, 36, 54), (16, 22, 34))

        if self.board is not None:
            draw_board_decor(surface, pygame.Rect(880, 120, 660, 620), self.t,
                             self.fonts, self.board)
        self.decor.draw(surface)

        # 左侧渐变遮罩，让文字更清晰
        layer = pygame.Surface((760, 900), pygame.SRCALPHA)
        for x in range(760):
            a = int(210 * (1.0 - x / 760.0))
            pygame.draw.line(layer, theme.color("bg", a), (x, 0), (x, 900))
        surface.blit(layer, (0, 0))

        self._draw_title(surface)
        self.draw_widgets(surface)
        self._draw_footer(surface)

    def _draw_title(self, surface: pygame.Surface) -> None:
        cx = 400
        # 骰子装饰
        from .animations import draw_die

        bob = math.sin(self.t * 1.6) * 6
        die1 = pygame.Rect(0, 0, 62, 62)
        die1.center = (cx - 92, 150 + bob)
        die2 = pygame.Rect(0, 0, 62, 62)
        die2.center = (cx + 92, 160 - bob)
        theme.shadow_rect(surface, die1, radius=10, spread=5, alpha=90)
        theme.shadow_rect(surface, die2, radius=10, spread=5, alpha=90)
        draw_die(surface, die1, 6)
        draw_die(surface, die2, 5)

        theme.draw_text(surface, "RICHMAN", self.fonts.sized(72, True),
                        theme.color("accent"), (cx, 214), anchor="center")
        theme.draw_text(surface, "大 富 翁", self.fonts.sized(38, True),
                        theme.color("text"), (cx, 272), anchor="center")
        theme.draw_text(surface, "城市之光 · 局域网多人桌游", self.fonts.small(),
                        theme.color("text_dim"), (cx, 310), anchor="center")

    def _draw_footer(self, surface: pygame.Surface) -> None:
        theme.draw_text(surface, "Enter 快速开始单机   ·   Esc 退出", self.fonts.tiny(),
                        theme.color("text_mute"), (400, 856), anchor="center")
        theme.draw_text(surface, "v1.0 · 原创实现 · 本地局域网联机，无需联网",
                        self.fonts.tiny(), theme.color("text_mute"),
                        (1580, 880), anchor="bottomright")
