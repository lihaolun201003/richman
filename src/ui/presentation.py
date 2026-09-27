"""演出层：把「引擎发生的事」翻译成玩家看得见的过程。

设计约束（很重要）：
- **演出绝不参与规则**。这里只读事件与账本，不改任何状态、不发任何命令；
- **演出不阻塞引擎**。队列只控制「画什么、画多久」，引擎按自己的阶段推进；
- 所有演出都能被点击跳过，且会自动超时消失，因此不会把玩家卡住。

三类演出：
    MoneyPop     资金浮动数字（来自 EconomyLedger，含对手方信息）
    EventCard    事件卡（福运 / 灾祸 / 机遇），带极性、标题、描述、实际效果
    ActionBanner 一句话横幅（AI 使用道具、玩家掉线、破产等）

`Presenter` 一次只显示一个主演出（横幅可以叠在下面），
新演出进来时旧演出立刻淡出，避免糊成一团。
"""
from __future__ import annotations

import time
from typing import Any

import pygame

from ..game.format import money_delta
from ..game.ledger import Reason
from ..utils.easing import clamp, ease_out_back, ease_out_cubic
from . import icons, theme

#: 极性 → (标题, 强调色, 图标)
POLARITY_STYLE = {
    "fortune": ("福 运", "success", "fortune"),
    "disaster": ("灾 祸", "danger", "disaster"),
    "neutral": ("机 遇", "accent", "star"),
    "info": ("通 知", "info", "bell"),
}

#: 资金流水分类 → 图标与语义（浮字用它，保证和事件日志一致）
REASON_STYLE = {
    Reason.START_REWARD: ("coin", "success"),
    Reason.INITIAL_MONEY: ("cash", "success"),
    Reason.PROPERTY_PURCHASE: ("property", "warning"),
    Reason.PROPERTY_UPGRADE: ("hammer", "accent"),
    Reason.PROPERTY_SALE: ("cash", "warning"),
    Reason.MORTGAGE: ("tag", "accent"),
    Reason.REDEEM: ("key", "primary"),
    Reason.RENT: ("coin", "danger"),
    Reason.TAX: ("tax", "danger"),
    Reason.CHANCE: ("star", "info"),
    Reason.CARD: ("card", "primary"),
    Reason.SHOP: ("shop", "warning"),
    Reason.JAIL: ("jail", "warning"),
    Reason.BONUS_POOL: ("trophy", "success"),
    Reason.BANKRUPT: ("alert", "danger"),
    Reason.CARD_STEAL: ("hand", "danger"),
    Reason.EVENT_FEE: ("alert", "danger"),
}


def reason_style(category: str) -> tuple[str, str]:
    return REASON_STYLE.get(category, ("coin", "text"))


def accent_for(polarity: str) -> str:
    """极性 → 颜色名（供动画层使用）。"""
    return POLARITY_STYLE.get(polarity, POLARITY_STYLE["neutral"])[1]


# ==================================================================== 资金浮字

class MoneyPop:
    """一笔资金变化的浮动数字。

    比单纯的 `+2,000` 多做三件事：
    1. 带图标（一眼看出是租金还是工资）；
    2. 带对方名字（「付给 电脑2」）；
    3. 收入绿、支出红，且支出方向朝下飘（方向本身就是信息）。
    """

    def __init__(self, text: str, sub: str, pos: tuple[float, float], *,
                 color_name: str = "success", icon: str = "coin",
                 duration: float = 1.25, size: int = 26) -> None:
        self.text = text
        self.sub = sub
        self.pos = pos
        self.color_name = color_name
        self.icon = icon
        self.duration = max(0.3, duration)
        self.size = size
        self.t = 0.0

    @property
    def progress(self) -> float:
        return clamp(self.t / self.duration, 0.0, 1.0)

    def update(self, dt: float) -> bool:
        self.t += dt
        return self.t < self.duration

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        p = self.progress
        rise = ease_out_cubic(p) * 54
        alpha = int(255 * (1.0 - max(0.0, (p - 0.6) / 0.4)))
        if alpha <= 4:
            return
        pop = 1.0 + 0.22 * (1.0 - ease_out_back(min(1.0, p * 3.2)))
        font = fonts.sized(self.size, True)
        sub_font = fonts.sized(theme.FONT["tiny"], True)

        img = font.render(self.text, True, theme.color(self.color_name))
        if pop > 1.01:
            img = pygame.transform.smoothscale(
                img, (max(1, int(img.get_width() * pop)), max(1, int(img.get_height() * pop))))
        img.set_alpha(alpha)
        sub_img = None
        if self.sub:
            sub_img = sub_font.render(self.sub, True, theme.color("text"))
            sub_img.set_alpha(int(alpha * 0.9))

        icon_size = max(16, int(self.size * 0.9))
        total_w = icon_size + 6 + img.get_width()
        x = int(self.pos[0] - total_w / 2)
        y = int(self.pos[1] - rise)
        # 底板：保证在浅色棋盘上也读得清
        plate = pygame.Rect(x - 10, y - 5, total_w + 20,
                            img.get_height() + (sub_img.get_height() + 2 if sub_img else 0) + 10)
        layer = pygame.Surface(plate.size, pygame.SRCALPHA)
        theme.rounded_rect(layer, layer.get_rect(), theme.color("bg", int(alpha * 0.62)),
                           radius=10)
        theme.rounded_rect(layer, layer.get_rect(), None, radius=10,
                           border=theme.color(self.color_name, alpha), border_width=2)
        surface.blit(layer, plate.topleft)

        icons.draw_icon(surface, self.icon,
                        pygame.Rect(x, y + (img.get_height() - icon_size) // 2,
                                    icon_size, icon_size),
                        theme.color(self.color_name, alpha), theme.color("shadow"))
        surface.blit(img, (x + icon_size + 6, y))
        if sub_img is not None:
            surface.blit(sub_img, (x + icon_size + 8, y + img.get_height() + 1))


