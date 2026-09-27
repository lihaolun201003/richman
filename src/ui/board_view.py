"""棋盘渲染：把 BoardModel 画成一张城市地图。

关键设计：
- 棋盘为 12×8 网格的环形布局，格子为 95×95 的正方形，正好铺满 16:9 画面；
- 静态部分（底纹、格名、价格、装饰）渲染一次后缓存成 Surface，
  每帧只在其上叠加动态内容（归属色、等级、路障、棋子、高亮）；
- 严格只读：BoardView 从不修改任何规则状态。

网格尺寸（列 × 行）与四角索引都从地图数据读取（Board.cols / Board.rows /
Board.corners），因此新增地图不需要改这里。

索引顺时针：
    左下角 → 沿底边向右 → 右下角 → 沿右边向上 → 右上角
    → 沿顶边向左 → 左上角 → 沿左边向下 → 回到左下角
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.board import Board, Tile
from ..game.property import Property
from ..game.tile import TileType
from . import theme

#: 默认网格（城市之光），仅用于没有地图数据的装饰场景
COLS = 11
ROWS = 9


def grid_position_for(cols: int, rows: int, corners: list[int],
                      index: int) -> tuple[int, int]:
    """通用网格定位：给定网格尺寸与四角索引，算出 (列, 行)。"""
    bl, br, tr, tl = corners[0], corners[1], corners[2], corners[3]
    last_row, last_col = rows - 1, cols - 1
    index = index % (2 * cols + 2 * (rows - 2))
    if index == bl:
        return 0, last_row
    if index == br:
        return last_col, last_row
    if index == tr:
        return last_col, 0
    if index == tl:
        return 0, 0
    if bl < index < br:
        return index - bl, last_row
    if br < index < tr:
        return last_col, last_row - (index - br)
    if tr < index < tl:
        return last_col - (index - tr), 0
    return 0, index - tl


def grid_position(index: int) -> tuple[int, int]:
    """默认地图（城市之光）的定位，供不带棋盘对象的装饰使用。"""
    return grid_position_for(COLS, ROWS, [0, COLS - 1,
                                          COLS + ROWS - 2,
                                          2 * COLS + ROWS - 3], index)


class BoardView:
    """棋盘视图。"""

    def __init__(self, board: Board, rect: pygame.Rect) -> None:
        self.board = board
        self.cols = max(4, int(getattr(board, "cols", COLS) or COLS))
        self.rows = max(4, int(getattr(board, "rows", ROWS) or ROWS))
        corners = list(getattr(board, "corners", None) or [])
        if len(corners) != 4:
            corners = [0, self.cols - 1, self.cols + self.rows - 2,
                       2 * self.cols + self.rows - 3]
        self.corners = corners
        area = pygame.Rect(rect)
        self.cell = max(24, min(area.width // self.cols, area.height // self.rows))
        w, h = self.cell * self.cols, self.cell * self.rows
        self.rect = pygame.Rect(
            area.x + (area.width - w) // 2,
            area.y + (area.height - h) // 2,
            w, h,
        )
        # 静态层缓存与交互状态
        self.cache: pygame.Surface | None = None
        self.cache_key: tuple = ()
        self.hover_index: int | None = None
        self.highlight_index: int | None = None
        self._fonts: theme.FontManager | None = None
        self._property_getter = None

    def position(self, index: int) -> tuple[int, int]:
        return grid_position_for(self.cols, self.rows, self.corners, index)

    # ------------------------------------------------------------ 几何

    def tile_rect(self, index: int) -> pygame.Rect:
        col, row = self.position(index)
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
            col, row = self.position(tile.index)
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
