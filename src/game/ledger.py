"""EconomyLedger：所有资金变化的唯一出口。

以前代码里到处是 `player.money += x`，导致：
- 无法回答「这一局钱都从哪来、到哪去」；
- 平衡分析只能靠猜；
- 结算界面拿不出「总收入 / 总支出」的分类明细。

现在所有规则层的资金变化都走这里：
    gain()      银行 → 玩家
    pay_bank()  玩家 → 银行
    transfer()  玩家 → 玩家
每次调用都记录 reason（中文）与分类（英文枚举），
既能给玩家看，也能给 balance_simulation.py 做统计。
"""
from __future__ import annotations

from typing import Any


class Reason:
    """资金变化的分类。"""

    START_REWARD = "start_reward"          # 经过 / 停留起点
    INITIAL_MONEY = "initial_money"        # 开局资金
    PROPERTY_PURCHASE = "purchase"         # 购地
    PROPERTY_UPGRADE = "upgrade"           # 升级
    PROPERTY_SALE = "sale"                 # 卖地
    MORTGAGE = "mortgage"                  # 抵押获得的现金
    REDEEM = "redeem"                      # 赎回支出
    RENT = "rent"                          # 租金
    TAX = "tax"                            # 税收
    CHANCE = "chance"                      # 机遇事件
    CARD = "card"                          # 道具效果
    SHOP = "shop"                          # 商店购卡
    JAIL = "jail"                          # 保释金
    BONUS_POOL = "bonus_pool"              # 奖金池
    BANKRUPT = "bankrupt"                  # 破产清算
    CARD_STEAL = "card_steal"              # 抢夺卡
    EVENT_FEE = "event_fee"                # 事件罚款 / 摊派

    @classmethod
    def label(cls, reason: str) -> str:
        return {
            cls.START_REWARD: "经过起点",
            cls.INITIAL_MONEY: "开局资金",
            cls.PROPERTY_PURCHASE: "购买地产",
            cls.PROPERTY_UPGRADE: "升级地产",
            cls.PROPERTY_SALE: "出售地产",
            cls.MORTGAGE: "抵押地产",
            cls.REDEEM: "赎回地产",
            cls.RENT: "租金",
            cls.TAX: "税收",
            cls.CHANCE: "机遇事件",
            cls.CARD: "道具",
            cls.SHOP: "商店",
            cls.JAIL: "保释金",
            cls.BONUS_POOL: "奖金池",
            cls.BANKRUPT: "破产清算",
            cls.CARD_STEAL: "被抢夺",
            cls.EVENT_FEE: "事件摊派",
        }.get(reason, reason)


class LedgerEntry:
    """一笔资金流水。"""

    __slots__ = ("seq", "reason", "category", "amount", "player_id",
                 "counterparty_id", "detail", "revision")

    def __init__(self, seq: int, category: str, amount: int, player_id: str,
                 counterparty_id: str | None = None, detail: str = "",
                 revision: int = 0) -> None:
        self.seq = seq
        self.category = category
        self.reason = Reason.label(category)
        self.amount = int(amount)      # 正数表示该玩家收到钱，负数表示支出
        self.player_id = player_id
        self.counterparty_id = counterparty_id
        self.detail = detail
        self.revision = revision

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "category": self.category, "reason": self.reason,
            "amount": self.amount, "player_id": self.player_id,
            "counterparty_id": self.counterparty_id, "detail": self.detail,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LedgerEntry":
        return cls(int(d.get("seq", 0)), d.get("category", ""), int(d.get("amount", 0)),
                   d.get("player_id", ""), d.get("counterparty_id"),
                   d.get("detail", ""), int(d.get("revision", 0)))

    def __repr__(self) -> str:  # pragma: no cover
        sign = "+" if self.amount >= 0 else ""
        return f"<Ledger {self.player_id} {sign}{self.amount} {self.reason}>"


#: 流水保留上限（存档会裁剪，避免无限增长）
MAX_ENTRIES = 1500


