"""地产信息面板与悬浮提示。

展示：名称、所属区、价格、所有者、等级、当前租金、下一级租金、升级费用、
片区完成度、抵押状态。不修改任何状态，纯展示 + 通过回调转发操作意图。

v0.3 的改动：
- 行距与字号统一走 theme 的 token，不再出现「最后两行叠在一起」；
- 无悬停时显示**当前玩家所在地块**（而不是一句废话），永远有信息可看；
- 加入片区完成度进度条与抵押状态，这两项是买地 / 升级决策的关键。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game import economy
from ..game.player import Player
from ..game.property import Property
from ..game.state import GameState
from ..game.tile import TileType
from . import icons, theme

LEVEL_BLOCKS = ["○", "▮", "▮▮", "▮▮▮"]


def property_tooltip(state: GameState, prop: Property) -> str:
    """生成地产悬浮提示文本（多行）。"""
    lines: list[str] = [prop.name]
    if prop.district:
        owned_all = state.district_owned_all(prop.owner_id, prop.district) if prop.owner_id else False
        lines.append(f"所属片区：{prop.district}" + ("（已垄断，租金 ×2）" if owned_all else ""))
        ids = (state.district_props or {}).get(prop.district, [])
        if ids:
            owner = state.player(prop.owner_id) if prop.owner_id else None
            if owner is not None:
                owned = sum(1 for pid in ids
                            if state.properties.get(pid) is not None
                            and state.properties[pid].owner_id == owner.id)
                lines.append(f"该片区进度：{owner.name} 持有 {owned}/{len(ids)}")
    else:
        lines.append("所属片区：独立地产")

    kind_label = "交通枢纽" if prop.kind == "STATION" else "地产"
    lines.append(f"类型：{kind_label}　地价：{prop.price:,}")

    owner = state.player(prop.owner_id) if prop.owner_id else None
    if owner is None:
        lines.append("所有者：无主（可购买）")
    else:
        lines.append(f"所有者：{owner.name}" + ("（抵押中）" if prop.mortgaged else ""))

    lines.append(f"等级：{prop.level}/{prop.max_level}　{prop.level_name}")
    preview = economy.rent_preview(state, prop)
    if prop.mortgaged:
        lines.append("当前租金：0（抵押中）")
        lines.append(f"赎回需要：{economy.redeem_cost(prop):,}")
    else:
        rent_text = f"当前租金：{preview['current']:,}"
        if preview["district_bonus"]:
            rent_text += f"（基础 {prop.base_rent:,}，垄断加成）"
        lines.append(rent_text)

    if not prop.is_max_level:
        lines.append(f"升级后租金：{preview['next']:,}")
        lines.append(f"升级费用：{prop.next_upgrade_cost or 0:,}")
    else:
        lines.append("已是最高等级")

    if prop.owner_id is None:
        lines.append(f"售价：{prop.price:,}（购买后即可收租）")
    return "\n".join(lines)


class PropertyInfoPanel:
    """右侧的「地块信息」卡。"""

    def __init__(self, rect: pygame.Rect, title: str = "地块信息") -> None:
        self.rect = pygame.Rect(rect)
        self.title = title
        self.prop: Property | None = None
        self.tile_index: int | None = None
        self.following: int | None = None      # 是否在跟随当前玩家

    def set_tile(self, state: GameState | None, tile_index: int | None) -> None:
        self.tile_index = tile_index
        self.following = None
        if state is None or tile_index is None:
            self.prop = None
            return
        self.prop = state.property_at(tile_index)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager,
             state: GameState | None) -> None:
        rect = self.rect
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        mouse = pygame.mouse.get_pos()
        hovering = rect.collidepoint(mouse)
        theme.section_header(surface, fonts,
                            pygame.Rect(rect.x + 16, rect.y + 10, rect.width - 32, 22),
                            self.title, icon="info",
                            note="" if hovering else "跟随当前玩家")

        if state is None or self.tile_index is None:
            theme.draw_text(surface, "把鼠标移到棋盘上查看地块详情",
                            fonts.small(), theme.color("text_mute"),
                            (rect.centerx, rect.centery), anchor="center")
            return

        tile = state.board.tile(self.tile_index)
        y = rect.y + 42
        accent = theme.tile_color(tile.type.value)

        theme.rounded_rect(surface, pygame.Rect(rect.x + 14, y, 5, 24), accent, radius=2)
        icons.draw_icon(surface, theme.tile_icon(tile.type.value),
                        pygame.Rect(rect.right - 40, y, 22, 22), accent,
                        theme.color("shadow"))
        theme.draw_text(surface, theme.truncate(tile.name, fonts.h2(), rect.width - 90),
                        fonts.h2(), theme.color("text"), (rect.x + 28, y))
        y += 28
        theme.draw_text(surface, tile.type.label, fonts.tiny(), accent, (rect.x + 28, y))
        y += 20

        prop = self.prop
        if prop is None:
            note = tile.note or "该地块不可购买"
            theme.draw_wrapped(surface, note, fonts.small(), theme.color("text_dim"),
                               pygame.Rect(rect.x + 18, y + 4, rect.width - 36,
                                           rect.bottom - y - 10))
            return

        owner = state.player(prop.owner_id) if prop.owner_id else None
        owner_color = theme.hex_to_rgb(_color_of(owner.color_id)) if owner else \
            theme.color("text_mute")

        # 片区完成度
        if prop.district:
            ids = (state.district_props or {}).get(prop.district, [])
            if ids:
                owned = sum(1 for pid in ids
                            if state.properties.get(pid) is not None
                            and state.properties[pid].owner_id == prop.owner_id)
                theme.draw_text(surface, f"{prop.district}　{owned}/{len(ids)}",
                                fonts.tiny(), theme.color("text_dim"), (rect.x + 18, y))
                bar = pygame.Rect(rect.x + 18, y + 18, rect.width - 36, 8)
                theme.progress_bar(surface, bar, owned / len(ids),
                                   color_name="success" if owned == len(ids) else "accent")
                y += 34

        preview = economy.rent_preview(state, prop)
        if prop.mortgaged:
            rent_line = "抵押中（不收租）"
            rent_color = "warning"
        else:
            rent_line = f"{preview['current']:,} / 圈"
            rent_color = "accent"

        rows: list[tuple[str, str, str]] = [
            ("地价", f"{prop.price:,}", "text"),
            ("所有者", owner.name if owner else "无主", "owner"),
            ("等级", f"{prop.level}/{prop.max_level}　"
                     + "▮" * prop.level + "▯" * (prop.max_level - prop.level), "text"),
            ("当前租金", rent_line, rent_color),
        ]
        if not prop.is_max_level:
            rows.append(("升级后租金", f"{preview['next']:,}", "text_dim"))
            rows.append(("升级费用", f"{prop.next_upgrade_cost or 0:,}", "warning"))
        elif not prop.mortgaged:
            rows.append(("状态", "已达最高等级", "success"))
        if prop.mortgaged:
            rows.append(("赎回需要", f"{economy.redeem_cost(prop):,}", "warning"))

        for label, value, color_name in rows:
            value_color = owner_color if color_name == "owner" else theme.color(color_name)
            theme.kv_row(surface, fonts,
                         pygame.Rect(rect.x + 18, y, rect.width - 36, 20), y,
                         label, value, value_color=value_color if color_name == "owner"
                         else color_name, value_key="small", row_h=22)
            y += 22


def _color_of(color_id: str) -> str:
    from .player_panel import _color_of as impl

    return impl(color_id)