# ==================================================================== 事件卡

class EventCard:
    """福运 / 灾祸 事件卡：标题 + 一句话描述 + 实际效果。

    不阻塞操作：点击任意处或到时间自动收起；期间引擎照常推进。
    """

    def __init__(self, polarity: str, title: str, description: str,
                 effect: str = "", *, actor: str = "", duration: float = 2.2) -> None:
        self.polarity = polarity if polarity in POLARITY_STYLE else "neutral"
        self.title = title
        self.description = description
        self.effect = effect
        self.actor = actor
        self.duration = max(0.8, duration)
        self.t = 0.0

    @property
    def progress(self) -> float:
        return clamp(self.t / self.duration, 0.0, 1.0)

    def update(self, dt: float) -> bool:
        self.t += dt
        return self.t < self.duration

    @property
    def rect(self) -> pygame.Rect:
        r = pygame.Rect(0, 0, 520, 250)
        r.center = (800, 430)
        return r

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        label, color_name, icon_name = POLARITY_STYLE[self.polarity]
        accent = theme.color(color_name)
        p = self.progress
        appear = ease_out_back(clamp(p * 4.0, 0.0, 1.0))
        fade = clamp((self.duration - self.t) / 0.35, 0.0, 1.0)
        alpha = int(255 * min(1.0, fade))
        scale = 0.9 + 0.1 * appear

        rect = self.rect
        scaled = pygame.Rect(0, 0, int(rect.width * scale), int(rect.height * scale))
        scaled.center = rect.center

        layer = pygame.Surface(scaled.size, pygame.SRCALPHA)
        local = pygame.Rect(0, 0, scaled.width, scaled.height)
        theme.rounded_rect(layer, local, theme.color("panel_alt"), radius=18)
        theme.rounded_rect(layer, local, None, radius=18, border=accent, border_width=3)
        theme.rounded_rect(layer, pygame.Rect(0, 0, scaled.width, 10), accent, radius=5)

        # 极性徽标
        badge = pygame.Rect(28, 28, 34, 34)
        icons.draw_icon(layer, icon_name, badge, accent, theme.color("shadow"))
        theme.draw_text(layer, label, fonts.sized(theme.FONT["h2"], True), accent,
                        (72, 34))
        if self.actor:
            theme.draw_text(layer, self.actor, fonts.sized(theme.FONT["small"]),
                            theme.color("text_dim"),
                            (scaled.width - 28, 40), anchor="topright")

        title_font = fonts.sized(theme.FONT["big"], True)
        theme.draw_text(layer, theme.truncate(self.title, title_font, scaled.width - 60),
                        title_font, theme.color("text"),
                        (scaled.centerx, 88), anchor="midtop")

        box = pygame.Rect(28, 138, scaled.width - 56, 74)
        theme.rounded_rect(layer, box, theme.color("bg_alt", 200), radius=12)
        theme.draw_wrapped(layer, self.description, fonts.sized(theme.FONT["small"]),
                           theme.color("text_dim"),
                           pygame.Rect(box.x + 14, box.y + 10, box.width - 28, box.height - 20))

        if self.effect:
            theme.draw_text(layer, self.effect, fonts.sized(theme.FONT["h3"], True),
                            accent, (scaled.centerx, scaled.bottom - 26), anchor="midbottom")
        layer.set_alpha(alpha)
        surface.blit(layer, scaled.topleft)


# ==================================================================== 横幅

