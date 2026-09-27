"""模态弹窗：购买、升级、机遇、监狱、破产、结算、暂停菜单。

关键约束：Modal 出现时会吃掉所有输入事件（handle_event 永远返回 True），
因此底层按钮不可能被穿透点击。

v0.3 修正的两个真实问题：
1. `_panel()` 之前返回「按动画缩放后」的矩形，而按钮坐标仍按未缩放的
   `self.rect` 计算 —— 弹出动画期间按钮会溢出面板（肉眼可见的错位）。
   现在统一用 `self.rect` 布局，缩放只作用于「内容层」的绘制偏移，
   因此按钮永远与面板对齐。
2. `DecisionDialog` 的高度公式没算标题与描述，最后一个按钮必然溢出。
   现在按内容逐项累加高度。
"""
from __future__ import annotations

import math
from typing import Any, Callable

import pygame

from ..game.commands import CommandType, PendingDecision
from ..game.events import money as fmt_money
from ..utils.easing import Tween, ease_out_back, ease_out_cubic
from . import icons, theme
from .widgets import Button

#: 逻辑分辨率
SCREEN_W = 1600
SCREEN_H = 900

#: 按钮标准高度
BTN_H = theme.BTN_H["lg"]
BTN_GAP = 10


class Modal:
    """模态弹窗基类。"""

    #: 是否允许点击遮罩关闭
    dismissable = False

    def __init__(self, on_close: Callable[[], None] | None = None) -> None:
        self.done = False
        self.on_close = on_close
        self.anim = Tween(0.22)
        self.buttons: list[Button] = []
        self._closing = False

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.done = True
        if self.on_close is not None:
            self.on_close()

    # ---- 生命周期
    def handle_event(self, event: pygame.event.Event) -> bool:
        """模态弹窗吞掉所有事件。"""
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            self.on_key(event.key)
        return True

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE and self.dismissable:
            self.close()

    def update(self, dt: float) -> None:
        self.anim.update(dt)
        mouse = pygame.mouse.get_pos()
        for button in self.buttons:
            button.update(dt, mouse)

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        raise NotImplementedError

    def _draw_scrim(self, surface: pygame.Surface) -> None:
        alpha = int(180 * ease_out_cubic(self.anim.progress))
        layer = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        layer.fill(theme.color("overlay", alpha))
        surface.blit(layer, (0, 0))

    @property
    def scale(self) -> float:
        """弹出动画的缩放系数（只用于内容层的微缩放，不影响命中区域）。"""
        return 0.92 + 0.08 * ease_out_back(self.anim.progress)

    def _panel(self, surface: pygame.Surface, rect: pygame.Rect) -> pygame.Rect:
        """绘制面板底板。

        返回的矩形**始终等于 rect**（不做位移缩放），
        这样按钮、文字与面板永远对齐；弹出感由 `scale` 作用在内容绘制上。
        """
        theme.shadow_rect(surface, rect, radius=18, spread=8, alpha=150)
        theme.rounded_rect(surface, rect, theme.color("panel_alt"), radius=18)
        theme.rounded_rect(surface, rect, None, radius=18, border=theme.color("border"),
                           border_width=2)
        return rect


# ==================================================================== 通用决策

