"""资产面板：地产管理与债务自救的操作台。

v0.3 结构（左列表 / 中详情 / 右操作）：
    左：地产列表（片区色 + 名称 + 等级 + 当前租金 + 状态），可滚动
    中：选中地产的完整信息（片区完成度 / 地价 / 当前租金 / 升级前后 / 抵押 / 赎回 / 出售）
    右：操作按钮，每个按钮都写清「花多少 / 拿多少」
    债务模式：顶部固定显示 欠款 / 现金 / 仍需筹集 + 进度条，随每次操作实时变化

所有操作仍然只发 Command，真正的校验在引擎里。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from ..game import economy
from ..game.format import money
from ..game.player import Player
from ..game.property import Property
from ..game.state import GameState
from . import icons, theme
from .dialogs import Modal
from .widgets import Button, ScrollPanel, draw_tooltip

SCREEN_W = 1600
SCREEN_H = 900

PANEL_W, PANEL_H = 1340, 760
HEADER_H = 92
DEBT_H = 72
FOOTER_H = 74
LIST_W = 424
DETAIL_W = 468
RIGHT_W = PANEL_W - LIST_W - DETAIL_W - 48

#: 操作 → (按钮文案模板, 样式, 图标)。{amount} 会替换成具体金额。
ACTIONS = (
    ("upgrade", "升级  {amount}", "success", "hammer"),
    ("mortgage", "抵押  {amount}", "accent", "tag"),
    ("redeem", "赎回  {amount}", "primary", "key"),
    ("downgrade", "拆一级 +{amount}", "secondary", "hammerdown"),
    ("sell", "出售  {amount}", "danger", "cash"),
)


class AssetRow:
    """地产列表中的一行（也是 find_action 返回的对象，保持旧接口兼容）。"""

    __slots__ = ("rect", "prop")

    def __init__(self, rect: pygame.Rect, prop: Property) -> None:
        self.rect = rect
        self.prop = prop


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
        debt: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.state = state
        self.player = player
        self.on_action = on_action
        self.title = title
        self.allow_sell = allow_sell
        self.note = note
        self.debt = debt or None
        self.on_declare = on_declare
        self.declare_label = declare_label

        self.rect = pygame.Rect(0, 0, PANEL_W, PANEL_H)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        #: 由场景注入，用于取片区颜色（保证与棋盘上的色带一致）
        self.board_view = None
        self.selected_index = 0
        self.hover_row = -1
        self.last_message = ""
        self.last_ok = True
        self.busy = False
        self._busy_revision = -1
        self._action_rects: dict[str, pygame.Rect] = {}
        self._rows: list[AssetRow] = []
        self._list = ScrollPanel(
            pygame.Rect(self.rect.x + 20, self._body_top(), LIST_W, self._body_height()))
        self._refresh()

    # ------------------------------------------------------------ 布局

    def _body_top(self) -> int:
        return self.rect.y + HEADER_H + (DEBT_H if self.debt else 0)

    def _body_height(self) -> int:
        return self.rect.height - HEADER_H - (DEBT_H if self.debt else 0) - FOOTER_H

    def _properties(self) -> list[Property]:
        props = self.state.properties_of(self.player.id)
        props.sort(key=lambda p: (p.tile_index,))
        return props

    def _refresh(self) -> None:
        props = self._properties()
        self.selected_index = max(0, min(self.selected_index, len(props) - 1))
        x = self.rect.x + 20
        y = self._body_top() + 12
        self._rows = []
        for prop in props:
            self._rows.append(AssetRow(pygame.Rect(x, y, LIST_W, 62), prop))
            y += 68
        self._list.rect = pygame.Rect(self.rect.x + 28, self._body_top() + 48,
                                      LIST_W - 16, self._body_height() - 60)
        self._list.set_content_height(len(self._rows) * 68)
        self._build_action_rects()
        self._sync_rows()

    def _sync_rows(self) -> None:
        """把滚动偏移应用到每一行的命中矩形（绘制与点击共用同一份坐标）。"""
        offset = self._list.offset()
        base = self._body_top() + 12
        for i, row in enumerate(self._rows):
            row.rect.y = base + i * 68 - offset

    def _selected(self) -> Property | None:
        props = self._properties()
        if not props:
            return None
        return props[self.selected_index]

    def _build_action_rects(self) -> None:
        """重建操作按钮。

        这里用真正的 `Button` 而不是「画上去的矩形」，原因有两个：
        - 通用自动化（冒烟测试 / 真实窗口自检）只能点 `modal.buttons`，
          操作按钮不在里面时，自动化一遇到债务面板就会空转；
        - 悬停 / 禁用态 / 图标 / tooltip 复用同一套控件样式，不会各自跑偏。
        """
        rect = self.rect
        x = rect.right - 20 - RIGHT_W
        y = self._body_top() + 76
        self._action_rects = {}
        self.buttons = []
        prop = self._selected()
        for action, label_tpl, style, icon_name in ACTIONS:
            brect = pygame.Rect(x, y, RIGHT_W, 52)
            y += 60
            self._action_rects[action] = brect
            if action == "sell" and not self.allow_sell:
                continue
            if prop is None:
                continue
            enabled = self._action_enabled(action, prop) and not self.busy
            hint = self._action_hint(action, prop)
            if enabled:
                label = label_tpl.format(amount=self._action_amount(action, prop))
            else:
                label = action_label(action)
                if hint:
                    label = f"{action_label(action)} · {hint}"
            self.buttons.append(Button(
                brect, label,
                on_click=(lambda a=action: self._do(a, self._selected())),
                style=style if enabled else "ghost", enabled=enabled,
                icon=icon_name, font_size=17, tooltip=hint or label))
        if self.on_declare is not None:
            self.buttons.append(Button(
                self._declare_rect(), self.declare_label, on_click=self._trigger_declare,
                style="danger", enabled=not self.busy, font_size=16, icon="alert",
                tooltip="资产不足以偿还欠款时，只能退出本局"))
        self.buttons.append(Button(
            self._close_rect(), "关闭（I / ESC）", on_click=self.close,
            style="secondary", font_size=16, icon="cross"))

    def _action_amount(self, action: str, prop: Property) -> str:
        st, player = self.state, self.player
        if action == "upgrade":
            return money(economy.upgrade_cost(st, player, prop)[0])
        if action == "mortgage":
            return money(economy.mortgage_value(st, player, prop)[0])
        if action == "redeem":
            return money(economy.redeem_cost(prop))
        if action == "downgrade":
            return money(prop.upgrade_costs[prop.level - 1] // 2 if prop.level > 0 else 0)
        return money(economy.sell_refund(st, player, prop)[0])

    def refresh(self) -> None:
        """外部状态变化后重建列表（保留选中与滚动位置）。"""
        self._refresh()

    #: 提交后等状态的超时（秒）。超过就认为这次操作没有被接受，
    #: 必须解锁界面并告诉玩家，否则一次被拒绝的操作会把面板永久锁死。
    BUSY_TIMEOUT = 2.5

    def update(self, dt: float) -> None:
        super().update(dt)
        if not self.busy:
            return
        if getattr(self.state, "revision", 0) != self._busy_revision:
            self.busy = False
            self._refresh()
            return
        self._busy_elapsed = getattr(self, "_busy_elapsed", 0.0) + dt
        if self._busy_elapsed >= self.BUSY_TIMEOUT:
            self.busy = False
            self._busy_elapsed = 0.0
            self.last_ok = False
            self.last_message = "这次操作没有被接受，请换一种处置方式"
            self._refresh()

    # ------------------------------------------------------------ 交互

    def handle_event(self, event: pygame.event.Event) -> bool:
        self._sync_rows()
        if event.type == pygame.MOUSEWHEEL:
            if self.rect.collidepoint(pygame.mouse.get_pos()):
                self._list.scroll_by(-event.y * 56)
                self._sync_rows()
                return True
        if event.type == pygame.MOUSEMOTION:
            self.hover_row = -1
            for i, row in enumerate(self._rows):
                if row.rect.collidepoint(event.pos):
                    self.hover_row = i
                    break
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            for i, row in enumerate(self._rows):
                if row.rect.collidepoint(pos):
                    self.selected_index = i
                    return True
            if self.on_declare is not None and self._declare_rect().collidepoint(pos):
                self._trigger_declare()
                return True
            if self._close_rect().collidepoint(pos):
                self.close()
                return True
            for action, brect in self._action_rects.items():
                if brect.collidepoint(pos):
                    prop = self._selected()
                    if prop is not None:
                        self._do(action, prop)
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
            if event.key in (pygame.K_DOWN, pygame.K_UP):
                props = self._properties()
                if props:
                    step = 1 if event.key == pygame.K_DOWN else -1
                    self.selected_index = (self.selected_index + step) % len(props)
                    self._scroll_to_selected()
                return True
        return True

    def _scroll_to_selected(self) -> None:
        row = self._rows[self.selected_index] if self.selected_index < len(self._rows) else None
        if row is None:
            return
        self._sync_rows()
        top = self._list.rect.y
        bottom = self._list.rect.bottom
        if row.rect.y < top:
            self._list.scroll_by(row.rect.y - top)
        elif row.rect.bottom > bottom:
            self._list.scroll_by(row.rect.bottom - bottom + 6)
        self._sync_rows()

    def _do(self, action: str, prop: Property) -> None:
        if self.busy:
            return
        ok = self.on_action(action, prop.id)
        if ok:
            self.busy = True
            self._busy_revision = getattr(self.state, "revision", 0)
        self.last_ok = bool(ok)
        self.last_message = {
            "upgrade": "升级成功" if ok else "升级失败：现金不足或已满级",
            "sell": "已出售" if ok else "出售失败",
            "mortgage": "已抵押，产权保留但不能收租" if ok else "抵押失败：需要先拆掉建筑",
            "redeem": "已赎回，下回合恢复收租" if ok else "赎回失败：现金不足",
            "downgrade": "已拆除一级建筑" if ok else "拆除失败",
        }.get(action, "")
        self._refresh()
        self._scroll_to_selected()

    # ---- 供自动化测试使用的稳定接口

    def button_rect(self, row: AssetRow, action: str) -> pygame.Rect | None:
        return self._action_rects.get(action)

    def find_action(self, action: str) -> tuple[AssetRow | None, pygame.Rect | None]:
        """找到第一个可执行指定操作的地产，并把它设为选中。"""
        for i, row in enumerate(self._rows):
            if self._action_enabled(action, row.prop):
                self.selected_index = i
                self._build_action_rects()
                return row, self._action_rects.get(action)
        return None, None

    def _declare_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 20 - RIGHT_W,
                           self.rect.bottom - FOOTER_H + 14, RIGHT_W - 220, 46)

    def _close_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 220, self.rect.bottom - FOOTER_H + 14,
                           200, 46)

    def _declare_button_rect(self) -> pygame.Rect:
        """兼容旧接口（自动化测试用）。"""
        return self._declare_rect()

    def _action_enabled(self, action: str, prop: Property) -> bool:
        st, player = self.state, self.player
        # 债务处理中只能「抵押 / 出售」——引擎的债务决策也只提供这两种选项，
        # 开放其它按钮只会让玩家点了没反应。
        if self.debt is not None and action not in ("mortgage", "sell"):
            return False
        if action == "upgrade":
            cost, _ = economy.upgrade_cost(st, player, prop)
            return (not prop.is_max_level) and player.money >= cost
        if action == "sell":
            return self.allow_sell and prop.owner_id == player.id
        if action == "mortgage":
            ok, _ = economy.can_mortgage(prop)
            return ok
        if action == "redeem":
            return prop.mortgaged and player.money >= economy.redeem_cost(prop)
        if action == "downgrade":
            return prop.level > 0
        return True

    def _action_hint(self, action: str, prop: Property) -> str:
        """禁用原因，直接写在按钮上（玩家不用猜）。"""
        st, player = self.state, self.player
        if self.debt is not None and action not in ("mortgage", "sell"):
            return "债务处理中只能抵押或出售"
        if action == "upgrade":
            if prop.is_max_level:
                return "已满级"
            cost, _ = economy.upgrade_cost(st, player, prop)
            if player.money < cost:
                return f"现金不足（差 {money(cost - player.money)}）"
        if action == "mortgage":
            if prop.mortgaged:
                return "已抵押"
            ok, reason = economy.can_mortgage(prop)
            if not ok:
                return reason
        if action == "redeem" and not prop.mortgaged:
            return "未抵押"
        if action == "redeem" and player.money < economy.redeem_cost(prop):
            return f"现金不足（差 {money(economy.redeem_cost(prop) - player.money)}）"
        if action == "downgrade" and prop.level <= 0:
            return "没有建筑"
        if action == "sell" and prop.mortgaged:
            return "抵押中，回收率较低"
        return ""

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self.rect
        theme.shadow_rect(surface, rect, radius=20, spread=10, alpha=170)
        theme.rounded_rect(surface, rect, theme.color("panel_alt"), radius=20)
        theme.rounded_rect(surface, rect, None, radius=20, border=theme.color("border"),
                           border_width=2)

        self._draw_header(surface, fonts)
        if self.debt:
            self._draw_debt(surface, fonts)
        self._draw_list(surface, fonts)
        self._draw_detail(surface, fonts)
        self._draw_actions(surface, fonts)
        self._draw_footer(surface, fonts)

    def _draw_header(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        header = pygame.Rect(rect.x, rect.y, rect.width, HEADER_H)
        theme.rounded_rect(surface, header, theme.color("panel"), radius=20)
        pygame.draw.rect(surface, theme.color("panel"),
                         pygame.Rect(header.x, header.bottom - 20, header.width, 20))

        theme.draw_text(surface, self.title, fonts.h1(), theme.color("text"),
                        (rect.x + 28, rect.y + 16))

        props = self._properties()
        rent_income = sum(economy.rent_value(self.state, p)
                          for p in props if not p.mortgaged)
        mortgaged = sum(1 for p in props if p.mortgaged)
        stats = [
            ("现金", money(self.player.money), "accent"),
            ("地产", f"{len(props)} 处", "text"),
            ("地产总值", money(sum(p.asset_value for p in props)), "text"),
            ("预计租金", f"{money(rent_income)} / 圈", "success"),
        ]
        x = rect.x + 330
        for label, value, color_name in stats:
            theme.stat(surface, fonts, x, rect.y + 18, label, value, color_name=color_name)
            x += 232
        if mortgaged:
            theme.chip(surface, fonts,
                       pygame.Rect(rect.right - 160, rect.y + 20, 132, 24),
                       f"抵押中 {mortgaged} 处", "warning", font_key="tiny", radius=6)
        pygame.draw.line(surface, theme.color("border_soft"),
                         (rect.x + 20, rect.y + HEADER_H - 2),
                         (rect.right - 20, rect.y + HEADER_H - 2), 1)

    def _draw_debt(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        """债务条：欠款 / 现金 / 仍需筹集，实时反映每次操作。"""
        rect = self.rect
        band = pygame.Rect(rect.x + 20, rect.y + HEADER_H + 4, rect.width - 40, DEBT_H - 10)
        theme.rounded_rect(surface, band, theme.color("danger", 30), radius=14)
        theme.rounded_rect(surface, band, None, radius=14, border=theme.color("danger"),
                           border_width=2)
        debt = self.debt or {}
        amount = int(debt.get("amount", 0))
        cash = self.player.money
        shortfall = max(0, amount - cash)
        reason = debt.get("reason", "")

        icons.draw_icon(surface, "alert", pygame.Rect(band.x + 14, band.centery - 13, 26, 26),
                        theme.color("danger"), theme.color("shadow"))
        theme.draw_text(surface, "债务处理中", fonts.h3(), theme.color("danger"),
                        (band.x + 48, band.y + 10))
        if reason:
            theme.draw_text(surface, f"（{reason}）", fonts.tiny(), theme.color("text_mute"),
                            (band.x + 48, band.y + 34))

        items = [
            ("欠款", money(amount), "text"),
            ("现有现金", money(cash), "accent"),
            ("仍需筹集", money(shortfall), "danger" if shortfall > 0 else "success"),
        ]
        x = band.x + 380
        for label, value, color_name in items:
            theme.stat(surface, fonts, x, band.y + 12, label, value, color_name=color_name,
                       value_key="h3")
            x += 210
        # 进度条与数字分开摆，避免「仍需筹集」被条形盖住
        ratio = 1.0 if amount <= 0 else min(1.0, max(0.0, cash / amount))
        bar = pygame.Rect(band.right - 280, band.centery - 6, 200, 12)
        theme.progress_bar(surface, bar, ratio,
                           color_name="success" if shortfall <= 0 else "danger")
        theme.draw_text(surface, f"已筹 {int(ratio * 100)}%", fonts.micro(),
                        theme.color("text_mute"),
                        (bar.right - 20, bar.bottom + 4), anchor="topright")

    def _draw_list(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        box = pygame.Rect(rect.x + 20, self._body_top(), LIST_W, self._body_height())
        theme.panel(surface, box, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, fonts,
                             pygame.Rect(box.x + 14, box.y + 10, box.width - 28, 22),
                             "地产列表", icon="property",
                             note=f"{len(self._rows)} 处 · 滚轮翻看")

        if not self._rows:
            theme.draw_text(surface, "你还没有任何地产。", fonts.body(),
                            theme.color("text_mute"),
                            (box.centerx, box.centery), anchor="center")
            theme.draw_text(surface, "落到无主地块时可以买下来。", fonts.small(),
                            theme.color("text_mute"),
                            (box.centerx, box.centery + 26), anchor="center")
            return

        old_clip = surface.get_clip()
        surface.set_clip(box.inflate(0, -44).move(0, 22))
        mouse = pygame.mouse.get_pos()
        self._sync_rows()
        for i, row in enumerate(self._rows):
            if row.rect.bottom < box.y + 44 or row.rect.y > box.bottom - 8:
                continue
            self._draw_row(surface, fonts, row, i, row.rect.collidepoint(mouse))
        surface.set_clip(old_clip)
        self._list.draw_bar(surface)

    def _draw_row(self, surface: pygame.Surface, fonts: theme.FontManager, row: AssetRow,
                  index: int, hovered: bool) -> None:
        prop = row.prop
        st = self.state
        selected = index == self.selected_index
        fill = "panel_hi" if selected else ("bg_alt" if hovered else "panel")
        theme.rounded_rect(surface, row.rect, theme.color(fill), radius=theme.RADIUS["lg"])
        theme.rounded_rect(surface, row.rect, None, radius=theme.RADIUS["lg"],
                           border=theme.color("accent" if selected else "border_soft"),
                           border_width=2 if selected else 1)

        band = self._district_color(prop)
        theme.rounded_rect(surface, pygame.Rect(row.rect.x + 6, row.rect.y + 8, 6, 46),
                           band, radius=3)

        owner_all = bool(prop.district) and st.district_owned_all(self.player.id, prop.district)
        theme.draw_text(surface, theme.truncate(prop.name, fonts.h3(), 210), fonts.h3(),
                        theme.color("text"), (row.rect.x + 20, row.rect.y + 8))
        theme.draw_text(surface, f"{prop.district or '独立地产'}"
                                 + ("（垄断 ×2）" if owner_all else ""),
                        fonts.micro(),
                        theme.color("success" if owner_all else "text_mute"),
                        (row.rect.x + 20, row.rect.y + 34))

        # 等级方块
        blocks = "▮" * prop.level + "▯" * (prop.max_level - prop.level)
        theme.draw_text(surface, blocks, fonts.sized(14, True),
                        theme.color("accent" if prop.level else "text_mute"),
                        (row.rect.right - 14, row.rect.y + 8), anchor="topright")

        rent = economy.rent_value(st, prop)
        theme.draw_text(surface, "抵押中" if prop.mortgaged else f"{rent:,}/圈",
                        fonts.small(),
                        theme.color("warning" if prop.mortgaged else "text_dim"),
                        (row.rect.right - 14, row.rect.y + 30), anchor="topright")

    def _district_color(self, prop: Property) -> tuple[int, int, int]:
        view = self.board_view
        if view is not None:
            return view.district_color(prop.district)
        return theme.color("t_property")

    def _draw_detail(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        box = pygame.Rect(rect.x + 20 + LIST_W + 12, self._body_top(),
                          DETAIL_W, self._body_height())
        theme.panel(surface, box, fill="panel", radius=theme.RADIUS["xl"])
        prop = self._selected()
        if prop is None:
            theme.draw_text(surface, "选择左侧的地产查看详情", fonts.body(),
                            theme.color("text_mute"), box.center, anchor="center")
            return
        st = self.state
        theme.section_header(surface, fonts,
                             pygame.Rect(box.x + 14, box.y + 10, box.width - 28, 22),
                             "地产详情", icon="info")

        band = self._district_color(prop)
        theme.rounded_rect(surface, pygame.Rect(box.x + 14, box.y + 46, 6, 30), band,
                           radius=3)
        theme.draw_text(surface, prop.name, fonts.h2(), theme.color("text"),
                        (box.x + 28, box.y + 44))
        theme.draw_text(surface, f"{prop.district or '独立地产'}", fonts.tiny(),
                        theme.color("text_mute"), (box.x + 28, box.y + 74))

        # 片区完成度
        if prop.district:
            ids = (st.district_props or {}).get(prop.district, [])
            owned = sum(1 for pid in ids
                        if st.properties.get(pid) is not None
                        and st.properties[pid].owner_id == self.player.id)
            total = len(ids)
            y = box.y + 98
            theme.draw_text(surface, f"片区完成度 {owned}/{total}", fonts.small(),
                            theme.color("success" if owned == total else "text_dim"),
                            (box.x + 20, y))
            bar = pygame.Rect(box.x + 20, y + 22, box.width - 40, 10)
            theme.progress_bar(surface, bar, owned / max(1, total),
                               color_name="success" if owned == total else "accent")
            rows = []
        else:
            rows = []

        preview = economy.rent_preview(st, prop)
        if prop.mortgaged:
            rent_line = "抵押中：暂不收租"
        else:
            rent_line = f"{money(preview['current'])} / 圈"
            if preview.get("district_bonus"):
                rent_line += "（含垄断 ×2）"

        level_blocks = "▮" * prop.level + "▯" * (prop.max_level - prop.level)
        rows = [
            ("地价", money(prop.price), "text"),
            ("等级", f"{prop.level}/{prop.max_level}　{level_blocks}", "text"),
            ("当前租金", rent_line, "accent"),
            ("已投入", money(prop.total_invested), "text"),
        ]
        if not prop.is_max_level:
            cost, cost_detail = economy.upgrade_cost(st, self.player, prop)
            rows.append(("升级费用", money(cost), "success" if self.player.money >= cost
                         else "danger"))
            rows.append(("升级后租金", money(preview["next"]), "text_dim"))
        else:
            rows.append(("升级", "已达最高等级", "success"))
        if prop.mortgaged:
            rows.append(("赎回需要", money(economy.redeem_cost(prop)), "warning"))
            redeem_value = economy.redeem_cost(prop)
            theme.draw_text(surface, "该地产处于抵押状态：不能收租，也不能升级。",
                            fonts.tiny(), theme.color("warning"),
                            (box.x + 20, box.bottom - 68))
        else:
            value, _ = economy.mortgage_value(st, self.player, prop)
            ok, why = economy.can_mortgage(prop)
            rows.append(("抵押可得", money(value) if ok else why, "accent"))
        refund, _ = economy.sell_refund(st, self.player, prop)
        rows.append(("出售回收", money(refund), "danger"))

        y = box.y + 142 if prop.district else box.y + 106
        for label, value, color_name in rows:
            theme.kv_row(surface, fonts, pygame.Rect(box.x + 20, y, box.width - 40, 22), y,
                         label, value, value_color=color_name, value_key="small")
            y += 26

    def _draw_actions(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        box = pygame.Rect(rect.right - 20 - RIGHT_W, self._body_top(),
                          RIGHT_W, self._body_height())
        theme.panel(surface, box, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, fonts,
                             pygame.Rect(box.x + 14, box.y + 10, box.width - 28, 22),
                             "可用操作", icon="gear")
        prop = self._selected()
        if prop is None:
            theme.draw_text(surface, "先选一块地产", fonts.body(), theme.color("text_mute"),
                            (box.centerx, box.centery), anchor="center")
            return
        if self.debt is not None:
            theme.draw_text(surface, "债务处理中：只能抵押或出售",
                            fonts.small(), theme.color("danger"), (box.x + 14, box.y + 42))
        elif self.busy:
            theme.draw_text(surface, "正在等待结算…", fonts.small(),
                            theme.color("text_mute"), (box.x + 14, box.y + 42))
        for button in self.buttons:
            button.draw(surface, fonts)

    def _draw_footer(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = self.rect
        footer = pygame.Rect(rect.x, rect.bottom - FOOTER_H, rect.width, FOOTER_H)
        theme.rounded_rect(surface, footer, theme.color("panel"), radius=20)
        pygame.draw.rect(surface, theme.color("panel"),
                         pygame.Rect(footer.x, footer.y, footer.width, 20))
        pygame.draw.line(surface, theme.color("border_soft"),
                         (footer.x + 20, footer.y + 1),
                         (footer.right - 20, footer.y + 1), 1)

        hint = ("正在等待结算…" if self.busy else
                "↑↓ 切换地产 · 抵押保留产权但不能收租 · 出售会永久失去这块地")
        if self.last_message:
            theme.draw_text(surface, self.last_message, fonts.small(),
                            theme.color("success" if self.last_ok else "danger"),
                            (rect.x + 28, footer.centery))
        else:
            theme.draw_text(surface, hint, fonts.small(), theme.color("text_mute"),
                            (rect.x + 28, footer.centery))

        if self.on_declare is not None:
            declare = Button(self._declare_rect(), self.declare_label,
                             on_click=self._trigger_declare, style="danger", font_size=16,
                             enabled=not self.busy, icon="alert",
                             tooltip="资产不足以偿还欠款时，只能退出本局")
            declare.draw(surface, fonts)

        close = Button(self._close_rect(), "关闭（I / ESC）", on_click=self.close,
                       style="secondary", font_size=16, icon="cross")
        close.draw(surface, fonts)

    def _trigger_declare(self) -> None:
        if self.busy or self.on_declare is None:
            return
        self.busy = True
        self._busy_revision = getattr(self.state, "revision", 0)
        self.on_declare()


def action_label(action: str) -> str:
    return {
        "upgrade": "升级", "sell": "出售", "mortgage": "抵押",
        "redeem": "赎回", "downgrade": "拆一级",
    }.get(action, action)
