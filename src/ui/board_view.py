"""棋盘渲染：把 BoardModel 画成一张城市地图。

关键设计：
- 棋盘为 12×8 网格的环形布局，格子为 95×95 的正方形，正好铺满 16:9 画面；
- 静态部分（底纹、格名、价格、装饰）渲染一次后缓存成 Surface，
  每帧只在其上叠加动态内容（归属色、等级、路障、棋子、高亮）；
- 严格只读：BoardView 从不修改任何规则状态。

坐标与索引的对应关系（index 顺时针），必须与 tools/gen_data.py 的
side_of() 完全一致，否则格子会错位：
    0  = 左下角   → 1..9   底边向右
    10 = 右下角   → 11..17 右边向上
    18 = 右上角   → 19..27 顶边向左
    28 = 左上角   → 29..35 左边向下
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.board import Board, Tile
from ..game.property import Property
from ..game.tile import TileType
from . import theme

#: 棋盘网格：11 列 × 9 行（含四角），正好容纳 36 格
COLS = 11
ROWS = 9
#: 角格索引
CORNER_BL = 0
CORNER_BR = 10
CORNER_TR = 18
CORNER_TL = 28


def grid_position(index: int) -> tuple[int, int]:
    """返回格子所在的 (列, 行)。索引超出 0..35 时环绕处理。"""
    index = index % (COLS * 2 + (ROWS - 2) * 2)
    if index == CORNER_BL:
        return 0, ROWS - 1
    if index == CORNER_BR:
        return COLS - 1, ROWS - 1
    if index == CORNER_TR:
        return COLS - 1, 0
    if index == CORNER_TL:
        return 0, 0
    if 1 <= index <= 9:
        return index, ROWS - 1
    if 11 <= index <= 17:
        return COLS - 1, ROWS - 1 - (index - 10)
    if 19 <= index <= 27:
        return COLS - 1 - (index - 18), 0
    return 0, index - 28


class BoardView:
    """棋盘视图。"""

    def __init__(self, board: Board, rect: pygame.Rect) -> None:
        self.board = board
        area = pygame.Rect(rect)
        self.cell = max(24, min(area.width // COLS, area.height // ROWS))
        w, h = self.cell * COLS, self.cell * ROWS
        self.rect = pygame.Rect(
            area.x + (area.width - w) // 2,
            area.y + (area.height - h) // 2,
            w, h,
        )
        self.cache: pygame.Surface | None = None
        self.cache_key: tuple = ()
        self.hover_index: int | None = None
        self.highlight_index: int | None = None
        self._fonts: theme.FontManager | None = None
        self._property_getter = None

    # ------------------------------------------------------------ 几何

    def tile_rect(self, index: int) -> pygame.Rect:
        col, row = grid_position(index)
        return pygame.Rect(
            self.rect.x + col * self.cell,
            self.rect.y + row * self.cell,
            self.cell,
            self.cell,
        )

    def tile_center(self, index: int) -> tuple[int, int]:
        r = self.tile_rect(index)
        return r.center

    def tile_centers(self) -> dict[int, tuple[int, int]]:
        return {t.index: self.tile_center(t.index) for t in self.board}

    @property
    def inner_rect(self) -> pygame.Rect:
        """中央区域（不含边缘格子）。"""
        return pygame.Rect(
            self.rect.x + self.cell,
            self.rect.y + self.cell,
            self.rect.width - self.cell * 2,
            self.rect.height - self.cell * 2,
        )

    def hit_test(self, pos: tuple[int, int]) -> int | None:
        if not self.rect.collidepoint(pos):
            return None
        col = (pos[0] - self.rect.x) // self.cell
        row = (pos[1] - self.rect.y) // self.cell
        for t in self.board:
            c, r = grid_position(t.index)
            if c == col and r == row:
                return t.index
        return None

    # ------------------------------------------------------------ 静态层

    def _build_cache(self, key: tuple) -> pygame.Surface:
        surf = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        local = pygame.Rect(0, 0, self.rect.width, self.rect.height)

        theme.vgradient(surf, local, theme.color("board_bg"), theme.color("board_inner"))
        theme.rounded_rect(surf, local, None, radius=18,
                           border=theme.color("board_line"), border_width=3)

        fonts = self._fonts
        for tile in self.board:
            col, row = grid_position(tile.index)
            local_rect = pygame.Rect(col * self.cell, row * self.cell, self.cell, self.cell)
            self._draw_static_tile(surf, local_rect, tile, fonts)

        # 中央区域
        inner = pygame.Rect(self.cell, self.cell,
                            self.rect.width - self.cell * 2,
                            self.rect.height - self.cell * 2)
        self._draw_center(surf, inner, fonts)
        return surf

    def _draw_center(self, surf: pygame.Surface, inner: pygame.Rect,
                     fonts: theme.FontManager) -> None:
        theme.rounded_rect(surf, inner.inflate(-8, -8), theme.color("board_inner"), radius=16)
        # 装饰性城市剪影
        import math

        base_y = inner.bottom - 30
        layer = pygame.Surface(inner.size, pygame.SRCALPHA)
        for i in range(26):
            w = 22 + (i * 7) % 26
            h = 30 + int(70 * abs(math.sin(i * 1.7)))
            x = 12 + i * (inner.width - 24) // 26
            rect = pygame.Rect(x, base_y - h - inner.y, w, h)
            if rect.right > inner.width - 12:
                break
            alpha = 26 + (i % 3) * 8
            theme.rounded_rect(layer, rect, (120, 140, 120, alpha), radius=3)
        surf.blit(layer, inner.topleft)
        # 中央装饰标题放在区域底部，上方留给回合提示与阶段横幅
        theme.draw_text(surf, self.board.name, fonts.sized(22, True), (176, 184, 166),
                        (inner.centerx, inner.bottom - 66), anchor="midtop")
        theme.draw_text(surf, "RICHMAN", fonts.small(), (190, 196, 178),
                        (inner.centerx, inner.bottom - 40), anchor="midtop")

    def _draw_static_tile(self, surf: pygame.Surface, rect: pygame.Rect,
                          tile: Tile, fonts: theme.FontManager) -> None:
        accent = theme.color(theme.TILE_TYPE_COLORS.get(tile.type.value, "t_property"))
        pad = 3
        body = pygame.Rect(rect.x + pad, rect.y + pad, rect.width - pad * 2, rect.height - pad * 2)

        fill = theme.color("tile") if (tile.index % 2 == 0) else theme.color("tile_alt")
        theme.rounded_rect(surf, body, fill, radius=8)

        # 顶部类型色条（角格画整圈）
        if tile.type in (TileType.START, TileType.JAIL, TileType.PARK, TileType.GO_TO_JAIL):
            theme.rounded_rect(surf, body, None, radius=8, border=accent, border_width=4)
        else:
            bar_h = max(6, self.cell // 12)
            bar = pygame.Rect(body.x + 4, body.y + 4, body.width - 8, bar_h)
            theme.rounded_rect(surf, bar, accent, radius=3)

        icon_h = 0
        # 类型图标（用文字符号代替图形资源）
        glyph = {
            TileType.START: "起",
            TileType.JAIL: "牢",
            TileType.PARK: "园",
            TileType.GO_TO_JAIL: "捕",
            TileType.CHANCE: "?",
            TileType.TAX: "税",
            TileType.BONUS: "奖",
        }.get(tile.type, "")
        if glyph:
            size = 26 if tile.type in (TileType.START, TileType.JAIL, TileType.PARK,
                                       TileType.GO_TO_JAIL) else 22
            theme.draw_text(surf, glyph, fonts.sized(size, True), accent,
                            (body.centerx, body.y + 20), anchor="midtop")
            icon_h = 30

        # 名称：优先单行；放不下则折成两行，保证完整显示不截断
        max_w = body.width - 8
        name_y = body.y + (16 + icon_h if icon_h else 22)
        line_h = fonts.sized(15, True).get_linesize()
        avail = max(1, (body.bottom - 22 - name_y) // max(1, line_h))
        for size in (16, 15, 14, 13):
            name_font = fonts.sized(size, True)
            lines = theme.wrap_text(tile.name, name_font, max_w)
            if len(lines) <= 1 or (len(lines) <= min(2, avail) and size <= 14):
                break
        lines = lines[:2]
        for line in lines:
            theme.draw_text(surf, line, name_font, theme.color("text_dark"),
                            (body.centerx, name_y), anchor="midtop")
            name_y += name_font.get_linesize() - 1

        # 价格
        prop = self._property_of(tile.index)
        if prop is not None:
            price_font = fonts.sized(13, True)
            theme.draw_text(surf, f"{prop.price:,}", price_font, (140, 118, 74),
                            (body.centerx, body.bottom - 20), anchor="midtop")

    def _property_of(self, index: int) -> Property | None:
        getter = self._property_getter
        return getter(index) if getter else None

    # ------------------------------------------------------------ 依赖注入

    def bind(self, fonts: theme.FontManager, property_getter) -> None:
        """注入字体与地产查询函数（避免视图直接持有 GameState）。"""
        self._fonts = fonts
        self._property_getter = property_getter
        self.invalidate()

    def invalidate(self) -> None:
        self.cache = None
        self.cache_key = ()

    # ------------------------------------------------------------ 绘制

    def draw_background(self, surface: pygame.Surface) -> None:
        key = (self.rect.size, self._fonts.scale)
        if self.cache is None or self.cache_key != key:
            self.cache = self._build_cache(key)
            self.cache_key = key
        surface.blit(self.cache, self.rect.topleft)

    def draw_tiles_dynamic(
        self,
        surface: pygame.Surface,
        fonts: theme.FontManager,
        properties: dict[str, Property],
        barriers: dict[str, int],
        owner_colors: dict[str, tuple[int, int, int]],
    ) -> None:
        """叠加动态信息：归属色带、等级点、路障。"""
        for tile in self.board:
            rect = self.tile_rect(tile.index)
            pad = 3
            body = pygame.Rect(rect.x + pad, rect.y + pad, rect.width - pad * 2, rect.height - pad * 2)

            prop = properties.get(f"p{tile.index:02d}")
            if prop is not None and prop.owner_id:
                col = owner_colors.get(prop.owner_id, theme.color("text_mute"))
                strip_h = max(7, self.cell // 11)
                strip = pygame.Rect(body.x + 4, body.bottom - strip_h - 4, body.width - 8, strip_h)
                theme.rounded_rect(surface, strip, col, radius=3)
                # 等级用小白点表示
                if prop.level > 0:
                    dot_r = max(3, self.cell // 20)
                    total = prop.level
                    gap = dot_r * 3
                    start_x = body.centerx - (total - 1) * gap // 2
                    for i in range(total):
                        pygame.draw.circle(surface, (255, 255, 255),
                                           (start_x + i * gap, strip.centery), dot_r)
                        pygame.draw.circle(surface, theme.darken(col, 0.4),
                                           (start_x + i * gap, strip.centery), dot_r, 1)
                if prop.mortgaged:
                    theme.draw_text(surface, "押", fonts.tiny(), theme.color("danger"),
                                    (body.right - 12, body.y + 30), anchor="midtop")

            if str(tile.index) in barriers:
                cone = pygame.Rect(0, 0, 22, 22)
                cone.center = (body.centerx, body.centery + 14)
                pygame.draw.polygon(surface, theme.color("warning"),
                                    [(cone.centerx, cone.top), (cone.right, cone.bottom),
                                     (cone.left, cone.bottom)])
                pygame.draw.polygon(surface, theme.darken(theme.color("warning"), 0.35),
                                    [(cone.centerx, cone.top), (cone.right, cone.bottom),
                                     (cone.left, cone.bottom)], 2)

    def draw_hover(self, surface: pygame.Surface) -> None:
        if self.hover_index is None:
            return
        rect = self.tile_rect(self.hover_index)
        theme.rounded_rect(surface, rect.inflate(-4, -4), theme.color("accent", 46), radius=9)
        theme.rounded_rect(surface, rect.inflate(-4, -4), None, radius=9,
                           border=theme.color("accent"), border_width=2)

    def draw_highlight(self, surface: pygame.Surface, index: int | None,
                       pulse: float = 1.0) -> None:
        """高亮某个格子（例如刚落地或即将前往的地产）。"""
        if index is None:
            return
        rect = self.tile_rect(index)
        alpha = int(90 + 110 * pulse)
        theme.rounded_rect(surface, rect.inflate(-2, -2), theme.color("accent", alpha), radius=10)
        theme.rounded_rect(surface, rect.inflate(-2, -2), None, radius=10,
                           border=theme.color("accent_soft"), border_width=3)
