"""统一的金额与数值格式化。

全项目只允许通过这里输出金额，避免出现 15000 / 15,000$ / ￥15000 混用。
"""
from __future__ import annotations

#: 货币符号（统一用全角人民币符号 + 空格 + 千分位）
CURRENCY = "¥"


def money(value: int | float) -> str:
    """金额：¥ 15,000"""
    return f"{CURRENCY} {int(round(value)):,}"


def money_plain(value: int | float) -> str:
    """不带符号的金额：15,000（用于表格里已经有表头的场景）"""
    return f"{int(round(value)):,}"


def money_delta(value: int | float) -> str:
    """带正负号的金额：+ ¥ 800 / - ¥ 800"""
    v = int(round(value))
    sign = "+" if v >= 0 else "-"
    return f"{sign} {CURRENCY} {abs(v):,}"


def money_short(value: int | float) -> str:
    """紧凑金额，用于空间紧张处：1.5万 / 3200"""
    v = int(round(value))
    if abs(v) >= 10000:
        return f"{v / 10000:.1f}万"
    return f"{v:,}"


def percent(value: float, digits: int = 0) -> str:
    """比例 → 百分比文本：0.08 → 8%"""
    return f"{value * 100:.{digits}f}%"


def signed_percent(value: float, digits: int = 0) -> str:
    """带符号的百分比：-0.08 → -8%"""
    return f"{value * 100:+.{digits}f}%"


def count(value: int) -> str:
    return f"{int(value):,}"
