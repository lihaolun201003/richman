"""Modifier 系统：把角色技能、状态效果、道具加成统一成「钩子 + 修正」。

解决的问题：以前经济计算里散落着 `player.perk_value("buy_discount")`
和一堆 `has_status(...)` 判断，每加一个新角色/新卡都要去改 economy.py。

现在统一成：
- 每个影响数值的来源（角色被动、状态效果、道具、全局规则）都声明若干 Modifier；
- 经济计算只问一件事：`resolve(provider, Hook.X, base)`；
- 计算过程带 breakdown，UI 可以直接展示「原价 / 折扣来自哪里 / 实付」。

这样以后加内容只需要在 data/*.json 里写 modifier，不用动核心代码。
"""
from __future__ import annotations

from typing import Any, Iterable


class Hook:
    """可被修正的钩子点。"""

    PURCHASE_PRICE = "modify_purchase_price"      # 购地价格
    UPGRADE_COST = "modify_upgrade_cost"          # 升级费用
    RENT_RECEIVED = "modify_rent_received"        # 收到的租金
    RENT_PAID = "modify_rent_paid"                # 支付的租金
    START_REWARD = "modify_start_reward"          # 经过起点奖励
    SHOP_PRICE = "modify_shop_price"              # 商店价格
    TAX = "modify_tax"                            # 税额
    JAIL_COST = "modify_jail_cost"                # 保释金
    CHANCE_WEIGHT = "modify_chance_weight"        # 事件抽取权重
    SELL_REFUND = "modify_sell_refund"            # 卖地回收比例
    MORTGAGE_VALUE = "modify_mortgage_value"      # 抵押可获得的现金比例

    @classmethod
    def all(cls) -> list[str]:
        return [v for k, v in vars(cls).items()
                if not k.startswith("_") and isinstance(v, str)]


class Op:
    """修正的运算方式。"""

    MUL = "mul"      # 按比例增减：final *= (1 + value)
    ADD = "add"      # 固定数额增减：final += value
    SET = "set"      # 直接覆盖：final = value
    MIN = "min"      # 取更小值
    MAX = "max"      # 取更大值


class Modifier:
    """一条数值修正。"""

    __slots__ = ("hook", "op", "value", "source")

    def __init__(self, hook: str, op: str = Op.MUL, value: float = 0.0,
                 source: str = "") -> None:
        self.hook = hook
        self.op = op
        self.value = float(value)
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {"hook": self.hook, "op": self.op, "value": self.value,
                "source": self.source}

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: str = "") -> "Modifier":
        return cls(d.get("hook", ""), d.get("op", Op.MUL),
                   d.get("value", 0.0), d.get("source") or source)

    def describe(self) -> str:
        if self.op == Op.MUL:
            pct = int(round(self.value * 100))
            return f"{self.source} {pct:+d}%" if pct else f"{self.source} 无修正"
        if self.op == Op.ADD:
            return f"{self.source} {int(self.value):+,}"
        return f"{self.source} 固定为 {int(self.value)}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Modifier {self.hook} {self.op} {self.value} {self.source}>"


# ------------------------------------------------------------------ 旧格式兼容

#: 第一版角色 perk 的 kind → Modifier 的映射（存档里可能还是旧格式）
LEGACY_PERK_MAP: dict[str, tuple[str, str]] = {
    "start_money_bonus": ("", Op.ADD),               # 特殊：直接影响初始资金
    "pass_start_bonus": (Hook.START_REWARD, Op.ADD),
    "buy_discount": (Hook.PURCHASE_PRICE, Op.MUL),
    "rent_income_bonus": (Hook.RENT_RECEIVED, Op.MUL),
    "jail_fee_discount": (Hook.JAIL_COST, Op.MUL),
    "event_gain_bonus": ("", Op.MUL),                # 特殊：只影响正面事件
    "upgrade_discount": (Hook.UPGRADE_COST, Op.MUL),
    "rent_payment_discount": (Hook.RENT_PAID, Op.MUL),
}
#: 这些 kind 的 value 在旧格式里是「正向的减免比例」，转成 Modifier 时要取负
LEGACY_NEGATE = {
    "buy_discount", "jail_fee_discount", "upgrade_discount", "rent_payment_discount",
}


