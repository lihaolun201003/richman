"""游戏引擎：唯一的规则权威。

Host（以及单机）持有一个 GameEngine 实例；客户端永远不运行本引擎，
只接收状态快照并发送 Command。

引擎对外只暴露三件事：
    update(dt)            —— 由主循环驱动，推进阶段机
    submit_command(cmd)   —— 校验并执行玩家意图
    drain_events()        —— 取出需要广播/展示的游戏事件
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from ..utils.logging_setup import get_logger
from . import bankruptcy, cards as cards_mod, chance as chance_mod, economy, jail, movement, victory
from .commands import (
    Command,
    CommandResult,
    CommandType,
    DecisionKind,
    DecisionOption,
    PendingDecision,
)
from .events import (
    EventType,
    money,
    msg_buy,
    msg_buy_discount,
    msg_card_used,
    msg_chance,
    msg_chance_detail,
    msg_dice,
    msg_game_over,
    msg_game_start,
    msg_jail_auto_pay,
    msg_jail_enter,
    msg_jail_fail,
    msg_jail_pay,
    msg_jail_roll_ok,
    msg_land_start,
    msg_move,
    msg_pass_start,
    msg_pool_gain,
    msg_rent,
    msg_rent_discount,
    msg_rent_double,
    msg_rent_free,
    msg_roll_again,
    msg_skip_buy,
    msg_skip_turn,
    msg_steal,
    msg_tax,
    msg_tax_pool,
    msg_turn_start,
    msg_upgrade,
    msg_upgrade_free,
)
from .phases import DECISION_PHASES, GamePhase, phase_duration
from .player import (
    STATUS_FIXED_DICE,
    STATUS_FREE_RENT,
    STATUS_PROTECTED,
    STATUS_PURCHASE_DISCOUNT,
    STATUS_RENT_DISCOUNT_ONCE,
    STATUS_RENT_DOUBLE_ONCE,
    STATUS_SKIP_TURN,
    STATUS_UPGRADE_DISCOUNT,
    StatusEffect,
)
from .state import GameState
from .tile import TileType

log = get_logger(__name__)

#: 每格移动的基础耗时（秒，1.0x 动画速度）
MOVE_TILE_SEC = 0.17


class GameEngine:
    """一局游戏的驱动器。"""

    def __init__(
        self,
        state: GameState,
        chance_registry: chance_mod.ChanceRegistry,
        card_registry: cards_mod.CardRegistry,
        anim_speed: float = 1.0,
    ) -> None:
        self.state = state
        self.chance_registry = chance_registry
        self.card_registry = card_registry
        self.anim_speed = max(0.25, float(anim_speed))
        self.controllers: dict[str, Any] = {}
        self.outbox: list[Any] = []
        self.dirty = True
        self.processed_commands: dict[str, int] = {}   # command_id -> 处理时的 revision
        self.listener: Callable[[Any], None] | None = None
        self.paused = False
        self.state.rules.setdefault("move_tile_sec", MOVE_TILE_SEC)

    # ================================================================ 生命周期

    def bind_controller(self, player_id: str, controller: Any) -> None:
        self.controllers[player_id] = controller

    def controller_of(self, player_id: str) -> Any:
        return self.controllers.get(player_id)

    def start(self) -> None:
        """初始化并进入开局流程。"""
        st = self.state
        rules = st.rules
        start_index = st.board.start_index()

        for p in st.players:
            p.position = start_index
            base = int(rules.get("starting_money", 15000))
            bonus = int(p.perk_value("start_money_bonus"))
            p.money = base + bonus
            p.stats = {k: 0 for k in p.stats}
            p.stats["start_money"] = p.money
            p.status_effects.clear()
        st.bonus_pool = int(rules.get("bonus_pool_base", 0))
        st.bankrupt_counter = 0
        st.round_number = 1
        st.turn_number = 0
        st.current_player_id = st.players[0].id if st.players else None
        st.game_over = False
        st.winner_id = None
        chance_mod.build_deck(st, self.chance_registry)

        st.log(EventType.GAME_START, msg_game_start(st.board.name),
               None, {"board": st.board.map_id, "seed": st.seed})

        # 初始道具：每人 2 张，让道具系统从第一轮就能用起来
        rng = st.next_rng()
        pool = self.card_registry.ids()
        limit = int(rules.get("max_cards_per_player", 5))
        dealt = int(rules.get("starting_cards", 2))
        if pool and dealt > 0:
            for p in st.players:
                for _ in range(dealt):
                    cid = pool[rng.randrange(len(pool))]
                    p.add_card(cid, limit)
                names = [self.card_registry.require(c).name for c in p.cards]
                if names:
                    st.log(EventType.CARD_GAINED, f"{p.name} 获得初始道具：{'、'.join(names)}",
                           p.id, {"cards": list(p.cards)})

        self._enter_phase(GamePhase.GAME_SETUP)
        st.bump()
        self.dirty = True

    def update(self, dt: float) -> None:
        """推进一帧。dt 为真实秒数；动画速度只影响演出，不影响规则。"""
        st = self.state
        if self.paused or st.game_over:
            return
        dt = max(0.0, min(dt, 0.25))
        st.clock += dt

        # 1) 有决策待处理 → 询问对应控制器
        if st.pending_decision is not None:
            ctrl = self.controllers.get(st.pending_decision.player_id)
            if ctrl is not None:
                try:
                    cmd = ctrl.poll(st.pending_decision, st, self, dt)
                except Exception as exc:  # 控制器异常不能拖垮整局
                    log.exception("控制器异常: %s", exc)
                    cmd = None
                if cmd is not None:
                    self.submit_command(cmd)
                    return
            # 防挂机：真人长时间不操作时自动执行保守选项，避免整局卡死
            st.decision_timer += dt
            timeout = float(st.rules.get("decision_timeout_sec", 180))
            if timeout > 0 and st.decision_timer >= timeout:
                self._auto_resolve_decision()
            return

        # 2) 自动阶段按时间推进
        if st.phase_duration > 0:
            st.phase_timer += dt * self.anim_speed
            if st.phase_timer >= st.phase_duration:
                st.phase_timer = st.phase_duration
                self._on_phase_finished()
            return

        # 3) 兜底：决策阶段却没有决策，说明流程丢了，强制推进避免卡局
        if st.pending_decision is None and st.phase in DECISION_PHASES:
            log.warning("决策阶段缺少决策，强制推进：%s", st.phase.value)
            st.log(EventType.INFO, f"流程修正：{st.phase.value} 阶段无待处理决策")
            self._enter_phase(GamePhase.TURN_END)

    # ================================================================ 命令入口

    def submit_command(self, cmd: Command) -> CommandResult:
        """校验并执行命令。所有非法输入在这里被拒绝。"""
        st = self.state

        if cmd.command_id and cmd.command_id in self.processed_commands:
            return CommandResult(False, "重复命令已忽略")

        try:
            result = self._dispatch(cmd)
        except Exception as exc:  # 规则实现异常不应导致进程崩溃
            log.exception("命令执行失败 %s: %s", cmd, exc)
            result = CommandResult(False, f"命令执行异常：{exc}")
            st.log(EventType.INFO, f"命令执行异常：{cmd.type}")

        if cmd.command_id:
            self.processed_commands[cmd.command_id] = st.revision
            if len(self.processed_commands) > 500:
                for k in list(self.processed_commands.keys())[:200]:
                    del self.processed_commands[k]
        return result

    def _dispatch(self, cmd: Command) -> CommandResult:
        st = self.state
        if st.game_over:
            return CommandResult(False, "游戏已结束")

        player = st.player(cmd.player_id)
        if player is None:
            return CommandResult(False, "玩家不存在")
        if player.bankrupt:
            return CommandResult(False, "你已破产退出")

        # 需要先回答当前决策
        if st.pending_decision is not None:
            return self._answer_decision(cmd, player)

        # 无决策时的合法命令：投降 / 使用道具（自己回合）
        if cmd.type == CommandType.SURRENDER:
            return self._surrender(player)
        if cmd.type == CommandType.USE_CARD:
            return self._use_card_command(player, cmd)
        if cmd.type == CommandType.END_TURN:
            if st.current_player_id == player.id and st.phase in (GamePhase.WAIT_ROLL, GamePhase.WAIT_DECISION):
                self._enter_phase(GamePhase.TURN_END)
                return CommandResult(True)
            return CommandResult(False, "当前不能结束回合")
        return CommandResult(False, f"当前阶段不接受命令：{cmd.type}")

    # ---------------------------------------------------------------- 决策应答

    def _answer_decision(self, cmd: Command, player) -> CommandResult:
        st = self.state
        decision = st.pending_decision
        assert decision is not None

        if cmd.type == CommandType.RESOLVE_DECISION:
            if cmd.decision_id and cmd.decision_id != decision.id:
                return CommandResult(False, "该问题已过期")
            if cmd.player_id != decision.player_id:
                return CommandResult(False, "这不是你的决策")
            option_id = str(cmd.payload.get("option_id", ""))
            opt = decision.option(option_id)
            if opt is None:
                return CommandResult(False, f"无效选项：{option_id}")
            if not opt.enabled:
                return CommandResult(False, "该选项当前不可用")
            inner = Command(
                ctype=opt.command_type,
                player_id=cmd.player_id,
                payload=dict(opt.payload),
                decision_id=decision.id,
            )
            return self._dispatch_decision_option(inner, decision)

        # 语义化命令：在该决策的选项中寻找匹配项
        if cmd.player_id != decision.player_id:
            return CommandResult(False, "这不是你的决策")

        # 自己回合的决策窗口里可以随时使用道具（不消耗决策本身）
        if cmd.type == CommandType.USE_CARD:
            return self._use_card_command(player, cmd, context=decision.context)

        match = None
        for opt in decision.options:
            if opt.command_type == cmd.type:
                if opt.payload and cmd.payload:
                    merged = dict(opt.payload)
                    merged.update(cmd.payload)
                    opt = DecisionOption(opt.id, opt.label, opt.command_type, merged,
                                         opt.enabled, opt.hint, opt.danger)
                match = opt
                break
        if match is None:
            return CommandResult(False, f"当前问题不接受命令：{cmd.type}")
        if not match.enabled:
            return CommandResult(False, "该选项当前不可用")
        return self._dispatch_decision_option(cmd, decision)

    def _dispatch_decision_option(self, cmd: Command, decision: PendingDecision) -> CommandResult:
        """执行选项对应的实际逻辑。

        关键约束：如果执行失败，必须把决策放回去，
        否则会出现「决策没了、阶段还停在等待决策」的死锁。
        """
        st = self.state
        st.pending_decision = None
        try:
            result = self._execute_decision_option(cmd, decision)
        except Exception:
            if st.pending_decision is None:
                st.pending_decision = decision
            raise
        if not result.ok and st.pending_decision is None:
            st.pending_decision = decision
        return result

    def _execute_decision_option(self, cmd: Command, decision: PendingDecision) -> CommandResult:
        st = self.state
        ctype = cmd.type
        actor = st.require_player(decision.player_id)

        # ---- 掷骰
        if ctype == CommandType.ROLL_DICE:
            if decision.kind != DecisionKind.ROLL:
                return CommandResult(False, "当前不是掷骰时机")
            return self._do_roll(actor, from_jail=False)

        if ctype == CommandType.ROLL_FOR_JAIL:
            if decision.kind != DecisionKind.JAIL:
                return CommandResult(False, "当前不是看守所决策")
            return self._do_roll(actor, from_jail=True)

        if ctype == CommandType.PAY_JAIL:
            if decision.kind != DecisionKind.JAIL:
                return CommandResult(False, "当前不是看守所决策")
            return self._do_pay_jail(actor)

        # ---- 地产
        if ctype == CommandType.BUY_PROPERTY:
            if decision.kind != DecisionKind.BUY_PROPERTY:
                return CommandResult(False, "当前没有可购买的地产")
            return self._do_buy(actor, cmd.payload.get("property_id"))

        if ctype == CommandType.SKIP_PROPERTY:
            if decision.kind == DecisionKind.BUY_PROPERTY:
                prop = st.properties.get(str(decision.context.get("property_id", "")))
                if prop is not None:
                    st.log(EventType.PROPERTY_SKIPPED,
                           msg_skip_buy(actor.name, prop.name), actor.id,
                           {"property": prop.id})
            self._enter_phase(GamePhase.TURN_END)
            return CommandResult(True)

        if ctype == CommandType.UPGRADE_PROPERTY:
            if decision.kind != DecisionKind.UPGRADE_PROPERTY:
                return CommandResult(False, "当前不能升级")
            return self._do_upgrade(
                actor,
                cmd.payload.get("property_id") or decision.context.get("property_id"),
                free=bool(cmd.payload.get("free")),
            )

        # ---- 道具目标（自己回合使用道具；决策保留给引擎重新询问）
        if ctype == CommandType.USE_CARD:
            st.pending_decision = decision
            return self._use_card_command(actor, cmd, context=decision.context)

        # ---- 事件效果选择目标（消耗该决策）
        if ctype == CommandType.SELECT_TARGET:
            return self._do_select_target(actor, cmd.payload, decision)

        if ctype == CommandType.CONFIRM_EVENT:
            self._enter_phase(GamePhase.TURN_END)
            return CommandResult(True)

        if ctype == CommandType.SELL_ASSET:
            st.pending_decision = decision
            return self._do_sell(actor, cmd.payload.get("property_id"))

        if ctype == CommandType.DECLARE_BANKRUPTCY:
            self._enter_phase(GamePhase.TURN_END)
            return CommandResult(True)

        if ctype == CommandType.END_TURN:
            self._enter_phase(GamePhase.TURN_END)
            return CommandResult(True)

        if ctype == CommandType.SURRENDER:
            return self._surrender(actor)

        return CommandResult(False, f"未知决策命令：{ctype}")

    def _do_select_target(self, player, payload: dict, decision: PendingDecision) -> CommandResult:
        """处理机遇事件里的「选择目标」决策。"""
        st = self.state
        action = str(payload.get("action", decision.context.get("action", "")))
        amount = int(payload.get("amount", decision.context.get("amount", 0)))

        if st.pending_effect is None:
            self._enter_phase(GamePhase.TURN_END)
            return CommandResult(True)

        target_id = payload.get("target_id")
        if not target_id:
            st.log(EventType.INFO, f"{player.name} 放弃了「{action}」效果", player.id)
            self._after_resolve()
            return CommandResult(True)

        target = st.player(str(target_id))
        if target is None or target.bankrupt:
            return CommandResult(False, "目标无效")
        if target.has_status(STATUS_PROTECTED):
            return CommandResult(False, f"{target.name} 处于保护状态")

        if action == "demand":
            actual = min(amount, target.money)
            if actual <= 0:
                st.log(EventType.INFO, f"{target.name} 没有现金，{player.name} 索要失败", player.id)
            else:
                target.money -= actual
                player.money += actual
                player.stats["money_earned"] = player.stats.get("money_earned", 0) + actual
                st.log(EventType.INFO,
                       f"{player.name} 向 {target.name} 索取 {money(actual)}", player.id,
                       {"amount": actual, "target": target.id,
                        "float": True, "float_target": player.id})
        else:
            st.log(EventType.INFO, f"{player.name} 对 {target.name} 施放了效果", player.id)

        self._after_resolve()
        return CommandResult(True)

    # ================================================================ 阶段推进

    def _enter_phase(self, phase: GamePhase, duration: float | None = None) -> None:
        st = self.state
        # 游戏一旦结束，任何后续流程都不得再改动阶段
        if st.game_over and phase is not GamePhase.GAME_OVER:
            return
        if duration is None:
            duration = phase_duration(phase)
        st.set_phase(phase, duration)
        st.bump()
        self.dirty = True

    def _on_phase_finished(self) -> None:
        st = self.state
        phase = st.phase

        if phase is GamePhase.GAME_SETUP:
            self._begin_turn()
        elif phase is GamePhase.TURN_START:
            self._wait_roll()
        elif phase is GamePhase.ROLLING:
            self._begin_moving()
        elif phase is GamePhase.MOVING:
            self._finish_moving()
        elif phase is GamePhase.ARRIVED:
            st.set_phase(GamePhase.RESOLVE_TILE, phase_duration(GamePhase.RESOLVE_TILE))
            self._resolve_tile()
        elif phase is GamePhase.RESOLVE_TILE:
            self._after_resolve()
        elif phase is GamePhase.APPLY_EVENT:
            self._after_resolve()
        elif phase is GamePhase.TURN_END:
            self._end_turn()
        else:
            # 决策阶段不应自动结束
            self._enter_phase(GamePhase.TURN_END)

    # ---------------------------------------------------------------- 回合

    def _begin_turn(self) -> None:
        st = self.state
        cur = st.current_player
        if cur is None:
            self._check_game_over()
            return
        st.turn_number += 1
        st.turn_rolled = False
        st.doubles_count = 0
        st.chance_depth = 0
        cur.stats["turns_played"] = cur.stats.get("turns_played", 0) + 1

        st.log(EventType.TURN_START, msg_turn_start(cur.name, st.round_number), cur.id,
               {"round": st.round_number, "turn": st.turn_number})

        # 跳过回合状态
        if cur.has_status(STATUS_SKIP_TURN):
            cur.consume_status(STATUS_SKIP_TURN)
            st.log(EventType.INFO, msg_skip_turn(cur.name), cur.id)
            self._enter_phase(GamePhase.TURN_END)
            return

        self._enter_phase(GamePhase.TURN_START)

    def _wait_roll(self) -> None:
        st = self.state
        cur = st.current_player
        if cur is None:
            self._check_game_over()
            return

        # 看守所决策
        if cur.in_jail:
            if jail.is_auto_release(st, cur):
                self._do_pay_jail(cur, auto=True)
                return
            fee = jail.jail_fee(st, cur)
            need = jail.jail_needed_roll(st)
            options = [
                DecisionOption(
                    "pay", f"支付保释金 {money(fee)}", CommandType.PAY_JAIL,
                    enabled=cur.money >= fee,
                    hint="立即离开看守所" if cur.money >= fee else "现金不足",
                ),
                DecisionOption(
                    "roll", f"掷骰尝试（{need} 点以上）", CommandType.ROLL_FOR_JAIL,
                    hint="免费，靠运气",
                ),
            ]
            left = max(0, jail.jail_max_turns(st) - cur.jail_turns)
            self._make_decision(
                cur.id, DecisionKind.JAIL, "看守所：你要怎么离开？",
                options, description=f"关押中，剩余 {left} 回合后强制付费释放",
                context={"fee": fee, "need": need},
            )
            st.set_phase(GamePhase.JAIL_DECISION, 0.0)
            st.bump()
            return

        hint = "点击骰子开始行动"
        if st.turn_rolled:
            hint = "本回合已掷过骰子"
        options = [
            DecisionOption("roll", "掷骰子", CommandType.ROLL_DICE, hint=hint),
        ]
        self._make_decision(
            cur.id, DecisionKind.ROLL,
            f"轮到 {cur.name}", options,
            description=hint,
            context={"doubles": st.doubles_count},
        )
        st.set_phase(GamePhase.WAIT_ROLL, 0.0)
        st.bump()

    def _end_turn(self) -> None:
        st = self.state
        cur = st.current_player

        # 回合结束前统一清算负现金（机遇事件可能把玩家扣成负数）
        for p in st.active_players():
            if self._settle_negative_cash(p):
                break
        if st.game_over:
            return

        if cur is not None:
            expired = cur.tick_statuses()
            for s in expired:
                st.log(EventType.INFO, f"{cur.name} 的状态「{s}」已结束", cur.id)

        # 额外回合（机遇给的机会）
        if st.extra_turns > 0:
            st.extra_turns -= 1
            st.turn_rolled = False
            if cur is not None:
                st.log(EventType.INFO, f"{cur.name} 获得额外回合", cur.id)
            self._wait_roll()
            return

        self._advance_player()
        self._check_game_over()
        if not st.game_over:
            self._begin_turn()

    def _advance_player(self) -> None:
        st = self.state
        order = [p.id for p in st.players]
        n = len(order)
        if n == 0:
            return
        cur_i = order.index(st.current_player_id) if st.current_player_id in order else 0
        for k in range(1, n + 1):
            j = (cur_i + k) % n
            cand = st.player(order[j])
            if cand is None or cand.bankrupt:
                continue
            if j <= cur_i:
                st.round_number += 1
                st.total_rounds_played += 1
                st.tick_barriers()
            st.current_player_id = cand.id
            return

    # ---------------------------------------------------------------- 掷骰

    def _do_roll(self, player, from_jail: bool = False) -> CommandResult:
        st = self.state
        if player.id != st.current_player_id:
            return CommandResult(False, "还没轮到你")

        forced: int | None = None
        eff = player.get_status(STATUS_FIXED_DICE)
        if eff is not None:
            forced = int(eff.payload.get("value", 6))
            player.consume_status(STATUS_FIXED_DICE)
            st.log(EventType.INFO, f"{player.name} 使用指定骰子，点数 {forced}", player.id)

        dice = _roll_dice_checked(st, forced)
        st.dice = dice
        st.turn_rolled = True
        st.move_source = "jail" if from_jail else "dice"

        if from_jail:
            st.log(EventType.DICE_ROLLED,
                   f"{player.name} 在看守所掷出 {dice.total} 点", player.id, dice.to_dict())
        else:
            st.log(EventType.DICE_ROLLED, msg_dice(player.name, dice.die1, dice.die2),
                   player.id, dice.to_dict())

        # 关押中掷骰：够点数则出狱，否则留在原地
        if from_jail:
            need = jail.jail_needed_roll(st)
            if dice.total >= need:
                jail.release_from_jail(player)
                st.log(EventType.JAIL_RELEASED, msg_jail_roll_ok(player.name, dice.total), player.id)
                after = "resolve"
            else:
                player.jail_turns += 1
                st.log(EventType.JAIL_ROLL_FAILED,
                       msg_jail_fail(player.name, dice.total, need), player.id)
                self._enter_phase(GamePhase.ROLLING, 1.0)
                st.move_after = "turn_end"
                st.move_path = []
                st.move_player_id = player.id
                return CommandResult(True)

        st.move_after = "resolve"

        # 连续双数三次 → 直接进看守所（限制无限连掷）
        if dice.is_double and not from_jail:
            st.doubles_count += 1
            if st.doubles_count >= 3:
                st.log(EventType.JAIL_ENTERED, msg_jail_enter(player.name), player.id)
                jail.put_in_jail(st, player)
                self._enter_phase(GamePhase.ROLLING, 1.0)
                st.move_after = "turn_end"
                st.move_path = []
                st.move_player_id = player.id
                return CommandResult(True)

        self._enter_phase(GamePhase.ROLLING, 1.15)
        return CommandResult(True)

    # ---------------------------------------------------------------- 移动

    def _begin_moving(self) -> None:
        st = self.state
        cur = st.current_player
        if cur is None:
            self._enter_phase(GamePhase.TURN_END)
            return

        if st.move_after == "turn_end" and not st.move_path:
            # 无需移动（监狱未出狱 / 押送已完成）
            self._enter_phase(GamePhase.TURN_END)
            return

        steps = st.dice.total if st.dice else 0
        if st.move_source == "chance" and st.move_path:
            steps = st.move_steps
        plan = movement.plan_move(st, cur.id, steps)
        st.move_from = plan.from_index
        st.move_path = plan.path
        st.move_steps = steps
        st.move_player_id = cur.id

        per_tile = float(st.rules.get("move_tile_sec", MOVE_TILE_SEC))
        duration = max(0.25, len(plan.path) * per_tile)
        self._enter_phase(GamePhase.MOVING, duration)

        if plan.barrier_index is not None:
            st.log(EventType.INFO,
                   f"{cur.name} 撞上路障，在「{st.board.tile(plan.final_index).name}」停下",
                   cur.id, {"barrier": plan.barrier_index})

    def _finish_moving(self) -> None:
        st = self.state
        cur = st.current_player
        if cur is None or not st.move_path:
            self._enter_phase(GamePhase.TURN_END)
            return

        path = list(st.move_path)
        dest = path[-1]
        cur.position = dest
        st.log(EventType.PLAYER_MOVED, msg_move(cur.name, st.move_steps,
                                                st.board.tile(dest).name), cur.id,
               {"from": st.move_from, "to": dest, "path": path, "steps": st.move_steps})

        # 经过起点奖励
        if st.move_steps > 0:
            self._maybe_start_bonus(cur, path, destination_is_start=(dest == st.board.start_index()))

        st.move_path = []
        self._enter_phase(GamePhase.ARRIVED)

    def _maybe_start_bonus(self, player, path: list[int], destination_is_start: bool) -> None:
        st = self.state
        start_index = st.board.start_index()
        passed = any(st.board.tile(i).type is TileType.START for i in path[:-1]) or destination_is_start
        if not passed:
            return
        amount = int(st.rules.get("pass_start_bonus", 2000))
        amount += int(player.perk_value("pass_start_bonus"))
        player.money += amount
        player.stats["start_passes"] = player.stats.get("start_passes", 0) + 1
        player.stats["money_earned"] = player.stats.get("money_earned", 0) + amount
        text = msg_land_start(player.name, amount) if destination_is_start else msg_pass_start(player.name, amount)
        st.log(EventType.PASSED_START, text, player.id, {"amount": amount, "float": True})

    # ---------------------------------------------------------------- 落点结算

    def _resolve_tile(self) -> None:
        st = self.state
        cur = st.current_player
        if cur is None:
            self._enter_phase(GamePhase.TURN_END)
            return
        tile = st.board.tile(cur.position)
        st.log(EventType.LANDED, f"{cur.name} 停在「{tile.name}」", cur.id,
               {"tile": tile.index, "type": tile.type.value})

        ttype = tile.type
        if ttype is TileType.START:
            self._enter_phase(GamePhase.TURN_END)
        elif ttype is TileType.CHANCE:
            self._draw_chance(cur)
        elif ttype is TileType.TAX:
            self._resolve_tax(cur, tile)
        elif ttype in (TileType.PARK, TileType.BONUS):
            self._resolve_bonus_pool(cur, tile)
        elif ttype is TileType.GO_TO_JAIL:
            self._send_to_jail(cur)
        elif ttype is TileType.JAIL:
            st.log(EventType.INFO, f"{cur.name} 只是路过看守所", cur.id)
            self._enter_phase(GamePhase.TURN_END)
        elif ttype.is_purchasable:
            self._resolve_property(cur, tile)
        else:
            self._enter_phase(GamePhase.TURN_END)

    def _resolve_property(self, player, tile) -> None:
        st = self.state
        prop = st.property_at(tile.index)
        if prop is None:
            self._enter_phase(GamePhase.TURN_END)
            return

        if prop.owner_id is None:
            self._offer_purchase(player, prop)
            return

        if prop.owner_id == player.id:
            self._offer_upgrade(player, prop)
            return

        owner = st.player(prop.owner_id)
        if owner is None or owner.bankrupt:
            self._enter_phase(GamePhase.TURN_END)
            return

        self._pay_rent(player, prop, owner)

    def _offer_purchase(self, player, prop) -> None:
        st = self.state
        price, detail = economy.buy_price(st, player, prop)
        affordable = player.money >= price
        discount_note = ""
        if detail.get("character") or detail.get("coupon"):
            discount_note = f"（折扣后 {money(price)}，原价 {money(detail['base'])}）"
        options = [
            DecisionOption(
                "buy", f"购买 {money(price)}", CommandType.BUY_PROPERTY,
                payload={"property_id": prop.id},
                enabled=affordable,
                hint="" if affordable else f"现金不足（还差 {money(price - player.money)}）",
            ),
            DecisionOption("skip", "放弃", CommandType.SKIP_PROPERTY,
                           payload={"property_id": prop.id}, hint="保留现金"),
        ]
        preview = economy.rent_preview(st, prop)
        desc = (
            f"{prop.district or '独立'} · 售价 {money(detail['base'])}" + discount_note +
            f"\n空地租金 {money(preview['current'])} → 满级租金 "
            f"{money(int(prop.rent_table[-1] * (st.district_bonus if prop.district else 1.0)))}"
        )
        self._make_decision(
            player.id, DecisionKind.BUY_PROPERTY,
            f"是否购买「{prop.name}」？", options,
            description=desc,
            context={"property_id": prop.id, "tile": prop.tile_index, "price": price},
        )
        st.set_phase(GamePhase.WAIT_DECISION, 0.0)
        st.bump()

    def _offer_upgrade(self, player, prop) -> None:
        st = self.state
        if prop.is_max_level:
            self._enter_phase(GamePhase.TURN_END)
            return
        cost, detail = economy.upgrade_cost(st, player, prop)
        affordable = player.money >= cost
        options = [
            DecisionOption(
                "upgrade", f"升级 {money(cost)}", CommandType.UPGRADE_PROPERTY,
                payload={"property_id": prop.id},
                enabled=affordable,
                hint="" if affordable else "现金不足",
            ),
            DecisionOption("skip", "暂不升级", CommandType.SKIP_PROPERTY,
                           payload={"property_id": prop.id}),
        ]
        preview = economy.rent_preview(st, prop)
        desc = (
            f"当前 {prop.level_name}（{prop.level} 级）租金 {money(preview['current'])}"
            f" → 升级后租金 {money(preview['next'] or 0)}"
        )
        self._make_decision(
            player.id, DecisionKind.UPGRADE_PROPERTY,
            f"升级自己的「{prop.name}」？", options,
            description=desc,
            context={"property_id": prop.id, "cost": cost},
        )
        st.set_phase(GamePhase.WAIT_DECISION, 0.0)
        st.bump()

    def _pay_rent(self, payer, prop, owner) -> None:
        st = self.state
        if owner.has_status(STATUS_PROTECTED) and False:
            pass
        amount, detail = economy.compute_rent_payment(st, payer, prop)

        if detail.get("free"):
            payer.consume_status(STATUS_FREE_RENT)
            st.log(EventType.RENT_FREE, msg_rent_free(payer.name, prop.name), payer.id,
                   {"property": prop.id, "float": True})
            self._enter_phase(GamePhase.TURN_END)
            return

        if amount <= 0:
            self._enter_phase(GamePhase.TURN_END)
            return

        # 消耗一次性状态
        if detail.get("doubled"):
            owner.consume_status(STATUS_RENT_DOUBLE_ONCE)
        if detail.get("halved"):
            payer.consume_status(STATUS_RENT_DISCOUNT_ONCE)

        before = payer.money
        out = bankruptcy.pay(st, payer, amount, creditor_id=owner.id, reason=f"{prop.name} 租金")
        paid = out.paid
        payer.stats["rent_paid"] = payer.stats.get("rent_paid", 0) + paid
        owner.stats["rent_income"] = owner.stats.get("rent_income", 0) + paid

        if detail.get("doubled"):
            text = msg_rent_double(payer.name, owner.name, prop.name, paid, detail["base"])
        elif detail.get("halved") or detail.get("payer_discount"):
            text = msg_rent_discount(payer.name, owner.name, prop.name, paid, detail["base"])
        else:
            text = msg_rent(payer.name, owner.name, prop.name, paid)

        st.log(EventType.RENT_PAID, text, payer.id, {
            "property": prop.id, "amount": paid, "owner": owner.id,
            "float": True, "float_target": payer.id, "detail": detail,
        })
        if out.liquidated:
            st.log(EventType.DEBT_STARTED,
                   f"{payer.name} 变卖了 {len(out.liquidated)} 处地产用于缴租", payer.id,
                   {"liquidated": [list(x) for x in out.liquidated]})

        self._enter_phase(GamePhase.TURN_END)

    def _resolve_tax(self, player, tile) -> None:
        st = self.state
        amount, label = economy.tax_amount(st, player, tile.type, tile.index)
        if amount <= 0:
            self._enter_phase(GamePhase.TURN_END)
            return
        out = bankruptcy.pay(st, player, amount, creditor_id=None, reason=label)
        player.stats["taxes_paid"] = player.stats.get("taxes_paid", 0) + out.paid
        st.log(EventType.TAX_PAID, msg_tax(player.name, label, out.paid), player.id,
               {"amount": out.paid, "label": label, "float": True, "float_target": player.id})
        # 税金一半注入奖金池
        pool_in = out.paid // 2
        if pool_in > 0:
            st.bonus_pool += pool_in
            st.log(EventType.BONUS_POOL_PAID, msg_tax_pool(label, pool_in), None,
                   {"amount": pool_in})
        self._enter_phase(GamePhase.TURN_END)

    def _resolve_bonus_pool(self, player, tile) -> None:
        st = self.state
        amount = st.bonus_pool
        if amount <= 0:
            st.log(EventType.INFO, f"{player.name} 停在「{tile.name}」，但奖金池是空的", player.id)
            self._enter_phase(GamePhase.TURN_END)
            return
        st.bonus_pool = 0
        player.money += amount
        player.stats["money_earned"] = player.stats.get("money_earned", 0) + amount
        st.log(EventType.BONUS_POOL_GAINED, msg_pool_gain(player.name, amount), player.id,
               {"amount": amount, "float": True, "float_target": player.id, "alert": True})
        self._enter_phase(GamePhase.TURN_END)

    def _send_to_jail(self, player) -> None:
        st = self.state
        jail.put_in_jail(st, player)
        st.log(EventType.JAIL_ENTERED, msg_jail_enter(player.name), player.id,
               {"alert": True, "tile": player.position})
        # 用一次短移动表现“被押送”
        st.move_from = player.position
        st.move_path = []
        st.move_player_id = player.id
        st.move_after = "turn_end"
        st.move_source = "jail"
        self._enter_phase(GamePhase.MOVING, 0.65)

    # ---------------------------------------------------------------- 机遇

    def _draw_chance(self, player) -> None:
        st = self.state
        if st.chance_depth >= 2:
            st.log(EventType.INFO, f"{player.name} 连续触发机遇，本次跳过抽卡", player.id)
            self._enter_phase(GamePhase.TURN_END)
            return

        card = chance_mod.draw(st, self.chance_registry)
        st.chance_depth += 1
        st.last_chance_id = card.id
        player.stats["chance_events"] = player.stats.get("chance_events", 0) + 1
        st.log(EventType.CHANCE_DRAWN, msg_chance(player.name, card.name), player.id,
               {"card": card.id, "name": card.name, "description": card.description,
                "rarity": card.rarity, "alert": True})

        followups, text = chance_mod.apply_effect(st, self.chance_registry, player, card.effect)
        st.last_chance_text = text
        st.log(EventType.CHANCE_APPLIED, msg_chance_detail(text), player.id,
               {"detail": text, "followups": followups})

        self._enter_phase(GamePhase.APPLY_EVENT, 0.55)
        st.pending_effect = {"followups": followups, "text": text}

    def _after_resolve(self) -> None:
        """APPLY_EVENT / RESOLVE_TILE 结束后的统一出口。"""
        st = self.state
        cur = st.current_player
        pending = st.pending_effect
        if cur is None or not pending:
            self._enter_phase(GamePhase.TURN_END)
            return

        followups = list(pending.get("followups") or [])
        st.pending_effect = None

        if not followups:
            self._check_debt_after_effect(cur)
            return

        action = followups[0].get("action")
        rest = followups[1:]
        st.pending_effect = {"followups": rest, "text": pending.get("text", "")}

        if action == chance_mod.FOLLOWUP_MOVE:
            self._start_effect_move(cur, int(followups[0].get("steps", 0)))
        elif action == chance_mod.FOLLOWUP_MOVE_TO_START:
            steps = -(st.board.distance_forward(cur.position, st.board.start_index()))
            steps = st.board.distance_forward(cur.position, st.board.start_index())
            self._start_effect_move(cur, steps)
        elif action == chance_mod.FOLLOWUP_TELEPORT:
            self._teleport(cur, int(followups[0].get("tile_index", 0)))
        elif action == chance_mod.FOLLOWUP_GO_JAIL:
            self._send_to_jail(cur)
        elif action == chance_mod.FOLLOWUP_ROLL_AGAIN:
            st.extra_turns += 1
            st.log(EventType.INFO, msg_roll_again(cur.name), cur.id)
            self._enter_phase(GamePhase.TURN_END)
        elif action == chance_mod.FOLLOWUP_FREE_UPGRADE:
            self._free_upgrade(cur)
        elif action == chance_mod.FOLLOWUP_CHOOSE_TARGET:
            self._ask_effect_target(cur, int(followups[0].get("amount", 0)),
                                    followups[0].get("reason", ""))
        else:
            self._after_resolve()

    def _check_debt_after_effect(self, player) -> None:
        """事件可能把玩家扣成负现金，这里补一次清算。"""
        self._settle_negative_cash(player)
        self._enter_phase(GamePhase.TURN_END)

    def _settle_negative_cash(self, player) -> bool:
        """负现金 → 走债务流程（变卖资产，仍不足则破产）。返回是否破产。"""
        st = self.state
        if player.money >= 0 or player.bankrupt:
            return False
        debt = -player.money
        st.log(EventType.DEBT_STARTED,
               f"{player.name} 现金不足，需要补足 {money(debt)}", player.id,
               {"amount": debt, "auto": True})
        out = bankruptcy.settle_negative(st, player, reason="强制清算")
        if out.liquidated:
            st.log(EventType.ASSET_LIQUIDATED,
                   f"{player.name} 变卖了 {len(out.liquidated)} 处地产用于抵债", player.id,
                   {"liquidated": [list(x) for x in out.liquidated]})
        if out.bankrupt:
            self._check_game_over()
            return True
        return False

    def _start_effect_move(self, player, steps: int) -> None:
        st = self.state
        if steps == 0:
            self._after_resolve()
            return
        st.move_source = "chance"
        st.move_steps = steps
        plan = movement.plan_move(st, player.id, steps)
        st.move_from = plan.from_index
        st.move_path = plan.path
        st.move_player_id = player.id
        st.move_after = "resolve"
        per_tile = float(st.rules.get("move_tile_sec", MOVE_TILE_SEC))
        self._enter_phase(GamePhase.MOVING, max(0.3, len(plan.path) * per_tile))

    def _teleport(self, player, tile_index: int) -> None:
        st = self.state
        tile_index = max(0, min(st.board.tile_count - 1, tile_index))
        old = player.position
        player.position = tile_index
        st.log(EventType.PLAYER_MOVED,
               f"{player.name} 被传送到「{st.board.tile(tile_index).name}」", player.id,
               {"from": old, "to": tile_index, "teleport": True})
        st.move_path = []
        st.move_after = "resolve"
        # 传送后直接结算落点（不再触发机遇抽卡，靠 chance_depth 控制）
        st.set_phase(GamePhase.RESOLVE_TILE, phase_duration(GamePhase.RESOLVE_TILE))
        st.bump()
        self._resolve_tile()

    def _free_upgrade(self, player) -> None:
        st = self.state
        owned = [p for p in st.properties_of(player.id) if not p.is_max_level]
        if not owned:
            st.log(EventType.INFO, f"{player.name} 没有可升级的地产，效果作废", player.id)
            self._enter_phase(GamePhase.TURN_END)
            return
        # 免费升级：选租金提升最大的一处
        owned.sort(key=lambda p: (p.next_rent or 0) - p.base_rent, reverse=True)
        prop = owned[0]
        prop.upgrade()
        player.stats["properties_upgraded"] = player.stats.get("properties_upgraded", 0) + 1
        st.log(EventType.PROPERTY_UPGRADED,
               f"{player.name} 免费把「{prop.name}」升到 {prop.level} 级", player.id,
               {"property": prop.id, "level": prop.level, "free": True})
        self._after_resolve()

    def _ask_effect_target(self, player, amount: int, reason: str) -> None:
        st = self.state
        targets = [p for p in st.other_active_players(player.id) if p.money > 0]
        if not targets:
            st.log(EventType.INFO, f"{player.name} 没有可以索取的对象，效果作废", player.id)
            self._enter_phase(GamePhase.TURN_END)
            return
        options = []
        for t in targets:
            options.append(DecisionOption(
                t.id, f"{t.name}（现金 {money(t.money)}）", CommandType.SELECT_TARGET,
                payload={"target_id": t.id, "action": "demand", "amount": amount},
            ))
        options.append(DecisionOption("skip", "放弃该效果", CommandType.SKIP_PROPERTY,
                                      payload={"effect_skip": True}))
        self._make_decision(
            player.id, DecisionKind.CARD_TARGET_PLAYER,
            f"{reason}：选择目标", options,
            description=f"从对手手中获得最多 {money(amount)}",
            context={"action": "demand", "amount": amount, "effect": True},
        )
        st.set_phase(GamePhase.WAIT_DECISION, 0.0)
        st.bump()

    # ---------------------------------------------------------------- 看守所

    def _do_pay_jail(self, player, auto: bool = False) -> CommandResult:
        st = self.state
        fee = jail.jail_fee(st, player)
        if player.money < fee and not auto:
            return CommandResult(False, "现金不足以支付保释金")
        out = bankruptcy.pay(st, player, fee, creditor_id=None, reason="保释金")
        jail.release_from_jail(player)
        text = msg_jail_auto_pay(player.name, out.paid) if auto else msg_jail_pay(player.name, out.paid)
        st.log(EventType.JAIL_PAID, text, player.id,
               {"amount": out.paid, "float": True, "float_target": player.id})
        self._enter_phase(GamePhase.TURN_END)
        return CommandResult(True)

    # ---------------------------------------------------------------- 购买/升级/出售

    def _do_buy(self, player, property_id: Any) -> CommandResult:
        st = self.state
        prop = st.properties.get(str(property_id or ""))
        if prop is None:
            return CommandResult(False, "地产不存在")
        if prop.owner_id is not None:
            return CommandResult(False, "该地产已有主人")

        price, detail = economy.buy_price(st, player, prop)
        if player.money < price:
            return CommandResult(False, "现金不足")

        player.money -= price
        player.stats["money_spent"] = player.stats.get("money_spent", 0) + price
        prop.assign(player.id)
        player.stats["properties_bought"] = player.stats.get("properties_bought", 0) + 1

        # 消耗折扣券
        if detail.get("coupon"):
            player.consume_status(STATUS_PURCHASE_DISCOUNT)

        if detail.get("character") or detail.get("coupon"):
            text = msg_buy_discount(player.name, prop.name, price, detail["base"])
        else:
            text = msg_buy(player.name, prop.name, price)
        st.log(EventType.PROPERTY_BOUGHT, text, player.id,
               {"property": prop.id, "price": price, "float": True, "float_target": player.id})

        # 买完全组 → 提示垄断
        if prop.district and st.district_owned_all(player.id, prop.district):
            st.log(EventType.INFO, f"{player.name} 垄断了「{prop.district}」，该区租金翻倍！",
                   player.id, {"district": prop.district, "alert": True})
        self._enter_phase(GamePhase.TURN_END)
        return CommandResult(True)

    def _do_upgrade(self, player, property_id: Any, free: bool = False) -> CommandResult:
        st = self.state
        prop = st.properties.get(str(property_id or ""))
        if prop is None:
            return CommandResult(False, "地产不存在")
        if prop.owner_id != player.id:
            return CommandResult(False, "这不是你的地产")
        if prop.is_max_level:
            return CommandResult(False, "已经满级")

        cost, detail = economy.upgrade_cost(st, player, prop)
        if free:
            cost = 0
        if player.money < cost:
            return CommandResult(False, "现金不足")

        player.money -= cost
        if cost:
            player.stats["money_spent"] = player.stats.get("money_spent", 0) + cost
        prop.upgrade()
        player.stats["properties_upgraded"] = player.stats.get("properties_upgraded", 0) + 1
        if detail.get("coupon"):
            player.consume_status(STATUS_UPGRADE_DISCOUNT)

        if free:
            text = msg_upgrade_free(player.name, prop.name, prop.level)
        else:
            text = msg_upgrade(player.name, prop.name, prop.level, cost)
        st.log(EventType.PROPERTY_UPGRADED, text, player.id,
               {"property": prop.id, "level": prop.level, "cost": cost,
                "float": True, "float_target": player.id})
        self._enter_phase(GamePhase.TURN_END)
        return CommandResult(True)

    def _do_sell(self, player, property_id: Any) -> CommandResult:
        st = self.state
        prop = st.properties.get(str(property_id or ""))
        if prop is None or prop.owner_id != player.id:
            return CommandResult(False, "这不是你的地产")
        refund = prop.sell_value
        prop.release()
        player.money += refund
        player.stats["money_earned"] = player.stats.get("money_earned", 0) + refund
        st.log(EventType.PROPERTY_SOLD, f"{player.name} 出售「{prop.name}」，回收 {money(refund)}",
               player.id, {"property": prop.id, "amount": refund})
        st.bump()
        return CommandResult(True)

    # ---------------------------------------------------------------- 道具卡

    def _use_card_command(self, player, cmd: Command, context: dict | None = None) -> CommandResult:
        card_id = str(cmd.payload.get("card_id", ""))
        card = self.card_registry.get(card_id)
        if card is None:
            return CommandResult(False, "未知道具")
        if card_id not in player.cards:
            return CommandResult(False, "你没有这张道具")
        ok, reason = cards_mod.can_use(self.state, player, card)
        if not ok:
            return CommandResult(False, reason)

        target = cmd.payload.get("target")
        if card.needs_target is not None and target is None:
            return CommandResult(False, "该道具需要选择目标")

        return self._apply_card(player, card, target)

    def _apply_card(self, player, card, target: Any) -> CommandResult:
        st = self.state
        eff = card.effect
        kind = eff.get("kind", "")
        player.remove_card(card.id)
        player.stats["cards_used"] = player.stats.get("cards_used", 0) + 1
        st.log(EventType.CARD_USED, msg_card_used(player.name, card.name), player.id,
               {"card": card.id, "float": True})

        if kind == "status":
            status = eff.get("status", "")
            duration = int(eff.get("duration", 1))
            payload = dict(eff.get("payload") or {})
            if status == STATUS_FIXED_DICE and target is not None:
                payload["value"] = int(target)
            player.add_status(StatusEffect(status, duration, payload=payload))
            st.log(EventType.INFO, f"{player.name} 获得状态「{card.name}」", player.id)

        elif kind == "gain_money":
            amount = int(eff.get("amount", 0))
            player.money += amount
            player.stats["money_earned"] = player.stats.get("money_earned", 0) + amount
            st.log(EventType.INFO, f"{player.name} 获得 {money(amount)}", player.id,
                   {"float": True, "float_target": player.id})

        elif kind == "teleport":
            self._teleport(player, int(target))
            return CommandResult(True)

        elif kind == "swap_position":
            other = st.player(str(target))
            if other is None or other.bankrupt:
                player.add_card(card.id)
                return CommandResult(False, "目标无效")
            old = player.position
            player.position, other.position = other.position, old
            st.log(EventType.PLAYER_MOVED,
                   f"{player.name} 与 {other.name} 交换了位置", player.id,
                   {"swap": True, "a": player.id, "b": other.id})

        elif kind == "steal":
            other = st.player(str(target))
            if other is None or other.bankrupt:
                player.add_card(card.id)
                return CommandResult(False, "目标无效")
            if other.has_status(STATUS_PROTECTED):
                player.add_card(card.id)
                return CommandResult(False, f"{other.name} 处于保护状态")
            amount = min(int(eff.get("amount", 800)), other.money)
            other.money -= amount
            player.money += amount
            player.stats["steals_done"] = player.stats.get("steals_done", 0) + 1
            st.log(EventType.INFO, msg_steal(player.name, other.name, amount), player.id,
                   {"amount": amount, "target": other.id})

        elif kind == "apply_status_to_target":
            other = st.player(str(target))
            if other is None or other.bankrupt:
                player.add_card(card.id)
                return CommandResult(False, "目标无效")
            if other.has_status(STATUS_PROTECTED):
                player.add_card(card.id)
                return CommandResult(False, f"{other.name} 处于保护状态")
            other.add_status(StatusEffect(eff.get("status", STATUS_SKIP_TURN),
                                          int(eff.get("duration", 1))))
            st.log(EventType.INFO, f"{player.name} 让 {other.name} 暂停一回合", player.id)

        elif kind == "place_barrier":
            idx = int(target)
            if idx == st.board.start_index():
                player.add_card(card.id)
                return CommandResult(False, "不能在起点放置路障")
            st.barriers[str(idx)] = int(eff.get("duration", 4))
            st.log(EventType.INFO,
                   f"{player.name} 在「{st.board.tile(idx).name}」放置了路障", player.id,
                   {"tile": idx})

        elif kind == "free_upgrade":
            prop = st.properties.get(str(target))
            if prop is None or prop.owner_id != player.id or prop.is_max_level:
                player.add_card(card.id)
                return CommandResult(False, "目标地产无效")
            prop.upgrade()
            player.stats["properties_upgraded"] = player.stats.get("properties_upgraded", 0) + 1
            st.log(EventType.PROPERTY_UPGRADED,
                   f"{player.name} 用道具免费把「{prop.name}」升到 {prop.level} 级", player.id,
                   {"property": prop.id, "level": prop.level})

        st.bump()
        return CommandResult(True)

    # ---------------------------------------------------------------- 投降

    def _surrender(self, player) -> CommandResult:
        st = self.state
        if player.bankrupt:
            return CommandResult(False, "你已经退出了")
        player.bankrupt = True
        st.bankrupt_counter += 1
        player.bankrupt_order = st.bankrupt_counter
        jail.release_from_jail(player)
        for prop in st.properties_of(player.id):
            prop.release()
        player.cards.clear()
        st.log(EventType.SURRENDERED, f"{player.name} 主动认输，退出游戏", player.id,
               {"alert": True})
        st.pending_decision = None
        self._check_game_over()
        if not st.game_over:
            self._advance_player()
            self._begin_turn()
        st.bump()
        return CommandResult(True)

    # ---------------------------------------------------------------- 结束

    def _check_game_over(self) -> bool:
        st = self.state
        if st.game_over:
            return True
        winner = victory.check_winner(st) or victory.check_max_rounds(st)
        if winner is None:
            return False
        st.winner_id = winner
        st.game_over = True
        st.ended_at = __import__("time").time()
        st.pending_decision = None
        w = st.player(winner)
        name = w.name if w else "无人"
        st.log(EventType.GAME_OVER, msg_game_over(name, st.round_number), winner,
               {"alert": True, "winner": winner})
        st.set_phase(GamePhase.GAME_OVER, 0.0)
        st.bump()
        self.dirty = True
        return True

    # ---------------------------------------------------------------- 工具

    def _make_decision(
        self,
        player_id: str,
        kind: str,
        title: str,
        options: list[DecisionOption],
        description: str = "",
        context: dict[str, Any] | None = None,
        cancellable: bool = False,
    ) -> PendingDecision:
        st = self.state
        st.decision_seq += 1
        decision = PendingDecision(
            decision_id=f"d{st.decision_seq}",
            player_id=player_id,
            kind=kind,
            title=title,
            options=options,
            description=description,
            context=context or {},
            cancellable=cancellable,
            created_revision=st.revision,
            seq=st.decision_seq,
        )
        st.pending_decision = decision
        st.decision_timer = 0.0
        return decision

    def _auto_resolve_decision(self) -> None:
        """决策等待超时：自动选择最保守的选项。"""
        st = self.state
        decision = st.pending_decision
        if decision is None:
            return
        priority = ("skip", "roll", "confirm")
        chosen: DecisionOption | None = None
        for pid in priority:
            opt = decision.option(pid)
            if opt is not None and opt.enabled:
                chosen = opt
                break
        if chosen is None:
            for opt in decision.options:
                if opt.enabled:
                    chosen = opt
                    break
        player = st.player(decision.player_id)
        if chosen is None:
            log.warning("决策 %s 无可用选项，强制结束回合", decision.kind)
            st.pending_decision = None
            self._enter_phase(GamePhase.TURN_END)
            return
        st.log(EventType.INFO,
               f"{player.name if player else '玩家'} 长时间未操作，自动选择「{chosen.label}」",
               decision.player_id)
        self._dispatch_decision_option(
            Command(ctype=chosen.command_type, player_id=decision.player_id,
                    payload=dict(chosen.payload), decision_id=decision.id),
            decision,
        )
        st.decision_timer = 0.0

    def drain_events(self) -> list[Any]:
        out = self.outbox
        self.outbox = []
        return out

    def consume_dirty(self) -> bool:
        was = self.dirty
        self.dirty = False
        return was

    def set_anim_speed(self, speed: float) -> None:
        self.anim_speed = max(0.25, float(speed))

    @property
    def match_id(self) -> str:
        return self.state.match_id


def _roll_dice_checked(state: GameState, forced: int | None):
    """掷骰（独立函数便于测试替换）。"""
    from .dice import roll

    rng = state.next_rng()
    return roll(rng, 6, forced)


def create_match_id() -> str:
    return uuid.uuid4().hex[:8]
