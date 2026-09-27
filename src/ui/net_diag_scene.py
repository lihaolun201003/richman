"""局域网诊断场景：给玩家一个「连不上时先看这里」的自检页。

诚实原则（重要）：
- 只显示**真的能拿到**的东西：本机 IP、监听端口、发现服务状态、协议版本；
- **不假装检测 Windows 防火墙**——那需要管理员权限与平台 API，
  检测不出来就不要给玩家一个假的「防火墙正常」；
- 每一条都给可执行的下一步动作。
"""
from __future__ import annotations

import socket
from typing import Any

import pygame

from ..network import protocol as proto
from ..network.transport import local_ip_addresses, primary_ip
from ..utils.clipboard import copy_text
from . import icons, theme
from .scene import Scene
from .widgets import Button, draw_tooltip

DISCOVERY_PORT = 28081


def _discovery_port_state() -> tuple[bool, str]:
    """尝试绑定发现端口，判断自动发现是否可用（绑不上说明被占用）。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.close()
        return True, "可用"
    except OSError as exc:
        return False, f"不可用（{exc.strerror or exc}）"


def _tcp_port_state(port: int) -> tuple[bool, str]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.close()
        return True, "端口空闲，可以建房"
    except OSError as exc:
        return False, f"端口被占用（{exc.strerror or exc}），建房时会自动改用其它端口"


class NetDiagScene(Scene):
    """局域网联机诊断。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.back_target = "menu"
        self.lines: list[tuple[str, str, str]] = []
        self.actions: list[str] = []
        self.widgets = [
            Button(pygame.Rect(140, 790, 240, 54), "返回", on_click=self._back,
                   style="secondary", icon="exit"),
            Button(pygame.Rect(400, 790, 280, 54), "复制本机 IP", on_click=self._copy_ip,
                   style="ghost", icon="copy"),
            Button(pygame.Rect(700, 790, 280, 54), "重新检测", on_click=self.refresh,
                   style="secondary", icon="refresh"),
        ]

    def on_enter(self, **kwargs: Any) -> None:
        self.back_target = kwargs.get("back", "menu")
        self.widgets[0].on_click = self._back
        self.refresh()

    def _back(self) -> None:
        self.app.scenes.switch_to(self.back_target)

    def _copy_ip(self) -> None:
        ip = primary_ip()
        if copy_text(ip):
            self.notify(f"已复制 {ip}", "success")
        else:
            self.notify(f"复制失败，请手动记下：{ip}", "warning")

    # ------------------------------------------------------------ 检测

    def refresh(self) -> None:
        ips = local_ip_addresses()
        host = self.app.host
        client = self.app.client

        if host is not None:
            server_state = f"运行中（端口 {host.port}，{host.lobby.player_count} 人）"
        elif client is not None:
            server_state = f"连接到 {client.host_address}"
        else:
            server_state = "未开启（回到主菜单点「创建局域网房间」）"

        discovery_ok, discovery_text = _discovery_port_state()
        tcp_ok, tcp_text = (True, "运行中") if host is not None else _tcp_port_state(28080)

        self.lines = [
            ("network", "本机局域网 IP", "　".join(ips) if ips else "未检测到",
             "success" if ips else "warning"),
            ("server", "服务器状态", server_state,
             "success" if host is not None else "text_dim"),
            ("network", "发现服务（UDP 28081）", discovery_text,
             "success" if discovery_ok else "warning"),
            ("network", "TCP 28080", tcp_text, "success" if tcp_ok else "warning"),
            ("info", "协议版本", str(proto.PROTOCOL_VERSION),
             "text"),
            ("info", "游戏版本", self._version_text(), "text"),
        ]

        self.actions = [
            "确认两台电脑连的是同一个 WiFi / 局域网（手机热点常见「AP 隔离」会阻止互访）。",
            "核对 IP 前三段是否一致：例如两边都是 192.168.1.x。",
            "Windows 防火墙首次会弹窗，必须勾选「专用网络」并允许；"
            "误点了取消就在管理员 PowerShell 里放行 TCP 28080 与 UDP 28081。",
            "搜不到房间不影响联机：让房主在大厅里点「复制连接信息」，"
            "把 IP:端口发给你，手动填进「加入房间」即可。",
            "仍然连不上时，先确认房主大厅里的端口号（被占用时会自动改端口）。",
        ]

    def _version_text(self) -> str:
        from ..version import APP_VERSION

        return APP_VERSION

    # ------------------------------------------------------------ 事件

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()
            return
        super().handle_event(event)

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.page_title(surface, self.fonts, "局域网联机诊断",
                         "连不上房间时先看这一页：它是按当前这台电脑的真实状态实时检测的")

        left = pygame.Rect(140, 200, 660, 540)
        theme.panel(surface, left, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(left.x + 20, left.y + 16, left.width - 40, 22),
                             "本机状态", icon="network")
        y = left.y + 60
        for icon_name, label, value, color_name in self.lines:
            icons.draw_icon(surface, icon_name,
                            pygame.Rect(left.x + 22, y + 3, 18, 18),
                            theme.color(color_name), theme.color("shadow"))
            theme.draw_text(surface, label, self.fonts.body(), theme.color("text_dim"),
                            (left.x + 50, y))
            theme.draw_text(surface, theme.truncate(value, self.fonts.body(),
                                                    left.width - 240),
                            self.fonts.body(), theme.color(color_name),
                            (left.right - 22, y), anchor="topright")
            y += 34
            pygame.draw.line(surface, theme.color("border_soft"),
                             (left.x + 22, y - 8), (left.right - 22, y - 8), 1)
        y += 6
        theme.draw_text(surface,
                        "说明：本页不会假装检测防火墙——那需要管理员权限，"
                        "检测不出来就不给出结论。",
                        self.fonts.micro(), theme.color("text_mute"),
                        (left.x + 22, left.bottom - 46))

        right = pygame.Rect(840, 200, 660, 540)
        theme.panel(surface, right, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(right.x + 20, right.y + 16, right.width - 40, 22),
                             "按顺序排查", icon="help")
        y = right.y + 60
        for i, text in enumerate(self.actions, start=1):
            badge = pygame.Rect(right.x + 22, y + 1, 22, 22)
            pygame.draw.circle(surface, theme.color("accent"), badge.center, 11)
            theme.draw_text(surface, str(i), self.fonts.micro(), theme.color("text_dark"),
                            badge.center, anchor="center")
            lines = theme.wrap_text(text, self.fonts.small(), right.width - 90)
            for line in lines:
                theme.draw_text(surface, line, self.fonts.small(),
                                theme.color("text_dim"), (right.x + 56, y))
                y += 22
            y += 14

        self.draw_widgets(surface)
