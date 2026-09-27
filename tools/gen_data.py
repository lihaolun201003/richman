"""生成地图与地产数值数据。

开发期工具：把平衡数值以公式集中定义，输出到 data/ 下的 JSON。
游戏运行时只读 JSON，不依赖本脚本。

支持多张地图：每张地图声明自己的网格尺寸（列 × 行）与格子布局，
棋盘渲染器从地图数据里读取网格尺寸，因此换地图不需要改渲染代码。

用法:
    python tools/gen_data.py
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

#: 租金 = 价格 * 系数，索引即等级 0..3
RENT_RATIO = [0.06, 0.34, 0.78, 1.45]
#: 车站类资产租金更高
RENT_RATIO_STATION = [0.08, 0.44, 1.00, 1.80]
#: 升级费用 = 价格 * 系数，索引 0->1, 1->2, 2->3
UPGRADE_RATIO = [0.50, 0.80, 1.20]

LEVEL_NAMES = ["空地", "小建筑", "中型建筑", "高级建筑"]


@dataclass
class MapSpec:
    """一张地图的完整定义。"""

    map_id: str
    name: str
    cols: int
    rows: int
    description: str
    layout: list[tuple[int, str, str, str | None, str]]
    prices: dict[int, int]
    tax_kind: dict[int, str] = field(default_factory=dict)

    @property
    def tile_count(self) -> int:
        return len(self.layout)

    def corner_indices(self) -> tuple[int, int, int, int]:
        """按顺时针返回四角索引：左下、右下、右上、左上。"""
        bl = 0
        br = self.cols - 1
        tr = br + (self.rows - 1)
        tl = tr + (self.cols - 1)
        return bl, br, tr, tl


def side_of(spec: MapSpec, index: int) -> tuple[str, int]:
    """返回格子所在的 (边, 槽位号)。四个角的槽位号为 0。"""
    bl, br, tr, tl = spec.corner_indices()
    if index == bl:
        return "corner_bl", 0
    if index == br:
        return "corner_br", 0
    if index == tr:
        return "corner_tr", 0
    if index == tl:
        return "corner_tl", 0
    if bl < index < br:
        return "bottom", index - bl
    if br < index < tr:
        return "right", index - br
    if tr < index < tl:
        return "top", index - tr
    return "left", index - tl


def grid_position(spec: MapSpec, index: int) -> tuple[int, int]:
    """返回格子在网格中的 (列, 行)。行 0 在顶部。"""
    bl, br, tr, tl = spec.corner_indices()
    last_row = spec.rows - 1
    last_col = spec.cols - 1
    if index == bl:
        return 0, last_row
    if index == br:
        return last_col, last_row
    if index == tr:
        return last_col, 0
    if index == tl:
        return 0, 0
    if bl < index < br:
        return index - bl, last_row
    if br < index < tr:
        return last_col, last_row - (index - br)
    if tr < index < tl:
        return last_col - (index - tr), 0
    return 0, index - tl


def round50(v: float) -> int:
    return int(round(v / 50.0)) * 50


# ==================================================================== 地图 1

CITY_LAYOUT: list[tuple[int, str, str, str | None, str]] = [
    (0, "START", "城市广场", None, "经过或停留可领取通行奖励"),
    (1, "PROPERTY", "晨曦社区", "东城片区", ""),
    (2, "PROPERTY", "晨光苑", "东城片区", ""),
    (3, "FORTUNE", "好运街角", None, "只会有好事发生"),
    (4, "PROPERTY", "星河商街", "星河商圈", ""),
    (5, "PROPERTY", "星港百货", "星河商圈", ""),
    (6, "TAX", "城市营业税", None, "缴纳固定税款"),
    (7, "PROPERTY", "云港码头", "云港湾区", ""),
    (8, "PROPERTY", "云帆中心", "云港湾区", ""),
    (9, "DISASTER", "事故现场", None, "小心，这里只有坏消息"),
    (10, "JAIL", "看守所", None, "路过无事，被关押则停留于此"),
    (11, "PROPERTY", "科技新城", "高新园区", ""),
    (12, "PROPERTY", "创客大厦", "高新园区", ""),
    (13, "SHOP", "便利店", None, "用现金购买道具卡"),
    (14, "PROPERTY", "湖畔花园", "湖景片区", ""),
    (15, "PROPERTY", "湖滨大道", "湖景片区", ""),
    (16, "STATION", "中央车站", "交通枢纽", "高级地产，租金成长更快"),
    (17, "PROPERTY", "创意园区", "文创街区", ""),
    (18, "PARK", "中央公园", None, "停留可领取奖金池全部奖金"),
    (19, "BONUS", "城市奖金池", None, "领取奖金池中的累积奖金"),
    (20, "PROPERTY", "艺术街区", "文创街区", ""),
    (21, "PROPERTY", "金融中心", "金融区", ""),
    (22, "PROPERTY", "银行大厦", "金融区", ""),
    (23, "FORTUNE", "好运街角", None, "只会有好事发生"),
    (24, "PROPERTY", "未来广场", "未来城", ""),
    (25, "PROPERTY", "未来科技馆", "未来城", ""),
    (26, "TAX", "奢侈消费税", None, "按资产比例征税"),
    (27, "PROPERTY", "大学城", "学区", ""),
    (28, "GO_TO_JAIL", "治安巡查", None, "被送入看守所"),
    (29, "PROPERTY", "实验中学", "学区", ""),
    (30, "PROPERTY", "海岸新城", "滨海新区", ""),
    (31, "PROPERTY", "海岸度假村", "滨海新区", ""),
    (32, "STATION", "国际机场", "交通枢纽", "高级地产，租金成长更快"),
    (33, "DISASTER", "事故现场", None, "小心，这里只有坏消息"),
    (34, "PROPERTY", "山景社区", None, "独立地产，不参与分区垄断"),
    (35, "PROPERTY", "未来港塔", "未来城", "全城地标，最昂贵的地产"),
]

CITY_PRICES: dict[int, int] = {
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

# ==================================================================== 地图 2

SEASIDE_LAYOUT: list[tuple[int, str, str, str | None, str]] = [
    (0, "START", "阳光沙滩", None, "旅程从这里开始"),
    (1, "PROPERTY", "椰林小径", "椰林区", ""),
    (2, "PROPERTY", "贝壳湾", "椰林区", ""),
    (3, "FORTUNE", "海风送福", None, "只会有好事发生"),
    (4, "PROPERTY", "冲浪海滩", "冲浪区", ""),
    (5, "PROPERTY", "潜水码头", "冲浪区", ""),
    (6, "TAX", "度假税", None, "缴纳固定税款"),
    (7, "PROPERTY", "海豚湾", "海豚湾", ""),
    (8, "PROPERTY", "水族馆", "海豚湾", ""),
    (9, "DISASTER", "台风警报", None, "小心，这里只有坏消息"),
    (10, "SHOP", "海滩集市", None, "用现金购买道具卡"),
    (11, "JAIL", "渔港派出所", None, "路过无事，被关押则停留于此"),
    (12, "PROPERTY", "灯塔岬", "灯塔区", ""),
    (13, "PROPERTY", "观景台", "灯塔区", ""),
    (14, "STATION", "渡轮码头", "交通枢纽", "高级地产，租金成长更快"),
    (15, "PROPERTY", "海鲜市场", "老港区", ""),
    (16, "PROPERTY", "码头仓库", "老港区", ""),
    (17, "FORTUNE", "拾贝之喜", None, "只会有好事发生"),
    (18, "PROPERTY", "珊瑚酒店", "度假区", ""),
    (19, "PROPERTY", "沙滩别墅", "度假区", ""),
    (20, "PARK", "海滨公园", None, "停留可领取奖金池全部奖金"),
    (21, "BONUS", "海洋奖金池", None, "领取奖金池中的累积奖金"),
    (22, "PROPERTY", "游艇会所", "游艇区", ""),
    (23, "PROPERTY", "海景公寓", "游艇区", ""),
    (24, "DISASTER", "赤潮来袭", None, "小心，这里只有坏消息"),
    (25, "PROPERTY", "珍珠养殖场", "养殖区", ""),
    (26, "PROPERTY", "海藻农场", "养殖区", ""),
    (27, "TAX", "海洋资源税", None, "按资产比例征税"),
    (28, "PROPERTY", "缆车站", "观光区", ""),
    (29, "PROPERTY", "悬崖餐厅", "观光区", ""),
    (30, "SHOP", "免税商店", None, "用现金购买道具卡"),
    (31, "GO_TO_JAIL", "海警巡逻", None, "被送入看守所"),
    (32, "PROPERTY", "无人岛", "秘境", ""),
    (33, "PROPERTY", "海蚀洞", "秘境", ""),
    (34, "PROPERTY", "远洋邮轮", "远洋区", ""),
    (35, "PROPERTY", "海底餐厅", "远洋区", ""),
    (36, "FORTUNE", "海豚引路", None, "只会有好事发生"),
    (37, "PROPERTY", "极地馆", "极地", ""),
    (38, "PROPERTY", "冰川酒店", "极地", ""),
    (39, "PROPERTY", "海岸旅游塔", "全景", "海滨地标，最昂贵的地产"),
]

SEASIDE_PRICES: dict[int, int] = {
    1: 1200, 2: 1400,
    4: 1700, 5: 1900,
    7: 2100, 8: 2300,
    12: 2500, 13: 2700,
    14: 3600,
    15: 2900, 16: 3100,
    18: 3300, 19: 3500,
    22: 3700, 23: 3900,
    25: 4100, 26: 4300,
    28: 3500, 29: 3900,
    32: 4500, 33: 4700,
    34: 4900, 35: 5100,
    37: 5300, 38: 5600,
    39: 6200,
}

MAPS = [
    MapSpec(
        map_id="city_default",
        name="城市之光",
        cols=11,
        rows=9,
        description="标准都市棋盘，地产密度高，节奏紧凑",
        layout=CITY_LAYOUT,
        prices=CITY_PRICES,
        tax_kind={6: "fixed", 26: "asset"},
    ),
    MapSpec(
        map_id="seaside",
        name="海滨假日",
        cols=12,
        rows=10,
        description="更大的海岛棋盘，地产更多，单局更从容",
        layout=SEASIDE_LAYOUT,
        prices=SEASIDE_PRICES,
        tax_kind={6: "fixed", 27: "asset"},
    ),
]

RECOMMENDED = {"city_default": "2～5 人", "seaside": "3～6 人"}


def build_districts(layout) -> dict[str, list[int]]:
    districts: dict[str, list[int]] = {}
    for index, ttype, _name, district, _note in layout:
        if district and ttype in ("PROPERTY", "STATION"):
            districts.setdefault(district, []).append(index)
    return districts


def main() -> None:
    per_map: dict[str, dict] = {}

    for spec in MAPS:
        tiles = []
        for index, ttype, name, district, note in spec.layout:
            side, slot = side_of(spec, index)
            col, row = grid_position(spec, index)
            tiles.append({
                "id": index,
                "index": index,
                "type": ttype,
                "name": name,
                "district": district,
                "side": side,
                "slot": slot,
                "col": col,
                "row": row,
                "metadata": {
                    "note": note,
                    "tax_kind": spec.tax_kind.get(index, "fixed"),
                },
            })

            if ttype in ("PROPERTY", "STATION"):
                price = spec.prices[index]
                ratio = RENT_RATIO_STATION if ttype == "STATION" else RENT_RATIO
                prop = {
                    "id": f"p{index:02d}",
                    "tile_index": index,
                    "name": name,
                    "district": district,
                    "kind": ttype,
                    "price": price,
                    "max_level": 3,
                    "rent_table": [round50(price * r) for r in ratio],
                    "upgrade_costs": [round50(price * u) for u in UPGRADE_RATIO],
                    "level_names": LEVEL_NAMES,
                }
                per_map.setdefault(spec.map_id, {"districts": {}, "properties": []})
                per_map[spec.map_id]["properties"].append(prop)

        districts = build_districts(spec.layout)
        per_map[spec.map_id]["districts"] = districts

        doc = {
            "id": spec.map_id,
            "name": spec.name,
            "description": spec.description,
            "cols": spec.cols,
            "rows": spec.rows,
            "tile_count": spec.tile_count,
            "corners": list(spec.corner_indices()),
            "recommended_players": RECOMMENDED.get(spec.map_id, "2～6 人"),
            "districts": districts,
            "property_ids": [f"p{t[0]:02d}" for t in spec.layout
                             if t[1] in ("PROPERTY", "STATION")],
            "tiles": tiles,
        }
        os.makedirs(DATA, exist_ok=True)
        fname = ("default_map.json" if spec.map_id == MAPS[0].map_id
                 else f"map_{spec.map_id}.json")
        with open(os.path.join(DATA, fname), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f"已生成 {fname}: {spec.tile_count} 格, "
              f"{spec.cols}×{spec.rows} 网格, {len(districts)} 个片区, "
              f"{len(doc['property_ids'])} 处地产")

    props_doc = {
        "district_bonus_multiplier": 2,
        "maps": per_map,
    }
    with open(os.path.join(DATA, "properties.json"), "w", encoding="utf-8") as f:
        json.dump(props_doc, f, ensure_ascii=False, indent=2)
    total = sum(len(v["properties"]) for v in per_map.values())
    print(f"已生成 properties.json: {len(per_map)} 张地图共 {total} 处地产")


if __name__ == "__main__":
    main()
