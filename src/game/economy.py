"""经济计算：购买价、升级价、租金、税收。

这里是全部数值规则的唯一出口，UI 与 AI 都调用同一批函数，
保证「界面上显示的价格」与「实际扣款」永远一致。
"""
from __future__ import annotations

from typing import Any

from .player import (
    STATUS_FREE_RENT,
    STATUS_PURCHASE_DISCOUNT,
    STATUS_RENT_DISCOUNT_ONCE,
    STATUS_RENT_DOUBLE_ONCE,
    STATUS_UPGRADE_DISCOUNT,
    Player,
)
from .property import Property
from .state import GameState
from .tile import TileType


# ------------------------------------------------------------------ 购买

def buy_price(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """返回 (实付价格, 明细)。"""
    base = prop.price
    price = float(base)
    detail: dict[str, Any] = {"base": base, "character": 0, "coupon": 0}

    # 角色折扣
    char_rate = player.perk_value("buy_discount")
    if char_rate:
        cut = price * char_rate
        price -= cut
        detail["character"] = int(cut)

    # 折扣券状态（一次性）
    eff = player.get_status(STATUS_PURCHASE_DISCOUNT)
    if eff is not None:
        pct = float(eff.payload.get("percent", 0)) / 100.0
        cut = price * pct
        price -= cut
        detail["coupon"] = int(cut)
        detail["coupon_percent"] = int(eff.payload.get("percent", 0))

    final = max(0, int(round(price)))
    detail["final"] = final
    return final, detail


def upgrade_cost(state: GameState, player: Player, prop: Property) -> tuple[int, dict[str, Any]]:
    """返回 (升级实付费用, 明细)。"""
    base = prop.next_upgrade_cost
    if base is None:
        return 0, {"base": 0, "final": 0, "max_level": True}
    price = float(base)
    detail: dict[str, Any] = {"base": base, "character": 0, "coupon": 0}

    char_rate = player.perk_value("upgrade_discount")
    if char_rate:
        cut = price * char_rate
        price -= cut
        detail["character"] = int(cut)

    eff = player.get_status(STATUS_UPGRADE_DISCOUNT)
    if eff is not None:
        pct = float(eff.payload.get("percent", 0)) / 100.0
        cut = price * pct
        price -= cut
        detail["coupon"] = int(cut)
        detail["coupon_percent"] = int(eff.payload.get("percent", 0))

    final = max(0, int(round(price)))
    detail["final"] = final
    return final, detail


def can_afford(player: Player, amount: int) -> bool:
    return player.money >= amount


# ------------------------------------------------------------------ 租金

def rent_multiplier(state: GameState, prop: Property) -> tuple[float, str]:
    """房东侧的租金倍率（垄断加成）。"""
    if prop.owner_id and state.district_owned_all(prop.owner_id, prop.district):
        return state.district_bonus, f"{prop.district}垄断"
    return 1.0, ""


def rent_value(state: GameState, prop: Property) -> int:
    """地产当前标准租金（含垄断加成，不含任何一次性状态）。"""
    mult, _ = rent_multiplier(state, prop)
    return int(round(prop.base_rent * mult))


def rent_preview(state: GameState, prop: Property) -> dict[str, Any]:
    """给 UI / AI 的租金预览信息。"""
    mult, reason = rent_multiplier(state, prop)
    cur = int(round(prop.base_rent * mult))
    nxt = prop.next_rent
    return {
        "current": cur,
        "current_raw": prop.base_rent,
        "district_bonus": mult > 1.0,
        "bonus_reason": reason,
        "next": int(round(nxt * mult)) if nxt is not None else None,
        "upgrade_cost": prop.next_upgrade_cost,
        "max_level": prop.is_max_level,
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
        "district_multiplier": round(base / prop.base_rent, 3) if prop.base_rent else 1.0,
        "owner_bonus": 0,
        "doubled": False,
        "payer_discount": 0,
        "free": False,
        "final": base,
    }
    amount = float(base)

    # 1) 房东「下次收租翻倍」
    if owner is not None and owner.has_status(STATUS_RENT_DOUBLE_ONCE):
        amount *= 2.0
        detail["doubled"] = True

    # 2) 房东角色收租加成
    if owner is not None:
        rate = owner.perk_value("rent_income_bonus")
        if rate:
            bonus = amount * rate
            amount += bonus
            detail["owner_bonus"] = int(bonus)

    # 3) 付款方免租
    if payer.has_status(STATUS_FREE_RENT):
        detail["free"] = True
        detail["final"] = 0
        return 0, detail

    # 4) 付款方租金减半
    if payer.has_status(STATUS_RENT_DISCOUNT_ONCE):
        cut = amount * 0.5
        amount -= cut
        detail["payer_discount"] = int(cut)
        detail["halved"] = True

    # 5) 付款方角色支付减免
    rate = payer.perk_value("rent_payment_discount")
    if rate:
        cut = amount * rate
        amount -= cut
        detail["payer_discount"] += int(cut)

    detail["final"] = max(0, int(round(amount)))
    return detail["final"], detail


# ------------------------------------------------------------------ 税收

def tax_amount(
    state: GameState, player: Player, tile_type: TileType, tile_index: int
) -> tuple[int, str]:
    """返回 (税额, 税种名称)。

    税种由地图数据里的 tax_kind 决定（fixed / asset），不硬编码格子索引，
    这样换地图不需要改规则代码。
    """
    if tile_type is not TileType.TAX:
        return 0, ""
    tile = state.board.tile(tile_index)
    if getattr(tile, "tax_kind", "fixed") == "asset":
        rate = float(state.rules.get("tax_asset_rate", 0.04))
        total_asset = player.money + sum(
            p.asset_value for p in state.properties_of(player.id)
        )
        return max(300, int(round(total_asset * rate))), "奢侈消费税"
    return int(state.rules.get("tax_fixed", 1200)), "城市营业税"


# ------------------------------------------------------------------ 出售 / 变卖

def sell_refund(prop: Property) -> int:
    """出售地产可回收资金。"""
    return prop.sell_value


def liquidate_plan(state: GameState, player: Player, need: int) -> list[Property]:
    """给出建议的变卖顺序：先卖估值最低的（保留优质资产）。"""
    owned = state.properties_of(player.id)
    # 先卖等级低的、便宜的；被抵押的优先
    owned.sort(key=lambda p: (p.mortgaged, p.level, p.price))
    return owned


def max_recoverable(state: GameState, player: Player) -> int:
    """变卖全部资产最多能凑到多少钱。"""
    return player.money + sum(p.sell_value for p in state.properties_of(player.id))


# ------------------------------------------------------------------ 展示辅助

def format_money(v: int) -> str:
    return f"{int(v):,}"
