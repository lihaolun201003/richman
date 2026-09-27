"""联机诊断中心的数据层：检测、连接测试、诊断包导出。

三条铁律（决定了这页能不能被普通玩家信任）：

1. **只说自己真的测过的事**。本模块从不宣称「Windows 防火墙已放行」——
   那需要管理员权限与平台 API；拿不到就不给结论，只给「怎么试」。
2. **每个结论都配一个动作**。玩家要的不是「TCP 超时」，而是
   「检查房主游戏是否还开着 / 是不是同一个 WiFi」。
3. **失败要分类**。「连接失败」这四个字会让玩家无从下手，
   而「对方拒绝了连接，通常代表对面没有开房间」是可以直接行动的信息。

所有检测都在主线程（少量非阻塞 socket 调用）或后台线程完成，
UI 只读结果，不在这里碰 pygame。
"""
from __future__ import annotations

import errno
import json
import os
import platform
import socket
import struct
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any

from ..utils.logging_setup import get_logger
from ..version import APP_VERSION
from . import netinfo, protocol as proto

log = get_logger(__name__)

DEFAULT_PORT = 28080
DISCOVERY_PORT = 28081

#: 连接测试的判定结果
TEST_OK = "ok"
TEST_REFUSED = "refused"
TEST_TIMEOUT = "timeout"
TEST_UNREACHABLE = "unreachable"
TEST_DNS = "dns"
TEST_VERSION = "version"
TEST_FULL = "full"
TEST_STARTED = "started"
TEST_PROTOCOL = "protocol"
TEST_UNKNOWN = "unknown"

TEST_LABELS = {
    TEST_OK: "连接成功",
    TEST_REFUSED: "对方拒绝连接",
    TEST_TIMEOUT: "连接超时",
    TEST_UNREACHABLE: "网络不可达",
    TEST_DNS: "地址无法解析",
    TEST_VERSION: "版本不一致",
    TEST_FULL: "房间已满",
    TEST_STARTED: "游戏已开始",
    TEST_PROTOCOL: "协议错误",
    TEST_UNKNOWN: "未知错误",
}

#: 每一种失败 → 给玩家 2～4 条可执行动作
TEST_ADVICE: dict[str, list[str]] = {
    TEST_OK: ["可以放心加入这个房间了。"],
    TEST_REFUSED: [
        "对方电脑上这个端口没有程序在听：多半是房主**还没点「创建房间」**，或者房间已经关了。",
        "如果房主确实开着，请他对照大厅里的端口号再看一眼，是不是填错了端口。",
    ],
    TEST_TIMEOUT: [
        "确认两台电脑连的是同一个 Wi-Fi / 同一个路由器。",
        "请房主确认游戏还开着（最小化没问题，关掉就不行了）。",
        "核对 IP 有没有输错：房主大厅里那一行大字才是正确地址。",
        "让房主在大厅点一次「复制连接信息」直接发给你，避免手打错。",
        "仍然不通时，多半是防火墙或路由器「AP 隔离」：请房主在 Windows 防火墙弹窗里勾选「专用网络」并允许。",
    ],
    TEST_UNREACHABLE: [
        "这个地址在本机没有路由：常见于填了 10.x / 172.x 但你们其实不在同一网段。",
        "两台电脑都执行 ipconfig，确认 IPv4 前三段一致（例如都是 192.168.1.x）。",
    ],
    TEST_DNS: [
        "这里只支持 IP 地址，不支持主机名。",
        "请房主在大厅里查看「复制连接信息」里的 IP 再填一次。",
    ],
    TEST_VERSION: [
        "两台电脑用的游戏文件不是同一个版本：把同一个 zip 解压，双方都用同一份。",
    ],
    TEST_FULL: ["房间已经 6 个人了，请房主先移出一个座位。"],
    TEST_STARTED: ["这一局已经开始了，等他们打完，或请房主开新房间。"],
    TEST_PROTOCOL: [
        "端口通了，但对面不是 Richman（可能是别的程序占用了这个端口）。",
        "请房主在大厅里看看实际端口号——端口被占用时游戏会自动往后换。",
    ],
    TEST_UNKNOWN: ["把「导出联机诊断」生成的 zip 发给开发者，里面记录了这次尝试。"],
}


