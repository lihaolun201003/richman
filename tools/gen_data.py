"""生成默认地图与地产数值数据。

开发期工具：把平衡数值以公式集中定义，输出到 data/ 下的 JSON。
游戏运行时只读 JSON，不依赖本脚本。

用法:
    python tools/gen_data.py
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TILE_COUNT = 36


# 棋盘为 10×8 网格的环形布局（宽 10 格、高 8 格），
# 对应 16:9 画面比例。四角分别位于 0 / 10 / 18 / 28。
CORNER_BL = 0
CORNER_BR = 10
CORNER_TR = 18
CORNER_TL = 28
#: 每条边（含两端角）的格子数
SIDE_SPAN = {"bottom": 10, "right": 8, "top": 10, "left": 8}


def side_of(index: int) -> tuple[str, int]:
    """返回 (所在边, 该边上的槽位号)。四个角槽位为 0。"""
    if index == CORNER_BL:
        return "corner_bl", 0
    if index == CORNER_BR:
        return "corner_br", 0
    if index == CORNER_TR:
        return "corner_tr", 0
    if index == CORNER_TL:
        return "corner_tl", 0
    if 1 <= index <= 9:
        return "bottom", index
    if 11 <= index <= 17:
        return "right", index - 10
    if 19 <= index <= 27:
        return "top", index - 18
    return "left", index - 28


# ---------------------------------------------------------------- 地图定义
# (index, type, name, district, 备注)
LAYOUT: list[tuple[int, str, str, str | None, str]] = [
    (0, "START", "城市广场", None, "经过或停留可领取通行奖励"),
    (1, "PROPERTY", "晨曦社区", "东城片区", ""),
    (2, "PROPERTY", "晨光苑", "东城片区", ""),
    (3, "CHANCE", "机遇路口", None, "抽取一张机遇卡"),
    (4, "PROPERTY", "星河商街", "星河商圈", ""),
    (5, "PROPERTY", "星港百货", "星河商圈", ""),
    (6, "TAX", "城市营业税", None, "缴纳固定税款"),
    (7, "PROPERTY", "云港码头", "云港湾区", ""),
    (8, "PROPERTY", "云帆中心", "云港湾区", ""),
    (9, "CHANCE", "机遇路口", None, ""),
    (10, "JAIL", "看守所", None, "路过无事，被关押则停留于此"),
    (11, "PROPERTY", "科技新城", "高新园区", ""),
    (12, "PROPERTY", "创客大厦", "高新园区", ""),
    (13, "CHANCE", "机遇路口", None, ""),
    (14, "PROPERTY", "湖畔花园", "湖景片区", ""),
    (15, "PROPERTY", "湖滨大道", "湖景片区", ""),
    (16, "STATION", "中央车站", "交通枢纽", "高级地产，租金成长更快"),
    (17, "PROPERTY", "创意园区", "文创街区", ""),
    (18, "PARK", "中央公园", None, "停留可领取奖金池全部奖金"),
    (19, "BONUS", "城市奖金池", None, "领取奖金池中的累积奖金"),
    (20, "PROPERTY", "艺术街区", "文创街区", ""),
    (21, "PROPERTY", "金融中心", "金融区", ""),
    (22, "PROPERTY", "银行大厦", "金融区", ""),
    (23, "CHANCE", "机遇路口", None, ""),
    (24, "PROPERTY", "未来广场", "未来城", ""),
    (25, "PROPERTY", "未来科技馆", "未来城", ""),
    (26, "TAX", "奢侈消费税", None, "按资产比例征税"),
    (27, "PROPERTY", "大学城", "学区", ""),
    (28, "GO_TO_JAIL", "治安巡查", None, "被送入看守所"),
    (29, "PROPERTY", "实验中学", "学区", ""),
    (30, "PROPERTY", "海岸新城", "滨海新区", ""),
    (31, "PROPERTY", "海岸度假村", "滨海新区", ""),
    (32, "STATION", "国际机场", "交通枢纽", "高级地产，租金成长更快"),
    (33, "CHANCE", "机遇路口", None, ""),
    (34, "PROPERTY", "山景社区", None, "独立地产，不参与分区垄断"),
    (35, "PROPERTY", "未来港塔", "未来城", "全城地标，最昂贵的地产"),
]

#: 税收格类型：fixed = 固定金额，asset = 按资产比例
TAX_KIND = {6: "fixed", 26: "asset"}

# 地产价格（仅 PROPERTY / STATION），沿环形递增
PRICES: dict[int, int] = {
    1: 1200, 2: 1400,
    4: 1800, 5: 2000,
    7: 2200, 8: 2400,
    11: 2600, 12: 2800,
    14: 3000, 15: 3200,
    16: 3800,
    17: 3400,
    20: 3600, 21: 4000, 22: 4200,
    24: 4400, 25: 4600,
    27: 3400, 29: 3600,
    30: 4800, 31: 5000,
    32: 5500,
    34: 4400, 35: 6000,
}

# 各地产归属的分组（同组全部持有 = 垄断，租金翻倍）
DISTRICTS: dict[str, list[int]] = {}
for _idx, _t, _n, _d, _ in LAYOUT:
    if _d and _t in ("PROPERTY", "STATION"):
        DISTRICTS.setdefault(_d, []).append(_idx)

# 租金 = 价格 * 系数，索引即等级 0..3
RENT_RATIO = [0.06, 0.34, 0.78, 1.45]
# 车站类资产租金更高
RENT_RATIO_STATION = [0.08, 0.44, 1.00, 1.80]
# 升级费用 = 价格 * 系数，索引 0->1, 1->2, 2->3
UPGRADE_RATIO = [0.50, 0.80, 1.20]

LEVEL_NAMES = ["空地", "小建筑", "中型建筑", "高级建筑"]


def round100(v: float) -> int:
    """取整到 50 的倍数，让数值看起来更像设计过的数字。"""
    return int(round(v / 50.0)) * 50


def main() -> None:
    tiles = []
    properties = []
    for index, ttype, name, district, note in LAYOUT:
        side, slot = side_of(index)
        tile = {
            "id": index,
            "index": index,
            "type": ttype,
            "name": name,
            "district": district,
            "side": side,
            "slot": slot,
            "metadata": {"note": note, "tax_kind": TAX_KIND.get(index, "fixed")},
        }
        tiles.append(tile)

        if ttype in ("PROPERTY", "STATION"):
            price = PRICES[index]
            ratio = RENT_RATIO_STATION if ttype == "STATION" else RENT_RATIO
            rent_table = [round100(price * r) for r in ratio]
            upgrade_costs = [round100(price * u) for u in UPGRADE_RATIO]
            properties.append({
                "id": f"p{index:02d}",
                "tile_index": index,
                "name": name,
                "district": district,
                "kind": ttype,
                "price": price,
                "max_level": 3,
                "rent_table": rent_table,
                "upgrade_costs": upgrade_costs,
                "level_names": LEVEL_NAMES,
            })

    map_doc = {
        "id": "city_default",
        "name": "城市之光",
        "tile_count": TILE_COUNT,
        "description": "36 格城市主题环形棋盘（10×8 网格）",
        "tiles": tiles,
    }

    props_doc = {
        "map_id": "city_default",
        "districts": {k: v for k, v in sorted(DISTRICTS.items())},
        "district_bonus_multiplier": 2,
        "properties": properties,
    }

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "default_map.json"), "w", encoding="utf-8") as f:
        json.dump(map_doc, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA, "properties.json"), "w", encoding="utf-8") as f:
        json.dump(props_doc, f, ensure_ascii=False, indent=2)

    print(f"已生成 {len(tiles)} 个格子，{len(properties)} 处地产")
    print(f"分组数：{len(DISTRICTS)}")
    total = sum(p["price"] for p in properties)
    print(f"地产总价值（不含升级）：{total}")


if __name__ == "__main__":
    main()
