"""局域网 Client：只读状态 + 发送意图。

客户端**没有** GameEngine，也永远不会自己修改规则状态。
它维护一份从 Host 收到的最近快照（readonly view），
所有操作都通过 COMMAND 发回 Host，等 Host 的快照回来才更新画面。
"""
from __future__ import annotations

import time
from typing import Any

from ..game.commands import Command, new_command_id  # noqa: F401
from ..game.serializer import state_from_snapshot
from ..utils.logging_setup import get_logger
from . import protocol as proto
from .transport import TcpConnection, connect_to

log = get_logger(__name__)


class ClientError(Exception):
    """连接失败等需要展示给用户的错误。"""


class GameClient:
    """客户端连接与状态视图。"""

    def __init__(self) -> None:
        self.conn: TcpConnection | None = None
        self.connected = False
        self.player_id: str = ""
        self.name: str = ""
        self.reconnect_token: str = ""
        self.room_name: str = ""
        self.host_player_id: str = ""
        self.host_address: str = ""

        # 大厅与状态视图
        self.lobby: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {}
        self.state: Any = None              # GameState 视图（只读用途）
        self.revision: int = -1
        self.server_time_offset: float = 0.0
        self.latency_ms: float = 0.0

        # 事件与错误
        self.messages: list[str] = []
        self.incoming_events: list[dict[str, Any]] = []
        self.last_error: str = ""
        self.kicked: bool = False
        self.disconnected_reason: str = ""

        # 命令回执
        self.pending_command_id: str | None = None
        self.last_result_ok: bool | None = None
        self.last_result_reason: str = ""

        self._closed = False

    # ================================================================ 连接

    def connect(self, host: str, port: int, name: str,
                character_id: str = "", color_id: str = "",
                timeout: float = 5.0) -> None:
        """建立连接并发送加入请求。"""
        try:
            conn = connect_to(host, port, timeout=timeout)
        except OSError as exc:
            raise ClientError(f"无法连接到 {host}:{port} —— {exc.strerror or exc}") from exc

        self.conn = conn
        self.host_address = f"{host}:{port}"
        self.name = name
        conn.start()
        conn.send_message(proto.MessageType.HELLO, {
            "protocol_version": proto.PROTOCOL_VERSION,
            "name": name,
            "client": "richman-desktop",
        })
        conn.send_message(proto.MessageType.JOIN_REQUEST, {
            "name": name,
            "character_id": character_id,
            "color_id": color_id,
        })

    def reconnect(self, host: str, port: int, token: str, timeout: float = 5.0) -> None:
        try:
            conn = connect_to(host, port, timeout=timeout)
        except OSError as exc:
            raise ClientError(f"重连失败：{exc.strerror or exc}") from exc
        self.conn = conn
        conn.start()
        conn.send_message(proto.MessageType.RECONNECT, {"reconnect_token": token})

    def close(self, notify: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        if self.conn is not None:
            if notify and self.conn.alive:
                self.conn.send_message(proto.MessageType.LEAVE, {})
                time.sleep(0.05)
            self.conn.close("client close")
        self.connected = False

    # ================================================================ 主循环

    def update(self, dt: float) -> None:
        """主线程每帧调用，处理收到的所有消息。"""
        if self.conn is None:
            return
        for msg in self.conn.poll_messages():
            try:
                self._handle(msg)
            except Exception as exc:
                log.exception("客户端消息处理失败: %s", exc)
        if not self.conn.alive and self.connected:
            self.connected = False
            self.disconnected_reason = self.disconnected_reason or "与房主的连接已断开"
            self.messages.append(self.disconnected_reason)

    # ================================================================ 消息

    def _handle(self, msg: dict[str, Any]) -> None:
        ok, reason = proto.check_version(msg)
        if not ok:
            self.last_error = reason
            self.disconnected_reason = reason
            self.messages.append(reason)
            self.connected = False
            return

        mtype = msg["type"]

        if mtype == proto.MessageType.WELCOME:
            self.room_name = msg.get("room_name", self.room_name)
            return

        if mtype == proto.MessageType.JOIN_ACCEPTED:
            self.player_id = msg.get("player_id", "")
            self.name = msg.get("name", self.name)
            self.reconnect_token = msg.get("reconnect_token", "")
            self.room_name = msg.get("room_name", self.room_name)
            self.host_player_id = msg.get("host_player_id", "")
            self.connected = True
            self.messages.append(f"已加入房间「{self.room_name}」")
            return

        if mtype == proto.MessageType.JOIN_REJECTED:
            self.last_error = msg.get("error_message") or proto.error_text(
                str(msg.get("error_code", "")))
            self.messages.append(self.last_error)
            self.close(notify=False)
            return

        if mtype == proto.MessageType.LOBBY_STATE:
            self.lobby = dict(msg.get("lobby") or {})
            return

        if mtype == proto.MessageType.GAME_START:
            self.messages.append("游戏开始！")
            return

        if mtype == proto.MessageType.STATE_SNAPSHOT:
            self._apply_snapshot(msg.get("snapshot") or {})
            return

        if mtype == proto.MessageType.GAME_EVENT:
            ev = msg.get("event")
            if isinstance(ev, dict):
                self.incoming_events.append(ev)
                if len(self.incoming_events) > 64:
                    del self.incoming_events[:-64]
            return

        if mtype == proto.MessageType.COMMAND_RESULT:
            self.pending_command_id = None
            self.last_result_ok = bool(msg.get("ok", False))
            self.last_result_reason = msg.get("reason", "")
            if not self.last_result_ok and self.last_result_reason:
                self.messages.append(self.last_result_reason)
            return

        if mtype == proto.MessageType.ERROR:
            self.last_error = msg.get("error_message") or proto.error_text(
                str(msg.get("error_code", "")))
            self.messages.append(self.last_error)
            return

        if mtype == proto.MessageType.KICK:
            self.kicked = True
            self.disconnected_reason = msg.get("reason", "你被移出房间")
            self.messages.append(self.disconnected_reason)
            self.connected = False
            return

        if mtype == proto.MessageType.DISCONNECT:
            self.disconnected_reason = msg.get("reason", "房主关闭了房间")
            self.messages.append(self.disconnected_reason)
            self.connected = False
            return

        if mtype == proto.MessageType.VERSION_MISMATCH:
            self.last_error = msg.get("message", "版本不兼容")
            self.messages.append(self.last_error)
            self.connected = False
            return

        if mtype == proto.MessageType.RECONNECT_OK:
            self.player_id = msg.get("player_id", self.player_id)
            self.reconnect_token = msg.get("reconnect_token", self.reconnect_token)
            if "snapshot" in msg:
                self._apply_snapshot(msg.get("snapshot") or {})
            if "lobby" in msg:
                self.lobby = dict(msg.get("lobby") or {})
            self.connected = True
            self.messages.append("已重新连接")
            return

        if mtype == proto.MessageType.RECONNECT_FAIL:
            self.last_error = msg.get("error_message", "重连失败")
            self.messages.append(self.last_error)
            return

        if mtype == proto.MessageType.PING:
            server_time = float(msg.get("server_time", 0.0))
            if server_time:
                # 单程估算（两端时钟未必同步，仅用于展示量级）
                self.latency_ms = max(0.0, (time.time() - server_time) * 1000.0)
            if self.conn is not None:
                self.conn.send_message(proto.MessageType.PONG,
                                       {"server_time": msg.get("server_time", 0)})
            return

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        """只接受更新的快照，禁止旧数据回滚。"""
        if not snapshot:
            return
        rev = int(snapshot.get("revision", -1))
        if rev <= self.revision:
            return
        self.snapshot = snapshot
        self.revision = rev
        try:
            self.state = state_from_snapshot(snapshot)
        except Exception as exc:
            log.warning("快照解析失败：%s", exc)
            return
        server_time = float(snapshot.get("server_time", 0.0))
        if server_time:
            self.server_time_offset = time.time() - server_time

    # ================================================================ 指令

    def send_command(self, cmd_type: str, payload: dict[str, Any] | None = None,
                     decision_id: str | None = None) -> str | None:
        """把玩家意图发回 Host。返回 command_id。"""
        if self.conn is None or not self.conn.alive or not self.player_id:
            self.messages.append("尚未连接，无法操作")
            return None
        cmd = Command(
            ctype=cmd_type,
            player_id=self.player_id,
            payload=payload or {},
            client_revision=self.revision,
            decision_id=decision_id,
        )
        self.pending_command_id = cmd.command_id
        self.conn.send_message(proto.MessageType.COMMAND, {"command": cmd.to_dict()})
        return cmd.command_id

    def resolve_decision(self, decision_id: str, option_id: str) -> str | None:
        return self.send_command("RESOLVE_DECISION", {"option_id": option_id},
                                 decision_id=decision_id)

    def send_ready(self, ready: bool) -> None:
        self._send(proto.MessageType.PLAYER_READY, {"ready": ready})

    def send_character(self, character_id: str, color_id: str = "") -> None:
        self._send(proto.MessageType.SET_CHARACTER,
                   {"character_id": character_id, "color_id": color_id})

    def request_start(self) -> None:
        self._send(proto.MessageType.GAME_START, {})

    def send_map(self, map_file: str) -> None:
        self._send(proto.MessageType.SET_MAP, {"map_file": map_file})

    def send_preset(self, preset: str) -> None:
        self._send(proto.MessageType.SET_PRESET, {"preset": preset})

    def leave(self) -> None:
        self._send(proto.MessageType.LEAVE, {})
        self.close(notify=False)

    def _send(self, mtype: str, payload: dict[str, Any]) -> None:
        if self.conn is not None and self.conn.alive:
            self.conn.send_message(mtype, payload)

    # ================================================================ 查询

    @property
    def is_host(self) -> bool:
        return bool(self.player_id) and self.player_id == self.host_player_id

    @property
    def in_game(self) -> bool:
        return bool(self.state) and not getattr(self.state, "game_over", False)

    def my_player(self):
        if self.state is None or not self.player_id:
            return None
        return self.state.player(self.player_id)

    def is_my_turn(self) -> bool:
        return bool(self.state) and self.state.current_player_id == self.player_id

    def my_decision(self):
        """当前是否轮到我做决策。"""
        if self.state is None or not self.player_id:
            return None
        pd = getattr(self.state, "pending_decision", None)
        if pd is not None and pd.player_id == self.player_id:
            return pd
        return None

    def drain_messages(self) -> list[str]:
        out = self.messages
        self.messages = []
        return out

    def drain_events(self) -> list[dict[str, Any]]:
        out = self.incoming_events
        self.incoming_events = []
        return out

    def describe(self) -> str:
        if not self.connected:
            return "未连接"
        ping = f" | 延迟 {self.latency_ms:.0f}ms" if self.latency_ms else ""
        return f"已连接 {self.host_address}{ping}"