def modifiers_from_perk(perk: dict[str, Any] | None) -> list[Modifier]:
    """把角色的 perk 转成 Modifier 列表，同时兼容新旧两种格式。"""
    if not perk:
        return []
    desc = str(perk.get("desc", "") or "角色被动")

    # 新格式
    raw = perk.get("modifiers")
    if isinstance(raw, list) and raw:
        return [Modifier.from_dict(m, source=m.get("source") or desc) for m in raw]

    # 旧格式
    kind = str(perk.get("kind", ""))
    if not kind or kind not in LEGACY_PERK_MAP:
        return []
    hook, op = LEGACY_PERK_MAP[kind]
    value = float(perk.get("value", 0))
    if op == Op.MUL and kind in LEGACY_NEGATE:
        value = -value
    if not hook:
        return []          # 特殊效果（初始资金、事件加成）另行处理
    return [Modifier(hook, op, value, source=desc)]


def perk_special(perk: dict[str, Any] | None, kind: str) -> float:
    """取出无法用 Modifier 表达的特殊被动值（初始资金、事件收益加成）。"""
    if not perk:
        return 0.0
    if perk.get("kind") == kind:
        return float(perk.get("value", 0))
    return 0.0


# ------------------------------------------------------------------ 采集与计算

def _status_modifiers(provider: Any) -> list[Modifier]:
    """状态效果自带的 modifier（放在 StatusEffect.payload["modifiers"] 里）。"""
    out: list[Modifier] = []
    for effect in getattr(provider, "status_effects", []) or []:
        payload = getattr(effect, "payload", None) or {}
        raws = payload.get("modifiers") or []
        if raws:
            for raw in raws:
                out.append(Modifier.from_dict(raw, source=raw.get("source") or effect.label))
            continue
        # 兜底：状态没带 modifiers 时按状态名映射（兼容旧存档）
        hook_op = STATUS_HOOK_MAP.get(getattr(effect, "status", ""))
        if hook_op is not None:
            hook, op, value = hook_op
            out.append(Modifier(hook, op, value, source=effect.label))
    return out


def collect(provider: Any, hook: str | None = None) -> list[Modifier]:
    """收集一个对象（通常是 Player）身上的全部修正。"""
    mods: list[Modifier] = []
    mods.extend(modifiers_from_perk(getattr(provider, "perk", None)))
    mods.extend(_status_modifiers(provider))
    for extra in getattr(provider, "extra_modifiers", None) or []:
        mods.append(extra if isinstance(extra, Modifier) else Modifier.from_dict(extra))
    if hook is None:
        return mods
    return [m for m in mods if m.hook == hook]


