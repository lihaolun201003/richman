"""动画系统：浮动文字、骰子、卡牌、棋子移动、脉冲高亮。

所有动画都只影响「显示」，不改动任何规则状态。
动画速度倍率在 AnimationManager 内统一处理，
因此设置里调整 0.5x / 2.0x 会立即对所有动画生效。
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable

import pygame

from ..utils.easing import Tween, clamp, ease_out_back, ease_out_cubic, ease_out_quad
from . import theme


class Animation:
    """动画基类。"""

    def __init__(self, duration: float = 0.5) -> None:
        self.tween = Tween(duration)
        self.done = False

    def update(self, dt: float) -> None:
        p = self.tween.update(dt)
        self.done = self.tween.done
        self.on_progress(p)

    def on_progress(self, p: float) -> None:
        ...

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        ...

    @property
    def progress(self) -> float:
        return self.tween.progress


# ==================================================================== 浮动文字

class FloatingText(Animation):
    """资金增减等浮动数字。"""

    def __init__(self, text: str, pos: tuple[float, float], color_name: str = "success",
                 duration: float = 1.1, size: int = 24, rise: int = 60) -> None:
        super().__init__(duration)
        self.text = text
        self.pos = pos
        self.color_name = color_name
        self.size = size
        self.rise = rise

    def on_progress(self, p: float) -> None:
        pass

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        p = self.progress
        y = self.pos[1] - self.rise * ease_out_cubic(p)
        alpha = int(255 * (1.0 - max(0.0, (p - 0.55) / 0.45)))
        if alpha <= 4:
            return
        font = fonts.sized(self.size, True)
        scale = 1.0 + 0.25 * (1.0 - ease_out_back(min(1.0, p * 3.0)))
        img = font.render(self.text, True, theme.color(self.color_name))
        if scale > 1.01:
            w = max(1, int(img.get_width() * scale))
            h = max(1, int(img.get_height() * scale))
            img = pygame.transform.smoothscale(img, (w, h))
        img.set_alpha(alpha)
        shadow = font.render(self.text, True, (0, 0, 0))
        shadow.set_alpha(int(alpha * 0.55))
        rect = img.get_rect(center=(int(self.pos[0]), int(y)))
        surface.blit(shadow, (rect.x + 2, rect.y + 2))
        surface.blit(img, rect)


# ==================================================================== 骰子

DICE_PIPS = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
}


def draw_die(surface: pygame.Surface, rect: pygame.Rect, value: int,
             face_color: tuple[int, int, int] = (252, 250, 244),
             pip_color: tuple[int, int, int] = (44, 54, 70),
             radius: int = 10) -> None:
    """绘制一个骰子面。"""
    theme.rounded_rect(surface, rect, face_color, radius=radius)
    theme.rounded_rect(surface, rect, None, radius=radius,
                       border=theme.darken(face_color, 0.25), border_width=2)
    size = min(rect.width, rect.height)
    step = size // 4
    cx, cy = rect.centerx, rect.centery
    r = max(3, size // 10)
    for dx, dy in DICE_PIPS.get(int(value), []):
        pygame.draw.circle(surface, pip_color, (cx + dx * step, cy + dy * step), r)


class DiceRollAnimation(Animation):
    """骰子滚动动画：先快速跳变，最后停在 Host 给的真实点数上。

    注意：动画中的随机数字纯粹是视觉效果，绝不影响最终结果。
    """

    def __init__(
        self,
        center: tuple[int, int],
        die_size: int,
        result: tuple[int, int],
        duration: float = 1.1,
        show_total: bool = True,
    ) -> None:
        super().__init__(duration)
        self.center = center
        self.die_size = die_size
        self.result = result
        self.show_total = show_total
        self._rng = random.Random()
        self._faces = (1, 1)
        self._change_timer = 0.0
        self.settled = False

    def update(self, dt: float) -> None:
        super().update(dt)
        if self.settled:
            return
        self._change_timer -= dt
        if self._change_timer <= 0:
            self._change_timer = 0.045 + self.progress * 0.16
            self._faces = (self._rng.randint(1, 6), self._rng.randint(1, 6))
        # 后 30% 时间直接显示最终结果，制造"停下来"的观感
        if self.progress >= 0.7:
            self._faces = self.result
            self.settled = True

    @property
    def faces(self) -> tuple[int, int]:
        return self._faces if not self.done else self.result

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if self.done:
            return
        size = self.die_size
        gap = 14
        total_w = size * 2 + gap
        rect1 = pygame.Rect(0, 0, size, size)
        rect2 = pygame.Rect(0, 0, size, size)
        scale = 1.0 + 0.12 * math.sin(self.progress * math.pi * 3)
        s = int(size * scale)
        rect1.size = (s, s)
        rect2.size = (s, s)
        rect1.center = (self.center[0] - total_w // 2 + size // 2, self.center[1])
        rect2.center = (self.center[0] + total_w // 2 - size // 2, self.center[1])

        for rect, value in ((rect1, self._faces[0]), (rect2, self._faces[1])):
            theme.shadow_rect(surface, rect, radius=10, spread=4, alpha=80)
            draw_die(surface, rect, value)

        if self.show_total and self.progress > 0.7:
            alpha = int(255 * clamp((self.progress - 0.7) / 0.3, 0.0, 1.0))
            font = fonts.h1()
            text = f"共 {self.result[0] + self.result[1]} 点"
            img = font.render(text, True, theme.color("accent"))
            img.set_alpha(alpha)
            rect = img.get_rect(center=(self.center[0], rect1.bottom + 34))
            shadow = font.render(text, True, (0, 0, 0))
            shadow.set_alpha(int(alpha * 0.6))
            surface.blit(shadow, (rect.x + 2, rect.y + 2))
            surface.blit(img, rect)


# ==================================================================== 卡牌翻出

class CardFlipAnimation(Animation):
    """机遇卡翻出：从窄变宽 + 淡入。"""

    def __init__(self, rect: pygame.Rect, title: str, description: str,
                 accent: str = "chance", duration: float = 0.55) -> None:
        super().__init__(duration)
        self.rect = pygame.Rect(rect)
        self.title = title
        self.description = description
        self.accent = accent

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if self.done:
            return
        p = ease_out_back(self.progress)
        w = max(6, int(self.rect.width * clamp(p, 0.05, 1.15)))
        rect = pygame.Rect(0, 0, w, self.rect.height)
        rect.center = self.rect.center
        alpha = int(255 * clamp(self.progress * 1.6, 0.0, 1.0))

        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        accent_rgb = theme.color(self.accent)
        theme.rounded_rect(layer, layer.get_rect(), theme.color("panel_alt"), radius=16)
        theme.rounded_rect(layer, layer.get_rect(), None, radius=16, border=accent_rgb,
                           border_width=3)
        bar = pygame.Rect(0, 0, rect.width, 8)
        theme.rounded_rect(layer, bar, accent_rgb, radius=4)

        if p > 0.6:
            font = fonts.h2()
            theme.draw_text(layer, theme.truncate(self.title, font, rect.width - 32), font,
                            theme.color("text"), (rect.width // 2, 34), anchor="midtop")
            body = fonts.small()
            theme.draw_wrapped(layer, self.description, body, theme.color("text_dim"),
                               pygame.Rect(20, 76, rect.width - 40, rect.height - 100))
        layer.set_alpha(alpha)
        surface.blit(layer, rect.topleft)


# ==================================================================== 棋子移动

class PieceMoveAnimation(Animation):
    """棋子逐格移动的显示位置计算。"""

    def __init__(self, path: list[int], tile_centers: dict[int, tuple[int, int]],
                 per_tile: float = 0.17) -> None:
        super().__init__(max(0.12, len(path) * per_tile))
        self.path = path
        self.tile_centers = tile_centers
        self.per_tile = per_tile

    def current_index(self) -> int | None:
        """当前正在走向（或已到达）的格子索引 —— 用于「经过每一格」的反馈。"""
        if not self.path:
            return None
        p = self.progress * len(self.path)
        return self.path[min(len(self.path) - 1, int(p))]

    def tile_count(self) -> int:
        return len(self.path)

    def current_center(self, start_center: tuple[int, int]) -> tuple[float, float]:
        """返回当前应绘制的坐标（线性插值 + 轻微弹跳）。"""
        if not self.path:
            return start_center
        p = self.progress * len(self.path)
        idx = min(len(self.path) - 1, int(p))
        local = clamp(p - idx, 0.0, 1.0)
        eased = ease_out_quad(local)
        if idx == 0:
            a = start_center
        else:
            a = self.tile_centers.get(self.path[idx - 1], start_center)
        b = self.tile_centers.get(self.path[idx], start_center)
        x = a[0] + (b[0] - a[0]) * eased
        y = a[1] + (b[1] - a[1]) * eased
        # 每格中间微抬，像跳格子；经过格子的最后 25% 会「落定」一下
        hop = math.sin(local * math.pi) * (7 if local < 0.8 else 4)
        return (x, y - hop)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        pass


# ==================================================================== 高亮脉冲

class Pulse:
    """持续循环的脉冲数值（用于当前回合高亮）。"""

    def __init__(self, period: float = 1.6, low: float = 0.45, high: float = 1.0) -> None:
        self.period = max(0.2, period)
        self.low = low
        self.high = high
        self.t = 0.0

    def update(self, dt: float) -> None:
        self.t = (self.t + dt) % self.period

    @property
    def value(self) -> float:
        phase = self.t / self.period
        wave = (1.0 - math.cos(phase * math.tau)) * 0.5
        return self.low + (self.high - self.low) * wave


class ShakeAnimation(Animation):
    """屏幕/面板抖动（破产、错误提示）。"""

    def __init__(self, amplitude: float = 8.0, duration: float = 0.4) -> None:
        super().__init__(duration)
        self.amplitude = amplitude
        self._rng = random.Random(7)

    def offset(self) -> tuple[int, int]:
        if self.done:
            return (0, 0)
        damp = 1.0 - self.progress
        return (
            int(self._rng.uniform(-1, 1) * self.amplitude * damp),
            int(self._rng.uniform(-1, 1) * self.amplitude * damp),
        )

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        pass


class FadeAnimation(Animation):
    """全屏淡入淡出。"""

    def __init__(self, duration: float = 0.35, fade_in: bool = True,
                 color_name: str = "overlay") -> None:
        super().__init__(duration)
        self.fade_in = fade_in
        self.color_name = color_name

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if self.done:
            return
        p = self.progress if self.fade_in else 1.0 - self.progress
        alpha = int(255 * clamp(p, 0.0, 1.0))
        if alpha <= 2:
            return
        layer = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        layer.fill(theme.color(self.color_name, alpha))
        surface.blit(layer, (0, 0))


# ==================================================================== 管理器

class AnimationManager:
    """集中管理所有瞬时动画，统一应用动画速度。"""

    def __init__(self) -> None:
        self.items: list[Animation] = []
        self.speed = 1.0
        self.pulse = Pulse()

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.25, float(speed))

    def add(self, anim: Animation) -> Animation:
        self.items.append(anim)
        return anim

    def float_text(self, text: str, pos: tuple[float, float], color_name: str = "success",
                   size: int = 24) -> None:
        self.add(FloatingText(text, pos, color_name, size=size))

    def update(self, dt: float) -> None:
        scaled = dt * self.speed
        self.pulse.update(scaled)
        for anim in list(self.items):
            anim.update(scaled)
            if anim.done:
                self.items.remove(anim)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        for anim in self.items:
            anim.draw(surface, fonts)

    def clear(self) -> None:
        self.items.clear()

    def has_active(self, cls: type | None = None) -> bool:
        if cls is None:
            return bool(self.items)
        return any(isinstance(a, cls) for a in self.items)

    def find(self, cls: type) -> Animation | None:
        for anim in self.items:
            if isinstance(anim, cls):
                return anim
        return None
