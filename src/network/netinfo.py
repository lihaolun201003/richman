"""网卡枚举：把「本机有哪些局域网地址」讲成玩家能看懂的话。

为什么需要这一层：
`socket.gethostname()` + `getaddrinfo` 只能吐出**一堆裸 IP**，
既不知道哪个是 WiFi、哪个是 VirtualBox，也不知道该把哪个发给室友。
真实玩家宿舍里常见 3～5 个网卡（WiFi / 以太网 / 虚拟网卡 / 蓝牙 / VPN），
随便挑一个然后「假装它一定对」，是联机失败最常见的根因。

这里用 Windows 自带的 `iphlpapi.GetAdaptersAddresses`（ctypes 直接调用，
不依赖第三方库，打包后同样可用）拿到网卡名称、类型与状态，
再按「能不能被室友访问到」的概率排序，并明确标注虚拟网卡。

非 Windows 或调用失败时回落到 socket 探测，功能不缺失，只是信息少一些。
"""
from __future__ import annotations

import ctypes
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

#: 网卡类型（自己归类，不用 Windows 的 IF_TYPE 原值，方便展示）
KIND_WIFI = "wifi"
KIND_ETHERNET = "ethernet"
KIND_VIRTUAL = "virtual"
KIND_LOOPBACK = "loopback"
KIND_OTHER = "other"

KIND_LABELS = {
    KIND_WIFI: "Wi-Fi（无线）",
    KIND_ETHERNET: "以太网（有线）",
    KIND_VIRTUAL: "虚拟网卡",
    KIND_LOOPBACK: "本机回环",
    KIND_OTHER: "其它网络",
}

#: 排序权重：数字越小越靠前（越可能被室友访问到）
_KIND_ORDER = {
    KIND_ETHERNET: 0,
    KIND_WIFI: 1,
    KIND_OTHER: 2,
    KIND_VIRTUAL: 3,
    KIND_LOOPBACK: 4,
}

#: 名字里出现这些词 → 判定为虚拟网卡
_VIRTUAL_HINTS = (
    "virtualbox", "vmware", "hyper-v", "hyperv", "vethernet", "virtual",
    "loopback", "tap-", "tap0", "npcap", "docker", "wsl", "zerotier",
    "hamachi", "radmin", "openvpn", "tailscale", "vpn", "tunnel",
    "bluetooth", "蓝牙", "npf", "pseudo", "teredo", "isatap",
)

#: 名字里出现这些词 → 判定为无线
_WIFI_HINTS = ("wi-fi", "wifi", "wireless", "wlan", "无线", "802.11", "本地连接*")

#: 名字里出现这些词 → 判定为有线
_ETH_HINTS = ("ethernet", "以太网", "本地连接", "realtek", "intel(r) i2", "gbe")


@dataclass
class Adapter:
    """一块网卡（或一个可用的本机地址）。"""

    ip: str
    name: str = ""
    description: str = ""
    kind: str = KIND_OTHER
    is_up: bool = True
    source: str = "socket"          # ctypes / socket
    extra_ips: list[str] = field(default_factory=list)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS[KIND_OTHER])

    @property
    def is_virtual(self) -> bool:
        return self.kind == KIND_VIRTUAL

    @property
    def is_loopback(self) -> bool:
        return self.ip.startswith("127.")

    @property
    def is_link_local(self) -> bool:
        """169.254.x.x：DHCP 失败时的自动地址，室友连不上。"""
        return self.ip.startswith("169.254.")

    @property
    def is_private_lan(self) -> bool:
        """常见家用/校园局域网私有网段。"""
        parts = self.ip.split(".")
        if len(parts) != 4:
            return False
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        return False

    @property
    def display(self) -> str:
        """玩家看到的一行：网卡名 + 地址。"""
        if self.name:
            return f"{self.name}　{self.ip}"
        return self.ip

    #: 排序键：真实网卡 > 虚拟网卡；然后看网卡通不通、地址像不像局域网、类型。
    #: 顺序很要紧（实测踩过两次）：
    #: - 插着网线但没连上的以太网卡，不该排在正在用的 WiFi 前面；
    #: - 但「VirtualBox 已启用 + 以太网未连接」时，也**不能**把 VirtualBox 排前面 ——
    #:   它的 192.168.56.1 看起来很像局域网地址，室友却永远连不上。
    def sort_key(self) -> tuple[int, int, int, int, str]:
        return (
            1 if self.is_virtual else 0,
            0 if self.is_up else 1,
            0 if self.is_private_lan else 1,
            _KIND_ORDER.get(self.kind, 5),
            self.name or self.ip,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "name": self.name,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "is_virtual": self.is_virtual,
            "is_up": self.is_up,
            "is_private_lan": self.is_private_lan,
        }