@dataclass
class CheckItem:
    """诊断页里的一行。"""

    label: str
    value: str
    level: str = "text"          # success / warning / danger / text_dim / info
    icon: str = "info"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "level": self.level}


@dataclass
class NetworkReport:
    """一次完整的本机联机自检结果。"""

    version: str = ""
    network_kind: str = ""
    adapters: list[netinfo.Adapter] = field(default_factory=list)
    primary_ip: str = ""
    tcp_state: str = ""
    tcp_port: int = DEFAULT_PORT
    udp_state: str = ""
    udp_ok: bool = False
    tcp_ok: bool = False
    last_connection: str = ""
    last_connection_ok: bool = False
    role: str = ""
    items: list[CheckItem] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    #: 玩家人话版的一行总结
    def headline(self) -> str:
        if self.warnings:
            return self.warnings[0]
        if self.tcp_ok:
            return "本机已经准备好当房主，可以把连接地址发给室友。"
        return "本机网络正常；加入别人的房间不需要在这里做任何设置。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "network_kind": self.network_kind,
            "adapters": [a.to_dict() for a in self.adapters],
            "primary_ip": self.primary_ip,
            "tcp_state": self.tcp_state,
            "udp_state": self.udp_state,
            "last_connection": self.last_connection,
            "role": self.role,
            "warnings": list(self.warnings),
        }


def check_udp_port(port: int = DISCOVERY_PORT) -> tuple[bool, str]:
    """UDP 发现端口能不能用（能绑上就是可用）。

    注意：这里的「可用」只代表本机能收广播，**不代表一定能收到**——
    很多校园网会屏蔽广播包，这一点必须如实告诉玩家。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        return True, "可用"
    except OSError as exc:
        return False, f"不可用（{exc.strerror or exc}）"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def check_tcp_port(port: int) -> tuple[bool, str]:
    """TCP 端口能不能绑（能绑=可以建房）。"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return True, "空闲"
    except OSError as exc:
        return False, f"被占用（{exc.strerror or exc}）"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def collect(app: Any = None) -> NetworkReport:
    """采集本机联机状态。app 可为 None（只做网络层检测）。"""
    report = NetworkReport()
    report.version = APP_VERSION
    report.adapters = netinfo.lan_endpoints()
    report.network_kind = netinfo.network_kind()
    report.primary_ip = netinfo.best_ip()

    host = getattr(app, "host", None)
    client = getattr(app, "client", None)
    last = getattr(app, "last_connection_result", None)

    # ---- 服务端
    if host is not None:
        report.role = "host"
        report.tcp_port = host.port
        report.tcp_ok = True
        report.tcp_state = f"监听中（端口 {host.port}，{host.lobby.player_count} 人）"
    elif client is not None:
        report.role = "client"
        report.tcp_ok = True
        report.tcp_port = getattr(client, "last_local_port", 0) or DEFAULT_PORT
        report.tcp_state = f"作为客户端连接到 {client.host_address}"
    else:
        report.role = ""
        report.tcp_port = int(getattr(app.settings, "last_port", DEFAULT_PORT)
                              if app is not None else DEFAULT_PORT)
        ok, text = check_tcp_port(DEFAULT_PORT)
        report.tcp_ok = ok
        report.tcp_state = (f"未启动（端口 {DEFAULT_PORT} {text}，"
                            f"回主菜单点「创建房间」即可）")

    # ---- UDP 发现
    if host is not None:
        report.udp_ok = True
        report.udp_state = f"广播中（端口 {DISCOVERY_PORT}）"
    else:
        report.udp_ok, text = check_udp_port()
        report.udp_state = "可用" if report.udp_ok else text

    # ---- 最近一次连接
    if last:
        report.last_connection = str(last.get("text", ""))
        report.last_connection_ok = bool(last.get("ok", False))
    else:
        report.last_connection = "本次运行还没有连接记录"

    _build_items(report)
    _build_advice(report)
    return report


