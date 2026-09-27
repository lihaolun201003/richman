"""玩家模型与状态效果系统。

状态效果统一放在 status_effects 列表里，避免出现
player.xxx_boolean_1 / player.xxx_boolean_2 这类散落的布尔字段。
"""
from __future__ import annotations

from typing import Any, Iterable

# ------------------------------------------------------------------ 状态效果

STATUS_RENT_DISCOUNT_ONCE = "rent_discount_once"    # 下次付租减半
STATUS_RENT_DOUBLE_ONCE = "rent_double_once"        # 下次收租翻倍
STATUS_FREE_RENT = "free_rent"                      # 下次付租全免
STATUS_SKIP_TURN = "skip_turn"                      # 跳过 N 个回合
STATUS_PROTECTED = "protected"                      # 免疫他人卡牌
STATUS_PURCHASE_DISCOUNT = "purchase_discount"      # 购地折扣
STATUS_UPGRADE_DISCOUNT = "upgrade_discount"        # 升级折扣
STATUS_FIXED_DICE = "fixed_dice"                    # 下次骰子点数固定
STATUS_RENT_IMMUNE = "rent_immune"                  # 收租豁免（保留扩展）
STATUS_EVENT_IMMUNE = "event_immune"                # 免疫下一次机遇 / 灾难
STATUS_LUCKY = "lucky"                              # 事件收益翻倍
STATUS_CARD_BLOCKED = "card_blocked"                # 下回合不能使用道具
STATUS_TAX_EXEMPT = "tax_exempt"                    # 免税一次
STATUS_JAIL_FREE = "jail_free"                      # 免费出狱一次

#: 需要在回合开始时递减持续时间的状态
TURN_BASED_STATUSES = frozenset(
    {
        STATUS_SKIP_TURN,
        STATUS_PROTECTED,
        STATUS_EVENT_IMMUNE,
        STATUS_LUCKY,
        STATUS_CARD_BLOCKED,
    }
)

STATUS_LABELS = {
    STATUS_RENT_DISCOUNT_ONCE: "下次付租减半",
    STATUS_RENT_DOUBLE_ONCE: "下次收租翻倍",
    STATUS_FREE_RENT: "免租一次",
    STATUS_SKIP_TURN: "暂停回合",
    STATUS_PROTECTED: "受保护",
    STATUS_PURCHASE_DISCOUNT: "购地折扣",
    STATUS_UPGRADE_DISCOUNT: "升级折扣",
    STATUS_FIXED_DICE: "骰子已指定",
    STATUS_RENT_IMMUNE: "免租",
    STATUS_EVENT_IMMUNE: "事件免疫",
    STATUS_LUCKY: "幸运",
    STATUS_CARD_BLOCKED: "道具封锁",
    STATUS_TAX_EXEMPT: "免税",
    STATUS_JAIL_FREE: "免保释",
}

#: 由对手施加的负面状态（净化卡可以清掉）
NEGATIVE_STATUSES = frozenset({STATUS_SKIP_TURN, STATUS_CARD_BLOCKED})


