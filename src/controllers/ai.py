"""启发式 AI 控制器。

设计目标（按重要性排序）：
1. 行为合法：绝不发出会被引擎拒绝的命令，绝不卡局；
2. 行为合理：会买地、会升级、会收租、会针对领先者、会自保；
3. 有节奏：带一点思考延迟，观感自然。

不做机器学习，也不需要——这是一套可解释的规则式决策。
"""
from __future__ import annotations

import random
from typing import Any

from ..game import cards as cards_mod
from ..game import economy, jail
from ..game.commands import (
    Command,
    CommandType,
    DecisionKind,
    PendingDecision,
)
from ..game.player import (
    STATUS_FREE_RENT,
    STATUS_PROTECTED,
    STATUS_RENT_DOUBLE_ONCE,
)
from ..game.state import GameState
from .base import BaseController


class Difficulty:
    EASY = "easy"
    NORMAL = "normal"


class AIController(BaseController):
    """一个会打大富翁的机器人。"""

    def __init__(
        self,
        player_id: str,
        difficulty: str = Difficulty.NORMAL,
        think_sec: float = 0.45,
        seed: int | None = None,
    ) -> None:
        super().__init__(player_id)
        self.difficulty = difficulty
        self.think_sec = think_sec
        self.rng = random.Random(seed)
        self._decision_id: str | None = None
        self._plan: Command | None = None
        self._waited = 0.0
        self._cards_used_turn = -1
        self._cards_used_count = 0
        self.persona = Personality.load(Personality.BALANCED)
        self._acted_this_turn: set[str] = set()
        self.registry = None
        self.last_thought = ""

    # ---------------------------------------------------------------- 主循环

    def poll(
        self,
        decision: PendingDecision | None,
        state: GameState,
        engine: Any,
        dt: float,
    ) -> Command | None:
        if decision is None:
            self._decision_id = None
            self._plan = None
            self._waited = 0.0
            return None
        if decision.player_id != self.player_id:
            return None

        if decision.id != self._decision_id:
            self._decision_id = decision.id
            self._waited = 0.0
            self.registry = getattr(engine, "card_registry", None)
            self._plan = self._decide(decision, state, engine)

        if self._plan is None:
            # 没有合适的选择时，退回第一个可用选项
            self._plan = self._fallback(decision)

        self._waited += dt
        jitter = 0.85 + self.rng.random() * 0.3
        # AI 演出速度：只缩短「等 AI 发呆」的时间，决策结果完全相同。
        # 决策本身是几毫秒算完的，玩家需要的是看清它在做什么，而不是等它想。
        scale = float(getattr(engine, "ai_think_scale", 1.0) or 1.0)
        think = self.think_sec * jitter / max(0.25, scale)
        if self._waited < think:
            return None
        plan = self._plan
        self._plan = None
        self._decision_id = None
        self._waited = 0.0
        return plan

    def describe(self) -> str:
        return "AI 玩家"

    # ---------------------------------------------------------------- 分发

    def _decide(
        self, decision: PendingDecision, state: GameState, engine: Any
    ) -> Command | None:
        player = state.player(self.player_id)
        if player is None or player.bankrupt:
            return None
        kind = decision.kind
        self._ensure_turn_counter(state)

        try:
            if kind == DecisionKind.ROLL:
                return self._decide_roll(decision, state, engine, player)
            if kind == DecisionKind.BUY_PROPERTY:
                return self._decide_buy(decision, state, player)
            if kind == DecisionKind.UPGRADE_PROPERTY:
                return self._decide_upgrade(decision, state, player)
            if kind == DecisionKind.JAIL:
                return self._decide_jail(decision, state, player)
            if kind == DecisionKind.CARD_TARGET_PLAYER:
                return self._decide_target_player(decision, state, player)
            if kind == DecisionKind.CARD_TARGET_TILE:
                return self._decide_target_tile(decision, state, player)
            if kind == DecisionKind.CARD_TARGET_OWN_PROPERTY:
                return self._decide_own_property(decision, state, player)
            if kind == DecisionKind.CARD_DICE_VALUE:
                return self._decide_dice_value(decision, state, player)
            if kind == DecisionKind.DEBT_RESOLUTION:
                return self._decide_debt(decision, state, player)
            if kind == DecisionKind.SHOP:
                return self._decide_shop(decision, state, player)
            if kind == DecisionKind.BANKRUPTCY:
                return self._fallback(decision)
        except Exception:  # AI 出错不能拖垮整局
            return self._fallback(decision)

        return self._fallback(decision)

    def _fallback(self, decision: PendingDecision) -> Command | None:
        for opt in decision.options:
            if opt.enabled:
                return self._resolve(decision, opt.id)
        return None

    def _resolve(self, decision: PendingDecision, option_id: str) -> Command:
        return Command(
            ctype=CommandType.RESOLVE_DECISION,
            player_id=self.player_id,
            payload={"option_id": option_id},
            decision_id=decision.id,
        )

    def _ensure_turn_counter(self, state: GameState) -> None:
        if self._cards_used_turn != state.turn_number:
            self._cards_used_turn = state.turn_number
            self._cards_used_count = 0
            self._acted_this_turn.clear()
            self.persona = personality_of(state, self.player_id)

    # ---------------------------------------------------------------- 掷骰

    def _decide_roll(
        self, decision: PendingDecision, state: GameState, engine: Any, player
    ) -> Command:
        # 现金充裕先赎回抵押地，紧张则抵押空地换流动性（每回合最多一次）
        redeem_cmd = self._maybe_redeem(state, player)
        if redeem_cmd is not None:
            return redeem_cmd
        cash_cmd = self._maybe_raise_cash(state, player)
        if cash_cmd is not None:
            return cash_cmd
        if self._cards_used_count < 1:
            card_cmd = self._maybe_use_card(state, engine, player)
            if card_cmd is not None:
                self._cards_used_count += 1
                self.last_thought = "使用道具"
                return card_cmd
        self.last_thought = "掷骰"
        return self._resolve(decision, "roll")

    # ---------------------------------------------------------------- 买地

    def _decide_buy(self, decision: PendingDecision, state: GameState, player) -> Command:
        prop_id = str(decision.context.get("property_id", ""))
        prop = state.properties.get(prop_id)
        if prop is None:
            return self._resolve(decision, "skip")

        price, detail = economy.buy_price(state, player, prop)
        if player.money < price:
            return self._resolve(decision, "skip")

        reserve = self._reserve(state, player)
        after = player.money - price

        # 分数越高越想买
        score = 0.0
        # 便宜且租金回报高 → 加分
        ratio = prop.base_rent / max(1, prop.price)
        score += ratio * 300
        # 能凑齐一整组 → 大幅加分
        if prop.district:
            district_ids = state.district_props.get(prop.district, [])
            owned = sum(1 for pid in district_ids
                        if (state.properties.get(pid) or prop).owner_id == player.id)
            if owned >= len(district_ids) - 1:
                score += 45
            elif owned > 0:
                score += 18
        # 高级地产（车站/机场）加分
        if prop.kind == "STATION":
            score += 12
        # 手上有折扣券 → 加分
        if player.has_status("purchase_discount"):
            score += 20
        # 领先/落后调整
        rank = self._asset_rank(state, player)
        score -= rank * 6

        # 现金安全线
        if after < 0:
            return self._resolve(decision, "skip")
        if after < reserve * 0.35:
            score -= 60
        elif after < reserve * 0.7:
            score -= 20

        score += float(self.persona.get("buy_threshold", 0.0))
        if self.difficulty == Difficulty.EASY:
            score -= 12
            if self.rng.random() < 0.18:
                score -= 30

        self.last_thought = f"买地评分 {score:.0f}"
        return self._resolve(decision, "buy" if score >= 0 else "skip")

    # ---------------------------------------------------------------- 升级

    def _decide_upgrade(self, decision: PendingDecision, state: GameState, player) -> Command:
        prop_id = str(decision.context.get("property_id", ""))
        prop = state.properties.get(prop_id)
        if prop is None or prop.is_max_level:
            return self._resolve(decision, "skip")

        cost, _ = economy.upgrade_cost(state, player, prop)
        if player.money < cost:
            return self._resolve(decision, "skip")

        after = player.money - cost
        reserve = self._reserve(state, player)
        gain = (prop.next_rent or 0) - prop.base_rent
        score = 10.0
        score += (gain / max(1, cost)) * 60          # 投入产出比
        if prop.district and state.district_owned_all(player.id, prop.district):
            score += 30                              # 垄断区优先升级
        if prop.level >= 1:
            score += 8
        if after < reserve * 0.4:
            score -= 45
        elif after < reserve:
            score -= 15
        if player.has_status("upgrade_discount"):
            score += 25

        score += float(self.persona.get("upgrade_threshold", 0.0))
        if self.difficulty == Difficulty.EASY:
            score -= 15

        self.last_thought = f"升级评分 {score:.0f}"
        return self._resolve(decision, "upgrade" if score >= 0 else "skip")

    # ---------------------------------------------------------------- 看守所

    def _decide_jail(self, decision: PendingDecision, state: GameState, player) -> Command:
        fee = int(decision.context.get("fee", 0))
        left = max(0, jail.jail_max_turns(state) - player.jail_turns)
        # 关押快到期限且付得起 → 直接付钱走人
        if left <= 1 and player.money >= fee:
            return self._resolve(decision, "pay")
        # 现金宽裕（保释后仍有安全线）→ 付钱
        if player.money - fee >= self._reserve(state, player) * 1.5 and player.money >= fee:
            return self._resolve(decision, "pay")
        # 否则赌一把
        return self._resolve(decision, "roll")

    # ---------------------------------------------------------------- 目标选择

    def _decide_target_player(
        self, decision: PendingDecision, state: GameState, player
    ) -> Command:
        action = decision.context.get("action", "")
        amount = int(decision.context.get("amount", 0))
        candidates = []
        for opt in decision.options:
            tid = opt.payload.get("target_id")
            if not tid:
                continue
            target = state.player(str(tid))
            if target is None or target.bankrupt:
                continue
            if target.has_status(STATUS_PROTECTED):
                continue
            candidates.append((target, opt))
        if not candidates:
            skip = decision.option("skip")
            return self._resolve(decision, skip.id if skip else decision.options[0].id)

        if action in ("demand", "steal"):
            # 拿富有的开刀
            candidates.sort(key=lambda x: -x[0].money)
        else:
            candidates.sort(key=lambda x: -x[0].money)
        best = candidates[0]
        self.last_thought = f"针对 {best[0].name}"
        return self._resolve(decision, best[1].id)

    def _decide_target_tile(
        self, decision: PendingDecision, state: GameState, player
    ) -> Command:
        options = [o for o in decision.options if o.enabled and o.payload.get("tile_index") is not None]
        if not options:
            return self._resolve(decision, decision.options[0].id)
        scored: list[tuple[float, Any]] = []
        for opt in options:
            idx = int(opt.payload["tile_index"])
            prop = state.property_at(idx)
            score = 0.0
            if prop is not None:
                if prop.owner_id is None:
                    price, _ = economy.buy_price(state, player, prop)
                    if player.money >= price:
                        score += prop.price / 300.0
                        if prop.district:
                            ids = state.district_props.get(prop.district, [])
                            owned = sum(1 for pid in ids
                                        if (state.properties.get(pid) or prop).owner_id == player.id)
                            if owned >= len(ids) - 1:
                                score += 25
                    else:
                        score -= 10
                elif prop.owner_id == player.id:
                    score += 2
                else:
                    score -= 30
            scored.append((score, opt))
        scored.sort(key=lambda x: -x[0])
        return self._resolve(decision, scored[0][1].id)

    def _decide_own_property(
        self, decision: PendingDecision, state: GameState, player
    ) -> Command:
        options = [o for o in decision.options if o.enabled]
        if not options:
            return self._resolve(decision, decision.options[0].id)
        best = None
        best_gain = -1.0
        for opt in options:
            pid = opt.payload.get("property_id")
            prop = state.properties.get(str(pid))
            if prop is None:
                continue
            gain = ((prop.next_rent or 0) - prop.base_rent) / max(1, prop.price)
            if gain > best_gain:
                best_gain = gain
                best = opt
        if best is None:
            best = options[0]
        return self._resolve(decision, best.id)

    def _decide_dice_value(
        self, decision: PendingDecision, state: GameState, player
    ) -> Command:
        options = [o for o in decision.options if o.enabled]
        if not options:
            return self._resolve(decision, decision.options[0].id)
        best_opt = None
        best_score = -1e9
        for opt in options:
            value = int(opt.payload.get("dice_value", opt.payload.get("value", 7)))
            landing = (player.position + value) % state.board.tile_count
            prop = state.property_at(landing)
            score = 0.0
            if prop is not None:
                if prop.owner_id is None:
                    price, _ = economy.buy_price(state, player, prop)
                    score += 20 if player.money >= price else -10
                elif prop.owner_id == player.id:
                    score += 8
                else:
                    amount, _ = economy.compute_rent_payment(state, player, prop)
                    score -= amount / 200.0
            if state.board.tile(landing).type.value == "START":
                score += 6
            if score > best_score:
                best_score = score
                best_opt = opt
        return self._resolve(decision, (best_opt or options[0]).id)


    # ---------------------------------------------------------------- 债务处理

    def _decide_debt(self, decision, state: GameState,
                     player) -> Command:
        """在债务处理里挑一条对资产伤害最小的路。

        优先级：
        1. 先抵押空地 —— 保留产权，之后还能赎回；
        2. 抵押不够时，卖等级最低、最便宜的（保留垄断区与核心地标）；
        3. 若能一次操作就凑够，优先选金额恰好够的那一项，避免过度变卖。
        """
        options = [o for o in decision.options if o.enabled and o.id != "declare"]
        if not options:
            declare = decision.option("declare")
            return self._resolve(decision, declare.id if declare else decision.options[-1].id)

        shortfall = int(decision.context.get("shortfall", 0))
        scored = []
        for opt in options:
            prop = state.properties.get(str(opt.payload.get("property_id", "")))
            if prop is None:
                continue
            amount = 0
            try:
                amount = int(str(opt.label).rsplit("+", 1)[-1].replace(",", "").strip())
            except (ValueError, IndexError):
                amount = 0
            is_mortgage = opt.command_type == "MORTGAGE_PROPERTY"

            score = 0.0
            # 抵押优于出售
            score += 40.0 if is_mortgage else 0.0
            # 空地优于有建筑
            score += 12.0 * (3 - min(3, prop.level))
            # 便宜的优于贵的；垄断区的地产要保住
            score -= prop.price / 1200.0
            if prop.district and state.district_owned_all(player.id, prop.district):
                score -= 25.0
            # 这笔操作能不能一次解决问题
            if shortfall > 0 and amount >= shortfall:
                score += 30.0
            # 超出欠款太多会浪费资产
            if shortfall > 0 and amount > shortfall * 2:
                score -= 12.0
            scored.append((score, opt))

        if not scored:
            return self._fallback(decision)
        scored.sort(key=lambda x: -x[0])
        self.last_thought = f"债务处理：{scored[0][1].label}"
        return self._resolve(decision, scored[0][1].id)

    # ---------------------------------------------------------------- 商店

    def _decide_shop(self, decision, state: GameState,
                     player) -> Command:
        """按当前处境挑道具：缺钱先买现金卡，有地先买升级卡，领先就买攻击卡。"""
        from ..game import economy as econ

        reserve = self._reserve(state, player)
        richest = self._richest_opponent(state, player)
        owned = state.properties_of(player.id)
        upgradeable = [p for p in owned if not p.is_max_level]
        dangerous = self._incoming_risk(state, player)

        best = None
        best_score = 0.0
        for opt in decision.options:
            if not opt.enabled or opt.command_type != "BUY_SHOP_CARD":
                continue
            cid = str(opt.payload.get("card_id", ""))
            card = self.registry.get(cid) if self.registry else None
            if card is None:
                continue
            price = getattr(card, "shop_price", 0) or 0
            if player.money - price < reserve * 0.2:
                continue

            score = 0.0
            kind = card.effect.get("kind", "")
            if cid == "card_cash":
                score = 45.0 if player.money < reserve else 8.0
            elif cid == "card_free_upgrade":
                score = 55.0 if upgradeable else -50.0
            elif kind == "status" and card.effect.get("status") == "free_rent":
                score = 40.0 if dangerous > 0 else 12.0
            elif cid == "card_double_rent":
                score = 30.0 + 6.0 * len(owned)
            elif kind in ("steal", "wealth_tax"):
                score = 24.0 + (10.0 if richest and richest.money > 4000 else 0.0)
            elif kind in ("apply_status_to_target", "downgrade_property",
                          "swap_property"):
                score = 22.0 if richest else -20.0
            elif kind == "place_barrier":
                score = 18.0
            elif kind == "extra_turn":
                score = 34.0
            elif cid == "card_teleport":
                score = 26.0
            elif kind == "gain_money":
                score = 30.0 if player.money < reserve else 10.0
            else:
                score = 14.0

            # 打折更像白捡
            if price > 0:
                score += max(0.0, (1400 - price) / 120.0)
            score *= self.persona.get("attack_bias", 1.0) if kind in (
                "steal", "wealth_tax", "downgrade_property", "swap_property",
                "apply_status_to_target") else 1.0

            if score > best_score:
                best_score = score
                best = opt

        if best is not None and best_score >= 18.0:
            self.last_thought = f"商店买入 {best.label}"
            return self._resolve(decision, best.id)
        leave = decision.option("leave")
        return self._resolve(decision, leave.id if leave else decision.options[-1].id)

    def _incoming_risk(self, state: GameState, player) -> int:
        """粗略评估「前方可能踩到的高租金地产」数量。"""
        risk = 0
        for step in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            idx = (player.position + step) % state.board.tile_count
            prop = state.property_at(idx)
            if prop is None or prop.owner_id in (None, player.id):
                continue
            if prop.level >= 1 or state.district_owned_all(prop.owner_id, prop.district):
                risk += 1
        return risk

    # ---------------------------------------------------------------- 主动理财

    def _maybe_redeem(self, state: GameState, player) -> Command | None:
        """现金充裕时把手里的抵押地赎回来 —— 抵押中不能收租，长期持有是亏的。"""
        from ..game import economy as econ

        reserve = self._reserve(state, player)
        if player.money < reserve * 1.4:
            return None
        candidates = []
        for prop in state.properties_of(player.id):
            if not prop.mortgaged:
                continue
            cost = econ.redeem_cost(prop)
            if player.money - cost < reserve * 0.9:
                continue
            # 优先赎回租金潜力大的（垄断区的优先）
            gain = prop.rent_table[0]
            if prop.district and state.district_owned_all(player.id, prop.district):
                gain *= 3
            candidates.append((gain, cost, prop))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[0], x[1]))
        prop = candidates[0][2]
        key = f"redeem:{prop.id}"
        if key in self._acted_this_turn:
            return None
        self._acted_this_turn.add(key)
        self.last_thought = f"现金充裕，赎回 {prop.name}"
        return Command(
            ctype=CommandType.REDEEM_PROPERTY,
            player_id=self.player_id,
            payload={"property_id": prop.id},
        )

    def _maybe_raise_cash(self, state: GameState, player) -> Command | None:
        """现金低于安全线且有空地时，主动抵押一块换现金（每回合最多一次）。

        这是「会玩」的体现：与其等着被高额租金打到债务处理，不如提前把
        空地抵押出去换流动性。
        """
        if player.money >= self._reserve(state, player):
            return None
        if not player.has_status("protected"):
            pass
        from ..game import economy as econ

        candidates = []
        for prop in state.properties_of(player.id):
            ok, _ = econ.can_mortgage(prop)
            if not ok:
                continue
            value, _ = econ.mortgage_value(state, player, prop)
            candidates.append((value, prop))
        if not candidates:
            return None
        # 抵押最便宜的一块，代价最小
        candidates.sort(key=lambda x: x[1].price)
        prop = candidates[0][1]
        key = f"mortgaged:{prop.id}"
        if key in self._acted_this_turn:
            return None
        self._acted_this_turn.add(key)
        self.last_thought = f"现金紧张，抵押 {prop.name}"
        return Command(
            ctype=CommandType.MORTGAGE_PROPERTY,
            player_id=self.player_id,
            payload={"property_id": prop.id},
        )

    # ---------------------------------------------------------------- 道具

    def _maybe_use_card(self, state: GameState, engine: Any, player) -> Command | None:
        """决定是否在本回合使用一张道具卡。"""
        if not player.cards:
            return None
        registry: cards_mod.CardRegistry = getattr(engine, "card_registry", None)
        if registry is None:
            return None

        reserve = self._reserve(state, player)
        richest = self._richest_opponent(state, player)
        candidates = list(dict.fromkeys(player.cards))  # 去重且保持顺序

        def build(card_id: str, target: Any) -> Command:
            return Command(
                ctype=CommandType.USE_CARD,
                player_id=self.player_id,
                payload={"card_id": card_id, "target": target},
            )

        for card_id in candidates:
            card = registry.get(card_id)
            if card is None:
                continue
            ok, _ = cards_mod.can_use(state, player, card)
            if not ok:
                continue
            kind = card.effect.get("kind", "")

            if card_id == "card_cash":
                if player.money < reserve:
                    return build(card_id, None)

            elif card_id == "card_free_rent":
                if player.money < reserve * 1.4 and self._has_high_rent_rival(state, player):
                    return build(card_id, None)

            elif card_id == "card_discount":
                target = self._best_buyable(state, player)
                if target is not None:
                    return build(card_id, None)

            elif card_id == "card_free_upgrade":
                prop = self._best_upgrade_target(state, player)
                if prop is not None:
                    return build(card_id, prop.id)

            elif card_id == "card_double_rent":
                if len(state.other_active_players(player.id)) >= 1 and state.owned_count(player.id) >= 2:
                    return build(card_id, None)

            elif card_id == "card_rob":
                if richest is not None and richest.money > 1200:
                    return build(card_id, richest.id)

            elif card_id == "card_stay":
                if richest is not None:
                    return build(card_id, richest.id)

            elif card_id == "card_shield":
                if player.money < reserve:
                    return build(card_id, None)

            elif card_id == "card_swap":
                if richest is not None and self._swap_gain(state, player, richest) > 0:
                    return build(card_id, richest.id)

            elif card_id == "card_teleport":
                target = self._best_buyable(state, player)
                if target is not None:
                    idx = target.tile_index
                    if idx != player.position:
                        return build(card_id, idx)

            elif card_id == "card_barrier":
                if richest is not None:
                    idx = (richest.position + 3) % state.board.tile_count
                    if idx != state.board.start_index():
                        return build(card_id, idx)

            elif card_id == "card_fixed_dice":
                if player.in_jail:
                    return build(card_id, 6)
                return build(card_id, self._best_dice_total(state, player))

        return None

    def _best_dice_total(self, state: GameState, player) -> int:
        best_value, best_score = 7, -1e9
        for value in range(2, 13):
            landing = (player.position + value) % state.board.tile_count
            prop = state.property_at(landing)
            score = 0.0
            if prop is not None:
                if prop.owner_id is None:
                    price, _ = economy.buy_price(state, player, prop)
                    score += 25 if player.money >= price else -8
                    if prop.district:
                        ids = state.district_props.get(prop.district, [])
                        owned = sum(1 for pid in ids
                                    if (state.properties.get(pid) or prop).owner_id == player.id)
                        if owned >= len(ids) - 1:
                            score += 30
                elif prop.owner_id == player.id:
                    score += 10
                else:
                    amount, _ = economy.compute_rent_payment(state, player, prop)
                    score -= amount / 150.0
            ttype = state.board.tile(landing).type.value
            if ttype == "START":
                score += 10
            elif ttype == "CHANCE":
                score += 3
            elif ttype == "PARK" or ttype == "BONUS":
                score += min(20, state.bonus_pool / 200.0)
            elif ttype == "TAX":
                score -= 15
            elif ttype == "GO_TO_JAIL":
                score -= 40
            if score > best_score:
                best_score = score
                best_value = value
        return best_value

    def _best_buyable(self, state: GameState, player):
        best = None
        best_score = 0.0
        for prop in state.unowned_properties():
            price, _ = economy.buy_price(state, player, prop)
            if player.money < price:
                continue
            score = prop.price / 1000.0
            if prop.district:
                ids = state.district_props.get(prop.district, [])
                owned = sum(1 for pid in ids
                            if (state.properties.get(pid) or prop).owner_id == player.id)
                score += owned * 3
            if score > best_score:
                best_score = score
                best = prop
        return best

    def _best_upgrade_target(self, state: GameState, player):
        best = None
        best_gain = 0.0
        for prop in state.properties_of(player.id):
            if prop.is_max_level:
                continue
            gain = ((prop.next_rent or 0) - prop.base_rent) / max(1, prop.price)
            if prop.district and state.district_owned_all(player.id, prop.district):
                gain *= 1.5
            if gain > best_gain:
                best_gain = gain
                best = prop
        return best

    def _has_high_rent_rival(self, state: GameState, player) -> bool:
        for prop in state.properties.values():
            if prop.owner_id and prop.owner_id != player.id and prop.level >= 1:
                return True
        return False

    def _swap_gain(self, state: GameState, player, other) -> float:
        mine = state.property_at(player.position)
        theirs = state.property_at(other.position)
        score = 0.0
        if theirs is not None and theirs.owner_id == player.id:
            score += 5
        if mine is not None and mine.owner_id is not None and mine.owner_id != player.id:
            score += 5
        if theirs is not None and theirs.owner_id not in (None, player.id):
            score -= 5
        return score

    # ---------------------------------------------------------------- 评估

    def _reserve(self, state: GameState, player) -> float:
        """安全现金线：随游戏推进下降（后期需要激进投资）。"""
        base = float(state.rules.get("ai_safety_reserve", 2500))
        base *= float(self.persona.get("reserve_multiplier", 1.0))
        round_factor = max(0.35, 1.0 - state.round_number * 0.03)
        risk = 1.0
        if player.in_jail:
            risk += 0.2
        return base * round_factor * risk

    def _asset_rank(self, state: GameState, player) -> int:
        """按总资产排名，0 为最富。"""
        rows = sorted(
            state.active_players(),
            key=lambda p: state.player_asset_value(p.id),
            reverse=True,
        )
        for i, p in enumerate(rows):
            if p.id == player.id:
                return i
        return len(rows) - 1

    def _richest_opponent(self, state: GameState, player):
        others = [p for p in state.other_active_players(player.id) if not p.bankrupt]
        if not others:
            return None
        others.sort(key=lambda p: state.player_asset_value(p.id), reverse=True)
        return others[0]


