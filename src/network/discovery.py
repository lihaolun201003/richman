"""局域网自动发现：UDP 广播。

Host 周期性广播房间信息，Client 监听即可列出同网段的房间。
手动输入 IP 始终是可靠入口，自动发现只是锦上添花。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Callable

from ..utils.logging_setup import get_logger
from . import protocol as proto

log = get_logger(__name__)

DISCOVERY_MAGIC = "RICHMAN_ROOM"
BROADCAST_ADDRS = ["255.255.255.255", "<broadcast>"]


def _broadcast_targets() -> list[str]:
    from . import netinfo

    targets = ["255.255.255.255"]
    # 补充各网卡的子网广播地址，某些交换机不放行 255.255.255.255
    for adapter in netinfo.list_adapters(include_loopback=False):
        ip = adapter.ip
        parts = ip.split(".")
        if len(parts) == 4 and not ip.startswith("169.254."):
            targets.append(".".join(parts[:3] + ["255"]))
    return list(dict.fromkeys(targets))


class DiscoveryBroadcaster:
    """Host 侧：周期广播房间信息。"""

    def __init__(self, port: int, info_provider: Callable[[], dict[str, Any]],
                 interval: float = 1.5) -> None:
        self.port = port
        self.info_provider = info_provider
        self.interval = interval
        self.running = False
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        if self.running:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock = sock
        except OSError as exc:
            log.warning("自动发现不可用：%s", exc)
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="discovery-broadcast", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        targets = _broadcast_targets()
        while self.running:
            try:
                info = self.info_provider()
                info["magic"] = DISCOVERY_MAGIC
                info["protocol_version"] = proto.PROTOCOL_VERSION
                info["ts"] = time.time()
                data = json.dumps(info, ensure_ascii=False).encode("utf-8")
                for addr in targets:
                    try:
                        self._sock.sendto(data, (addr, self.port))
                    except OSError:
                        continue
            except Exception as exc:  # pragma: no cover
                log.debug("广播异常：%s", exc)
            time.sleep(self.interval)

    def stop(self) -> None:
        self.running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class RoomInfo:
    """Client 侧看到的一个房间。"""

    __slots__ = ("room_name", "host_name", "ip", "port", "players", "max_players",
                 "phase", "map_name", "preset_name", "seen_at", "protocol_version",
                 "app_version")

    def __init__(self, d: dict[str, Any], ip: str) -> None:
        self.room_name = d.get("room_name", "未命名房间")
        self.host_name = d.get("host_name", "")
        self.ip = ip
        self.port = int(d.get("port", 0))
        self.players = int(d.get("players", 0))
        self.max_players = int(d.get("max_players", 6))
        self.phase = d.get("phase", "lobby")
        self.map_name = d.get("map_name", "")
        self.preset_name = d.get("preset_name", "") or d.get("preset", "")
        self.seen_at = time.time()
        self.protocol_version = int(d.get("protocol_version", 0))
        self.app_version = str(d.get("app_version", "") or "")

    @property
    def full(self) -> bool:
        return self.players >= self.max_players

    @property
    def version_ok(self) -> bool:
        """广播里带了协议号就比对；没带（很旧的版本）就当不兼容。"""
        if not self.protocol_version:
            return False
        return self.protocol_version == proto.PROTOCOL_VERSION

    @property
    def joinable(self) -> bool:
        return self.phase == "lobby" and not self.full and self.version_ok

    @property
    def age_sec(self) -> float:
        """这条广播多久之前收到的（越新越可信）。"""
        return max(0.0, time.time() - self.seen_at)

    def status_label(self) -> str:
        """给玩家看的短状态：可加入 / 版本不一致 / 已开局 / 已满。"""
        if not self.version_ok:
            return "版本不一致"
        if self.phase != "lobby":
            return "已开局"
        if self.full:
            return "已满"
        return "可加入"

    def status_color(self) -> str:
        if not self.version_ok:
            return "warning"
        if self.phase != "lobby" or self.full:
            return "text_mute"
        return "success"

    def detail_line(self) -> str:
        host = f"房主 {self.host_name}　·　" if self.host_name else ""
        return (f"{self.ip}:{self.port}　·　{self.players}/{self.max_players} 人　·　"
                f"{host}{self.map_name}")

    def label(self) -> str:
        return f"{self.room_name}  {self.ip}:{self.port}  {self.players}/{self.max_players}  {self.status_label()}"

    @classmethod
    def from_packet(cls, data: bytes, ip: str) -> "RoomInfo | None":
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(doc, dict) or doc.get("magic") != DISCOVERY_MAGIC:
            return None
        return cls(doc, ip)


class DiscoveryListener:
    """Client 侧：监听房间广播。"""

    def __init__(self, port: int, timeout_sec: float = 30.0) -> None:
        self.port = port
        self.timeout_sec = timeout_sec
        self.rooms: dict[str, RoomInfo] = {}
        self.running = False
        self.error: str = ""
        self.last_packet_at: float = 0.0
        self.packet_count: int = 0
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self.running:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.settimeout(0.5)
            self._sock = sock
        except OSError as exc:
            self.error = f"无法监听发现端口 {self.port}：{exc}"
            log.warning(self.error)
            return False
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="discovery-listen", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        while self.running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                self._prune()
                continue
            except OSError:
                break
            info = RoomInfo.from_packet(data, addr[0])
            if info is None:
                continue
            self.last_packet_at = time.time()
            self.packet_count += 1
            key = f"{info.ip}:{info.port}"
            with self._lock:
                self.rooms[key] = info

    def _prune(self) -> None:
        now = time.time()
        with self._lock:
            stale = [k for k, v in self.rooms.items() if now - v.seen_at > self.timeout_sec]
            for k in stale:
                del self.rooms[k]

    def snapshot(self) -> list[RoomInfo]:
        self._prune()
        with self._lock:
            rooms = list(self.rooms.values())
        rooms.sort(key=lambda r: (not r.joinable, -r.seen_at))
        return rooms

    def stop(self) -> None:
        self.running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