def classify(name: str, description: str, if_type: int) -> str:
    """把网卡名字/描述/类型翻译成我们的分类。

    先看名字里有没有虚拟网卡特征词（最可靠），再看 Windows 的 IF_TYPE。
    顺序很重要：VirtualBox 的网卡 IF_TYPE 也是 6（以太网），只有名字能区分。
    """
    text = f"{name} {description}".lower()
    if any(h in text for h in _VIRTUAL_HINTS):
        return KIND_VIRTUAL
    if any(h in text for h in _WIFI_HINTS):
        return KIND_WIFI
    if if_type == 71:          # IF_TYPE_IEEE80211
        return KIND_WIFI
    if if_type == 24:          # IF_TYPE_SOFTWARE_LOOPBACK
        return KIND_LOOPBACK
    if any(h in text for h in _ETH_HINTS):
        return KIND_ETHERNET
    if if_type == 6:
        return KIND_ETHERNET
    if if_type in (23, 131, 144):    # PPP / TUNNEL / IEEE1394
        return KIND_VIRTUAL
    return KIND_OTHER


# ----------------------------------------------------------------- Windows 枚举

_AF_UNSPEC = 0
_GAA_FLAG_SKIP_ANYCAST = 0x0002
_GAA_FLAG_SKIP_MULTICAST = 0x0004
_GAA_FLAG_SKIP_DNS_SERVER = 0x0008
_IF_OPER_STATUS_UP = 1


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong),
    ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong),
    ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int),
]


def _sockaddr_to_ip(ptr: int) -> str:
    """从 sockaddr 指针取出 IPv4 字符串（非 v4 返回空串）。"""
    if not ptr:
        return ""
    family = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort)).contents.value
    if family != socket.AF_INET:
        return ""
    raw = ctypes.string_at(ptr + 4, 4)
    return ".".join(str(b) for b in raw)


def windows_adapters(timeout_ms: int = 400) -> list[Adapter]:
    """用 iphlpapi 枚举带地址的网卡。失败返回空列表（由调用方回落）。"""
    if sys.platform != "win32":
        return []
    try:
        iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return []

    flags = (_GAA_FLAG_SKIP_ANYCAST | _GAA_FLAG_SKIP_MULTICAST
             | _GAA_FLAG_SKIP_DNS_SERVER)
    size = ctypes.c_ulong(16 * 1024)
    buf = None
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetAdaptersAddresses(
            ctypes.c_ulong(_AF_UNSPEC), ctypes.c_ulong(flags), None,
            ctypes.byref(buf), ctypes.byref(size))
        if ret == 0:              # NO_ERROR
            break
        if ret == 111:            # ERROR_BUFFER_OVERFLOW：size 已更新，重试
            continue
        return []
    else:
        return []

    out: list[Adapter] = []
    node = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while node:
        item = node.contents
        name = item.FriendlyName or ""
        desc = item.Description or ""
        kind = classify(name, desc, int(item.IfType))
        up = int(item.OperStatus) == _IF_OPER_STATUS_UP
        ips: list[str] = []
        uni = item.FirstUnicastAddress
        while uni:
            ip = _sockaddr_to_ip(uni.contents.Address.lpSockaddr or 0)
            if ip:
                ips.append(ip)
            uni = uni.contents.Next
        if ips:
            out.append(Adapter(ip=ips[0], name=name or desc, description=desc,
                               kind=kind, is_up=up, source="ctypes",
                               extra_ips=ips[1:]))
        node = item.Next
    return out


# ----------------------------------------------------------------- 对外接口

