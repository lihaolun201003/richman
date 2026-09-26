"""破产与债务处理。

不允许「money < 0 就死」这种写法。完整流程：

    现金不足
      ├─ 变卖部分资产就能付清 → 按顺序变卖（先卖便宜的、等级低的），付清为止
      └─ 变卖全部也不够       → 判定破产
                                · 现金与全部地产转移给债权人（无债权人则归银行）
                                · 破产者退出，不再参与回合

注意顺序：**先判断够不够，再决定是否变卖**。
如果注定要破产，就不该变卖（变卖只能回收 70%，等于白白亏掉三成），
直接把资产交给债权人更合理。变卖过程会在事件日志里逐条展示。
"""
from __future__ import annotations

from typing import Any

from . import economy
from .events import (
    EventType,
    msg_bankrupt,
    msg_debt,
    msg_liquidate,
    msg_release,
    msg_transfer,
)
from .player import Player
from .state import GameState


class PaymentOutcome:
    """一次付款结算的结果。"""

    __slots__ = ("paid", "liquidated", "bankrupt", "creditor_id", "events", "deficit")

    def __init__(self) -> None:
        self.paid: int = 0
        self.liquidated: list[tuple[str, int]] = []
        self.bankrupt: bool = False
        self.creditor_id: str | None = None
        self.events: list[Any] = []
        self.deficit: int = 0

    @property
    def liquidated_total(self) -> int:
        return sum(v for _, v in self.liquidated)


def _give_cash(payer: Player, creditor: Player | None, amount: int) -> int:
    """从 payer 手里划走 amount 交给 creditor（None 表示银行）。返回实际划走金额。"""
    amount = max(0, min(int(amount), max(0, payer.money)))
    if amount <= 0:
        return 0
    payer.money -= amount
    if creditor is not None and not creditor.bankrupt:
        creditor.money += amount
        creditor.stats["money_earned"] = creditor.stats.get("money_earned", 0) + amount
    return amount


def pay(
    state: GameState,
    payer: Player,
    amount: int,
    creditor_id: str | None = None,
    reason: str = "付款",
) -> PaymentOutcome:
    """让 payer 向 creditor（或银行）支付 amount，自动处理不足部分。"""
    out = PaymentOutcome()
    out.creditor_id = creditor_id
    amount = max(0, int(amount))
    creditor = state.player(creditor_id) if creditor_id else None

    if amount <= 0 or payer.bankrupt:
        return out

    # ---- 现金足够：直接支付
    if payer.money >= amount:
        out.paid = _give_cash(payer, creditor, amount)
        payer.stats["money_spent"] = payer.stats.get("money_spent", 0) + out.paid
        return out

    # ---- 现金不足：先评估能否靠变卖凑够
    recoverable = max(0, payer.money) + sum(p.sell_value for p in state.properties_of(payer.id))
    state.log(EventType.DEBT_STARTED, msg_debt(payer.name, amount), payer.id,
              {"amount": amount, "creditor": creditor_id, "reason": reason,
               "recoverable": recoverable})
    out.events.append(("debt", payer.id, amount, reason))

    if recoverable < amount:
        # 变卖全部也不够 —— 直接破产，把资产整体交给债权人
        _declare_bankrupt(state, payer, creditor, out, amount, reason)
        return out

    # ---- 变卖部分资产即可付清
    if payer.money > 0:
        out.paid = _give_cash(payer, creditor, payer.money)
        payer.stats["money_spent"] = payer.stats.get("money_spent", 0) + out.paid
    else:
        # 现金已经是负数（事件扣成负的）：归零，负数部分视为已核销，不再重复计息
        payer.money = 0
        out.paid = 0
    need = amount - out.paid

    guard = 0
    while need > 0 and guard < 64:
        guard += 1
        candidates = state.properties_of(payer.id)
        if not candidates:
            break
        # 先卖便宜的、等级低的；被抵押的优先处理
        candidates.sort(key=lambda p: (not p.mortgaged, p.level, p.price))
        prop = candidates[0]
        refund = prop.sell_value
        prop.release()
        out.liquidated.append((prop.name, refund))
        state.log(EventType.ASSET_LIQUIDATED,
                  msg_liquidate(payer.name, prop.name, refund), payer.id,
                  {"property": prop.id, "amount": refund})

        paid_now = min(refund, need)
        need -= paid_now
        out.paid += paid_now
        if creditor is not None and not creditor.bankrupt:
            creditor.money += paid_now
            creditor.stats["money_earned"] = creditor.stats.get("money_earned", 0) + paid_now
        # 变卖所得超出欠款的部分归自己
        if refund > paid_now:
            payer.money += refund - paid_now
        state.bump()

    out.deficit = max(0, need)
    if out.deficit > 0:
        # 变卖过程中出现意外（例如资产不足），兜底走破产
        _declare_bankrupt(state, payer, creditor, out, amount, reason)
    return out


def _declare_bankrupt(
    state: GameState,
    player: Player,
    creditor: Player | None,
    out: PaymentOutcome,
    amount: int,
    reason: str,
) -> None:
    """宣告破产：现金与全部地产交给债权人；无债权人则归银行。"""
    player.bankrupt = True
    player.in_jail = False
    player.jail_turns = 0
    state.bankrupt_counter += 1
    player.bankrupt_order = state.bankrupt_counter

    to_creditor = creditor is not None and not creditor.bankrupt

    # 地产
    for prop in state.properties_of(player.id):
        if to_creditor:
            prop.transfer(creditor.id, keep_level=True)
            state.log(EventType.PROPERTY_TRANSFERRED,
                      msg_transfer(player.name, prop.name, creditor.name), player.id,
                      {"property": prop.id, "to": creditor.id})
        else:
            prop.release()
            state.log(EventType.PROPERTY_RELEASED, msg_release(prop.name), player.id,
                      {"property": prop.id})

    # 现金
    remaining_cash = player.money
    if remaining_cash > 0 and to_creditor:
        creditor.money += remaining_cash
        creditor.stats["money_earned"] = creditor.stats.get("money_earned", 0) + remaining_cash
    player.money = 0

    # 道具与状态一并清空
    player.cards.clear()
    player.status_effects.clear()

    out.bankrupt = True
    out.deficit = max(0, amount - out.paid)
    state.log(EventType.BANKRUPT,
              msg_bankrupt(player.name, creditor.name if to_creditor else None), player.id,
              {"creditor": creditor.id if to_creditor else None,
               "order": player.bankrupt_order, "reason": reason})
    out.events.append(("bankrupt", player.id, out.liquidated))
    state.bump()


def settle_negative(state: GameState, player: Player, reason: str = "强制清算") -> PaymentOutcome:
    """把负现金的玩家拉回非负（变卖资产或破产）。"""
    if player.money >= 0 or player.bankrupt:
        return PaymentOutcome()
    return pay(state, player, -player.money, creditor_id=None, reason=reason)


def can_cover(state: GameState, player: Player, amount: int) -> bool:
    """变卖全部资产后能否付得起。"""
    return economy.max_recoverable(state, player) >= amount


def creditor_for(state: GameState, tile_owner_id: str | None) -> str | None:
    """租金类付款的收款方。"""
    return tile_owner_id