class EconomyLedger:
    """一局游戏的全部资金流水。"""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self._seq = 0

    # ------------------------------------------------------------ 记账

    def _record(self, state: Any, category: str, amount: int, player_id: str,
                counterparty_id: str | None = None, detail: str = "") -> LedgerEntry:
        self._seq += 1
        entry = LedgerEntry(self._seq, category, amount, player_id,
                            counterparty_id, detail,
                            revision=getattr(state, "revision", 0))
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            del self.entries[: len(self.entries) - MAX_ENTRIES]
        return entry

    # ---- 三个入口（规则层只能通过它们改钱）

    def gain(self, state: Any, player: Any, amount: int, category: str,
             detail: str = "") -> int:
        """银行 → 玩家。返回实际到账金额。"""
        amount = max(0, int(amount))
        if amount <= 0 or player is None or getattr(player, "bankrupt", False):
            return 0
        player.money += amount
        player.stats["money_earned"] = player.stats.get("money_earned", 0) + amount
        self._record(state, category, amount, player.id, None, detail)
        return amount

    def pay_bank(self, state: Any, player: Any, amount: int, category: str,
                 detail: str = "") -> int:
        """玩家 → 银行。允许把余额扣成负数（债务流程会处理）。

        返回实际扣款额（可能大于玩家原有现金，表示欠款）。
        """
        amount = max(0, int(amount))
        if amount <= 0 or player is None:
            return 0
        player.money -= amount
        player.stats["money_spent"] = player.stats.get("money_spent", 0) + amount
        self._record(state, category, -amount, player.id, None, detail)
        return amount

    def transfer(self, state: Any, payer: Any, receiver: Any, amount: int,
                 category: str, detail: str = "") -> int:
        """玩家 → 玩家。返回实际转移金额。

        付款方可以扣成负数（表示欠款），但收款方只收到付款方「真正拿得出」的部分由
        调用方（bankruptcy.pay）负责结算，这里只处理全额成功的场景。
        """
        amount = max(0, int(amount))
        if amount <= 0 or payer is None:
            return 0
        payer.money -= amount
        payer.stats["money_spent"] = payer.stats.get("money_spent", 0) + amount
        if receiver is not None and not getattr(receiver, "bankrupt", False):
            receiver.money += amount
            receiver.stats["money_earned"] = receiver.stats.get("money_earned", 0) + amount
        self._record(state, category, -amount, payer.id,
                     receiver.id if receiver else None, detail)
        if receiver is not None and not getattr(receiver, "bankrupt", False):
            self._record(state, category, amount, receiver.id, payer.id, detail)
        return amount

    # ------------------------------------------------------------ 统计

    def player_summary(self, player_id: str) -> dict[str, Any]:
        """某个玩家的收支分类汇总（给结算界面 / AI / HUD 用）。"""
        income: dict[str, int] = {}
        expense: dict[str, int] = {}
        for e in self.entries:
            if e.player_id != player_id:
                continue
            bucket = income if e.amount >= 0 else expense
            bucket[e.reason] = bucket.get(e.reason, 0) + abs(e.amount)
        return {
            "income": income,
            "expense": expense,
            "total_income": sum(income.values()),
            "total_expense": sum(expense.values()),
        }

    def category_totals(self) -> dict[str, int]:
        """全场的分类净流量（用于平衡分析）。"""
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.reason] = out.get(e.reason, 0) + e.amount
        return out

    def net_created(self) -> int:
        """银行净投放（正数表示系统向玩家净注入资金）。

        只统计「玩家 ↔ 银行」的部分：玩家间转移互相抵消。
        """
        net = 0
        for e in self.entries:
            if e.counterparty_id is None:
                net += e.amount
        return net

    def recent(self, count: int = 8, player_id: str | None = None) -> list[LedgerEntry]:
        items = self.entries
        if player_id is not None:
            items = [e for e in items if e.player_id == player_id]
        return items[-count:]

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self._seq,
            "entries": [e.to_dict() for e in self.entries[-400:]],
        }

    def load_dict(self, d: dict[str, Any]) -> None:
        self._seq = int(d.get("seq", 0))
        self.entries = [LedgerEntry.from_dict(x) for x in (d.get("entries") or [])]

    def clear(self) -> None:
        self.entries.clear()
        self._seq = 0
