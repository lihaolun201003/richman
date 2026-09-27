"""资产面板：完整的地产管理与自救界面。

这是「破产前有策略地处理资产」的操作台，玩家可以：
    升级 / 出售 / 抵押 / 赎回 / 拆除一级建筑

布局用视觉卡片而不是表格，每张卡上直接写清「当前租金 → 升级后租金」，
让玩家一眼看懂收益。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from ..game import economy
from ..game.format import money, money_delta
from ..game.player import Player
from ..game.property import Property
from ..game.state import GameState
from . import theme
from .dialogs import Modal
from .widgets import Button, draw_tooltip

SCREEN_W = 1600
SCREEN_H = 900

#: 卡片尺寸与间距
CARD_W, CARD_H = 372, 168
CARD_GAP = 16
COLUMNS = 3
HEADER_H = 104
FOOTER_H = 72


class AssetCard:
    """一张地产卡片。"""

    def __init__(self, rect: pygame.Rect, prop: Property) -> None:
        self.rect = pygame.Rect(rect)
        self.prop = prop
        self.buttons: list[tuple[str, pygame.Rect, str]] = []   # (动作, 区域, 标签)


class AssetPanel(Modal):
    """模态资产面板。"""

    def __init__(
        self,
        state: GameState,
        player: Player,
        on_action: Callable[[str, str], bool],
        on_close: Callable[[], None] | None = None,
        title: str = "我的资产",
        allow_sell: bool = True,
        note: str = "",
        on_declare: Callable[[], None] | None = None,
        declare_label: str = "宣告破产",
    ) -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.state = state
        self.player = player
        self.on_action = on_action
        self.title = title
        self.allow_sell = allow_sell
        self.note = note
        #: 债务模式下必须留一条出路，否则玩家凑不出钱就卡住了
        self.on_declare = on_declare
        self.declare_label = declare_label

        self.rect = pygame.Rect(0, 0, 1264, 720)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.scroll = 0.0
        self.max_scroll = 0.0
        self.cards: list[AssetCard] = []
        self.hover_action: tuple[str, str] | None = None
        self.last_message = ""
        self.last_ok = True
        #: 已提交一次操作、正在等状态更新（单机等下一帧，联机等 Host 快照）。
        #: 这既是防重复提交，也让玩家看到「操作已发出」的反馈。
        self.busy = False
        self._busy_revision = -1
        self._layout_cards()

    # ------------------------------------------------------------ 布局

    def _properties(self) -> list[Property]:
        props = self.state.properties_of(self.player.id)
        props.sort(key=lambda p: (p.tile_index,))
        return props

    def _layout_cards(self) -> None:
        props = self._properties()
        self.cards = []
        grid_top = self.rect.y + HEADER_H
        for i, prop in enumerate(props):
            col, row = i % COLUMNS, i // COLUMNS
            rect = pygame.Rect(
                self.rect.x + 28 + col * (CARD_W + CARD_GAP),
                grid_top + row * (CARD_H + CARD_GAP),
                CARD_W, CARD_H,
            )
            card = AssetCard(rect, prop)
            self._build_card_buttons(card)
            self.cards.append(card)
        rows = max(1, (len(props) + COLUMNS - 1) // COLUMNS)
        content_h = rows * (CARD_H + CARD_GAP)
        view_h = self.rect.height - HEADER_H - FOOTER_H
        self.max_scroll = max(0.0, content_h - view_h + 8)

    def _build_card_buttons(self, card: AssetCard) -> None:
        st, player, prop = self.state, self.player, card.prop
        card.buttons = []
        by = card.rect.bottom - 42
        bw, bh = 104, 32
        x = card.rect.x + 12

        if not prop.is_max_level:
            cost, _ = economy.upgrade_cost(st, player, prop)
            card.buttons.append(("upgrade", pygame.Rect(x, by, bw, bh),
                                 f"升级 {money(cost)}"))
            x += bw + 8

        if prop.mortgaged:
            cost = economy.redeem_cost(prop)
            card.buttons.append(("redeem", pygame.Rect(x, by, bw, bh),
                                 f"赎回 {money(cost)}"))
            x += bw + 8
        else:
            ok, _ = economy.can_mortgage(prop)
            if ok and prop.level == 0:
                value, _ = economy.mortgage_value(st, player, prop)
                card.buttons.append(("mortgage", pygame.Rect(x, by, bw, bh),
                                     f"抵押 {money(value)}"))
                x += bw + 8

        if prop.level > 0:
            card.buttons.append(("downgrade", pygame.Rect(x, by, bw, bh), "拆一级"))
            x += bw + 8

        if self.allow_sell:
            refund, _ = economy.sell_refund(st, player, prop)
            card.buttons.append(("sell", pygame.Rect(x, by, bw, bh),
                                 f"出售 {money(refund)}"))

    def refresh(self) -> None:
        """外部状态变化后重建卡片（保留滚动位置）。"""
        keep = self.scroll
        self._layout_cards()
        self.scroll = min(keep, self.max_scroll)

    def update(self, dt: float) -> None:
        super().update(dt)
        # 状态一变（revision 前进）就说明操作已被引擎 / Host 处理
        if self.busy and getattr(self.state, "revision", 0) != self._busy_revision:
            self.busy = False
            self.refresh()

    # ------------------------------------------------------------ 交互

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0.0, min(self.max_scroll, self.scroll - event.y * 56))
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if self.on_declare is not None and self._declare_button_rect().collidepoint(pos):
                if not self.busy:
                    self.busy = True
                    self._busy_revision = getattr(self.state, "revision", 0)
                    self.on_declare()
                return True
            for card in self._visible_cards():
                for action, rect, _label in card.buttons:
                    # 命中判定必须用「滚动后」的坐标，否则翻页后点错按钮
                    if rect.move(0, -int(self.scroll)).collidepoint(pos):
                        self._do(action, card.prop)
                        return True
            if self.rect.collidepoint(pos):
                return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if not self.rect.collidepoint(event.pos):
                self.close()
            return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_i):
                self.close()
                return True
        return True

    def _do(self, action: str, prop: Property) -> None:
        if self.busy:
            return
        ok = self.on_action(action, prop.id)
        if ok:
            self.busy = True
            self._busy_revision = getattr(self.state, "revision", 0)
        self.last_ok = bool(ok)
        self.last_message = {
            "upgrade": "升级成功" if ok else "升级失败",
            "sell": "已出售" if ok else "出售失败",
            "mortgage": "已抵押" if ok else "抵押失败",
            "redeem": "已赎回" if ok else "赎回失败",
            "downgrade": "已拆除一级" if ok else "拆除失败",
        }.get(action, "")
        self.refresh()

    def button_rect(self, card: AssetCard, action: str) -> pygame.Rect | None:
        """返回某个操作按钮在屏幕上的实际位置（已计入滚动）。"""
        for act, rect, _label in card.buttons:
            if act == action:
                return rect.move(0, -int(self.scroll))
        return None

    def find_action(self, action: str) -> tuple[AssetCard | None, pygame.Rect | None]:
        """找到第一个可用（且已启用）的指定操作。"""
        for card in self.cards:
            rect = self.button_rect(card, action)
            if rect is not None and self._action_enabled(action, card.prop):
                return card, rect
        return None, None

    def _close_button_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 180, self.rect.bottom - FOOTER_H + 14,
                           152, 44)

    def _declare_button_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 400, self.rect.bottom - FOOTER_H + 14,
                           200, 44)

    def _visible_cards(self) -> list[AssetCard]:
        top = self.rect.y + HEADER_H - self.scroll
        bottom = self.rect.bottom - FOOTER_H + 0
        out = []
        for card in self.cards:
            if card.rect.bottom >= top and card.rect.y <= bottom:
                out.append(card)
        return out

    def _card_offset_rect(self, card: AssetCard) -> pygame.Rect:
        return card.rect.move(0, -int(self.scroll))

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self.rect
        theme.shadow_rect(surface, rect, radius=18, spread=10, alpha=160)
        theme.rounded_rect(surface, rect, theme.color("panel_alt"), radius=18)
        theme.rounded_rect(surface, rect, None, radius=18, border=theme.color("border"),
                           border_width=2)

        self._draw_header(surface, fonts)
        self._draw_cards(surface, fonts)
        self._draw_footer(surface, fonts)

    def _draw_header(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        header = pygame.Rect(rect.x, rect.y, rect.width, HEADER_H)
        theme.rounded_rect(surface, header, theme.color("panel"), radius=18)
        pygame.draw.rect(surface, theme.color("panel"), pygame.Rect(
            header.x, header.bottom - 18, header.width, 18))

        theme.draw_text(surface, self.title, fonts.h1(), theme.color("text"),
                        (rect.x + 28, rect.y + 16))

        props = self._properties()
        total_value = sum(p.asset_value for p in props)
        rent_income = sum(economy.rent_value(self.state, p) for p in props if not p.mortgaged)
        mortgaged = sum(1 for p in props if p.mortgaged)

        stats = [
            ("现金", money(self.player.money), "accent"),
            ("地产", f"{len(props)} 处", "text"),
            ("地产总值", money(total_value), "text"),
            ("预计租金", f"{money(rent_income)} / 圈", "success"),
        ]
        x = rect.x + 330
        for label, value, color_name in stats:
            theme.draw_text(surface, label, fonts.tiny(), theme.color("text_mute"), (x, rect.y + 22))
            theme.draw_text(surface, value, fonts.h3(), theme.color(color_name), (x, rect.y + 42))
            x += 220
        if mortgaged:
            theme.draw_text(surface, f"抵押中 {mortgaged} 处", fonts.small(),
                            theme.color("warning"), (rect.right - 28, rect.y + 26),
                            anchor="topright")
        if self.note:
            theme.draw_text(surface, self.note, fonts.small(), theme.color("text_dim"),
                            (rect.right - 28, rect.y + 54), anchor="topright")
        pygame.draw.line(surface, theme.color("border_soft"),
                         (rect.x + 20, rect.y + HEADER_H - 2),
                         (rect.right - 20, rect.y + HEADER_H - 2), 1)

    def _draw_cards(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        clip = surface.get_clip()
        body = pygame.Rect(self.rect.x + 4, self.rect.y + HEADER_H - self.scroll,
                           self.rect.width - 8,
                           self.rect.height - HEADER_H - FOOTER_H + self.scroll)
        surface.set_clip(body)

        props = self._properties()
        if not props:
            theme.draw_text(surface, "你还没有任何地产。落到无主地块时可以买下来。",
                            fonts.h2(), theme.color("text_mute"),
                            (self.rect.centerx, self.rect.centery - 40), anchor="center")
        mouse = pygame.mouse.get_pos()
        self.hover_action = None
        for card in self.cards:
            rect = self._card_offset_rect(card)
            if rect.bottom < body.y - 40 or rect.y > body.bottom + 40:
                continue
            self._draw_card(surface, fonts, card, rect, mouse)
        surface.set_clip(clip)

        if self.max_scroll > 0:
            bar = pygame.Rect(self.rect.right - 12, self.rect.y + HEADER_H,
                              8, self.rect.height - HEADER_H - FOOTER_H)
            theme.rounded_rect(surface, bar, theme.color("bg_alt"), radius=4)
            ratio = (self.rect.height - HEADER_H - FOOTER_H) / max(
                1.0, self.max_scroll + self.rect.height - HEADER_H - FOOTER_H)
            handle_h = max(36, int(bar.height * ratio))
            pos = self.scroll / max(1.0, self.max_scroll)
            handle = pygame.Rect(bar.x, bar.y + int((bar.height - handle_h) * pos),
                                 bar.width, handle_h)
            theme.rounded_rect(surface, handle, theme.color("border"), radius=4)

    def _draw_card(self, surface: pygame.Surface, fonts: theme.FontManager,
                   card: AssetCard, rect: pygame.Rect, mouse: tuple[int, int]) -> None:
        prop = card.prop
        st = self.state

        fill = theme.color("panel")
        border = theme.color("border_soft")
        if prop.mortgaged:
            fill = theme.color("bg_alt")
            border = theme.color("warning")
        theme.rounded_rect(surface, rect, fill, radius=14)
        theme.rounded_rect(surface, rect, None, radius=14, border=border,
                           border_width=2 if prop.mortgaged else 1)

        # 片区色条
        accent = theme.color("t_property" if prop.kind == "PROPERTY" else "t_station")
        theme.rounded_rect(surface, pygame.Rect(rect.x + 12, rect.y + 12, 5, 26),
                           accent, radius=2)

        theme.draw_text(surface, theme.truncate(prop.name, fonts.h3(), CARD_W - 130),
                        fonts.h3(), theme.color("text"), (rect.x + 24, rect.y + 12))
        district = prop.district or "独立地产"
        owned_all = bool(prop.district) and st.district_owned_all(self.player.id, prop.district)
        theme.draw_text(surface, district + ("（垄断）" if owned_all else ""),
                        fonts.tiny(), theme.color("success" if owned_all else "text_mute"),
                        (rect.x + 24, rect.y + 38))

        # 等级
        blocks = "▮" * prop.level + "▯" * (prop.max_level - prop.level)
        theme.draw_text(surface, blocks, fonts.sized(15, True),
                        accent if prop.level else theme.color("text_mute"),
                        (rect.right - 14, rect.y + 16), anchor="topright")

        # 租金行
        y = rect.y + 62
        current = economy.rent_value(st, prop)
        theme.draw_text(surface, "当前租金", fonts.tiny(), theme.color("text_mute"),
                        (rect.x + 24, y))
        theme.draw_text(surface, money(current) if not prop.mortgaged else "抵押中，无租金",
                        fonts.body(), theme.color("accent" if not prop.mortgaged else "warning"),
                        (rect.x + 92, y - 2))

        if not prop.is_max_level:
            nxt = prop.next_rent or 0
            mult, _ = economy.rent_multiplier(st, prop)
            nxt = int(round(nxt * mult))
            gain = nxt - current
            y += 24
            theme.draw_text(surface, "升级后", fonts.tiny(), theme.color("text_mute"),
                            (rect.x + 24, y))
            theme.draw_text(surface, money(nxt), fonts.body(), theme.color("success"),
                            (rect.x + 92, y - 2))
            theme.draw_text(surface, f"（+{money(max(0, gain))}）", fonts.tiny(),
                            theme.color("text_dim"), (rect.x + 190, y + 2))
        else:
            y += 24
            theme.draw_text(surface, "已达最高等级", fonts.small(), theme.color("success"),
                            (rect.x + 24, y))

        # 估值
        y += 26
        info = f"地价 {money(prop.price)} · 已投入 {money(prop.total_invested)}"
        theme.draw_text(surface, info, fonts.tiny(), theme.color("text_mute"),
                        (rect.x + 24, y))
        if prop.mortgaged:
            theme.draw_text(surface, f"赎回需要 {money(economy.redeem_cost(prop))}",
                            fonts.tiny(), theme.color("warning"), (rect.x + 24, y + 16))

        # 按钮
        for action, brect0, label in card.buttons:
            brect = brect0.move(0, -int(self.scroll))
            enabled = self._action_enabled(action, prop) and not self.busy
            hovered = brect.collidepoint(mouse) and enabled
            if hovered:
                self.hover_action = (action, prop.name)
            base = {
                "upgrade": "success", "sell": "danger", "mortgage": "accent",
                "redeem": "primary", "downgrade": "secondary",
            }.get(action, "secondary")
            if not enabled:
                theme.rounded_rect(surface, brect, theme.color("bg_alt"), radius=8)
                theme.rounded_rect(surface, brect, None, radius=8,
                                   border=theme.color("border_soft"), border_width=1)
                theme.draw_text(surface, label, fonts.tiny(), theme.color("text_mute"),
                                brect.center, anchor="center")
            else:
                col = theme.color(base)
                if hovered:
                    col = theme.lighten(col, 0.12)
                theme.rounded_rect(surface, brect, col, radius=8)
                theme.draw_text(surface, label, fonts.tiny(), theme.color("text"),
                                brect.center, anchor="center")

    def _action_enabled(self, action: str, prop: Property) -> bool:
        st, player = self.state, self.player
        if action == "upgrade":
            cost, _ = economy.upgrade_cost(st, player, prop)
            return (not prop.is_max_level) and player.money >= cost
        if action == "sell":
            return prop.owner_id == player.id
        if action == "mortgage":
            ok, _ = economy.can_mortgage(prop)
            return ok and prop.level == 0
        if action == "redeem":
            return player.money >= economy.redeem_cost(prop)
        if action == "downgrade":
            return prop.level > 0
        return True

    def _draw_footer(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        footer = pygame.Rect(rect.x, rect.bottom - FOOTER_H, rect.width, FOOTER_H)
        theme.rounded_rect(surface, footer, theme.color("panel"), radius=18)
        pygame.draw.rect(surface, theme.color("panel"), pygame.Rect(
            footer.x, footer.y, footer.width, 18))
        pygame.draw.line(surface, theme.color("border_soft"),
                         (footer.x + 20, footer.y + 1),
                         (footer.right - 20, footer.y + 1), 1)

        hint = ("正在等待结算…" if self.busy else
                "滚轮翻看 · 抵押保留产权但不能收租 · 出售会永久失去这块地")
        if self.last_message:
            theme.draw_text(surface, self.last_message, fonts.small(),
                            theme.color("success" if self.last_ok else "danger"),
                            (rect.x + 28, rect.centery + FOOTER_H // 2 - 8))
        else:
            theme.draw_text(surface, hint, fonts.small(), theme.color("text_mute"),
                            (rect.x + 28, rect.centery + FOOTER_H // 2 - 8))

        if self.on_declare is not None:
            declare = Button(self._declare_button_rect(), self.declare_label,
                             on_click=self.on_declare, style="danger", font_size=16,
                             enabled=not self.busy,
                             tooltip="资产不足以偿还欠款时，只能退出本局")
            if not self.busy:
                declare.on_click = self._trigger_declare
            declare.draw(surface, fonts)

        close = Button(self._close_button_rect(),
                       "关闭（I / ESC）", on_click=self.close, style="secondary",
                       font_size=16)
        close.draw(surface, fonts)

    def _trigger_declare(self) -> None:
        if self.busy or self.on_declare is None:
            return
        self.busy = True
        self._busy_revision = getattr(self.state, "revision", 0)
        self.on_declare()


def show_debt_panel(state: GameState, fonts: theme.FontManager) -> None:
    """占位：债务处理目前复用 DecisionDialog，这里保留扩展点。"""
    del state, fonts