class DecisionDialog(Modal):
    """由 PendingDecision 直接生成的通用弹窗。"""

    def __init__(
        self,
        decision: PendingDecision,
        on_choose: Callable[[str], None],
        accent: str = "accent",
        extra_lines: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.decision = decision
        self.on_choose = on_choose
        self.accent = accent
        self.extra_lines = extra_lines or []
        self.rect = self._compute_rect()
        self._build_buttons()

    def _compute_rect(self) -> pygame.Rect:
        n = max(1, len(self.decision.options))
        width = 660
        head = 34 + 8                       # 顶部色条 + 间距
        head += 40 + 14                     # 标题（h1）行高 + 间距
        if self.decision.description:
            head += 34 * max(1, len(theme.wrap_text(
                self.decision.description, pygame.font.Font(None, 17), width - 60)))
            head += 10
        head += 24 * len(self.extra_lines)
        body = n * (BTN_H + BTN_GAP) + 18
        footer = 28
        height = head + body + footer
        height = min(height, SCREEN_H - 80)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        return rect

    def _build_buttons(self) -> None:
        self.buttons = []
        rect = self.rect
        total = len(self.decision.options)
        y = rect.bottom - 22 - total * (BTN_H + BTN_GAP)
        for opt in self.decision.options:
            style = "ghost"
            if opt.id in ("buy", "upgrade", "roll", "confirm", "pay"):
                style = "primary"
            if opt.id in ("buy", "upgrade"):
                style = "accent"
            if opt.danger:
                style = "danger"
            if opt.id in ("skip", "cancel", "leave"):
                style = "ghost"
            button = Button(
                pygame.Rect(rect.x + 30, y, rect.width - 60, BTN_H),
                opt.label,
                on_click=lambda oid=opt.id: self._choose(oid),
                style=style,
                enabled=opt.enabled,
                tooltip=opt.hint,
            )
            self.buttons.append(button)
            y += BTN_H + BTN_GAP

    def _choose(self, option_id: str) -> None:
        self.on_choose(option_id)
        self.close()

    def on_key(self, key: int) -> None:
        # 数字键快捷选择
        if pygame.K_1 <= key <= pygame.K_9:
            idx = key - pygame.K_1
            if idx < len(self.buttons) and self.buttons[idx].enabled:
                self.buttons[idx].on_click()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self.accent)

        bar = pygame.Rect(rect.x, rect.y, rect.width, 8)
        theme.rounded_rect(surface, bar, accent, radius=4)

        theme.draw_text(surface, self.decision.title, fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 34), anchor="midtop")

        y = rect.y + 88
        if self.decision.description:
            desc_font = fonts.body()
            lines = theme.wrap_text(self.decision.description, desc_font, rect.width - 60)
            for line in lines:
                theme.draw_text(surface, line, desc_font, theme.color("text_dim"),
                                (rect.centerx, y), anchor="midtop")
                y += 34
        for line in self.extra_lines:
            theme.draw_text(surface, line, fonts.small(), theme.color("text_mute"),
                            (rect.centerx, y), anchor="midtop")
            y += 24

        for button in self.buttons:
            button.draw(surface, fonts)

        if self.dismissable:
            theme.draw_text(surface, "按 ESC 关闭", fonts.tiny(), theme.color("text_mute"),
                            (rect.centerx, rect.bottom - 14), anchor="midbottom")


# ==================================================================== 机遇卡

class ChanceCardDialog(Modal):
    """机遇卡翻出展示（点击继续时使用）。"""

    def __init__(self, title: str, description: str, detail: str = "",
                 rarity: str = "common", polarity: str = "neutral",
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.title = title
        self.description = description
        self.detail = detail
        self.rarity = rarity
        self.polarity = polarity
        self.rect = pygame.Rect(0, 0, 600, 400)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [
            Button(pygame.Rect(self.rect.x + 170, self.rect.bottom - 84, 260, 52),
                   "知道了", on_click=self.close, style="accent", icon="check"),
        ]

    def _accent(self) -> str:
        return {"fortune": "success", "disaster": "danger"}.get(
            self.polarity,
            {"common": "info", "uncommon": "accent", "rare": "danger"}.get(
                self.rarity, "accent"))

    def _header(self) -> tuple[str, str]:
        if self.polarity == "fortune":
            return "福 运", "fortune"
        if self.polarity == "disaster":
            return "灾 祸", "disaster"
        return "机 遇", "star"

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self._accent())
        label, icon_name = self._header()

        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 10), accent,
                           radius=5)
        icons.draw_icon(surface, icon_name, pygame.Rect(rect.x + 30, rect.y + 28, 32, 32),
                        accent, theme.color("shadow"))
        theme.draw_text(surface, label, fonts.h2(), accent, (rect.x + 74, rect.y + 32))

        theme.draw_text(surface, self.title, fonts.sized(30, True), theme.color("text"),
                        (rect.centerx, rect.y + 92), anchor="midtop")

        box = pygame.Rect(rect.x + 36, rect.y + 150, rect.width - 72, 110)
        theme.rounded_rect(surface, box, theme.color("bg_alt"), radius=12)
        theme.draw_wrapped(surface, self.description, fonts.body(), theme.color("text_dim"),
                           pygame.Rect(box.x + 16, box.y + 14, box.width - 32, box.height - 24))

        if self.detail:
            theme.draw_text(surface, self.detail, fonts.sized(20, True), accent,
                            (rect.centerx, rect.y + 278), anchor="midtop")

        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 结算

