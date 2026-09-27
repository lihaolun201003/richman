"""经济计算：购买价、升级价、租金、税收、抵押、出售。

这里是全部数值规则的唯一出口，UI 与 AI 都调用同一批函数，
保证「界面上显示的价格」与「实际扣款」永远一致。

所有加成 / 折扣都走 modifiers 系统，不再散落 `player.perk_value(...)` 判断，
因此新增角色、状态、道具只需要在数据里声明 Modifier。
"""
from __future__ import annotations

from typing import Any

from . import modifiers as mods
from .player import Player
from .property import Property
from .state import GameState
from .tile import TileType

#: 出售回收比例（未抵押 / 已抵押）
SELL_REFUND_RATE = 0.70
MORTGAGED_SELL_RATE = 0.45
#: 抵押可获得的一次性现金比例（相对地价）
MORTGAGE_RATE = 0.60
#: 赎回需要支付的倍数（抵押金额 × 该系数）
REDEEM_MULTIPLIER = 1.10


# ------------------------------------------------------------------ 购买

def buy_price(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """返回 (实付价格, 明细)。明细含 breakdown，可直接给 UI 展示。"""
    price, breakdown = mods.resolve(player, mods.Hook.PURCHASE_PRICE, prop.price)
    price = max(0, price)
    return price, {
        "base": prop.price,
        "final": price,
        "saved": max(0, prop.price - price),
        "breakdown": breakdown,
        "discounted": price < prop.price,
    }


def upgrade_cost(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """返回 (升级实付费用, 明细)。"""
    base = prop.next_upgrade_cost
    if base is None:
        return 0, {"base": 0, "final": 0, "max_level": True, "breakdown": []}
    cost, breakdown = mods.resolve(player, mods.Hook.UPGRADE_COST, base)
    cost = max(0, cost)
    return cost, {
        "base": base,
        "final": cost,
        "saved": max(0, base - cost),
        "max_level": False,
        "breakdown": breakdown,
        "discounted": cost < base,
    }


def can_afford(player: Player, amount: int) -> bool:
    return player.money >= amount


# ------------------------------------------------------------------ 租金

def rent_multiplier(state: GameState, prop: Property) -> tuple[float, str]:
    """房东侧的租金倍率（片区垄断加成）。"""
    if prop.owner_id and state.district_owned_all(prop.owner_id, prop.district):
        return state.district_bonus, f"{prop.district}垄断"
    return 1.0, ""


def rent_value(state: GameState, prop: Property) -> int:
    """地产当前标准租金（含垄断加成，不含任何一次性状态）。抵押中为 0。"""
    if prop.mortgaged:
        return 0
    mult, _ = rent_multiplier(state, prop)
    return int(round(prop.base_rent * mult))


def rent_preview(state: GameState, prop: Property) -> dict[str, Any]:
    """给 UI / AI 的租金预览信息。"""
    mult, reason = rent_multiplier(state, prop)
    cur = 0 if prop.mortgaged else int(round(prop.base_rent * mult))
    nxt = prop.next_rent
    return {
        "current": cur,
        "current_raw": prop.base_rent,
        "district_bonus": mult > 1.0,
        "bonus_reason": reason,
        "next": int(round(nxt * mult)) if nxt is not None else None,
        "upgrade_cost": prop.next_upgrade_cost,
        "max_level": prop.is_max_level,
        "mortgaged": prop.mortgaged,
    }


def compute_rent_payment(
    state: GameState, payer: Player, prop: Property
) -> tuple[int, dict[str, Any]]:
    """计算 payer 踩到 prop 需要支付的租金，并返回明细。

    不修改状态；一次性状态（免租、减半）的消耗由调用方负责。
    """
    owner = state.player(prop.owner_id)
    base = rent_value(state, prop)
    detail: dict[str, Any] = {
        "base": base,
        "raw_rent": prop.base_rent,
        "district_multiplier": (round(base / prop.base_rent, 3)
                                if prop.base_rent and not prop.mortgaged else 1.0),
        "owner_breakdown": [],
        "payer_breakdown": [],
        "doubled": False,
        "halved": False,
        "free": False,
        "final": base,
    }
    if base <= 0:
        detail["final"] = 0
        return 0, detail

    # 1) 房东侧修正（收租加成、双倍卡…）
    if owner is not None:
        amount, owner_breakdown = mods.resolve(owner, mods.Hook.RENT_RECEIVED, base)
    else:
        amount, owner_breakdown = base, []
    detail["owner_breakdown"] = owner_breakdown
    detail["doubled"] = any(
        b.get("op") == mods.Op.MUL and b.get("value", 0) > 0
        for b in owner_breakdown
    )

    # 2) 付款方免租
    if payer.has_status("free_rent"):
        detail["free"] = True
        detail["final"] = 0
        return 0, detail

    # 3) 付款方侧修正（减半、角色减免…）
    final, payer_breakdown = mods.resolve(payer, mods.Hook.RENT_PAID, amount)
    detail["payer_breakdown"] = payer_breakdown
    detail["halved"] = any(
        b.get("op") == mods.Op.MUL and b.get("value", 0) < 0 for b in payer_breakdown
    )

    detail["final"] = max(0, final)
    return detail["final"], detail


# ------------------------------------------------------------------ 税收

def tax_amount(
    state: GameState, player: Player, tile_type: TileType, tile_index: int
) -> tuple[int, str]:
    """返回 (税额, 税种名称)。

    税种由地图数据里的 tax_kind 决定（fixed / asset），不硬编码格子索引。
    """
    if tile_type is not TileType.TAX:
        return 0, ""
    tile = state.board.tile(tile_index)
    if getattr(tile, "tax_kind", "fixed") == "asset":
        rate = float(state.rules.get("tax_asset_rate", 0.04))
        base = max(300, int(round(state.player_asset_value(player.id) * rate)))
        label = "奢侈消费税"
    else:
        base = int(state.rules.get("tax_fixed", 1200))
        label = "城市营业税"
    final, _ = mods.resolve(player, mods.Hook.TAX, base)
    return max(0, final), label


# ------------------------------------------------------------------ 出售

def sell_refund(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """出售地产可回收资金（含角色 / 状态修正）。"""
    rate = MORTGAGED_SELL_RATE if prop.mortgaged else SELL_REFUND_RATE
    base = int(prop.total_invested * rate)
    final, breakdown = mods.resolve(player, mods.Hook.SELL_REFUND, base)
    final = max(0, final)
    return final, {"base": base, "final": final, "rate": rate, "breakdown": breakdown}


# ------------------------------------------------------------------ 抵押

def mortgage_value(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """抵押可获得的一次性现金。"""
    base = int(prop.price * MORTGAGE_RATE)
    final, breakdown = mods.resolve(player, mods.Hook.MORTGAGE_VALUE, base)
    final = max(0, final)
    return final, {"base": base, "final": final, "rate": MORTGAGE_RATE,
                   "breakdown": breakdown}


def can_mortgage(prop: Property) -> tuple[bool, str]:
    if prop.owner_id is None:
        return False, "这块地不属于任何人"
    if prop.mortgaged:
        return False, "该地产已经处于抵押状态"
    if prop.level > 0:
        return False, "需要先把建筑降到空地才能抵押"
    return True, ""


def mortgage_base(prop: Property) -> int:
    return int(prop.price * MORTGAGE_RATE)


def redeem_cost(prop: Property) -> int:
    """赎回需要支付的金额 = 抵押金 × 1.1。"""
    return int(round(mortgage_base(prop) * REDEEM_MULTIPLIER))


def can_redeem(prop: Property, player: Player) -> tuple[bool, str]:
    if prop.owner_id != player.id:
        return False, "这不是你的地产"
    if not prop.mortgaged:
        return False, "该地产未被抵押"
    if player.money < redeem_cost(prop):
        return False, f"现金不足（需要 {redeem_cost(prop):,}）"
    return True, ""


# ------------------------------------------------------------------ 变卖与自救

def liquidate_plan(state: GameState, player: Player, need: int) -> list[Property]:
    """给出建议的变卖顺序：先卖便宜的、等级低的、被抵押的。"""
    owned = state.properties_of(player.id)
    owned.sort(key=lambda p: (not p.mortgaged, p.level, p.price))
    return owned


def max_recoverable(state: GameState, player: Player) -> int:
    """变卖全部资产后最多能凑到多少钱。"""
    total = player.money
    for p in state.properties_of(player.id):
        rate = MORTGAGED_SELL_RATE if p.mortgaged else SELL_REFUND_RATE
        total += int(p.total_invested * rate)
    return total


def debt_rescue_options(state: GameState, player: Player, need: int) -> list[dict[str, Any]]:
    """列出可用于还债的资产操作（给债务处理弹窗用）。

    每项包含动作类型、目标地产、可获金额、说明。
    排序原则是「对资产伤害最小」：先抵押空地，再卖等级低的廉价地。
    """
    options: list[dict[str, Any]] = []
    for prop in state.properties_of(player.id):
        refund, _ = sell_refund(state, player, prop)
        options.append({
            "action": "sell",
            "property_id": prop.id,
            "name": prop.name,
            "amount": refund,
            "level": prop.level,
            "mortgaged": prop.mortgaged,
            "hint": f"出售（{prop.level} 级{'，抵押中' if prop.mortgaged else ''}）",
        })
        ok, _reason = can_mortgage(prop)
        if ok:
            value, _ = mortgage_value(state, player, prop)
            options.append({
                "action": "mortgage",
                "property_id": prop.id,
                "name": prop.name,
                "amount": value,
                "level": 0,
                "mortgaged": False,
                "hint": "抵押（保留产权，但不能收租）",
            })
    options.sort(key=lambda o: (o["action"] != "mortgage", o["level"], o["amount"]))
    return options
