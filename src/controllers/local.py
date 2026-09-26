"""本机真人控制器。

UI 事件处理函数（按钮点击、点击骰子、选择卡片目标）
统一通过 submit_command 把 Command 投递到这里。
UI 本身绝不直接修改 GameState。
"""
from __future__ import annotations

from typing import Any, Callable

from ..game.commands import Command
from .base import LocalController

__all__ = ["LocalController"]


def make_local(player_id: str) -> LocalController:
    return LocalController(player_id)
