"""pytest 公共夹具。

测试全部使用固定的 seed，保证结果可复现。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.controllers.ai import AIController  # noqa: E402
from src.game.commands import Command, CommandType  # noqa: E402
from src.game.setup import create_engine  # noqa: E402

DT = 1.0 / 30.0


def make_specs(count: int, humans: int = 1) -> list[dict]:
    chars = ["char_ajin", "char_xiaoman", "char_laochen", "char_nana",
             "char_tiedan", "char_drbo"]
    colors = ["red", "blue", "green", "amber", "purple", "orange"]
    specs = []
    for i in range(count):
        specs.append({
            "id": f"p{i + 1}",
            "name": f"玩家{i + 1}",
            "character_id": chars[i % len(chars)],
            "color_id": colors[i % len(colors)],
            "is_ai": i >= humans,
            "is_host": i == 0,
        })
    return specs


@pytest.fixture
def engine():
    """4 人局，全部由 AI 控制（便于自动推进）。"""
    eng = create_engine(make_specs(4, humans=0), seed=20240926, anim_speed=8.0)
    for p in eng.state.players:
        eng.bind_controller(p.id, AIController(p.id, seed=100 + p.slot, think_sec=0.0))
    eng.start()
    return eng


@pytest.fixture
def engine_no_ai():
    """4 人局，无控制器：用于手工驱动状态机。"""
    eng = create_engine(make_specs(4, humans=4), seed=7, anim_speed=8.0)
    eng.start()
    return eng


def advance(eng, seconds: float = 5.0, dt: float = DT) -> None:
    """推进引擎固定时长（模拟时间）。"""
    steps = int(seconds / dt)
    for _ in range(steps):
        eng.update(dt)
        if eng.state.game_over:
            break


def run_until_decision(eng, max_seconds: float = 30.0) -> bool:
    """一直推进到出现待处理决策。"""
    import time as _t

    steps = int(max_seconds / DT)
    for _ in range(steps):
        eng.update(DT)
        if eng.state.pending_decision is not None or eng.state.game_over:
            return True
    return False


def submit(eng, ctype: str, player_id: str = "", payload: dict | None = None,
           decision_id: str | None = None):
    pd = eng.state.pending_decision
    if not player_id and pd is not None:
        player_id = pd.player_id
    if decision_id is None and pd is not None:
        decision_id = pd.id
    return eng.submit_command(Command(ctype=ctype, player_id=player_id,
                                      payload=payload or {}, decision_id=decision_id))


def resolve(eng, option_id: str):
    pd = eng.state.pending_decision
    assert pd is not None, "当前没有待处理决策"
    return eng.submit_command(Command(
        ctype=CommandType.RESOLVE_DECISION,
        player_id=pd.player_id,
        payload={"option_id": option_id},
        decision_id=pd.id,
    ))
