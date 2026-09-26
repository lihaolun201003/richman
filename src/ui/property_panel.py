"""地产信息面板与悬浮提示。

展示：名称、所属区、价格、所有者、等级、当前租金、下一级租金、升级费用、
垄断状态。不修改任何状态，纯展示 + 通过回调转发操作意图。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game import economy
from ..game.player import Player
from ..game.property import Property
from ..game.state import GameState
from ..game.tile import TileType
from . import theme

LEVEL_BLOCKS = ["○", "▮", "▮▮", "▮▮▮"]


def property_tooltip(state: GameState, prop: Property) -> str:
    """生成地产悬浮提示文本（多行）。"""
    lines: list[str] = [prop.name]
    if prop.district:
        owned_all = state.district_owned_all(prop.owner_id, prop.district) if prop.owner_id else False
        lines.append(f"所属：{prop.district}" + ("（已垄断，租金 ×2）" if owned_all else ""))
    else:
        lines.append("所属：独立地产")

    kind_label = "交通枢纽" if prop.kind == "STATION" else "地产"
    lines.append(f"类型：{kind_label}    地价：{prop.price:,}")

    owner = state.player(prop.owner_id) if prop.owner_id else None
    if owner is None:
        lines.append("所有者：无主（可购买）")
    else:
        lines.append(f"所有者：{owner.name}")

    lines.append(f"等级：{prop.level}/{prop.max_level}  {prop.level_name}")
    preview = economy.rent_preview(state, prop)
    rent_text = f"当前租金：{preview['current']:,}"
    if preview["district_bonus"]:
        rent_text += f"（基础 {prop.base_rent:,}）"
    lines.append(rent_text)

    if not prop.is_max_level:
        lines.append(f"升级后租金：{preview['next']:,}")
        cost = prop.next_upgrade_cost or 0
        lines.append(f"升级费用：{cost:,}")
    else:
        lines.append("已是最高等级")

    if owner is not None and prop.tile_index >= 0:
        pass
    # 购买价提示
    if prop.owner_id is None:
        lines.append(f"售价：{prop.price:,}（购买后即可收租）")
    return "\n".join(lines)


class PropertyInfoPanel:
    """右侧的「当前地产」信息卡。"""

    def __init__(self, rect: pygame.Rect, title: str = "地块信息") -> None:
        self.rect = pygame.Rect(rect)
        self.title = title
        self.prop: Property | None = None
        self.tile_index: int | None = None

    def set_tile(self, state: GameState | None, tile_index: int | None) -> None:
        self.tile_index = tile_index
        if state is None or tile_index is None:
            self.prop = None
            return
        self.prop = state.property_at(tile_index)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager,
             state: GameState | None) -> None:
        rect = self.rect
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, self.title, fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 12))

        if state is None or self.tile_index is None:
            theme.draw_text(surface, "把鼠标移到棋盘上查看地块详情",
                            fonts.small(), theme.color("text_mute"),
                            (rect.centerx, rect.y + rect.height // 2), anchor="center")
            return

        tile = state.board.tile(self.tile_index)
        y = rect.y + 44
        accent = theme.color(theme.TILE_TYPE_COLORS.get(tile.type.value, "t_property"))

        # 名称与类型
        theme.rounded_rect(surface, pygame.Rect(rect.x + 14, y, 5, 26), accent, radius=2)
        theme.draw_text(surface, theme.truncate(tile.name, fonts.h2(), rect.width - 50),
                        fonts.h2(), theme.color("text"), (rect.x + 28, y + 2))
        y += 34
        theme.draw_text(surface, tile.type.label, fonts.tiny(), accent, (rect.x + 28, y))
        if tile.note:
            note_font = fonts.tiny()
            theme.draw_wrapped(surface, tile.note, note_font, theme.color("text_mute"),
                               pygame.Rect(rect.x + 28, y, rect.width - 44, 34))
        y += 30

        prop = self.prop
        if prop is None:
            theme.draw_text(surface, "该地块不可购买", fonts.small(),
                            theme.color("text_dim"), (rect.x + 16, y + 8))
            return

        owner = state.player(prop.owner_id) if prop.owner_id else None
        owner_color = theme.color("text_dim")
        if owner is not None:
            from .player_panel import _color_of

            owner_color = theme.hex_to_rgb(_color_of(owner.color_id))

        rows = [
            ("地价", f"{prop.price:,}", "text"),
            ("所有者", owner.name if owner else "无主", None),
            ("等级", f"{prop.level}/{prop.max_level} {prop.level_name}", "text"),
        ]
        preview = economy.rent_preview(state, prop)
        rows.append(("当前租金", f"{preview['current']:,}", "accent"))
        if preview["district_bonus"]:
            rows.append(("", f"└ {preview['bonus_reason']} 加成", "success"))
        if not prop.is_max_level:
            rows.append(("升级后", f"{preview['next']:,}", "text_dim"))
            rows.append(("升级费用", f"{prop.next_upgrade_cost or 0:,}", "warning"))
        else:
            rows.append(("", "已达最高等级", "success"))

        for label, value, color_name in rows:
            if label:
                theme.draw_text(surface, label, fonts.small(), theme.color("text_dim"),
                                (rect.x + 18, y))
            if color_name == "owner":
                color_value = owner_color
            elif color_name is None:
                color_value = owner_color
            else:
                color_value = theme.color(color_name)
            theme.draw_text(surface, value, fonts.small(), color_value,
                            (rect.right - 18, y), anchor="topright")
            y += 22

        # 等级可视化
        blocks = "▮" * prop.level + "▯" * (prop.max_level - prop.level)
        theme.draw_text(surface, blocks, fonts.sized(18, True),
                        accent if prop.level else theme.color("text_mute"),
                        (rect.x + 18, rect.bottom - 30))
