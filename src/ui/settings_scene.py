"""设置界面：游戏节奏、音量与开关、显示、昵称、调试。

改动即时生效，并写入用户数据目录（打包后是 exe 旁边的 config/，
开发时是项目里的 config/），不会写进只读资源目录。

v0.3 的改动：
- **修正了分组框与控件错位**（原来「音频」框只有 190 高，静音/调试开关都跑到框外）；
- 新增「游戏节奏：慢 / 标准 / 快」——只影响演出时长，不改变任何规则；
- 新增音效开关、音乐开关、详细日志开关；
- 明确显示设置文件的真实路径，出问题时玩家知道去哪找。
"""
from __future__ import annotations

import os
from typing import Any

import pygame

from ..persistence.settings import (
    FONT_SCALE_CHOICES,
    PACE_CHOICES,
    RESOLUTION_CHOICES,
)
from . import icons, theme
from .scene import Scene
from .widgets import Button, SegmentedControl, Slider, TextInput, Toggle

PAGE_X = 140
COL_W = 620


class SettingsScene(Scene):
    """设置场景。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.back_target = "menu"
        self._layout_key: tuple = ()
        self._build()

    # ------------------------------------------------------------ 布局

    def _build(self) -> None:
        fonts = self.app.fonts
        key = (fonts.scale,)
        if key == self._layout_key:
            return
        self._layout_key = key
        y0 = theme.page_content_top(
            fonts, "所有改动立即生效，并保存到用户数据目录（不会写进只读资源目录）")
        self.y0 = y0
        col1 = PAGE_X
        col2 = PAGE_X + COL_W + 60

        # ---- 左列：音频
        self.slider_master = Slider(
            pygame.Rect(col1, y0 + 46, COL_W, 24), self.app.settings.master_volume,
            0.0, 1.0, 0.05, on_change=self._on_master, label="主音量")
        self.slider_bgm = Slider(
            pygame.Rect(col1, y0 + 128, COL_W, 24), self.app.settings.bgm_volume,
            0.0, 1.0, 0.05, on_change=self._on_bgm, label="背景音乐音量")
        self.slider_sfx = Slider(
            pygame.Rect(col1, y0 + 210, COL_W, 24), self.app.settings.sfx_volume,
            0.0, 1.0, 0.05, on_change=self._on_sfx, label="音效音量")
        self.toggle_mute = Toggle(
            pygame.Rect(col1, y0 + 276, 200, 28), self.app.settings.muted,
            on_change=self._on_mute, label="全部静音")
        self.toggle_sfx = Toggle(
            pygame.Rect(col1 + 260, y0 + 276, 200, 28), self.app.settings.sfx_enabled,
            on_change=self._on_sfx_enabled, label="音效开关")
        self.toggle_bgm = Toggle(
            pygame.Rect(col1 + 520, y0 + 276, 200, 28), self.app.settings.bgm_enabled,
            on_change=self._on_bgm_enabled, label="音乐开关")

        # ---- 右列：显示与操作
        self.pace_select = SegmentedControl(
            pygame.Rect(col2, y0 + 46, COL_W, 44),
            [(f"{label}（{value:g}x）", value) for label, value in PACE_CHOICES],
            value=self._nearest_pace(), on_change=self._on_pace,
            label="游戏节奏（只影响演出时长，不改规则）")
        self.anim_select = SegmentedControl(
            pygame.Rect(col2, y0 + 128, COL_W, 44),
            [(label, value) for label, value in
             [("0.5x", 0.5), ("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)]],
            value=self.app.settings.animation_speed, on_change=self._on_anim,
            label="动画速度微调")
        self.font_select = SegmentedControl(
            pygame.Rect(col2, y0 + 210, COL_W, 44),
            [(label, value) for label, value in FONT_SCALE_CHOICES],
            value=self.app.settings.font_scale, on_change=self._on_font,
            label="界面字体大小")
        self.res_select = SegmentedControl(
            pygame.Rect(col2, y0 + 292, COL_W, 44),
            [(f"{w}×{h}", (w, h)) for w, h in RESOLUTION_CHOICES],
            value=(self.app.settings.width, self.app.settings.height),
            on_change=self._on_resolution, label="窗口分辨率")
        self.fullscreen_toggle = Toggle(
            pygame.Rect(col2, y0 + 358, 220, 28), self.app.settings.fullscreen,
            on_change=self._on_fullscreen, label="全屏显示")
        self.nick_input = TextInput(
            pygame.Rect(col2, y0 + 432, 380, 44), self.app.settings.nickname,
            placeholder="昵称（最长 12 字）", max_length=12,
            on_change=lambda v: self.app.settings.set_nickname(v))
        self.debug_toggle = Toggle(
            pygame.Rect(col2, y0 + 500, 220, 28), self.app.settings.debug_overlay,
            on_change=self._on_debug, label="调试信息 (F1)")
        self.log_toggle = Toggle(
            pygame.Rect(col2 + 260, y0 + 500, 240, 28),
            self.app.settings.detailed_log, on_change=self._on_log, label="详细事件日志")

        self.widgets = [
            self.slider_master, self.slider_bgm, self.slider_sfx,
            self.toggle_mute, self.toggle_sfx, self.toggle_bgm,
            self.pace_select, self.anim_select, self.font_select, self.res_select,
            self.fullscreen_toggle, self.nick_input, self.debug_toggle, self.log_toggle,
            Button(pygame.Rect(PAGE_X, 812, 240, 54), "返回", on_click=self._back,
                   style="secondary", icon="exit"),
            Button(pygame.Rect(PAGE_X + 264, 812, 240, 54), "恢复默认",
                   on_click=self._reset, style="ghost", icon="refresh"),
            Button(pygame.Rect(PAGE_X + 528, 812, 260, 54), "保存设置",
                   on_click=self._save, style="accent", icon="save"),
        ]

    def _nearest_pace(self) -> float:
        speed = self.app.settings.animation_speed
        best = PACE_CHOICES[1][1]
        for _label, value in PACE_CHOICES:
            if abs(value - speed) < abs(best - speed):
                best = value
        return best

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

    def _on_sfx_enabled(self, value: bool) -> None:
        self.app.settings.set("audio", "sfx_enabled", value)
        self.app.audio.set_sfx_enabled(value)
        self.app.audio.play_sfx("click")

    def _on_bgm_enabled(self, value: bool) -> None:
        self.app.settings.set("audio", "bgm_enabled", value)
        self.app.audio.set_bgm_enabled(value)

    def _on_pace(self, value: float) -> None:
        self.app.settings.set_pace(value)
        self.anim_select.set_value(value)
        self.app.apply_animation_speed()
        self.notify(f"游戏节奏：{self.app.settings.pace_label()}", "info")

    def _on_anim(self, value: float) -> None:
        self.app.settings.set("ui", "animation_speed", value)
        self.pace_select.set_value(self._nearest_pace())
        self.app.apply_animation_speed()

    def _on_font(self, value: float) -> None:
        self.app.settings.set("ui", "font_scale", value)
        self.app.apply_font_scale()
        self._layout_key = ()          # 字号变了要重排本页

    def _on_resolution(self, value: tuple[int, int]) -> None:
        self.app.settings.set_resolution(*value)
        self.app.apply_display()
        self.notify(f"分辨率已切换为 {value[0]}×{value[1]}", "success")

    def _on_fullscreen(self, value: bool) -> None:
        self.app.settings.set_fullscreen(value)
        self.app.apply_display()

    def _on_debug(self, value: bool) -> None:
        self.app.settings.set("ui", "debug_overlay", value)

    def _on_log(self, value: bool) -> None:
        self.app.settings.set("ui", "detailed_log", value)

    def _back(self) -> None:
        self.app.settings.save()
        self.app.scenes.switch_to(self.back_target)

    def _save(self) -> None:
        if self.app.settings.save():
            self.notify(f"设置已保存到 {self.app.settings.path}", "success")
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
        self.toggle_sfx.value = s.sfx_enabled
        self.toggle_bgm.value = s.bgm_enabled
        self.pace_select.set_value(self._nearest_pace())
        self.anim_select.set_value(s.animation_speed)
        self.font_select.set_value(s.font_scale)
        self.res_select.set_value((s.width, s.height))
        self.fullscreen_toggle.value = s.fullscreen
        self.nick_input.set_text(s.nickname)
        self.debug_toggle.value = s.debug_overlay
        self.log_toggle.value = s.detailed_log

    # ------------------------------------------------------------ 场景

    def on_enter(self, **kwargs: Any) -> None:
        self.back_target = kwargs.get("back", "menu")
        self._build()
        self._sync_widgets()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()
            return
        super().handle_event(event)

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        self._build()
        theme.page_title(surface, self.fonts, "设置",
                         "所有改动立即生效，并保存到用户数据目录（不会写进只读资源目录）")
        y0 = self.y0

        left = pygame.Rect(PAGE_X - 30, y0 - 12, COL_W + 60, 330)
        theme.panel(surface, left, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(left.x + 20, left.y + 14, left.width - 40, 22),
                             "音频", icon="audio")

        right = pygame.Rect(PAGE_X + COL_W + 30, y0 - 12, COL_W + 60, 552)
        theme.panel(surface, right, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(right.x + 20, right.y + 14, right.width - 40, 22),
                             "显示与操作", icon="gear")

        theme.draw_text(surface, "昵称", self.fonts.small(), theme.color("text_dim"),
                        (PAGE_X + COL_W + 60, y0 + 412))
        theme.draw_text(surface, "调试与日志", self.fonts.small(), theme.color("text_dim"),
                        (PAGE_X + COL_W + 60, y0 + 480))

        self.draw_widgets(surface)

        # 真实状态与路径
        info_y = 700
        icons.draw_icon(surface, "info", pygame.Rect(PAGE_X, info_y - 2, 18, 18),
                        theme.color("text_mute"), theme.color("shadow"))
        theme.draw_text(surface, f"音频状态：{self.app.audio.status_text}",
                        self.fonts.small(), theme.color("text_mute"),
                        (PAGE_X + 26, info_y))
        theme.draw_text(surface, f"中文字体：{self.app.fonts.font_name}",
                        self.fonts.small(), theme.color("text_mute"),
                        (PAGE_X + 26, info_y + 24))
        theme.draw_text(surface, f"设置文件：{self.app.settings.path}",
                        self.fonts.small(), theme.color("text_mute"),
                        (PAGE_X + 26, info_y + 48))
        if self.app.fonts.warning:
            theme.draw_text(surface, self.app.fonts.warning, self.fonts.small(),
                            theme.color("warning"), (PAGE_X + 26, info_y + 72))
