"""机遇事件（Chance）系统。

事件定义来自 data/chance_events.json。
本模块只做两件事：
1. 从牌堆抽卡、洗牌；
2. 执行原子效果，并把需要引擎参与的动作（移动、二次掷骰、免费升级…）
   作为 followup 返回，由 engine 按阶段机推进。
"""
from __future__ import annotations

import json
from typing import Any

from . import modifiers as mods
from .ledger import Reason
from .player import STATUS_EVENT_IMMUNE, STATUS_LUCKY, StatusEffect
from .state import GameState
from .events import (
    EventType,
    msg_chance,
    msg_chance_detail,
    msg_pool_all,
    msg_pool_gain,
    msg_pool_pay,
    money,
)

#: 需要引擎后续处理的动作
FOLLOWUP_MOVE = "move"
FOLLOWUP_MOVE_TO_START = "move_to_start"
FOLLOWUP_TELEPORT = "teleport"
FOLLOWUP_GO_JAIL = "go_to_jail"
FOLLOWUP_ROLL_AGAIN = "roll_again"
FOLLOWUP_FREE_UPGRADE = "free_upgrade"
FOLLOWUP_CHOOSE_TARGET = "choose_target"


class Polarity:
    """事件正负倾向，决定它出现在哪类格子上。"""

    FORTUNE = "fortune"      # 福运格
    DISASTER = "disaster"    # 灾祸格
    NEUTRAL = "neutral"      # 两类格子都可能出现


