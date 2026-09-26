"""玩家信息面板：顶部横排的玩家卡片。

每张卡片显示头像（程序化生成的色块 + 角色首字）、名字、资金、地产数、
道具数、状态、是否掉线 / 破产 / 由 AI 接管，并高亮当前回合玩家。
颜色之外同时有名字与首字图标，对色觉差异更友好。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game.player import Player
from . import theme


class PlayerPanel:
    """顶部玩家卡片条。"""

    def __init__(self, rect: pygame.Rect, card_gap: int = 10) -> None:
        self.rect = pygame.Rect(rect)
        self.card_gap = card_gap
        self.cards: list[pygame.Rect] = []
        self.hover_index: int | None = None
        self.local_player_id: str = ""

    def layout(self, count: int) -> None:
        """按玩家数量重新计算卡片位置。"""
        count = max(1, count)
        gap = self.card_gap
        total_gap = gap * (count - 1)
        width = (self.rect.width - total_gap) // count
        width = max(120, width)
        self.cards = []
        x = self.rect.x
        for _ in range(count):
            self.cards.append(pygame.Rect(x, self.rect.y, width, self.rect.height))
            x += width + gap

    def hit_test(self, pos: tuple[int, int]) -> int | None:
        for i, card in enumerate(self.cards):
            if card.collidepoint(pos):
                return i
        return None

    def draw(
        self,
        surface: pygame.Surface,
        fonts: theme.FontManager,
        players: list[Player],
        current_player_id: str | None,
        owner_counts: dict[str, int],
        pulse: float,
        char_names: dict[str, str] | None = None,
        card_defs: Any = None,
    ) -> None:
        if len(self.cards) != len(players):
            self.layout(len(players))

        for i, player in enumerate(players):
            if i >= len(self.cards):
                break
            self._draw_card(surface, fonts, self.cards[i], player,
                            player.id == current_player_id, pulse,
                            owner_counts.get(player.id, 0),
                            (char_names or {}).get(player.character_id, ""),
                            card_defs)

    def _draw_card(
        self,
        surface: pygame.Surface,
        fonts: theme.FontManager,
        rect: pygame.Rect,
        player: Player,
        is_current: bool,
        pulse: float,
        prop_count: int,
        char_name: str,
        card_defs: Any,
    ) -> None:
        base_color = theme.hex_to_rgb(_color_of(player.color_id))
        is_local = player.id == self.local_player_id

        # 背景
        if player.bankrupt:
            fill = theme.color("bg_alt")
            border = theme.color("border_soft")
        elif is_current:
            glow = theme.mix(theme.color("panel_hi"), base_color, 0.22 + 0.12 * pulse)
            fill = glow
            border = theme.mix(base_color, theme.color("accent_soft"), pulse)
        else:
            fill = theme.color("panel")
            border = theme.color("border_soft")

        theme.rounded_rect(surface, rect, fill, radius=12)
        theme.rounded_rect(surface, rect, None, radius=12, border=border,
                           border_width=3 if is_current else 1)

        # 头像
        avatar_size = min(46, rect.height - 22)
        avatar = pygame.Rect(rect.x + 10, rect.centery - avatar_size // 2, avatar_size, avatar_size)
        if player.bankrupt:
            avatar_col = theme.color("text_mute")
        else:
            avatar_col = base_color
        pygame.draw.circle(surface, avatar_col, avatar.center, avatar_size // 2)
        pygame.draw.circle(surface, theme.darken(avatar_col, 0.35), avatar.center,
                           avatar_size // 2, 2)
        initial = (char_name or player.name or "?")[0]
        theme.draw_text(surface, initial, fonts.sized(int(avatar_size * 0.5), True),
                        (255, 255, 255), avatar.center, anchor="center")

        text_x = avatar.right + 10
        text_w = rect.right - text_x - 8

        # 名字行
        name_font = fonts.sized(16, True)
        name = theme.truncate(player.name, name_font, text_w - 22)
        name_color = theme.color("text_mute") if player.bankrupt else theme.color("text")
        theme.draw_text(surface, name, name_font, name_color, (text_x, rect.y + 8))
        # 本地标识
        if is_local:
            tag = pygame.Rect(text_x + name_font.size(name)[0] + 6, rect.y + 10, 30, 15)
            if tag.right < rect.right - 4:
                theme.rounded_rect(surface, tag, theme.color("primary"), radius=4)
                theme.draw_text(surface, "你", fonts.micro(), theme.color("text"),
                                tag.center, anchor="center")

        # 资金
        money_font = fonts.sized(19, True)
        money_text = f"{player.money:,}"
        money_color = theme.color("danger") if player.bankrupt else (
            theme.color("accent") if player.money > 0 else theme.color("text_mute"))
        theme.draw_text(surface, money_text, money_font, money_color,
                        (text_x, rect.y + 28))

        # 第二行：地产 / 道具 / 状态
        info_font = fonts.micro()
        y = rect.y + 52
        pieces = [f"地产 {prop_count}", f"道具 {len(player.cards)}"]
        theme.draw_text(surface, "  ".join(pieces), info_font, theme.color("text_dim"),
                        (text_x, y))

        # 状态徽标：与信息行同一行、右对齐，避免和「地产/道具」文字叠在一起
        badges: list[tuple[str, str]] = []
        if player.bankrupt:
            badges.append(("已破产", "text_mute"))
        elif player.disconnected:
            badges.append(("AI接管" if player.bot_controlled else "掉线", "warning"))
        elif player.in_jail:
            badges.append((f"关押{player.jail_turns + 1}/3", "info"))
        for eff in player.status_effects[:1]:
            badges.append((eff.label, "accent_soft"))
        if badges:
            bx = rect.right - 8
            for text, color_name in badges[:2]:
                w = info_font.size(text)[0] + 10
                if bx - w < text_x:
                    break
                tag_rect = pygame.Rect(bx - w, y - 2, w, 17)
                theme.rounded_rect(surface, tag_rect, theme.color(color_name, 55), radius=5)
                theme.rounded_rect(surface, tag_rect, None, radius=5,
                                   border=theme.color(color_name), border_width=1)
                theme.draw_text(surface, text, info_font, theme.color(color_name),
                                tag_rect.center, anchor="center")
                bx -= w + 5

        # 破产灰化
        if player.bankrupt:
            theme.rounded_rect(surface, rect, theme.color("bg", 120), radius=12)
            pygame.draw.line(surface, theme.color("danger_dark"),
                             (rect.x + 12, rect.centery), (rect.right - 12, rect.centery), 2)

    def tooltip_for(self, index: int, players: list[Player], props_of,
                    card_defs: Any = None) -> str:
        if index is None or index >= len(players):
            return ""
        p = players[index]
        lines = [f"{p.name}"]
        lines.append(f"资金：{p.money:,}")
        owned = props_of(p.id)
        lines.append(f"地产：{len(owned)} 处（估值 {sum(x.asset_value for x in owned):,}）")
        if p.cards and card_defs is not None:
            names = []
            for cid in p.cards:
                cd = card_defs.get(cid)
                names.append(cd.name if cd else cid)
            lines.append("道具：" + "、".join(names))
        if p.status_effects:
            lines.append("状态：" + "、".join(e.label for e in p.status_effects))
        lines.append(f"累计收租：{p.stats.get('rent_income', 0):,}")
        lines.append(f"累计付租：{p.stats.get('rent_paid', 0):,}")
        lines.append(f"经过起点：{p.stats.get('start_passes', 0)} 次")
        return "\n".join(lines)


def _color_of(color_id: str) -> str:
    from ..game.setup import palette

    for item in palette():
        if item["id"] == color_id:
            return item["color"]
    return "#8899AA"
