"""新手引导：第一次进对局时的轻量分步提示（6 步，不阻塞操作）。

与 `TutorialOverlay` 的分工：

| | TutorialOverlay（主菜单 → 新手教程） | GuideOverlay（首次进对局） |
| --- | --- | --- |
| 触发 | 玩家主动点 | 第一次进对局自动出现 |
| 形式 | 教练面板 + 分步说明，跟着真实对局走 | 遮罩挖洞高亮真实控件 + 一句话说明 |
| 目的 | 完整学会（买地 / 收租 / 升级 / 道具） | 知道第一步该点哪里 |

**高亮用的是真实界面坐标**：掷骰按钮、棋盘、资产按钮、手牌区，
都是 GameScene 里同一批常量，不是另画一套示意图。
引导不吃输入（除了自己的按钮），跳过与"以后不再显示"都只写设置，不影响对局。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from ..utils.easing import clamp, ease_out_cubic
from . import icons, theme
from .widgets import Button

SCREEN_W = 1600
SCREEN_H = 900

#: 挖洞高亮的形状参数
FOCUS_PAD = 10
SCRIM_ALPHA = 158


class GuideStep:
    """一步引导：高亮哪一块 + 说什么。"""

    __slots__ = ("title", "text", "hint", "focus", "place")

    def __init__(self, title: str, text: str, hint: str,
                 focus: Callable[[Any], pygame.Rect] | None, place: str = "auto") -> None:
        self.title = title
        self.text = text
        self.hint = hint
        self.focus = focus            # 传入 scene，返回要高亮的矩形（None = 全屏中心）
        self.place = place            # auto / top / bottom


def _roll_button(scene: Any) -> pygame.Rect:
    from .game_scene import ROLL_BUTTON_RECT, SIDE_RECT
    rect = pygame.Rect(ROLL_BUTTON_RECT)
    # 把手牌区一起框进来（第一回合最容易问「我的牌在哪」）
    return rect.union(pygame.Rect(SIDE_RECT.x, SIDE_RECT.y, SIDE_RECT.width, 286))


def _board(scene: Any) -> pygame.Rect:
    from .game_scene import BOARD_RECT
    return pygame.Rect(BOARD_RECT)


def _assets_button(scene: Any) -> pygame.Rect:
    return pygame.Rect(1314, 14, 280, 60)


def _cards_area(scene: Any) -> pygame.Rect:
    from .game_scene import CARD_H, CARD_ORIGIN, CARD_W, SIDE_RECT
    rect = pygame.Rect(CARD_ORIGIN[0] - 8, CARD_ORIGIN[1] - 26,
                       CARD_W * 5 + 6 * 4 + 24, CARD_H + 44)
    return rect.union(pygame.Rect(SIDE_RECT.x, SIDE_RECT.y + 180, 240, 110))


def _center(scene: Any) -> pygame.Rect:
    rect = pygame.Rect(0, 0, 700, 300)
    rect.center = (800, 430)
    return rect


def build_steps() -> list[GuideStep]:
    return [
        GuideStep(
            "1 / 6　掷骰子开始行动",
            "轮到你就点右边这个「掷骰子」（或者直接按空格）。"
            "两个骰子的点数决定你走几步。",
            "先点一下试试",
            _roll_button, place="bottom"),
        GuideStep(
            "2 / 6　棋子会逐格前进",
            "经过起点能领工资；落到哪一格，就按那一格的规则处理。"
            "格子外圈的色带表示片区，集齐整个片区租金翻倍。",
            "看看你落在哪一格",
            _board, place="top"),
        GuideStep(
            "3 / 6　落在空地就能买",
            "弹出购买窗口时，会写清售价、现在的租金和满级租金。"
            "买下的地，别人踩到就要给你交租。",
            "现金够就买，不够就放弃",
            _center, place="bottom"),
        GuideStep(
            "4 / 6　收租与管理资产",
            "别人踩到你的地会给你交租。点右上角「资产」（或按 I）"
            "可以升级、抵押、出售自己的地产。",
            "升级能大幅提高租金",
            _assets_button, place="bottom"),
        GuideStep(
            "5 / 6　道具牌",
            "右侧「我的道具」是你的手牌，按数字键 1～5 或直接点卡片使用。"
            "灰掉带锁的卡表示现在不能用，鼠标放上去会说明原因。",
            "每张卡都有使用时机",
            _cards_area, place="top"),
        GuideStep(
            "6 / 6　钱不够时不会立刻死",
            "付不起钱会打开债务面板，你可以抵押或出售地产自救；"
            "实在还不上才会破产。最后活着的人获胜。",
            "就是这样，开始玩吧！",
            None, place="center"),
    ]


class GuideOverlay:
    """引导覆盖层：高亮 + 说明卡 + 三个按钮。"""

    def __init__(self, on_finish: Callable[[bool], None] | None = None) -> None:
        self.steps = build_steps()
        self.index = 0
        self.finished = False
        self.on_finish = on_finish
        self._t = 0.0
        self.scene: Any = None
        self._build_buttons()

    def _build_buttons(self) -> None:
        self.buttons = [
            Button(pygame.Rect(0, 0, 132, 44), "不再显示",
                   on_click=lambda: self.finish(skip_all=True),
                   style="ghost", font_size=15),
            Button(pygame.Rect(0, 0, 120, 44), "跳过",
                   on_click=lambda: self.finish(skip_all=False),
                   style="secondary", font_size=15),
            Button(pygame.Rect(0, 0, 160, 44), "下一步",
                   on_click=self.advance, style="accent", font_size=15,
                   icon="arrow_right"),
        ]

    # ------------------------------------------------------------ 生命周期

    def bind(self, scene: Any) -> None:
        self.scene = scene

    @property
    def step(self) -> GuideStep | None:
        if self.finished or self.index >= len(self.steps):
            return None
        return self.steps[self.index]

    def advance(self) -> None:
        self.index += 1
        self._t = 0.0
        if self.index >= len(self.steps):
            self.finish(skip_all=False)

    def finish(self, skip_all: bool) -> None:
        self.finished = True
        if self.on_finish is not None:
            self.on_finish(skip_all)

    def update(self, dt: float) -> None:
        if self.finished:
            return
        self._t += dt

    # ------------------------------------------------------------ 事件

    def handle_event(self, event: pygame.event.Event) -> bool:
        if self.finished:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.advance()
                return True
            if event.key == pygame.K_ESCAPE:
                self.finish(skip_all=False)
                return True
        for button in self.buttons:
            if button.handle_event(event):
                return True
        return False

    # ------------------------------------------------------------ 绘制

    def _focus_rect(self) -> pygame.Rect | None:
        step = self.step
        if step is None or step.focus is None or self.scene is None:
            return None
        try:
            rect = step.focus(self.scene)
        except Exception:
            return None
        return rect.inflate(FOCUS_PAD * 2, FOCUS_PAD * 2)

    def _panel_rect(self, focus: pygame.Rect | None) -> pygame.Rect:
        step = self.step
        rect = pygame.Rect(0, 0, 620, 178)
        place = step.place if step is not None else "center"
        if focus is None or place == "center":
            rect.center = (SCREEN_W // 2, SCREEN_H // 2 + 40)
            return rect
        if place == "top":
            rect.midbottom = (focus.centerx, max(150, focus.top - 18))
        else:
            rect.midtop = (focus.centerx, min(SCREEN_H - 210, focus.bottom + 18))
        rect.clamp_ip(pygame.Rect(24, 24, SCREEN_W - 48, SCREEN_H - 48))
        return rect

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        step = self.step
        if step is None:
            return
        focus = self._focus_rect()

        # 遮罩 + 挖洞：让玩家看清「就是这一块」
        mask = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        mask.fill((10, 14, 22, SCRIM_ALPHA))
        if focus is not None:
            hole = pygame.Surface(focus.size, pygame.SRCALPHA)
            hole.fill((0, 0, 0, 0))
            mask.blit(hole, focus.topleft, special_flags=pygame.BLEND_RGBA_MIN)
        surface.blit(mask, (0, 0))

        if focus is not None:
            pulse = 0.5 + 0.5 * ease_out_cubic(clamp(self._t / 0.6, 0.0, 1.0))
            border = theme.color("accent")
            pygame.draw.rect(surface, border, focus, width=3, border_radius=14)
            pygame.draw.rect(surface, theme.color("accent_soft", int(70 + 60 * pulse)),
                             focus.inflate(6, 6), width=2, border_radius=16)

        panel = self._panel_rect(focus)
        theme.shadow_rect(surface, panel, radius=16, spread=8, alpha=140)
        theme.rounded_rect(surface, panel, theme.color("panel_alt", 250), radius=16)
        theme.rounded_rect(surface, panel, None, radius=16, border=theme.color("accent"),
                           border_width=2)
        theme.rounded_rect(surface, pygame.Rect(panel.x, panel.y, 6, panel.height),
                           theme.color("accent"), radius=3)

        frac = (self.index + 1) / max(1, len(self.steps))
        theme.progress_bar(surface, pygame.Rect(panel.x + 26, panel.y + 16,
                                                panel.width - 52, 6),
                           frac, color_name="accent")
        theme.draw_text(surface, step.title, fonts.h2(), theme.color("text"),
                        (panel.x + 26, panel.y + 32))
        theme.draw_wrapped(surface, step.text, fonts.body(), theme.color("text_dim"),
                           pygame.Rect(panel.x + 26, panel.y + 68, panel.width - 52, 60))
        icons.draw_icon(surface, "info", pygame.Rect(panel.x + 26, panel.bottom - 44, 18, 18),
                        theme.color("accent"), theme.color("shadow"))
        theme.draw_text(surface, theme.truncate(step.hint, fonts.small(), panel.width - 90),
                        fonts.small(), theme.color("accent"),
                        (panel.x + 52, panel.bottom - 44))

        # 按钮排布：右下角三个，从右往左
        last = self.index == len(self.steps) - 1
        self.buttons[2].label = "开始游戏" if last else "下一步"
        x = panel.right - 26 - self.buttons[2].rect.width
        self.buttons[2].rect.topleft = (x, panel.bottom - 56)
        x -= 12 + self.buttons[1].rect.width
        self.buttons[1].rect.topleft = (x, panel.bottom - 56)
        x -= 12 + self.buttons[0].rect.width
        self.buttons[0].rect.topleft = (x, panel.bottom - 56)

        mouse = pygame.mouse.get_pos()
        for button in self.buttons:
            button.update(1 / 60, mouse)
            button.draw(surface, fonts)
