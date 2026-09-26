"""游戏事件（GameEvent）与中文日志文本。

所有面向玩家的叙述都在这里生成，UI 只负责展示，
保证单机与联机日志文案一致。
"""
from __future__ import annotations

from typing import Any


class EventType:
    """事件类型常量。"""

    GAME_START = "GAME_START"
    TURN_START = "TURN_START"
    TURN_END = "TURN_END"

    DICE_ROLLED = "DICE_ROLLED"
    PLAYER_MOVED = "PLAYER_MOVED"
    PASSED_START = "PASSED_START"
    LANDED = "LANDED"

    PROPERTY_BOUGHT = "PROPERTY_BOUGHT"
    PROPERTY_SKIPPED = "PROPERTY_SKIPPED"
    PROPERTY_UPGRADED = "PROPERTY_UPGRADED"
    PROPERTY_SOLD = "PROPERTY_SOLD"
    PROPERTY_MORTGAGED = "PROPERTY_MORTGAGED"
    PROPERTY_UNMORTGAGED = "PROPERTY_UNMORTGAGED"
    PROPERTY_TRANSFERRED = "PROPERTY_TRANSFERRED"
    PROPERTY_RELEASED = "PROPERTY_RELEASED"

    RENT_PAID = "RENT_PAID"
    RENT_DISCOUNTED = "RENT_DISCOUNTED"
    RENT_DOUBLED = "RENT_DOUBLED"
    RENT_FREE = "RENT_FREE"
    TAX_PAID = "TAX_PAID"

    CHANCE_DRAWN = "CHANCE_DRAWN"
    CHANCE_APPLIED = "CHANCE_APPLIED"
    CARD_GAINED = "CARD_GAINED"
    CARD_USED = "CARD_USED"
    CARD_LOST = "CARD_LOST"

    JAIL_ENTERED = "JAIL_ENTERED"
    JAIL_PAID = "JAIL_PAID"
    JAIL_ROLL_FAILED = "JAIL_ROLL_FAILED"
    JAIL_RELEASED = "JAIL_RELEASED"

    BONUS_POOL_GAINED = "BONUS_POOL_GAINED"
    BONUS_POOL_PAID = "BONUS_POOL_PAID"

    DEBT_STARTED = "DEBT_STARTED"
    ASSET_LIQUIDATED = "ASSET_LIQUIDATED"
    BANKRUPT = "BANKRUPT"
    SURRENDERED = "SURRENDERED"

    PLAYER_DISCONNECTED = "PLAYER_DISCONNECTED"
    PLAYER_RECONNECTED = "PLAYER_RECONNECTED"
    PLAYER_BOT_TAKEOVER = "PLAYER_BOT_TAKEOVER"

    GAME_OVER = "GAME_OVER"
    PHASE_CHANGED = "PHASE_CHANGED"
    INFO = "INFO"


#: 需要在 UI 中弹出提示板（而不是只进日志）的事件类型
ALERT_EVENTS = frozenset(
    {
        EventType.CHANCE_DRAWN,
        EventType.BANKRUPT,
        EventType.GAME_OVER,
        EventType.DEBT_STARTED,
        EventType.JAIL_ENTERED,
        EventType.BONUS_POOL_GAINED,
    }
)


class GameEvent:
    """一条游戏事件记录。既是日志条目，也是客户端播放动画的依据。"""

    __slots__ = ("event_id", "type", "player_id", "message", "data", "revision", "tick", "seq")

    def __init__(
        self,
        event_id: str,
        etype: str,
        message: str,
        player_id: str | None = None,
        data: dict[str, Any] | None = None,
        revision: int = 0,
        tick: float = 0.0,
        seq: int = 0,
    ) -> None:
        self.event_id = event_id
        self.type = etype
        self.player_id = player_id
        self.message = message
        self.data = data or {}
        self.revision = revision
        self.tick = tick
        self.seq = seq

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "player_id": self.player_id,
            "message": self.message,
            "data": dict(self.data),
            "revision": self.revision,
            "tick": self.tick,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GameEvent":
        return cls(
            event_id=d["event_id"],
            etype=d["type"],
            message=d.get("message", ""),
            player_id=d.get("player_id"),
            data=dict(d.get("data") or {}),
            revision=int(d.get("revision", 0)),
            tick=float(d.get("tick", 0.0)),
            seq=int(d.get("seq", 0)),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.type} {self.message}>"


# ------------------------------------------------------------------ 文本辅助

def money(v: int) -> str:
    """金额格式化：1234 -> 1,234"""
    return f"{int(v):,}"


def fmt_dice(die1: int, die2: int) -> str:
    return f"{die1} + {die2} = {die1 + die2}"


def fmt_money_delta(v: int) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}{money(abs(v))}"


# ------------------------------------------------------------------ 文案模板

def msg_game_start(board_name: str) -> str:
    return f"游戏开始！棋盘：{board_name}"


def msg_turn_start(name: str, round_no: int) -> str:
    return f"第 {round_no} 轮 · 轮到 {name} 行动"


def msg_dice(name: str, die1: int, die2: int) -> str:
    return f"{name} 掷出了 {fmt_dice(die1, die2)} 点"


def msg_move(name: str, steps: int, target: str) -> str:
    verb = "前进" if steps >= 0 else "后退"
    return f"{name} {verb} {abs(steps)} 格，抵达「{target}」"


def msg_pass_start(name: str, amount: int) -> str:
    return f"{name} 经过起点，获得 {money(amount)}"


def msg_land_start(name: str, amount: int) -> str:
    return f"{name} 停在起点，获得 {money(amount)}"


