"""主题：颜色、字体、绘制辅助。

字体策略：在系统字体里按优先级查找可用的中文字体，
绝不绑定某台机器的绝对路径；找不到时退回 pygame 默认字体并给出提示，
但不会崩溃，也不会出现一堆方块（会退化为可见的缺字提示）。
"""
from __future__ import annotations

import os
from typing import Any

import pygame

from ..utils.logging_setup import get_logger

log = get_logger(__name__)

# ==================================================================== 颜色

COLORS: dict[str, tuple[int, int, int]] = {
    # 背景层次
    "bg": (24, 32, 44),
    "bg_alt": (30, 40, 56),
    "panel": (36, 48, 66),
    "panel_alt": (44, 58, 78),
    "panel_hi": (56, 74, 98),
    "border": (66, 86, 112),
    "border_soft": (52, 68, 90),

    # 文本
    "text": (240, 244, 250),
    "text_dim": (154, 168, 184),
    "text_mute": (110, 124, 142),
    "text_dark": (40, 50, 64),

    # 强调
    "accent": (240, 180, 41),
    "accent_dark": (196, 142, 22),
    "accent_soft": (252, 216, 122),
    "primary": (66, 150, 240),
    "primary_dark": (44, 116, 200),
    "success": (60, 200, 130),
    "success_dark": (36, 156, 96),
    "danger": (232, 90, 78),
    "danger_dark": (188, 62, 52),
    "warning": (244, 158, 52),
    "info": (120, 190, 240),

    # 棋盘
    "board_bg": (247, 243, 232),
    "board_inner": (236, 230, 214),
    "board_line": (206, 198, 180),
    "tile": (252, 250, 244),
    "tile_alt": (244, 240, 230),
    "tile_hover": (255, 252, 236),

    # 格子类型色
    "t_start": (86, 196, 132),
    "t_property": (120, 168, 232),
    "t_station": (156, 138, 232),
    "t_chance": (248, 186, 72),
    "t_tax": (232, 116, 100),
    "t_jail": (140, 148, 164),
    "t_go_jail": (216, 108, 120),
    "t_park": (108, 196, 168),
    "t_bonus": (240, 160, 96),

    # 遮罩
    "overlay": (12, 16, 24),
    "shadow": (10, 14, 20),
}

TILE_TYPE_COLORS = {
    "START": "t_start",
    "PROPERTY": "t_property",
    "STATION": "t_station",
    "CHANCE": "t_chance",
    "TAX": "t_tax",
    "JAIL": "t_jail",
    "GO_TO_JAIL": "t_go_jail",
    "PARK": "t_park",
    "BONUS": "t_bonus",
}


def color(name: str, alpha: int | None = None) -> tuple[int, ...]:
    """取颜色。alpha 不为 None 时返回带透明度的四元组。"""
    rgb = COLORS.get(name, (255, 0, 255))
    if alpha is None:
        return rgb
    return (rgb[0], rgb[1], rgb[2], int(alpha))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except (ValueError, IndexError):
        return (200, 200, 200)


# ==================================================================== 字体

#: 中文字体查找优先级（用 pygame 的字体名，跨机器可移植）
CJK_FONT_CANDIDATES = [
    "microsoftyaheiui", "microsoftyahei", "msyh",
    "simhei", "dengxian", "simsun", "nsimsun",
    "notosanscjksc", "notosanssc", "sourcehansanssc",
    "pingfangsc", "heitisc", "wenquanyimicrohei",
    "fangsong", "kaiti", "simkai", "youyuan", "libian",
    "arialunicodems",
]


class FontManager:
    """字体缓存。同一 (尺寸, 粗体) 只创建一次。"""

    def __init__(self, scale: float = 1.0) -> None:
        self.scale = max(0.6, min(2.0, scale))
        self._cache: dict[tuple[int, bool], pygame.font.Font] = {}
        self._font_path: str | None = None
        self._bold_path: str | None = None
        self.warning: str = ""
        self._resolve_fonts()

    def _resolve_fonts(self) -> None:
        available = set(pygame.font.get_fonts())
        picked = None
        for name in CJK_FONT_CANDIDATES:
            if name in available:
                picked = name
                break
        if picked is None:
            self.warning = "未找到中文字体，中文可能无法正常显示"
            log.warning(self.warning)
            return
        try:
            self._font_path = pygame.font.match_font(picked)
            self._bold_path = pygame.font.match_font(picked, bold=True) or self._font_path
        except Exception as exc:  # pragma: no cover
            log.warning("字体解析失败：%s", exc)

    @property
    def font_name(self) -> str:
        return os.path.basename(self._font_path) if self._font_path else "默认字体"

    def set_scale(self, scale: float) -> None:
        scale = max(0.6, min(2.0, scale))
        if abs(scale - self.scale) < 1e-3:
            return
        self.scale = scale
        self._cache.clear()

    def sized(self, size: int, bold: bool = False) -> pygame.font.Font:
        actual = max(10, int(round(size * self.scale)))
        key = (actual, bold)
        font = self._cache.get(key)
        if font is None:
            path = self._bold_path if bold else self._font_path
            try:
                font = pygame.font.Font(path, actual)
            except Exception:
                font = pygame.font.SysFont(None, actual, bold=bold)
            self._cache[key] = font
        return font

    # ---- 常用尺寸（按 1600×900 逻辑分辨率设计）
    def title(self) -> pygame.font.Font:
        return self.sized(56, True)

    def h1(self) -> pygame.font.Font:
        return self.sized(34, True)

    def h2(self) -> pygame.font.Font:
        return self.sized(24, True)

    def h3(self) -> pygame.font.Font:
        return self.sized(20, True)

    def body(self) -> pygame.font.Font:
        return self.sized(17)

    def small(self) -> pygame.font.Font:
        return self.sized(15)

    def tiny(self) -> pygame.font.Font:
        return self.sized(13)

    def micro(self) -> pygame.font.Font:
        return self.sized(11)

    def money(self) -> pygame.font.Font:
        return self.sized(22, True)

    def tile(self) -> pygame.font.Font:
        return self.sized(15, True)

    def tile_small(self) -> pygame.font.Font:
        return self.sized(12)