class ChanceCard:
    """一张机遇卡定义。"""

    __slots__ = ("id", "name", "description", "rarity", "target_type",
                 "effect", "polarity")

    def __init__(self, d: dict[str, Any]) -> None:
        self.id = d["id"]
        self.name = d["name"]
        self.description = d.get("description", "")
        self.rarity = d.get("rarity", "common")
        self.target_type = d.get("target_type", "self")
        self.effect = dict(d.get("effect") or {})
        self.polarity = d.get("polarity", Polarity.NEUTRAL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "rarity": self.rarity,
            "target_type": self.target_type,
            "effect": dict(self.effect),
            "polarity": self.polarity,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChanceCard {self.id} {self.name}>"


class ChanceRegistry:
    """机遇事件注册表。"""

    def __init__(self, cards: list[ChanceCard]) -> None:
        self.cards: dict[str, ChanceCard] = {c.id: c for c in cards}
        self._order = [c.id for c in cards]

    def get(self, card_id: str) -> ChanceCard | None:
        return self.cards.get(card_id)

    def ids(self) -> list[str]:
        return list(self._order)

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "cards": [self.cards[i].to_dict() for i in self._order]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChanceRegistry":
        return cls([ChanceCard(c) for c in d["cards"]])

    @classmethod
    def load(cls, path: str) -> "ChanceRegistry":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ------------------------------------------------------------------ 牌堆

def build_deck(state: GameState, registry: ChanceRegistry) -> None:
    """洗牌并放入牌堆。稀有事件权重更低。"""
    rng = state.next_rng()
    weights = {"common": 10, "uncommon": 5, "rare": 2}
    pool: list[str] = []
    for cid in registry.ids():
        card = registry.cards[cid]
        pool.extend([cid] * weights.get(card.rarity, 5))
    rng.shuffle(pool)
    state.chance_deck = pool
    state.chance_discard = []


def _matches(card: ChanceCard, polarity: str | None) -> bool:
    if polarity is None:
        return True
    return card.polarity == polarity or card.polarity == Polarity.NEUTRAL


def draw(state: GameState, registry: ChanceRegistry,
         polarity: str | None = None) -> ChanceCard:
    """抽一张事件卡；牌堆空了自动重洗弃牌堆。

    polarity 不为 None 时只在牌堆里找匹配倾向的卡
    （福运格只出好事，灾祸格只出坏事），中性卡两类都可能出现。
    """
    for _ in range(2):
        if not state.chance_deck:
            rng = state.next_rng()
            if state.chance_discard:
                state.chance_deck = list(state.chance_discard)
                state.chance_discard = []
                rng.shuffle(state.chance_deck)
            else:
                build_deck(state, registry)
        for i in range(len(state.chance_deck) - 1, -1, -1):
            card = registry.cards[state.chance_deck[i]]
            if _matches(card, polarity):
                state.chance_deck.pop(i)
                state.chance_discard.append(card.id)
                if len(state.chance_discard) > 200:
                    del state.chance_discard[:100]
                return card
        # 本轮牌堆没有匹配的卡，把它们全部当作已抽走，进入下一轮重洗
        state.chance_discard.extend(state.chance_deck)
        state.chance_deck = []
    # 极端兜底：直接返回任意一张
    return registry.cards[state.next_rng().choice(registry.ids())]


# ------------------------------------------------------------------ 效果执行

def _gain(state: GameState, player, amount: int) -> int:
    """给玩家加钱，含角色「事件收益加成」。返回实际增加额。"""
    bonus_rate = mods.perk_special(player.perk, "event_gain_bonus")
    if bonus_rate and amount > 0:
        amount = int(round(amount * (1.0 + bonus_rate)))
    # 幸运状态：事件收益翻倍
    if amount > 0 and player.has_status(STATUS_LUCKY):
        amount *= 2
    amount = state.ledger.gain(state, player, amount, Reason.CHANCE, "机遇事件")
    player.stats["event_gain"] = player.stats.get("event_gain", 0) + amount
    return amount


def _spend(state: GameState, player, amount: int) -> int:
    """扣钱（可为负余额，由 engine 的债务流程处理）。返回实际扣款额。"""
    amount = max(0, int(amount))
    state.ledger.pay_bank(state, player, amount, Reason.CHANCE, "机遇事件")
    player.stats["event_loss"] = player.stats.get("event_loss", 0) + amount
    return amount


def apply_effect(
    state: GameState, registry: ChanceRegistry, player, effect: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """执行一个效果。

    返回 (followups, 中文描述)。followups 交给 engine 继续处理。
    """
    kind = effect.get("kind", "gain_money")
    followups: list[dict[str, Any]] = []

    if kind == "gain_money":
        amount = _gain(state, player, int(effect.get("amount", 0)))
        return followups, f"{player.name} 获得 {money(amount)}"

    if kind == "lose_money":
        amount = _spend(state, player, int(effect.get("amount", 0)))
        return followups, f"{player.name} 支付 {money(amount)}"

    if kind == "lose_money_percent":
        pct = float(effect.get("percent", 0.05))
        amount = _spend(state, player, int(player.money * pct))
        return followups, f"{player.name} 损失现金 {money(amount)}"

    if kind == "pay_per_property":
        per = int(effect.get("amount", 100))
        count = state.owned_count(player.id)
        amount = _spend(state, player, per * count)
        return followups, f"{player.name} 按 {count} 处地产支付 {money(amount)}"

    if kind == "collect_from_all":
        per = int(effect.get("amount", 0))
        total = 0
        for other in state.other_active_players(player.id):
            pay = min(per, max(0, other.money))
            state.ledger.transfer(state, other, player, pay, Reason.CHANCE, "分红派息")
            total += pay
        got = _gain(state, player, total)
        return followups, f"{player.name} 从其他玩家共收取 {money(got)}"

    if kind == "pay_all":
        per = int(effect.get("amount", 0))
        total = 0
        for other in state.other_active_players(player.id):
            state.ledger.transfer(state, player, other, per, Reason.CHANCE, "慈善捐款")
            total += per
        spent = total
        return followups, f"{player.name} 向其他玩家共支付 {money(spent)}"

    if kind == "demand_from_player":
        amount = int(effect.get("amount", 0))
        followups.append({"action": FOLLOWUP_CHOOSE_TARGET, "amount": amount, "reason": "债务催收"})
        return followups, f"{player.name} 向指定对手索取 {money(amount)}"

    if kind == "move_forward":
        steps = int(effect.get("steps", 1))
        followups.append({"action": FOLLOWUP_MOVE, "steps": steps})
        return followups, f"{player.name} 前进 {steps} 格"

    if kind == "move_backward":
        steps = int(effect.get("steps", 1))
        followups.append({"action": FOLLOWUP_MOVE, "steps": -steps})
        return followups, f"{player.name} 后退 {steps} 格"

    if kind == "move_to_start":
        followups.append({"action": FOLLOWUP_MOVE_TO_START})
        return followups, f"{player.name} 直接返回起点"

    if kind == "teleport_random":
        rng = state.next_rng()
        target = rng.randrange(state.board.tile_count)
        followups.append({"action": FOLLOWUP_TELEPORT, "tile_index": target})
        return followups, f"{player.name} 被传送到「{state.board.tile(target).name}」"

    if kind == "status":
        status = effect.get("status", "")
        duration = int(effect.get("duration", 1))
        payload = dict(effect.get("payload") or {})
        if not status:
            return followups, "无效果"
        player.add_status(StatusEffect(status, duration, payload=payload))
        from .player import STATUS_LABELS
        return followups, f"{player.name} 获得状态：{STATUS_LABELS.get(status, status)}"

    if kind == "free_upgrade":
        followups.append({"action": FOLLOWUP_FREE_UPGRADE})
        return followups, f"{player.name} 可以免费升级一处地产"

    if kind == "gain_card":
        rng = state.next_rng()
        from .cards import CardRegistry  # 延迟导入避免循环
        limit = int(state.rules.get("max_cards_per_player", 5))
        pool = state.rules.get("_card_ids") or []
        if not pool:
            return followups, "没有可获得的道具"
        cid = pool[rng.randrange(len(pool))]
        if player.add_card(cid, limit):
            from .cards import CardRegistry as _CR  # noqa: F401
            name = state.rules.get("_card_names", {}).get(cid, cid)
            return followups, f"{player.name} 获得道具「{name}」"
        return followups, f"{player.name} 的道具已满，无法获得新道具"

    if kind == "lose_card":
        if player.cards:
            rng = state.next_rng()
            idx = rng.randrange(len(player.cards))
            lost = player.cards.pop(idx)
            name = state.rules.get("_card_names", {}).get(lost, lost)
            return followups, f"{player.name} 失去道具「{name}」"
        return followups, f"{player.name} 没有道具可以失去"

    if kind == "go_to_jail":
        followups.append({"action": FOLLOWUP_GO_JAIL})
        return followups, f"{player.name} 被押送到看守所"

    if kind == "roll_again":
        followups.append({"action": FOLLOWUP_ROLL_AGAIN})
        return followups, f"{player.name} 获得再次掷骰的机会"

    if kind == "bonus_pool_gain":
        amount = state.bonus_pool
        state.bonus_pool = 0
        got = _gain(state, player, amount)
        return followups, f"{player.name} 领取奖金池 {money(got)}"

    if kind == "pay_bonus_pool":
        amount = _spend(state, player, int(effect.get("amount", 0)))
        state.bonus_pool += max(0, amount)
        return followups, f"{player.name} 向奖金池注入 {money(amount)}"

    if kind == "all_pay_pool":
        per = int(effect.get("amount", 0))
        total = 0
        for p in state.active_players():
            paid = min(per, max(0, p.money))
            state.ledger.pay_bank(state, p, paid, Reason.BONUS_POOL, "全城建设费")
            total += paid
        state.bonus_pool += total
        return followups, msg_pool_all(per, len(state.active_players()))

    return followups, "无效果"


def describe_card(card: ChanceCard) -> str:
    return card.description
