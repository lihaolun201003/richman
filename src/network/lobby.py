"""大厅（房间）状态：加入、准备、加 AI、开局条件。

大厅是纯数据对象，不碰网络也不碰引擎，方便单测。
"""
from __future__ import annotations

from typing import Any

from .session import PlayerSession, new_player_id

#: 大厅阶段
PHASE_LOBBY = "lobby"
PHASE_PLAYING = "playing"
PHASE_FINISHED = "finished"


class LobbyState:
    """一个局域网房间。"""

    def __init__(
        self,
        room_name: str,
        host_player_id: str,
        max_players: int = 6,
        min_players: int = 2,
        port: int = 0,
        map_id: str = "city_default",
        map_name: str = "城市之光",
        map_file: str = "default_map.json",
        preset: str = "standard",
    ) -> None:
        self.room_name = room_name
        self.host_player_id = host_player_id
        self.max_players = max_players
        self.min_players = min_players
        self.port = port
        self.map_id = map_id
        self.map_name = map_name
        self.map_file = map_file
        self.preset = preset
        self.map_cols = 0
        self.map_rows = 0
        self.map_tiles = 0
        self.map_recommended = ""
        self.phase = PHASE_LOBBY
        self.sessions: list[PlayerSession] = []
        self.ai_counter = 0
        self.notice = ""

    # ------------------------------------------------------------ 查询

    def session(self, player_id: str) -> PlayerSession | None:
        for s in self.sessions:
            if s.player_id == player_id:
                return s
        return None

    def session_by_name(self, name: str) -> PlayerSession | None:
        for s in self.sessions:
            if s.name == name:
                return s
        return None

    def session_by_conn(self, conn) -> PlayerSession | None:
        for s in self.sessions:
            if s.conn is conn:
                return s
        return None

    def session_by_token(self, token: str) -> PlayerSession | None:
        if not token:
            return None
        for s in self.sessions:
            if s.reconnect_token == token:
                return s
        return None

    def humans(self) -> list[PlayerSession]:
        return [s for s in self.sessions if not s.is_ai]

    def remote_humans(self) -> list[PlayerSession]:
        return [s for s in self.sessions if not s.is_ai and not s.is_host]

    @property
    def player_count(self) -> int:
        return len(self.sessions)

    @property
    def all_ready(self) -> bool:
        return all(s.ready or s.disconnected for s in self.sessions)

    @property
    def ready(self) -> bool:
        return all(s.ready for s in self.sessions)

    # ------------------------------------------------------------ 地图与规则

    def set_map(self, map_file: str) -> bool:
        """切换地图（调用方负责权限检查）。"""
        if self.phase != PHASE_LOBBY:
            return False
        try:
            from ..game.setup import load_board

            board = load_board(map_file)
        except Exception:
            return False
        self.map_file = map_file
        self.map_id = board.map_id
        self.map_name = board.name
        self.map_cols = board.cols
        self.map_rows = board.rows
        self.map_tiles = board.tile_count
        self.map_recommended = board.recommended
        return True

    def set_preset(self, preset: str) -> bool:
        if self.phase != PHASE_LOBBY:
            return False
        self.preset = preset
        return True

    def preset_label(self) -> str:
        try:
            from ..game.setup import preset_list

            for item in preset_list():
                if item["key"] == self.preset:
                    return item["name"]
        except Exception:
            pass
        return self.preset

    def ensure_map_info(self) -> None:
        if not self.map_tiles:
            self.set_map(self.map_file)

    # ------------------------------------------------------------ 变更

    def next_slot(self) -> int:
        used = {s.slot for s in self.sessions}
        slot = 0
        while slot in used:
            slot += 1
        return slot

    def add_human(
        self,
        name: str,
        conn,
        character_id: str = "",
        color_id: str = "",
    ) -> tuple[PlayerSession | None, str]:
        """加入一名真人。返回 (会话, 错误码)。"""
        if self.phase != PHASE_LOBBY:
            return None, "game_started"
        if len(self.sessions) >= self.max_players:
            return None, "room_full"
        if self.session_by_name(name) is not None:
            return None, "name_taken"

        session = PlayerSession(
            player_id=new_player_id(),
            name=name,
            is_ai=False,
            is_host=False,
            character_id=character_id,
            color_id=color_id,
            slot=self.next_slot(),
        )
        session.conn = conn
        self.sessions.append(session)
        return session, ""

    def add_ai(self, name: str = "", character_id: str = "", color_id: str = "") -> tuple[PlayerSession | None, str]:
        if self.phase != PHASE_LOBBY:
            return None, "game_started"
        if len(self.sessions) >= self.max_players:
            return None, "room_full"
        self.ai_counter += 1
        ai_name = name or f"电脑 {self.ai_counter}"
        # 避免重名
        while self.session_by_name(ai_name) is not None:
            self.ai_counter += 1
            ai_name = f"电脑 {self.ai_counter}"
        session = PlayerSession(
            player_id=new_player_id(),
            name=ai_name,
            is_ai=True,
            is_host=False,
            character_id=character_id,
            color_id=color_id,
            slot=self.next_slot(),
        )
        session.ready = True
        self.sessions.append(session)
        return session, ""

    def remove(self, player_id: str) -> bool:
        session = self.session(player_id)
        if session is None:
            return False
        self.sessions.remove(session)
        return True

    def set_ready(self, player_id: str, ready: bool) -> bool:
        session = self.session(player_id)
        if session is None or session.is_ai:
            return False
        session.ready = bool(ready)
        return True

    def set_character(self, player_id: str, character_id: str, color_id: str = "") -> bool:
        session = self.session(player_id)
        if session is None:
            return False
        session.character_id = character_id
        if color_id:
            session.color_id = color_id
        return True

    def take_over_slot(self, player_id: str, conn) -> bool:
        """把某个断线座位交给新连接（重连）。"""
        session = self.session(player_id)
        if session is None:
            return False
        session.mark_reconnected(conn)
        return True

    # ------------------------------------------------------------ 开局条件

    def host_name(self) -> str:
        s = self.session(self.host_player_id)
        return s.name if s is not None else ""

    def start_blockers(self) -> list[str]:
        """列出「为什么还不能开始」，按玩家能理解的说法，一条一个原因。

        房主不需要准备，所以这里不会把房主算进「等待准备」。
        """
        out: list[str] = []
        if self.phase != PHASE_LOBBY:
            out.append("游戏已经开始")
            return out
        count = len(self.sessions)
        if count < self.min_players:
            out.append(f"还需要 {self.min_players - count} 名参与者（至少 {self.min_players} 人）")
        if count > self.max_players:
            out.append("人数超过上限")
        not_ready = [s for s in self.sessions if not s.is_ai and not s.is_host and not s.ready]
        if not_ready:
            names = "、".join(s.name for s in not_ready[:3])
            if len(not_ready) > 3:
                names += f" 等 {len(not_ready)} 人"
            out.append(f"等待 {len(not_ready)} 名玩家准备：{names}")
        offline = [s for s in self.sessions if not s.is_ai and s.disconnected]
        if offline:
            names = "、".join(s.name for s in offline[:3])
            out.append(f"{names} 已掉线，请等待重连")
        return out

    def can_start(self) -> tuple[bool, str]:
        blockers = self.start_blockers()
        if blockers:
            return False, blockers[0]
        return True, ""

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        from ..version import APP_VERSION

        blockers = self.start_blockers()
        return {
            "room_name": self.room_name,
            "host_player_id": self.host_player_id,
            "host_name": self.host_name(),
            "app_version": APP_VERSION,
            "max_players": self.max_players,
            "min_players": self.min_players,
            "port": self.port,
            "map_id": self.map_id,
            "map_name": self.map_name,
            "map_file": self.map_file,
            "map_cols": self.map_cols,
            "map_rows": self.map_rows,
            "map_tiles": self.map_tiles,
            "map_recommended": self.map_recommended,
            "preset": self.preset,
            "preset_name": self.preset_label(),
            "phase": self.phase,
            "notice": self.notice,
            "can_start": not blockers,
            "start_reason": blockers[0] if blockers else "",
            "start_blockers": blockers,
            "players": [s.to_dict() for s in self.sessions],
        }

    def to_discovery_dict(self) -> dict[str, Any]:
        """UDP 广播用的精简信息。"""
        from ..version import APP_VERSION

        return {
            "room_name": self.room_name,
            "host_name": self.host_name(),
            "app_version": APP_VERSION,
            "players": len(self.sessions),
            "max_players": self.max_players,
            "phase": self.phase,
            "map_name": self.map_name,
            "preset": self.preset,
            "preset_name": self.preset_label(),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Lobby {self.room_name} {len(self.sessions)}/{self.max_players} {self.phase}>"