def _build_items(report: NetworkReport) -> None:
    items: list[CheckItem] = []
    items.append(CheckItem("游戏版本", report.version, "text", "info"))
    items.append(CheckItem("当前网络", report.network_kind or "未知",
                           "success" if "未检测到" not in report.network_kind else "warning",
                           "network"))

    physical = [a for a in report.adapters if not a.is_virtual]
    virtual = [a for a in report.adapters if a.is_virtual]
    if not physical:
        items.append(CheckItem("本机地址", "没有可用的局域网地址", "danger", "network",
                               "室友无法连接到这台电脑"))
    else:
        first = physical[0]
        text, level = netinfo.describe_address(first.ip)
        items.append(CheckItem(
            "推荐地址", f"{first.ip}（{first.name or first.kind_label}）", level, "network",
            text))
        for extra in physical[1:3]:
            items.append(CheckItem(
                "其它地址", f"{extra.ip}（{extra.name or extra.kind_label}）",
                "text_dim", "network", ""))
    if virtual:
        names = "、".join(sorted({a.name or "虚拟网卡" for a in virtual})[:3])
        items.append(CheckItem("虚拟网卡", f"{names}（{len(virtual)} 个，已忽略）",
                               "text_dim", "info",
                               "虚拟网卡地址室友连不上，这里不推荐使用"))

    items.append(CheckItem("服务端端口", report.tcp_state,
                           "success" if report.tcp_ok else "text_dim", "server"))
    items.append(CheckItem(
        "房间发现（UDP）", report.udp_state, "success" if report.udp_ok else "warning",
        "network",
        "本机可以收发广播；但部分校园网 / 访客 WiFi 会屏蔽广播包"))
    if report.last_connection:
        items.append(CheckItem("最近连接", report.last_connection,
                               "success" if report.last_connection_ok else "text_dim",
                               "network"))
    report.items = items


def _build_advice(report: NetworkReport) -> None:
    """按真实状态给下一步动作，而不是固定清单。"""
    advice: list[str] = []
    warnings: list[str] = []

    if report.primary_ip.startswith("127."):
        warnings.append("本机没有检测到局域网地址（只有回环地址）")
        advice.append("这台电脑现在只能自己连自己：请先连上 Wi-Fi 或插上网线。")
    if report.primary_ip.startswith("169.254."):
        warnings.append("当前地址是 169.254 开头，说明没有正常拿到局域网地址")
        advice.append("169.254 开头的地址室友连不上：请重连 Wi-Fi，"
                      "或把路由器重启一次再试。")

    physical = [a for a in report.adapters if not a.is_virtual]
    if len(physical) > 1:
        advice.append("这台电脑有多个可用的局域网地址："
                      "如果室友用第一个地址连不上，请把「其它地址」也发给他试一次。")

    if not report.udp_ok:
        advice.append("房间自动发现（UDP）在本机不可用："
                      "不影响联机，让房主把连接地址发给你，手动填进「加入房间」即可。")
    elif report.role != "host":
        advice.append("自动发现只在本机可收广播；如果搜不到房间，"
                      "多半是网络屏蔽了广播，手动输入地址永远是保底方案。")

    if report.udp_ok and not warnings:
        advice.append("有的校园网 / 访客 WiFi 会屏蔽广播，导致搜不到房间——"
                      "这种情况请让房主复制地址给你手动加入。")

    if report.role == "host":
        advice.append("你已经开着房间：把「连接地址」发给室友即可。")
    elif not report.tcp_ok:
        advice.append("想自己建房，回主菜单点「创建房间」；加入别人的房间不需要改动本机设置。")

    report.advice = advice
    report.warnings = warnings


# ==================================================================== 连接测试

@dataclass
class TestResult:
    """一次「测试连接」的结果。"""

    status: str = TEST_UNKNOWN
    message: str = ""
    detail: str = ""
    host: str = ""
    port: int = 0
    elapsed_ms: int = 0
    room: dict[str, Any] = field(default_factory=dict)
    advice: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == TEST_OK

    @property
    def label(self) -> str:
        return TEST_LABELS.get(self.status, TEST_LABELS[TEST_UNKNOWN])

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "label": self.label, "message": self.message,
            "detail": self.detail, "host": self.host, "port": self.port,
            "elapsed_ms": self.elapsed_ms, "room": self.room, "advice": self.advice,
        }