class GameOverDialog(Modal):
    """最终结算界面：分区展示比赛结果 / 排名 / 玩家统计 / 经济统计 / 趣味称号。

    只展示真实存在的数据（全部来自 GameState.stats 与 EconomyLedger），
    没有统计的东西不显示，绝不编造。
    """

    #: 玩家统计表的列：(标题, 取值函数)
    STAT_COLUMNS = [
        ("总收入", lambda r: sum(r["income"].values())),
        ("总支出", lambda r: sum(r["expense"].values())),
        ("租金收入", lambda r: r["income"].get("租金", 0)),
        ("租金支出", lambda r: r["expense"].get("租金", 0)),
        ("购地", lambda r: r["stats"].get("properties_bought", 0)),
        ("升级", lambda r: r["stats"].get("properties_upgraded", 0)),
        ("道具", lambda r: r["stats"].get("cards_used", 0)),
        ("机遇", lambda r: r["stats"].get("chance_events", 0)),
        ("抽税", lambda r: r["expense"].get("税收", 0)),
        ("最终净资产", lambda r: r["total_asset"]),
    ]

    def __init__(self, ranking: list[dict[str, Any]], rounds: int, stats: dict[str, Any],
                 on_again: Callable[[], None] | None = None,
                 on_exit: Callable[[], None] | None = None,
                 can_restart: bool = True,
                 client_mode: bool = False,
                 exit_label: str = "返回主菜单") -> None:
        super().__init__()
        self.ranking = ranking
        self.rounds = rounds
        self.stats = stats
        self.client_mode = client_mode
        self.exit_label = "返回大厅" if client_mode else exit_label
        self.rect = pygame.Rect(0, 0, 1320, 780)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = []
        if client_mode:
            # 联机结算：只有房主能重新开始，客户端显示等待
            if on_exit is not None:
                self.buttons.append(Button(
                    pygame.Rect(self.rect.centerx - 150, self.rect.bottom - 76, 300, 54),
                    "返回大厅", on_click=on_exit, style="accent", icon="network"))
        elif can_restart and on_again is not None:
            self.buttons.append(Button(
                pygame.Rect(self.rect.centerx - 300, self.rect.bottom - 76, 280, 54),
                "再来一局", on_click=on_again, style="accent", icon="replay"))
            self.buttons.append(Button(
                pygame.Rect(self.rect.centerx + 20, self.rect.bottom - 76, 280, 54),
                self.exit_label, on_click=on_exit, style="secondary", icon="exit"))
        elif on_exit is not None:
            self.buttons.append(Button(
                pygame.Rect(self.rect.centerx - 140, self.rect.bottom - 74, 280, 54),
                self.exit_label, on_click=on_exit, style="accent", icon="exit"))

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        self._draw_banner(surface, fonts, rect)
        self._draw_ranking(surface, fonts, pygame.Rect(rect.x + 28, rect.y + 116, 720, 300))
        self._draw_titles(surface, fonts, pygame.Rect(rect.x + 772, rect.y + 116, 520, 300))
        self._draw_stats_table(surface, fonts,
                               pygame.Rect(rect.x + 28, rect.y + 436, 1264, 262))
        for button in self.buttons:
            button.draw(surface, fonts)
        if self.client_mode:
            theme.draw_text(surface, "由房主决定是否再来一局", fonts.small(),
                            theme.color("text_mute"),
                            (rect.centerx, self.buttons[0].rect.y - 22), anchor="midbottom")

    def _draw_banner(self, surface: pygame.Surface, fonts: theme.FontManager,
                     rect: pygame.Rect) -> None:
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 12),
                           theme.color("accent"), radius=6)
        winner = self.ranking[0] if self.ranking else None
        title = f"{winner['name']} 获胜！" if winner else "游戏结束"
        banner = pygame.Rect(rect.x, rect.y, rect.width, 104)
        theme.rounded_rect(surface, banner, theme.color("panel"), radius=18)
        pygame.draw.rect(surface, theme.color("panel"),
                         pygame.Rect(banner.x, banner.bottom - 18, banner.width, 18))

        icon_box = pygame.Rect(rect.x + 34, rect.y + 30, 46, 46)
        icons.draw_icon(surface, "crown", icon_box, theme.color("accent"),
                        theme.color("shadow"))
        theme.draw_text(surface, title, fonts.sized(38, True), theme.color("accent"),
                        (icon_box.right + 18, rect.y + 26))

        secs = int(self.stats.get("duration_sec", 0) or 0)
        duration = f"{secs // 60} 分 {secs % 60} 秒" if secs else "—"
        bits = [
            f"共 {self.rounds} 轮",
            f"{self.stats.get('bankrupt_count', 0)} 人破产",
            f"用时 {duration}",
            f"全场收租 {self.stats.get('total_rent_income', 0):,}",
        ]
        theme.draw_text(surface, "　·　".join(bits), fonts.small(),
                        theme.color("text_dim"), (icon_box.right + 20, rect.y + 70))

        # 冠军关键数字
        if winner is not None:
            stats = [
                ("最终资金", f"{winner['money']:,}", "accent"),
                ("地产", f"{winner['property_count']} 处", "text"),
                ("地产总值", f"{winner['property_value']:,}", "text"),
                ("总资产", f"{winner['total_asset']:,}", "success"),
            ]
            x = rect.right - 34
            for label, value, color_name in reversed(stats):
                w = max(fonts.small().size(label)[0], fonts.h3().size(value)[0]) + 26
                label_font = fonts.tiny()
                theme.draw_text(surface, label, label_font, theme.color("text_mute"),
                                (x, rect.y + 30), anchor="topright")
                theme.draw_text(surface, value, fonts.h3(), theme.color(color_name),
                                (x, rect.y + 48), anchor="topright")
                x -= w
        pygame.draw.line(surface, theme.color("border_soft"),
                         (rect.x + 24, banner.bottom - 2), (rect.right - 24, banner.bottom - 2), 1)

    def _draw_ranking(self, surface: pygame.Surface, fonts: theme.FontManager,
                      rect: pygame.Rect) -> None:
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "最终排名", icon="trophy")
        cols = [0, 54, 300, 420, 560]
        headers = ["名次", "玩家", "最终资金", "地产", "总资产"]
        head_y = rect.y + 48
        for i, text in enumerate(headers):
            theme.draw_text(surface, text, fonts.tiny(), theme.color("text_mute"),
                            (rect.x + 20 + cols[i], head_y))
        pygame.draw.line(surface, theme.color("border"),
                         (rect.x + 16, head_y + 22), (rect.right - 16, head_y + 22), 1)

        from .player_panel import _color_of

        y = head_y + 32
        for row in self.ranking[:6]:
            highlight = row.get("winner")
            row_rect = pygame.Rect(rect.x + 12, y - 5, rect.width - 24, 38)
            if highlight:
                theme.rounded_rect(surface, row_rect, theme.color("accent", 40),
                                   radius=8)
            color = theme.hex_to_rgb(_color_of(row.get("color_id", "")))
            pygame.draw.circle(surface, color, (rect.x + 30, y + 12), 9)
            if highlight:
                icons.draw_icon(surface, "crown",
                                pygame.Rect(rect.x + 44, y + 2, 18, 18),
                                theme.color("accent"), theme.color("shadow"))

            name = row["name"]
            if row["bankrupt"]:
                name += "（已破产）"
            theme.draw_text(surface, str(row["rank"]), fonts.small(),
                            theme.color("accent" if highlight else "text_dim"),
                            (rect.x + 20 + cols[0], y + 2))
            theme.draw_text(surface, theme.truncate(name, fonts.small(), 230),
                            fonts.small(),
                            theme.color("text" if not row["bankrupt"] else "text_mute"),
                            (rect.x + 20 + cols[1], y + 2))
            theme.draw_text(surface, f"{row['money']:,}", fonts.small(),
                            theme.color("text_mute" if row["bankrupt"] else "text"),
                            (rect.x + 20 + cols[2], y + 2), anchor="topright")
            theme.draw_text(surface, f"{row['property_count']}", fonts.small(),
                            theme.color("text_dim"),
                            (rect.x + 20 + cols[3], y + 2), anchor="topright")
            theme.draw_text(surface, f"{row['total_asset']:,}", fonts.small(),
                            theme.color("accent" if highlight else "text"),
                            (rect.right - 36, y + 2), anchor="topright")
            y += 42

        flow = self.stats.get("flow") or {}
        inc = "、".join(f"{k} {v:,}" for k, v in list(flow.get("income", {}).items())[:3])
        exp = "、".join(f"{k} {v:,}" for k, v in list(flow.get("expense", {}).items())[:3])
        if inc:
            theme.draw_text(surface, f"全场收入主要来自：{inc}", fonts.tiny(),
                            theme.color("text_mute"), (rect.x + 20, rect.bottom - 42))
            theme.draw_text(surface, f"全场支出主要在于：{exp}", fonts.tiny(),
                            theme.color("text_mute"), (rect.x + 20, rect.bottom - 24))

    def _draw_titles(self, surface: pygame.Surface, fonts: theme.FontManager,
                     rect: pygame.Rect) -> None:
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "趣味称号", icon="star", note="只影响展示")

        from .player_panel import _color_of

        by_title: dict[str, dict] = {}
        for row in self.ranking:
            for title in row.get("titles") or []:
                by_title.setdefault(title, row)

        y = rect.y + 50
        for title in ("地产大亨", "最佳房东", "卡牌大师", "运势之王",
                      "散财童子", "守财奴", "倒霉蛋"):
            row = by_title.get(title)
            chip_rect = pygame.Rect(rect.x + 20, y, 148, 30)
            theme.rounded_rect(surface, chip_rect,
                               theme.color("accent" if row else "bg_alt", 60 if row else 120),
                               radius=8)
            theme.rounded_rect(surface, chip_rect, None, radius=8,
                               border=theme.color("accent" if row else "border_soft"),
                               border_width=1)
            theme.draw_text(surface, title, fonts.small(),
                            theme.color("text" if row else "text_mute"), chip_rect.center,
                            anchor="center")
            if row is not None:
                color = theme.hex_to_rgb(_color_of(row.get("color_id", "")))
                pygame.draw.circle(surface, color, (chip_rect.right + 18, chip_rect.centery), 8)
                theme.draw_text(surface, theme.truncate(row["name"], fonts.small(), 150),
                                fonts.small(), theme.color("text"),
                                (chip_rect.right + 32, chip_rect.centery), anchor="midleft")
            else:
                theme.draw_text(surface, "无人获得", fonts.tiny(), theme.color("text_mute"),
                                (chip_rect.right + 32, chip_rect.centery), anchor="midleft")
            y += 34

        pool = self.stats.get("bonus_pool", 0)
        theme.draw_text(surface, f"结束时奖金池余额 {pool:,}", fonts.tiny(),
                        theme.color("text_mute"), (rect.x + 20, rect.bottom - 24))

    def _draw_stats_table(self, surface: pygame.Surface, fonts: theme.FontManager,
                          rect: pygame.Rect) -> None:
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 32, 22),
                             "玩家统计", icon="scales", note="数据来自经济账本")

        col_x = [0, 120]
        step = (rect.width - 150) // len(self.STAT_COLUMNS)
        for i, (label, _) in enumerate(self.STAT_COLUMNS):
            col_x.append(140 + i * step)
        head_y = rect.y + 46
        for i, (label, _) in enumerate(self.STAT_COLUMNS):
            theme.draw_text(surface, label, fonts.tiny(), theme.color("text_mute"),
                            (rect.x + 16 + col_x[i + 2], head_y), anchor="topright")
        pygame.draw.line(surface, theme.color("border"),
                         (rect.x + 16, head_y + 20), (rect.right - 16, head_y + 20), 1)

        from .player_panel import _color_of

        y = head_y + 30
        for row in self.ranking[:6]:
            color = theme.hex_to_rgb(_color_of(row.get("color_id", "")))
            pygame.draw.circle(surface, color, (rect.x + 30, y + 9), 8)
            theme.draw_text(surface, theme.truncate(row["name"], fonts.small(), 120),
                            fonts.small(),
                            theme.color("text_mute" if row["bankrupt"] else "text"),
                            (rect.x + 46, y + 1))
            for i, (_, getter) in enumerate(self.STAT_COLUMNS):
                try:
                    value = getter(row)
                except Exception:
                    value = 0
                color_name = "text_dim"
                if i in (0, 2):
                    color_name = "success"
                elif i in (1, 3, 8):
                    color_name = "warning"
                elif i == len(self.STAT_COLUMNS) - 1:
                    color_name = "accent"
                theme.draw_text(surface, f"{value:,}", fonts.small(),
                                theme.color(color_name),
                                (rect.x + 16 + col_x[i + 2], y + 1), anchor="topright")
            y += 30

        bought = self.stats.get("total_bought", 0)
        upgraded = self.stats.get("total_upgraded", 0)
        cards = self.stats.get("total_cards_used", 0)
        chance = self.stats.get("total_chance", 0)
        passes = self.stats.get("total_start_passes", 0)
        theme.draw_text(surface,
                        f"全场累计：购地 {bought} 次 · 升级 {upgraded} 次 · "
                        f"道具 {cards} 张 · 机遇 {chance} 次 · 经过起点 {passes} 次",
                        fonts.tiny(), theme.color("text_mute"),
                        (rect.centerx, rect.bottom - 22), anchor="midbottom")


