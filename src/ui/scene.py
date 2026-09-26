"""场景系统：主菜单 / 大厅 / 对局 / 设置 的切换与生命周期。

每个场景只依赖 App 提供的资源（字体、设置、音频、网络），
场景之间不直接互相引用，全部通过 SceneManager 切换。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from . import theme


class Scene:
    """场景基类。"""

    #: 是否绘制下层场景（用于叠加的模态场景）
    transparent = False

    def __init__(self, app: Any) -> None:
        self.app = app
        self.widgets: list[Any] = []

    # ---- 生命周期
    def on_enter(self, **kwargs: Any) -> None:
        """进入场景（每次切换进来都会调用）。"""

    def on_exit(self) -> None:
        """离开场景。"""

    def on_resume(self) -> None:
        """从上层场景返回时调用。"""

    # ---- 每帧
    def handle_event(self, event: pygame.event.Event) -> None:
        for widget in list(self.widgets):
            if not getattr(widget, "visible", True):
                continue
            if widget.handle_event(event):
                return

    def update(self, dt: float) -> None:
        mouse = pygame.mouse.get_pos()
        for widget in self.widgets:
            widget.update(dt, mouse)

    def draw(self, surface: pygame.Surface) -> None:
        raise NotImplementedError

    # ---- 便利
    def draw_widgets(self, surface: pygame.Surface,
                     skip: set[Any] | None = None) -> None:
        fonts = self.app.fonts
        for widget in self.widgets:
            if skip and widget in skip:
                continue
            widget.draw(surface, fonts)

    @property
    def fonts(self) -> theme.FontManager:
        return self.app.fonts

    def notify(self, text: str, kind: str = "info") -> None:
        self.app.toast(text, kind)


class SceneManager:
    """场景栈：支持 switch（替换栈底）与 push/pop（叠加模态）。"""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.factories: dict[str, Callable[..., Scene]] = {}
        self.cache: dict[str, Scene] = {}
        self.stack: list[Scene] = []

    def register(self, name: str, factory: Callable[..., Scene], cached: bool = True) -> None:
        self.factories[name] = factory
        if cached:
            self.cache[name] = factory(self.app)

    def get(self, name: str) -> Scene:
        scene = self.cache.get(name)
        if scene is None:
            factory = self.factories[name]
            scene = factory(self.app)
            self.cache[name] = scene
        return scene

    @property
    def current(self) -> Scene | None:
        return self.stack[-1] if self.stack else None

    def switch_to(self, name: str, **kwargs: Any) -> Scene:
        """清空栈并进入目标场景。"""
        while self.stack:
            self.stack.pop().on_exit()
        scene = self.get(name)
        self.stack.append(scene)
        scene.on_enter(**kwargs)
        return scene

    def push(self, name: str, **kwargs: Any) -> Scene:
        """叠加一个场景（下层仍然会被绘制，如果 transparent）。"""
        scene = self.get(name)
        self.stack.append(scene)
        scene.on_enter(**kwargs)
        return scene

    def pop(self) -> None:
        if len(self.stack) <= 1:
            return
        scene = self.stack.pop()
        scene.on_exit()
        if self.stack:
            self.stack[-1].on_resume()

    def clear_cache(self, name: str | None = None) -> None:
        """丢弃缓存，让场景下次重新构建（用于重置状态）。"""
        if name is None:
            self.cache.clear()
        else:
            self.cache.pop(name, None)

    # ---- 转发
    def handle_event(self, event: pygame.event.Event) -> None:
        for scene in reversed(self.stack):
            if scene is self.current or scene.transparent:
                scene.handle_event(event)
            if scene is self.current and not scene.transparent:
                break
            if not scene.transparent:
                break

    def update(self, dt: float) -> None:
        for scene in list(self.stack):
            scene.update(dt)

    def draw(self, surface: pygame.Surface) -> None:
        start = 0
        for i in range(len(self.stack) - 1, -1, -1):
            if not self.stack[i].transparent:
                start = i
                break
        for scene in self.stack[start:]:
            scene.draw(surface)

    def resize(self) -> None:
        for scene in self.stack:
            if hasattr(scene, "on_resize"):
                scene.on_resize()
