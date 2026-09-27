"""角色展示：把 data/characters.json 里的数值变成「玩家看得懂的能力卡」。

设计原则（重要）：
- **只可视化真实数据**。所有文字与条形都从 `perk.modifiers` / `perk.special`
  反推出来，绝不写死「擅长发育」这类文案去美化一个不存在的效果；
- 每个角色都有一张完整的能力卡：头像 / 名字 / 称号 / 被动 / 一句话风格 /
  AI 性格 / 能力倾向条；
- 头像用程序化几何图形（同心环 + 首字 + 形状差异），不依赖任何美术资源，
  也不会有版权问题。
"""
from __future__ import annotations

import math
from typing import Any

import pygame

from ..game.setup import character_by_id, load_characters
from . import icons, theme

#: Hook → (中文说明模板, 「越好越有利」还是「越低越有利」)
HOOK_LABELS: dict[str, tuple[str, str]] = {
    "modify_purchase_price": ("购买地产支出", "lower"),
    "modify_upgrade_cost": ("地产升级支出", "lower"),
    "modify_rent_received": ("收取租金", "higher"),
    "modify_rent_paid": ("支付租金", "lower"),
    "modify_jail_cost": ("保释金支出", "lower"),
    "modify_shop_price": ("商店购买支出", "lower"),
    "modify_sell_refund": ("卖地回收", "higher"),
    "modify_mortgage_value": ("抵押可得现金", "higher"),
    "modify_tax": ("缴纳税款", "lower"),
    "modify_start_reward": ("经过起点奖励", "higher"),
}

#: special 被动 → (中文说明, 数值文本)
SPECIAL_LABELS: dict[str, str] = {
    "start_money_bonus": "起始资金",
    "event_gain_bonus": "机遇事件收益",
}

#: AI 人格 → (名称, 一句话, 颜色)
PERSONALITY_STYLE = {
    "steady": ("稳健型 AI", "保守留现金，少冒险", "info"),
    "balanced": ("均衡型 AI", "攻守平衡，默认策略", "accent"),
    "aggressive": ("激进型 AI", "猛买地猛升级，现金留得少", "danger"),
}


class Effect:
    """一个可展示的能力影响。"""

    __slots__ = ("label", "value_text", "good", "magnitude")

    def __init__(self, label: str, value_text: str, good: bool, magnitude: float) -> None:
        self.label = label
        self.value_text = value_text
        self.good = good
        self.magnitude = magnitude      # (0, 1]，用于画倾向条


def character_effects(char: dict[str, Any]) -> list[Effect]:
    """从真实数据推导出可展示的能力列表。"""
    out: list[Effect] = []
    perk = char.get("perk") or {}
    for mod in perk.get("modifiers") or []:
        hook = mod.get("hook", "")
        op = mod.get("op", "mul")
        value = float(mod.get("value", 0) or 0)
        label, better = HOOK_LABELS.get(hook, (hook, "higher"))
        if op == "mul":
            text = f"{value * 100:+.0f}%"
            magnitude = min(1.0, abs(value) / 0.5)
        elif op == "add":
            text = f"{value:+,.0f}"
            magnitude = min(1.0, abs(value) / 2000.0)
        else:
            text = f"{value:g}"
            magnitude = 0.4
        good = (value >= 0) if better == "higher" else (value <= 0)
        out.append(Effect(label, text, good, max(0.18, magnitude)))
    special = perk.get("special")
    if special:
        value = float(perk.get("value", 0) or 0)
        label = SPECIAL_LABELS.get(special, special)
        if special.endswith("_bonus") and "money" not in special:
            text = f"{value * 100:+.0f}%"
            magnitude = min(1.0, abs(value) / 0.5)
        else:
            text = f"{value:+,.0f}"
            magnitude = min(1.0, abs(value) / 2000.0)
        out.append(Effect(label, text, value >= 0, max(0.18, magnitude)))
    return out


def all_characters() -> list[dict[str, Any]]:
    return list(load_characters()["characters"])


def personality_style(char: dict[str, Any]) -> tuple[str, str, str]:
    return PERSONALITY_STYLE.get(char.get("personality", "balanced"),
                                 PERSONALITY_STYLE["balanced"])


# ==================================================================== 头像

