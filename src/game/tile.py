"""棋盘格子类型定义。"""
from __future__ import annotations

from enum import Enum


class TileType(str, Enum):
    """格子类型。使用 str 枚举，便于 JSON 直接序列化。"""

    START = "START"              # 起点：经过或停留领取奖励
    PROPERTY = "PROPERTY"        # 可购买地产
    STATION = "STATION"          # 高级地产（车站/机场），租金成长更快
    CHANCE = "CHANCE"            # 机遇格（正面与风险混合）
    FORTUNE = "FORTUNE"          # 福运格：只出正面 / 趣味事件
    DISASTER = "DISASTER"        # 灾祸格：只出风险事件
    TAX = "TAX"                  # 税收格
    JAIL = "JAIL"                # 看守所：仅停留标志，路过分文无事
    GO_TO_JAIL = "GO_TO_JAIL"    # 押送看守所
    SHOP = "SHOP"                # 商店：用现金购买道具卡
    PARK = "PARK"                # 中央公园：停留领取奖金池
    BONUS = "BONUS"              # 奖金池格

    @property
    def is_purchasable(self) -> bool:
        return self in (TileType.PROPERTY, TileType.STATION)

    @property
    def label(self) -> str:
        return {
            TileType.START: "起点",
            TileType.PROPERTY: "地产",
            TileType.STATION: "枢纽",
            TileType.CHANCE: "机遇",
            TileType.FORTUNE: "福运",
            TileType.DISASTER: "灾祸",
            TileType.TAX: "税收",
            TileType.JAIL: "看守所",
            TileType.GO_TO_JAIL: "巡查",
            TileType.SHOP: "商店",
            TileType.PARK: "公园",
            TileType.BONUS: "奖金",
        }.get(self, str(self.value))


class TileSide(str, Enum):
    """格子所在的棋盘边，渲染器据此计算位置。"""

    CORNER_BL = "corner_bl"
    BOTTOM = "bottom"
    CORNER_BR = "corner_br"
    RIGHT = "right"
    CORNER_TR = "corner_tr"
    TOP = "top"
    CORNER_TL = "corner_tl"
    LEFT = "left"