def make_ai(player_id: str, difficulty: str = Difficulty.NORMAL, seed: int | None = None) -> AIController:
    return AIController(player_id, difficulty=difficulty, seed=seed)


# ==================================================================== 人格

class Personality:
    """AI 策略倾向。不同角色默认绑定不同人格，玩家不需要额外配置。"""

    STEADY = "steady"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

    #: 内置默认值（data/characters.json 里的 ai_personalities 会覆盖）
    DEFAULTS: dict[str, dict[str, float]] = {
        STEADY: {"name": "稳健型", "reserve_multiplier": 1.6,
                 "buy_threshold": 12, "upgrade_threshold": 6, "attack_bias": 0.7},
        BALANCED: {"name": "均衡型", "reserve_multiplier": 1.0,
                   "buy_threshold": 0, "upgrade_threshold": 0, "attack_bias": 1.0},
        AGGRESSIVE: {"name": "激进型", "reserve_multiplier": 0.6,
                     "buy_threshold": -14, "upgrade_threshold": -8, "attack_bias": 1.4},
    }

    @classmethod
    def load(cls, key: str) -> dict[str, float]:
        """读取人格参数；优先用 data/characters.json 里的定义。"""
        key = key if key in cls.DEFAULTS else cls.BALANCED
        params = dict(cls.DEFAULTS[key])
        try:
            from ..game.setup import load_characters

            doc = load_characters().get("ai_personalities") or {}
            if key in doc:
                for k, v in doc[key].items():
                    if k in ("reserve_multiplier", "buy_threshold",
                             "upgrade_threshold", "attack_bias"):
                        params[k] = float(v)
                    elif k == "name":
                        params["name"] = str(v)
        except Exception:
            pass
        params["key"] = key
        return params


def personality_of(state, player_id: str) -> dict[str, float]:
    """从玩家的角色定义里取出人格参数。"""
    player = state.player(player_id)
    key = Personality.BALANCED
    if player is not None:
        try:
            from ..game.setup import character_by_id

            char = character_by_id(player.character_id) or {}
            key = char.get("personality") or Personality.BALANCED
        except Exception:
            key = Personality.BALANCED
    return Personality.load(key)

