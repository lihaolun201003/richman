"""新手教程：用一个真实短局把核心循环走一遍。

设计约束（重要）：
- **不创建第二套规则引擎**。教程跑的就是真实 `GameEngine` + 真实 `Command`，
  只是额外叠一层「教练面板」，告诉你现在该做什么；
- 教程面板不吃输入（不阻塞任何操作），只在观感上引导；
- 每一步的完成条件都来自真实状态（阶段 / 决策 / 统计），不是写死的计时器，
  所以玩家慢慢玩也不会卡在教程里。

步骤对应需求里的 8 件事：掷骰 / 移动 / 买地 / 收租 / 升级 / 用道具 / 资产管理 / 胜负。
"""
from __future__ import annotations

from typing import Any, Callable

import pygame

from ..game.phases import GamePhase
from . import icons, theme
from .widgets import Button

PanelCheck = Callable[[Any, Any], bool]


class TutorialStep:
    """一个教学步骤。"""

    __slots__ = ("title", "text", "hint", "check", "focus", "manual_only")

    def __init__(self, title: str, text: str, hint: str, check: PanelCheck,
                 focus: str = "", manual_only: bool = False) -> None:
        self.title = title
        self.text = text
        self.hint = hint
        self.check = check
        self.focus = focus            # 高亮区域名（roll / board / cards / assets）
        self.manual_only = manual_only


def _stat(state, key: str) -> int:
    me = state.player(state.current_player_id) if state else None
    return int((me.stats or {}).get(key, 0)) if me else 0


def build_steps() -> list[TutorialStep]:
    """构造教程步骤。每条的完成条件都基于真实状态。"""
    return [
        TutorialStep(
            "1 / 8　开始行动",
            "这是棋盘。左下角那个带编号的圆点就是你的棋子，"
            "外圈色带表示片区，格子里的图标表示类型。",
            "点右侧橙色「掷骰子」（或按空格）开始你的第一回合。",
            lambda st, sc: st.turn_number > 1,
            focus="roll"),
        TutorialStep(
            "2 / 8　掷骰与移动",
            "骰子停下后棋子会逐格前进，经过起点能领工资。"
            "落点决定了接下来发生什么。",
            "等棋子停下来，看看你落在哪一格。",
            lambda st, sc: st.phase in (GamePhase.RESOLVE_TILE, GamePhase.WAIT_DECISION)
            or st.turn_number > 1,
            focus="board"),
        TutorialStep(
            "3 / 8　买下地产",
            "落在无主地产会弹出购买窗口，里面写着售价、当前租金和满级租金。"
            "买下之后，对手踩到就要给你交租。",
            "在弹窗里点「购买」把这块地买下来（现金够的话）。",
            lambda st, sc: _stat(st, "properties_bought") > 0 or st.round_number > 2,
            focus="modal"),
        TutorialStep(
            "4 / 8　收租与交租",
            "踩到别人的地产要付租金；付不够会进入债务处理，"
            "你可以抵押或出售地产自救，实在还不上才会破产。",
            "继续掷骰，试试让对手踩到你的地。",
            lambda st, sc: (st.player(sc.session.player_id).stats.get("rent_income", 0) > 0
                            if sc.session and st.player(sc.session.player_id) else False)
            or st.round_number > 3,
            focus="board"),
        TutorialStep(
            "5 / 8　升级地产",
            "自己的地可以升到 3 级，租金会大幅上涨；"
            "集齐同一片区的全部地产还能让该区租金翻倍。",
            "按 I 打开资产面板，给一块自己的地产点「升级」。",
            lambda st, sc: _stat(st, "properties_upgraded") > 0 or st.round_number > 4,
            focus="assets"),
        TutorialStep(
            "6 / 8　使用道具",
            "右侧「我的道具」是你的手牌。"
            "**灰掉并带锁的卡表示当前时机不能用**，把鼠标放上去会告诉你原因。",
            "用鼠标点一张亮着的卡，或者按数字键 1～5 使用它。",
            lambda st, sc: _stat(st, "cards_used") > 0 or st.round_number > 5,
            focus="cards"),
        TutorialStep(
            "7 / 8　资产管理",
            "资产面板里可以升级 / 出售 / 抵押 / 赎回。"
            "抵押能拿到现金但要放弃收租，赎回要付 110%。",
            "随时按 I 打开资产面板看看自己的全部资产。",
            lambda st, sc: st.round_number > 6, focus="assets", manual_only=True),
        TutorialStep(
            "8 / 8　怎么算赢",
            "让其他玩家全部破产，你就是赢家；"
            "如果打到轮数上限，就按总资产排名判定。",
            "教程到此结束，可以继续把这局打完，也可以回主菜单开新的一局。",
            lambda st, sc: st.game_over, focus="", manual_only=True),
    ]


