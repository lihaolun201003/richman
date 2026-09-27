"""棋盘模型：从 JSON 地图数据构建，与渲染完全分离。

Board 只关心格子拓扑（索引、类型、名称、邻接、分组），
不知道任何绘制细节；UI 层的 BoardView 负责几何布局。
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .tile import TileSide, TileType


class Tile:
    """棋盘上的一格。"""

    __slots__ = ("id", "index", "type", "name", "district", "side", "slot", "note", "tax_kind")

    def __init__(
        self,
        tile_id: int,
        index: int,
        ttype: TileType,
        name: str,
        district: str | None,
        side: TileSide,
        slot: int,
        note: str = "",
        tax_kind: str = "fixed",
    ) -> None:
        self.id = tile_id
        self.index = index
        self.type = ttype
        self.name = name
        self.district = district
        self.side = side
        self.slot = slot
        self.note = note
        self.tax_kind = tax_kind

    @property
    def is_purchasable(self) -> bool:
        return self.type.is_purchasable

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "type": self.type.value,
            "name": self.name,
            "district": self.district,
            "side": self.side.value,
            "slot": self.slot,
            "note": self.note,
            "tax_kind": self.tax_kind,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tile":
        meta = d.get("metadata") or {}
        return cls(
            tile_id=int(d.get("id", d["index"])),
            index=int(d["index"]),
            ttype=TileType(d["type"]),
            name=d["name"],
            district=d.get("district"),
            side=TileSide(d.get("side", "bottom")),
            slot=int(d.get("slot", 0)),
            note=str(meta.get("note", "")),
            tax_kind=str(meta.get("tax_kind", "fixed")),
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Tile {self.index} {self.type.value} {self.name}>"


class Board:
    """环形棋盘。格子按 index 顺时针排列，末格的下一个是第 0 格。

    网格尺寸与四角索引来自地图数据，因此任何满足「环形路径」的布局都能直接用，
    渲染层不需要为每张地图写代码。
    """

    def __init__(self, map_id: str, name: str, tiles: list[Tile],
                 cols: int = 0, rows: int = 0,
                 corners: list[int] | None = None,
                 description: str = "", recommended: str = "") -> None:
        if not tiles:
            raise ValueError("地图必须至少包含一个格子")
        self.map_id = map_id
        self.name = name
        self.description = description
        self.recommended = recommended
        self.tiles: list[Tile] = sorted(tiles, key=lambda t: t.index)
        self.tile_count = len(self.tiles)
        self.cols = int(cols) if cols else self._guess_cols()
        self.rows = int(rows) if rows else 2 + max(1, (self.tile_count - 2 * self.cols) // 2)
        self.corners = list(corners) if corners else [0, self.cols - 1,
                                                      self.cols + self.rows - 2,
                                                      2 * self.cols + self.rows - 3]
        self._verify_indices()

    def _guess_cols(self) -> int:
        """没有显式声明网格时，按「周长 = 2*cols + 2*(rows-2)」反推一个合理值。"""
        n = self.tile_count
        best, best_diff = 11, 10 ** 9
        for cols in range(4, 24):
            rows = (n - 2 * cols) // 2 + 2
            if rows < 4:
                continue
            if 2 * cols + 2 * (rows - 2) != n:
                continue
            diff = abs(cols / max(1, rows) - 1.25)
            if diff < best_diff:
                best, best_diff = cols, diff
        return best

    def _verify_indices(self) -> None:
        expect = list(range(self.tile_count))
        actual = [t.index for t in self.tiles]
        if actual != expect:
            raise ValueError(f"地图格子索引必须从 0 连续编号，实际为 {actual}")

    # ------------------------------------------------------------ 访问
    def tile(self, index: int) -> Tile:
        """按索引取格子，自动环绕。"""
        return self.tiles[index % self.tile_count]

    def index_of(self, tile_id: int) -> int:
        for t in self.tiles:
            if t.id == tile_id:
                return t.index
        raise KeyError(f"未找到格子 id={tile_id}")

    def path(self, start: int, steps: int) -> list[int]:
        """从 start 出发走 steps 步的完整索引路径（不含起点，含终点）。

        steps 可为负（后退），结果仍然环绕到合法索引。
        """
        result: list[int] = []
        cur = start
        step = 1 if steps >= 0 else -1
        for _ in range(abs(steps)):
            cur = (cur + step) % self.tile_count
            result.append(cur)
        return result

    def distance_forward(self, frm: int, to: int) -> int:
        """从 frm 顺时针走到 to 需要的步数（0..count-1）。"""
        return (to - frm) % self.tile_count

    def passes_start(self, frm: int, steps: int) -> bool:
        """走 steps 步的过程中是否经过（或落于）START。"""
        if steps <= 0:
            return False
        for idx in self.path(frm, steps):
            if self.tile(idx).type is TileType.START:
                return True
        return False

    def passes_over(self, frm: int, steps: int, tile_index: int) -> bool:
        """走 steps 步的过程中是否经过指定格（含落点，不含出发点）。"""
        if steps <= 0:
            return False
        return (tile_index % self.tile_count) in self.path(frm, steps)

    def start_index(self) -> int:
        for t in self.tiles:
            if t.type is TileType.START:
                return t.index
        return 0

    def jail_index(self) -> int:
        for t in self.tiles:
            if t.type is TileType.JAIL:
                return t.index
        return 0

    def of_type(self, ttype: TileType) -> list[Tile]:
        return [t for t in self.tiles if t.type is ttype]

    def purchasable_tiles(self) -> list[Tile]:
        return [t for t in self.tiles if t.is_purchasable]

    def __iter__(self) -> Iterable[Tile]:
        return iter(self.tiles)

    def __len__(self) -> int:
        return self.tile_count

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "name": self.name,
            "tile_count": self.tile_count,
            "cols": self.cols,
            "rows": self.rows,
            "corners": list(self.corners),
            "description": self.description,
            "recommended_players": self.recommended,
            "tiles": [t.to_dict() for t in self.tiles],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Board":
        tiles = [Tile.from_dict(t) for t in d["tiles"]]
        return cls(
            d.get("map_id") or d.get("id") or "unknown",
            d.get("name", "未命名地图"), tiles,
            cols=int(d.get("cols", 0) or 0),
            rows=int(d.get("rows", 0) or 0),
            corners=list(d.get("corners") or []) or None,
            description=d.get("description", ""),
            recommended=d.get("recommended_players", ""),
        )

    @classmethod
    def load(cls, path: str) -> "Board":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
