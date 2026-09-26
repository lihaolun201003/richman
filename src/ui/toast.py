"""Toast 提示：屏幕上方/右下角飘出的短消息。"""
from __future__ import annotations

import time
from typing import Any

import pygame

from ..utils.easing import clamp, ease_out_cubic
from . import theme

KIND_ACCENT = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
    "accent": "accent",
}


class Toast:
    __slots__ = ("text", "kind", "created", "duration")

    def __init__(self, text: str, kind: str = "info", duration: float = 3.2) -> None:
        self.text = text
        self.kind = kind
        self.created = time.time()
        self.duration = duration

    @property
    def age(self) -> float:
        return time.time() - self.created

    @property
    def alive(self) -> bool:
        return self.age < self.duration


class ToastManager:
    """管理一列提示。最多同时显示 4 条，自动淡出。"""

    def __init__(self, max_visible: int = 4) -> None:
        self.items: list[Toast] = []
        self.max_visible = max_visible

    def push(self, text: str, kind: str = "info", duration: float = 3.2) -> None:
        if not text:
            return
        # 完全相同的消息短时间内不重复堆叠
        for t in self.items[-3:]:
            if t.text == text and t.age < 1.2:
                t.created = time.time()
                return
        self.items.append(Toast(text, kind, duration))
        if len(self.items) > 12:
            del self.items[:-12]

    def clear(self) -> None:
        self.items.clear()

    def update(self, dt: float) -> None:
        self.items = [t for t in self.items if t.alive]

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager,
             center_x: int = 800, bottom_y: int = 862) -> None:
        """从底部往上堆叠，水平居中——不遮挡顶部的玩家面板与轮次信息。"""
        visible = self.items[-self.max_visible:]
        y = bottom_y
        for toast in reversed(visible):
            age = toast.age
            appear = clamp(age / 0.22, 0.0, 1.0)
            fade = clamp((toast.duration - age) / 0.45, 0.0, 1.0)
            alpha = int(255 * min(ease_out_cubic(appear), fade))
            if alpha <= 4:
                continue
            y -= 50
            self._draw_one(surface, fonts, toast, center_x, y, alpha, appear, centered=True)

    def _draw_one(self, surface: pygame.Surface, fonts: theme.FontManager, toast: Toast,
                  anchor_x: int, y: int, alpha: int, appear: float,
                  centered: bool = False) -> None:
        font = fonts.small()
        text = theme.truncate(toast.text, font, 460)
        width = font.size(text)[0] + 30
        height = 42
        slide = int((1.0 - ease_out_cubic(appear)) * 30)
        if centered:
            rect = pygame.Rect(anchor_x - width // 2, y + slide, width, height)
        else:
            rect = pygame.Rect(anchor_x - width + slide, y, width, height)

        accent = theme.color(KIND_ACCENT.get(toast.kind, "info"))

        theme.shadow_rect(surface, rect, radius=10, spread=4, alpha=int(alpha * 0.4))
        theme.rounded_rect(surface, rect, theme.color("panel_alt", alpha), radius=10)
        theme.rounded_rect(surface, rect, None, radius=10, border=accent, border_width=2)
        bar = pygame.Rect(rect.x + 4, rect.y + 6, 4, rect.height - 12)
        theme.rounded_rect(surface, bar, accent, radius=2)

        text_color = theme.color("text")
        theme.draw_text(surface, text, font, (*text_color, alpha),
                        (rect.x + 18, rect.centery), anchor="midleft")
