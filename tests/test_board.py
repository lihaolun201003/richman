"""棋盘、移动、经过起点相关规则测试。"""
from __future__ import annotations

import pytest

from src.game.board import Board
from src.game.movement import plan_move
from src.game.setup import load_board
from src.game.tile import TileType


def test_board_loads_36_tiles():
    board = load_board()
    assert len(board) == 36
    assert [t.index for t in board] == list(range(36))


def test_board_has_corners():
    board = load_board()
    assert board.tile(0).type is TileType.START
    assert board.tile(10).type is TileType.JAIL
    assert board.tile(18).type is TileType.PARK
    assert board.tile(28).type is TileType.GO_TO_JAIL
    assert board.start_index() == 0
    assert board.jail_index() == 10


def test_path_wraps_around():
    board = load_board()
    path = board.path(34, 5)
    assert path == [35, 0, 1, 2, 3]


def test_path_backwards():
    board = load_board()
    assert board.path(2, -4) == [1, 0, 35, 34]


def test_passes_start_detects_crossing():
    board = load_board()
    # 34 -> 35 -> 0：经过起点
    assert board.passes_start(34, 3) is True
    # 0 -> 1 -> 2：起点就在脚下，但不再「经过」
    assert board.passes_start(0, 2) is False
    # 停在起点也算经过
    assert board.passes_start(35, 1) is True


def test_distance_forward():
    board = load_board()
    assert board.distance_forward(0, 5) == 5
    assert board.distance_forward(30, 3) == 9
    assert board.distance_forward(7, 7) == 0


def test_plan_move_passes_start(engine):
    st = engine.state
    player = st.players[0]
    player.position = 34
    plan = plan_move(st, player.id, 4)
    assert plan.passes_start is True
    assert plan.final_index == 2


def test_plan_move_stops_at_barrier(engine):
    st = engine.state
    player = st.players[0]
    player.position = 0
    st.barriers["3"] = 5
    plan = plan_move(st, player.id, 6)
    assert plan.barrier_index == 3
    assert plan.final_index == 3
    assert len(plan.path) == 3


def test_barrier_not_triggered_backwards(engine):
    st = engine.state
    player = st.players[0]
    player.position = 6
    st.barriers["3"] = 5
    plan = plan_move(st, player.id, -4)
    assert plan.barrier_index is None
    assert plan.final_index == 2


def test_barriers_expire(engine):
    st = engine.state
    st.barriers["5"] = 2
    st.tick_barriers()
    assert st.barriers["5"] == 1
    st.tick_barriers()
    assert "5" not in st.barriers
