"""缓动函数与补间工具，供 UI 动画使用。"""
from __future__ import annotations


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def ease_out_back(t: float, overshoot: float = 1.70158) -> float:
    t = clamp(t, 0.0, 1.0)
    c3 = overshoot + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + overshoot * (t - 1.0) ** 2


def ease_out_quad(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) * (1.0 - t)


class Tween:
    """按时间推进的插值器。"""

    __slots__ = ("duration", "elapsed", "done", "delay")

    def __init__(self, duration: float, delay: float = 0.0) -> None:
        self.duration = max(1e-6, duration)
        self.elapsed = 0.0
        self.delay = delay
        self.done = False

    def update(self, dt: float) -> float:
        """推进时间，返回 0..1 的原始进度。"""
        if self.done:
            return 1.0
        if self.delay > 0.0:
            self.delay -= dt
            if self.delay > 0.0:
                return 0.0
            dt = -self.delay
            self.delay = 0.0
        self.elapsed += dt
        if self.elapsed >= self.duration:
            self.elapsed = self.duration
            self.done = True
        return self.elapsed / self.duration

    @property
    def progress(self) -> float:
        return clamp(self.elapsed / self.duration, 0.0, 1.0)