# ==================================================================== 暂停

class PauseMenu(Modal):
    """ESC 暂停菜单。"""

    def __init__(self, on_resume: Callable[[], None], items: list[tuple[str, Callable[[], None], str]],
                 note: str = "") -> None:
        super().__init__()
        self.dismissable = True
        self.note = note
        self.rect = pygame.Rect(0, 0, 460, 150 + len(items) * 62)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = []
        y = self.rect.y + 96
        for text, callback, style in items:
            self.buttons.append(Button(pygame.Rect(self.rect.x + 40, y, self.rect.width - 80, 52),
                                       text, on_click=callback, style=style))
            y += 62
        self.on_resume = on_resume

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.close()
            self.on_resume()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        theme.draw_text(surface, "暂 停", fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 28), anchor="midtop")
        if self.note:
            theme.draw_text(surface, self.note, fonts.small(), theme.color("text_mute"),
                            (rect.centerx, rect.y + 68), anchor="midtop")
        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 道具目标

class CardTargetDialog(Modal):
    """选择道具使用目标。

    目标列表由规则层 `cards.valid_targets()` 给出，UI 不做任何过滤，
    因此「能选的一定是合法的」；不可选的目标根本不会出现在列表里。
    """

    def __init__(
        self,
        card_name: str,
        card_desc: str,
        targets: list[tuple[Any, str]],     # (target_id, label)
        on_pick: Callable[[Any], None],
        on_cancel: Callable[[], None] | None = None,
        columns: int = 2,
        icon: str = "card",
        timing: str = "",
    ) -> None:
        super().__init__()
        self.dismissable = True
        self.card_name = card_name
        self.card_desc = card_desc
        self.targets = targets
        self.on_pick = on_pick
        self.on_cancel = on_cancel
        self.icon = icon
        self.timing = timing

        rows = max(1, math.ceil(len(targets) / columns))
        visible_rows = min(rows, 7)
        height = 210 + visible_rows * 54 + 74
        self.rect = pygame.Rect(0, 0, 760, min(height, SCREEN_H - 80))
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self._build_buttons(columns)

    def _build_buttons(self, columns: int) -> None:
        self.buttons = []
        rect = self.rect
        per_col = max(1, math.ceil(len(self.targets) / columns))
        w = (rect.width - 60 - (columns - 1) * 12) // columns
        for i, (target_id, label) in enumerate(self.targets):
            c = i // per_col
            r = i % per_col
            x = rect.x + 30 + c * (w + 12)
            y = rect.y + 140 + r * 54
            if y + 46 > rect.bottom - 74:
                break
            self.buttons.append(Button(
                pygame.Rect(x, y, w, 46), label,
                on_click=lambda tid=target_id: self._pick(tid), style="secondary",
                font_size=16, tooltip=label))
        self.buttons.append(Button(
            pygame.Rect(rect.centerx - 100, rect.bottom - 64, 200, 46),
            "取消（ESC）", on_click=self._cancel, style="ghost", icon="cross"))

    def _pick(self, target_id: Any) -> None:
        self.close()
        self.on_pick(target_id)

    def _cancel(self) -> None:
        self.close()
        if self.on_cancel is not None:
            self.on_cancel()

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self._cancel()

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8),
                           theme.color("primary"), radius=4)
        icons.draw_icon(surface, self.icon, pygame.Rect(rect.x + 30, rect.y + 26, 30, 30),
                        theme.color("primary"), theme.color("shadow"))
        theme.draw_text(surface, f"使用「{self.card_name}」", fonts.h1(),
                        theme.color("text"), (rect.x + 72, rect.y + 22))
        if self.timing:
            chip = pygame.Rect(rect.right - 220, rect.y + 30, 190, 24)
            theme.chip(surface, fonts, chip, self.timing, "accent_soft", font_key="tiny",
                       radius=6)
        theme.draw_text(surface, self.card_desc, fonts.small(), theme.color("text_dim"),
                        (rect.x + 32, rect.y + 74))
        theme.draw_text(surface, "请选择目标（合法目标已在棋盘上高亮，也可直接点棋盘）",
                        fonts.small(), theme.color("text_mute"),
                        (rect.x + 32, rect.y + 104))
        for button in self.buttons:
            button.draw(surface, fonts)