class TutorialOverlay:
    """教练面板：显示当前步骤、进度与提示，不阻塞操作。"""

    def __init__(self) -> None:
        self.steps = build_steps()
        self.index = 0
        self._seen_phase = None
        self._step_entered = 0.0
        self.finished = False
        self._skip_requested = False
        #: 放在棋盘中央信息区的中段（骰子托盘与最近事件之间），不遮挡任何可点区域
        self.rect = pygame.Rect(0, 0, 1000, 164)
        self.rect.center = (800, 466)
        self.buttons: list[Button] = []
        self._build_buttons()

    def _build_buttons(self) -> None:
        self.buttons = [
            Button(pygame.Rect(self.rect.right - 320, self.rect.bottom - 58, 130, 44),
                   "跳过教程", on_click=self.finish, style="ghost", font_size=16),
            Button(pygame.Rect(self.rect.right - 178, self.rect.bottom - 58, 146, 44),
                   "下一步", on_click=self.advance, style="secondary", font_size=16,
                   icon="arrow_right"),
        ]

    # ------------------------------------------------------------ 生命周期

    def bind(self, scene: Any) -> None:
        self.scene = scene

    @property
    def step(self) -> TutorialStep | None:
        if self.finished or self.index >= len(self.steps):
            return None
        return self.steps[self.index]

    def advance(self) -> None:
        self.index += 1
        self._step_entered = 0.0
        if self.index >= len(self.steps):
            self.finish()

    def finish(self) -> None:
        self.finished = True
        try:
            self.scene.app.settings.set("ui", "tutorial_done", True)
        except Exception:
            pass
        self.scene.toasts.push("教程结束，祝你好运！", "success")

    # ------------------------------------------------------------ 每帧

    def update(self, state: Any, scene: Any) -> None:
        if self.finished or state is None:
            return
        self._step_entered += 1.0 / 60.0
        step = self.step
        if step is None:
            return
        if self._seen_phase != state.phase:
            self._seen_phase = state.phase
        # 从第 1 步开始，满足条件就自动进入下一步（第 1 步用掷骰次数判断）
        if self.index == 0 and state.turn_number > 1:
            self.advance()
            return
        if self.index > 0 and self._step_entered > 1.2:
            try:
                if step.check(state, scene):
                    self.advance()
            except Exception:
                pass

    def handle_event(self, event: pygame.event.Event) -> bool:
        for button in self.buttons:
            if button.handle_event(event):
                return True
        return False

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        step = self.step
        if step is None:
            if self.finished:
                self._draw_finished(surface, fonts)
            return
        rect = self.rect
        theme.shadow_rect(surface, rect, radius=16, spread=8, alpha=150)
        theme.rounded_rect(surface, rect, theme.color("panel_alt", 246), radius=16)
        theme.rounded_rect(surface, rect, None, radius=16, border=theme.color("primary"),
                           border_width=2)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, 6, rect.height),
                           theme.color("primary"), radius=3)

        # 进度
        frac = (self.index + 1) / max(1, len(self.steps))
        theme.progress_bar(surface, pygame.Rect(rect.x + 24, rect.y + 14, rect.width - 48, 6),
                           frac, color_name="primary")

        theme.draw_text(surface, step.title, fonts.h2(), theme.color("primary"),
                        (rect.x + 24, rect.y + 30))
        theme.draw_wrapped(surface, step.text, fonts.body(), theme.color("text"),
                           pygame.Rect(rect.x + 24, rect.y + 62, rect.width - 48, 46))
        icons.draw_icon(surface, "info", pygame.Rect(rect.x + 24, rect.bottom - 52, 20, 20),
                        theme.color("accent"), theme.color("shadow"))
        theme.draw_text(surface, theme.truncate(step.hint, fonts.small(), rect.width - 380),
                        fonts.small(), theme.color("accent"),
                        (rect.x + 52, rect.bottom - 50))
        mouse = pygame.mouse.get_pos()
        for button in self.buttons:
            button.update(1 / 60, mouse)
            button.draw(surface, fonts)

    def _draw_finished(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        rect = pygame.Rect(0, 0, 640, 96)
        rect.midbottom = (800, 890)
        theme.rounded_rect(surface, rect, theme.color("panel_alt", 236), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14, border=theme.color("success"),
                           border_width=2)
        icons.draw_icon(surface, "check", pygame.Rect(rect.x + 20, rect.y + 30, 32, 32),
                        theme.color("success"), theme.color("shadow"))
        theme.draw_text(surface, "教程完成：你已经会玩这款游戏了", fonts.h3(),
                        theme.color("text"), (rect.x + 66, rect.y + 22))
        theme.draw_text(surface, "继续打完这局，或者按 ESC 菜单退出回主菜单开新局。",
                        fonts.small(), theme.color("text_dim"), (rect.x + 66, rect.y + 52))