class StatusEffect:
    """一条状态效果。duration 为剩余回合数，-1 表示永久直到被消耗。"""

    __slots__ = ("status", "duration", "trigger", "payload")

    def __init__(
        self,
        status: str,
        duration: int = 1,
        trigger: str = "auto",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.duration = duration
        self.trigger = trigger
        self.payload = payload or {}
        # 带数值的状态（折扣 / 减免…）在这里把数值固化成 modifiers，
        # 于是经济计算完全不需要知道具体是哪个状态
        if "modifiers" not in self.payload:
            try:
                from .modifiers import status_modifiers

                built = status_modifiers(status, self.payload)
                if built:
                    self.payload["modifiers"] = built
            except Exception:  # pragma: no cover - 极端情况下不影响状态本身
                pass

    @property
    def label(self) -> str:
        base = STATUS_LABELS.get(self.status, self.status)
        if self.status in TURN_BASED_STATUSES and self.duration > 1:
            return f"{base}({self.duration})"
        return base

    def tick_turn(self) -> bool:
        """回合结束时递减，返回是否已失效。"""
        if self.duration < 0:
            return False
        self.duration -= 1
        return self.duration <= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "duration": self.duration,
            "trigger": self.trigger,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StatusEffect":
        return cls(
            status=d["status"],
            duration=int(d.get("duration", 1)),
            trigger=d.get("trigger", "auto"),
            payload=dict(d.get("payload") or {}),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Status {self.status} x{self.duration}>"


def new_stats() -> dict[str, int]:
    return {
        "rent_income": 0,
        "rent_paid": 0,
        "properties_bought": 0,
        "properties_upgraded": 0,
        "cards_used": 0,
        "chance_events": 0,
        "start_passes": 0,
        "turns_played": 0,
        "taxes_paid": 0,
        "jail_visits": 0,
        "event_gain": 0,
        "event_loss": 0,
        "money_earned": 0,
        "money_spent": 0,
        "steals_done": 0,
    }


class Player:
    """一名参与者，可能是真人、AI，或掉线后被 AI 接管的真人。"""

    __slots__ = (
        "id", "name", "character_id", "color_id", "slot",
        "is_ai", "is_host", "money", "position",
        "in_jail", "jail_turns", "bankrupt", "bankrupt_order",
        "disconnected", "bot_controlled", "reconnect_token",
        "status_effects", "cards", "stats", "perk", "extra_modifiers",
    )

    def __init__(
        self,
        player_id: str,
        name: str,
        character_id: str,
        color_id: str,
        slot: int = 0,
        is_ai: bool = False,
        is_host: bool = False,
        money: int = 15000,
        start_position: int = 0,
        perk: dict[str, Any] | None = None,
    ) -> None:
        self.id = player_id
        self.name = name
        self.character_id = character_id
        self.color_id = color_id
        self.slot = slot
        self.is_ai = is_ai
        self.is_host = is_host
        self.money = money
        self.position = start_position
        self.in_jail = False
        self.jail_turns = 0
        self.bankrupt = False
        self.bankrupt_order = -1
        self.disconnected = False
        self.bot_controlled = False
        self.reconnect_token = ""
        self.status_effects: list[StatusEffect] = []
        self.cards: list[str] = []
        self.stats: dict[str, int] = new_stats()
        self.perk: dict[str, Any] = perk or {}
        #: 临时性数值修正（道具 / 事件挂上的），与 perk、status 一起参与 modifiers.resolve
        self.extra_modifiers: list[Any] = []

    # ------------------------------------------------------------ 查询
    @property
    def active(self) -> bool:
        """是否仍在游戏中（未破产）。"""
        return not self.bankrupt

    @property
    def controlled_by_ai(self) -> bool:
        """当前是否由 AI 决策（原生 AI 或掉线接管）。"""
        return self.is_ai or self.bot_controlled

    @property
    def perk_kind(self) -> str:
        return str(self.perk.get("kind", ""))

    def perk_value(self, kind: str) -> float:
        if self.perk.get("kind") == kind:
            return float(self.perk.get("value", 0))
        return 0.0

    def card_count(self) -> int:
        return len(self.cards)

    def status_count(self) -> int:
        return len(self.status_effects)

    # ------------------------------------------------------------ 状态效果
    def get_status(self, status: str) -> StatusEffect | None:
        for eff in self.status_effects:
            if eff.status == status:
                return eff
        return None

    def has_status(self, status: str) -> bool:
        return self.get_status(status) is not None

    def add_status(self, effect: StatusEffect) -> None:
        """同类效果叠加时取更长的持续时间，避免互相覆盖丢效果。"""
        exist = self.get_status(effect.status)
        if exist is None:
            self.status_effects.append(effect)
            return
        if effect.duration < 0 or exist.duration < 0:
            exist.duration = -1
        else:
            exist.duration = max(exist.duration, effect.duration)
        if effect.payload:
            exist.payload.update(effect.payload)

    def remove_status(self, status: str) -> bool:
        eff = self.get_status(status)
        if eff is None:
            return False
        self.status_effects.remove(eff)
        return True

    def consume_status(self, status: str) -> StatusEffect | None:
        """取出并移除一条状态（一次性效果消耗）。"""
        eff = self.get_status(status)
        if eff is not None:
            self.status_effects.remove(eff)
        return eff

    def tick_statuses(self) -> list[str]:
        """回合结束调用，返回本次失效的状态名列表。"""
        expired: list[str] = []
        for eff in list(self.status_effects):
            if eff.status in TURN_BASED_STATUSES:
                if eff.tick_turn():
                    expired.append(eff.status)
                    self.status_effects.remove(eff)
        return expired

    # ------------------------------------------------------------ 卡片
    def add_card(self, card_id: str, limit: int = 5) -> bool:
        if len(self.cards) >= limit:
            return False
        self.cards.append(card_id)
        return True

    def remove_card(self, card_id: str) -> bool:
        if card_id in self.cards:
            self.cards.remove(card_id)
            return True
        return False

    def add_modifier(self, modifier: Any) -> None:
        """挂上一条临时数值修正（道具 / 事件用）。"""
        self.extra_modifiers.append(modifier)

    def remove_modifier(self, hook: str, source: str = "") -> bool:
        for m in list(self.extra_modifiers):
            if getattr(m, "hook", "") == hook and (not source or getattr(m, "source", "") == source):
                self.extra_modifiers.remove(m)
                return True
        return False

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "character_id": self.character_id,
            "color_id": self.color_id,
            "slot": self.slot,
            "is_ai": self.is_ai,
            "is_host": self.is_host,
            "money": self.money,
            "position": self.position,
            "in_jail": self.in_jail,
            "jail_turns": self.jail_turns,
            "bankrupt": self.bankrupt,
            "bankrupt_order": self.bankrupt_order,
            "disconnected": self.disconnected,
            "bot_controlled": self.bot_controlled,
            "status_effects": [e.to_dict() for e in self.status_effects],
            "cards": list(self.cards),
            "stats": dict(self.stats),
            "perk": dict(self.perk),
            "extra_modifiers": [
                m.to_dict() if hasattr(m, "to_dict") else dict(m)
                for m in self.extra_modifiers
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Player":
        p = cls(
            player_id=d["id"],
            name=d["name"],
            character_id=d.get("character_id", ""),
            color_id=d.get("color_id", "red"),
            slot=int(d.get("slot", 0)),
            is_ai=bool(d.get("is_ai", False)),
            is_host=bool(d.get("is_host", False)),
            money=int(d.get("money", 0)),
            start_position=int(d.get("position", 0)),
            perk=dict(d.get("perk") or {}),
        )
        p.position = int(d.get("position", 0))
        p.in_jail = bool(d.get("in_jail", False))
        p.jail_turns = int(d.get("jail_turns", 0))
        p.bankrupt = bool(d.get("bankrupt", False))
        p.bankrupt_order = int(d.get("bankrupt_order", -1))
        p.disconnected = bool(d.get("disconnected", False))
        p.bot_controlled = bool(d.get("bot_controlled", False))
        p.reconnect_token = d.get("reconnect_token", "")
        p.status_effects = [StatusEffect.from_dict(e) for e in (d.get("status_effects") or [])]
        p.cards = list(d.get("cards") or [])
        stats = new_stats()
        stats.update({k: int(v) for k, v in (d.get("stats") or {}).items()})
        p.stats = stats
        from .modifiers import Modifier

        p.extra_modifiers = [
            m if isinstance(m, Modifier) else Modifier.from_dict(m)
            for m in (d.get("extra_modifiers") or [])
        ]
        return p

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Player {self.name} ${self.money} pos={self.position}>"


def pick_color(palette: Iterable[dict[str, Any]], used: set[str]) -> dict[str, Any]:
    """从调色板挑选一个未被占用的颜色。"""
    items = list(palette)
    for item in items:
        if item["id"] not in used:
            used.add(item["id"])
            return item
    # 全部被占用时循环复用（6 色对 6 人，正常不会发生）
    item = items[len(used) % len(items)]
    used.add(item["id"])
    return item