class ActionBanner:
    """一句话横幅：AI 使用道具、掉线、破产、重连等短事件。"""

    def __init__(self, text: str, *, sub: str = "", color_name: str = "info",
                 icon: str = "", duration: float = 2.0) -> None:
        self.text = text
        self.sub = sub
        self.color_name = color_name
        self.icon = icon
        self.duration = max(0.6, duration)
        self.t = 0.0

    def update(self, dt: float) -> bool:
        self.t += dt
        return self.t < self.duration

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager,
             y: int = 620) -> None:
        p = clamp(self.t / self.duration, 0.0, 1.0)
        appear = ease_out_cubic(clamp(self.t / 0.22, 0.0, 1.0))
        fade = clamp((self.duration - self.t) / 0.3, 0.0, 1.0)
        alpha = int(255 * min(appear, fade))
        if alpha <= 4:
            return
        accent = theme.color(self.color_name)
        font = fonts.sized(theme.FONT["h3"], True)
        sub_font = fonts.sized(theme.FONT["tiny"])
        text = self.text
        w = font.size(text)[0] + 64
        if self.sub:
            w = max(w, sub_font.size(self.sub)[0] + 64)
        rect = pygame.Rect(0, 0, int(w), 46 if not self.sub else 62)
        rect.midtop = (800, y - int((1.0 - appear) * 16))

        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        theme.rounded_rect(layer, layer.get_rect(), theme.color("panel_alt", 238), radius=12)
        theme.rounded_rect(layer, layer.get_rect(), None, radius=12, border=accent,
                           border_width=2)
        theme.rounded_rect(layer, pygame.Rect(0, 8, 5, rect.height - 16), accent, radius=2)
        if self.icon:
            icons.draw_icon(layer, self.icon, pygame.Rect(16, 14, 20, 20), accent,
                            theme.color("shadow"))
        theme.draw_text(layer, text, font, theme.color("text"),
                        (rect.centerx + (10 if self.icon else 0), rect.centery if not self.sub
                         else 22), anchor="center")
        if self.sub:
            theme.draw_text(layer, self.sub, sub_font, theme.color("text_dim"),
                            (rect.centerx, rect.centery + 16), anchor="center")
        layer.set_alpha(alpha)
        surface.blit(layer, rect.topleft)


# ==================================================================== 管理器

class Presenter:
    """演出队列：一次一个主演出，横幅可并存，全部可点击跳过。"""

    def __init__(self) -> None:
        self.money: list[MoneyPop] = []
        self.cards: list[EventCard] = []
        self.banners: list[ActionBanner] = []
        self._card_gap = 0.0

    # ---- 入队

    def pop_money(self, text: str, pos: tuple[float, float], *,
                  sub: str = "", color_name: str = "success",
                  icon: str = "coin", size: int = 26) -> None:
        # 同一位置的浮字稍作错开，避免完全重叠
        jitter = (len(self.money) % 3 - 1) * 18
        self.money.append(MoneyPop(text, sub, (pos[0] + jitter, pos[1]),
                                   color_name=color_name, icon=icon, size=size))

    def push_card(self, card: EventCard) -> None:
        self.cards.append(card)
        if len(self.cards) > 3:
            del self.cards[:-3]

    def push_banner(self, banner: ActionBanner) -> None:
        self.banners.append(banner)
        if len(self.banners) > 4:
            del self.banners[:-4]

    # ---- 状态

    @property
    def has_card(self) -> bool:
        return bool(self.cards)

    def clear(self) -> None:
        self.money.clear()
        self.cards.clear()
        self.banners.clear()
        self._card_gap = 0.0

    def update(self, dt: float) -> None:
        self.money = [m for m in self.money if m.update(dt)]
        self.banners = [b for b in self.banners if b.update(dt)]
        self.cards = [c for c in self.cards if c.update(dt)]

    def dismiss_card(self) -> bool:
        """点击跳过当前事件卡。返回是否真的跳过了。"""
        if self.cards:
            self.cards[0].t = self.cards[0].duration
            return True
        return False

    # ---- 绘制

    def draw_world(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        """棋盘层演出：资金浮字。"""
        for pop in self.money:
            pop.draw(surface, fonts)

    def draw_overlay(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        """覆盖层演出：事件卡（后进先出，只画最上面一张）与横幅。"""
        if self.cards:
            self.cards[-1].draw(surface, fonts)
        for banner in self.banners[-2:]:
            banner.draw(surface, fonts)


def describe_ledger_entry(entry: Any, players: dict[str, Any]) -> tuple[str, str, str, str]:
    """把一条流水翻译成 (主文本, 副文本, 颜色名, 图标名)。

    这是资金反馈的唯一翻译点，保证浮字、日志、面板口径一致。
    """
    icon_name, color_name = reason_style(entry.category)
    amount = int(entry.amount)
    text = money_delta(amount)
    sub = entry.reason
    me = players.get(entry.player_id)
    other = players.get(entry.counterparty_id) if entry.counterparty_id else None
    if amount < 0:
        color_name = "danger"
        if other is not None:
            sub = f"付给 {other.name}"
        elif entry.reason == Reason.TAX:
            sub = "缴纳税款"
        else:
            sub = entry.reason
    else:
        if other is not None:
            sub = f"来自 {other.name}"
        elif entry.reason == Reason.START_REWARD:
            sub = "经过起点"
    if me is not None and entry.detail and len(entry.detail) <= 8:
        sub = entry.detail
    return text, sub, color_name, icon_name
