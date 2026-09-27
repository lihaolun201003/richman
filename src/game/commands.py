"""Command 与 PendingDecision 模型。

客户端只能发 Command；Host 负责 validate → execute。
所有「等待真人操作」统一表达为 PendingDecision，
客户端提交时必须带上 decision_id，避免旧窗口回答新问题。
"""
from __future__ import annotations

import uuid
from typing import Any


class CommandType:
    """命令类型常量。"""

    # 回合流程
    ROLL_DICE = "ROLL_DICE"
    END_TURN = "END_TURN"
    CONFIRM_EVENT = "CONFIRM_EVENT"

    # 地产
    BUY_PROPERTY = "BUY_PROPERTY"
    SKIP_PROPERTY = "SKIP_PROPERTY"
    UPGRADE_PROPERTY = "UPGRADE_PROPERTY"
    SELL_PROPERTY = "SELL_PROPERTY"
    MORTGAGE_PROPERTY = "MORTGAGE_PROPERTY"
    UNMORTGAGE_PROPERTY = "UNMORTGAGE_PROPERTY"

    # 卡牌
    USE_CARD = "USE_CARD"
    SELECT_TARGET = "SELECT_TARGET"

    # 看守所
    PAY_JAIL = "PAY_JAIL"
    ROLL_FOR_JAIL = "ROLL_FOR_JAIL"

    # 资产操作（自己回合内可执行）
    MORTGAGE_PROPERTY = "MORTGAGE_PROPERTY"
    REDEEM_PROPERTY = "REDEEM_PROPERTY"
    DOWNGRADE_PROPERTY = "DOWNGRADE_PROPERTY"

    # 商店
    BUY_SHOP_CARD = "BUY_SHOP_CARD"
    LEAVE_SHOP = "LEAVE_SHOP"

    # 破产清算
    SELL_ASSET = "SELL_ASSET"
    DECLARE_BANKRUPTCY = "DECLARE_BANKRUPTCY"
    STOP_LIQUIDATION = "STOP_LIQUIDATION"

    # 通用决策响应（推荐 LAN 使用，带 decision_id）
    RESOLVE_DECISION = "RESOLVE_DECISION"

    # 通用
    SURRENDER = "SURRENDER"


ALL_COMMAND_TYPES = {
    v for k, v in vars(CommandType).items()
    if not k.startswith("_") and isinstance(v, str)
}


def new_command_id() -> str:
    return uuid.uuid4().hex[:12]


class Command:
    """玩家意图。客户端唯一能对游戏状态施加影响的方式。"""

    __slots__ = ("type", "player_id", "command_id", "payload", "client_revision", "decision_id")

    def __init__(
        self,
        ctype: str,
        player_id: str,
        payload: dict[str, Any] | None = None,
        command_id: str | None = None,
        client_revision: int = -1,
        decision_id: str | None = None,
    ) -> None:
        self.type = ctype
        self.player_id = player_id
        self.payload = payload or {}
        self.command_id = command_id or new_command_id()
        self.client_revision = int(client_revision)
        self.decision_id = decision_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "player_id": self.player_id,
            "command_id": self.command_id,
            "payload": dict(self.payload),
            "client_revision": self.client_revision,
            "decision_id": self.decision_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Command":
        return cls(
            ctype=d["type"],
            player_id=d.get("player_id", ""),
            payload=dict(d.get("payload") or {}),
            command_id=d.get("command_id"),
            client_revision=int(d.get("client_revision", -1)),
            decision_id=d.get("decision_id"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Command {self.type} by {self.player_id} {self.payload}>"


class DecisionOption:
    """决策中的一个可选项。"""

    __slots__ = ("id", "label", "command_type", "payload", "enabled", "hint", "danger")

    def __init__(
        self,
        option_id: str,
        label: str,
        command_type: str,
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
        hint: str = "",
        danger: bool = False,
    ) -> None:
        self.id = option_id
        self.label = label
        self.command_type = command_type
        self.payload = payload or {}
        self.enabled = enabled
        self.hint = hint
        self.danger = danger

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "command_type": self.command_type,
            "payload": dict(self.payload),
            "enabled": self.enabled,
            "hint": self.hint,
            "danger": self.danger,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionOption":
        return cls(
            option_id=d["id"],
            label=d["label"],
            command_type=d["command_type"],
            payload=dict(d.get("payload") or {}),
            enabled=bool(d.get("enabled", True)),
            hint=d.get("hint", ""),
            danger=bool(d.get("danger", False)),
        )


class DecisionKind:
    """决策种类。"""

    ROLL = "roll"
    BUY_PROPERTY = "buy_property"
    UPGRADE_PROPERTY = "upgrade_property"
    JAIL = "jail"
    CARD_TARGET_TILE = "card_target_tile"
    CARD_TARGET_PLAYER = "card_target_player"
    CARD_TARGET_OWN_PROPERTY = "card_target_own_property"
    CARD_DICE_VALUE = "card_dice_value"
    CHANCE_ACK = "chance_ack"
    BANKRUPTCY = "bankruptcy"
    DEBT_RESOLUTION = "debt_resolution"
    SHOP = "shop"
    ASSET_MANAGE = "asset_manage"
    GAME_OVER = "game_over"


class PendingDecision:
    """等待某个玩家（或其控制器）作出的决策。"""

    __slots__ = (
        "id", "player_id", "kind", "title", "description",
        "options", "context", "cancellable", "created_revision", "seq",
    )

    def __init__(
        self,
        decision_id: str,
        player_id: str,
        kind: str,
        title: str,
        options: list[DecisionOption],
        description: str = "",
        context: dict[str, Any] | None = None,
        cancellable: bool = False,
        created_revision: int = 0,
        seq: int = 0,
    ) -> None:
        self.id = decision_id
        self.player_id = player_id
        self.kind = kind
        self.title = title
        self.description = description
        self.options = options
        self.context = context or {}
        self.cancellable = cancellable
        self.created_revision = created_revision
        self.seq = seq

    def option(self, option_id: str) -> DecisionOption | None:
        for opt in self.options:
            if opt.id == option_id:
                return opt
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "player_id": self.player_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "options": [o.to_dict() for o in self.options],
            "context": dict(self.context),
            "cancellable": self.cancellable,
            "created_revision": self.created_revision,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PendingDecision":
        return cls(
            decision_id=d["id"],
            player_id=d["player_id"],
            kind=d["kind"],
            title=d["title"],
            options=[DecisionOption.from_dict(o) for o in d.get("options", [])],
            description=d.get("description", ""),
            context=dict(d.get("context") or {}),
            cancellable=bool(d.get("cancellable", False)),
            created_revision=int(d.get("created_revision", 0)),
            seq=int(d.get("seq", 0)),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Decision {self.id} {self.kind} for {self.player_id}>"


class CommandResult:
    """命令处理结果，用于给发起者回执与错误提示。"""

    __slots__ = ("ok", "reason", "events")

    def __init__(self, ok: bool, reason: str = "", events: list[Any] | None = None) -> None:
        self.ok = ok
        self.reason = reason
        self.events = events or []

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CommandResult ok={self.ok} {self.reason}>"
