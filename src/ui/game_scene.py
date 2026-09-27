"""对局场景：棋盘、HUD、决策交互、动画、演出。

设计要点：
- 通过 SessionView 抽象统一「单机引擎」与「局域网客户端」，
  场景本身不关心状态是本地算出来的还是网络传来的；
- UI 只发送 Command，绝不直接修改状态；
- 弹窗出现时吞掉所有输入，杜绝穿透点击；
- **演出不参与规则**：资金浮字读 EconomyLedger，事件卡读 event_log，
  引擎照常按自己的阶段推进，演出只决定「画什么、画多久」；
- 所有演出都能点击跳过，不会把玩家卡住。
"""
from __future__ import annotations

import math
import time
from typing import Any

import pygame

from ..audio.manager import audio
from ..game import economy
from ..game.cards import TIMING_LABEL, CardRegistry, can_use, valid_targets
from ..game.commands import Command, CommandType, DecisionKind, PendingDecision
from ..game.events import EventType
from ..game.format import money
from ..game.ledger import Reason
from ..game.phases import GamePhase
from ..game.state import GameState
from ..game.tile import TileType
from ..game import victory
from ..persistence import savegame
from . import icons, theme
from .animations import (
    AnimationManager,
    CardFlipAnimation,
    DiceRollAnimation,
    FloatingText,
    PieceMoveAnimation,
    draw_die,
)
from .asset_panel import AssetPanel
from .board_view import BoardView, district_progress, monopoly_districts
from .dialogs import (
    CardTargetDialog,
    ShopDialog,
    ChanceCardDialog,
    DecisionDialog,
    GameOverDialog,
    MessageDialog,
    Modal,
    PauseMenu,
)
from .player_panel import PlayerPanel, _color_of
from .presentation import (
    PRIORITY_AMBIENT,
    PRIORITY_CRITICAL,
    PRIORITY_NORMAL,
    ActionBanner,
    BankruptcyBanner,
    EventCard,
    Presenter,
    accent_for,
    describe_ledger_entry,
)
from .property_panel import PropertyInfoPanel, property_tooltip
from .scene import Scene
from .toast import ToastManager
from .widgets import Button, IconButton, Label, Panel, ScrollList, draw_tooltip

SCREEN_W = 1600
SCREEN_H = 900

#: 各区域的布局（棋盘网格由 BoardView 按地图数据自适应居中）
BOARD_RECT = pygame.Rect(24, 88, 980, 782)
SIDE_RECT = pygame.Rect(1024, 88, 552, 782)
PANEL_RECT = pygame.Rect(20, 12, 1348, 68)
ROUND_RECT = pygame.Rect(1382, 12, 198, 68)

#: 侧栏三段的高度
ACTION_H = 286
INFO_H = 232
LOG_GAP = 12

#: 侧栏内控件的固定位置（绘制与命中判定共用，保证不会错位）
ROLL_BUTTON_RECT = pygame.Rect(SIDE_RECT.x + 16, SIDE_RECT.y + 84, 200, 58)
DICE_PREVIEW_RECT = pygame.Rect(SIDE_RECT.x + 228, SIDE_RECT.y + 84, 156, 58)
CARD_W, CARD_H, CARD_GAP = 84, 54, 6
CARD_ORIGIN = (SIDE_RECT.x + 14, SIDE_RECT.y + 214)


def card_button_rect(index: int) -> pygame.Rect:
    return pygame.Rect(CARD_ORIGIN[0] + index * (CARD_W + CARD_GAP),
                       CARD_ORIGIN[1], CARD_W, CARD_H)


class LocalInteraction:
    """本机玩家此刻到底能做什么。

    全项目只有这一个地方判断「现在该谁操作、能做什么」，
    按钮 / 快捷键 / 弹窗 / 棋盘点击全部读它，避免每个控件自己判断 current_player。
    """

    NONE = "none"
    ROLL_DICE = "roll_dice"
    PROPERTY_DECISION = "property_decision"
    JAIL_DECISION = "jail_decision"
    CARD_TARGET = "card_target"
    DEBT = "debt"
    SHOP = "shop"
    CONFIRM = "confirm"
    GAME_OVER = "game_over"
    WAITING = "waiting"

    LABELS = {
        NONE: "无操作",
        ROLL_DICE: "请掷骰子",
        PROPERTY_DECISION: "请处理地块",
        JAIL_DECISION: "请决定看守所行动",
        CARD_TARGET: "请选择道具目标",
        DEBT: "请处理债务",
        SHOP: "商店选购中",
        CONFIRM: "请确认",
        GAME_OVER: "对局结束",
        WAITING: "等待其他玩家",
    }

    #: 每种状态给玩家的下一步指引（侧栏提示行用它）
    HINTS = {
        ROLL_DICE: "点右侧「掷骰子」或按空格开始行动",
        PROPERTY_DECISION: "在弹出的窗口里选择买下或放弃",
        JAIL_DECISION: "选择付保释金或掷骰碰运气",
        CARD_TARGET: "在棋盘或弹窗中选择道具的目标",
        DEBT: "在资产面板里抵押或出售地产来凑钱",
        SHOP: "点击卡片购买，或点「不买了」离开",
        CONFIRM: "查看事件结果后继续",
        WAITING: "等待其他玩家行动，可以先看看棋盘",
        GAME_OVER: "对局已结束",
        NONE: "",
    }


class SessionView:
    """把「单机引擎」与「局域网客户端」统一成同一个只读视图。"""

    def __init__(self, app: Any, engine: Any = None, client: Any = None,
                 my_player_id: str = "", host: Any = None,
                 tutorial: Any = None, guide: Any = None) -> None:
        self.app = app
        self.engine = engine
        self.client = client
        self.host = host
        self.my_player_id = my_player_id
        self.tutorial = tutorial
        self.guide = guide
        self.local_players: set[str] = set()
        if engine is not None:
            # 单机：所有非 AI 座位都由本机真人操作（可同一台电脑轮流玩）
            self.local_players = {p.id for p in engine.state.players if not p.is_ai}

    # ---- 状态
    @property
    def state(self) -> GameState | None:
        if self.engine is not None:
            return self.engine.state
        if self.client is not None:
            return self.client.state
        return None

    @property
    def is_local_host(self) -> bool:
        return self.engine is not None and self.host is None

    def is_mine(self, player_id: str | None) -> bool:
        if player_id is None:
            return False
        if self.engine is not None:
            return player_id in self.local_players
        return player_id == self.my_player_id

    @property
    def decision(self) -> PendingDecision | None:
        st = self.state
        if st is None:
            return None
        pd = st.pending_decision
        if pd is not None and self.is_mine(pd.player_id):
            return pd
        return None

    @property
    def watching_decision(self) -> PendingDecision | None:
        st = self.state
        return st.pending_decision if st is not None else None

    def submit(self, cmd_type: str, payload: dict[str, Any] | None = None,
               decision_id: str | None = None) -> bool:
        """发送玩家意图。返回是否已发出。"""
        if self.engine is not None:
            player_id = self.my_player_id
            pd = self.engine.state.pending_decision
            if pd is not None and self.is_mine(pd.player_id):
                player_id = pd.player_id
            ctrl = self.engine.controller_of(player_id)
            if ctrl is None:
                return False
            cmd = Command(ctype=cmd_type, player_id=player_id, payload=payload or {},
                          decision_id=decision_id)
            ctrl.submit(cmd)
            return True
        if self.client is not None:
            return self.client.send_command(cmd_type, payload, decision_id) is not None
        return False

    def update(self, dt: float) -> None:
        # 单机时由这里驱动引擎；LAN 模式下 Host/Client 由 App 统一驱动
        if self.engine is not None and self.host is None:
            self.engine.update(dt)

    def drain_messages(self) -> list[str]:
        if self.client is not None:
            return self.client.drain_messages()
        return []

    @property
    def player_id(self) -> str:
        """当前该我操作的玩家 id（用于提示）。"""
        pd = self.decision
        if pd is not None:
            return pd.player_id
        return self.my_player_id


class CardTile:
    """侧栏里的一张手牌：图标 + 名称 + 时机标记 + 不可用原因。"""

    __slots__ = ("rect", "card_id", "name", "icon", "enabled", "reason", "timing",
                 "description", "hovered")

    def __init__(self, rect: pygame.Rect, card_id: str, name: str, icon: str,
                 timing: str, description: str, enabled: bool, reason: str) -> None:
        self.rect = rect
        self.card_id = card_id
        self.name = name
        self.icon = icon
        self.timing = timing
        self.description = description
        self.enabled = enabled
        self.reason = reason
        self.hovered = False

    def tooltip(self, index: int) -> str:
        lines = [f"{self.name}", self.description, self.timing]
        if self.enabled:
            lines.append(f"点击或按 {index + 1} 使用")
        else:
            lines.append(f"暂不可用：{self.reason}")
        return "\n".join(x for x in lines if x)


