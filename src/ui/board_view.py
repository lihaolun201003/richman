"""棋盘渲染：把 BoardModel 画成一张真正的城市地图。

设计目标（v0.3）：不再只是「方格 + 文字 + 圆点」，而是一眼像正式桌游地图。

信息分层（从外到内，玩家能快速读出「哪一片 / 谁的 / 几级」）：
    外缘色带 = 片区归属（未购买也能看出片区）
    格内图标 = 格子类型（矢量图形，不是文字符号）
    格内文字 = 名称 + 价格 / 当前租金
    建筑图形 = 等级（1～3 层楼，等级越高楼越高）
    内缘色带 = 所有者（含玩家编号）
    斜纹 + 锁 = 已抵押

关键设计：
- 静态部分（底纹、片区色带、类型图标、名称、价格）渲染一次后缓存成 Surface，
  每帧只叠加动态内容（归属、建筑、路障、棋子、高亮）；
- 严格只读：BoardView 从不修改任何规则状态；
- 网格尺寸与四角索引都从地图数据读取，新地图不需要改这里。
"""
from __future__ import annotations

import math
from typing import Any

import pygame

from ..game.board import Board, Tile
from ..game.property import Property
from ..game.tile import TileType
from . import icons, theme

#: 默认网格（城市之光），仅用于没有地图数据的装饰场景
COLS = 11
ROWS = 9

