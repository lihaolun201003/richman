"""本局记录：按轮次分组的玩家视角历史。

单独一个文件的原因：
- 「玩家日志」与「开发日志」是两件事。这里只显示引擎写好的中文事件消息，
  绝不出现 revision / packet / command / hash 这类字段（那些只在 F1 面板里）；
- 结算页也要复用同一套筛选与配色，避免两处各写一份。

内容全部**只读** `GameState.event_log`：打开记录不会改变任何游戏状态。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from ..game.events import EventType
from . import icons, theme
from .dialogs import SCREEN_H, SCREEN_W, Modal
from .widgets import Button

#: 与金钱直接相关的事件（「只看钱」筛选用）
MONEY_EVENTS = frozenset({
    EventType.PROPERTY_BOUGHT, EventType.RENT_PAID, EventType.TAX_PAID,
    EventType.BONUS_POOL_GAINED, EventType.BONUS_POOL_PAID, EventType.PROPERTY_SOLD,
    EventType.PROPERTY_MORTGAGED, EventType.PROPERTY_UNMORTGAGED,
    EventType.ASSET_LIQUIDATED, EventType.PROPERTY_TRANSFERRED,
    EventType.PROPERTY_UPGRADED,
})

#: 与地产直接相关的事件（「只看地产」筛选用）
PROPERTY_EVENTS = frozenset({
    EventType.PROPERTY_BOUGHT, EventType.PROPERTY_UPGRADED, EventType.PROPERTY_SOLD,
    EventType.PROPERTY_MORTGAGED, EventType.PROPERTY_UNMORTGAGED,
    EventType.PROPERTY_TRANSFERRED, EventType.PROPERTY_RELEASED,
    EventType.PROPERTY_MONOPOLY, EventType.PROPERTY_SKIPPED,
})

#: 不该出现在「本局记录」里的内部流水（阶段变化、逐格移动等）
HIDDEN_EVENTS = frozenset({
    EventType.PHASE_CHANGED, EventType.PLAYER_MOVED,
})

#: 事件类型 → (颜色名, 图标名)。与对局侧栏的配色保持一致。
_LOG_STYLE: dict[str, tuple[str, str]] = {
    EventType.PROPERTY_BOUGHT: ("success", "property"),
    EventType.RENT_PAID: ("warning", "coin"),
    EventType.PASSED_START: ("accent", "start"),
    EventType.CHANCE_DRAWN: ("info", "star"),
    EventType.CHANCE_APPLIED: ("text_dim", "star"),
    EventType.BANKRUPT: ("danger", "alert"),
    EventType.GAME_OVER: ("accent", "trophy"),
    EventType.CARD_USED: ("primary", "card"),
    EventType.CARD_GAINED: ("text_dim", "card"),
    EventType.TURN_START: ("accent", ""),
    EventType.JAIL_ENTERED: ("warning", "jail"),
    EventType.JAIL_RELEASED: ("success", "key"),
    EventType.JAIL_PAID: ("warning", "key"),
    EventType.DEBT_STARTED: ("danger", "alert"),
    EventType.BONUS_POOL_GAINED: ("success", "trophy"),
    EventType.BONUS_POOL_PAID: ("warning", "trophy"),
    EventType.TAX_PAID: ("danger", "tax"),
    EventType.PROPERTY_UPGRADED: ("accent", "hammer"),
    EventType.PROPERTY_SOLD: ("warning", "cash"),
    EventType.PROPERTY_MORTGAGED: ("warning", "tag"),
    EventType.PROPERTY_UNMORTGAGED: ("success", "key"),
    EventType.ASSET_LIQUIDATED: ("warning", "cash"),
    EventType.PLAYER_DISCONNECTED: ("danger", "network"),
    EventType.PLAYER_RECONNECTED: ("success", "network"),
    EventType.PLAYER_BOT_TAKEOVER: ("warning", "person"),
    EventType.PROPERTY_MONOPOLY: ("accent", "property"),
}


def log_style(etype: str) -> tuple[str, str]:
    return _LOG_STYLE.get(etype, ("text_dim", ""))


def visible_events(state: Any) -> list[Any]:
    """玩家视角能看到的事件序列（已剔除内部流水）。"""
    return [e for e in (getattr(state, "event_log", None) or [])
            if e.type not in HIDDEN_EVENTS]


def passes_filter(ev: Any, key: str, my_player_id: str) -> bool:
    if key == "all":
        return True
    if key == "mine":
        return ev.player_id == my_player_id
    if key == "money":
        return ev.type in MONEY_EVENTS
    if key == "property":
        return ev.type in PROPERTY_EVENTS
    return True


class PlayerLogDialog(Modal):
    """本局记录弹窗：按轮次分组、可筛选、可滚动。"""

    FILTERS = [
        ("全部", "all"),
        ("只看我", "mine"),
        ("只看钱", "money"),
        ("只看地产", "property"),
    ]

    ROW_H = 26

    def __init__(self, state: Any, my_player_id: str = "",
                 on_close: Callable[[], None] | None = None,
                 title: str = "本局记录") -> None:
        super().__init__(on_close)
        self.state = state
        self.my_player_id = my_player_id
        self.title = title
        self.filter_key = "all"
        self.scroll = 0.0
        self.rect = pygame.Rect(0, 0, 1040, 720)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self._filter_rects: list[tuple[pygame.Rect, str, str]] = []
        self._rows: list[tuple[str, str, str]] = []
        self._content_height = 0
        self._build_buttons()
        self.rebuild()

    def _build_buttons(self) -> None:
        self.buttons = [
            Button(pygame.Rect(self.rect.right - 168, self.rect.bottom - 68, 140, 48),
                   "关闭", on_click=self.close, style="secondary", font_size=16),
        ]
        x = self.rect.x + 28
        for label, key in self.FILTERS:
            rect = pygame.Rect(x, self.rect.y + 70, 108, 34)
            self._filter_rects.append((rect, key, label))
            x += 118

    # ------------------------------------------------------------ 数据

    def rebuild(self) -> None:
        rows: list[tuple[str, str, str]] = []
        last_round: int | None = None
        for ev in visible_events(self.state):
            if not passes_filter(ev, self.filter_key, self.my_player_id):
                continue
            rnd = self._round_of(ev)
            if rnd != last_round:
                last_round = rnd
                rows.append(("group", f"第 {rnd} 轮", "accent"))
            color, _icon = log_style(ev.type)
            rows.append(("item", ev.message, color))
        self._rows = rows[-400:]
        self._content_height = len(self._rows) * self.ROW_H + 24

    def _round_of(self, ev: Any) -> int:
        data = ev.data or {}
        value = data.get("round")
        if isinstance(value, (int, float)):
            return int(value)
        return int(getattr(self.state, "round_number", 0) or 0)

    # ------------------------------------------------------------ 事件

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0.0, min(self.max_scroll, self.scroll - event.y * 56))
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, key, _label in self._filter_rects:
                if rect.collidepoint(event.pos):
                    self.filter_key = key
                    self.scroll = 0.0
                    self.rebuild()
                    return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return True
        return super().handle_event(event)

    @property
    def body_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x + 20, self.rect.y + 120,
                           self.rect.width - 40, self.rect.height - 212)

    @property
    def max_scroll(self) -> float:
        return max(0.0, self._content_height - self.body_rect.height)

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)

        icons.draw_icon(surface, "book", pygame.Rect(rect.x + 26, rect.y + 22, 28, 28),
                        theme.color("accent"), theme.color("shadow"))
        theme.draw_text(surface, self.title, fonts.h2(), theme.color("text"),
                        (rect.x + 64, rect.y + 20))
        note = (f"第 {getattr(self.state, 'round_number', 0)} 轮　·　"
                f"{len(self._rows)} 条记录　·　滚轮翻看")
        theme.draw_text(surface, note, fonts.small(), theme.color("text_mute"),
                        (rect.right - 28, rect.y + 30), anchor="topright")
        theme.draw_text(surface, "这里只记录发生过的游戏事件，不含开发信息。",
                        fonts.tiny(), theme.color("text_mute"), (rect.x + 66, rect.y + 50))

        mouse = pygame.mouse.get_pos()
        for frect, key, label in self._filter_rects:
            active = key == self.filter_key
            hovered = frect.collidepoint(mouse)
            theme.rounded_rect(surface, frect,
                               theme.color("accent" if active
                                           else ("panel_hi" if hovered else "bg_alt")),
                               radius=theme.RADIUS["sm"])
            theme.draw_text(surface, label, fonts.small(),
                            theme.color("text_dark" if active else "text_dim"),
                            frect.center, anchor="center")

        body = self.body_rect
        theme.rounded_rect(surface, body, theme.color("bg_alt"), radius=theme.RADIUS["lg"])
        clip = surface.get_clip()
        surface.set_clip(body)
        y = body.y + 12 - self.scroll
        for kind, text, color in self._rows:
            if y > body.bottom:
                break
            if y + self.ROW_H < body.y:
                y += self.ROW_H
                continue
            if kind == "group":
                band = pygame.Rect(body.x + 8, y + 1, body.width - 16, 22)
                theme.rounded_rect(surface, band, theme.color("panel"), radius=6)
                theme.draw_text(surface, text, fonts.small(), theme.color("accent"),
                                (band.x + 12, y + 3))
            else:
                pygame.draw.circle(surface, theme.color(color),
                                   (body.x + 22, y + 12), 3)
                theme.draw_text(surface,
                                theme.truncate(text, fonts.small(), body.width - 70),
                                fonts.small(), theme.color(color), (body.x + 36, y + 2))
            y += self.ROW_H
        surface.set_clip(clip)

        if self.max_scroll > 0:
            frac = self.scroll / self.max_scroll
            track = pygame.Rect(body.right - 10, body.y + 8, 4, body.height - 16)
            theme.rounded_rect(surface, track, theme.color("bg"), radius=2)
            ratio = body.height / max(1.0, self._content_height)
            h = max(28, int(track.height * min(1.0, ratio)))
            knob = pygame.Rect(track.x, track.y + int((track.height - h) * frac), 4, h)
            theme.rounded_rect(surface, knob, theme.color("accent"), radius=2)
        if not self._rows:
            theme.draw_text(surface, "这个筛选下还没有记录", fonts.body(),
                            theme.color("text_mute"), body.center, anchor="center")

        for button in self.buttons:
            button.draw(surface, fonts)