# ==================================================================== 绘制

def rounded_rect(
    surface: pygame.Surface,
    rect: pygame.Rect,
    fill: tuple[int, ...] | None,
    radius: int = 12,
    border: tuple[int, ...] | None = None,
    border_width: int = 2,
) -> pygame.Rect:
    """绘制圆角矩形，返回实际绘制的矩形。"""
    r = pygame.Rect(rect)
    if r.width <= 0 or r.height <= 0:
        return r
    radius = max(0, min(radius, min(r.width, r.height) // 2))
    if fill is not None:
        if len(fill) == 4:
            layer = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.rect(layer, fill, layer.get_rect(), border_radius=radius)
            surface.blit(layer, r.topleft)
        else:
            pygame.draw.rect(surface, fill, r, border_radius=radius)
    if border is not None and border_width > 0:
        pygame.draw.rect(surface, border, r, width=border_width, border_radius=radius)
    return r


def shadow_rect(
    surface: pygame.Surface,
    rect: pygame.Rect,
    radius: int = 12,
    spread: int = 4,
    alpha: int = 90,
) -> None:
    """在矩形下方画一层柔和阴影。"""
    r = pygame.Rect(rect).inflate(spread * 2, spread * 2)
    r.y += max(2, spread // 2)
    layer = pygame.Surface(r.size, pygame.SRCALPHA)
    pygame.draw.rect(layer, color("shadow", alpha), layer.get_rect(), border_radius=radius + spread)
    surface.blit(layer, r.topleft)


def vgradient(surface: pygame.Surface, rect: pygame.Rect,
              top: tuple[int, ...], bottom: tuple[int, ...]) -> None:
    """竖直渐变填充。"""
    rect = pygame.Rect(rect)
    if rect.height <= 0 or rect.width <= 0:
        return
    for y in range(rect.height):
        t = y / max(1, rect.height - 1)
        col = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        pygame.draw.line(surface, col, (rect.x, rect.y + y), (rect.right - 1, rect.y + y))


def draw_text(
    surface: pygame.Surface,
    text: str,
    font: pygame.font.Font,
    color_value: tuple[int, ...],
    pos: tuple[int, int],
    anchor: str = "topleft",
    shadow: bool = False,
) -> pygame.Rect:
    """绘制单行文本，返回其矩形。"""
    if not text:
        return pygame.Rect(pos, (0, 0))
    if len(color_value) == 4:
        img = font.render(text, True, color_value[:3])
        img.set_alpha(color_value[3])
    else:
        img = font.render(text, True, color_value)
    rect = img.get_rect(**{anchor: pos})
    if shadow:
        sh = font.render(text, True, color("shadow"))
        surface.blit(sh, (rect.x + 1, rect.y + 2))
    surface.blit(img, rect)
    return rect


def wrap_text(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    """按像素宽度折行。中文按字符折，英文按单词折。"""
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            if font.size(trial)[0] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines


def draw_wrapped(
    surface: pygame.Surface,
    text: str,
    font: pygame.font.Font,
    color_value: tuple[int, ...],
    rect: pygame.Rect,
    line_gap: int = 4,
    max_lines: int = 0,
) -> int:
    """在矩形内绘制折行文本，返回实际行数。"""
    lines = wrap_text(text, font, rect.width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = lines[-1][:-1] + "…"
    line_h = font.get_linesize()
    y = rect.y
    for line in lines:
        if y + line_h > rect.bottom + 2:
            break
        draw_text(surface, line, font, color_value, (rect.x, y))
        y += line_h + line_gap
    return len(lines)


def truncate(text: str, font: pygame.font.Font, max_width: int) -> str:
    """超出宽度时截断并加省略号。"""
    if not text:
        return ""
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    ell_w = font.size(ellipsis)[0]
    if ell_w > max_width:
        return ""
    result = ""
    for ch in text:
        if font.size(result + ch)[0] + ell_w > max_width:
            break
        result += ch
    return result + ellipsis


def lighten(rgb: tuple[int, int, int], amount: float = 0.15) -> tuple[int, int, int]:
    return (
        min(255, int(rgb[0] + (255 - rgb[0]) * amount)),
        min(255, int(rgb[1] + (255 - rgb[1]) * amount)),
        min(255, int(rgb[2] + (255 - rgb[2]) * amount)),
    )


def darken(rgb: tuple[int, int, int], amount: float = 0.2) -> tuple[int, int, int]:
    return (
        max(0, int(rgb[0] * (1.0 - amount))),
        max(0, int(rgb[1] * (1.0 - amount))),
        max(0, int(rgb[2] * (1.0 - amount))),
    )


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )
