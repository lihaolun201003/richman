"""模态弹窗：购买、升级、机遇、监狱、破产、结算、暂停菜单。

关键约束：Modal 出现时会吃掉所有输入事件（handle_event 永远返回 True），
因此底层按钮不可能被穿透点击。
"""
from __future__ import annotations

import math
from typing import Any, Callable

import pygame

from ..game.commands import CommandType, PendingDecision
from ..game.events import money as fmt_money
from ..utils.easing import Tween, ease_out_back, ease_out_cubic
from . import theme
from .widgets import Button

#: 逻辑分辨率
SCREEN_W = 1600
SCREEN_H = 900


class Modal:
    """模态弹窗基类。"""

    #: 是否允许点击遮罩关闭
    dismissable = False

    def __init__(self, on_close: Callable[[], None] | None = None) -> None:
        self.done = False
        self.on_close = on_close
        self.anim = Tween(0.22)
        self.buttons: list[Button] = []
        self._closing = False

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.done = True
        if self.on_close is not None:
            self.on_close()

    # ---- 生命周期
    def handle_event(self, event: pygame.event.Event) -> bool:
        """模态弹窗吞掉所有事件。"""
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            self.on_key(event.key)
        return True

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE and self.dismissable:
            self.close()

    def update(self, dt: float) -> None:
        self.anim.update(dt)
        mouse = pygame.mouse.get_pos()
        for button in self.buttons:
            button.update(dt, mouse)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        raise NotImplementedError

    def _draw_scrim(self, surface: pygame.Surface) -> None:
        alpha = int(180 * ease_out_cubic(self.anim.progress))
        layer = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        layer.fill(theme.color("overlay", alpha))
        surface.blit(layer, (0, 0))

    def _panel(self, surface: pygame.Surface, rect: pygame.Rect) -> pygame.Rect:
        """带弹出动画的面板矩形。"""
        p = ease_out_back(self.anim.progress)
        scaled = pygame.Rect(0, 0, int(rect.width * (0.85 + 0.15 * p)),
                             int(rect.height * (0.85 + 0.15 * p)))
        scaled.center = rect.center
        theme.shadow_rect(surface, scaled, radius=18, spread=8, alpha=150)
        theme.rounded_rect(surface, scaled, theme.color("panel_alt"), radius=18)
        theme.rounded_rect(surface, scaled, None, radius=18, border=theme.color("border"),
                           border_width=2)
        return scaled


# ==================================================================== 通用决策

class DecisionDialog(Modal):
    """由 PendingDecision 直接生成的通用弹窗。"""

    def __init__(
        self,
        decision: PendingDecision,
        on_choose: Callable[[str], None],
        accent: str = "accent",
        extra_lines: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.decision = decision
        self.on_choose = on_choose
        self.accent = accent
        self.extra_lines = extra_lines or []
        self.rect = self._compute_rect()
        self._build_buttons()

    def _compute_rect(self) -> pygame.Rect:
        n = max(1, len(self.decision.options))
        width = 620
        height = 250 + n * 62
        if self.decision.description:
            height += 46
        if self.extra_lines:
            height += 22 * len(self.extra_lines)
        height = min(height, SCREEN_H - 90)
        return pygame.Rect(0, 0, width, height).move(
            (SCREEN_W - width) // 2, (SCREEN_H - height) // 2
        )

    def _build_buttons(self) -> None:
        self.buttons = []
        rect = self.rect
        y = rect.bottom - 26 - 54 * len(self.decision.options)
        for opt in self.decision.options:
            style = "ghost"
            if opt.id in ("buy", "upgrade", "roll", "confirm", "pay"):
                style = "primary"
            if opt.id in ("buy", "upgrade"):
                style = "accent"
            if opt.danger:
                style = "danger"
            if opt.id in ("skip", "cancel", "leave"):
                style = "ghost"
            label = opt.label
            button = Button(
                pygame.Rect(rect.x + 30, y, rect.width - 60, 50),
                label,
                on_click=lambda oid=opt.id: self._choose(oid),
                style=style,
                enabled=opt.enabled,
                tooltip=opt.hint,
            )
            self.buttons.append(button)
            y += 60

    def _choose(self, option_id: str) -> None:
        self.on_choose(option_id)
        self.close()

    def on_key(self, key: int) -> None:
        # 数字键快捷选择
        if pygame.K_1 <= key <= pygame.K_9:
            idx = key - pygame.K_1
            if idx < len(self.buttons) and self.buttons[idx].enabled:
                self.buttons[idx].on_click()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self.accent)

        # 顶部色条
        bar = pygame.Rect(rect.x, rect.y, rect.width, 8)
        theme.rounded_rect(surface, bar, accent, radius=4)

        theme.draw_text(surface, self.decision.title, fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 32), anchor="midtop")

        y = rect.y + 86
        if self.decision.description:
            theme.draw_wrapped(surface, self.decision.description, fonts.body(),
                               theme.color("text_dim"),
                               pygame.Rect(rect.x + 30, y, rect.width - 60, 60))
            y += 52
        for line in self.extra_lines:
            theme.draw_text(surface, line, fonts.small(), theme.color("text_mute"),
                            (rect.x + 30, y))
            y += 22

        for button in self.buttons:
            button.draw(surface, fonts)

        if self.dismissable:
            theme.draw_text(surface, "按 ESC 关闭", fonts.tiny(), theme.color("text_mute"),
                            (rect.centerx, rect.bottom - 12), anchor="midbottom")