def draw_avatar(surface: pygame.Surface, rect: pygame.Rect, char: dict[str, Any],
                fonts: theme.FontManager, *, alpha: int = 255,
                selected: bool = False) -> None:
    """程序化角色头像：底色 + 几何纹样 + 首字。

    不同角色用不同的「环数 + 弧度」区分，因此即使打印成灰度图也能分辨。
    """
    r = pygame.Rect(rect)
    color = theme.hex_to_rgb(char.get("theme_color", "#8899AA"))
    layer = pygame.Surface(r.size, pygame.SRCALPHA)
    local = pygame.Rect(0, 0, r.width, r.height)
    radius = min(local.width, local.height) // 2

    pygame.draw.circle(layer, (*color, alpha), local.center, radius)
    pygame.draw.circle(layer, (*theme.darken(color, 0.45), alpha), local.center, radius, 2)

    # 几何纹样：环数由 id 决定（2～4 道），形成区分度
    seed = sum(ord(c) for c in char.get("id", "")) % 3
    rings = 2 + seed
    for i in range(rings):
        rr = int(radius * (0.78 - i * 0.16))
        if rr <= 2:
            break
        shade = theme.lighten(color, 0.10 + i * 0.06)
        pygame.draw.circle(layer, (*shade, int(alpha * 0.55)), local.center, rr, 1)
    # 下半圈弧线：像「肩膀」，让头像不是纯圆
    pygame.draw.arc(layer, (*theme.darken(color, 0.25), alpha),
                    pygame.Rect(local.x, local.centery, local.width, local.height),
                    math.pi, math.tau, max(2, radius // 6))

    surface.blit(layer, r.topleft)
    name = char.get("name_cn", "?")
    theme.draw_text(surface, name[0], fonts.sized(max(11, int(radius * 0.9)), True),
                    (255, 255, 255), r.center, anchor="center")


# ==================================================================== 网格

class CharacterGallery:
    """角色网格：12 张卡一次看全，选中一张看详情。"""

    def __init__(self, rect: pygame.Rect, selected: str, on_change,
                 columns: int = 4, card_h: int = 74) -> None:
        self.rect = pygame.Rect(rect)
        self.selected = selected
        self.on_change = on_change
        self.chars = all_characters()
        self.columns = columns
        self.card_h = card_h
        self.hover: int | None = None
        self._scroll = 0.0

    # ---- 几何
    @property
    def rows(self) -> int:
        return max(1, math.ceil(len(self.chars) / self.columns))

    def _card_rect(self, index: int) -> pygame.Rect:
        gap = theme.SPACE["sm"]
        w = (self.rect.width - gap * (self.columns - 1)) // self.columns
        col = index % self.columns
        row = index // self.columns
        return pygame.Rect(self.rect.x + col * (w + gap),
                           self.rect.y + row * (self.card_h + gap), w, self.card_h)

    def content_height(self) -> int:
        return self.rows * (self.card_h + theme.SPACE["sm"])

    # ---- 交互
    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hover = None
            for i in range(len(self.chars)):
                if self._card_rect(i).collidepoint(event.pos):
                    self.hover = i
                    break
        elif event.type == pygame.MOUSEWHEEL:
            if self.rect.collidepoint(pygame.mouse.get_pos()):
                max_scroll = max(0.0, self.content_height() - self.rect.height)
                self._scroll = max(0.0, min(max_scroll, self._scroll - event.y * 48))
                return True
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i in range(len(self.chars)):
                if self._card_rect(i).collidepoint(event.pos):
                    self.selected = self.chars[i]["id"]
                    self.on_change(self.selected)
                    return True
        return False

    # ---- 绘制
    def draw(self, surface: pygame.Surface, fonts: theme.FontManager,
             tooltip_out: list[str] | None = None) -> None:
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        for i, char in enumerate(self.chars):
            rect = self._card_rect(i).move(0, -int(self._scroll))
            if rect.bottom < self.rect.y - 4 or rect.y > self.rect.bottom + 4:
                continue
            self._draw_card(surface, fonts, rect, char,
                            char["id"] == self.selected, i == self.hover)
        surface.set_clip(clip)
        if tooltip_out is not None and self.hover is not None:
            char = self.chars[self.hover]
            effects = character_effects(char)
            lines = [f"{char['name_cn']} · {char['title']}", char["perk"]["desc"],
                     char["bio"]]
            lines += [f"{e.label} {e.value_text}" for e in effects]
            tooltip_out.append("\n".join(lines))

    def _draw_card(self, surface: pygame.Surface, fonts: theme.FontManager,
                   rect: pygame.Rect, char: dict[str, Any],
                   active: bool, hovered: bool) -> None:
        color = theme.hex_to_rgb(char.get("theme_color", "#8899AA"))
        fill = theme.mix(theme.color("panel"), color, 0.36 if active else
                         (0.18 if hovered else 0.08))
        theme.rounded_rect(surface, rect, fill, radius=theme.RADIUS["lg"])
        theme.rounded_rect(surface, rect, None, radius=theme.RADIUS["lg"],
                           border=color if active else theme.color("border_soft"),
                           border_width=3 if active else 1)

        avatar_size = rect.height - 20
        avatar = pygame.Rect(rect.x + 10, rect.centery - avatar_size // 2,
                             avatar_size, avatar_size)
        draw_avatar(surface, avatar, char, fonts)

        text_x = avatar.right + 10
        text_w = rect.right - text_x - 8
        theme.draw_text(surface, theme.truncate(char["name_cn"], fonts.sized(17, True),
                                                text_w),
                        fonts.sized(17, True), theme.color("text"), (text_x, rect.y + 8))
        theme.draw_text(surface, theme.truncate(char["title"], fonts.micro(), text_w),
                        fonts.micro(), theme.color("text_mute"), (text_x, rect.y + 28))
        perk_font = fonts.sized(11, True)
        theme.draw_wrapped(surface, char["perk"]["desc"], perk_font,
                           theme.color("accent" if active else "text_dim"),
                           pygame.Rect(text_x, rect.y + 44, text_w,
                                       rect.bottom - rect.y - 46), line_gap=1, max_lines=2)


# ==================================================================== 能力卡

def draw_character_card(surface: pygame.Surface, fonts: theme.FontManager,
                        rect: pygame.Rect, char_id: str,
                        *, show_ai: bool = True) -> None:
    """完整角色卡：头像 / 名字 / 称号 / 被动能力 / 风格 / AI 性格。

    内容按 rect 高度自适应排布，因此 160～260 高都能正常显示，
    不会出现「文字压在头像上」或「AI 提示盖住被动」。
    """
    char = character_by_id(char_id)
    theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
    if char is None:
        theme.draw_text(surface, "未选择角色", fonts.body(), theme.color("text_mute"),
                        rect.center, anchor="center")
        return
    color = theme.hex_to_rgb(char.get("theme_color", "#8899AA"))
    theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 6), color,
                       radius=3)

    avatar_size = 64 if rect.height < 200 else 76
    avatar = pygame.Rect(rect.x + 18, rect.y + 18, avatar_size, avatar_size)
    draw_avatar(surface, avatar, char, fonts)

    theme.draw_text(surface, char["name_cn"], fonts.sized(24, True), theme.color("text"),
                    (avatar.right + 16, rect.y + 16))
    theme.draw_text(surface, char["title"], fonts.small(), color,
                    (avatar.right + 18, rect.y + 46))
    theme.draw_text(surface, theme.truncate(char["bio"], fonts.tiny(),
                                            rect.right - avatar.right - 34),
                    fonts.tiny(), theme.color("text_dim"), (avatar.right + 18, rect.y + 68))

    y = max(avatar.bottom, rect.y + 92) + 10
    for eff in character_effects(char)[:2]:
        row = pygame.Rect(rect.x + 18, y, rect.width - 36, 30)
        theme.rounded_rect(surface, row, theme.color("bg_alt", 170), radius=8)
        color_name = "success" if eff.good else "warning"
        theme.draw_text(surface, eff.label, fonts.small(), theme.color("text"),
                        (row.x + 12, row.centery), anchor="midleft")
        theme.draw_text(surface, eff.value_text, fonts.sized(16, True),
                        theme.color(color_name), (row.right - 12, row.centery),
                        anchor="midright")
        bar = pygame.Rect(row.x + 12, row.bottom - 5,
                          int((row.width - 24) * eff.magnitude), 3)
        theme.rounded_rect(surface, bar, theme.color(color_name, 200), radius=2)
        y += 34

    if show_ai:
        ai_name, ai_desc, ai_color = personality_style(char)
        ai_line = f"AI 接管时：{ai_name} · {ai_desc}"
        theme.draw_text(surface, theme.truncate(ai_line, fonts.micro(), rect.width - 40),
                        fonts.micro(), theme.color(ai_color),
                        (rect.x + 18, rect.bottom - 22))