class GameScene(Scene):
    """对局主场景。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.session: SessionView | None = None
        self.board_view: BoardView | None = None
        self.player_panel = PlayerPanel(PANEL_RECT)
        self.prop_panel = PropertyInfoPanel(
            pygame.Rect(SIDE_RECT.x, SIDE_RECT.y + ACTION_H + LOG_GAP,
                        SIDE_RECT.width, INFO_H))
        self.log_panel_rect = pygame.Rect(
            SIDE_RECT.x,
            SIDE_RECT.y + ACTION_H + INFO_H + LOG_GAP * 2,
            SIDE_RECT.width,
            SIDE_RECT.height - ACTION_H - INFO_H - LOG_GAP * 2)
        self.log_list = ScrollList(
            pygame.Rect(self.log_panel_rect.x + 8, self.log_panel_rect.y + 48,
                        self.log_panel_rect.width - 16, self.log_panel_rect.height - 56),
            line_height=21)
        self.anim = AnimationManager()
        self.presenter = Presenter()
        self.toasts = ToastManager()
        self.modal: Modal | None = None
        self.buttons: list[Button] = []
        self.card_tiles: list[CardTile] = []
        self.roll_button: Button | None = None
        self._card_tooltip: str = ""
        self.tile_index_clicked: int | None = None
        self.hover_tile: int | None = None
        self.hover_player: int | None = None
        self._last_seen_seq = 0
        self._last_ledger_seq = 0
        self._last_phase = None
        self._last_current = None
        self._move_anim: PieceMoveAnimation | None = None
        self._move_player: str | None = None
        self._move_trail: list[tuple[int, float]] = []
        self._move_last_tile: int | None = None
        self._dice_anim: DiceRollAnimation | None = None
        self._card_defs: CardRegistry | None = None
        self._char_names: dict[str, str] = {}
        self._paused = False
        self._autosave_turn = -999
        self._autosave_time = 0.0
        self._game_over_shown = False
        self._pending_card_id: str | None = None
        self._targets: dict[Any, str] = {}
        self._pending_debt_reopen = False
        self._card_tiles_key: tuple = ()
        self._last_state_error = ""
        self._log_cache: list[Any] = []
        self._log_seq = -1
        self._card_reasons: dict[str, str] = {}
        self._last_hint = ""
        self._money_anim: dict[str, float] = {}
        self._tutorial = None
        #: 破产棋子的淡出进度：player_id -> 已经过的秒数（纯演出）
        self._fading: dict[str, float] = {}
        #: 资金音效节流
        self._last_money_sfx_at = 0.0

    # ================================================================ 场景

    def bind(self, session: SessionView) -> None:
        self.session = session
        st = session.state
        if st is None:
            return
        if self.board_view is None or self.board_view.board is not st.board:
            self.board_view = BoardView(st.board, BOARD_RECT)
        self.board_view.bind(self.app.fonts, None)
        from ..game.setup import load_card_registry, load_characters

        self._card_defs = load_card_registry()
        self._char_names = {c["id"]: c["name_cn"] for c in load_characters()["characters"]}
        self.player_panel.local_player_id = session.my_player_id if not session.engine else ""
        self._last_seen_seq = max(0, st.event_seq - 40)
        # 已经存在的流水不再补演（读档 / 刚连上时不应该刷一屏浮字）
        self._last_ledger_seq = (st.ledger.entries[-1].seq if st.ledger.entries else 0)
        self._game_over_shown = False
        self._log_seq = -1
        self.anim.clear()
        self.presenter.clear()
        self.modal = None
        self._paused = False
        self._pending_card_id = None
        self._targets = {}
        self._fading = {}
        self._tutorial = session.tutorial
        self._guide = session.guide
        self._build_buttons()
        if self._tutorial is not None:
            self._tutorial.bind(self)
        if self._guide is not None:
            self._guide.bind(self)

    def _build_buttons(self) -> None:
        self.buttons = [
            Button(pygame.Rect(1322, 22, 84, 44), "资产", on_click=self.open_asset_panel,
                   style="secondary", font_size=16, icon="property",
                   tooltip="管理地产：升级 / 出售 / 抵押 / 赎回（I）"),
            Button(pygame.Rect(1412, 22, 84, 44), "图鉴", on_click=self._open_help,
                   style="secondary", font_size=16, icon="book",
                   tooltip="规则与道具图鉴（H）"),
            Button(pygame.Rect(1502, 22, 80, 44), "菜单", on_click=self._open_pause,
                   style="secondary", font_size=16, icon="gear",
                   tooltip="暂停 / 菜单（ESC）"),
        ]
        self.roll_button = Button(
            ROLL_BUTTON_RECT, "掷骰子", on_click=self._try_roll, style="accent",
            enabled=False, tooltip="空格键也可以掷骰")
        self.buttons.append(self.roll_button)
        # 「本局记录」的整块标题区可点：位置每帧由 _draw_log 对齐。
        # 不进 buttons 列表 —— 它是一个覆盖在面板标题上的命中区域，不该被绘制。
        self.log_button = Button(
            pygame.Rect(self.log_panel_rect.x + 10, self.log_panel_rect.y + 8,
                        self.log_panel_rect.width - 20, 32),
            "", on_click=self.open_player_log, style="ghost")
        self._refresh_action_buttons()

    def open_player_log(self) -> None:
        """打开本局记录（可筛选 / 按轮分组）。"""
        st = self.session.state if self.session else None
        if st is None:
            return
        from .player_log import PlayerLogDialog

        my_id = self.session.player_id if self.session else ""
        self.modal = PlayerLogDialog(st, my_id, on_close=self._on_player_log_closed)

    def _on_player_log_closed(self) -> None:
        self.modal = None

    def on_enter(self, **kwargs: Any) -> None:
        audio.play_bgm("game")

    # ================================================================ 每帧

    def update(self, dt: float) -> None:
        if self.session is None:
            return
        self.session.update(dt)
        st = self.session.state
        if st is None:
            return

        for msg in self.session.drain_messages():
            self.toasts.push(msg, "info")

        self.anim.set_speed(self.app.settings.animation_speed)
        self._sync_animations(st, dt)
        self.anim.update(dt)
        self.presenter.update(dt)
        self.toasts.update(dt)
        super().update(dt)

        if self._tutorial is not None:
            self._tutorial.update(st, self)
        if self._guide is not None:
            self._guide.update(dt)

        if self.modal is not None:
            self.modal.update(dt)
            # 债务一旦结清，债务面板要自己关掉。
            # 否则玩家会在「已经还完钱」的面板里继续抵押，白白毁掉自己的资产。
            if st.debt is None and getattr(self.modal, "debt", None) is not None:
                self._pending_debt_reopen = False
                self.modal.close()
                self.modal = None
                self.toasts.push("债务已结清", "success")
                audio.play_sfx("coin")
            if self.modal is not None and self.modal.done:
                self.modal = None
                if getattr(self, '_pending_debt_reopen', False):
                    self._pending_debt_reopen = False
                    self._maybe_open_decision(st)
            self._update_money_animation(dt)
            return

        self._refresh_action_buttons()
        self._handle_new_events(st)
        self._handle_ledger(st)
        self._maybe_open_decision(st)
        self._maybe_show_game_over(st)
        self._maybe_autosave(st)

        mouse = pygame.mouse.get_pos()
        self._update_hover(st, mouse)

        if self._paused:
            return
        self._update_log(st)

    def _update_money_animation(self, dt: float) -> None:
        for pid in list(self._money_anim):
            value = self._money_anim[pid]
            step = abs(value) * min(1.0, dt * 6.0) + 1
            if value > 0:
                self._money_anim[pid] = max(0.0, value - step)
            else:
                self._money_anim[pid] = min(0.0, value + step)
            if abs(self._money_anim[pid]) < 1:
                del self._money_anim[pid]

    # ---------------------------------------------------------------- 日志

    #: 事件类型 → (颜色, 图标)
    LOG_STYLE = {
        EventType.PROPERTY_BOUGHT: ("success", "property"),
        EventType.RENT_PAID: ("warning", "coin"),
        EventType.PASSED_START: ("accent", "start"),
        EventType.CHANCE_DRAWN: ("info", "star"),
        EventType.CHANCE_APPLIED: ("text_dim", ""),
        EventType.BANKRUPT: ("danger", "alert"),
        EventType.GAME_OVER: ("accent", "trophy"),
        EventType.CARD_USED: ("primary", "card"),
        EventType.CARD_GAINED: ("text_dim", "card"),
        EventType.TURN_START: ("text", ""),
        EventType.JAIL_ENTERED: ("warning", "jail"),
        EventType.JAIL_RELEASED: ("success", "key"),
        EventType.JAIL_PAID: ("warning", "key"),
        EventType.DEBT_STARTED: ("danger", "alert"),
        EventType.BONUS_POOL_GAINED: ("success", "trophy"),
        EventType.BONUS_POOL_PAID: ("warning", "trophy"),
        EventType.TAX_PAID: ("danger", "tax"),
        EventType.PROPERTY_UPGRADED: ("accent", "hammer"),
        EventType.PROPERTY_SOLD: ("warning", "cash"),
        EventType.PROPERTY_MORTGAGED: ("warning", "tag"),
        EventType.PROPERTY_UNMORTGAGED: ("success", "key"),
        EventType.ASSET_LIQUIDATED: ("warning", "cash"),
        EventType.PLAYER_DISCONNECTED: ("danger", "network"),
        EventType.PLAYER_RECONNECTED: ("success", "network"),
        EventType.PLAYER_BOT_TAKEOVER: ("warning", "person"),
    }

    #: 关闭「详细日志」时隐藏的低信息量事件
    LOG_MINOR = frozenset({
        EventType.CHANCE_APPLIED, EventType.PHASE_CHANGED, EventType.TURN_END,
        EventType.PLAYER_MOVED,
    })

    def _update_log(self, st: GameState) -> None:
        if st.event_seq == self._log_seq:
            return
        self._log_seq = st.event_seq
        detailed = self.app.settings.detailed_log
        items = []
        for ev in st.event_log[-160:]:
            if not detailed and ev.type in self.LOG_MINOR:
                continue
            color, icon_name = self.LOG_STYLE.get(ev.type, ("text_dim", ""))
            prefix = "▍" if ev.type == EventType.TURN_START else ""
            if ev.type == EventType.TURN_START:
                color = "accent"
            items.append((f"{prefix}{ev.message}", color, icon_name))
        self.log_list.set_items(items)
        self.log_list.scroll = self.log_list.max_scroll

    # ---------------------------------------------------------------- 演出同步

    def _sync_animations(self, st: GameState, dt: float) -> None:
        """根据阶段变化启动/结束表现动画。"""
        if st.phase is GamePhase.ROLLING and st.dice is not None:
            if self._dice_anim is None and self._last_phase is not GamePhase.ROLLING:
                center = self.board_view.inner_rect.center if self.board_view else (600, 400)
                self._dice_anim = DiceRollAnimation(
                    (center[0], center[1] - 10), 78, (st.dice.die1, st.dice.die2),
                    duration=max(0.55, st.phase_duration * 0.82),
                    show_total=not st.dice.from_jail,
                )
                self.dice_settle_at = time.time() + max(0.55, st.phase_duration * 0.82)
                self.anim.add(self._dice_anim)
                audio.play_sfx("dice")
        if st.phase is not GamePhase.ROLLING:
            self._dice_anim = None

        if st.phase is GamePhase.MOVING and self.board_view is not None:
            if self._move_anim is None or self._move_player != st.move_player_id:
                length = max(1, len(st.move_path))
                # 每格时间由引擎阶段时长决定；上面留 12% 作为「落定前的停顿」
                per_tile = max(0.09, st.phase_duration * 0.88 / length)
                self._move_anim = PieceMoveAnimation(
                    list(st.move_path), self.board_view.tile_centers(), per_tile)
                if self._move_player != st.move_player_id:
                    self._move_anim.tween.elapsed = 0.0
                    self._move_anim.tween.done = False
                    self.anim.add(self._move_anim)
                    self._move_trail.clear()
                    self._move_last_tile = None
                self._move_player = st.move_player_id
                audio.play_sfx("move")
        else:
            if self._move_anim is not None and st.phase is not GamePhase.MOVING:
                self._move_anim = None
                self._move_player = None
                self._move_last_tile = None

        # 逐格反馈：每经过一格点亮一次
        if st.phase is GamePhase.MOVING and self._move_anim is not None:
            idx = self._move_anim.current_index()
            if idx is not None and idx != self._move_last_tile:
                self._move_last_tile = idx
                self._move_trail.append((idx, 0.0))
        for i in range(len(self._move_trail) - 1, -1, -1):
            tile_index, t = self._move_trail[i]
            t += dt * 2.6
            if t >= 1.0:
                del self._move_trail[i]
            else:
                self._move_trail[i] = (tile_index, t)

        if st.phase is not self._last_phase:
            if st.phase is GamePhase.RESOLVE_TILE:
                target = st.current_player.position if st.current_player else None
                if self.board_view is not None:
                    self.board_view.highlight_index = target
                if target is not None:
                    self.presenter.push_banner(ActionBanner(
                        self._tile_banner_text(st, target), color_name="info",
                        icon="info", duration=1.1, priority=PRIORITY_AMBIENT))
                audio.play_sfx("land")
            if st.phase is GamePhase.WAIT_ROLL and st.current_player_id != self._last_current:
                audio.play_sfx("turn")
            self._last_phase = st.phase
            self._last_current = st.current_player_id

        # 破产棋子淡出（只影响绘制）
        for pid in list(self._fading):
            self._fading[pid] += dt
            if self._fading[pid] > 2.2:
                del self._fading[pid]

    def _tile_banner_text(self, st: GameState, tile_index: int) -> str:
        tile = st.board.tile(tile_index)
        return f"抵达「{tile.name}」"

    # ---------------------------------------------------------------- 事件演出

    def _handle_new_events(self, st: GameState) -> None:
        """处理新增事件：事件卡、横幅、音效。"""
        new_events = [e for e in st.event_log if e.seq > self._last_seen_seq]
        if not new_events:
            return
        self._last_seen_seq = new_events[-1].seq
        for ev in new_events:
            self._present_event(st, ev)

    def _handle_ledger(self, st: GameState) -> None:
        """资金反馈的唯一来源：EconomyLedger。

        v0.2 只有 4 种事件会飘字，玩家经常「钱少了但不知道为什么」。
        现在任何一笔资金变化都会浮字 + 进日志，因为它来自账本本身。
        """
        entries = st.ledger.entries
        if not entries:
            return
        new = [e for e in entries if e.seq > self._last_ledger_seq]
        if not new:
            return
        if len(new) > 12:      # 追平积压（例如刚读档 / 刚连上）时不刷屏
            new = new[-12:]
        self._last_ledger_seq = entries[-1].seq
        players = {p.id: p for p in st.players}
        for entry in new:
            # 开局发钱不是「变化」，不飘字，否则每个玩家一进局就顶着双倍数字
            if entry.category == Reason.INITIAL_MONEY:
                continue
            text, sub, color_name, icon_name = describe_ledger_entry(entry, players)
            player = players.get(entry.player_id)
            if player is None or self.board_view is None:
                continue
            # 破产玩家的结算残值不再飘字
            if player.bankrupt and entry.amount <= 0:
                continue
            pos = self.board_view.tile_center(player.position)
            self.presenter.pop_money(text, (pos[0], pos[1] - 24), sub=sub,
                                     color_name=color_name, icon=icon_name,
                                     size=26 if entry.amount > 0 else 24)
            self._money_anim[player.id] = self._money_anim.get(player.id, 0.0) + entry.amount
            self._play_money_sfx(entry.category)

    #: 资金流水 → 音效（买了 / 被收租 / 交税 听起来必须不一样）
    MONEY_SFX = {
        Reason.PROPERTY_PURCHASE: "buy",
        Reason.RENT: "rent",
        Reason.TAX: "pay",
        Reason.PROPERTY_UPGRADE: "upgrade",
        Reason.START_REWARD: "coin",
        Reason.BONUS_POOL: "coin",
        Reason.CHANCE: "coin",
        Reason.CARD: "card",
    }

    def _play_money_sfx(self, category: str) -> None:
        """给资金变化配声音，并做节流：连续多笔流水不该叠成噪音。"""
        name = self.MONEY_SFX.get(category)
        if not name:
            return
        now = time.time()
        if now - self._last_money_sfx_at < 0.16:
            return
        self._last_money_sfx_at = now
        audio.play_sfx(name)

    def _present_event(self, st: GameState, ev) -> None:
        data = ev.data or {}
        if ev.type == EventType.CHANCE_DRAWN:
            self._present_chance_card(st, ev)
        elif ev.type == EventType.CARD_USED:
            self._present_card_used(st, ev)
        elif ev.type == EventType.BANKRUPT:
            self._present_bankruptcy(st, ev)
        elif ev.type == EventType.PROPERTY_BOUGHT:
            self._present_ai_action(st, ev, "购买了")
        elif ev.type == EventType.PROPERTY_UPGRADED:
            self._present_ai_action(st, ev, "升级了")
        elif ev.type == EventType.PROPERTY_MONOPOLY:
            self.presenter.push_banner(ActionBanner(
                ev.message, color_name="accent", icon="property", duration=2.4,
                priority=PRIORITY_NORMAL))
        elif ev.type == EventType.JAIL_ENTERED:
            audio.play_sfx("jail")
        elif ev.type == EventType.PLAYER_DISCONNECTED:
            self.presenter.push_banner(ActionBanner(
                ev.message, sub="30 秒内可重新连接，超时由 AI 接管",
                color_name="warning", icon="network", duration=3.4,
                priority=PRIORITY_CRITICAL))
            audio.play_sfx("disconnect")
        elif ev.type == EventType.PLAYER_RECONNECTED:
            self.presenter.push_banner(ActionBanner(
                ev.message, sub="操作已交还本人", color_name="success", icon="network",
                duration=3.0, priority=PRIORITY_CRITICAL))
            audio.play_sfx("reconnect")
        elif ev.type == EventType.PLAYER_BOT_TAKEOVER:
            self.presenter.push_banner(ActionBanner(
                ev.message, sub="他回来时可以接回控制权", color_name="warning",
                icon="person", duration=3.0, priority=PRIORITY_CRITICAL))
            audio.play_sfx("disconnect")
        elif ev.type == EventType.GAME_OVER:
            audio.play_sfx("win")

    def _present_bankruptcy(self, st: GameState, ev) -> None:
        """破产演出：债务 / 资产 / 地产数量 + 资产去向，让所有人看清发生了什么。"""
        data = ev.data or {}
        player = st.player(ev.player_id)
        if player is None:
            return
        creditor = str(data.get("creditor_name", "") or "")
        if not creditor and data.get("creditor"):
            who = st.player(str(data["creditor"]))
            creditor = who.name if who is not None else ""
        self.presenter.push_bankruptcy(BankruptcyBanner(
            player.name,
            debt=int(data.get("debt", 0) or 0),
            assets=int(data.get("assets", 0) or 0),
            properties=int(data.get("properties", 0) or 0),
            creditor=creditor,
            reason=str(data.get("reason", "") or ""),
        ))
        self.presenter.push_banner(ActionBanner(
            f"{player.name} 破产退出", sub="资产已结算（见上方明细）",
            color_name="danger", icon="alert", duration=3.0,
            priority=PRIORITY_CRITICAL))
        # 棋子开始淡出（纯演出，不影响任何规则状态）
        self._fading[player.id] = 0.0
        audio.play_sfx("bankrupt")

    def _present_ai_action(self, st: GameState, ev, verb: str) -> None:
        """别人（尤其是 AI）买地 / 升级时给一句短提示。

        玩家需要看懂 AI 在做什么，而不是只看到自己的钱在变。
        只对「不是我做的」动作出提示，避免自己操作时被自己的提示刷屏。
        """
        if self.session is None:
            return
        actor = st.player(ev.player_id)
        if actor is None or not actor.is_ai and self.session.is_mine(actor.id):
            return
        data = ev.data or {}
        prop = st.properties.get(str(data.get("property", "")))
        if prop is None:
            return
        price = int(data.get("price", 0) or 0)
        if verb == "升级了":
            detail = f"{prop.level} 级"
            text = f"{actor.name} 把「{prop.name}」升到 {detail}"
            if price:
                text += f" ¥{price:,}"
        else:
            text = f"{actor.name} 购买了「{prop.name}」"
            if price:
                text += f" ¥{price:,}"
        speed = max(0.5, float(self.app.settings.animation_speed))
        self.presenter.push_banner(ActionBanner(
            text, color_name="info", icon="info",
            duration=max(0.65, 1.25 / speed), priority=PRIORITY_NORMAL))

    def _present_chance_card(self, st: GameState, ev) -> None:
        data = ev.data or {}
        detail = ""
        for later in st.event_log:
            if later.seq > ev.seq and later.type == EventType.CHANCE_APPLIED:
                detail = later.data.get("detail", "")
                break
        actor = st.player(ev.player_id)
        polarity = data.get("polarity", "neutral")
        speed = max(0.4, float(self.app.settings.animation_speed))
        self.presenter.push_card(EventCard(
            polarity,
            data.get("name", "机遇"),
            data.get("description", ""),
            detail,
            actor=actor.name if actor else "",
            duration=2.1 / min(2.0, speed) + 0.7,
        ))
        self.anim.add(CardFlipAnimation(
            pygame.Rect(0, 0, 420, 300), data.get("name", "机遇"),
            data.get("description", ""), accent=accent_for(polarity)))
        audio.play_sfx("chance" if polarity != "disaster" else "error")

    def _present_card_used(self, st: GameState, ev) -> None:
        """AI（或其他人）使用道具时给一句演出，避免「被偷袭却不知道」。"""
        data = ev.data or {}
        actor = st.player(ev.player_id)
        card = self._card_defs.get(data.get("card", "")) if self._card_defs else None
        if actor is None:
            return
        mine = self.session.is_mine(actor.id) if self.session else False
        if mine:
            return
        name = card.name if card else "道具"
        target_text = self._describe_ai_target(st, data, ev)
        self.presenter.push_banner(ActionBanner(
            f"{actor.name} 使用【{name}】", sub=target_text,
            color_name="warning", icon="card", duration=2.2,
            priority=PRIORITY_NORMAL))

    def _describe_ai_target(self, st: GameState, data: dict, ev) -> str:
        """从事件里恢复出「谁对谁做了什么」，只读不改。"""
        target = data.get("target")
        if target is None:
            for later in st.event_log:
                if later.seq > ev.seq and later.player_id == ev.player_id:
                    if target is None:
                        target = later.data.get("target")
        if target is None:
            return ""
        if isinstance(target, str) and target.startswith("p") and len(target) == 3:
            prop = st.properties.get(target)
            if prop is not None:
                return f"目标：{prop.name}"
        if isinstance(target, str):
            who = st.player(target)
            if who is not None:
                return f"目标：{who.name}"
        if isinstance(target, int):
            if 0 <= target < st.board.tile_count:
                return f"目标：{st.board.tile(target).name}"
            return f"目标：{target} 点"
        return ""

    # ---------------------------------------------------------------- hover

    def _update_hover(self, st: GameState, mouse: tuple[int, int]) -> None:
        if self.board_view is None:
            return
        idx = self.board_view.hit_test(mouse)
        self.board_view.hover_index = idx
        self.hover_tile = idx
        if idx is not None:
            self.prop_panel.set_tile(st, idx)
        elif st.current_player is not None:
            self.prop_panel.set_tile(st, st.current_player.position)
        self.hover_player = self.player_panel.hit_test(mouse)
        # 手牌悬停
        self._card_tooltip = ""
        for i, tile in enumerate(self.card_tiles):
            tile.hovered = tile.rect.collidepoint(mouse)
            if tile.hovered:
                self._card_tooltip = tile.tooltip(i)

    #: 自动存档的最小间隔（秒）与最小回合间隔 —— 每回合存一次既慢又没意义
    AUTOSAVE_INTERVAL_SEC = 20.0
    AUTOSAVE_TURNS = 4

    def _maybe_autosave(self, st: GameState) -> None:
        if self.session is None or self.session.engine is None:
            return
        if st.game_over:
            return
        now = time.time()
        if (now - self._autosave_time < self.AUTOSAVE_INTERVAL_SEC
                and st.turn_number - self._autosave_turn < self.AUTOSAVE_TURNS):
            return
        self._autosave_time = now
        self._autosave_turn = st.turn_number
        savegame.autosave(self.session.engine)

    # ================================================================ 决策

    def _maybe_open_decision(self, st: GameState) -> None:
        if self.modal is not None or self._paused:
            return
        decision = self.session.decision if self.session else None
        if decision is None:
            return
        kind = decision.kind
        # 掷骰与升级这种「有默认动作」的决策不弹窗，改用侧栏按钮，体验更顺
        if kind in (DecisionKind.ROLL,):
            return
        if kind == DecisionKind.DEBT_RESOLUTION:
            if not isinstance(self.modal, AssetPanel):
                self.open_asset_panel(debt_mode=True)
            return
        if kind == DecisionKind.SHOP:
            if not isinstance(self.modal, ShopDialog):
                self._open_shop_dialog(decision)
            return
        if self._pending_card_id is not None:
            return

        accent = {
            DecisionKind.BUY_PROPERTY: "accent",
            DecisionKind.UPGRADE_PROPERTY: "success",
            DecisionKind.JAIL: "warning",
            DecisionKind.CARD_TARGET_PLAYER: "primary",
            DecisionKind.CARD_TARGET_TILE: "primary",
            DecisionKind.CARD_TARGET_OWN_PROPERTY: "primary",
            DecisionKind.CARD_DICE_VALUE: "primary",
        }.get(kind, "accent")

        extra: list[str] = []
        player = st.player(decision.player_id)
        if kind == DecisionKind.JAIL and player is not None:
            extra.append(f"你的现金：{money(player.money)}")
        if kind == DecisionKind.BUY_PROPERTY and player is not None:
            prop = self._decision_property(st, decision)
            if prop is not None:
                district_owned, district_total = self._district_count(
                    st, player.id, prop.district)
                extra.append(f"你的现金：{money(player.money)}　·　"
                             f"本区进度：{district_owned}/{district_total}")
            else:
                extra.append(f"你的现金：{money(player.money)}")

        self.modal = DecisionDialog(decision, self._on_decision_choice,
                                    accent=accent, extra_lines=extra)

    def _decision_property(self, st: GameState, decision: PendingDecision):
        pid = decision.context.get("property_id") or decision.context.get("property")
        if pid is None:
            return None
        return st.properties.get(str(pid))

    def _district_count(self, st: GameState, player_id: str,
                        district: str | None) -> tuple[int, int]:
        if not district:
            return 0, 0
        ids = (st.district_props or {}).get(district, [])
        owned = sum(1 for pid in ids
                    if (st.properties.get(pid) is not None
                        and st.properties[pid].owner_id == player_id))
        return owned, len(ids)

    def _on_decision_choice(self, option_id: str) -> None:
        decision = self.session.decision if self.session else None
        if decision is None:
            return
        self.session.submit(CommandType.RESOLVE_DECISION, {"option_id": option_id},
                            decision_id=decision.id)
        self._autosave_turn = -999
        self._autosave_time = 0.0
        audio.play_sfx("click")

    def _maybe_show_game_over(self, st: GameState) -> None:
        if not st.game_over or self._game_over_shown:
            return
        if self.modal is not None and not isinstance(self.modal, GameOverDialog):
            return
        self._game_over_shown = True
        ranking = victory.ranking(st)
        stats = victory.final_stats(st)
        timeline = list(getattr(getattr(st, "analytics", None), "milestones", []) or [])
        can_restart = self.session is not None and self.session.engine is not None
        is_client = self.session is not None and self.session.client is not None
        self.modal = GameOverDialog(
            ranking, st.round_number, stats,
            on_again=self._restart,
            on_exit=self._back_to_lobby,
            can_restart=can_restart,
            client_mode=is_client,
            timeline=timeline,
        )
        audio.play_sfx("win")

    # ================================================================ 输入状态

    def interaction_state(self) -> str:
        """本机玩家此刻能做什么 —— 全项目唯一的判定点。"""
        if self.session is None:
            return LocalInteraction.NONE
        st = self.session.state
        if st is None:
            return LocalInteraction.NONE
        if st.game_over:
            return LocalInteraction.GAME_OVER
        pd = self.session.decision
        if self._pending_card_id is not None and pd is None:
            return LocalInteraction.CARD_TARGET
        if pd is None:
            return LocalInteraction.WAITING
        return {
            DecisionKind.ROLL: LocalInteraction.ROLL_DICE,
            DecisionKind.BUY_PROPERTY: LocalInteraction.PROPERTY_DECISION,
            DecisionKind.UPGRADE_PROPERTY: LocalInteraction.PROPERTY_DECISION,
            DecisionKind.JAIL: LocalInteraction.JAIL_DECISION,
            DecisionKind.DEBT_RESOLUTION: LocalInteraction.DEBT,
            DecisionKind.SHOP: LocalInteraction.SHOP,
            DecisionKind.CHANCE_ACK: LocalInteraction.CONFIRM,
        }.get(pd.kind, LocalInteraction.CARD_TARGET)

    @property
    def i_am_acting(self) -> bool:
        """此刻是不是该我操作。"""
        return self.interaction_state() not in (
            LocalInteraction.NONE, LocalInteraction.WAITING,
            LocalInteraction.GAME_OVER)

    # ================================================================ 资产面板

    def _my_player(self):
        if self.session is None:
            return None
        st = self.session.state
        if st is None:
            return None
        return st.player(self.session.player_id)

    def open_asset_panel(self, debt_mode: bool = False) -> None:
        """打开资产面板。debt_mode 下顶部显示欠款进度。"""
        st = self.session.state if self.session else None
        if st is None:
            return
        player = self._my_player()
        if player is None:
            return

        title = "我的资产"
        debt = None
        if debt_mode and st.debt:
            amount = int(st.debt["amount"])
            debt = {
                "amount": amount,
                "reason": st.debt.get("reason", ""),
                "creditor": st.debt.get("creditor_id") or st.debt.get("creditor"),
            }
        panel = AssetPanel(
            st, player, self._asset_action,
            on_close=self._on_asset_panel_closed,
            title=title, allow_sell=True, debt=debt,
            on_declare=(self._declare_bankruptcy if debt is not None else None),
        )
        panel.board_view = self.board_view
        self.modal = panel

    def _asset_action(self, action: str, property_id: str) -> bool:
        """资产面板里的操作：本地预检给即时反馈，真正的校验仍在引擎里。"""
        if self.session is None:
            return False
        cmd = {
            "upgrade": CommandType.UPGRADE_PROPERTY,
            "sell": CommandType.SELL_PROPERTY,
            "mortgage": CommandType.MORTGAGE_PROPERTY,
            "redeem": CommandType.REDEEM_PROPERTY,
            "downgrade": CommandType.DOWNGRADE_PROPERTY,
        }.get(action)
        if cmd is None:
            return False
        st = self.session.state
        player = self._my_player()
        if st is None or player is None:
            return False

        prop = st.properties.get(str(property_id))
        if prop is None:
            return False
        if action == "upgrade":
            cost, _ = economy.upgrade_cost(st, player, prop)
            if prop.is_max_level or player.money < cost:
                return False
        elif action == "mortgage":
            ok, _ = economy.can_mortgage(prop)
            if not ok:
                return False
        elif action == "redeem":
            if player.money < economy.redeem_cost(prop):
                return False

        self.session.submit(cmd, {"property_id": property_id})
        audio.play_sfx("click")
        return True

    def _declare_bankruptcy(self) -> None:
        """债务救不回来时，玩家可以选择结束本局 —— 必须有这条出路。"""
        from src.game.commands import CommandType as CT

        if self.session is not None:
            self.session.submit(CT.DECLARE_BANKRUPTCY, {})

    def _on_asset_panel_closed(self) -> None:
        """债务未解决时不允许靠关窗口逃债。"""
        st = self.session.state if self.session else None
        if st is None or st.debt is None:
            return
        player = self._my_player()
        if player is None or st.debt.get("player_id") != player.id:
            return
        self._pending_debt_reopen = True

    # ================================================================ 商店

    def _open_shop_dialog(self, decision) -> None:
        st = self.session.state if self.session else None
        player = self._my_player()
        if st is None or player is None or self._card_defs is None:
            return
        offers = []
        for cid in decision.context.get("cards", []):
            card = self._card_defs.get(cid)
            if card is None:
                continue
            offers.append({
                "id": cid,
                "name": card.name,
                "description": card.description,
                "rarity": card.rarity,
                "icon": card.icon,
                "timing_label": TIMING_LABEL.get(card.timing, ""),
            })
        limit = int(st.rules.get("max_cards_per_player", 5))
        self.modal = ShopDialog(
            decision.title.replace("商店 · ", ""),
            offers, player.money,
            on_buy=lambda cid: self.session.submit(CommandType.BUY_SHOP_CARD,
                                                   {"card_id": cid}),
            on_leave=lambda: self.session.submit(CommandType.LEAVE_SHOP, {}),
            inventory=len(player.cards), inventory_limit=limit,
            price_of=lambda cid: self._shop_price_for(player, cid),
        )

    def _shop_price_for(self, player, card_id: str) -> int:
        from ..game import modifiers as mods

        card = self._card_defs.get(card_id) if self._card_defs else None
        base = int(getattr(card, "shop_price", 0) or 0) if card else 0
        if base <= 0:
            st = self.session.state if self.session else None
            base = int(st.rules.get("shop_base_price", 1200)) if st else 1200
        final, _ = mods.resolve(player, mods.Hook.SHOP_PRICE, base)
        return max(0, final)

    # ================================================================ 输入

    def handle_event(self, event: pygame.event.Event) -> None:
        if self.session is None:
            return
        # 新手引导在最上层：它要能拦住「点一下试试」之外的误操作
        if self._guide is not None and self._guide.handle_event(event):
            return
        if self.modal is not None:
            self.modal.handle_event(event)
            return
        # 教练面板的按钮优先（它就画在棋盘上方）
        if self._tutorial is not None and self._tutorial.handle_event(event):
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self._pending_card_id is not None:
                    self._cancel_card()
                    return
                if self.presenter.dismiss_card():
                    return
                self._open_pause()
                return
            if event.key == pygame.K_F1:
                self.app.settings.set("ui", "debug_overlay",
                                      not self.app.settings.debug_overlay)
                return
            if event.key == pygame.K_SPACE:
                if self.presenter.dismiss_card():
                    return
                self._try_roll()
                return
            if event.key == pygame.K_i:
                if self.interaction_state() != LocalInteraction.DEBT:
                    self.open_asset_panel()
                return
            if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
                self._use_card_by_index(event.key - pygame.K_1)
                return
            if event.key == pygame.K_F2:
                return          # 交给全局快捷键处理（静音）

        if event.type == pygame.MOUSEWHEEL:
            if self.log_list.rect.collidepoint(pygame.mouse.get_pos()):
                self.log_list.handle_event(event)
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # 点任意处可关掉事件卡（不阻塞操作）
            if self.presenter.dismiss_card():
                return
            # 「本局记录」标题区：点一下打开完整记录
            if self.log_button.rect.collidepoint(event.pos):
                self.open_player_log()
                return
            if self._pending_card_id is not None and self.board_view is not None:
                idx = self.board_view.hit_test(event.pos)
                if idx is not None and idx in self._targets:
                    self._confirm_card_target(idx)
                    return
            if self.board_view is not None:
                idx = self.board_view.hit_test(event.pos)
                if idx is not None:
                    self.tile_index_clicked = idx
                    self.prop_panel.set_tile(self.session.state, idx)
                    return

        for button in self.buttons:
            if button.handle_event(event):
                return
        for tile in self.card_tiles:
            if not tile.enabled:
                continue
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                if tile.rect.collidepoint(event.pos):
                    self._start_card_use(tile.card_id)
                    return
        if self.log_list.handle_event(event):
            return
        super().handle_event(event)

    def _open_help(self) -> None:
        self.app.scenes.push("help", back="game")

    def _open_pause(self) -> None:
        if self.modal is not None:
            return
        is_local = self.session is not None and self.session.engine is not None
        items: list[tuple[str, Any, str]] = [("继续游戏", self._close_modal, "primary")]
        me = self._my_player()
        if me is not None and not me.bankrupt:
            items.append(("管理资产（I）", self._pause_open_assets, "secondary"))
        items.append(("规则与图鉴（H）", self._pause_open_help, "secondary"))
        if is_local:
            items.append(("保存游戏", self._save_now, "secondary"))
        items.append(("设置", lambda: self.app.scenes.switch_to(
            "settings", back="game"), "secondary"))
        items.append(("退出对局", self._back_to_lobby, "danger"))
        note = "单机模式：ESC 暂停不会影响其他玩家" if is_local else \
            "联机模式：只能调整本机设置，无法暂停其他玩家"
        self._paused = True
        self.modal = PauseMenu(self._close_modal, items, note=note)

    def _close_modal(self) -> None:
        self._paused = False
        self.modal = None

    def _pause_open_assets(self) -> None:
        self._paused = False
        self.modal = None
        self.open_asset_panel()

    def _pause_open_help(self) -> None:
        self._paused = False
        self.modal = None
        self._open_help()

    def _save_now(self) -> None:
        if self.session is None or self.session.engine is None:
            return
        try:
            path = savegame.save_to_slot(self.session.engine, "manual")
            name = path.replace("\\", "/").split("/")[-1]
            self.toasts.push(f"已保存到 saves/{name}", "success")
        except Exception as exc:
            self.toasts.push(f"保存失败：{exc}", "error")

    def _restart(self) -> None:
        self.app.restart_current_game()

    def _back_to_lobby(self) -> None:
        self.app.return_to_lobby()

    # ================================================================ 操作

    def _try_roll(self) -> None:
        if self.session is None:
            return
        decision = self.session.decision
        if decision is None or decision.kind != DecisionKind.ROLL:
            return
        self.session.submit(CommandType.ROLL_DICE, {}, decision_id=decision.id)
        self._autosave_turn = -999
        self._autosave_time = 0.0

    def _use_card_by_index(self, index: int) -> None:
        if index >= len(self.card_tiles):
            return
        tile = self.card_tiles[index]
        if not tile.enabled:
            self.toasts.push(f"「{tile.name}」暂不可用：{tile.reason}", "warning")
            audio.play_sfx("error")
            return
        self._start_card_use(tile.card_id)

    def _start_card_use(self, card_id: str) -> None:
        if self.session is None or self._card_defs is None:
            return
        st = self.session.state
        card = self._card_defs.get(card_id)
        if st is None or card is None:
            return
        player = st.player(self.session.player_id)
        if player is None:
            return
        ok, reason = can_use(st, player, card)
        if not ok:
            self.toasts.push(reason, "warning")
            audio.play_sfx("error")
            return

        targets = valid_targets(st, player, card)
        if card.needs_target is None:
            self._confirm_card(card, None)
            return

        pairs: list[tuple[Any, str]] = []
        self._targets = {}
        if card.needs_target == "tile":
            for idx in targets:
                tile = st.board.tile(idx)
                prop = st.property_at(idx)
                label = tile.name
                if prop is not None:
                    if prop.owner_id is None:
                        label += f"（无主 {prop.price:,}）"
                    elif prop.owner_id == player.id:
                        label += f"（你的 {prop.level} 级）"
                    else:
                        owner = st.player(prop.owner_id)
                        label += f"（{owner.name if owner else '?'}）"
                pairs.append((idx, label))
                self._targets[idx] = label
        elif card.needs_target == "player":
            for pid in targets:
                other = st.player(str(pid))
                if other is not None:
                    label = f"{other.name}（现金 {other.money:,}）"
                    pairs.append((pid, label))
                    self._targets[pid] = label
        elif card.needs_target == "own_property":
            for pid in targets:
                prop = st.properties.get(str(pid))
                if prop is not None:
                    label = f"{prop.name} {prop.level}→{prop.level + 1} 级"
                    pairs.append((pid, label))
                    self._targets[pid] = label
        elif card.needs_target == "dice_value":
            for value in targets:
                landing = (player.position + int(value)) % st.board.tile_count
                label = f"{value} 点 → {st.board.tile(landing).name}"
                pairs.append((value, label))
                self._targets[value] = label

        if not pairs:
            self.toasts.push("没有可用目标", "warning")
            return

        self._pending_card_id = card_id
        self.modal = CardTargetDialog(
            card.name, card.description, pairs,
            on_pick=lambda target: self._confirm_card(card, target),
            on_cancel=self._cancel_card,
            columns=1 if card.needs_target == "tile" else 2,
            icon=card.icon,
            timing=TIMING_LABEL.get(card.timing, ""),
        )

    def _confirm_card_target(self, target: Any) -> None:
        """在棋盘上直接点选目标。"""
        if self._card_defs is None or self._pending_card_id is None:
            return
        card = self._card_defs.get(self._pending_card_id)
        if card is None:
            return
        if self.modal is not None:
            self.modal.close()
            self.modal = None
        self._confirm_card(card, target)

    def _cancel_card(self) -> None:
        self._pending_card_id = None
        self._targets = {}
        if self.modal is not None:
            self.modal.close()
            self.modal = None

    def _confirm_card(self, card, target: Any) -> None:
        if self.session is None:
            return
        self._pending_card_id = None
        self._targets = {}
        self.session.submit(CommandType.USE_CARD,
                            {"card_id": card.id, "target": target})
        audio.play_sfx("click")

    # ================================================================ 绘制

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, SCREEN_W, SCREEN_H),
                        (22, 30, 42), (16, 22, 32))
        st = self.session.state if self.session else None
        if st is None:
            theme.draw_text(surface, "正在载入对局…", self.fonts.h1(),
                            theme.color("text_dim"), (800, 450), anchor="center")
            return

        self._draw_topbar(surface, st)
        self._draw_board(surface, st)
        self._draw_side(surface, st)
        self.draw_widgets(surface)

        self.presenter.draw_world(surface, self.fonts)
        self.anim.draw(surface, self.fonts)
        self.presenter.draw_overlay(surface, self.fonts)

        mouse = pygame.mouse.get_pos()
        if self.app.settings.show_tooltips and self.modal is None:
            self._draw_tooltip(surface, st, mouse)

        if self.app.settings.debug_overlay:
            self._draw_debug(surface, st)

        self.toasts.draw(surface, self.fonts, center_x=800, bottom_y=884)

        if self._tutorial is not None:
            self._tutorial.draw(surface, self.fonts)
        if self._guide is not None:
            self._guide.draw(surface, self.fonts)

        if self.modal is not None:
            self.modal.draw(surface, self.fonts)
            if self.app.settings.show_tooltips:
                self._draw_modal_tooltip(surface, mouse)

    # ---------------------------------------------------------------- 顶栏

    def _draw_topbar(self, surface: pygame.Surface, st: GameState) -> None:
        owners = {p.id: st.owned_count(p.id) for p in st.players}
        self.player_panel.draw(
            surface, self.fonts, st.players, st.current_player_id, owners,
            self.anim.pulse.value, self._char_names, self._card_defs,
            money_anim=self._money_anim)

        info = ROUND_RECT
        theme.panel(surface, info, fill="panel", radius=theme.RADIUS["lg"])
        theme.draw_text(surface, f"第 {st.round_number} 轮", self.fonts.h3(),
                        theme.color("accent"), (info.centerx, info.y + 12), anchor="midtop")
        theme.draw_text(surface, st.phase.label, self.fonts.tiny(), theme.color("text_dim"),
                        (info.centerx, info.y + 38), anchor="midtop")
        alive = sum(1 for p in st.players if not p.bankrupt)
        theme.draw_text(surface, f"存活 {alive}/{len(st.players)}", self.fonts.micro(),
                        theme.color("text_mute"), (info.centerx, info.bottom - 16),
                        anchor="midtop")

    # ---------------------------------------------------------------- 棋盘

    def _draw_board(self, surface: pygame.Surface, st: GameState) -> None:
        bv = self.board_view
        if bv is None:
            return
        owner_colors = {
            p.id: theme.hex_to_rgb(_color_of(p.color_id)) for p in st.players
        }
        owner_slots = {p.id: p.slot for p in st.players}
        monopolies = set()
        for p in st.players:
            if not p.bankrupt:
                monopolies |= monopoly_districts(st, p.id)

        bv.draw_background(surface)
        bv.draw_tiles_dynamic(surface, self.fonts, st.properties, st.barriers,
                              owner_colors, owner_slots, monopolies)

        # 逐格移动反馈：经过的格子亮一下
        for tile_index, t in self._move_trail:
            rect = bv.tile_rect(tile_index)
            alpha = int(150 * (1.0 - t))
            inflate = int(-2 + 8 * t)
            theme.rounded_rect(surface, rect.inflate(inflate, inflate),
                               theme.color("accent", alpha), radius=10,
                               border=theme.color("accent_soft", alpha), border_width=2)

        # 道具选目标：合法目标高亮
        if self._pending_card_id is not None:
            for target in self._targets:
                if isinstance(target, int) and 0 <= target < st.board.tile_count:
                    bv.draw_target_ring(surface, target, "primary", self.anim.pulse.value)

        bv.draw_hover(surface)
        if st.phase is not GamePhase.MOVING:
            bv.draw_highlight(surface, bv.highlight_index, self.anim.pulse.value)

        inner = bv.inner_rect
        self._draw_center_info(surface, st, inner)
        self._draw_pieces(surface, st, bv)

        if self.tile_index_clicked is not None:
            rect = bv.tile_rect(self.tile_index_clicked)
            theme.rounded_rect(surface, rect.inflate(-2, -2), None, radius=10,
                               border=theme.color("primary"), border_width=3)

    def _draw_center_info(self, surface: pygame.Surface, st: GameState,
                          inner: pygame.Rect) -> None:
        """中央区域：阶段提示 + 当前玩家卡 + 骰子托盘 + 最近事件 + 奖金池。

        这些内容都不可点击（中央区域不参与命中判定），因此不会挡住棋盘点选。
        """
        cur = st.current_player
        if cur is None:
            return
        is_my = self.session.is_mine(cur.id) if self.session else False
        acting = self.interaction_state() not in (
            LocalInteraction.NONE, LocalInteraction.WAITING,
            LocalInteraction.GAME_OVER)

        # ---- 阶段胶囊
        phase_font = self.fonts.sized(theme.FONT["small"], True)
        label = st.phase.label
        pill = pygame.Rect(0, 0, phase_font.size(label)[0] + 44, 32)
        pill.midtop = (inner.centerx, inner.y + 10)
        accent = "accent" if is_my else "border"
        theme.rounded_rect(surface, pill, theme.color("bg_alt", 232), radius=16)
        theme.rounded_rect(surface, pill, None, radius=16, border=theme.color(accent),
                           border_width=2)
        theme.draw_text(surface, label, phase_font, theme.color(accent),
                        pill.center, anchor="center")

        # ---- 当前玩家卡
        card = pygame.Rect(0, 0, 520, 92)
        card.midtop = (inner.centerx, pill.bottom + 14)
        col = theme.hex_to_rgb(_color_of(cur.color_id))
        theme.panel(surface, card, fill="panel", radius=theme.RADIUS["xl"],
                    border=theme.color("accent") if is_my else None, shadow=True)
        if is_my:
            theme.rounded_rect(surface, card, None, radius=theme.RADIUS["xl"],
                               border=theme.color("accent"), border_width=2)
        avatar = pygame.Rect(card.x + 14, card.centery - 26, 52, 52)
        pygame.draw.circle(surface, col, avatar.center, 26)
        pygame.draw.circle(surface, theme.darken(col, 0.4), avatar.center, 26, 2)
        initial = (self._char_names.get(cur.character_id) or cur.name or "?")[0]
        theme.draw_text(surface, initial, self.fonts.sized(24, True), (255, 255, 255),
                        avatar.center, anchor="center")

        head = "该你行动了" if (is_my and acting) else (
            "你的回合（准备中）" if is_my else f"{cur.name} 的回合")
        theme.draw_text(surface, head, self.fonts.sized(theme.FONT["h2"], True),
                        theme.color("accent" if is_my else "text"), (avatar.right + 14, card.y + 12))
        money_text = f"{cur.money:,}"
        theme.draw_text(surface, money_text, self.fonts.sized(theme.FONT["big"], True),
                        theme.color("text_mute" if cur.bankrupt else "accent"),
                        (card.right - 18, card.y + 12), anchor="topright")
        sub_parts = [f"资产 {st.player_asset_value(cur.id):,}",
                     f"地产 {st.owned_count(cur.id)}"]
        if cur.status_effects:
            sub_parts.append("、".join(e.label for e in cur.status_effects[:2]))
        theme.draw_text(surface, "　·　".join(sub_parts), self.fonts.sized(theme.FONT["small"]),
                        theme.color("text_dim"), (avatar.right + 14, card.y + 46))

        # 决策指引（我会操作时显示明确下一步）
        hint = self._next_step_hint(st)
        if hint:
            hint_font = self.fonts.sized(theme.FONT["small"], True)
            theme.draw_text(surface, hint, hint_font,
                            theme.color("accent" if is_my else "text_mute"),
                            (card.centerx, card.bottom + 10), anchor="midtop")

        # ---- 骰子托盘
        tray = pygame.Rect(0, 0, 300, 118)
        tray.center = (inner.centerx, inner.centery + 6)
        theme.rounded_rect(surface, tray, theme.color("bg_alt", 120), radius=18)
        theme.rounded_rect(surface, tray, None, radius=18,
                           border=theme.color("border_soft", 150), border_width=1)
        theme.draw_text(surface, "骰子", self.fonts.sized(theme.FONT["tiny"], True),
                        theme.color("text_mute"), (tray.x + 14, tray.centery),
                        anchor="midleft")

        # ---- 最近事件（1 行摘要，让玩家随时知道刚发生了什么）
        recent = [e for e in st.event_log[-14:]
                  if e.type not in (EventType.PHASE_CHANGED,)]
        if recent:
            ev = recent[-1]
            color, icon_name = self.LOG_STYLE.get(ev.type, ("text_dim", "info"))
            plate = pygame.Rect(0, 0, inner.width - 80, 42)
            plate.midtop = (inner.centerx, tray.bottom + 12)
            theme.rounded_rect(surface, plate, theme.color("bg_alt", 210), radius=12)
            icons.draw_icon(surface, icon_name or "info",
                            pygame.Rect(plate.x + 12, plate.centery - 10, 20, 20),
                            theme.color(color), theme.color("shadow"))
            theme.draw_text(surface,
                            theme.truncate(ev.message, self.fonts.sized(theme.FONT["small"]),
                                           plate.width - 60),
                            self.fonts.sized(theme.FONT["small"]),
                            theme.color("text"), (plate.x + 42, plate.centery),
                            anchor="midleft")

        # ---- 奖金池
        if st.bonus_pool > 0:
            pool = pygame.Rect(0, 0, 250, 44)
            pool.midbottom = (inner.centerx, inner.bottom - 18)
            theme.rounded_rect(surface, pool, theme.color("bg_alt", 232), radius=22)
            theme.rounded_rect(surface, pool, None, radius=22,
                               border=theme.color("accent"), border_width=2)
            icons.draw_icon(surface, "trophy",
                            pygame.Rect(pool.x + 16, pool.centery - 11, 22, 22),
                            theme.color("accent"), theme.color("shadow"))
            theme.draw_text(surface, f"奖金池 {st.bonus_pool:,}", self.fonts.body(),
                            theme.color("accent"),
                            (pool.x + 46, pool.centery), anchor="midleft")

        # 「等待某人操作」不再单独画一块——玩家卡下面那行指引已经写了同样的话，
        # 同一屏出现两次只会分散注意力。

    def _next_step_hint(self, st: GameState) -> str:
        """给玩家一句「现在该点哪里」的明确指引。"""
        if self.session is None:
            return ""
        state = self.interaction_state()
        if state == LocalInteraction.WAITING:
            pd = self.session.watching_decision
            if pd is not None:
                other = st.player(pd.player_id)
                if other is not None:
                    return f"等待 {other.name} 操作…"
            # 没有待处理决策 = 正处在自动演出阶段。
            # 这时候说「等待其他玩家」是错的（v0.3 的截图里同一屏
            # 一边写「该你行动了」一边写「等待其他玩家」），要说明在演什么。
            if st.phase is GamePhase.GAME_SETUP:
                return "正在发放初始资金与道具…"
            if st.phase in (GamePhase.TURN_START, GamePhase.ROLLING,
                            GamePhase.MOVING, GamePhase.ARRIVED,
                            GamePhase.RESOLVE_TILE, GamePhase.APPLY_EVENT,
                            GamePhase.TURN_END):
                cur = st.current_player
                if cur is not None and self.session.is_mine(cur.id):
                    return f"正在处理你的回合（{st.phase.label}）…"
                if cur is not None:
                    return f"{cur.name} 正在行动（{st.phase.label}）…"
            return "等待其他玩家…"
        return LocalInteraction.HINTS.get(state, "")

    def _draw_pieces(self, surface: pygame.Surface, st: GameState,
                     bv: BoardView) -> None:
        """绘制所有棋子，同格自动错位。破产的棋子会淡出（纯演出）。"""
        by_tile: dict[int, list[Any]] = {}
        for p in st.players:
            # 破产玩家已经退出棋盘；只有「正在淡出」的那两秒例外
            if p.bankrupt and p.id not in self._fading:
                continue
            by_tile.setdefault(p.position, []).append(p)

        moving_id = st.move_player_id if st.phase is GamePhase.MOVING else None

        for tile_index, players in by_tile.items():
            players = sorted(players, key=lambda x: x.slot)
            n = len(players)
            center = bv.tile_center(tile_index)
            radius = max(10, int(bv.cell * 0.17))
            if n > 2:
                radius = max(9, int(radius * 0.88))
            if n > 4:
                radius = max(8, int(radius * 0.9))
            ring = 0 if n == 1 else min(bv.cell * 0.30, radius * 0.78 * n)
            for i, player in enumerate(players):
                if player.id == moving_id and self._move_anim is not None:
                    continue
                if n == 1:
                    offset = (0, -int(bv.cell * 0.17))
                else:
                    angle = (i / n) * math.tau - math.pi / 2
                    offset = (int(math.cos(angle) * ring), int(math.sin(angle) * ring) + 2)
                alpha = self._piece_alpha(player)
                self._draw_one_piece(surface, st, player,
                                     (center[0] + offset[0], center[1] + offset[1]),
                                     radius, alpha=alpha)

        if moving_id is not None and self._move_anim is not None:
            player = st.player(moving_id)
            if player is not None:
                from_center = bv.tile_center(st.move_from)
                pos = self._move_anim.current_center(from_center)
                move_radius = max(10, int(bv.cell * 0.17))
                self._draw_one_piece(surface, st, player, pos, move_radius, moving=True)

    def _piece_alpha(self, player) -> int:
        """破产淡出期间的棋子透明度（255 = 完全不透明）。"""
        elapsed = self._fading.get(player.id)
        if elapsed is None:
            return 255
        return max(0, int(255 * (1.0 - min(1.0, elapsed / 1.8))))

    def _draw_one_piece(self, surface: pygame.Surface, st: GameState, player,
                        pos: tuple[float, float], radius: int,
                        moving: bool = False, alpha: int = 255) -> None:
        if alpha <= 6:
            return
        if alpha >= 255:
            self._draw_one_piece_raw(surface, st, player, pos, radius, moving)
            return
        # 淡出：整枚棋子（含光环与编号）一起变淡，避免只剩半个影子
        layer = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        self._draw_one_piece_raw(layer, st, player, pos, radius, moving)
        layer.set_alpha(alpha)
        surface.blit(layer, (0, 0))

    def _draw_one_piece_raw(self, surface: pygame.Surface, st: GameState, player,
                            pos: tuple[float, float], radius: int,
                            moving: bool = False) -> None:
        x, y = int(pos[0]), int(pos[1])
        col = theme.hex_to_rgb(_color_of(player.color_id))
        is_current = player.id == st.current_player_id
        pulse = self.anim.pulse.value

        # 当前玩家光环 + 箭头
        if is_current:
            r = int(radius + 8 + 4 * pulse)
            halo = pygame.Surface((r * 2 + 8, r * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(halo, theme.color("accent", int(60 + 60 * pulse)),
                               (r + 4, r + 4), r)
            surface.blit(halo, (x - r - 4, y - r - 4))
            ay = y - radius - 18 - int(4 * pulse)
            pygame.draw.polygon(surface, theme.color("accent"),
                                [(x, ay + 13), (x - 10, ay), (x + 10, ay)])
            pygame.draw.polygon(surface, theme.color("accent_dark"),
                                [(x, ay + 13), (x - 10, ay), (x + 10, ay)], 2)

        # 影子（移动时抬高，影子变小）
        shadow_r = radius if not moving else max(4, radius - 4)
        shadow = pygame.Surface((radius * 2 + 6, radius * 2 + 6), pygame.SRCALPHA)
        pygame.draw.circle(shadow, (0, 0, 0, 90), (radius + 3, radius + 8), shadow_r)
        surface.blit(shadow, (x - radius - 3, y - radius - 3))

        pygame.draw.circle(surface, col, (x, y), radius)
        pygame.draw.circle(surface, theme.lighten(col, 0.32), (x - radius // 3, y - radius // 3),
                           max(2, radius // 3))
        pygame.draw.circle(surface, (255, 255, 255), (x, y), radius, 1)
        pygame.draw.circle(surface, theme.darken(col, 0.45), (x, y), radius, 2)

        theme.draw_text(surface, str(player.slot + 1), self.fonts.sized(radius, True),
                        (255, 255, 255), (x, y), anchor="center")

        for i, icon_name in enumerate(self._piece_status_icons(player)):
            icons.draw_icon(surface, icon_name,
                            pygame.Rect(x + radius - 6, y - radius - 2 + i * 15, 15, 15),
                            theme.color("warning"), theme.color("shadow"))

    def _piece_status_icons(self, player) -> list[str]:
        out: list[str] = []
        if player.in_jail:
            out.append("jail")
        if player.disconnected:
            out.append("network")
        elif player.bot_controlled:
            out.append("person")
        return out[:2]

    # ---------------------------------------------------------------- 右栏

    def _draw_side(self, surface: pygame.Surface, st: GameState) -> None:
        self._draw_action_panel(surface, st)
        self.prop_panel.draw(surface, self.fonts, st)
        self._draw_log(surface)

    def _draw_action_panel(self, surface: pygame.Surface, st: GameState) -> None:
        rect = pygame.Rect(SIDE_RECT.x, SIDE_RECT.y, SIDE_RECT.width, ACTION_H)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        cur = st.current_player
        theme.section_header(surface, self.fonts, pygame.Rect(rect.x + 16, rect.y + 12,
                                                              rect.width - 32, 22),
                             "当前行动", icon="clock")

        if cur is None:
            return
        is_mine = self.session.is_mine(cur.id) if self.session else False

        col = theme.hex_to_rgb(_color_of(cur.color_id))
        pygame.draw.circle(surface, col, (rect.x + 30, rect.y + 58), 13)
        theme.draw_text(surface, theme.truncate(cur.name, self.fonts.h3(), 180),
                        self.fonts.h3(), theme.color("text"), (rect.x + 50, rect.y + 46))
        theme.draw_text(surface, f"{cur.money:,}", self.fonts.h3(), theme.color("accent"),
                        (rect.right - 18, rect.y + 46), anchor="topright")

        self.roll_button.draw(surface, self.fonts)

        preview = DICE_PREVIEW_RECT
        theme.rounded_rect(surface, preview, theme.color("bg_alt"), radius=theme.RADIUS["lg"])
        if st.dice is not None:
            d1 = pygame.Rect(0, 0, 34, 34)
            d1.center = (preview.x + 46, preview.centery)
            d2 = pygame.Rect(0, 0, 34, 34)
            d2.center = (preview.x + 88, preview.centery)
            draw_die(surface, d1, st.dice.die1)
            draw_die(surface, d2, st.dice.die2)
            theme.draw_text(surface, f"{st.dice.total}", self.fonts.h3(),
                            theme.color("accent"), (preview.right - 28, preview.centery),
                            anchor="center")
        else:
            theme.draw_text(surface, "— —", self.fonts.h2(), theme.color("text_mute"),
                            preview.center, anchor="center")

        # ---- 下一步指引（比 v0.2的一句小字更明确）
        hint_state = self.interaction_state()
        hint = self._next_step_hint(st)
        hint_rect = pygame.Rect(rect.x + 16, rect.y + 152, rect.width - 32, 24)
        if hint:
            icons.draw_icon(surface, "info",
                            pygame.Rect(hint_rect.x, hint_rect.y + 3, 16, 16),
                            theme.color("accent" if is_mine else "text_mute"),
                            theme.color("shadow"))
            theme.draw_text(surface, theme.truncate(hint, self.fonts.small(),
                                                    hint_rect.width - 24),
                            self.fonts.small(),
                            theme.color("accent" if is_mine else "text_dim"),
                            (hint_rect.x + 22, hint_rect.y + 2))

        me = st.player(self.session.player_id) if self.session else None
        if me is not None and not me.bankrupt:
            self._draw_my_cards(surface, st, me, rect)

    def _refresh_action_buttons(self) -> None:
        """按当前状态刷新侧栏按钮与手牌。

        每帧都会调用，所以必须做「无变化就跳过」的判断，
        否则每帧新建一批对象会带来大量垃圾、拖慢帧率。
        """
        if self.session is None or self.roll_button is None:
            return
        st = self.session.state
        if st is None:
            return
        decision = self.session.decision
        can_roll = (decision is not None and decision.kind == DecisionKind.ROLL
                    and st.phase is GamePhase.WAIT_ROLL)
        if can_roll != self.roll_button.enabled:
            self.roll_button.set_enabled(can_roll)
            if can_roll:
                self.roll_button.tooltip = "空格键也可以掷骰"
            else:
                self.roll_button.tooltip = LocalInteraction.HINTS.get(
                    self.interaction_state(), "现在还不能掷骰")

        me = st.player(self.session.player_id)
        cards = tuple(me.cards[:5]) if me is not None and not me.bankrupt else ()
        phase_key = (st.phase.value, st.turn_rolled, can_roll,
                     st.debt is not None, st.pending_decision.id if st.pending_decision else "")
        if cards == self._card_tiles_key and phase_key == getattr(self, "_card_tiles_phase", None):
            return
        self._card_tiles_key = cards
        self._card_tiles_phase = phase_key

        tiles: list[CardTile] = []
        reasons: dict[str, str] = {}
        if me is not None and not me.bankrupt and self._card_defs is not None:
            for i, card_id in enumerate(cards):
                card = self._card_defs.get(card_id)
                if card is None:
                    continue
                ok, reason = can_use(st, me, card)
                reasons[card_id] = reason if not ok else ""
                tiles.append(CardTile(
                    card_button_rect(i), card_id, card.name, card.icon,
                    TIMING_LABEL.get(card.timing, ""), card.description, ok,
                    reason or "当前状态不满足使用条件"))
        self.card_tiles = tiles
        self._card_reasons = reasons

    def _draw_my_cards(self, surface: pygame.Surface, st: GameState, me,
                       panel: pygame.Rect) -> None:
        limit = int(st.rules.get("max_cards_per_player", 5))
        title_rect = pygame.Rect(panel.x + 16, panel.y + 186, panel.width - 32, 20)
        theme.draw_text(surface, f"我的道具（{len(me.cards)}/{limit}）",
                        self.fonts.sized(theme.FONT["tiny"], True),
                        theme.color("text_dim"), (title_rect.x, title_rect.y))
        usable = sum(1 for t in self.card_tiles if t.enabled)
        if self.card_tiles:
            theme.draw_text(surface, f"现在可用 {usable} 张", self.fonts.micro(),
                            theme.color("success" if usable else "text_mute"),
                            (title_rect.right, title_rect.y + 2), anchor="topright")
        if not me.cards:
            theme.draw_text(surface, "暂无道具 · 机遇事件或商店可以获得",
                            self.fonts.micro(), theme.color("text_mute"),
                            (CARD_ORIGIN[0], CARD_ORIGIN[1] + 14))
            return

        for i, tile in enumerate(self.card_tiles):
            rect = tile.rect
            if tile.enabled:
                fill = theme.color("panel_hi")
                border = theme.color("accent")
            else:
                fill = theme.color("bg_alt")
                border = theme.color("border_soft")
            theme.rounded_rect(surface, rect, fill, radius=theme.RADIUS["sm"])
            theme.rounded_rect(surface, rect, None, radius=theme.RADIUS["sm"],
                               border=border, border_width=2 if tile.hovered else 1)
            icon_col = theme.color("accent" if tile.enabled else "text_mute")
            icons.draw_icon(surface, tile.icon,
                            pygame.Rect(rect.x + 6, rect.y + 6, 22, 22), icon_col,
                            theme.color("shadow"))
            # 时机标记 + 快捷键
            badge = pygame.Rect(rect.right - 20, rect.y + 5, 14, 14)
            pygame.draw.circle(surface, theme.color("bg" if tile.enabled else "bg_alt"),
                               badge.center, 7)
            theme.draw_text(surface, str(i + 1), self.fonts.micro(),
                            theme.color("text" if tile.enabled else "text_mute"),
                            badge.center, anchor="center")
            name_font = self.fonts.sized(11, True)
            text_w = rect.width - 14 - (14 if not tile.enabled else 0)
            theme.draw_text(surface, theme.truncate(tile.name, name_font, text_w),
                            name_font,
                            theme.color("text" if tile.enabled else "text_mute"),
                            (rect.x + 7, rect.bottom - 17))
            if not tile.enabled:
                # 不可用的卡压一层灰 + 右侧一把小锁，一眼看出现在打不出去
                theme.rounded_rect(surface, rect, theme.color("bg", 90),
                                   radius=theme.RADIUS["sm"])
                icons.draw_icon(surface, "lock",
                                pygame.Rect(rect.right - 17, rect.bottom - 19, 13, 13),
                                theme.color("warning"), theme.color("shadow"))

    def _draw_log(self, surface: pygame.Surface) -> None:
        rect = self.log_panel_rect
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 16, rect.y + 12, rect.width - 22, 22),
                             "本局记录", icon="book", note="点这里看全部")
        # 整块标题区域都可点击 → 打开完整记录（可筛选、按轮分组）
        self.log_button.rect = pygame.Rect(rect.x + 10, rect.y + 8, rect.width - 20, 32)
        pygame.draw.line(surface, theme.color("border_soft"),
                         (rect.x + 12, rect.y + 44), (rect.right - 12, rect.y + 44), 1)
        self.log_list.draw(surface, self.fonts)

    # ---------------------------------------------------------------- 提示

    def _draw_tooltip(self, surface: pygame.Surface, st: GameState,
                      mouse: tuple[int, int]) -> None:
        text = ""
        if self.board_view is not None and self.board_view.hover_index is not None:
            idx = self.board_view.hover_index
            prop = st.property_at(idx)
            if prop is not None:
                text = property_tooltip(st, prop)
            else:
                tile = st.board.tile(idx)
                text = f"{tile.name}\n{tile.type.label}" + (f"\n{tile.note}" if tile.note else "")
                if st.barrier_at(idx):
                    text += "\n注意：此处有路障，经过的玩家会被拦下"
        elif self.hover_player is not None and self.hover_player < len(st.players):
            text = self.player_panel.tooltip_for(
                self.hover_player, st.players,
                lambda pid: st.properties_of(pid), self._card_defs)
        if self._card_tooltip and any(t.hovered for t in self.card_tiles):
            text = self._card_tooltip
        if text:
            draw_tooltip(surface, self.fonts, text, mouse, bounds=(SCREEN_W, SCREEN_H))

    def _draw_modal_tooltip(self, surface: pygame.Surface, mouse: tuple[int, int]) -> None:
        """弹窗里禁用按钮的原因也要能看到，否则玩家会不知道为什么点不动。"""
        modal = self.modal
        if modal is None:
            return
        for button in getattr(modal, "buttons", []):
            if button.rect.collidepoint(mouse) and button.tooltip:
                text = button.tooltip
                if not button.enabled:
                    text = f"不可用：{text}"
                draw_tooltip(surface, self.fonts, text, mouse, bounds=(SCREEN_W, SCREEN_H))
                return

    # ---------------------------------------------------------------- 调试

    def _draw_debug(self, surface: pygame.Surface, st: GameState) -> None:
        lines = [
            f"FPS {self.app.clock.get_fps():5.1f}",
            f"phase    {st.phase.value}",
            f"revision {st.revision}",
            f"round    {st.round_number}  turn {st.turn_number}",
            f"current  {st.current_player_id}",
            f"decision {(st.pending_decision.kind + ' → ' + st.pending_decision.player_id) if st.pending_decision else '-'}",
            f"seed     {st.seed} (rng {st.rng_counter})",
            f"ledger   {len(st.ledger.entries)} 条 (seq {st.ledger._seq})",
            f"hash     {st.canonical_hash()}",
            f"mode     {'单机/房主' if self.session and self.session.engine else '客户端'}",
            f"net      {self.app.net_status()}",
        ]
        rect = pygame.Rect(BOARD_RECT.x + 8, BOARD_RECT.bottom - 8 - len(lines) * 17, 470,
                           len(lines) * 17 + 12)
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill(theme.color("bg", 215))
        surface.blit(layer, rect.topleft)
        theme.rounded_rect(surface, rect, None, radius=6, border=theme.color("border"), border_width=1)
        y = rect.y + 6
        for line in lines:
            theme.draw_text(surface, line, self.fonts.micro(), theme.color("success"),
                            (rect.x + 8, y))
            y += 17