# ==================================================================== 机遇卡

class ChanceCardDialog(Modal):
    """机遇卡翻出展示。"""

    def __init__(self, title: str, description: str, detail: str = "",
                 rarity: str = "common", on_close: Callable[[], None] | None = None) -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.title = title
        self.description = description
        self.detail = detail
        self.rarity = rarity
        self.rect = pygame.Rect(0, 0, 560, 360)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [
            Button(pygame.Rect(self.rect.x + 150, self.rect.bottom - 78, 260, 50),
                   "知道了", on_click=self.close, style="accent"),
        ]

    def _rarity_accent(self) -> str:
        return {"common": "info", "uncommon": "accent", "rare": "danger"}.get(self.rarity, "accent")

    def _rarity_label(self) -> str:
        return {"common": "普通", "uncommon": "稀有", "rare": "传说"}.get(self.rarity, "")

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self._rarity_accent())

        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 10), accent, radius=5)
        theme.draw_text(surface, "机 遇", fonts.h2(), accent,
                        (rect.centerx, rect.y + 30), anchor="midtop")
        theme.draw_text(surface, self._rarity_label(), fonts.tiny(), theme.color("text_mute"),
                        (rect.right - 24, rect.y + 34), anchor="topright")

        theme.draw_text(surface, self.title, fonts.sized(30, True), theme.color("text"),
                        (rect.centerx, rect.y + 80), anchor="midtop")

        box = pygame.Rect(rect.x + 36, rect.y + 138, rect.width - 72, 96)
        theme.rounded_rect(surface, box, theme.color("bg_alt"), radius=12)
        theme.draw_wrapped(surface, self.description, fonts.body(), theme.color("text_dim"),
                           pygame.Rect(box.x + 16, box.y + 14, box.width - 32, box.height - 24))

        if self.detail:
            theme.draw_text(surface, self.detail, fonts.sized(20, True), theme.color("success"),
                            (rect.centerx, rect.y + 250), anchor="midtop")

        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 结算

class GameOverDialog(Modal):
    """最终结算界面。"""

    def __init__(self, ranking: list[dict[str, Any]], rounds: int, stats: dict[str, Any],
                 on_again: Callable[[], None] | None = None,
                 on_exit: Callable[[], None] | None = None,
                 can_restart: bool = True) -> None:
        super().__init__()
        self.ranking = ranking
        self.rounds = rounds
        self.stats = stats
        self.rect = pygame.Rect(0, 0, 940, 620)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = []
        if can_restart and on_again is not None:
            self.buttons.append(Button(
                pygame.Rect(self.rect.x + 165, self.rect.bottom - 78, 280, 54),
                "再来一局", on_click=on_again, style="accent"))
            self.buttons.append(Button(
                pygame.Rect(self.rect.x + 495, self.rect.bottom - 78, 280, 54),
                "返回主菜单", on_click=on_exit, style="secondary"))
        elif on_exit is not None:
            self.buttons.append(Button(
                pygame.Rect(self.rect.centerx - 140, self.rect.bottom - 76, 280, 54),
                "返回", on_click=on_exit, style="accent"))

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 12),
                           theme.color("accent"), radius=6)

        winner = self.ranking[0] if self.ranking else None
        title = f"{winner['name']} 获胜！" if winner else "游戏结束"
        theme.draw_text(surface, title, fonts.sized(40, True), theme.color("accent"),
                        (rect.centerx, rect.y + 34), anchor="midtop")
        theme.draw_text(surface,
                        f"共进行 {self.rounds} 轮 · "
                        f"{self.stats.get('bankrupt_count', 0)} 人破产 · "
                        f"全场收租 {self.stats.get('total_rent_income', 0):,}",
                        fonts.body(), theme.color("text_dim"),
                        (rect.centerx, rect.y + 88), anchor="midtop")

        # 表头
        table_x = rect.x + 40
        table_y = rect.y + 132
        col = [0, 60, 230, 400, 560, 700]
        headers = ["名次", "玩家", "最终资金", "地产数量", "地产总值", "收租 / 支出"]
        for i, text in enumerate(headers):
            theme.draw_text(surface, text, fonts.small(), theme.color("text_mute"),
                            (table_x + col[i], table_y))
        pygame.draw.line(surface, theme.color("border"),
                         (table_x, table_y + 24), (rect.right - 40, table_y + 24), 1)

        y = table_y + 34
        from .player_panel import _color_of

        for row in self.ranking[:6]:
            highlight = row.get("winner")
            row_rect = pygame.Rect(table_x - 12, y - 4, rect.width - 76, 40)
            if highlight:
                theme.rounded_rect(surface, row_rect, theme.color("accent", 40), radius=8)
            color = theme.hex_to_rgb(_color_of(row.get("color_id", "")))
            pygame.draw.circle(surface, color, (table_x + 8, y + 14), 9)

            stats = row.get("stats") or {}
            titles = row.get("titles") or []
            name_text = row["name"]
            if highlight:
                name_text += "（冠军）"
            elif row["bankrupt"]:
                name_text += "（已破产）"
            if titles:
                name_text += " · " + "、".join(titles)
            values = [
                f"{row['rank']}",
                name_text,
                f"{row['money']:,}",
                f"{row['property_count']}",
                f"{row['property_value']:,}",
                f"{stats.get('rent_income', 0):,} / {stats.get('rent_paid', 0):,}",
            ]
            for i, text in enumerate(values):
                color_name = "text" if i != 4 else "accent"
                theme.draw_text(surface, theme.truncate(text, fonts.small(),
                                                        (col[4] - col[3]) if i == 3 else 260),
                                fonts.small(),
                                theme.color(color_name) if not row["bankrupt"] else theme.color("text_mute"),
                                (table_x + col[i], y + 4))
            y += 44

        # 汇总统计
        sy = y + 6
        summary = (
            f"购地 {self.stats.get('total_bought', 0)} 次 · "
            f"升级 {self.stats.get('total_upgraded', 0)} 次 · "
            f"道具 {self.stats.get('total_cards_used', 0)} 张 · "
            f"机遇 {self.stats.get('total_chance', 0)} 次 · "
            f"经过起点 {self.stats.get('total_start_passes', 0)} 次"
        )
        theme.draw_text(surface, summary, fonts.small(), theme.color("text_dim"),
                        (rect.centerx, sy), anchor="midtop")
        # 全场资金流：钱主要从哪来、往哪去
        flow = self.stats.get("flow") or {}
        if flow:
            inc = "、".join(f"{k} {v:,}" for k, v in list(flow.get("income", {}).items())[:3])
            exp = "、".join(f"{k} {v:,}" for k, v in list(flow.get("expense", {}).items())[:3])
            theme.draw_text(surface, f"全场收入主要来自：{inc}", fonts.tiny(),
                            theme.color("text_mute"), (rect.centerx, sy + 22),
                            anchor="midtop")
            theme.draw_text(surface, f"全场支出主要在：{exp}", fonts.tiny(),
                            theme.color("text_mute"), (rect.centerx, sy + 40),
                            anchor="midtop")

        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 暂停

class PauseMenu(Modal):
    """ESC 暂停菜单。"""

    def __init__(self, on_resume: Callable[[], None], items: list[tuple[str, Callable[[], None], str]],
                 note: str = "") -> None:
        super().__init__()
        self.dismissable = True
        self.note = note
        self.rect = pygame.Rect(0, 0, 460, 150 + len(items) * 62)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = []
        y = self.rect.y + 96
        for text, callback, style in items:
            self.buttons.append(Button(pygame.Rect(self.rect.x + 40, y, self.rect.width - 80, 52),
                                       text, on_click=callback, style=style))
            y += 62
        self.on_resume = on_resume

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.close()
            self.on_resume()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        theme.draw_text(surface, "暂 停", fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 28), anchor="midtop")
        if self.note:
            theme.draw_text(surface, self.note, fonts.small(), theme.color("text_mute"),
                            (rect.centerx, rect.y + 68), anchor="midtop")
        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 道具目标

class CardTargetDialog(Modal):
    """选择道具使用目标。"""

    def __init__(
        self,
        card_name: str,
        card_desc: str,
        targets: list[tuple[Any, str]],     # (target_id, label)
        on_pick: Callable[[Any], None],
        on_cancel: Callable[[], None] | None = None,
        columns: int = 2,
    ) -> None:
        super().__init__()
        self.dismissable = True
        self.card_name = card_name
        self.card_desc = card_desc
        self.targets = targets
        self.on_pick = on_pick
        self.on_cancel = on_cancel

        rows = max(1, math.ceil(len(targets) / columns))
        height = 200 + min(rows, 6) * 52 + 70
        self.rect = pygame.Rect(0, 0, 720, min(height, SCREEN_H - 100))
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self._build_buttons(columns)

    def _build_buttons(self, columns: int) -> None:
        self.buttons = []
        rect = self.rect
        per_col = max(1, math.ceil(len(self.targets) / columns))
        w = (rect.width - 60 - (columns - 1) * 12) // columns
        for i, (target_id, label) in enumerate(self.targets):
            c = i // per_col
            r = i % per_col
            x = rect.x + 30 + c * (w + 12)
            y = rect.y + 130 + r * 52
            if y + 44 > rect.bottom - 70:
                break
            self.buttons.append(Button(
                pygame.Rect(x, y, w, 44), theme.truncate(label, self._font_probe(), w - 20),
                on_click=lambda tid=target_id: self._pick(tid), style="secondary", font_size=16))
        self.buttons.append(Button(
            pygame.Rect(rect.centerx - 100, rect.bottom - 62, 200, 44),
            "取消", on_click=self._cancel, style="ghost"))

    def _font_probe(self):
        return pygame.font.Font(None, 16)

    def _pick(self, target_id: Any) -> None:
        self.close()
        self.on_pick(target_id)

    def _cancel(self) -> None:
        self.close()
        if self.on_cancel is not None:
            self.on_cancel()

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self._cancel()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8),
                           theme.color("primary"), radius=4)
        theme.draw_text(surface, f"使用「{self.card_name}」", fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 28), anchor="midtop")
        theme.draw_text(surface, self.card_desc, fonts.small(), theme.color("text_dim"),
                        (rect.centerx, rect.y + 72), anchor="midtop")
        theme.draw_text(surface, "请选择目标", fonts.small(), theme.color("text_mute"),
                        (rect.centerx, rect.y + 100), anchor="midtop")
        for button in self.buttons:
            button.draw(surface, fonts)


class MessageDialog(Modal):
    """简单信息弹窗。"""

    def __init__(self, title: str, message: str, accent: str = "info",
                 on_close: Callable[[], None] | None = None,
                 button_text: str = "确定") -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.title = title
        self.message = message
        self.accent = accent
        self.rect = pygame.Rect(0, 0, 560, 300)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [Button(
            pygame.Rect(self.rect.x + 150, self.rect.bottom - 76, 260, 50),
            button_text, on_click=self.close, style="primary")]

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self.accent)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8), accent, radius=4)
        theme.draw_text(surface, self.title, fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 32), anchor="midtop")
        theme.draw_wrapped(surface, self.message, fonts.body(), theme.color("text_dim"),
                           pygame.Rect(rect.x + 34, rect.y + 92, rect.width - 68, 130))
        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 商店

class ShopDialog(Modal):
    """商店：以卡片形式展示可购买的道具。"""

    def __init__(
        self,
        tile_name: str,
        offers: list[dict],
        player_money: int,
        on_buy: Callable[[str], None],
        on_leave: Callable[[], None],
        inventory: int = 0,
        inventory_limit: int = 5,
        price_of: Callable[[str], int] | None = None,
    ) -> None:
        super().__init__()
        self.dismissable = False
        self.tile_name = tile_name
        self.offers = offers
        self.player_money = player_money
        self.on_buy = on_buy
        self.on_leave = on_leave
        self.inventory = inventory
        self.inventory_limit = inventory_limit
        self.price_of = price_of or (lambda cid: 0)

        n = max(1, len(offers))
        width = min(1180, 240 + n * 300)
        self.rect = pygame.Rect(0, 0, width, 470)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [Button(
            pygame.Rect(self.rect.centerx - 130, self.rect.bottom - 74, 260, 52),
            "不买了", on_click=self._leave, style="ghost")]
        self._card_rects: list[pygame.Rect] = []
        self._layout()

    def _layout(self) -> None:
        n = max(1, len(self.offers))
        gap = 18
        w = (self.rect.width - 60 - gap * (n - 1)) // n
        h = 230
        self._card_rects = []
        for i in range(n):
            self._card_rects.append(pygame.Rect(
                self.rect.x + 30 + i * (w + gap), self.rect.y + 116, w, h))

    def _leave(self) -> None:
        self.close()
        self.on_leave()

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self._leave()
        elif pygame.K_1 <= key <= pygame.K_9:
            idx = key - pygame.K_1
            if idx < len(self.offers) and self._can_buy(self.offers[idx]):
                self._buy(self.offers[idx]["id"])

    def _can_buy(self, offer: dict) -> bool:
        if self.inventory >= self.inventory_limit:
            return False
        return self.player_money >= self.price_of(offer["id"])

    def _buy(self, card_id: str) -> None:
        self.close()
        self.on_buy(card_id)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, offer in zip(self._card_rects, self.offers):
                if rect.collidepoint(event.pos):
                    if self._can_buy(offer):
                        self._buy(offer["id"])
                    return True
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            self.on_key(event.key)
        return True

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)

        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8),
                           theme.color("accent"), radius=4)
        theme.draw_text(surface, f"商 店 · {self.tile_name}", fonts.h1(),
                        theme.color("text"), (rect.centerx, rect.y + 26), anchor="midtop")
        theme.draw_text(surface,
                        f"你的现金 {self.player_money:,}   道具 {self.inventory}/{self.inventory_limit}"
                        f"    （每个商店限购一张）",
                        fonts.small(), theme.color("text_dim"),
                        (rect.centerx, rect.y + 72), anchor="midtop")

        mouse = pygame.mouse.get_pos()
        for card_rect, offer in zip(self._card_rects, self.offers):
            self._draw_offer(surface, fonts, card_rect, offer, mouse)

        for button in self.buttons:
            button.draw(surface, fonts)

    def _draw_offer(self, surface: pygame.Surface, fonts: theme.FontManager,
                    rect: pygame.Rect, offer: dict, mouse: tuple[int, int]) -> None:
        can = self._can_buy(offer)
        hovered = rect.collidepoint(mouse) and can
        rarity_color = {"common": "info", "uncommon": "accent", "rare": "danger"}.get(
            offer.get("rarity", "common"), "info")
        accent = theme.color(rarity_color)

        theme.rounded_rect(surface, rect,
                           theme.color("panel_hi") if hovered else theme.color("panel"),
                           radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=accent if can else theme.color("border_soft"),
                           border_width=2 if hovered else 1)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 6),
                           accent, radius=3)

        theme.draw_text(surface, offer["name"], fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 18))
        theme.draw_wrapped(surface, offer.get("description", ""), fonts.small(),
                           theme.color("text_dim"),
                           pygame.Rect(rect.x + 16, rect.y + 50, rect.width - 32, 96))

        timing = offer.get("timing_label", "")
        if timing:
            theme.draw_text(surface, timing, fonts.micro(), theme.color("text_mute"),
                            (rect.x + 16, rect.bottom - 62))

        price = self.price_of(offer["id"])
        price_color = "accent" if can else "danger"
        theme.draw_text(surface, f"{price:,}", fonts.h2(), theme.color(price_color),
                        (rect.x + 16, rect.bottom - 40))

        if not can:
            reason = "道具已满" if self.inventory >= self.inventory_limit else "现金不足"
            theme.draw_text(surface, reason, fonts.small(), theme.color("danger"),
                            (rect.right - 16, rect.bottom - 34), anchor="topright")
        else:
            theme.draw_text(surface, "点击购买", fonts.small(),
                            theme.color("accent" if hovered else "text_mute"),
                            (rect.right - 16, rect.bottom - 34), anchor="topright")