def msg_buy(name: str, prop: str, price: int) -> str:
    return f"{name} 买下了「{prop}」，花费 {money(price)}"


def msg_buy_discount(name: str, prop: str, price: int, origin: int) -> str:
    return f"{name} 以折扣价买下「{prop}」，花费 {money(price)}（原价 {money(origin)}）"


def msg_skip_buy(name: str, prop: str) -> str:
    return f"{name} 放弃购买「{prop}」"


def msg_upgrade(name: str, prop: str, level: int, cost: int) -> str:
    return f"{name} 把「{prop}」升到 {level} 级，花费 {money(cost)}"


def msg_upgrade_free(name: str, prop: str, level: int) -> str:
    return f"{name} 免费把「{prop}」升到 {level} 级"


def msg_sell(name: str, prop: str, amount: int) -> str:
    return f"{name} 出售了「{prop}」，回收 {money(amount)}"


def msg_rent(payer: str, owner: str, prop: str, amount: int) -> str:
    return f"{payer} 进入「{prop}」，向 {owner} 支付租金 {money(amount)}"


def msg_rent_double(payer: str, owner: str, prop: str, amount: int, origin: int) -> str:
    return f"{payer} 进入「{prop}」，租金翻倍，向 {owner} 支付 {money(amount)}（原 {money(origin)}）"


def msg_rent_discount(payer: str, owner: str, prop: str, amount: int, origin: int) -> str:
    return f"{payer} 进入「{prop}」，租金减半，向 {owner} 支付 {money(amount)}（原 {money(origin)}）"


def msg_rent_free(payer: str, prop: str) -> str:
    return f"{payer} 使用免租效果，「{prop}」的租金被免除"


def msg_rent_immune(owner: str, prop: str) -> str:
    return f"{owner} 处于保护状态，「{prop}」本次不收取租金"


def msg_tax(name: str, label: str, amount: int) -> str:
    return f"{name} 缴纳{label} {money(amount)}"


def msg_tax_pool(label: str, amount: int) -> str:
    return f"{label} {money(amount)} 已注入城市奖金池"


def msg_chance(name: str, title: str) -> str:
    return f"{name} 抽到机遇：「{title}」"


def msg_chance_detail(detail: str) -> str:
    return f"机遇效果：{detail}"


def msg_card_gained(name: str, card: str) -> str:
    return f"{name} 获得了道具「{card}」"


def msg_card_used(name: str, card: str) -> str:
    return f"{name} 使用了道具「{card}」"


def msg_card_lost(name: str, card: str) -> str:
    return f"{name} 失去了道具「{card}」"


def msg_jail_enter(name: str) -> str:
    return f"{name} 被押送到看守所"


def msg_jail_pay(name: str, amount: int) -> str:
    return f"{name} 支付保释金 {money(amount)} 离开看守所"


def msg_jail_fail(name: str, total: int, need: int) -> str:
    return f"{name} 在看守所掷出 {total} 点（需 {need} 点），未能离开"


def msg_jail_roll_ok(name: str, total: int) -> str:
    return f"{name} 在看守所掷出 {total} 点，成功离开"


def msg_jail_auto_pay(name: str, amount: int) -> str:
    return f"{name} 关押期满，自动支付保释金 {money(amount)} 离开看守所"


def msg_pool_gain(name: str, amount: int) -> str:
    return f"{name} 领取了城市奖金池 {money(amount)}"


def msg_pool_pay(name: str, amount: int) -> str:
    return f"{name} 向城市奖金池注入 {money(amount)}"


def msg_pool_all(amount: int, count: int) -> str:
    return f"共 {count} 名玩家各向城市奖金池注入 {money(amount)}"


def msg_bankrupt(name: str, creditor: str | None) -> str:
    if creditor:
        return f"{name} 资不抵债，破产退出，剩余资产归 {creditor}"
    return f"{name} 资不抵债，破产退出，资产由银行收回"


def msg_debt(name: str, amount: int) -> str:
    return f"{name} 现金不足，需要筹集 {money(amount)}"


def msg_liquidate(name: str, prop: str, amount: int) -> str:
    return f"{name} 变卖「{prop}」换取 {money(amount)}"


def msg_transfer(name: str, prop: str, new_owner: str) -> str:
    return f"「{prop}」转移到 {new_owner} 名下"


def msg_release(prop: str) -> str:
    return f"「{prop}」被银行收回，恢复无主状态"


def msg_game_over(name: str, rounds: int) -> str:
    return f"游戏结束！{name} 在第 {rounds} 轮成为最后的赢家"


def msg_disconnected(name: str) -> str:
    return f"{name} 与房间失去连接"


def msg_reconnected(name: str) -> str:
    return f"{name} 重新连接成功"


def msg_bot_takeover(name: str) -> str:
    return f"{name} 掉线超时，由 AI 代为操作"


def msg_surrender(name: str) -> str:
    return f"{name} 主动认输，退出游戏"


def msg_fixed_dice(name: str, total: int) -> str:
    return f"{name} 的骰子点数被指定为 {total}"


def msg_barrier_placed(name: str, tile: str) -> str:
    return f"{name} 在「{tile}」放置了路障"


def msg_barrier_hit(name: str, tile: str) -> str:
    return f"{name} 撞上路障，被迫停在「{tile}」"


def msg_roll_again(name: str) -> str:
    return f"{name} 获得再次掷骰的机会"


def msg_skip_turn(name: str) -> str:
    return f"{name} 本回合被跳过"


def msg_steal(actor: str, target: str, amount: int) -> str:
    return f"{actor} 从 {target} 手中抢走 {money(amount)}"


def msg_shop(label: str) -> str:
    return label