def _socket_fallback() -> list[Adapter]:
    """不依赖系统 API 的兜底：解析主机名 + UDP 探测默认出口。"""
    out: list[Adapter] = []
    seen: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip in seen or ip.startswith("127."):
                continue
            seen.add(ip)
            out.append(Adapter(ip=ip, name="", kind=KIND_OTHER, source="socket"))
    except OSError:
        pass
    if not out:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            probe.close()
            if ip and not ip.startswith("127."):
                seen.add(ip)
                out.append(Adapter(ip=ip, name="", kind=KIND_OTHER, source="socket"))
        except OSError:
            pass
    return out


def list_adapters(include_loopback: bool = False) -> list[Adapter]:
    """返回排序后的可用网卡列表（最可能可用的在最前）。

    排序原则：有线 > 无线 > 其它 > 虚拟 > 回环；
    同一类型里私有网段靠前，之后按名称稳定排序。
    """
    adapters = windows_adapters() or _socket_fallback()
    if not adapters:
        return [Adapter(ip="127.0.0.1", name="本机回环", kind=KIND_LOOPBACK)]

    if include_loopback:
        adapters.append(Adapter(ip="127.0.0.1", name="本机回环", kind=KIND_LOOPBACK))
    else:
        adapters = [a for a in adapters if not a.is_loopback] or [
            Adapter(ip="127.0.0.1", name="本机回环", kind=KIND_LOOPBACK)]

    # 同一个 IP 只保留一条
    uniq: dict[str, Adapter] = {}
    for a in adapters:
        uniq.setdefault(a.ip, a)
    return sorted(uniq.values(), key=lambda a: a.sort_key())


def lan_endpoints() -> list[Adapter]:
    """给「建房 / 诊断」用的地址列表：排除回环，但保留 169.254 以便提示玩家。"""
    return [a for a in list_adapters() if not a.is_loopback]


def best_ip() -> str:
    """最适合发给室友的地址（找不到就用回环，并让上层提示）。"""
    for a in list_adapters():
        if not a.is_loopback and not a.is_virtual and not a.is_link_local:
            return a.ip
    for a in list_adapters():
        if not a.is_loopback and not a.is_link_local:
            return a.ip
    return "127.0.0.1"


def local_ips(include_loopback: bool = False) -> list[str]:
    """兼容旧接口：只要 IP 字符串列表（顺序即推荐顺序）。"""
    return [a.ip for a in list_adapters(include_loopback=include_loopback)]


def network_kind() -> str:
    """本机当前联网方式（用于诊断页「当前网络」一行）。"""
    adapters = [a for a in list_adapters() if a.is_up and not a.is_loopback]
    if not adapters:
        return "未检测到可用网络"
    kinds = {a.kind for a in adapters if not a.is_virtual}
    if KIND_ETHERNET in kinds and KIND_WIFI in kinds:
        return "以太网 + Wi-Fi"
    if KIND_ETHERNET in kinds:
        return "以太网（有线）"
    if KIND_WIFI in kinds:
        return "Wi-Fi（无线）"
    if KIND_VIRTUAL in kinds:
        return "仅检测到虚拟网卡"
    return "未知"


def describe_address(ip: str) -> tuple[str, str]:
    """对单个地址给一句人话 + 严重级别（success / warning / danger / text）。

    诊断页的判断全部走这里，保证「结论」和「建议」口径一致。
    """
    if not ip:
        return "没有检测到局域网地址", "warning"
    if ip.startswith("127."):
        return "这个地址只能本机访问，请不要发给室友", "danger"
    if ip.startswith("169.254."):
        return "当前可能没有正常获得局域网地址（DHCP 失败）", "danger"
    if ip.startswith("192.168.56.") or ip.startswith("10.211.55."):
        return "这看起来是虚拟网卡地址，室友多半连不上", "warning"
    parts = ip.split(".")
    if len(parts) == 4:
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            return "", "text"
        if a == 192 and b == 168:
            return "标准的家用 / 校园局域网地址，适合发给室友", "success"
        if a == 10 or (a == 172 and 16 <= b <= 31):
            return "私有局域网地址，通常可以发给室友", "success"
    return "公网或特殊地址，请确认两台电脑在同一个局域网", "warning"
