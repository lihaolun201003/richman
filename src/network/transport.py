"""TCP 传输层：每个连接一对收发线程，绝不阻塞主循环。

主线程（pygame 循环）只做两件事：
    conn.send_message(...)        —— 非阻塞，写进发送队列
    conn.poll_messages()          —— 非阻塞，取出已收到的消息

所有 socket 读写都在后台线程完成。
"""
from __future__ import annotations

import queue
import socket
import threading
from typing import Any, Callable

from ..utils.logging_setup import get_logger
from .protocol import LENGTH_SIZE, MAX_MESSAGE_BYTES, MessageDecoder, encode

log = get_logger(__name__)

#: 单次 recv 缓冲区
RECV_CHUNK = 65536


class ConnectionClosed(Exception):
    """连接已关闭。"""


class TcpConnection:
    """一条 TCP 连接（Host 侧代表一个客户端）。"""

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int] | None = None,
        name: str = "",
    ) -> None:
        self.sock = sock
        self.addr = addr or ("?", 0)
        self.name = name
        self.decoder = MessageDecoder()
        self.inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self.send_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=512)
        self.closed = threading.Event()
        self.peer_closed = threading.Event()
        self.send_error: str = ""
        self._seq = 0
        self._send_thread: threading.Thread | None = None
        self._recv_thread: threading.Thread | None = None
        self.on_close: Callable[["TcpConnection"], None] | None = None
        self.latency_ms: float = 0.0
        self._bytes_sent = 0
        self._bytes_recv = 0

        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name=f"recv-{self.name or self.addr}", daemon=True
        )
        self._send_thread = threading.Thread(
            target=self._send_loop, name=f"send-{self.name or self.addr}", daemon=True
        )
        self._recv_thread.start()
        self._send_thread.start()

    def close(self, reason: str = "") -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.send_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        if self.on_close is not None:
            try:
                self.on_close(self)
            except Exception:  # pragma: no cover
                pass
        log.debug("连接已关闭 %s (%s)", self.addr, reason)

    @property
    def alive(self) -> bool:
        return not self.closed.is_set() and not self.peer_closed.is_set()

    # ------------------------------------------------------------ 收发

    def send_message(self, msg_type: str, payload: dict[str, Any] | None = None) -> bool:
        """非阻塞发送。队列满表示对端处理不过来，直接判定断开更安全。"""
        if not self.alive:
            return False
        self._seq += 1
        try:
            data = encode(msg_type, payload, seq=self._seq)
        except ValueError as exc:
            log.warning("编码消息失败：%s", exc)
            return False
        try:
            self.send_queue.put_nowait(data)
            return True
        except queue.Full:
            log.warning("发送队列已满，断开连接 %s", self.addr)
            self.close("send queue full")
            return False

    def poll_messages(self, limit: int = 64) -> list[dict[str, Any]]:
        """非阻塞取出已收到的消息。"""
        out: list[dict[str, Any]] = []
        for _ in range(limit):
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out

    # ------------------------------------------------------------ 线程

    def _recv_loop(self) -> None:
        try:
            while not self.closed.is_set():
                data = self.sock.recv(RECV_CHUNK)
                if not data:
                    self.peer_closed.set()
                    break
                self._bytes_recv += len(data)
                try:
                    for msg in self.decoder.feed(data):
                        self.inbox.put(msg)
                except ValueError as exc:
                    log.warning("协议解析失败 %s: %s", self.addr, exc)
                    self.peer_closed.set()
                    break
        except OSError:
            self.peer_closed.set()
        except Exception as exc:  # pragma: no cover
            log.exception("接收线程异常 %s: %s", self.addr, exc)
            self.peer_closed.set()
        finally:
            self.peer_closed.set()
            self.close("recv loop end")

    def _send_loop(self) -> None:
        try:
            while True:
                item = self.send_queue.get()
                if item is None or self.closed.is_set():
                    break
                self.sock.sendall(item)
                self._bytes_sent += len(item)
        except OSError as exc:
            self.send_error = str(exc)
            self.peer_closed.set()
        except Exception as exc:  # pragma: no cover
            log.exception("发送线程异常 %s: %s", self.addr, exc)
            self.peer_closed.set()
        finally:
            self.peer_closed.set()
            self.close("send loop end")

    # ------------------------------------------------------------ 统计

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "addr": f"{self.addr[0]}:{self.addr[1]}",
            "alive": self.alive,
            "sent": self._bytes_sent,
            "recv": self._bytes_recv,
            "pending": self.send_queue.qsize(),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TcpConnection {self.addr} alive={self.alive}>"


def connect_to(host: str, port: int, timeout: float = 5.0) -> TcpConnection:
    """客户端连接 Host。失败抛出 OSError。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(None)
    return TcpConnection(sock, (host, port), name="client")


def listen_on(host: str, port: int, backlog: int = 8) -> socket.socket:
    """创建监听 socket。绑定 0.0.0.0 才能被局域网内其他机器访问。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(backlog)
    return srv


def local_ip_addresses() -> list[str]:
    """本机所有可用的局域网 IPv4 地址，用于在大厅里展示给朋友。"""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        # 兜底：通过 UDP 探测默认出口地址（不会真的发包）
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return ips or ["127.0.0.1"]


def primary_ip() -> str:
    ips = local_ip_addresses()
    return ips[0] if ips else "127.0.0.1"
