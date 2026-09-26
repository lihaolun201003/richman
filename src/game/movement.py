"""移动逻辑：路径计算、经过起点、路障拦截。

纯计算，不修改状态；实际位移由 engine 执行。
"""
from __future__ import annotations

from typing import Any

from .state import GameState


class MovePlan:
    """一次移动计划。"""

    __slots__ = ("player_id", "from_index", "steps", "path", "passes_start", "barrier_index", "final_index")

    def __init__(
        self,
        player_id: str,
        from_index: int,
        steps: int,
        path: list[int],
        passes_start: bool,
        barrier_index: int | None,
        final_index: int,
    ) -> None:
        self.player_id = player_id
        self.from_index = from_index
        self.steps = steps
        self.path = path
        self.passes_start = passes_start
        self.barrier_index = barrier_index
        self.final_index = final_index

    @property
    def effective_steps(self) -> int:
        return len(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "from_index": self.from_index,
            "steps": self.steps,
            "path": list(self.path),
            "passes_start": self.passes_start,
            "barrier_index": self.barrier_index,
            "final_index": self.final_index,
        }


def plan_move(state: GameState, player_id: str, steps: int) -> MovePlan:
    """规划一次移动。

    - 前进时若路径上有路障，则在路障处停下；
    - 后退不受路障影响（路障只拦正向通行）。
    """
    player = state.require_player(player_id)
    board = state.board
    start = player.position

    barrier_index: int | None = None
    if steps > 0:
        cur = start
        for i in range(steps):
            nxt = (cur + 1) % board.tile_count
            if state.barrier_at(nxt):
                barrier_index = nxt
                break
            cur = nxt
        if barrier_index is not None:
            raw_path = board.path(start, board.distance_forward(start, barrier_index))
        else:
            raw_path = board.path(start, steps)
    else:
        raw_path = board.path(start, steps)

    path = list(raw_path)
    passes_start = any(board.tile(i).type.value == "START" for i in path)
    final_index = path[-1] if path else start

    return MovePlan(
        player_id=player_id,
        from_index=start,
        steps=steps,
        path=path,
        passes_start=passes_start,
        barrier_index=barrier_index,
        final_index=final_index,
    )


def tile_positions(state: GameState) -> dict[int, list[str]]:
    """统计每格上站了哪些玩家，供渲染时做位置偏移。"""
    result: dict[int, list[str]] = {}
    for p in state.players:
        if p.bankrupt:
            continue
        result.setdefault(p.position, []).append(p.id)
    return result


def player_offset_slot(state: GameState, player_id: str) -> tuple[int, int]:
    """同格多人时返回 (序号, 总数)，用于棋子错位摆放。"""
    player = state.require_player(player_id)
    same = [
        p.id for p in state.players
        if not p.bankrupt and p.position == player.position
    ]
    same.sort()
    try:
        return same.index(player_id), len(same)
    except ValueError:  # pragma: no cover
        return 0, 1