def parse_target(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int, str]:
    """把玩家输入解析成 (host, port, 错误信息)。

    支持这些写法（都是玩家会真的粘进来的形式）：
        192.168.1.10
        192.168.1.10:28080
        192.168.1.10：28080   （中文冒号）
        http://192.168.1.10:28080
        "192.168.1.10:28080"  （带引号，从聊天软件复制常见）
    """
    raw = (text or "").strip().strip('"').strip("'").strip()
    if not raw:
        return "", default_port, "请输入房主的 IP 地址"
    for prefix in ("http://", "https://", "richman://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.split("/")[0]
    raw = raw.replace("：", ":")
    if raw.count(":") == 1:
        host_part, port_part = raw.split(":")
        port_part = port_part.strip()
        if port_part.isdigit():
            port = int(port_part)
            if not (1 <= port <= 65535):
                return "", default_port, f"端口 {port} 超出范围（1-65535）"
            host_part = host_part.strip()
            if not host_part:
                return "", default_port, "缺少 IP 地址"
            return host_part, port, ""
        # 「:28080」不是数字 → 当成没写端口，让用户自己发现
        return raw, default_port, ""
    if raw.count(":") > 1 and "]" not in raw:
        # IPv6 字面量：本项目只支持 IPv4 局域网，明确告诉玩家
        return "", default_port, "看起来是 IPv6 地址，本游戏目前只支持 IPv4（192.168.x.x）"
    return raw, default_port, ""


def test_connection(host: str, port: int, timeout: float = 3.5,
                    version: str = APP_VERSION) -> TestResult:
    """真正建立一次 TCP 连接并握手，把失败原因分类。

    这是同步阻塞调用（最长 timeout 秒），调用方应放在后台线程里跑。
    """
    host = (host or "").strip()
    port = int(port or DEFAULT_PORT)
    result = TestResult(host=host, port=port)
    started = time.time()

    if not host:
        result.status = TEST_DNS
        result.message = "没有填写 IP 地址"
        result.advice = TEST_ADVICE[TEST_DNS]
        return result

    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except socket.gaierror as exc:
        result.status = TEST_DNS
        result.message = f"无法解析「{host}」"
        result.detail = str(exc)
    except socket.timeout:
        result.status = TEST_TIMEOUT
        result.message = f"{host}:{port} 在 {int(timeout)} 秒内没有响应"
    except ConnectionRefusedError:
        result.status = TEST_REFUSED
        result.message = f"{host} 上这个端口拒绝了连接"
    except OSError as exc:
        result.status = _classify_oserror(exc, host)
        result.message = f"{exc.strerror or exc}"
        result.detail = f"errno={getattr(exc, 'errno', '?')}"
    else:
        result.elapsed_ms = int((time.time() - started) * 1000)
        try:
            _handshake(sock, result, version, timeout)
        finally:
            try:
                sock.close()
            except OSError:
                pass
    if not result.elapsed_ms:
        result.elapsed_ms = int((time.time() - started) * 1000)
    if not result.advice:
        result.advice = TEST_ADVICE.get(result.status, TEST_ADVICE[TEST_UNKNOWN])
    return result


def _classify_oserror(exc: OSError, host: str) -> str:
    err = getattr(exc, "errno", None)
    winerr = getattr(exc, "winerror", None)
    if winerr in (10060, 10061):
        return TEST_TIMEOUT if winerr == 10060 else TEST_REFUSED
    if winerr in (10051, 10065, 10013):
        return TEST_UNREACHABLE
    if winerr in (11001, 11004):
        return TEST_DNS
    if err in (errno.ETIMEDOUT, errno.EHOSTUNREACH):
        return TEST_TIMEOUT if err == errno.ETIMEDOUT else TEST_UNREACHABLE
    if err == errno.ECONNREFUSED:
        return TEST_REFUSED
    if err in (errno.EHOSTDOWN, errno.ENETUNREACH):
        return TEST_UNREACHABLE
    if not host:
        return TEST_DNS
    return TEST_UNKNOWN


def _handshake(sock: socket.socket, result: TestResult, version: str,
               timeout: float) -> None:
    """TCP 连上了：用一次轻量握手判断「对面到底是不是 Richman 房间」。"""
    proto_version = getattr(proto, "PROTOCOL_VERSION", 1)
    payload = {
        "protocol_version": proto_version,
        "type": getattr(proto.MessageType, "HELLO", "HELLO"),
        "seq": 1,
        "app_version": version,
        "client": "richman-diagnostic",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock.settimeout(timeout)
    try:
        sock.sendall(struct.pack(">I", len(body)) + body)
        probe = json.dumps({
            "protocol_version": proto_version,
            "type": getattr(proto.MessageType, "PROBE", "PROBE"),
            "seq": 2,
            "app_version": version,
        }, ensure_ascii=False).encode("utf-8")
        sock.sendall(struct.pack(">I", len(probe)) + probe)
        doc = _read_message(sock)
    except socket.timeout:
        result.status = TEST_PROTOCOL
        result.message = "端口通了，但对方没有回应 Richman 握手"
        return
    except (ConnectionResetError, BrokenPipeError):
        result.status = TEST_PROTOCOL
        result.message = "对方在握手过程中断开了连接"
        return
    except OSError as exc:
        result.status = TEST_PROTOCOL
        result.message = f"握手失败：{exc}"
        return

    if doc is None:
        result.status = TEST_PROTOCOL
        result.message = "端口被占用，但对方不是 Richman 房间"
        return

    mtype = str(doc.get("type", ""))
    peer_protocol = doc.get("protocol_version")
    peer_version = str(doc.get("app_version", "") or "")
    result.detail = f"对方版本：{peer_version or '未知'}（协议 {peer_protocol}）"

    if mtype == "VERSION_MISMATCH":
        result.status = TEST_VERSION
        result.message = doc.get("message") or "版本不一致"
        result.room = {"app_version": peer_version}
        return
    if peer_protocol is not None and int(peer_protocol) != int(proto_version):
        result.status = TEST_VERSION
        result.message = (f"协议不一致：本机 {proto_version}，对方 {peer_protocol}")
        return

    if mtype in ("PROBE_RESULT", "WELCOME"):
        room = doc.get("room") if isinstance(doc.get("room"), dict) else doc
        phase = str(room.get("phase", "lobby"))
        players = int(room.get("players", 0) or 0)
        max_players = int(room.get("max_players", 6) or 6)
        result.room = {
            "room_name": room.get("room_name", ""),
            "players": players,
            "max_players": max_players,
            "phase": phase,
            "map_name": room.get("map_name", ""),
            "preset_name": room.get("preset_name", ""),
            "app_version": peer_version,
        }
        if phase != "lobby":
            result.status = TEST_STARTED
            result.message = f"找到房间「{room.get('room_name', '')}」，但这一局已经开始了"
        elif players >= max_players:
            result.status = TEST_FULL
            result.message = f"找到房间「{room.get('room_name', '')}」，但已经人满（{players}/{max_players}）"
        else:
            result.status = TEST_OK
            result.message = (f"找到房间「{room.get('room_name', '')}」"
                              f"（{players}/{max_players} 人，可加入）")
        return

    result.status = TEST_PROTOCOL
    result.message = f"对面回了非预期的消息：{mtype or '空'}"


def _read_message(sock: socket.socket) -> dict[str, Any] | None:
    """读一条长度前缀消息（尽力而为，失败返回 None）。"""
    header = _recv_exact(sock, 4)
    if not header:
        return None
    (length,) = struct.unpack(">I", header)
    if length <= 0 or length > 16 * 1024 * 1024:
        return None
    body = _recv_exact(sock, length)
    if not body:
        return None
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)


class ConnectionTester:
    """后台线程版连接测试：UI 每帧检查 finished/result，不阻塞主循环。"""

    def __init__(self, host: str, port: int, timeout: float = 3.5) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.result: TestResult | None = None
        self.finished = False
        self.started_at = time.time()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="conn-test", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.result = test_connection(self.host, self.port, self.timeout)
        except Exception as exc:      # pragma: no cover - 防御性
            log.exception("连接测试异常")
            self.result = TestResult(status=TEST_UNKNOWN, message=str(exc),
                                     host=self.host, port=self.port,
                                     advice=TEST_ADVICE[TEST_UNKNOWN])
        self.finished = True

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


# ==================================================================== 诊断包

#: 导出时只保留这些日志行（其余丢弃，避免夹带无关信息）
_LOG_KEYWORDS = ("network", "host", "client", "discovery", "reconnect", "transport",
                 "protocol", "lobby")


def _sanitize(text: str, replacements: dict[str, str]) -> str:
    """把用户名 / 绝对路径等敏感内容抹掉。"""
    out = text
    for key, value in replacements.items():
        if key:
            out = out.replace(key, value)
    return out


def build_bundle_dict(app: Any = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造诊断包里的「结构化信息」部分。不含任何玩家隐私。"""
    report = collect(app)
    home = os.path.expanduser("~")
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    data: dict[str, Any] = {
        "app_version": APP_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "os": {
            "platform": platform.platform(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable_kind": "frozen" if getattr(sys, "frozen", False) else "source",
        },
        "network": report.to_dict(),
        "role": report.role or "idle",
        "recent_connection": {
            "text": report.last_connection,
            "ok": report.last_connection_ok,
        },
        "open_errors": [],
    }

    host = getattr(app, "host", None)
    client = getattr(app, "client", None)
    if host is not None:
        data["host_room"] = {
            "room_name": host.lobby.room_name,
            "phase": host.lobby.phase,
            "players": host.lobby.player_count,
            "max_players": host.lobby.max_players,
            "port": host.port,
            "connections": [c.stats for c in list(getattr(host, "_conns", []))],
        }
        data["open_errors"] = list(getattr(host, "errors", []))[-20:]
    if client is not None:
        data["client"] = {
            "connected": client.connected,
            "revision": client.revision,
            "latency_ms": round(float(client.latency_ms or 0), 1),
            "last_error": client.last_error,
            "kicked": client.kicked,
            "disconnected_reason": client.disconnected_reason,
        }
    reconnector = getattr(app, "reconnector", None)
    if reconnector is not None:
        data["reconnect"] = {
            "status": reconnector.status,
            "attempts": reconnector.attempts,
            "elapsed": round(reconnector.elapsed, 1),
            "last_error": reconnector.last_error,
        }
    if extra:
        data.update(extra)

    # 脱敏：把用户名与家目录路径替换掉
    replacements = {
        home: "<用户目录>",
        user_name: "<用户>" if user_name and len(user_name) > 2 else "",
    }
    if replacements.get(user_name) == "":
        replacements.pop(user_name, None)
    return _deep_sanitize(data, replacements)


def _deep_sanitize(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _sanitize(value, replacements)
    if isinstance(value, dict):
        return {k: _deep_sanitize(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_sanitize(v, replacements) for v in value]
    return value


def export_bundle(path: str, app: Any = None, *, log_path: str = "",
                  extra: dict[str, Any] | None = None) -> str:
    """生成诊断 zip（结构化信息 + 最近的网络日志）。返回实际写入的路径。"""
    doc = build_bundle_dict(app, extra)
    home = os.path.expanduser("~")
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    replacements = {home: "<用户目录>"}
    if user_name and len(user_name) > 2:
        replacements[user_name] = "<用户>"

    lines: list[str] = []
    if log_path and os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-4000:]
            for line in tail:
                low = line.lower()
                if any(k in low for k in _LOG_KEYWORDS):
                    lines.append(_sanitize(line.rstrip("\n"), replacements))
            doc["log_lines_kept"] = len(lines)
        except OSError as exc:
            doc["log_read_error"] = str(exc)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("network-report.json",
                    json.dumps(doc, ensure_ascii=False, indent=2))
        zf.writestr("network-log.txt",
                    "\n".join(lines) if lines
                    else "（本次运行没有可导出的网络日志）")
        zf.writestr("HOW-TO-SEND.txt", (
            "Richman 联机诊断包\n"
            "==================\n"
            "这个文件里只有网络状态与最近几条网络日志，\n"
            "不包含存档、昵称、聊天内容或系统用户名。\n\n"
            "把它整个发给开发者即可。\n"))
    return path


def bundle_filename(when: float | None = None) -> str:
    """Richman-network-diagnostic-YYYYMMDD-HHMM.zip"""
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(when or time.time()))
    return f"Richman-network-diagnostic-{stamp}.zip"
