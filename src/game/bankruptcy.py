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
from .ledger import Reason
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

    __slots__ = ("paid", "liquidated", "bankrupt", "creditor_id", "events",
                 "deficit", "deferred")

    def __init__(self) -> None:
        self.paid: int = 0
        self.liquidated: list[tuple[str, int]] = []
        self.bankrupt: bool = False
        self.creditor_id: str | None = None
        self.events: list[Any] = []
        self.deficit: int = 0
        #: 钱不够但「抵押 + 变卖」或许能救回来 —— 交给引擎启动债务处理流程，
        #: 而不是当场判死。玩家因此获得真正的自救机会。
        self.deferred: bool = False

    @property
    def liquidated_total(self) -> int:
        return sum(v for _, v in self.liquidated)


def _give_cash(state: GameState, payer: Player, creditor: Player | None,
               amount: int, reason: str = "付款",
               category: str | None = None) -> int:
    """从 payer 手里划走 amount 交给 creditor（None 表示银行）。返回实际划走金额。"""
    amount = max(0, min(int(amount), max(0, payer.money)))
    if amount <= 0:
        return 0
    state.ledger.transfer(state, payer, creditor, amount,
                          category or Reason.EVENT_FEE, reason)
    return amount


def pay(
    state: GameState,
    payer: Player,
    amount: int,
    creditor_id: str | None = None,
    reason: str = "付款",
    defer: bool = True,
    category: str | None = None,
) -> PaymentOutcome:
    """让 payer 向 creditor（或银行）支付 amount，自动处理不足部分。

    分三种走向：
    1. 现金够 → 直接付清；
    2. 现金不够，但变卖部分资产就能付清 → 自动变卖抵债（不打断节奏）；
    3. 连「变卖 + 抵押」都不够 → 若 defer=True，标记 deferred 交由引擎
       启动债务处理流程（玩家自救）；defer=False 才当场破产。
    """
    out = PaymentOutcome()
    out.creditor_id = creditor_id
    amount = max(0, int(amount))
    creditor = state.player(creditor_id) if creditor_id else None
    # reason 是给玩家看的说明，category 是给统计用的分类；两者不能混用，
    # 否则经济报表里会出现「湖滨大道 租金」这种一次性类别。
    category = category or Reason.EVENT_FEE

    if amount <= 0 or payer.bankrupt:
        return out

    # ---- 现金足够：直接支付
    if payer.money >= amount:
        out.paid = _give_cash(state, payer, creditor, amount, reason, category)
        return out

    # ---- 现金不足：先评估能否靠变卖凑够
    recoverable = max(0, payer.money) + sum(p.sell_value for p in state.properties_of(payer.id))
    state.log(EventType.DEBT_STARTED, msg_debt(payer.name, amount), payer.id,
              {"amount": amount, "creditor": creditor_id, "reason": reason,
               "recoverable": recoverable})
    out.events.append(("debt", payer.id, amount, reason))

    # 抵押能凑到的上限：抵押只换现金、不动产权。
    # 注意：抵押的筹款能力（地价 60%）天然低于卖地（投资额 70%），
    # 所以「抵押能救」必然蕴含「卖地也能救」。抵押的价值不在于筹更多钱，
    # 而在于**保住产权** —— 因此只要抵押能付清，就该让玩家自己选，
    # 而不是替他决定把地卖掉。
    mortgage_power = max(0, payer.money) + sum(
        economy.mortgage_value(state, payer, p)[0]
        for p in state.properties_of(payer.id)
    )

    # 判定顺序（从「对玩家最有利」到「最不利」）：
    #   1. 抵押就能付清 → 转入债务处理，玩家自己选抵押还是变卖；
    #   2. 抵押不够、但变卖能付清 → 自动变卖抵债，不打断节奏；
    #   3. 卖光也不够 → 直接破产，资产整体交给债权人。
    if mortgage_power >= amount and defer:
        out.deferred = True
        out.deficit = amount - max(0, payer.money)
        return out
    if recoverable < amount:
        _declare_bankrupt(state, payer, creditor, out, amount, reason)
        return out

    # ---- 变卖部分资产即可付清
    if payer.money > 0:
        out.paid = _give_cash(state, payer, creditor, payer.money, reason, category)
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
        refund, _ = economy.sell_refund(state, payer, prop)
        prop.release()
        out.liquidated.append((prop.name, refund))
        state.log(EventType.ASSET_LIQUIDATED,
                  msg_liquidate(payer.name, prop.name, refund), payer.id,
                  {"property": prop.id, "amount": refund})

        # 先让卖地收入入账，再从账上支付欠款：
        # 这样「卖得比欠款多」时余额自然留在自己手里，账目也清晰。
        state.ledger.gain(state, payer, refund, Reason.PROPERTY_SALE,
                          f"变卖 {prop.name}")
        paid_now = min(refund, need)
        need -= paid_now
        out.paid += paid_now
        if paid_now > 0:
            state.ledger.transfer(state, payer, creditor, paid_now,
                                  category, f"变卖 {prop.name} 抵债")
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
    # 先记下破产瞬间的「死因画像」：玩家会想知道他欠了多少、还剩多少、几块地。
    # 这些数字必须在清零与转移之前取，之后就再也拿不到了。
    owned = state.properties_of(player.id)
    debt = max(0, int(amount) - out.paid)
    assets_before = max(0, player.money) + sum(p.asset_value for p in owned)

    player.bankrupt = True
    player.in_jail = False
    player.jail_turns = 0
    state.bankrupt_counter += 1
    player.bankrupt_order = state.bankrupt_counter

    to_creditor = creditor is not None and not creditor.bankrupt

    # 地产
    title_count = 0
    for prop in state.properties_of(player.id):
        title_count += 1
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
    if remaining_cash > 0:
        state.ledger.transfer(state, player, creditor if to_creditor else None,
                              remaining_cash, Reason.BANKRUPT, "破产清算")
    player.money = 0

    # 道具与状态一并清空
    player.cards.clear()
    player.status_effects.clear()

    out.bankrupt = True
    out.deficit = debt
    state.log(EventType.BANKRUPT,
              msg_bankrupt(player.name, creditor.name if to_creditor else None), player.id,
              {"creditor": creditor.id if to_creditor else None,
               "creditor_name": creditor.name if to_creditor else "",
               "order": player.bankrupt_order, "reason": reason,
               "debt": debt, "assets": assets_before, "properties": title_count,
               "alert": True})
    out.events.append(("bankrupt", player.id, out.liquidated))
    state.bump()


def settle_now(state: GameState, payer: Player, amount: int,
               creditor_id: str | None = None,
               reason: str = "付款",
               category: str | None = None) -> PaymentOutcome:
    """假定现金已备足，直接支付（债务处理流程收尾用）。"""
    out = PaymentOutcome()
    out.creditor_id = creditor_id
    amount = max(0, int(amount))
    creditor = state.player(creditor_id) if creditor_id else None
    if amount <= 0 or payer.bankrupt:
        return out
    out.paid = _give_cash(state, payer, creditor, amount, reason,
                          category or Reason.EVENT_FEE)
    return out


def settle_negative(state: GameState, player: Player, reason: str = "强制清算") -> PaymentOutcome:
    """把负现金的玩家拉回非负（变卖资产或破产）。"""
    if player.money >= 0 or player.bankrupt:
        return PaymentOutcome()
    return pay(state, player, -player.money, creditor_id=None, reason=reason, defer=False)


def can_cover(state: GameState, player: Player, amount: int) -> bool:
    """变卖全部资产后能否付得起。"""
    return economy.max_recoverable(state, player) >= amount


def creditor_for(state: GameState, tile_owner_id: str | None) -> str | None:
    """租金类付款的收款方。"""
    return tile_owner_id