class MessageDialog(Modal):
    """简单信息弹窗。"""

    def __init__(self, title: str, message: str, accent: str = "info",
                 on_close: Callable[[], None] | None = None,
                 button_text: str = "确定") -> None:
        super().__init__(on_close)
        self.dismissable = True
        self.title = title
        self.message = message
        self.accent = accent
        self.rect = pygame.Rect(0, 0, 620, 300)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [Button(
            pygame.Rect(self.rect.centerx - 130, self.rect.bottom - 76, 260, 50),
            button_text, on_click=self.close, style="primary")]

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color(self.accent)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8), accent, radius=4)
        theme.draw_text(surface, self.title, fonts.h1(), theme.color("text"),
                        (rect.centerx, rect.y + 34), anchor="midtop")
        theme.draw_wrapped(surface, self.message, fonts.body(), theme.color("text_dim"),
                           pygame.Rect(rect.x + 34, rect.y + 92, rect.width - 68, 130))
        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 断线重连

class ReconnectDialog(Modal):
    """掉线后的可见流程：倒计时 + 进度条 + 放弃按钮。

    内容与实际重连逻辑（AutoReconnector / GameHost 宽限期）保持一致，
    UI 不会承诺底层做不到的事。
    """

    def __init__(self, reason: str, grace_sec: float = 30.0,
                 on_give_up: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.dismissable = False
        self.reason = reason
        self.grace_sec = grace_sec
        self.rect = pygame.Rect(0, 0, 640, 340)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.status = "正在尝试重新连接…"
        self.attempts = 0
        self.remaining = grace_sec
        self.progress = 0.0
        self.buttons = [Button(
            pygame.Rect(self.rect.centerx - 130, self.rect.bottom - 74, 260, 50),
            "放弃并返回主菜单", on_click=self._give_up, style="ghost", font_size=16)]
        self.on_give_up = on_give_up
        self._gave_up = False

    def _give_up(self) -> None:
        self._gave_up = True
        if self.on_give_up is not None:
            self.on_give_up()
        self.close()

    @property
    def gave_up(self) -> bool:
        return self._gave_up

    def sync(self, status: str, attempts: int, remaining: float,
             progress: float, hint: str = "") -> None:
        self.status = status
        self.attempts = attempts
        self.remaining = remaining
        self.progress = progress
        self.hint = hint

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)
        accent = theme.color("warning")
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8), accent,
                           radius=4)
        icons.draw_icon(surface, "network", pygame.Rect(rect.x + 34, rect.y + 28, 34, 34),
                        accent, theme.color("shadow"))
        theme.draw_text(surface, "与房主的连接已中断", fonts.h1(), theme.color("text"),
                        (rect.x + 80, rect.y + 24))

        theme.draw_text(surface, self.reason, fonts.small(), theme.color("text_dim"),
                        (rect.x + 36, rect.y + 84))
        theme.draw_text(surface, self.status, fonts.h3(), theme.color("accent"),
                        (rect.x + 36, rect.y + 116))

        bar = pygame.Rect(rect.x + 36, rect.y + 156, rect.width - 72, 12)
        theme.progress_bar(surface, bar, 1.0 - self.progress, color_name="warning")
        theme.draw_text(surface, f"剩余 {int(self.remaining)} 秒", fonts.small(),
                        theme.color("text"), (rect.x + 36, rect.y + 176))
        theme.draw_text(surface, f"已尝试 {self.attempts} 次", fonts.small(),
                        theme.color("text_mute"), (rect.right - 36, rect.y + 176),
                        anchor="topright")

        note = getattr(self, "hint", "") or (
            f"房主会为你保留座位 {int(self.grace_sec)} 秒；"
            "超时后由 AI 接管，本局不会中断，之后仍然可以重连回来。")
        theme.draw_wrapped(surface, note, fonts.small(), theme.color("text_mute"),
                           pygame.Rect(rect.x + 36, rect.y + 204, rect.width - 72, 48))
        for button in self.buttons:
            button.draw(surface, fonts)


