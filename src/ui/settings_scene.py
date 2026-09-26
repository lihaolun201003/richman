"""设置界面：音量、动画速度、字体大小、分辨率、昵称。

改动即时生效（音量、动画速度、字体），并写入 config/user_settings.json。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..persistence.settings import ANIM_SPEED_CHOICES, FONT_SCALE_CHOICES, RESOLUTION_CHOICES
from . import theme
from .dialogs import MessageDialog
from .scene import Scene
from .widgets import Button, Label, Panel, SegmentedControl, Slider, TextInput, Toggle


class SettingsScene(Scene):
    """设置场景。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.back_target = "menu"
        self._build()

    def _build(self) -> None:
        s = self.app.settings
        col1 = 140
        col2 = 860
        y0 = 200

        self.slider_master = Slider(
            pygame.Rect(col1, y0 + 40, 520, 24), s.master_volume, 0.0, 1.0, 0.05,
            on_change=self._on_master, label="主音量")
        self.slider_bgm = Slider(
            pygame.Rect(col1, y0 + 130, 520, 24), s.bgm_volume, 0.0, 1.0, 0.05,
            on_change=self._on_bgm, label="背景音乐音量")
        self.slider_sfx = Slider(
            pygame.Rect(col1, y0 + 220, 520, 24), s.sfx_volume, 0.0, 1.0, 0.05,
            on_change=self._on_sfx, label="音效音量")
        self.toggle_mute = Toggle(
            pygame.Rect(col1, y0 + 300, 200, 28), s.muted, on_change=self._on_mute,
            label="静音")

        self.anim_select = SegmentedControl(
            pygame.Rect(col2, y0 + 40, 560, 44),
            [(label, value) for label, value in ANIM_SPEED_CHOICES],
            value=s.animation_speed, on_change=self._on_anim, label="动画速度")

        self.font_select = SegmentedControl(
            pygame.Rect(col2, y0 + 150, 560, 44),
            [(label, value) for label, value in FONT_SCALE_CHOICES],
            value=s.font_scale, on_change=self._on_font, label="字体大小")

        self.res_select = SegmentedControl(
            pygame.Rect(col2, y0 + 260, 560, 44),
            [(f"{w}×{h}", (w, h)) for w, h in RESOLUTION_CHOICES],
            value=(s.width, s.height), on_change=self._on_resolution, label="分辨率")

        self.fullscreen_toggle = Toggle(
            pygame.Rect(col2, y0 + 340, 200, 28), s.fullscreen,
            on_change=self._on_fullscreen, label="全屏显示")

        self.nick_input = TextInput(
            pygame.Rect(col2, y0 + 430, 380, 44), s.nickname,
            placeholder="昵称（最长 12 字）", max_length=12,
            on_change=lambda v: self.app.settings.set_nickname(v))

        self.debug_toggle = Toggle(
            pygame.Rect(col2, y0 + 520, 200, 28), s.debug_overlay,
            on_change=self._on_debug, label="显示调试信息 (F1)")

        self.widgets = [
            self.slider_master, self.slider_bgm, self.slider_sfx, self.toggle_mute,
            self.anim_select, self.font_select, self.res_select,
            self.fullscreen_toggle, self.nick_input, self.debug_toggle,
            Button(pygame.Rect(140, 800, 240, 54), "返回",
                   on_click=self._back, style="secondary"),
            Button(pygame.Rect(400, 800, 240, 54), "恢复默认",
                   on_click=self._reset, style="ghost"),
            Button(pygame.Rect(660, 800, 240, 54), "保存设置",
                   on_click=self._save, style="accent"),
        ]

    # ------------------------------------------------------------ 回调

    def _on_master(self, value: float) -> None:
        self.app.settings.set("audio", "master_volume", value)
        self.app.audio.set_master_volume(value)

    def _on_bgm(self, value: float) -> None:
        self.app.settings.set("audio", "bgm_volume", value)
        self.app.audio.set_bgm_volume(value)

    def _on_sfx(self, value: float) -> None:
        self.app.settings.set("audio", "sfx_volume", value)
        self.app.audio.set_sfx_volume(value)
        self.app.audio.play_sfx("click")

    def _on_mute(self, value: bool) -> None:
        self.app.settings.set("audio", "muted", value)
        self.app.audio.set_muted(value)

    def _on_anim(self, value: float) -> None:
        self.app.settings.set("ui", "animation_speed", value)
        self.app.apply_animation_speed()

    def _on_font(self, value: float) -> None:
        self.app.settings.set("ui", "font_scale", value)
        self.app.apply_font_scale()

    def _on_resolution(self, value: tuple[int, int]) -> None:
        self.app.settings.set_resolution(*value)
        self.app.apply_display()
        self.notify(f"分辨率已切换为 {value[0]}×{value[1]}", "success")

    def _on_fullscreen(self, value: bool) -> None:
        self.app.settings.set_fullscreen(value)
        self.app.apply_display()

    def _on_debug(self, value: bool) -> None:
        self.app.settings.set("ui", "debug_overlay", value)

    def _back(self) -> None:
        self.app.settings.save()
        self.app.scenes.switch_to(self.back_target)

    def _save(self) -> None:
        if self.app.settings.save():
            self.notify("设置已保存到 config/user_settings.json", "success")
        else:
            self.notify("保存失败，请检查目录权限", "error")

    def _reset(self) -> None:
        from ..persistence.settings import DEFAULT_SETTINGS

        s = self.app.settings
        s.data = {k: (dict(v) if isinstance(v, dict) else v)
                  for k, v in DEFAULT_SETTINGS.items()}
        s.save()
        self.app.apply_settings()
        self._sync_widgets()
        self.notify("已恢复默认设置", "info")

    def _sync_widgets(self) -> None:
        s = self.app.settings
        self.slider_master.value = s.master_volume
        self.slider_bgm.value = s.bgm_volume
        self.slider_sfx.value = s.sfx_volume
        self.toggle_mute.value = s.muted
        self.anim_select.set_value(s.animation_speed)
        self.font_select.set_value(s.font_scale)
        self.res_select.set_value((s.width, s.height))
        self.fullscreen_toggle.value = s.fullscreen
        self.nick_input.set_text(s.nickname)
        self.debug_toggle.value = s.debug_overlay

    # ------------------------------------------------------------ 场景

    def on_enter(self, **kwargs: Any) -> None:
        self.back_target = kwargs.get("back", "menu")
        self._sync_widgets()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()
            return
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.draw_text(surface, "设置", self.fonts.h1(), theme.color("text"), (140, 96))
        theme.draw_text(surface, "所有改动立即生效，保存后写入 config/user_settings.json",
                        self.fonts.small(), theme.color("text_dim"), (142, 136))
        pygame.draw.line(surface, theme.color("border_soft"), (140, 168), (1460, 168), 1)

        # 分组底
        left_panel = pygame.Rect(110, 180, 600, 190)
        theme.rounded_rect(surface, left_panel, theme.color("panel", 120), radius=14)
        theme.draw_text(surface, "音频", self.fonts.h3(), theme.color("accent"),
                        (left_panel.x + 20, left_panel.y - 4))

        right_panel = pygame.Rect(830, 180, 640, 560)
        theme.rounded_rect(surface, right_panel, theme.color("panel", 120), radius=14)
        theme.draw_text(surface, "显示与操作", self.fonts.h3(), theme.color("accent"),
                        (right_panel.x + 20, right_panel.y - 4))

        theme.draw_text(surface, "昵称", self.fonts.small(), theme.color("text_dim"),
                        (860, 626))
        theme.draw_text(surface, "调试信息", self.fonts.small(), theme.color("text_dim"),
                        (860, 716))

        self.draw_widgets(surface)

        # 状态提示
        theme.draw_text(surface, f"音频状态：{self.app.audio.status_text}",
                        self.fonts.small(), theme.color("text_mute"), (140, 760))
        theme.draw_text(surface, f"中文字体：{self.app.fonts.font_name}",
                        self.fonts.small(), theme.color("text_mute"), (140, 782))
        if self.app.fonts.warning:
            theme.draw_text(surface, self.app.fonts.warning, self.fonts.small(),
                            theme.color("warning"), (140, 804))
