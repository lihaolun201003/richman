"""远程玩家控制器（局域网客户端）。

Host 侧：接收网络层解析出的 Command 并投递给引擎。
客户端侧：本地只用它保存「我的 player_id」以及最近一次 Host 回执。
"""
from __future__ import annotations

from typing import Any

from ..game.commands import Command, CommandResult
from .base import RemoteController

__all__ = ["RemoteController"]


class ClientSidePlayer:
    """客户端本地视角：我的身份 + Host 返回的最近结果。

    客户端没有 GameEngine，所有状态都来自 Host 的 STATE_SNAPSHOT。
    """

    def __init__(self, player_id: str, name: str) -> None:
        self.player_id = player_id
        self.name = name
        self.last_command_id: str | None = None
        self.last_result: CommandResult | None = None
        self.pending_command_id: str | None = None

    def mark_pending(self, command: Command) -> None:
        self.last_command_id = command.command_id
        self.pending_command_id = command.command_id

    def clear_pending(self) -> None:
        self.pending_command_id = None

    @property
    def awaiting_host(self) -> bool:
        return self.pending_command_id is not None

    def on_result(self, result: CommandResult) -> None:
        self.last_result = result
        self.clear_pending()