# ==================================================================== 商店

class ShopDialog(Modal):
    """商店：以卡片形式展示可购买的道具。"""

    def __init__(
        self,
        tile_name: str,
        offers: list[dict],
        player_money: int,
        on_buy: Callable[[str], None],
        on_leave: Callable[[], None],
        inventory: int = 0,
        inventory_limit: int = 5,
        price_of: Callable[[str], int] | None = None,
    ) -> None:
        super().__init__()
        self.dismissable = False
        self.tile_name = tile_name
        self.offers = offers
        self.player_money = player_money
        self.on_buy = on_buy
        self.on_leave = on_leave
        self.inventory = inventory
        self.inventory_limit = inventory_limit
        self.price_of = price_of or (lambda cid: 0)

        n = max(1, len(offers))
        width = min(1180, 240 + n * 300)
        self.rect = pygame.Rect(0, 0, width, 470)
        self.rect.center = (SCREEN_W // 2, SCREEN_H // 2)
        self.buttons = [Button(
            pygame.Rect(self.rect.centerx - 130, self.rect.bottom - 74, 260, 52),
            "不买了", on_click=self._leave, style="ghost")]
        self._card_rects: list[pygame.Rect] = []
        self._layout()

    def _layout(self) -> None:
        n = max(1, len(self.offers))
        gap = 18
        w = (self.rect.width - 60 - gap * (n - 1)) // n
        h = 230
        self._card_rects = []
        for i in range(n):
            self._card_rects.append(pygame.Rect(
                self.rect.x + 30 + i * (w + gap), self.rect.y + 116, w, h))

    def _leave(self) -> None:
        self.close()
        self.on_leave()

    def on_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self._leave()
        elif pygame.K_1 <= key <= pygame.K_9:
            idx = key - pygame.K_1
            if idx < len(self.offers) and self._can_buy(self.offers[idx]):
                self._buy(self.offers[idx]["id"])

    def _can_buy(self, offer: dict) -> bool:
        if self.inventory >= self.inventory_limit:
            return False
        return self.player_money >= self.price_of(offer["id"])

    def _buy(self, card_id: str) -> None:
        self.close()
        self.on_buy(card_id)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, offer in zip(self._card_rects, self.offers):
                if rect.collidepoint(event.pos):
                    if self._can_buy(offer):
                        self._buy(offer["id"])
                    return True
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            self.on_key(event.key)
        return True

    def draw(self, surface: pygame.Surface, fonts: theme.FontManager) -> None:
        self._draw_scrim(surface)
        rect = self._panel(surface, self.rect)

        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 8),
                           theme.color("accent"), radius=4)
        theme.draw_text(surface, f"商 店 · {self.tile_name}", fonts.h1(),
                        theme.color("text"), (rect.centerx, rect.y + 26), anchor="midtop")
        theme.draw_text(surface,
                        f"你的现金 {self.player_money:,}   道具 {self.inventory}/{self.inventory_limit}"
                        f"    （每个商店限购一张）",
                        fonts.small(), theme.color("text_dim"),
                        (rect.centerx, rect.y + 72), anchor="midtop")

        mouse = pygame.mouse.get_pos()
        for card_rect, offer in zip(self._card_rects, self.offers):
            self._draw_offer(surface, fonts, card_rect, offer, mouse)

        for button in self.buttons:
            button.draw(surface, fonts)

    def _draw_offer(self, surface: pygame.Surface, fonts: theme.FontManager,
                    rect: pygame.Rect, offer: dict, mouse: tuple[int, int]) -> None:
        can = self._can_buy(offer)
        hovered = rect.collidepoint(mouse) and can
        rarity_color = {"common": "info", "uncommon": "accent", "rare": "danger"}.get(
            offer.get("rarity", "common"), "info")
        accent = theme.color(rarity_color)

        theme.rounded_rect(surface, rect,
                           theme.color("panel_hi") if hovered else theme.color("panel"),
                           radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=accent if can else theme.color("border_soft"),
                           border_width=2 if hovered else 1)
        theme.rounded_rect(surface, pygame.Rect(rect.x, rect.y, rect.width, 6),
                           accent, radius=3)

        icons.draw_icon(surface, offer.get("icon", "card"),
                        pygame.Rect(rect.x + 16, rect.y + 20, 30, 30), accent,
                        theme.color("shadow"))
        theme.draw_text(surface, offer["name"], fonts.h3(), theme.color("text"),
                        (rect.x + 54, rect.y + 22))
        theme.draw_wrapped(surface, offer.get("description", ""), fonts.small(),
                           theme.color("text_dim"),
                           pygame.Rect(rect.x + 16, rect.y + 62, rect.width - 32, 96))

        timing = offer.get("timing_label", "")
        if timing:
            theme.chip(surface, fonts, pygame.Rect(rect.x + 16, rect.bottom - 78, 150, 20),
                       timing, "accent_soft", font_key="micro", radius=5)

        price = self.price_of(offer["id"])
        price_color = "accent" if can else "danger"
        theme.draw_text(surface, f"{price:,}", fonts.h2(), theme.color(price_color),
                        (rect.x + 16, rect.bottom - 48))

        if not can:
            reason = "道具已满" if self.inventory >= self.inventory_limit else "现金不足"
            theme.draw_text(surface, reason, fonts.small(), theme.color("danger"),
                            (rect.right - 16, rect.bottom - 42), anchor="topright")
        else:
            theme.draw_text(surface, "点击购买", fonts.small(),
                            theme.color("accent" if hovered else "text_mute"),
                            (rect.right - 16, rect.bottom - 42), anchor="topright")