#: 角格（非地产）：整圈边框 + 大图标
CORNER_TYPES = (TileType.START, TileType.JAIL, TileType.PARK, TileType.GO_TO_JAIL)


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
        #: 片区 → 颜色（按地图中片区首次出现顺序稳定分配）
        self.district_colors: dict[str, tuple[int, int, int]] = {}
        self._compute_districts()

    # ------------------------------------------------------------ 片区

    def _compute_districts(self) -> None:
        order: list[str] = []
        for tile in self.board:
            if tile.district and tile.district not in order:
                order.append(tile.district)
        self.district_colors = theme.district_palette(order)

    def district_color(self, district: str | None) -> tuple[int, int, int]:
        if not district:
            return theme.color("text_mute")
        return self.district_colors.get(district, theme.color("text_mute"))

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

    def edge_of(self, index: int) -> str:
        """格子朝向棋盘外侧的方向：bottom / right / top / left。"""
        col, row = self.position(index)
        if row >= self.rows - 1:
            return "bottom"
        if col >= self.cols - 1:
            return "right"
        if row <= 0:
            return "top"
        return "left"

    def hit_test(self, pos: tuple[int, int]) -> int | None:
        if not self.rect.collidepoint(pos):
            return None
        col = (pos[0] - self.rect.x) // self.cell
        row = (pos[1] - self.rect.y) // self.cell
        for t in self.board:
            c, r = grid_position_for(self.cols, self.rows, self.corners, t.index)
            if c == col and r == row:
                return t.index
        return None

    # ------------------------------------------------------------ 静态层

    def _build_cache(self, key: tuple) -> pygame.Surface:
        surf = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        local = pygame.Rect(0, 0, self.rect.width, self.rect.height)

        # 桌面：木纹感底板 + 内圈
        theme.vgradient(surf, local, (250, 246, 236), (232, 226, 210))
        theme.rounded_rect(surf, local, None, radius=20,
                           border=(206, 196, 176), border_width=4)
        theme.rounded_rect(surf, local.inflate(-12, -12), None, radius=16,
                           border=(222, 214, 196), border_width=1)

        fonts = self._fonts
        for tile in self.board:
            col, row = self.position(tile.index)
            local_rect = pygame.Rect(col * self.cell, row * self.cell, self.cell, self.cell)
            self._draw_static_tile(surf, local_rect, tile, fonts)

        inner = pygame.Rect(self.cell, self.cell,
                            self.rect.width - self.cell * 2,
                            self.rect.height - self.cell * 2)
        self._draw_center(surf, inner, fonts)
        return surf

    # ---- 中央区域（静态部分：只画背景与水印，动态信息由 GameScene 叠加）

    def _draw_center(self, surf: pygame.Surface, inner: pygame.Rect,
                     fonts: theme.FontManager) -> None:
        plate = inner.inflate(-10, -10)
        theme.rounded_rect(surf, plate, (240, 235, 222), radius=18)
        theme.rounded_rect(surf, plate, None, radius=18, border=(216, 208, 190),
                           border_width=2)

        layer = pygame.Surface(plate.size, pygame.SRCALPHA)
        w, h = plate.size
        # 街区网格：让中央看起来像一张城市图，而不是空白
        road = pygame.Surface((w, h), pygame.SRCALPHA)
        step = max(46, w // 16)
        for x in range(step, w, step):
            pygame.draw.line(road, (196, 190, 172, 90), (x, 0), (x, h), 2)
        for y in range(step, h, step):
            pygame.draw.line(road, (196, 190, 172, 90), (0, y), (w, y), 2)
        layer.blit(road, (0, 0))

        # 城市街块：越靠下越密，形成景深
        import random

        rng = random.Random(hash(self.board.map_id) & 0xFFFF)
        for i in range(46):
            bw = rng.randint(18, 46)
            bh = rng.randint(14, 40)
            x = rng.randint(8, max(9, w - bw - 8))
            y = rng.randint(int(h * 0.42), max(int(h * 0.43), h - bh - 8))
            tint = rng.choice([(206, 210, 198), (214, 206, 192), (198, 206, 208),
                               (218, 210, 192)])
            theme.rounded_rect(layer, pygame.Rect(x, y, bw, bh), (*tint, 130), radius=4)
            theme.rounded_rect(layer, pygame.Rect(x, y, bw, bh), None, radius=4,
                               border=(176, 172, 158, 150), border_width=1)
        # 顶部留白处画两行树
        for i in range(12):
            cx = 18 + i * max(1, (w - 36) // 11)
            cy = int(h * 0.4)
            pygame.draw.circle(layer, (176, 202, 172, 110), (cx, cy), 7)
            pygame.draw.circle(layer, (150, 178, 148, 130), (cx, cy), 7, 1)
        surf.blit(layer, plate.topleft)

        # 水印：地图名 + 品牌字样（低对比，不抢信息）
        theme.draw_text(surf, self.board.name, fonts.sized(30, True), (206, 198, 178),
                        (plate.centerx, plate.y + 16), anchor="midtop")
        theme.draw_text(surf, "R I C H M A N", fonts.sized(13, True), (208, 202, 184),
                        (plate.centerx, plate.y + 50), anchor="midtop")

    # ---- 单个格子

    def _draw_static_tile(self, surf: pygame.Surface, rect: pygame.Rect,
                          tile: Tile, fonts: theme.FontManager) -> None:
        type_color = theme.tile_color(tile.type.value)
        is_corner = tile.type in CORNER_TYPES
        pad = 2 if is_corner else 3
        body = pygame.Rect(rect.x + pad, rect.y + pad,
                           rect.width - pad * 2, rect.height - pad * 2)
        radius = max(6, self.cell // 12)

        # 底板：偶数格略亮，形成纸面质感
        fill = (255, 253, 247) if tile.index % 2 == 0 else (248, 244, 234)
        theme.rounded_rect(surf, body, fill, radius=radius)
        theme.rounded_rect(surf, body, None, radius=radius,
                           border=(198, 190, 172), border_width=1)

        # 外缘色带：片区（地产）或类型色（其它）
        band_color = self.district_color(tile.district) if tile.is_purchasable else type_color
        band_w = max(7, self.cell // 11)
        self._draw_outer_band(surf, body, band_color, band_w, is_corner, tile)

        icon_size = max(18, int(self.cell * (0.30 if is_corner else 0.26)))
        icon_rect = pygame.Rect(0, 0, icon_size, icon_size)

        inner = body.inflate(-band_w * 2 - 6, -band_w * 2 - 6)
        # 图标位置：角格居中偏上，普通格左上角
        if is_corner:
            icon_rect.center = (inner.centerx, inner.y + icon_size // 2 + 2)
            draw_alpha = 255
        else:
            icon_rect.topleft = (inner.x + 1, inner.y + 1)
            draw_alpha = 235
        icons.draw_icon(surf, theme.tile_icon(tile.type.value), icon_rect,
                        type_color, (120, 112, 96))

        # 文字区
        text_left = inner.x + (0 if is_corner else icon_size + 3)
        max_w = max(20, inner.right - text_left)
        name_bottom = self._draw_tile_name(surf, fonts, tile, text_left, inner, 
                                           is_corner=is_corner, max_w=max_w)
        self._draw_tile_footer(surf, fonts, tile, body, inner, band_w, name_bottom)

    def _draw_outer_band(self, surf: pygame.Surface, body: pygame.Rect,
                         band_color: tuple[int, int, int], band_w: int,
                         is_corner: bool, tile: Tile) -> None:
        """在朝向棋盘外侧的边上画一条粗色带（片区 / 类型色）。"""
        if is_corner:
            theme.rounded_rect(surf, body, None, radius=max(6, self.cell // 12),
                               border=theme.lighten(band_color, 0.1), border_width=4)
            return
        edge = self.edge_of(tile.index)
        if edge == "bottom":
            r = pygame.Rect(body.x + 3, body.bottom - band_w - 3, body.width - 6, band_w)
        elif edge == "top":
            r = pygame.Rect(body.x + 3, body.y + 3, body.width - 6, band_w)
        elif edge == "right":
            r = pygame.Rect(body.right - band_w - 3, body.y + 3, band_w, body.height - 6)
        else:
            r = pygame.Rect(body.x + 3, body.y + 3, band_w, body.height - 6)
        theme.rounded_rect(surf, r, band_color, radius=max(2, band_w // 3))
        theme.rounded_rect(surf, r, None, radius=max(2, band_w // 3),
                           border=theme.darken(band_color, 0.25), border_width=1)

    def _draw_tile_name(self, surf: pygame.Surface, fonts: theme.FontManager,
                        tile: Tile, x: int, inner: pygame.Rect, *,
                        is_corner: bool, max_w: int) -> int:
        """绘制格子名称，返回名称区域的底边 y。"""
        lines_size = [(15, True), (14, True), (13, True), (12, True)] if is_corner \
            else [(13, True), (12, True), (11, True)]
        y = inner.y + (self.cell * 0.44 if is_corner else 0)
        if is_corner:
            y = inner.y + int(self.cell * 0.42)
        best_lines: list[str] = [tile.name]
        best_size = lines_size[0][0]
        avail_h = max(1, inner.bottom - y - (18 if not is_corner else 6))
        for size, bold in lines_size:
            font = fonts.sized(size, bold)
            lines = theme.wrap_text(tile.name, font, max_w)
            line_h = font.get_linesize()
            max_lines = max(1, avail_h // max(1, line_h))
            if len(lines) <= 1 or len(lines) <= min(2, max_lines):
                best_lines, best_size = lines[:2], size
                break
            best_lines, best_size = lines[:2], size
        font = fonts.sized(best_size, True)
        for line in best_lines[:2]:
            theme.draw_text(surf, line, font, (58, 54, 46), (x, y))
            y += font.get_linesize() - 1
        return y

    def _draw_tile_footer(self, surf: pygame.Surface, fonts: theme.FontManager,
                          tile: Tile, body: pygame.Rect, inner: pygame.Rect,
                          band_w: int, name_bottom: int) -> None:
        """格子底部：地价或类型说明。"""
        prop = self._property_of(tile.index)
        edge = self.edge_of(tile.index)
        bottom = body.bottom - (band_w + 5 if edge == "bottom" else 4)
        if prop is not None and prop.price:
            font = fonts.sized(12, True)
            text = f"{prop.price:,}"
            theme.draw_text(surf, text, font, (150, 116, 62),
                            (inner.centerx, bottom), anchor="midbottom")
        elif tile.type is TileType.TAX and tile.note:
            font = fonts.sized(10)
            theme.draw_text(surf, theme.truncate(tile.note, font, inner.width),
                            font, (146, 118, 108), (inner.centerx, bottom), anchor="midbottom")

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
        key = (self.rect.size, self._fonts.scale if self._fonts else 1.0)
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
        owner_slots: dict[str, int] | None = None,
        monopolies: set[str] | None = None,
    ) -> None:
        """叠加动态信息：所有者色带、建筑等级、抵押斜纹、路障。"""
        owner_slots = owner_slots or {}
        monopolies = monopolies or set()
        for tile in self.board:
            prop = properties.get(f"p{tile.index:02d}")
            if prop is not None and prop.owner_id:
                self._draw_owner_overlay(surface, fonts, tile, prop, owner_colors,
                                         owner_slots, monopolies)
            if str(tile.index) in barriers:
                self._draw_barrier(surface, tile)

    def _owner_inner_rect(self, tile: Tile) -> pygame.Rect:
        pad = 2 if tile.type in CORNER_TYPES else 3
        body = self.tile_rect(tile.index).inflate(-pad * 2, -pad * 2)
        band_w = max(7, self.cell // 11)
        edge = self.edge_of(tile.index)
        inset = 3
        if edge == "bottom":
            body.height -= band_w + inset
            body.y += inset
        elif edge == "top":
            body.y += band_w + inset * 2
            body.height -= band_w + inset * 2
        elif edge == "right":
            body.width -= band_w + inset
        else:
            body.x += band_w + inset * 2
            body.width -= band_w + inset * 2
        return body

    def _draw_owner_overlay(self, surface: pygame.Surface, fonts: theme.FontManager,
                            tile: Tile, prop: Property,
                            owner_colors: dict[str, tuple[int, int, int]],
                            owner_slots: dict[str, int],
                            monopolies: set[str]) -> None:
        col = owner_colors.get(prop.owner_id, theme.color("text_mute"))
        body = self.tile_rect(tile.index).inflate(-4, -4)
        radius = max(6, self.cell // 12)
        band_w = max(7, self.cell // 11)
        edge = self.edge_of(tile.index)

        # 内缘所有者色带（与外缘片区色带区分）
        strip_w = max(5, self.cell // 16)
        if edge == "bottom":
            strip = pygame.Rect(body.x + 4, body.y + 3, body.width - 8, strip_w)
        elif edge == "top":
            strip = pygame.Rect(body.x + 4, body.bottom - strip_w - 3, body.width - 8, strip_w)
        elif edge == "right":
            strip = pygame.Rect(body.x + 3, body.y + 4, strip_w, body.height - 8)
        else:
            strip = pygame.Rect(body.right - strip_w - 3, body.y + 4, strip_w, body.height - 8)
        theme.rounded_rect(surface, strip, col, radius=2)
        theme.rounded_rect(surface, strip, None, radius=2,
                           border=theme.darken(col, 0.35), border_width=1)

        # 整格淡色底：一眼看出这块地有主
        theme.rounded_rect(surface, body, (*col, 34), radius=radius)
        # 垄断片区：金色描边 + 皇冠
        if prop.district and prop.district in monopolies:
            theme.rounded_rect(surface, body, None, radius=radius,
                               border=theme.color("accent"), border_width=3)

        content = self._owner_inner_rect(tile)
        # 建筑（等级）：层数越多楼越高；0 级画空地虚框
        build_h = max(16, int(self.cell * (0.34 if edge in ("left", "right") else 0.38)))
        build_w = max(16, int(self.cell * 0.5))
        area = pygame.Rect(0, 0, build_w, build_h)
        area.midbottom = (content.centerx, content.bottom - 1)
        if prop.level > 0:
            icons.draw_icon(surface, f"building{min(3, prop.level)}", area, col,
                            theme.darken(col, 0.5))
        else:
            # 空地：虚线框 + 空地标记
            theme.rounded_rect(surface, area, (*col, 40), radius=4)
            theme.rounded_rect(surface, area, None, radius=4,
                               border=(*col, 190), border_width=2)

        # 所有者编号（颜色之外的第二重标识，色觉差异下也能分辨）
        slot = owner_slots.get(prop.owner_id)
        if slot is not None:
            badge = max(11, self.cell // 6)
            r = pygame.Rect(0, 0, badge, badge)
            r.topright = (content.right - 1, content.y + 1)
            pygame.draw.circle(surface, col, r.center, badge // 2)
            pygame.draw.circle(surface, theme.darken(col, 0.45), r.center, badge // 2, 1)
            theme.draw_text(surface, str(slot + 1), fonts.sized(max(9, badge // 2), True),
                            (255, 255, 255), r.center, anchor="center")

        # 抵押：斜纹 + 锁
        if prop.mortgaged:
            self._draw_mortgage_hatch(surface, body, radius)
            lock = max(14, int(self.cell * 0.26))
            lr = pygame.Rect(0, 0, lock, lock)
            lr.center = content.center
            icons.draw_icon(surface, "lock", lr, theme.color("warning"),
                            (110, 70, 20))

    def _draw_mortgage_hatch(self, surface: pygame.Surface, body: pygame.Rect,
                             radius: int) -> None:
        layer = pygame.Surface(body.size, pygame.SRCALPHA)
        step = max(7, self.cell // 8)
        color = (240, 158, 52, 58)
        for i in range(-body.height, body.width + body.height, step):
            pygame.draw.line(layer, color, (i, 0), (i + body.height, body.height), 3)
        mask = pygame.Surface(body.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
        layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        surface.blit(layer, body.topleft)

    def _draw_barrier(self, surface: pygame.Surface, tile: Tile) -> None:
        body = self.tile_rect(tile.index).inflate(-6, -6)
        size = max(18, int(self.cell * 0.42))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = (body.centerx, body.bottom - size // 2 - 2)
        icons.draw_icon(surface, "cone", rect, theme.color("warning"),
                        theme.darken(theme.color("warning"), 0.45))
        font = self._fonts.sized(10, True) if self._fonts else None
        if font is not None:
            days = None
            theme.draw_text(surface, "", font, theme.color("text"), (0, 0))

    def draw_hover(self, surface: pygame.Surface) -> None:
        if self.hover_index is None:
            return
        rect = self.tile_rect(self.hover_index)
        theme.rounded_rect(surface, rect.inflate(-4, -4), theme.color("accent", 44),
                           radius=max(6, self.cell // 12))
        theme.rounded_rect(surface, rect.inflate(-4, -4), None,
                           radius=max(6, self.cell // 12),
                           border=theme.color("accent"), border_width=2)

    def draw_highlight(self, surface: pygame.Surface, index: int | None,
                       pulse: float = 1.0) -> None:
        """高亮某个格子（例如刚落地或即将前往的地产）。"""
        if index is None:
            return
        rect = self.tile_rect(index)
        alpha = int(70 + 110 * pulse)
        theme.rounded_rect(surface, rect.inflate(-2, -2), theme.color("accent", alpha),
                           radius=max(7, self.cell // 11))
        theme.rounded_rect(surface, rect.inflate(-2, -2), None,
                           radius=max(7, self.cell // 11),
                           border=theme.color("accent_soft"), border_width=3)

    def draw_target_ring(self, surface: pygame.Surface, index: int,
                         color_name: str = "primary", pulse: float = 1.0) -> None:
        """可选目标的高亮环（道具选目标时用）。"""
        rect = self.tile_rect(index)
        alpha = int(120 + 100 * pulse)
        theme.rounded_rect(surface, rect.inflate(-2, -2), theme.color(color_name, 40),
                           radius=max(7, self.cell // 11))
        theme.rounded_rect(surface, rect.inflate(-2, -2), None,
                           radius=max(7, self.cell // 11),
                           border=theme.color(color_name, alpha), border_width=3)


def district_progress(state: Any, player_id: str) -> list[tuple[str, int, int]]:
    """返回某玩家在各片区的「已持有 / 总数」，用于地图与资产面板的片区完成度。"""
    out: list[tuple[str, int, int]] = []
    for district, ids in (state.district_props or {}).items():
        total = len(ids)
        owned = 0
        for pid in ids:
            prop = state.properties.get(pid)
            if prop is not None and prop.owner_id == player_id:
                owned += 1
        out.append((district, owned, total))
    out.sort(key=lambda x: (-(x[1] / max(1, x[2])), x[0]))
    return out


def monopoly_districts(state: Any, player_id: str) -> set[str]:
    """某玩家已经集齐（垄断）的片区集合。"""
    out: set[str] = set()
    for district, ids in (state.district_props or {}).items():
        if not ids:
            continue
        if all((state.properties.get(pid) is not None
                and state.properties[pid].owner_id == player_id) for pid in ids):
            out.add(district)
    return out


def piece_bounce(t: float) -> float:
    """棋子落地的小弹跳曲线（0 → 1 的补间形状）。"""
    return math.sin(math.pi * min(1.0, max(0.0, t)))
