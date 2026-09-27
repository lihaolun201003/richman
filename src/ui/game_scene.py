"""对局场景：棋盘、HUD、决策交互、动画、结算。

设计要点：
- 通过 SessionView 抽象统一「单机引擎」与「局域网客户端」，
  场景本身不关心状态是本地算出来的还是网络传来的；
- UI 只发送 Command，绝不直接修改状态；
- 弹窗出现时吞掉所有输入，杜绝穿透点击；
- 动画只影响显示，不参与规则计算。
"""
from __future__ import annotations

import math
import time
from typing import Any

import pygame

from ..audio.manager import audio
from ..game import economy
from ..game.cards import CardRegistry, can_use, valid_targets
from ..game.commands import Command, CommandType, DecisionKind, PendingDecision
from ..game.events import EventType
from ..game.format import money
from ..game.phases import GamePhase
from ..game.player import STATUS_LABELS
from ..game.state import GameState
from ..game.tile import TileType
from ..game import victory
from ..persistence import savegame
from . import theme
from .animations import (
    AnimationManager,
    CardFlipAnimation,
    DiceRollAnimation,
    FloatingText,
    PieceMoveAnimation,
    draw_die,
)
from .asset_panel import AssetPanel
from .board_view import BoardView
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
from .property_panel import PropertyInfoPanel, property_tooltip
from .scene import Scene
from .toast import ToastManager
from .widgets import Button, IconButton, Label, Panel, ScrollList, draw_tooltip

SCREEN_W = 1600
SCREEN_H = 900

#: 各区域的布局（棋盘 11×9 网格，cell 由 BoardView 自适应居中）
BOARD_RECT = pygame.Rect(24, 88, 980, 782)
SIDE_RECT = pygame.Rect(1024, 88, 552, 782)
PANEL_RECT = pygame.Rect(20, 12, 1348, 68)
ROUND_RECT = pygame.Rect(1382, 12, 198, 68)

#: 侧栏三段的高度
ACTION_H = 252
INFO_H = 200
LOG_GAP = 12

#: 侧栏内控件的固定位置（绘制与命中判定共用，保证不会错位）
ROLL_BUTTON_RECT = pygame.Rect(SIDE_RECT.x + 16, SIDE_RECT.y + 82, 200, 56)
DICE_PREVIEW_RECT = pygame.Rect(SIDE_RECT.x + 232, SIDE_RECT.y + 82, 152, 56)
CARD_W, CARD_H, CARD_GAP = 72, 34, 6
CARD_ORIGIN = (SIDE_RECT.x + 14, SIDE_RECT.y + 196)


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


