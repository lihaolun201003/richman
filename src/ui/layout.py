"""逻辑分辨率与缩放。

游戏内部固定按 1600×900 的逻辑分辨率布局（保证任何分辨率下比例一致、
不会出现挤压变形），再等比缩放并居中到实际窗口（letterbox）。
鼠标坐标统一换算回逻辑坐标，因此所有控件代码只需要关心逻辑坐标。

这既满足「布局响应缩放」，也避免了为每个分辨率重算一遍坐标。
"""
from __future__ import annotations

import pygame

#: 逻辑分辨率
LOGICAL_WIDTH = 1600
LOGICAL_HEIGHT = 900
LOGICAL_SIZE = (LOGICAL_WIDTH, LOGICAL_HEIGHT)


class Viewport:
    """窗口 ↔ 逻辑坐标的换算器。"""

    def __init__(self) -> None:
        self.window_size = LOGICAL_SIZE
        self.scale = 1.0
        self.offset = (0, 0)

    def update(self, window_size: tuple[int, int]) -> None:
        self.window_size = window_size
        w, h = window_size
        self.scale = min(w / LOGICAL_WIDTH, h / LOGICAL_HEIGHT)
        scaled_w = LOGICAL_WIDTH * self.scale
        scaled_h = LOGICAL_HEIGHT * self.scale
        self.offset = ((w - scaled_w) / 2.0, (h - scaled_h) / 2.0)

    # ---- 坐标换算
    def to_logical(self, pos: tuple[float, float]) -> tuple[int, int]:
        if self.scale <= 0:
            return (int(pos[0]), int(pos[1]))
        x = (pos[0] - self.offset[0]) / self.scale
        y = (pos[1] - self.offset[1]) / self.scale
        return (int(x), int(y))

    def to_window(self, pos: tuple[float, float]) -> tuple[int, int]:
        x = pos[0] * self.scale + self.offset[0]
        y = pos[1] * self.scale + self.offset[1]
        return (int(x), int(y))

    def contains(self, pos: tuple[float, float]) -> bool:
        x, y = self.to_logical(pos)
        return 0 <= x < LOGICAL_WIDTH and 0 <= y < LOGICAL_HEIGHT

    # ---- 呈现
    def blit(self, surface: pygame.Surface, target: pygame.Surface) -> None:
        target.fill((0, 0, 0))
        if abs(self.scale - 1.0) < 1e-3 and self.offset == (0, 0):
            target.blit(surface, (0, 0))
            return
        scaled_size = (
            max(1, int(LOGICAL_WIDTH * self.scale)),
            max(1, int(LOGICAL_HEIGHT * self.scale)),
        )
        if scaled_size == surface.get_size():
            scaled = surface
        else:
            scaled = pygame.transform.smoothscale(surface, scaled_size)
        target.blit(scaled, (int(self.offset[0]), int(self.offset[1]))) 


def translate_event(event: pygame.event.Event, viewport: Viewport) -> pygame.event.Event:
    """把鼠标事件坐标从窗口坐标换算为逻辑坐标。"""
    if event.type in (
        pygame.MOUSEMOTION,
        pygame.MOUSEBUTTONDOWN,
        pygame.MOUSEBUTTONUP,
    ):
        attr = event.dict.copy()
        if "pos" in attr:
            attr["pos"] = viewport.to_logical(attr["pos"])
        if "rel" in attr:
            s = viewport.scale if viewport.scale > 0 else 1.0
            attr["rel"] = (int(attr["rel"][0] / s), int(attr["rel"][1] / s))
        return pygame.event.Event(event.type, attr)
    return event
