"""道具卡注册表。

卡牌数据来自 data/cards.json；本模块负责加载、查询与合法性前置判断。
实际效果执行由 engine 完成（因为需要与移动、地产、其他玩家交互）。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .player import Player
from .state import GameState

#: 目标类型
TARGET_NONE = None
TARGET_TILE = "tile"
TARGET_PLAYER = "player"
TARGET_OWN_PROPERTY = "own_property"
TARGET_OTHER_PROPERTY = "other_property"
TARGET_DICE_VALUE = "dice_value"

#: 对手施加的负面状态（净化卡会清掉这些）
NEGATIVE_STATUSES = ("skip_turn", "card_blocked")

#: 使用时机。决定「这张卡什么时候可以点」。
class Timing:
    PRE_ROLL = "pre_roll"        # 掷骰前
    POST_ROLL = "post_roll"      # 掷骰后、移动前
    ON_PROPERTY = "on_property"  # 站在自己的地产上时
    REACTIVE = "reactive"        # 需要支付时（响应式）
    ANY_TURN = "any_turn"        # 任意时刻（含别人回合）
    OWN_TURN = "own_turn"        # 自己回合内任意时点（默认）


TIMING_OWN_TURN = "own_turn"

#: 时机的中文说明（用于把不可用的卡灰化时给出原因）
TIMING_LABEL = {
    Timing.PRE_ROLL: "仅可在掷骰前使用",
    Timing.POST_ROLL: "仅可在掷骰后使用",
    Timing.ON_PROPERTY: "仅可站在自己的地产上使用",
    Timing.REACTIVE: "仅在需要付款时使用",
    Timing.ANY_TURN: "任意时刻可用",
    Timing.OWN_TURN: "仅可在自己回合使用",
}


class CardDef:
    """一张道具卡的静态定义。"""

    __slots__ = (
        "id", "name", "description", "rarity", "icon",
        "timing", "needs_target", "effect", "ai_weight", "shop_price", "tags",
    )

    def __init__(self, d: dict[str, Any]) -> None:
        self.id = d["id"]
        self.name = d["name"]
        self.description = d.get("description", "")
        self.rarity = d.get("rarity", "common")
        self.icon = d.get("icon", "star")
        self.timing = d.get("timing", TIMING_OWN_TURN)
        self.needs_target = d.get("needs_target")
        self.effect = dict(d.get("effect") or {})
        self.ai_weight = float(d.get("ai_weight", 0.5))
        self.shop_price = int(d.get("shop_price", 0) or 0)
        self.tags = list(d.get("tags") or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "rarity": self.rarity,
            "icon": self.icon,
            "timing": self.timing,
            "needs_target": self.needs_target,
            "effect": dict(self.effect),
            "ai_weight": self.ai_weight,
            "shop_price": self.shop_price,
            "tags": list(self.tags),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CardDef {self.id} {self.name}>"


class CardRegistry:
    """全部道具卡定义。"""

    def __init__(self, cards: list[CardDef]) -> None:
        self.cards: dict[str, CardDef] = {c.id: c for c in cards}
        self._order = [c.id for c in cards]

    def get(self, card_id: str) -> CardDef | None:
        return self.cards.get(card_id)

    def require(self, card_id: str) -> CardDef:
        card = self.cards.get(card_id)
        if card is None:
            raise KeyError(f"未知道具卡: {card_id}")
        return card

    def ids(self) -> list[str]:
        return list(self._order)

    def all(self) -> Iterable[CardDef]:
        return [self.cards[i] for i in self._order]

    def common_ids(self) -> list[str]:
        return [c.id for c in self.all() if c.rarity == "common"]

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "cards": [c.to_dict() for c in self.all()]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CardRegistry":
        return cls([CardDef(c) for c in d["cards"]])

    @classmethod
    def load(cls, path: str) -> "CardRegistry":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ------------------------------------------------------------------ 目标枚举

def valid_targets(state: GameState, player: Player, card: CardDef) -> list[Any]:
    """返回该卡在当前位置下的合法目标列表。

    - tile: 所有格子索引（传送卡允许任意格）
    - player: 其他未破产玩家 id
    - own_property: 自己未满级的地产 id
    - dice_value: 2..12 的整数列表
    """
    t = card.needs_target
    if t is None:
        return []
    if t == TARGET_TILE:
        return [tile.index for tile in state.board]
    if t == TARGET_PLAYER:
        return [p.id for p in state.other_active_players(player.id) if not p.bankrupt]
    if t == TARGET_OWN_PROPERTY:
        return [
            p.id for p in state.properties_of(player.id)
            if not p.is_max_level
        ]
    if t == TARGET_OTHER_PROPERTY:
        return [
            p.id for p in state.properties.values()
            if p.owner_id is not None and p.owner_id != player.id
        ]
    if t == TARGET_DICE_VALUE:
        return list(range(2, 13))
    return []


def can_use(state: GameState, player: Player, card: CardDef) -> tuple[bool, str]:
    """判断该玩家当前能否使用这张卡（含使用时机校验）。"""
    from ..game.phases import GamePhase

    if state.game_over:
        return False, "游戏已结束"
    if player.bankrupt:
        return False, "你已破产退出"

    active = state.current_player
    is_my_turn = active is not None and active.id == player.id
    timing = card.timing

    if timing == Timing.ANY_TURN:
        pass
    elif timing == Timing.REACTIVE:
        # 响应式卡只在需要付款时可用（由债务 / 支付流程显式提供入口）
        if state.debt is None or state.debt.get("player_id") != player.id:
            return False, "仅在需要付款时使用"
    elif timing == Timing.PRE_ROLL:
        if not is_my_turn or state.turn_rolled:
            return False, "仅可在掷骰前使用"
    elif timing == Timing.POST_ROLL:
        if not is_my_turn or not state.turn_rolled:
            return False, "仅可在掷骰后使用"
    elif timing == Timing.ON_PROPERTY:
        if not is_my_turn:
            return False, "仅可在自己回合使用"
        prop = state.property_at(player.position)
        if prop is None or prop.owner_id != player.id:
            return False, "仅可站在自己的地产上使用"
    else:  # OWN_TURN
        if not is_my_turn:
            return False, "只能在自己回合使用道具"

    if state.debt is not None and state.debt.get("player_id") != player.id:
        return False, "其他玩家正在处理债务"
    if player.has_status("card_blocked"):
        return False, "你被封锁，下一回合才能使用道具"
    if card.needs_target is not None and not valid_targets(state, player, card):
        return False, "没有可用目标"

    kind = card.effect.get("kind")
    if kind == "free_upgrade":
        if not [p for p in state.properties_of(player.id) if not p.is_max_level]:
            return False, "没有可升级的地产"
    if kind == "swap_position":
        if not state.other_active_players(player.id):
            return False, "没有可交换的对手"
    if kind == "steal":
        targets = [p for p in state.other_active_players(player.id)
                   if p.money > 0 and not p.has_status("protected")]
        if not targets:
            return False, "没有可抢夺的对手"
    if kind == "downgrade_property":
        targets = [p for p in state.properties.values()
                   if p.owner_id is not None and p.owner_id != player.id and p.level > 0]
        if not targets:
            return False, "场上没有可降级的对手地产"
    if kind == "swap_property":
        if not state.properties_of(player.id):
            return False, "你还没有地产可以拿去交换"
        targets = [p for p in state.properties.values()
                   if p.owner_id is not None and p.owner_id != player.id]
        if not targets:
            return False, "场上没有可交换的对手地产"
    if kind == "leave_jail":
        if not player.in_jail:
            return False, "只有在看守所里才能使用保释券"
    if kind == "clear_negative":
        if not any(player.has_status(x) for x in NEGATIVE_STATUSES):
            return False, "你身上没有负面状态"
    if kind == "wealth_tax":
        targets = [p for p in state.other_active_players(player.id)
                   if p.money > 0 and not p.has_status("protected")]
        if not targets:
            return False, "没有可征税的对手"
    return True, ""


def describe_effect(card: CardDef) -> str:
    return card.description


def sort_cards_for_ai(store: CardRegistry, card_ids: list[str]) -> list[str]:
    """AI 使用顺序：价值高的先用。"""
    def key(cid: str) -> float:
        c = store.get(cid)
        return -(c.ai_weight if c else 0.0)
    return sorted(card_ids, key=key)
