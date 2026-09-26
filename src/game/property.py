"""地产模型。"""
from __future__ import annotations

from typing import Any


class Property:
    """一处可购买地产（含车站/机场这类高级地产）。"""

    __slots__ = (
        "id", "tile_index", "name", "district", "kind", "price",
        "max_level", "rent_table", "upgrade_costs", "level_names",
        "owner_id", "level", "mortgaged",
    )

    def __init__(
        self,
        prop_id: str,
        tile_index: int,
        name: str,
        district: str | None,
        kind: str,
        price: int,
        max_level: int,
        rent_table: list[int],
        upgrade_costs: list[int],
        level_names: list[str] | None = None,
        owner_id: str | None = None,
        level: int = 0,
        mortgaged: bool = False,
    ) -> None:
        self.id = prop_id
        self.tile_index = tile_index
        self.name = name
        self.district = district
        self.kind = kind
        self.price = price
        self.max_level = max_level
        self.rent_table = rent_table
        self.upgrade_costs = upgrade_costs
        self.level_names = level_names or ["空地", "小建筑", "中型建筑", "高级建筑"]
        self.owner_id = owner_id
        self.level = level
        self.mortgaged = mortgaged

    # ------------------------------------------------------------ 查询
    @property
    def is_owned(self) -> bool:
        return self.owner_id is not None

    @property
    def is_max_level(self) -> bool:
        return self.level >= self.max_level

    @property
    def base_rent(self) -> int:
        lv = max(0, min(self.level, len(self.rent_table) - 1))
        return self.rent_table[lv]

    @property
    def next_rent(self) -> int | None:
        """升级后的租金，已满级返回 None。"""
        if self.is_max_level:
            return None
        lv = min(self.level + 1, len(self.rent_table) - 1)
        return self.rent_table[lv]

    @property
    def next_upgrade_cost(self) -> int | None:
        """升到下一级的基础费用，已满级返回 None。"""
        if self.is_max_level:
            return None
        idx = min(self.level, len(self.upgrade_costs) - 1)
        return self.upgrade_costs[idx]

    @property
    def level_name(self) -> str:
        lv = max(0, min(self.level, len(self.level_names) - 1))
        return self.level_names[lv]

    @property
    def total_invested(self) -> int:
        """已投入的总资金（地价 + 已支付的升级费）。"""
        spent = self.price
        for i in range(self.level):
            if i < len(self.upgrade_costs):
                spent += self.upgrade_costs[i]
        return spent

    @property
    def sell_value(self) -> int:
        """出售可回收的资金（含地价一部分与全部升级费的一半）。"""
        return int(self.total_invested * 0.7)

    @property
    def asset_value(self) -> int:
        """计入总资产的估值。"""
        return self.total_invested

    # ------------------------------------------------------------ 变更
    def assign(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self.level = 0
        self.mortgaged = False

    def release(self) -> None:
        """回归银行（无主）。"""
        self.owner_id = None
        self.level = 0
        self.mortgaged = False

    def transfer(self, new_owner_id: str, keep_level: bool = True) -> None:
        """转移给新主人，默认保留建筑等级。"""
        self.owner_id = new_owner_id
        if not keep_level:
            self.level = 0
        self.mortgaged = False

    def upgrade(self) -> bool:
        if self.is_max_level or not self.is_owned:
            return False
        self.level += 1
        return True

    def downgrade(self) -> bool:
        if self.level <= 0:
            return False
        self.level -= 1
        return True

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tile_index": self.tile_index,
            "name": self.name,
            "district": self.district,
            "kind": self.kind,
            "price": self.price,
            "max_level": self.max_level,
            "rent_table": list(self.rent_table),
            "upgrade_costs": list(self.upgrade_costs),
            "level_names": list(self.level_names),
            "owner_id": self.owner_id,
            "level": self.level,
            "mortgaged": self.mortgaged,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Property":
        return cls(
            prop_id=d["id"],
            tile_index=int(d["tile_index"]),
            name=d["name"],
            district=d.get("district"),
            kind=d.get("kind", "PROPERTY"),
            price=int(d["price"]),
            max_level=int(d.get("max_level", 3)),
            rent_table=[int(v) for v in d["rent_table"]],
            upgrade_costs=[int(v) for v in d["upgrade_costs"]],
            level_names=list(d.get("level_names") or ["空地", "小建筑", "中型建筑", "高级建筑"]),
            owner_id=d.get("owner_id"),
            level=int(d.get("level", 0)),
            mortgaged=bool(d.get("mortgaged", False)),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Property {self.name} owner={self.owner_id} lv={self.level}>"


def load_property_definitions(path: str) -> tuple[list[Property], dict[str, list[int]], int]:
    """从 data/properties.json 读取地产定义。

    返回 (地产模板列表, 分组表, 垄断租金倍率)。模板的 owner_id 为 None。
    """
    import json

    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    props = [Property.from_dict(p) for p in doc["properties"]]
    districts = {k: [int(v) for v in vals] for k, vals in (doc.get("districts") or {}).items()}
    bonus = int(doc.get("district_bonus_multiplier", 2))
    return props, districts, bonus
