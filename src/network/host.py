"""局域网 Host：唯一权威。

职责：
- 监听 TCP 端口，接受客户端加入；
- 维护大厅状态；
- 开局后持有唯一的 GameEngine 与唯一的 GameState；
- 接收客户端 Command → 校验 → 执行 → 广播新状态；
- 处理掉线、重连、超时转 AI。

主线程每帧调用 update(dt)，网络线程只负责收发字节。
"""
from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Any

from ..controllers.ai import AIController
from ..controllers.base import LocalController, RemoteController
from ..game.commands import Command
from ..game.events import ALERT_EVENTS, EventType
from ..game.setup import create_engine
from ..game.state import GameState
from ..utils.logging_setup import get_logger
from . import protocol as proto
from .lobby import PHASE_FINISHED, PHASE_LOBBY, PHASE_PLAYING, LobbyState
from .session import PlayerSession, new_player_id
from .transport import TcpConnection, listen_on, local_ip_addresses, primary_ip

log = get_logger(__name__)

#: 状态广播的最小间隔（秒），避免高频刷包
SNAPSHOT_INTERVAL = 0.08
#: 单次广播的日志条数上限
SNAPSHOT_LOG_TAIL = 40
#: 心跳间隔 / 超时
HEARTBEAT_INTERVAL = 2.0
#: 掉线宽限期：超过后由 AI 接管
RECONNECT_GRACE_SEC = 30.0


class HostError(Exception):
    """Host 启动失败。"""


