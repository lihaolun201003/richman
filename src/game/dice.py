"""骰子：由 Host / 单机引擎产生，客户端只接收结果。"""
from __future__ import annotations

import random
from typing import Any


class DiceResult:
    """一次掷骰的结果。"""

    __slots__ = ("die1", "die2", "forced", "from_jail")

    def __init__(self, die1: int, die2: int, forced: bool = False, from_jail: bool = False) -> None:
        self.die1 = int(die1)
        self.die2 = int(die2)
        self.forced = forced
        self.from_jail = from_jail

    @property
    def total(self) -> int:
        return self.die1 + self.die2

    @property
    def is_double(self) -> bool:
        return self.die1 == self.die2

    def to_dict(self) -> dict[str, Any]:
        return {
            "die1": self.die1,
            "die2": self.die2,
            "total": self.total,
            "is_double": self.is_double,
            "forced": self.forced,
            "from_jail": self.from_jail,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DiceResult":
        return cls(
            die1=int(d["die1"]),
            die2=int(d["die2"]),
            forced=bool(d.get("forced", False)),
            from_jail=bool(d.get("from_jail", False)),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Dice {self.die1}+{self.die2}={self.total}>"


def roll(rng: random.Random, sides: int = 6, fixed_total: int | None = None) -> DiceResult:
    """掷两个骰子。fixed_total 用于「指定骰子卡 / 幸运骰子」状态。"""
    if fixed_total is not None:
        total = max(2, min(2 * sides, int(fixed_total)))
        first = max(1, min(sides, total - sides))
        # 让两颗骰子都落在合法范围，且和为 total
        d1 = rng.randint(first, min(sides, total - 1))
        d2 = total - d1
        return DiceResult(d1, d2, forced=True)
    return DiceResult(rng.randint(1, sides), rng.randint(1, sides))
