"""主题与设计系统：颜色、字体、间距、控件度量、绘制辅助。

这里是全项目**唯一**的视觉语言出口：
- 颜色、间距（SPACE）、圆角（RADIUS）、按钮高度（BTN_H）、字号（FONT）
  都以命名常量给出，场景里不允许再出现散落的 magic number；
- 组合组件（panel / page_title / section / chip / stat / kv_row）统一在这里实现，
  避免每个 Scene 自己画一套「标题 + 分隔线 + 面板」；
- 字体策略：在系统字体里按优先级查找可用的中文字体，
  绝不绑定某台机器的绝对路径；找不到时退回 pygame 默认字体并给出提示。
  需要符号时不要用文字符号（`▶` `▮` 会变成豆腐块），统一走 `icons.draw_icon`。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

import pygame

from ..utils.logging_setup import get_logger

log = get_logger(__name__)

# ==================================================================== 度量 token

#: 间距阶梯（4 的倍数，全项目统一）
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32, "huge": 48}

#: 圆角阶梯
RADIUS = {"xs": 6, "sm": 8, "md": 10, "lg": 12, "xl": 16, "xxl": 20, "pill": 999}

#: 按钮高度
BTN_H = {"sm": 32, "md": 44, "lg": 56, "xl": 68}

#: 字号阶梯（按 1600×900 逻辑分辨率设计）
FONT = {
    "title": 56, "huge": 40, "h1": 34, "big": 30, "h2": 24, "money": 22,
    "h3": 20, "body": 17, "button": 19, "small": 15, "tiny": 13, "micro": 11,
}

#: 标准内容区（页面左右留白 / 起始 y）
PAGE_X = 120
PAGE_TOP = 72
CONTENT_RIGHT = 1480

#: 语义色（供 need_color() 使用）：把「语义」映射到具体颜色名
SEMANTIC = {
    "neutral": "text",
    "muted": "text_dim",
    "positive": "success",
    "negative": "danger",
    "warning": "warning",
    "accent": "accent",
    "info": "info",
}


def need_color(name: str | None) -> str:
    return SEMANTIC.get(name or "", name or "text")

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
    "t_fortune": (72, 190, 150),
    "t_disaster": (226, 106, 96),
    "t_tax": (232, 116, 100),
    "t_jail": (140, 148, 164),
    "t_go_jail": (216, 108, 120),
    "t_shop": (238, 158, 88),
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
    "FORTUNE": "t_fortune",
    "DISASTER": "t_disaster",
    "TAX": "t_tax",
    "JAIL": "t_jail",
    "GO_TO_JAIL": "t_go_jail",
    "SHOP": "t_shop",
    "PARK": "t_park",
    "BONUS": "t_bonus",
}

#: 格子类型 → 矢量图标名（icons.py）。这是「不同类型格子有不同图形」的唯一来源。
TILE_TYPE_ICONS = {
    "START": "start",
    "PROPERTY": "property",
    "STATION": "station",
    "CHANCE": "star",
    "FORTUNE": "fortune",
    "DISASTER": "disaster",
    "TAX": "tax",
    "JAIL": "jail",
    "GO_TO_JAIL": "police",
    "SHOP": "shop",
    "PARK": "park",
    "BONUS": "trophy",
}

#: 片区配色（按片区名稳定分配，同一张地图内不重复）
DISTRICT_PALETTE = [
    (96, 156, 232),   # 蓝
    (232, 122, 96),   # 砖红
    (108, 196, 140),  # 绿
    (196, 138, 232),  # 紫
    (236, 178, 72),   # 金
    (86, 190, 200),   # 青
    (226, 118, 168),  # 粉
    (150, 170, 96),   # 橄榄
    (128, 140, 224),  # 靛
    (214, 146, 92),   # 棕橙
    (108, 176, 116),  # 深绿
    (208, 106, 118),  # 玫红
    (124, 164, 196),  # 灰蓝
    (176, 148, 96),   # 卡其
]


def district_palette(district_names: Sequence[str]) -> dict[str, tuple[int, int, int]]:
    """给一组片区名分配稳定且互不重复的颜色。

    顺序按传入顺序（调用方应传入地图中片区首次出现的顺序），
    这样同一张地图每次运行的颜色完全一致，玩家能形成记忆。
    """
    out: dict[str, tuple[int, int, int]] = {}
    for i, name in enumerate(district_names):
        out[name] = DISTRICT_PALETTE[i % len(DISTRICT_PALETTE)]
    return out


def tile_color(tile_type_value: str) -> tuple[int, int, int]:
    """格子类型色。未知类型退回地产色，但不会 KeyError。"""
    return color(TILE_TYPE_COLORS.get(tile_type_value, "t_property"))


def tile_icon(tile_type_value: str) -> str:
    return TILE_TYPE_ICONS.get(tile_type_value, "property")


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


#: 渐变缓存：全屏渐变每帧重画要 900 次 draw.line，
#: 而它的内容只取决于尺寸与两个端点色，因此缓存下来直接用 blit。
_GRADIENT_CACHE: dict[tuple, pygame.Surface] = {}
_GRADIENT_CACHE_ORDER: list[tuple] = []
_GRADIENT_CACHE_LIMIT = 12


def vgradient(surface: pygame.Surface, rect: pygame.Rect,
              top: tuple[int, ...], bottom: tuple[int, ...]) -> None:
    """竖直渐变填充（带缓存）。"""
    rect = pygame.Rect(rect)
    if rect.height <= 0 or rect.width <= 0:
        return
    key = (rect.width, rect.height, tuple(top[:3]), tuple(bottom[:3]))
    cached = _GRADIENT_CACHE.get(key)
    if cached is None:
        cached = pygame.Surface((rect.width, rect.height))
        for y in range(rect.height):
            t = y / max(1, rect.height - 1)
            col = (
                int(top[0] + (bottom[0] - top[0]) * t),
                int(top[1] + (bottom[1] - top[1]) * t),
                int(top[2] + (bottom[2] - top[2]) * t),
            )
            pygame.draw.line(cached, col, (0, y), (rect.width - 1, y))
        _GRADIENT_CACHE[key] = cached
        _GRADIENT_CACHE_ORDER.append(key)
        while len(_GRADIENT_CACHE_ORDER) > _GRADIENT_CACHE_LIMIT:
            _GRADIENT_CACHE.pop(_GRADIENT_CACHE_ORDER.pop(0), None)
    surface.blit(cached, rect.topleft)


def clear_caches() -> None:
    """丢弃绘制缓存（改变字体 / 主题后调用）。"""
    _GRADIENT_CACHE.clear()
    _GRADIENT_CACHE_ORDER.clear()


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


class Fonts:
    """字号语义名 → FontManager 查询的便捷包装。

    场景里写 `fonts.body()` 之类没问题，但新代码更推荐 `T.body`，
    这样字号阶梯只在一处定义。
    """

    def __init__(self, manager: "FontManager") -> None:
        self._m = manager

    def get(self, key: str, bold: bool | None = None) -> pygame.font.Font:
        size = FONT.get(key, FONT["body"])
        if bold is None:
            bold = key in ("title", "huge", "h1", "big", "h2", "h3", "money", "button")
        return self._m.sized(size, bold)


def styled(fonts: "FontManager", key: str, bold: bool | None = None) -> pygame.font.Font:
    """按字号 token 取字体：styled(fonts, "h3") / styled(fonts, "body", bold=False)。"""
    return Fonts(fonts).get(key, bold)


# ==================================================================== 组合组件

def panel(
    surface: pygame.Surface,
    rect: pygame.Rect | Sequence[int],
    *,
    fill: str = "panel",
    border: str | None = "border_soft",
    radius: int = RADIUS["xl"],
    alpha: int | None = None,
    shadow: bool = False,
) -> pygame.Rect:
    """统一的面板底板。所有卡片 / 分组 / 侧栏都用它，保证圆角与描边一致。"""
    r = pygame.Rect(rect)
    if shadow:
        shadow_rect(surface, r, radius=radius, spread=6, alpha=140)
    fill_value = color(fill, alpha) if alpha is not None else color(fill)
    rounded_rect(surface, r, fill_value, radius=radius)
    if border:
        rounded_rect(surface, r, None, radius=radius, border=color(border), border_width=1)
    return r


def divider(surface: pygame.Surface, rect: pygame.Rect | Sequence[int], y: int | None = None,
            inset: int = 0, color_name: str = "border_soft") -> None:
    """面板内的水平分隔线。"""
    r = pygame.Rect(rect)
    line_y = r.y if y is None else y
    pygame.draw.line(surface, color(color_name),
                     (r.x + inset, line_y), (r.right - inset, line_y), 1)


def page_content_top(fonts: "FontManager", subtitle: str = "",
                     y: int = PAGE_TOP) -> int:
    """与 `page_title` 完全一致的内容区起始 y（不绘制）。

    场景在 `_build()` 里排布控件时需要先知道这个值，
    因此把计算单独抽出来，避免「标题画在哪」和「内容排在哪」两套算法打架。
    """
    top = y + fonts.sized(FONT["h1"], True).get_linesize() + SPACE["xs"]
    if subtitle:
        top += fonts.sized(FONT["small"]).get_linesize()
    return top + SPACE["md"] + 1 + SPACE["lg"]


def page_title(
    surface: pygame.Surface,
    fonts: "FontManager",
    title: str,
    subtitle: str = "",
    *,
    x: int = PAGE_X,
    y: int = PAGE_TOP,
    divider_after: bool = True,
    right: int = CONTENT_RIGHT,
) -> int:
    """页面顶部标题块，返回内容区的起始 y。

    这样写是为了根治「标题与副标题叠字」——
    副标题的位置由标题的实际行高算出来，而不是各写一个 magic number。
    """
    title_font = fonts.sized(FONT["h1"], True)
    rect = draw_text(surface, title, title_font, color("text"), (x, y))
    next_y = rect.bottom + SPACE["xs"]
    if subtitle:
        sub_font = fonts.sized(FONT["small"])
        draw_text(surface, subtitle, sub_font, color("text_dim"), (x + 2, next_y))
        next_y += sub_font.get_linesize()
    next_y += SPACE["md"]
    if divider_after:
        pygame.draw.line(surface, color("border_soft"), (x, next_y), (right, next_y), 1)
    return page_content_top(fonts, subtitle, y)


def section_header(
    surface: pygame.Surface,
    fonts: "FontManager",
    rect: pygame.Rect | Sequence[int],
    title: str,
    *,
    icon: str | None = None,
    accent: str = "text",
    note: str = "",
    note_color: str = "text_mute",
) -> int:
    """面板内的小标题行，返回其下内容起始 y。"""
    r = pygame.Rect(rect)
    x = r.x
    if icon:
        from . import icons

        icons.draw_icon(surface, icon, pygame.Rect(x, r.y + 1, 18, 18), color(accent))
        x += 26
    font = fonts.sized(FONT["h3"], True)
    text_rect = draw_text(surface, title, font, color(accent), (x, r.y))
    if note:
        note_font = fonts.sized(FONT["tiny"])
        draw_text(surface, note, note_font, color(note_color),
                  (r.right, r.y + 5), anchor="topright")
    return max(text_rect.bottom, r.y + 22)


def chip(
    surface: pygame.Surface,
    fonts: "FontManager",
    rect: pygame.Rect | Sequence[int],
    text: str,
    color_name: str = "text_dim",
    *,
    filled: bool = False,
    font_key: str = "micro",
    radius: int = RADIUS["xs"],
) -> pygame.Rect:
    """小标签（已破产 / 已准备 / 关押 2/3 / 垄断）。"""
    r = pygame.Rect(rect)
    accent = color(color_name)
    if filled:
        rounded_rect(surface, r, color(color_name, 70), radius=radius)
    rounded_rect(surface, r, None, radius=radius, border=accent, border_width=1)
    font = fonts.sized(FONT[font_key], True)
    draw_text(surface, truncate(text, font, r.width - 8), font, accent,
              r.center, anchor="center")
    return r


def chip_width(fonts: "FontManager", text: str, font_key: str = "micro",
               padding: int = 12) -> int:
    return fonts.sized(FONT[font_key], True).size(text)[0] + padding


def stat(
    surface: pygame.Surface,
    fonts: "FontManager",
    x: int,
    y: int,
    label: str,
    value: str,
    *,
    color_name: str = "text",
    label_color: str = "text_mute",
    value_key: str = "h3",
    width: int = 0,
) -> int:
    """「标签在上、数值在下」的小统计块，返回块宽度。"""
    label_font = fonts.sized(FONT["tiny"])
    value_font = fonts.sized(FONT[value_key], True)
    draw_text(surface, label, label_font, color(label_color), (x, y))
    draw_text(surface, value, value_font, color(color_name), (x, y + label_font.get_linesize() + 2))
    return width or max(label_font.size(label)[0], value_font.size(value)[0])


def kv_row(
    surface: pygame.Surface,
    fonts: "FontManager",
    rect: pygame.Rect | Sequence[int],
    y: int,
    label: str,
    value: str,
    *,
    label_color: str = "text_dim",
    value_color: str = "text",
    value_key: str = "small",
    row_h: int = 22,
    bold_value: bool = False,
) -> int:
    """左标签右数值的标准信息行，返回值行的 y 步进。"""
    r = pygame.Rect(rect)
    label_font = fonts.sized(FONT["small"])
    value_font = fonts.sized(FONT[value_key], bold_value)
    draw_text(surface, label, label_font, color(label_color), (r.x, y))
    draw_text(surface, truncate(value, value_font, max(40, r.width - label_font.size(label)[0] - 20)),
              value_font, color(value_color), (r.right, y), anchor="topright")
    return row_h


def progress_bar(
    surface: pygame.Surface,
    rect: pygame.Rect | Sequence[int],
    ratio: float,
    *,
    color_name: str = "accent",
    track: str = "bg_alt",
    radius: int | None = None,
) -> None:
    """细进度条（债务筹资进度 / 血量 / 进度提示）。"""
    r = pygame.Rect(rect)
    radius = r.height // 2 if radius is None else radius
    rounded_rect(surface, r, color(track), radius=radius)
    ratio = max(0.0, min(1.0, ratio))
    w = int(r.width * ratio)
    if w >= 2:
        rounded_rect(surface, pygame.Rect(r.x, r.y, w, r.height), color(color_name),
                     radius=radius)


def money_text(value: int | float, *, sign: bool = False) -> str:
    """统一金额文本：¥ 15,000 / + ¥ 800 / - ¥ 800。"""
    from ..game.format import money, money_delta

    return money_delta(value) if sign else money(value)


def money_color_name(value: int | float, *, income_positive: bool = True) -> str:
    if value > 0:
        return "success" if income_positive else "danger"
    if value < 0:
        return "danger" if income_positive else "success"
    return "text_mute"