class GameHost:
    """一个局域网房间的房主端。"""

    def __init__(
        self,
        room_name: str,
        host_name: str,
        host_character: str,
        host_color: str = "",
        port: int = 28080,
        max_players: int = 6,
        min_players: int = 2,
        anim_speed: float = 1.0,
        difficulty: str = "normal",
        map_file: str = "default_map.json",
        preset: str = "standard",
    ) -> None:
        self.port = int(port)
        self.anim_speed = anim_speed
        self.difficulty = difficulty
        self.lobby = LobbyState(
            room_name=room_name,
            host_player_id="",
            max_players=max_players,
            min_players=min_players,
            port=self.port,
            preset=preset,
        )
        self.lobby.set_map(map_file)
        self.engine: GameEngine | None = None
        self.errors: list[str] = []
        self.notices: list[str] = []
        self.running = False
        self.on_lobby_changed = None
        self.on_game_over = None

        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._accept_queue: queue.Queue[TcpConnection] = queue.Queue()
        self._conns: list[TcpConnection] = []
        self._conn_player: dict[int, str] = {}     # id(conn) -> player_id
        self._last_snapshot_at = 0.0
        self._last_broadcast_revision = -1
        self._last_event_seq = 0
        self._last_heartbeat = 0.0
        self._ping_seq = 0

        # 房主自己占第一个座位
        host_session = PlayerSession(
            player_id=new_player_id(),
            name=host_name,
            is_ai=False,
            is_host=True,
            character_id=host_character,
            color_id=host_color,
            slot=0,
        )
        host_session.ready = True
        self.lobby.sessions.append(host_session)
        self.lobby.host_player_id = host_session.player_id
        self.host_player_id = host_session.player_id
        self.host_controller = LocalController(host_session.player_id)

    # ================================================================ 网络

    def start(self) -> None:
        """绑定端口并开始接受连接。端口被占用时自动向后寻找。"""
        last_error: Exception | None = None
        for offset in range(0, 20):
            port = self.port + offset
            try:
                self._server_sock = listen_on("0.0.0.0", port)
                self.port = port
                self.lobby.port = port
                break
            except OSError as exc:
                last_error = exc
                continue
        if self._server_sock is None:
            raise HostError(f"端口绑定失败（已尝试 {self.port}-{self.port + 19}）：{last_error}")

        self.running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, name="host-accept", daemon=True)
        self._accept_thread.start()
        log.info("房间已开启：%s 端口 %s", self.lobby.room_name, self.port)
        self.notices.append(f"房间已在端口 {self.port} 开启")

    def _accept_loop(self) -> None:
        assert self._server_sock is not None
        while self.running:
            try:
                sock, addr = self._server_sock.accept()
            except OSError:
                break
            conn = TcpConnection(sock, addr, name=f"client-{addr[1]}")
            conn.start()
            try:
                self._accept_queue.put_nowait(conn)
            except queue.Full:  # pragma: no cover
                conn.close("accept queue full")

    def stop(self) -> None:
        self.running = False
        for conn in list(self._conns):
            try:
                conn.send_message(proto.MessageType.DISCONNECT,
                                  {"reason": proto.error_text("server_closed")})
            except Exception:
                pass
            conn.close("host stop")
        self._conns.clear()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

    # ================================================================ 主循环

    def update(self, dt: float) -> None:
        """主线程每帧调用。"""
        if not self.running:
            return
        self._accept_connections()
        self._poll_connections()
        self._check_timeouts()

        if self.engine is not None and self.lobby.phase == PHASE_PLAYING:
            self.engine.update(dt)
            self._pump_engine_events()
            self._maybe_broadcast_snapshot()
            if self.engine.state.game_over:
                self.lobby.phase = PHASE_FINISHED
                if self.on_game_over is not None:
                    try:
                        self.on_game_over(self.engine.state)
                    except Exception:
                        log.exception("结算回调异常")

        now = time.time()
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            self._last_heartbeat = now
            self._send_heartbeats()

    def _accept_connections(self) -> None:
        while True:
            try:
                conn = self._accept_queue.get_nowait()
            except queue.Empty:
                return
            self._conns.append(conn)
            conn.send_message(proto.MessageType.WELCOME, {
                "protocol_version": proto.PROTOCOL_VERSION,
                "room_name": self.lobby.room_name,
                "phase": self.lobby.phase,
                "player_count": self.lobby.player_count,
                "max_players": self.lobby.max_players,
            })
            log.info("新连接：%s", conn.addr)

    def _session_of(self, conn: TcpConnection) -> PlayerSession | None:
        pid = self._conn_player.get(id(conn))
        if pid is None:
            return None
        session = self.lobby.session(pid)
        if session is not None:
            session.conn = conn
        return session

    def _poll_connections(self) -> None:
        for conn in list(self._conns):
            session = self._session_of(conn)
            for msg in conn.poll_messages():
                if session is not None:
                    session.last_seen = time.time()
                try:
                    self._handle_message(conn, session, msg)
                except PermissionError:
                    pass
                except Exception as exc:
                    log.exception("处理消息失败 %s: %s", msg.get("type"), exc)
                    conn.send_message(proto.MessageType.ERROR,
                                      proto.make_error("invalid_command", str(exc)))

            if not conn.alive:
                self._drop_connection(conn, session)

    def _drop_connection(self, conn: TcpConnection, session: PlayerSession | None) -> None:
        if conn in self._conns:
            self._conns.remove(conn)
        self._conn_player.pop(id(conn), None)
        if session is not None and session.conn is conn:
            self._on_disconnect(session)

    # ================================================================ 消息处理

    def _handle_message(
        self, conn: TcpConnection, session: PlayerSession | None, msg: dict[str, Any]
    ) -> None:
        ok, reason = proto.check_version(msg)
        if not ok:
            conn.send_message(proto.MessageType.VERSION_MISMATCH, {"message": reason})
            conn.close("version mismatch")
            self.errors.append(reason)
            return

        mtype = msg["type"]

        # ---- 心跳与握手：任何阶段都接受
        if mtype == proto.MessageType.PING:
            conn.send_message(proto.MessageType.PONG, {"client_time": msg.get("client_time", 0)})
            return
        if mtype == proto.MessageType.PONG:
            sent_at = float(msg.get("server_time", 0))
            if sent_at:
                conn.latency_ms = max(0.0, (time.time() - sent_at) * 1000.0)
            return

        # ---- 未加入座位时只允许 JOIN / RECONNECT
        if session is None:
            if mtype == proto.MessageType.JOIN_REQUEST:
                self._handle_join(conn, msg)
                return
            if mtype == proto.MessageType.RECONNECT:
                self._handle_reconnect(conn, msg)
                return
            conn.send_message(proto.MessageType.ERROR,
                              proto.make_error("invalid_command", "尚未加入房间"))
            return

        if mtype == proto.MessageType.LEAVE:
            self._handle_leave(session)
            return
        if mtype == proto.MessageType.PLAYER_READY:
            self.lobby.set_ready(session.player_id, bool(msg.get("ready", True)))
            self.broadcast_lobby()
            return
        if mtype == proto.MessageType.SET_CHARACTER:
            self.lobby.set_character(session.player_id,
                                     str(msg.get("character_id", "")),
                                     str(msg.get("color_id", "")))
            self.broadcast_lobby()
            return
        if mtype == proto.MessageType.SET_MAP:
            self._require_host(session)
            if self.lobby.set_map(str(msg.get("map_file", ""))):
                self.notices.append(f"地图切换为「{self.lobby.map_name}」")
            self.broadcast_lobby()
            return
        if mtype == proto.MessageType.SET_PRESET:
            self._require_host(session)
            if self.lobby.set_preset(str(msg.get("preset", "standard"))):
                self.notices.append(
                    f"规则切换为「{self.lobby.preset_label()}」")
            self.broadcast_lobby()
            return
        if mtype == proto.MessageType.ADD_AI:
            self._require_host(session)
            self.lobby.add_ai(character_id=str(msg.get("character_id", "")),
                              color_id=str(msg.get("color_id", "")))
            self.broadcast_lobby()
            return
        if mtype == proto.MessageType.REMOVE_AI:
            self._require_host(session)
            target = self.lobby.session(str(msg.get("player_id", "")))
            if target is not None and target.is_ai:
                self.lobby.remove(target.player_id)
                self.broadcast_lobby()
            return
        if mtype == proto.MessageType.KICK:
            self._require_host(session)
            target = self.lobby.session(str(msg.get("player_id", "")))
            if target is not None and not target.is_host and not target.is_ai:
                if target.conn is not None:
                    target.conn.send_message(proto.MessageType.KICK, {"reason": "你被房主移出房间"})
                    target.conn.close("kicked")
                self.lobby.remove(target.player_id)
                self.broadcast_lobby()
            return
        if mtype == proto.MessageType.GAME_START:
            self._require_host(session)
            self.try_start_game()
            return
        if mtype == proto.MessageType.COMMAND:
            self._handle_command(session, msg)
            return

    def _require_host(self, session: PlayerSession) -> None:
        if session.player_id != self.lobby.host_player_id:
            if session.conn is not None:
                session.conn.send_message(
                    proto.MessageType.ERROR,
                    proto.make_error("not_host", proto.error_text("not_host")))
            raise PermissionError(proto.error_text("not_host"))

    def _handle_join(self, conn: TcpConnection, msg: dict[str, Any]) -> None:
        name = str(msg.get("name", "")).strip() or "玩家"
        character_id = str(msg.get("character_id", ""))
        color_id = str(msg.get("color_id", ""))

        session, err = self.lobby.add_human(name, conn, character_id, color_id)
        if session is None:
            conn.send_message(proto.MessageType.JOIN_REJECTED,
                              proto.make_error(err, proto.error_text(err)))
            log.info("拒绝加入 %s：%s", name, err)
            return

        self._conn_player[id(conn)] = session.player_id
        conn.send_message(proto.MessageType.JOIN_ACCEPTED, {
            "player_id": session.player_id,
            "name": session.name,
            "reconnect_token": session.reconnect_token,
            "room_name": self.lobby.room_name,
            "host_player_id": self.lobby.host_player_id,
            "port": self.port,
        })
        log.info("%s 加入了房间", name)
        self.notices.append(f"{name} 加入了房间")
        self.broadcast_lobby()

    def _handle_reconnect(self, conn: TcpConnection, msg: dict[str, Any]) -> None:
        token = str(msg.get("reconnect_token", ""))
        target = self.lobby.session_by_token(token)
        if target is None:
            conn.send_message(proto.MessageType.RECONNECT_FAIL,
                              proto.make_error("reconnect_failed",
                                               proto.error_text("reconnect_failed")))
            return
        target.mark_reconnected(conn)
        self._conn_player[id(conn)] = target.player_id

        payload: dict[str, Any] = {
            "player_id": target.player_id,
            "reconnect_token": target.reconnect_token,
        }
        if self.engine is not None:
            player = self.engine.state.player(target.player_id)
            if player is not None:
                player.disconnected = False
                player.bot_controlled = False
                ctrl = self.engine.controller_of(target.player_id)
                if not isinstance(ctrl, RemoteController):
                    ctrl = RemoteController(target.player_id)
                    self.engine.bind_controller(target.player_id, ctrl)
                ctrl.mark_connected()
                self.engine.state.log(EventType.PLAYER_RECONNECTED,
                                      f"{player.name} 重新连接成功", player.id)
            payload["snapshot"] = self._snapshot_for_send()
        else:
            payload["lobby"] = self.lobby.to_dict()

        conn.send_message(proto.MessageType.RECONNECT_OK, payload)
        self.notices.append(f"{target.name} 重新连接")
        self.broadcast_lobby()
        if self.engine is not None:
            self._force_broadcast()

    def _handle_leave(self, session: PlayerSession) -> None:
        if self.lobby.phase == PHASE_LOBBY:
            if session.conn is not None:
                self._conn_player.pop(id(session.conn), None)
                session.conn.close("leave")
            self.lobby.remove(session.player_id)
            self.notices.append(f"{session.name} 离开了房间")
            self.broadcast_lobby()
        else:
            self._on_disconnect(session)

    def _handle_command(self, session: PlayerSession, msg: dict[str, Any]) -> None:
        if self.engine is None or self.lobby.phase != PHASE_PLAYING:
            return
        payload = msg.get("command")
        if not isinstance(payload, dict):
            return
        # 关键：用 session 的 player_id 覆盖，客户端无法冒充他人
        payload["player_id"] = session.player_id
        cmd = Command.from_dict(payload)
        ctrl = self.engine.controller_of(session.player_id)
        if not isinstance(ctrl, RemoteController):
            return
        ctrl.submit(cmd)

    # ================================================================ 掉线

    def _on_disconnect(self, session: PlayerSession) -> None:
        if session.disconnected:
            return
        session.mark_disconnected()
        log.info("%s 掉线", session.name)
        self.notices.append(f"{session.name} 掉线")
        if self.engine is not None and self.lobby.phase == PHASE_PLAYING:
            player = self.engine.state.player(session.player_id)
            if player is not None:
                player.disconnected = True
                self.engine.state.log(EventType.PLAYER_DISCONNECTED,
                                      f"{player.name} 与房间失去连接", player.id)
            self.broadcast_lobby()
            self._force_broadcast()
        elif self.lobby.phase == PHASE_LOBBY:
            self.lobby.remove(session.player_id)
            self.broadcast_lobby()

    def _check_timeouts(self) -> None:
        if self.engine is None or self.lobby.phase != PHASE_PLAYING:
            return
        now = time.time()
        for session in list(self.lobby.sessions):
            if not session.disconnected or session.is_ai or session.bot_takeover:
                continue
            if now - session.disconnected_at < RECONNECT_GRACE_SEC:
                continue
            session.bot_takeover = True
            player = self.engine.state.player(session.player_id)
            if player is not None:
                player.bot_controlled = True
                self.engine.bind_controller(
                    session.player_id,
                    AIController(session.player_id, difficulty=self.difficulty,
                                 seed=abs(hash(session.player_id)) % 100000),
                )
                self.engine.state.log(EventType.PLAYER_BOT_TAKEOVER,
                                      f"{player.name} 掉线超时，由 AI 代为操作", player.id)
                self.notices.append(f"{player.name} 已由 AI 接管")
            self.broadcast_lobby()
            self._force_broadcast()

    def _send_heartbeats(self) -> None:
        self._ping_seq += 1
        now = time.time()
        for conn in list(self._conns):
            if conn.alive:
                conn.send_message(proto.MessageType.PING, {"server_time": now, "seq": self._ping_seq})

    # ================================================================ 开局

    def try_start_game(self) -> tuple[bool, str]:
        ok, reason = self.lobby.can_start()
        if not ok:
            self.errors.append(reason)
            self._broadcast_error("not_ready", reason)
            return False, reason

        specs = []
        for s in self.lobby.sessions:
            specs.append({
                "id": s.player_id,
                "name": s.name,
                "character_id": s.character_id,
                "color_id": s.color_id or None,
                "is_ai": s.is_ai,
                "is_host": s.is_host,
            })

        self.engine = create_engine(
            specs, anim_speed=self.anim_speed,
            map_file=self.lobby.map_file, preset=self.lobby.preset)
        for s in self.lobby.sessions:
            if s.is_ai:
                self.engine.bind_controller(
                    s.player_id,
                    AIController(s.player_id, difficulty=self.difficulty,
                                 seed=abs(hash(s.player_id)) % 100000))
            elif s.is_host:
                self.engine.bind_controller(s.player_id, self.host_controller)
            else:
                self.engine.bind_controller(s.player_id, RemoteController(s.player_id))

        self.engine.start()
        self.lobby.phase = PHASE_PLAYING
        self._last_event_seq = 0
        self._last_broadcast_revision = -1

        self.broadcast(proto.MessageType.GAME_START, {
            "match_id": self.engine.state.match_id,
            "seed": self.engine.state.seed,
        })
        self._force_broadcast()
        self.broadcast_lobby()
        log.info("游戏开始，%d 名参与者", len(specs))
        return True, ""

    def return_to_lobby(self) -> None:
        """结算后回到大厅：保留玩家座位，重置准备状态。"""
        self.lobby.phase = PHASE_LOBBY
        for s in self.lobby.sessions:
            s.ready = s.is_ai or s.is_host
            s.bot_takeover = False
            s.disconnected = not s.connected
        self.engine = None
        self._last_broadcast_revision = -1
        self._last_event_seq = 0
        self.broadcast_lobby()

    # ================================================================ 广播

    def broadcast(self, msg_type: str, payload: dict[str, Any]) -> None:
        for conn in list(self._conns):
            if conn.alive:
                conn.send_message(msg_type, payload)

    def broadcast_lobby(self) -> None:
        self.broadcast(proto.MessageType.LOBBY_STATE, {"lobby": self.lobby.to_dict()})
        if self.on_lobby_changed is not None:
            try:
                self.on_lobby_changed()
            except Exception:
                log.exception("大厅回调异常")

    def _broadcast_error(self, code: str, message: str) -> None:
        self.broadcast(proto.MessageType.ERROR, proto.make_error(code, message))

    def _snapshot_for_send(self) -> dict[str, Any]:
        """生成广播用快照：裁掉过长的日志，控制包体积。"""
        assert self.engine is not None
        snap = self.engine.state.to_dict()
        log_list = snap.get("event_log") or []
        if len(log_list) > SNAPSHOT_LOG_TAIL:
            snap["event_log"] = log_list[-SNAPSHOT_LOG_TAIL:]
        snap["server_time"] = time.time()
        return snap

    def _pump_engine_events(self) -> None:
        """把引擎产生的新事件单独广播给客户端（用于触发弹窗/动画）。"""
        assert self.engine is not None
        state = self.engine.state
        new_events = [e for e in state.event_log if e.seq > self._last_event_seq]
        if not new_events:
            return
        self._last_event_seq = new_events[-1].seq
        for ev in new_events:
            if ev.type in ALERT_EVENTS or ev.data.get("alert"):
                self.broadcast(proto.MessageType.GAME_EVENT, {"event": ev.to_dict()})

    def _maybe_broadcast_snapshot(self) -> None:
        assert self.engine is not None
        now = time.time()
        state = self.engine.state
        if state.revision == self._last_broadcast_revision:
            return
        if now - self._last_snapshot_at < SNAPSHOT_INTERVAL:
            return
        self._last_snapshot_at = now
        self._last_broadcast_revision = state.revision
        self.broadcast(proto.MessageType.STATE_SNAPSHOT, {"snapshot": self._snapshot_for_send()})

    def _force_broadcast(self) -> None:
        self.push_snapshot()

    def push_snapshot(self) -> None:
        """立即把当前权威状态推送给所有客户端（不等限流窗口）。"""
        if self.engine is None:
            return
        self._last_broadcast_revision = self.engine.state.revision
        self._last_snapshot_at = time.time()
        self.broadcast(proto.MessageType.STATE_SNAPSHOT, {"snapshot": self._snapshot_for_send()})

    # ================================================================ 查询

    @property
    def state(self) -> GameState | None:
        return self.engine.state if self.engine else None

    def ip_addresses(self) -> list[str]:
        return local_ip_addresses()

    def primary_ip(self) -> str:
        return primary_ip()

    def room_info(self) -> dict[str, Any]:
        return {
            "room_name": self.lobby.room_name,
            "port": self.port,
            "ips": self.ip_addresses(),
            "phase": self.lobby.phase,
            "players": self.lobby.player_count,
            "max_players": self.lobby.max_players,
        }

    def drain_notices(self) -> list[str]:
        out = self.notices
        self.notices = []
        return out

    def drain_errors(self) -> list[str]:
        out = self.errors
        self.errors = []
        return out
