"""基础控件库：按钮、输入框、滑块、开关、分段选择、滚动列表。

所有控件都只关心逻辑坐标，由 layout.Viewport 统一处理缩放。
控件之间不互相依赖，也不直接触碰游戏状态。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

import pygame

from . import theme


class Widget:
    """控件基类。"""

    def __init__(self, rect: pygame.Rect | Sequence[int]) -> None:
        self.rect = pygame.Rect(rect)
        self.enabled = True
        self.visible = True
        self.hovered = False
        self.focused = False
        self.tooltip = ""
        self.cursor = "arrow"

    def handle_event(self, event: pygame.event.Event) -> bool:
        """处理事件，返回 True 表示已消费（阻止穿透）。"""
        return False

    def update(self, dt: float, mouse_pos: tuple[int, int]) -> None:
        self.hovered = self.visible and self.enabled and self.rect.collidepoint(mouse_pos)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        raise NotImplementedError

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.visible and self.enabled and self.rect.collidepoint(pos)


# ==================================================================== 按钮

class Button(Widget):
    """圆角按钮。"""

    STYLES = {
        "primary": ("primary", "text", "primary_dark"),
        "accent": ("accent", "text_dark", "accent_dark"),
        "secondary": ("panel_alt", "text", "panel_hi"),
        "ghost": (None, "text_dim", "border_soft"),
        "danger": ("danger", "text", "danger_dark"),
        "success": ("success", "text", "success_dark"),
    }

    def __init__(
        self,
        rect: pygame.Rect | Sequence[int],
        label: str,
        on_click: Callable[[], None] | None = None,
        style: str = "primary",
        enabled: bool = True,
        icon: str = "",
        subtitle: str = "",
        font_size: int = 19,
        tooltip: str = "",
        radius: int = 12,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.subtitle = subtitle
        self.on_click = on_click
        self.style = style
        self.enabled = enabled
        self.icon = icon
        self.font_size = font_size
        self.tooltip = tooltip
        self.radius = radius
        self.pressed = False
        self._press_t = 0.0
        self.anim = 0.0

    def set_enabled(self, value: bool, hint: str = "") -> None:
        self.enabled = bool(value)
        if hint:
            self.tooltip = hint

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or not self.enabled:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.pressed = True
                self._press_t = time.time()
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.pressed:
                self.pressed = False
                if self.rect.collidepoint(event.pos):
                    if self.on_click is not None:
                        self.on_click()
                    return True
        return False

    def update(self, dt: float, mouse_pos: tuple[int, int]) -> None:
        super().update(dt, mouse_pos)
        target = 1.0 if self.hovered else 0.0
        self.anim += (target - self.anim) * min(1.0, dt * 12.0)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        fill_name, text_name, dark_name = self.STYLES.get(self.style, self.STYLES["primary"])
        rect = pygame.Rect(self.rect)
        if self.pressed and self.hovered:
            rect.y += 1

        if not self.enabled:
            fill = theme.color("panel")
            text_color = theme.color("text_mute")
            border = theme.color("border_soft")
        else:
            base = theme.color(fill_name) if fill_name else None
            if base is not None:
                base = theme.lighten(base, 0.10 * self.anim)
            fill = base
            text_color = theme.color(text_name)
            border = theme.color(dark_name) if fill_name else theme.color("border_soft")

        if fill is not None:
            theme.rounded_rect(surface, rect, fill, radius=self.radius)
        else:
            theme.rounded_rect(surface, rect, theme.color("panel", 140), radius=self.radius)
        theme.rounded_rect(surface, rect, None, radius=self.radius, border=border,
                           border_width=2)

        font = fonts.sized(self.font_size, True)
        label = self.label
        if self.icon:
            label = f"{self.icon}  {label}"
        text_rect = rect
        if self.subtitle:
            text_rect = pygame.Rect(rect.x, rect.y + rect.height // 2 - 20, rect.width, 22)
            theme.draw_text(surface, theme.truncate(label, font, rect.width - 20), font,
                            text_color, text_rect.center, anchor="center")
            sub = fonts.small()
            theme.draw_text(surface, theme.truncate(self.subtitle, sub, rect.width - 16), sub,
                            theme.color("text_dim"), (rect.centerx, rect.y + rect.height // 2 + 12),
                            anchor="center")
        else:
            theme.draw_text(surface, theme.truncate(label, font, rect.width - 16), font,
                            text_color, rect.center, anchor="center")


class IconButton(Button):
    """只显示一个符号的小按钮。"""

    def __init__(self, rect, glyph: str, on_click=None, style: str = "secondary",
                 tooltip: str = "", font_size: int = 20) -> None:
        super().__init__(rect, glyph, on_click, style=style, tooltip=tooltip,
                         font_size=font_size, radius=10)


# ==================================================================== 文本输入

class TextInput(Widget):
    """单行文本输入框。支持中文（通过 TEXTINPUT 事件）。"""

    def __init__(
        self,
        rect: pygame.Rect | Sequence[int],
        text: str = "",
        placeholder: str = "",
        max_length: int = 20,
        on_change: Callable[[str], None] | None = None,
        on_submit: Callable[[str], None] | None = None,
        numeric: bool = False,
    ) -> None:
        super().__init__(rect)
        self.text = text
        self.placeholder = placeholder
        self.max_length = max_length
        self.on_change = on_change
        self.on_submit = on_submit
        self.numeric = numeric
        self.caret = len(text)
        self._blink = 0.0
        self._offset = 0

    def set_text(self, text: str) -> None:
        self.text = text[: self.max_length]
        self.caret = len(self.text)
        self._offset = 0

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or not self.enabled:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.focused = True
                self.caret = len(self.text)
                try:
                    pygame.key.start_text_input()
                except Exception:
                    pass
                return True
            if self.focused:
                self.focused = False
                try:
                    pygame.key.stop_text_input()
                except Exception:
                    pass
        if not self.focused:
            return False

        if event.type == pygame.TEXTINPUT:
            chunk = event.text
            if self.numeric and not chunk.isdigit():
                chunk = "".join(c for c in chunk if c.isdigit())
            if chunk:
                head = self.text[: self.caret]
                tail = self.text[self.caret:]
                self.text = (head + chunk + tail)[: self.max_length]
                self.caret = min(len(self.text), self.caret + len(chunk))
                self._changed()
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                if self.caret > 0:
                    self.text = self.text[: self.caret - 1] + self.text[self.caret:]
                    self.caret -= 1
                    self._changed()
                return True
            if event.key == pygame.K_DELETE:
                if self.caret < len(self.text):
                    self.text = self.text[: self.caret] + self.text[self.caret + 1:]
                    self._changed()
                return True
            if event.key == pygame.K_LEFT:
                self.caret = max(0, self.caret - 1)
                return True
            if event.key == pygame.K_RIGHT:
                self.caret = min(len(self.text), self.caret + 1)
                return True
            if event.key == pygame.K_HOME:
                self.caret = 0
                return True
            if event.key == pygame.K_END:
                self.caret = len(self.text)
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.focused = False
                try:
                    pygame.key.stop_text_input()
                except Exception:
                    pass
                if self.on_submit is not None:
                    self.on_submit(self.text)
                return True
            if event.key == pygame.K_ESCAPE:
                self.focused = False
                return True
        return False

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.text)

    def update(self, dt: float, mouse_pos: tuple[int, int]) -> None:
        super().update(dt, mouse_pos)
        if self.focused:
            self._blink = (self._blink + dt) % 1.0
        self.cursor = "text" if self.hovered or self.focused else "arrow"

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        font = fonts.body()
        border = theme.color("accent") if self.focused else theme.color("border")
        theme.rounded_rect(surface, self.rect, theme.color("bg_alt"), radius=10)
        theme.rounded_rect(surface, self.rect, None, radius=10, border=border,
                           border_width=2 if self.focused else 1)

        inner = self.rect.inflate(-20, -12)
        shown = self.text if self.text else ""
        color = theme.color("text") if self.text else theme.color("text_mute")
        if not shown:
            shown = self.placeholder

        # 文本较宽时滚动显示尾部
        while font.size(shown)[0] > inner.width and len(shown) > 1:
            shown = shown[1:]
        rect = theme.draw_text(surface, shown, font, color, (inner.x, inner.centery),
                               anchor="midleft")

        if self.focused:
            visible = self._blink < 0.55
            if visible:
                prefix = self.text[: self.caret]
                while font.size(prefix)[0] > inner.width:
                    prefix = prefix[1:]
                cx = inner.x + font.size(prefix)[0]
                pygame.draw.line(surface, theme.color("accent"),
                                 (cx, inner.y), (cx, inner.bottom), 2)
        if self.hovered and not self.focused:
            pass


# ==================================================================== 滑块

class Slider(Widget):
    """水平滑块。"""

    def __init__(
        self,
        rect: pygame.Rect | Sequence[int],
        value: float = 0.5,
        min_value: float = 0.0,
        max_value: float = 1.0,
        step: float = 0.05,
        on_change: Callable[[float], None] | None = None,
        label: str = "",
        show_percent: bool = True,
    ) -> None:
        super().__init__(rect)
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
        self.step = step
        self.on_change = on_change
        self.label = label
        self.show_percent = show_percent
        self.dragging = False

    def _track_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x, self.rect.centery - 5, self.rect.width, 10)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or not self.enabled:
            return False
        track = self._track_rect().inflate(0, 22)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if track.collidepoint(event.pos):
                self.dragging = True
                self._set_from_x(event.pos[0])
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging:
                self.dragging = False
                return True
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            self._set_from_x(event.pos[0])
            return True
        return False

    def _set_from_x(self, x: int) -> None:
        track = self._track_rect()
        ratio = (x - track.x) / max(1, track.width)
        ratio = max(0.0, min(1.0, ratio))
        raw = self.min_value + (self.max_value - self.min_value) * ratio
        if self.step > 0:
            steps = round((raw - self.min_value) / self.step)
            raw = self.min_value + steps * self.step
        raw = max(self.min_value, min(self.max_value, raw))
        if abs(raw - self.value) > 1e-6:
            self.value = raw
            if self.on_change is not None:
                self.on_change(self.value)

    @property
    def ratio(self) -> float:
        span = self.max_value - self.min_value
        return 0.0 if span <= 0 else (self.value - self.min_value) / span

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        track = self._track_rect()
        theme.rounded_rect(surface, track, theme.color("bg_alt"), radius=5)
        filled = pygame.Rect(track.x, track.y, int(track.width * self.ratio), track.height)
        theme.rounded_rect(surface, filled,
                           theme.color("accent") if self.enabled else theme.color("text_mute"),
                           radius=5)
        knob_x = track.x + int(track.width * self.ratio)
        knob_r = 11 if (self.hovered or self.dragging) else 9
        pygame.draw.circle(surface, theme.color("text"), (knob_x, track.centery), knob_r)
        pygame.draw.circle(surface, theme.color("accent"), (knob_x, track.centery), knob_r, 3)

        if self.label:
            theme.draw_text(surface, self.label, fonts.small(), theme.color("text_dim"),
                            (self.rect.x, self.rect.y - 22))
        if self.show_percent:
            theme.draw_text(surface, f"{int(round(self.ratio * 100))}%", fonts.small(),
                            theme.color("text"), (self.rect.right, self.rect.y - 22),
                            anchor="topright")


# ==================================================================== 开关

class Toggle(Widget):
    """开关（用于全屏、静音等）。"""

    def __init__(self, rect, value: bool = False, on_change=None, label: str = "") -> None:
        super().__init__(rect)
        self.value = bool(value)
        self.on_change = on_change
        self.label = label
        self.anim = 1.0 if value else 0.0

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or not self.enabled:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.value = not self.value
                if self.on_change is not None:
                    self.on_change(self.value)
                return True
        return False

    def update(self, dt: float, mouse_pos: tuple[int, int]) -> None:
        super().update(dt, mouse_pos)
        target = 1.0 if self.value else 0.0
        self.anim += (target - self.anim) * min(1.0, dt * 12.0)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        track = pygame.Rect(self.rect.x, self.rect.centery - 14, 52, 28)
        bg = theme.mix(theme.color("panel_hi"), theme.color("success"), self.anim)
        theme.rounded_rect(surface, track, bg, radius=14)
        knob_x = track.x + 14 + int((track.width - 28) * self.anim)
        pygame.draw.circle(surface, theme.color("text"), (knob_x, track.centery), 11)
        if self.label:
            theme.draw_text(surface, self.label, fonts.body(), theme.color("text_dim"),
                            (track.right + 14, track.centery), anchor="midleft")


# ==================================================================== 分段选择

class SegmentedControl(Widget):
    """一排互斥选项（动画速度 / 字体大小 / 分辨率）。"""

    def __init__(
        self,
        rect: pygame.Rect | Sequence[int],
        options: Sequence[tuple[str, Any]],
        value: Any = None,
        on_change: Callable[[Any], None] | None = None,
        label: str = "",
    ) -> None:
        super().__init__(rect)
        self.options = list(options)
        self.value = value if value is not None else (self.options[0][1] if self.options else None)
        self.on_change = on_change
        self.label = label

    def set_value(self, value: Any) -> None:
        self.value = value

    def _item_rects(self) -> list[pygame.Rect]:
        n = max(1, len(self.options))
        gap = 8
        w = (self.rect.width - gap * (n - 1)) // n
        out = []
        for i in range(n):
            out.append(pygame.Rect(self.rect.x + i * (w + gap), self.rect.y, w, self.rect.height))
        return out

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or not self.enabled:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, (_, value) in zip(self._item_rects(), self.options):
                if rect.collidepoint(event.pos):
                    if value != self.value:
                        self.value = value
                        if self.on_change is not None:
                            self.on_change(value)
                    return True
        return False

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        if self.label:
            theme.draw_text(surface, self.label, fonts.small(), theme.color("text_dim"),
                            (self.rect.x, self.rect.y - 22))
        font = fonts.sized(16, True)
        for rect, (text, value) in zip(self._item_rects(), self.options):
            active = value == self.value
            fill = theme.color("accent") if active else theme.color("panel_alt")
            theme.rounded_rect(surface, rect, fill, radius=9)
            theme.rounded_rect(surface, rect, None, radius=9,
                               border=theme.color("accent_dark") if active else theme.color("border_soft"),
                               border_width=1)
            theme.draw_text(surface, text, font,
                            theme.color("text_dark") if active else theme.color("text_dim"),
                            rect.center, anchor="center")


# ==================================================================== 滚动列表

class ScrollList(Widget):
    """带滚动条和自动贴底的列表（用于事件日志）。"""

    def __init__(
        self,
        rect: pygame.Rect | Sequence[int],
        line_height: int = 22,
        auto_scroll: bool = True,
        padding: int = 10,
    ) -> None:
        super().__init__(rect)
        self.items: list[Any] = []
        self.line_height = line_height
        self.scroll = 0.0
        self.auto_scroll = auto_scroll
        self.padding = padding
        self.stick_bottom = True
        self.dragging = False

    def append(self, item: Any, limit: int = 400) -> None:
        self.items.append(item)
        if len(self.items) > limit:
            del self.items[: len(self.items) - limit]
        if self.auto_scroll and self.stick_bottom:
            self.scroll = self.max_scroll

    def set_items(self, items: Iterable[Any]) -> None:
        self.items = list(items)
        if self.auto_scroll and self.stick_bottom:
            self.scroll = self.max_scroll

    @property
    def view_height(self) -> int:
        return self.rect.height - self.padding * 2

    @property
    def content_height(self) -> int:
        return len(self.items) * self.line_height

    @property
    def max_scroll(self) -> float:
        return max(0.0, self.content_height - self.view_height)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible:
            return False
        if event.type == pygame.MOUSEWHEEL:
            mouse = pygame.mouse.get_pos()
            if self.rect.collidepoint(self._local_mouse(mouse)):
                self.scroll = max(0.0, min(self.max_scroll, self.scroll - event.y * 48))
                self.stick_bottom = self.scroll >= self.max_scroll - 2
                return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                bar = self._bar_rect()
                if bar.collidepoint(event.pos):
                    self.dragging = True
                    return True
                return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging:
                self.dragging = False
                return True
        if event.type == pygame.MOUSEMOTION and self.dragging:
            ratio = (event.pos[1] - self.rect.y - 10) / max(1, self.rect.height - 20)
            self.scroll = max(0.0, min(self.max_scroll, ratio * self.max_scroll))
            self.stick_bottom = self.scroll >= self.max_scroll - 2
            return True
        return False

    def _local_mouse(self, pos) -> tuple[int, int]:
        return pos

    def _bar_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 8, self.rect.y, 8, self.rect.height)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        clip = surface.get_clip()
        surface.set_clip(self.rect)

        y = self.rect.y + self.padding - int(self.scroll)
        for item in self.items:
            if y + self.line_height >= self.rect.y and y <= self.rect.bottom:
                self._draw_item(surface, fonts, item, y)
            y += self.line_height
        surface.set_clip(clip)

        if self.max_scroll > 0:
            bar = self._bar_rect()
            theme.rounded_rect(surface, bar, theme.color("bg_alt"), radius=4)
            ratio = self.view_height / max(1, self.content_height)
            handle_h = max(28, int(bar.height * ratio))
            pos = self.scroll / max(1.0, self.max_scroll)
            handle = pygame.Rect(bar.x, bar.y + int((bar.height - handle_h) * pos), bar.width, handle_h)
            theme.rounded_rect(surface, handle, theme.color("border"), radius=4)

    def _draw_item(self, surface, fonts, item, y: int) -> None:
        """默认按 (文本, 颜色名) 元组或纯文本绘制。"""
        text = item
        color_name = "text_dim"
        if isinstance(item, tuple) and len(item) >= 2:
            text, color_name = item[0], item[1]
        font = fonts.small()
        theme.draw_text(surface, theme.truncate(str(text), font, self.rect.width - 24), font,
                        theme.color(color_name), (self.rect.x + self.padding, y))


# ==================================================================== 其它

class Label(Widget):
    """静态文本。"""

    def __init__(self, rect, text: str, size: int = 17, color_name: str = "text",
                 anchor: str = "topleft", bold: bool = False, wrap: bool = False) -> None:
        super().__init__(rect)
        self.text = text
        self.size = size
        self.color_name = color_name
        self.anchor = anchor
        self.bold = bold
        self.wrap = wrap

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible or not self.text:
            return
        font = fonts.sized(self.size, self.bold)
        if self.wrap:
            theme.draw_wrapped(surface, self.text, font, theme.color(self.color_name), self.rect)
        else:
            theme.draw_text(surface, self.text, font, theme.color(self.color_name),
                            getattr(self.rect, self.anchor), anchor=self.anchor)


class Panel(Widget):
    """带标题的面板容器。"""

    def __init__(self, rect, title: str = "", radius: int = 14, fill: str = "panel") -> None:
        super().__init__(rect)
        self.title = title
        self.radius = radius
        self.fill = fill

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        if not self.visible:
            return
        theme.rounded_rect(surface, self.rect, theme.color(self.fill), radius=self.radius)
        theme.rounded_rect(surface, self.rect, None, radius=self.radius,
                           border=theme.color("border_soft"), border_width=1)
        if self.title:
            theme.draw_text(surface, self.title, fonts.h3(), theme.color("text"),
                            (self.rect.x + 16, self.rect.y + 14))


class ProgressBar(Widget):
    """进度条（用于破产/资金对比）。"""

    def __init__(self, rect, value: float = 0.0, color_name: str = "accent") -> None:
        super().__init__(rect)
        self.value = max(0.0, min(1.0, value))
        self.color_name = color_name
        self.shown = self.value

    def update(self, dt: float, mouse_pos) -> None:
        self.shown += (self.value - self.shown) * min(1.0, dt * 8.0)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        theme.rounded_rect(surface, self.rect, theme.color("bg_alt"), radius=self.rect.height // 2)
        w = int(self.rect.width * max(0.0, min(1.0, self.shown)))
        if w > 2:
            fill = pygame.Rect(self.rect.x, self.rect.y, w, self.rect.height)
            theme.rounded_rect(surface, fill, theme.color(self.color_name),
                               radius=self.rect.height // 2)


def draw_tooltip(surface: pygame.Surface, fonts: theme.FontManager, text: str,
                 mouse_pos: tuple[int, int], max_width: int = 340,
                 bounds: tuple[int, int] = (1600, 900)) -> None:
    """在鼠标附近绘制提示框，自动避免越界。"""
    if not text:
        return
    font = fonts.small()
    lines = theme.wrap_text(text, font, max_width)
    if not lines:
        return
    line_h = font.get_linesize() + 2
    width = max(font.size(line)[0] for line in lines) + 22
    height = line_h * len(lines) + 16
    x = mouse_pos[0] + 18
    y = mouse_pos[1] + 18
    if x + width > bounds[0] - 8:
        x = max(8, mouse_pos[0] - width - 14)
    if y + height > bounds[1] - 8:
        y = max(8, mouse_pos[1] - height - 14)
    rect = pygame.Rect(x, y, width, height)
    theme.shadow_rect(surface, rect, radius=10, spread=3, alpha=110)
    theme.rounded_rect(surface, rect, theme.color("bg_alt", 246), radius=10)
    theme.rounded_rect(surface, rect, None, radius=10, border=theme.color("border"),
                       border_width=1)
    ty = rect.y + 8
    for line in lines:
        theme.draw_text(surface, line, font, theme.color("text"), (rect.x + 11, ty))
        ty += line_h