class SessionView:
    """把「单机引擎」与「局域网客户端」统一成同一个只读视图。"""

    def __init__(self, app: Any, engine: Any = None, client: Any = None,
                 my_player_id: str = "", host: Any = None) -> None:
        self.app = app
        self.engine = engine
        self.client = client
        self.host = host
        self.my_player_id = my_player_id
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
        self.toasts = ToastManager()
        self.modal: Modal | None = None
        self.buttons: list[Button] = []
        self.card_buttons: list[Button] = []
        self.roll_button: Button | None = None
        self._card_tooltip: str = ""
        self.tile_index_clicked: int | None = None
        self.hover_tile: int | None = None
        self.hover_player: int | None = None
        self._last_seen_seq = 0
        self._last_phase = None
        self._last_current = None
        self._move_anim: PieceMoveAnimation | None = None
        self._move_player: str | None = None
        self._dice_anim: DiceRollAnimation | None = None
        self._card_defs: CardRegistry | None = None
        self._char_names: dict[str, str] = {}
        self._paused = False
        self._autosave_turn = -999
        self._autosave_time = 0.0
        self._game_over_shown = False
        self._pending_card_id: str | None = None
        self._pending_debt_reopen = False
        self._card_buttons_key: tuple = ()
        self._card_buttons_roll = False
        self._last_state_error = ""
        self._log_cache: list[Any] = []
        self._log_seq = -1
        self._floated_events: set[str] = set()

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
        self._last_seen_seq = 0
        self._game_over_shown = False
        self._log_seq = -1
        self._floated_events.clear()
        self.anim.clear()
        self.modal = None
        self._paused = False
        self._build_buttons()

    def _build_buttons(self) -> None:
        self.buttons = [
            Button(pygame.Rect(1330, 22, 76, 44), "资产", on_click=self.open_asset_panel,
                   style="secondary", font_size=16, tooltip="管理地产：升级 / 出售 / 抵押（I）"),
            Button(pygame.Rect(1414, 22, 76, 44), "图鉴", on_click=self._open_help,
                   style="secondary", font_size=16, tooltip="规则与道具图鉴（H）"),
            Button(pygame.Rect(1494, 22, 72, 44), "菜单", on_click=self._open_pause,
                   style="secondary", font_size=16, tooltip="暂停 / 菜单（ESC）"),
        ]
        self.roll_button = Button(
            ROLL_BUTTON_RECT, "掷骰子", on_click=self._try_roll, style="accent",
            enabled=False, tooltip="空格键也可以掷骰")
        self.buttons.append(self.roll_button)
        self._refresh_action_buttons()

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

        # 消息提示
        for msg in self.session.drain_messages():
            self.toasts.push(msg, "info")

        self._sync_animations(st, dt)
        self.anim.set_speed(self.app.settings.animation_speed)
        self.anim.update(dt)
        self.toasts.update(dt)
        super().update(dt)

        if self.modal is not None:
            self.modal.update(dt)
            if self.modal.done:
                self.modal = None
                if getattr(self, '_pending_debt_reopen', False):
                    self._pending_debt_reopen = False
                    self._maybe_open_decision(st)
            return

        self._refresh_action_buttons()
        self._handle_new_events(st)
        self._maybe_open_decision(st)
        self._maybe_show_game_over(st)
        self._maybe_autosave(st)

        mouse = pygame.mouse.get_pos()
        self._update_hover(st, mouse)

        if self._paused:
            return
        # 键盘快捷键
        self._update_log(st)

    def _update_log(self, st: GameState) -> None:
        if st.event_seq == self._log_seq:
            return
        self._log_seq = st.event_seq
        colors = {
            EventType.PROPERTY_BOUGHT: "success",
            EventType.RENT_PAID: "warning",
            EventType.PASSED_START: "accent",
            EventType.CHANCE_DRAWN: "info",
            EventType.BANKRUPT: "danger",
            EventType.GAME_OVER: "accent",
            EventType.CARD_USED: "info",
            EventType.TURN_START: "text",
            EventType.JAIL_ENTERED: "warning",
            EventType.DEBT_STARTED: "danger",
            EventType.BONUS_POOL_GAINED: "success",
        }
        items = []
        for ev in st.event_log[-120:]:
            color = colors.get(ev.type, "text_dim")
            prefix = ""
            if ev.type == EventType.TURN_START:
                prefix = "▍"
            items.append((f"{prefix}{ev.message}", color))
        self.log_list.set_items(items)
        self.log_list.scroll = self.log_list.max_scroll

    def _sync_animations(self, st: GameState, dt: float) -> None:
        """根据阶段变化启动/结束表现动画。"""
        # 骰子
        if st.phase is GamePhase.ROLLING and st.dice is not None:
            if self._dice_anim is None and self._last_phase is not GamePhase.ROLLING:
                center = self.board_view.inner_rect.center if self.board_view else (600, 400)
                # duration 用「游戏时间」：加速由 AnimationManager 统一处理，
                # 这里再除一次动画速度会导致骰子动画一闪而过。
                self._dice_anim = DiceRollAnimation(
                    center, 78, (st.dice.die1, st.dice.die2),
                    duration=max(0.5, st.phase_duration),
                    show_total=not st.dice.from_jail,
                )
                self.anim.add(self._dice_anim)
                audio.play_sfx("dice")
        if st.phase is not GamePhase.ROLLING:
            self._dice_anim = None

        # 棋子移动
        if st.phase is GamePhase.MOVING and st.move_path and self.board_view is not None:
            if self._move_anim is None or self._move_player != st.move_player_id:
                per_tile = st.phase_duration / max(1, len(st.move_path))
                self._move_anim = PieceMoveAnimation(
                    list(st.move_path), self.board_view.tile_centers(), per_tile)
                if self._move_player != st.move_player_id:
                    self._move_anim.tween.elapsed = 0.0
                    self._move_anim.tween.done = False
                    self.anim.add(self._move_anim)
                self._move_player = st.move_player_id
                audio.play_sfx("move")
        else:
            if self._move_anim is not None and st.phase is not GamePhase.MOVING:
                self._move_anim = None
                self._move_player = None

        if st.phase is not self._last_phase:
            if st.phase is GamePhase.RESOLVE_TILE:
                target = st.current_player.position if st.current_player else None
                if self.board_view is not None:
                    self.board_view.highlight_index = target
            if st.phase is GamePhase.WAIT_ROLL and st.current_player_id != self._last_current:
                audio.play_sfx("turn")
            self._last_phase = st.phase
            self._last_current = st.current_player_id

    def _handle_new_events(self, st: GameState) -> None:
        """处理新增事件：浮字、机遇弹窗。"""
        new_events = [e for e in st.event_log if e.seq > self._last_seen_seq]
        if not new_events:
            return
        self._last_seen_seq = new_events[-1].seq
        for ev in new_events:
            self._present_event(st, ev)

    def _present_event(self, st: GameState, ev) -> None:
        data = ev.data or {}
        # 浮字
        if data.get("float") and self.board_view is not None:
            target_id = data.get("float_target") or ev.player_id
            player = st.player(target_id)
            if player is not None and ev.event_id not in self._floated_events:
                self._floated_events.add(ev.event_id)
                pos = self.board_view.tile_center(player.position)
                amount = int(data.get("amount", 0))
                if ev.type == EventType.PASSED_START or ev.type == EventType.BONUS_POOL_GAINED:
                    self.anim.float_text(f"+{money(amount)}", (pos[0], pos[1] - 26),
                                         "success", 26)
                    audio.play_sfx("coin")
                elif ev.type in (EventType.RENT_PAID, EventType.TAX_PAID):
                    self.anim.float_text(f"-{money(amount)}", (pos[0], pos[1] - 26),
                                         "danger", 26)
                    audio.play_sfx("rent")
                elif ev.type == EventType.PROPERTY_BOUGHT:
                    self.anim.float_text(f"-{money(amount)}", (pos[0], pos[1] - 26),
                                         "warning", 24)
                    audio.play_sfx("buy")
                elif ev.type == EventType.PROPERTY_UPGRADED:
                    self.anim.float_text("升级！", (pos[0], pos[1] - 26), "accent", 22)
                    audio.play_sfx("upgrade")

        # 机遇卡弹窗
        if ev.type == EventType.CHANCE_DRAWN:
            detail = ""
            for later in st.event_log:
                if later.seq > ev.seq and later.type == EventType.CHANCE_APPLIED:
                    detail = later.data.get("detail", "")
                    break
            card_rect = pygame.Rect(0, 0, 420, 300)
            self.anim.add(CardFlipAnimation(
                card_rect, ev.data.get("name", "机遇"),
                ev.data.get("description", ""), accent="chance"))
            audio.play_sfx("chance")

        if ev.type == EventType.BANKRUPT:
            audio.play_sfx("bankrupt")
        if ev.type == EventType.JAIL_ENTERED:
            audio.play_sfx("jail")

    def _update_hover(self, st: GameState, mouse: tuple[int, int]) -> None:
        if self.board_view is None:
            return
        idx = self.board_view.hit_test(mouse)
        self.board_view.hover_index = idx
        self.hover_tile = idx
        self.prop_panel.set_tile(st, idx)
        self.hover_player = self.player_panel.hit_test(mouse)

    #: 自动存档的最小间隔（秒）与最小回合间隔 —— 每回合存一次既慢又没意义
    AUTOSAVE_INTERVAL_SEC = 20.0
    AUTOSAVE_TURNS = 4

    def _maybe_autosave(self, st: GameState) -> None:
        """单机模式：定期自动存档（节流，避免每回合都写盘）。"""
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
            # 正在选道具目标，先让道具流程走完
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
        if kind == DecisionKind.JAIL:
            player = st.player(decision.player_id)
            if player is not None:
                extra.append(f"你的现金：{player.money:,}")
        if kind == DecisionKind.BUY_PROPERTY:
            player = st.player(decision.player_id)
            if player is not None:
                extra.append(f"你的现金：{player.money:,}")

        self.modal = DecisionDialog(decision, self._on_decision_choice,
                                    accent=accent, extra_lines=extra)

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
        can_restart = self.session is not None and self.session.engine is not None
        self.modal = GameOverDialog(
            ranking, st.round_number, stats,
            on_again=self._restart,
            on_exit=self._back_to_lobby,
            can_restart=can_restart,
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
        from .asset_panel import AssetPanel

        st = self.session.state if self.session else None
        if st is None:
            return
        player = self._my_player()
        if player is None:
            return

        title = "我的资产"
        note = ""
        if debt_mode and st.debt:
            amount = int(st.debt["amount"])
            shortfall = max(0, amount - player.money)
            title = f"债务处理 · 还需 {money(shortfall)}"
            note = (f"欠款 {money(amount)}（{st.debt.get('reason', '')}）"
                    f" · 现金 {money(player.money)}")
        self.modal = AssetPanel(
            st, player, self._asset_action,
            on_close=self._on_asset_panel_closed,
            title=title, allow_sell=True, note=note,
            on_declare=(self._declare_bankruptcy if debt_mode and st.debt else None),
        )

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

        from ..game import economy as econ

        prop = st.properties.get(str(property_id))
        if prop is None:
            return False
        if action == "upgrade":
            cost, _ = econ.upgrade_cost(st, player, prop)
            if prop.is_max_level or player.money < cost:
                return False
        elif action == "mortgage":
            ok, _ = econ.can_mortgage(prop)
            if not ok:
                return False
        elif action == "redeem":
            if player.money < econ.redeem_cost(prop):
                return False

        self.session.submit(cmd, {"property_id": property_id})
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
        from ..game.cards import TIMING_LABEL
        from .dialogs import ShopDialog

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
        if self.modal is not None:
            self.modal.handle_event(event)
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._open_pause()
                return
            if event.key == pygame.K_F1:
                self.app.settings.set("ui", "debug_overlay",
                                      not self.app.settings.debug_overlay)
                return
            if event.key == pygame.K_SPACE:
                self._try_roll()
                return
            if event.key == pygame.K_i:
                if self.interaction_state() != LocalInteraction.DEBT:
                    self.open_asset_panel()
                return
            if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
                self._use_card_by_index(event.key - pygame.K_1)
                return
            if event.key == pygame.K_m:
                self.board_view_zoom = not getattr(self, "board_view_zoom", False)

        if event.type == pygame.MOUSEWHEEL:
            if self.log_list.rect.collidepoint(pygame.mouse.get_pos()):
                self.log_list.handle_event(event)
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.board_view is not None:
                idx = self.board_view.hit_test(event.pos)
                if idx is not None:
                    self.tile_index_clicked = idx
                    self.prop_panel.set_tile(self.session.state, idx)
                    return

        for button in self.buttons:
            if button.handle_event(event):
                return
        for button in self.card_buttons:
            if button.handle_event(event):
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
            self.toasts.push(f"已保存到 saves/{path.split(chr(92))[-1].split('/')[-1]}",
                             "success")
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
        if self.session is None or self._card_defs is None:
            return
        st = self.session.state
        if st is None:
            return
        player = st.player(self.session.player_id)
        if player is None or index >= len(player.cards):
            return
        self._start_card_use(player.cards[index])

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

        # 构造目标列表
        pairs: list[tuple[Any, str]] = []
        if card.needs_target == "tile":
            for idx in targets:
                tile = st.board.tile(idx)
                prop = st.property_at(idx)
                label = tile.name
                if prop is not None:
                    if prop.owner_id is None:
                        label += f"（无主 {prop.price:,}）"
                    elif prop.owner_id == player.id:
                        label += f"（你的 {prop.level}级）"
                    else:
                        owner = st.player(prop.owner_id)
                        label += f"（{owner.name if owner else '?'}）"
                pairs.append((idx, label))
        elif card.needs_target == "player":
            for pid in targets:
                other = st.player(str(pid))
                if other is not None:
                    pairs.append((pid, f"{other.name}（现金 {other.money:,}）"))
        elif card.needs_target == "own_property":
            for pid in targets:
                prop = st.properties.get(str(pid))
                if prop is not None:
                    pairs.append((pid, f"{prop.name} {prop.level}→{prop.level + 1} 级"))
        elif card.needs_target == "dice_value":
            from .animations import DICE_PIPS

            for value in targets:
                landing = (player.position + int(value)) % st.board.tile_count
                pairs.append((value, f"{value} 点 → {st.board.tile(landing).name}"))

        if not pairs:
            self.toasts.push("没有可用目标", "warning")
            return

        self._pending_card_id = card_id
        self.modal = CardTargetDialog(
            card.name, card.description, pairs,
            on_pick=lambda target: self._confirm_card(card, target),
            on_cancel=self._cancel_card,
            columns=1 if card.needs_target == "tile" else 2,
        )

    def _cancel_card(self) -> None:
        self._pending_card_id = None

    def _confirm_card(self, card, target: Any) -> None:
        if self.session is None:
            return
        self._pending_card_id = None
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

        # 动画层
        self.anim.draw(surface, self.fonts)

        # Tooltip
        mouse = pygame.mouse.get_pos()
        if self.app.settings.show_tooltips and self.modal is None:
            self._draw_tooltip(surface, st, mouse)

        self._draw_phase_banner(surface, st)

        if self.app.settings.debug_overlay:
            self._draw_debug(surface, st)

        self.toasts.draw(surface, self.fonts, center_x=800, bottom_y=884)

        if self.modal is not None:
            self.modal.draw(surface, self.fonts)
            if self.app.settings.show_tooltips:
                self._draw_modal_tooltip(surface, mouse)

    # ---------------------------------------------------------------- 顶栏

    def _draw_topbar(self, surface: pygame.Surface, st: GameState) -> None:
        owners = {p.id: st.owned_count(p.id) for p in st.players}
        self.player_panel.draw(
            surface, self.fonts, st.players, st.current_player_id, owners,
            self.anim.pulse.value, self._char_names, self._card_defs)

        # 轮次信息
        info = ROUND_RECT
        theme.rounded_rect(surface, info, theme.color("panel"), radius=12)
        theme.rounded_rect(surface, info, None, radius=12,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, f"第 {st.round_number} 轮", self.fonts.h3(),
                        theme.color("accent"), (info.centerx, info.y + 14), anchor="midtop")
        theme.draw_text(surface, st.phase.label, self.fonts.tiny(), theme.color("text_dim"),
                        (info.centerx, info.y + 40), anchor="midtop")

    # ---------------------------------------------------------------- 棋盘

    def _draw_board(self, surface: pygame.Surface, st: GameState) -> None:
        bv = self.board_view
        if bv is None:
            return
        owner_colors = {
            p.id: theme.hex_to_rgb(_color_of(p.color_id)) for p in st.players
        }
        bv.draw_background(surface)
        bv.draw_tiles_dynamic(surface, self.fonts, st.properties, st.barriers, owner_colors)
        bv.draw_hover(surface)
        if st.phase is not GamePhase.MOVING:
            bv.draw_highlight(surface, bv.highlight_index, self.anim.pulse.value)

        # 中央信息
        inner = bv.inner_rect
        self._draw_center_info(surface, st, inner)

        # 棋子
        self._draw_pieces(surface, st, bv)

        # 点击过的地块描边
        if self.tile_index_clicked is not None:
            rect = bv.tile_rect(self.tile_index_clicked)
            theme.rounded_rect(surface, rect.inflate(-2, -2), None, radius=10,
                               border=theme.color("primary"), border_width=3)

    def _draw_center_info(self, surface: pygame.Surface, st: GameState,
                          inner: pygame.Rect) -> None:
        """中央区域：当前行动提示 / 奖金池 / 等待提示。"""
        cur = st.current_player
        if cur is None:
            return

        # 大号当前回合提示（阶段横幅占 inner.y+12..46）
        y = inner.y + 118
        is_my = self.session.is_mine(cur.id) if self.session else False
        head = "该你行动了" if is_my else f"{cur.name} 的回合"
        color_name = "accent" if is_my else "text_dim"
        theme.draw_text(surface, head, self.fonts.sized(30, True), theme.color(color_name),
                        (inner.centerx, y), anchor="midtop")
        y += 44
        sub = "（本机操作）" if is_my else ""
        if sub:
            theme.draw_text(surface, sub, self.fonts.small(),
                            theme.color("text_mute"), (inner.centerx, y), anchor="midtop")

        # 奖金池
        if st.bonus_pool > 0:
            pool = pygame.Rect(inner.centerx - 120, inner.bottom - 78, 240, 44)
            theme.rounded_rect(surface, pool, theme.color("bg_alt", 220), radius=20)
            theme.rounded_rect(surface, pool, None, radius=20,
                               border=theme.color("accent"), border_width=2)
            theme.draw_text(surface, f"奖金池 {st.bonus_pool:,}", self.fonts.body(),
                            theme.color("accent"), pool.center, anchor="center")

        # 等待他人提示
        watching = self.session.watching_decision if self.session else None
        if watching is not None and not (self.session.is_mine(watching.player_id)):
            waiter = st.player(watching.player_id)
            if waiter is not None:
                rect = pygame.Rect(inner.centerx - 200, inner.y + 210, 400, 50)
                theme.rounded_rect(surface, rect, theme.color("bg_alt", 210), radius=14)
                theme.rounded_rect(surface, rect, None, radius=14,
                                   border=theme.color("border"), border_width=1)
                dots = "·" * (1 + int(self.anim.pulse.value * 3))
                theme.draw_text(surface, f"等待 {waiter.name} 操作 {dots}", self.fonts.body(),
                                theme.color("text_dim"), rect.center, anchor="center")

    def _phase_hint(self, st: GameState, cur) -> str:
        phase = st.phase
        if phase is GamePhase.WAIT_ROLL:
            if cur.in_jail:
                return "在看守所中，需要决定如何离开"
            return "点击右侧「掷骰子」或按空格开始行动"
        if phase is GamePhase.ROLLING:
            return "骰子滚动中…"
        if phase is GamePhase.MOVING:
            return "移动中…"
        if phase is GamePhase.WAIT_DECISION:
            return "等待决策"
        if phase is GamePhase.JAIL_DECISION:
            return "看守所决策中"
        if phase is GamePhase.GAME_OVER:
            return "对局已结束"
        return ""

    def _draw_pieces(self, surface: pygame.Surface, st: GameState,
                     bv: BoardView) -> None:
        """绘制所有棋子，同格自动错位。"""
        # 计算每格上的玩家
        by_tile: dict[int, list[Any]] = {}
        for p in st.players:
            if p.bankrupt:
                continue
            by_tile.setdefault(p.position, []).append(p)

        moving_id = st.move_player_id if st.phase is GamePhase.MOVING else None

        for tile_index, players in by_tile.items():
            players = sorted(players, key=lambda x: x.slot)
            n = len(players)
            center = bv.tile_center(tile_index)
            # 棋子半径：单人时最大，同格人数越多越紧凑，但保证相邻两颗不相交
            radius = max(9, int(bv.cell * 0.155))
            if n > 2:
                radius = max(8, int(radius * 0.86))
            if n > 4:
                radius = max(7, int(radius * 0.9))
            ring = 0 if n == 1 else min(bv.cell * 0.30, radius * 0.72 * n)
            for i, player in enumerate(players):
                if player.id == moving_id and self._move_anim is not None:
                    continue  # 移动中的棋子单独画
                if n == 1:
                    offset = (0, -int(bv.cell * 0.16))
                else:
                    angle = (i / n) * math.tau - math.pi / 2
                    offset = (int(math.cos(angle) * ring), int(math.sin(angle) * ring) + 2)
                self._draw_one_piece(surface, st, player,
                                     (center[0] + offset[0], center[1] + offset[1]),
                                     radius, bv.cell)

        # 移动中的棋子
        if moving_id is not None and self._move_anim is not None:
            player = st.player(moving_id)
            if player is not None:
                start = bv.tile_center(self._move_anim.path[0] if self._move_anim.path
                                       else player.position)
                from_center = bv.tile_center(st.move_from)
                pos = self._move_anim.current_center(from_center)
                move_radius = max(9, int(bv.cell * 0.155))
                self._draw_one_piece(surface, st, player, pos, move_radius, bv.cell,
                                     moving=True)

    def _draw_one_piece(self, surface: pygame.Surface, st: GameState, player,
                        pos: tuple[float, float], radius: int, cell: int,
                        moving: bool = False) -> None:
        x, y = int(pos[0]), int(pos[1])
        col = theme.hex_to_rgb(_color_of(player.color_id))
        is_current = player.id == st.current_player_id

        # 当前玩家光环 + 箭头
        if is_current:
            pulse = self.anim.pulse.value
            r = int(radius + 7 + 4 * pulse)
            halo = pygame.Surface((r * 2 + 8, r * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(halo, theme.color("accent", int(60 + 60 * pulse)),
                               (r + 4, r + 4), r)
            surface.blit(halo, (x - r - 4, y - r - 4))
            # 头顶箭头
            ay = y - radius - 16 - int(4 * pulse)
            pygame.draw.polygon(surface, theme.color("accent"),
                                [(x, ay + 12), (x - 9, ay), (x + 9, ay)])
            pygame.draw.polygon(surface, theme.color("accent_dark"),
                                [(x, ay + 12), (x - 9, ay), (x + 9, ay)], 2)

        shadow = pygame.Surface((radius * 2 + 6, radius * 2 + 6), pygame.SRCALPHA)
        pygame.draw.circle(shadow, (0, 0, 0, 90), (radius + 3, radius + 5), radius)
        surface.blit(shadow, (x - radius - 3, y - radius - 3))

        pygame.draw.circle(surface, col, (x, y), radius)
        pygame.draw.circle(surface, theme.lighten(col, 0.35), (x - radius // 3, y - radius // 3),
                           max(2, radius // 3))
        pygame.draw.circle(surface, theme.darken(col, 0.4), (x, y), radius, 2)

        # 编号（颜色之外的第二重标识）
        theme.draw_text(surface, str(player.slot + 1), self.fonts.micro(), (255, 255, 255),
                        (x, y), anchor="center")

        # 状态图标
        icons = []
        if player.in_jail:
            icons.append("锁")
        if player.disconnected:
            icons.append("断")
        for i, icon in enumerate(icons[:2]):
            theme.draw_text(surface, icon, self.fonts.micro(), theme.color("warning"),
                            (x + radius - 2, y - radius + i * 11), anchor="center")

    # ---------------------------------------------------------------- 右栏

    def _draw_side(self, surface: pygame.Surface, st: GameState) -> None:
        self._draw_action_panel(surface, st)
        self.prop_panel.draw(surface, self.fonts, st)
        self._draw_log(surface)

    def _draw_action_panel(self, surface: pygame.Surface, st: GameState) -> None:
        rect = pygame.Rect(SIDE_RECT.x, SIDE_RECT.y, SIDE_RECT.width, ACTION_H)
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        cur = st.current_player
        theme.draw_text(surface, "当前行动", self.fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 12))

        if cur is None:
            return
        is_mine = self.session.is_mine(cur.id) if self.session else False

        # 当前玩家名
        col = theme.hex_to_rgb(_color_of(cur.color_id))
        pygame.draw.circle(surface, col, (rect.x + 28, rect.y + 52), 12)
        theme.draw_text(surface, theme.truncate(cur.name, self.fonts.h3(), 200),
                        self.fonts.h3(), theme.color("text"), (rect.x + 48, rect.y + 40))
        theme.draw_text(surface, f"{cur.money:,}", self.fonts.h3(), theme.color("accent"),
                        (rect.right - 16, rect.y + 40), anchor="topright")

        # ---- 掷骰按钮（持久化对象，位置与命中判定共用同一组常量）
        self.roll_button.draw(surface, self.fonts)

        # 骰子预览
        preview = DICE_PREVIEW_RECT
        theme.rounded_rect(surface, preview, theme.color("bg_alt"), radius=12)
        if st.dice is not None:
            d1 = pygame.Rect(0, 0, 34, 34)
            d1.center = (preview.x + 44, preview.centery)
            d2 = pygame.Rect(0, 0, 34, 34)
            d2.center = (preview.x + 86, preview.centery)
            draw_die(surface, d1, st.dice.die1)
            draw_die(surface, d2, st.dice.die2)
            theme.draw_text(surface, f"{st.dice.total}", self.fonts.h3(),
                            theme.color("accent"), (preview.right - 26, preview.centery),
                            anchor="center")
        else:
            theme.draw_text(surface, "— —", self.fonts.h2(), theme.color("text_mute"),
                            preview.center, anchor="center")

        # ---- 自己 / 旁人提示
        hint_rect = pygame.Rect(rect.x + 16, rect.y + 148, rect.width - 32, 26)
        if is_mine:
            hint = self._phase_hint(st, cur)
            color_name = "accent"
        else:
            hint = f"等待 {cur.name} 行动"
            color_name = "text_dim"
        theme.draw_text(surface, theme.truncate(hint, self.fonts.small(), hint_rect.width),
                        self.fonts.small(), theme.color(color_name),
                        (hint_rect.x, hint_rect.y))

        # ---- 我的道具
        me = st.player(self.session.player_id) if self.session else None
        if me is not None and not me.bankrupt:
            self._draw_my_cards(surface, st, me, rect)

    def _refresh_action_buttons(self) -> None:
        """按当前状态刷新侧栏按钮。

        每帧都会调用，所以必须做「无变化就跳过」的判断：
        否则每帧新建一批 Button 对象会带来大量垃圾，拖慢帧率。
        """
        if self.session is None or self.roll_button is None:
            return
        st = self.session.state
        if st is None:
            return
        decision = self.session.decision
        can_roll = (decision is not None and decision.kind == DecisionKind.ROLL
                    and st.phase is GamePhase.WAIT_ROLL)
        self.roll_button.set_enabled(can_roll)

        me = st.player(self.session.player_id)
        cards = tuple(me.cards[:5]) if me is not None and not me.bankrupt else ()
        # 手牌与可用性都没变时直接复用上一帧的按钮
        if cards == self._card_buttons_key and can_roll == self._card_buttons_roll:
            return
        self._card_buttons_key = cards
        self._card_buttons_roll = can_roll

        buttons: list[Button] = []
        if me is not None and not me.bankrupt and self._card_defs is not None:
            for i, card_id in enumerate(cards):
                card = self._card_defs.get(card_id)
                enabled = bool(card) and can_use(st, me, card)[0]
                buttons.append(Button(
                    card_button_rect(i), "",
                    on_click=(lambda cid=card_id: self._start_card_use(cid)),
                    enabled=enabled, style="ghost"))
        self.card_buttons = buttons

    def _draw_my_cards(self, surface: pygame.Surface, st: GameState, me,
                       panel: pygame.Rect) -> None:
        theme.draw_text(surface, f"我的道具（{len(me.cards)}/5）", self.fonts.tiny(),
                        theme.color("text_dim"), (panel.x + 16, panel.y + 178))
        if not me.cards:
            theme.draw_text(surface, "暂无道具，机遇事件中可能获得",
                            self.fonts.micro(), theme.color("text_mute"),
                            (CARD_ORIGIN[0], CARD_ORIGIN[1] + 4))
            return

        mouse = pygame.mouse.get_pos()
        self._card_tooltip = ""
        for i, card_id in enumerate(me.cards[:5]):
            card = self._card_defs.get(card_id) if self._card_defs else None
            name = card.name if card else card_id
            rect = card_button_rect(i)
            enabled = bool(card) and can_use(st, me, card)[0]
            theme.rounded_rect(surface, rect,
                               theme.color("panel_hi") if enabled else theme.color("bg_alt"),
                               radius=8)
            theme.rounded_rect(surface, rect, None, radius=8,
                               border=theme.color("accent" if enabled else "border_soft"),
                               border_width=1)
            theme.draw_text(surface, theme.truncate(name, self.fonts.micro(), CARD_W - 10),
                            self.fonts.micro(),
                            theme.color("text") if enabled else theme.color("text_mute"),
                            rect.center, anchor="center")
            if rect.collidepoint(mouse):
                theme.rounded_rect(surface, rect, None, radius=8,
                                   border=theme.color("accent"), border_width=2)
                if card is not None:
                    self._card_tooltip = (f"{card.name}\n{card.description}\n"
                                          f"（点击或按 {i + 1} 使用）")

    def _draw_log(self, surface: pygame.Surface) -> None:
        rect = self.log_panel_rect
        theme.rounded_rect(surface, rect, theme.color("panel"), radius=14)
        theme.rounded_rect(surface, rect, None, radius=14,
                           border=theme.color("border_soft"), border_width=1)
        theme.draw_text(surface, "事件日志", self.fonts.h3(), theme.color("text"),
                        (rect.x + 16, rect.y + 10))
        pygame.draw.line(surface, theme.color("border_soft"),
                         (rect.x + 12, rect.y + 42), (rect.right - 12, rect.y + 42), 1)
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
            owner_counts = st.owned_count(st.players[self.hover_player].id)
            text = self.player_panel.tooltip_for(
                self.hover_player, st.players,
                lambda pid: st.properties_of(pid), self._card_defs)
        card_tip = getattr(self, "_card_tooltip", "")
        if card_tip and any(b.rect.collidepoint(mouse) for b in self.card_buttons):
            text = card_tip
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

    def _draw_phase_banner(self, surface: pygame.Surface, st: GameState) -> None:
        """把当前阶段画在棋盘中央区域顶部。

        不能画在棋盘外——顶部已经有玩家面板，画上去会互相遮挡。
        """
        if self.modal is not None or self.board_view is None:
            return
        inner = self.board_view.inner_rect
        text = st.phase.label
        font = self.fonts.body()
        width = font.size(text)[0] + 44
        rect = pygame.Rect(0, 0, width, 34)
        rect.midtop = (inner.centerx, inner.y + 12)
        theme.rounded_rect(surface, rect, theme.color("bg_alt", 225), radius=17)
        theme.rounded_rect(surface, rect, None, radius=17,
                           border=theme.color("accent"), border_width=2)
        theme.draw_text(surface, text, font, theme.color("accent"),
                        rect.center, anchor="center")

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
            f"hash     {st.canonical_hash()}",
            f"mode     {'单机/房主' if self.session and self.session.engine else '客户端'}",
            f"net      {self.app.net_status()}",
        ]
        rect = pygame.Rect(BOARD_RECT.x + 8, BOARD_RECT.bottom - 8 - len(lines) * 17, 460,
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
