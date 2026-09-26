"""房间参与者会话。"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .transport import TcpConnection


def new_player_id() -> str:
    return "p" + uuid.uuid4().hex[:8]


def new_reconnect_token() -> str:
    return uuid.uuid4().hex[:16]


class PlayerSession:
    """房间里的一个座位。

    可能是：
    - 房主（真人，本机）
    - 局域网客户端（真人，通过 TCP）
    - AI（没有任何连接）
    """

    def __init__(
        self,
        player_id: str,
        name: str,
        is_ai: bool = False,
        is_host: bool = False,
        character_id: str = "",
        color_id: str = "",
        slot: int = 0,
    ) -> None:
        self.player_id = player_id
        self.name = name
        self.is_ai = is_ai
        self.is_host = is_host
        self.character_id = character_id
        self.color_id = color_id
        self.slot = slot
        self.ready = is_ai          # AI 默认视为已准备
        self.conn: TcpConnection | None = None
        self.reconnect_token = new_reconnect_token()
        self.disconnected = False
        self.disconnected_at = 0.0
        self.last_seen = time.time()
        self.bot_takeover = False
        self.joined_at = time.time()

    # ------------------------------------------------------------ 状态

    @property
    def is_remote(self) -> bool:
        return self.conn is not None or (not self.is_ai and not self.is_host)

    @property
    def connected(self) -> bool:
        if self.is_ai:
            return True
        if self.conn is None:
            return not self.disconnected
        return self.conn.alive and not self.disconnected

    @property
    def participant(self) -> bool:
        """是否算作一名参与者（用于开局人数判断）。"""
        return self.is_ai or self.connected or self.is_host

    def mark_disconnected(self) -> None:
        self.disconnected = True
        self.disconnected_at = time.time()
        self.ready = self.is_ai or self.is_host

    def mark_reconnected(self, conn: TcpConnection) -> None:
        self.conn = conn
        self.disconnected = False
        self.bot_takeover = False
        self.last_seen = time.time()

    def to_dict(self, include_token: bool = False) -> dict[str, Any]:
        d = {
            "player_id": self.player_id,
            "name": self.name,
            "is_ai": self.is_ai,
            "is_host": self.is_host,
            "character_id": self.character_id,
            "color_id": self.color_id,
            "slot": self.slot,
            "ready": self.ready,
            "connected": self.connected,
            "disconnected": self.disconnected,
            "bot_takeover": self.bot_takeover,
        }
        if include_token:
            d["reconnect_token"] = self.reconnect_token
        return d

    def __repr__(self) -> str:  # pragma: no cover
        tag = "AI" if self.is_ai else ("HOST" if self.is_host else "CLIENT")
        return f"<Session {self.name} {tag} ready={self.ready}>"