def resolve(provider: Any, hook: str, base: float) -> tuple[int, list[dict[str, Any]]]:
    """计算修正后的值。

    返回 (最终整数值, breakdown)。breakdown 里每一段都能在 UI 上展示，
    让玩家看懂「为什么是这个价」。

    计算顺序：先收集全部修正 → 先取 SET（如有）→ 再累加 MUL → 再累加 ADD → 最后夹 MIN/MAX。
    全程 raw 用浮点，只在最后取整，避免多次取整导致误差。
    """
    mods = collect(provider, hook)
    breakdown: list[dict[str, Any]] = []
    raw = float(base)

    sets = [m for m in mods if m.op == Op.SET]
    if sets:
        raw = sets[-1].value
        breakdown.append({"source": sets[-1].source, "op": Op.SET,
                          "value": raw, "text": sets[-1].describe()})

    mul = sum(m.value for m in mods if m.op == Op.MUL)
    if abs(mul) > 1e-9:
        raw *= (1.0 + mul)
        for m in mods:
            if m.op == Op.MUL:
                breakdown.append({"source": m.source, "op": Op.MUL,
                                  "value": m.value, "text": m.describe()})

    add = sum(m.value for m in mods if m.op == Op.ADD)
    if abs(add) > 1e-9:
        raw += add
        for m in mods:
            if m.op == Op.ADD:
                breakdown.append({"source": m.source, "op": Op.ADD,
                                  "value": m.value, "text": m.describe()})

    mins = [m for m in mods if m.op == Op.MIN]
    if mins:
        floor = min(m.value for m in mins)
        if raw < floor:
            raw = floor
            breakdown.append({"source": mins[0].source, "op": Op.MIN,
                              "value": floor, "text": f"不低于 {int(floor)}"})

    maxs = [m for m in mods if m.op == Op.MAX]
    if maxs:
        cap = max(m.value for m in maxs)
        if raw > cap:
            raw = cap
            breakdown.append({"source": maxs[0].source, "op": Op.MAX,
                              "value": cap, "text": f"不高于 {int(cap)}"})

    final = int(round(raw))
    if breakdown:
        breakdown.insert(0, {"source": "基础值", "op": "base",
                             "value": int(base), "text": f"基础 {int(base):,}"})
    return final, breakdown


#: 旧式状态名 → 直接映射的修正（仅作为兜底；正常路径由 status_modifiers() 生成）
STATUS_HOOK_MAP: dict[str, tuple[str, str, float]] = {
    "rent_double_once": (Hook.RENT_RECEIVED, Op.MUL, 1.0),
    "rent_discount_once": (Hook.RENT_PAID, Op.MUL, -0.5),
    "free_rent": (Hook.RENT_PAID, Op.SET, 0.0),
    "purchase_discount": (Hook.PURCHASE_PRICE, Op.MUL, -0.3),
    "upgrade_discount": (Hook.UPGRADE_COST, Op.MUL, -0.4),
}


def status_modifiers(status: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按状态名与 payload 生成 modifiers。

    由 StatusEffect 在创建时调用并写回 payload["modifiers"]，
    这样「折扣 30% 还是 50%」这类数值完全由数据决定，不在代码里写死。
    """
    pct = float(payload.get("percent", 0) or 0)
    if status == "purchase_discount":
        if pct <= 0:
            pct = 30.0
        return [{"hook": Hook.PURCHASE_PRICE, "op": Op.MUL, "value": -pct / 100.0,
                 "source": f"购地折扣 -{int(pct)}%"}]
    if status == "upgrade_discount":
        if pct <= 0:
            pct = 40.0
        return [{"hook": Hook.UPGRADE_COST, "op": Op.MUL, "value": -pct / 100.0,
                 "source": f"升级折扣 -{int(pct)}%"}]
    if status == "rent_double_once":
        return [{"hook": Hook.RENT_RECEIVED, "op": Op.MUL, "value": 1.0,
                 "source": "收租翻倍"}]
    if status == "rent_discount_once":
        return [{"hook": Hook.RENT_PAID, "op": Op.MUL, "value": -0.5,
                 "source": "租金减半"}]
    if status == "free_rent":
        return [{"hook": Hook.RENT_PAID, "op": Op.SET, "value": 0.0,
                 "source": "免租"}]
    if status == "tax_exempt":
        return [{"hook": Hook.TAX, "op": Op.SET, "value": 0.0, "source": "免税"}]
    if status == "shop_discount":
        return [{"hook": Hook.SHOP_PRICE, "op": Op.MUL, "value": -0.5,
                 "source": "商店半价"}]
    return []


def describe_modifiers(provider: Any) -> list[str]:
    """给 UI 用：列出这个对象当前拥有的全部修正说明。"""
    return [m.describe() for m in collect(provider)]
