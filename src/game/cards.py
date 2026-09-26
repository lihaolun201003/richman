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
TARGET_DICE_VALUE = "dice_value"

#: 使用时机
TIMING_OWN_TURN = "own_turn"


class CardDef:
    """一张道具卡的静态定义。"""

    __slots__ = (
        "id", "name", "description", "rarity", "icon",
        "timing", "needs_target", "effect", "ai_weight",
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
    if t == TARGET_DICE_VALUE:
        return list(range(2, 13))
    return []


def can_use(state: GameState, player: Player, card: CardDef) -> tuple[bool, str]:
    """判断该玩家当前能否使用这张卡。"""
    if card.timing != TIMING_OWN_TURN:
        return False, "该道具当前无法使用"
    active = state.current_player
    if active is None or active.id != player.id:
        return False, "只能在自己回合使用道具"
    if state.game_over:
        return False, "游戏已结束"
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
    return True, ""


def describe_effect(card: CardDef) -> str:
    return card.description


def sort_cards_for_ai(store: CardRegistry, card_ids: list[str]) -> list[str]:
    """AI 使用顺序：价值高的先用。"""
    def key(cid: str) -> float:
        c = store.get(cid)
        return -(c.ai_weight if c else 0.0)
    return sorted(card_ids, key=key)
