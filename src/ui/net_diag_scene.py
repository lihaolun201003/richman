"""联机诊断中心：给玩家一个「连不上时先看这里」的自检页。

设计原则（v0.4 重写）：

1. **只显示真的能拿到的东西**。本页不会假装检测 Windows 防火墙——
   那需要管理员权限与平台 API，检测不出来就不给结论，只给「怎么试」。
2. **结论按当前状态给，不是固定清单**。地址是 127.0.0.1、是 169.254.x.x、
   有多个网卡、UDP 搜不到——每种情况给的下一步都不一样。
3. **能动手就动手**：内置「测试连接」（真的建 TCP、真的握手、失败分类），
   以及「导出联机诊断」（一键生成脱敏 zip 发给开发者）。
4. **不使用开发术语**：协议版本号这类字段只作为附带信息出现，
   主位留给「当前网络 / 推荐地址 / 下一步怎么办」。
"""
from __future__ import annotations

import os
import time
from typing import Any

import pygame

from ..network import diagnose, netinfo
from ..network.transport import local_ip_addresses, primary_ip
from ..utils.clipboard import copy_text
from ..utils.paths import ensure_dir, logs_path, project_root
from . import icons, theme
from .scene import Scene
from .widgets import Button, TextInput, draw_tooltip

LEFT_X = 120
LEFT_W = 660
RIGHT_X = 820
RIGHT_W = 660

PANEL_TOP = 196
NETWORK_H = 366
SERVICE_Y = PANEL_TOP + NETWORK_H + 16
SERVICE_H = 206

ADVICE_H = 330
TEST_Y = PANEL_TOP + ADVICE_H + 16
TEST_H = SERVICE_Y + SERVICE_H - TEST_Y

BTN_Y = 812


class NetDiagScene(Scene):
    """联机诊断：本机状态 + 下一步怎么办 + 测试连接 + 导出诊断包。"""

    #: 端口类检测的自动重检间隔（秒）——不能每帧 bind socket
    REFRESH_INTERVAL = 2.5

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.back_target = "menu"
        self.report: diagnose.NetworkReport = diagnose.NetworkReport()
        self.tester: diagnose.ConnectionTester | None = None
        self.last_test: diagnose.TestResult | None = None
        self._refresh_at = 0.0
        self._export_path = ""
        self._build()

    def _build(self) -> None:
        self.target_input = TextInput(
            pygame.Rect(RIGHT_X + 16, TEST_Y + 96, 400, 46),
            self.app.settings.last_host if self._looks_like_ip(
                self.app.settings.last_host) else "",
            placeholder="例：192.168.1.5:28080", max_length=48)
        self.test_button = Button(
            pygame.Rect(RIGHT_X + RIGHT_W - 200, TEST_Y + 96, 184, 46),
            "测试连接", on_click=self._run_test, style="accent",
            font_size=16, icon="bolt")
        self.copy_button = Button(
            pygame.Rect(LEFT_X + LEFT_W - 190, PANEL_TOP + 106, 174, 40),
            "复制这个地址", on_click=self._copy_ip, style="ghost",
            font_size=14, icon="copy")

        self.widgets = [
            self.target_input, self.test_button, self.copy_button,
            Button(pygame.Rect(LEFT_X, BTN_Y, 220, 54), "返回",
                   on_click=self._back, style="secondary", icon="exit"),
            Button(pygame.Rect(LEFT_X + 244, BTN_Y, 300, 54), "导出联机诊断",
                   on_click=self._export, style="primary", icon="save",
                   tooltip="生成一个不含隐私信息的 zip，出问题时发给开发者"),
            Button(pygame.Rect(LEFT_X + 568, BTN_Y, 220, 54), "重新检测",
                   on_click=self.refresh, style="ghost", icon="refresh"),
        ]

    @staticmethod
    def _looks_like_ip(text: str) -> bool:
        return bool(text) and text[0].isdigit() and "." in text

    # ------------------------------------------------------------ 生命周期

    def on_enter(self, **kwargs: Any) -> None:
        self.back_target = kwargs.get("back", "menu")
        self.refresh()
        # 预填上次连过的房间地址，省掉玩家回忆
        host = self.app.settings.last_host
        port = self.app.settings.last_port
        if self._looks_like_ip(host):
            self.target_input.set_text(f"{host}:{port}" if port else host)

    def on_exit(self) -> None:
        self.tester = None

    def _back(self) -> None:
        self.app.scenes.switch_to(self.back_target)

    def _copy_ip(self) -> None:
        ip = self.report.primary_ip or primary_ip()
        if copy_text(ip):
            self.notify(f"已复制 {ip}", "success")
        else:
            self.notify(f"复制失败，请手动记下：{ip}", "warning")

    def refresh(self) -> None:
        self.report = diagnose.collect(self.app)
        self._refresh_at = time.time()

    # ------------------------------------------------------------ 连接测试

    def _run_test(self) -> None:
        raw = (self.target_input.text or "").strip()
        host, port, error = diagnose.parse_target(raw, self.app.settings.last_port)
        if error:
            self.last_test = diagnose.TestResult(
                status=diagnose.TEST_UNKNOWN, message=error,
                advice=["请填写房主大厅里显示的地址，例如 192.168.1.5:28080"])
            self.notify(error, "warning")
            return
        self.last_test = None
        self.tester = diagnose.ConnectionTester(host, port)
        self.tester.start()
        self.app.settings.remember_server(host, port)
        self.notify(f"正在连接 {host}:{port} …", "info")

    def _poll_test(self) -> None:
        tester = self.tester
        if tester is None or not tester.finished:
            return
        self.tester = None
        self.last_test = tester.result
        if self.last_test is None:
            return
        self._record_connection_result(self.last_test)
        kind = "success" if self.last_test.ok else "warning"
        self.notify(f"{self.last_test.label}：{self.last_test.message}", kind)

    def _record_connection_result(self, result: diagnose.TestResult) -> None:
        """把测试结果记到 app 上，大厅/加入页也能显示「上次连接」的结论。"""
        self.app.last_connection_result = {
            "text": f"{result.label}：{result.message}",
            "ok": result.ok,
            "status": result.status,
            "host": result.host,
            "port": result.port,
        }

    # ------------------------------------------------------------ 导出诊断包

    def _export(self) -> None:
        try:
            out_dir = ensure_dir(os.path.join(project_root(), "diag"))
            path = os.path.join(out_dir, diagnose.bundle_filename())
            log_file = logs_path("richman.log")
            diagnose.export_bundle(path, self.app, log_path=log_file)
        except Exception as exc:
            self.notify(f"导出失败：{exc}", "error")
            return
        self._export_path = path
        name = os.path.basename(path)
        if copy_text(path):
            self.notify(f"已生成 {name}，并复制了完整路径", "success")
        else:
            self.notify(f"已生成 {name}（在 diag 文件夹里）", "success")

    # ------------------------------------------------------------ 事件

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._back()
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._run_test()
                return
        super().handle_event(event)

    def update(self, dt: float) -> None:
        super().update(dt)
        self._poll_test()
        if time.time() - self._refresh_at >= self.REFRESH_INTERVAL and self.tester is None:
            self.refresh()

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.page_title(surface, self.fonts, "联机诊断",
                         "连不上房间时先看这一页：每一项都是按当前这台电脑的真实状态测出来的")

        self._draw_network_panel(surface)
        self._draw_service_panel(surface)
        self._draw_advice_panel(surface)
        self._draw_test_panel(surface)
        self.draw_widgets(surface)

        mouse = pygame.mouse.get_pos()
        for widget in self.widgets:
            tip = getattr(widget, "tooltip", "")
            if tip and widget.rect.collidepoint(mouse):
                draw_tooltip(surface, self.fonts, tip, mouse, bounds=(1600, 900))
                break

    # ---- 本机网络

    def _draw_network_panel(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(LEFT_X, PANEL_TOP, LEFT_W, NETWORK_H)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 18, rect.y + 14, rect.width - 36, 22),
                             "本机网络", icon="network",
                             note=self.report.version)

        report = self.report
        y = rect.y + 52
        # 当前网络
        theme.draw_text(surface, "当前网络", self.fonts.small(), theme.color("text_dim"),
                        (rect.x + 20, y))
        theme.draw_text(surface, report.network_kind or "未知", self.fonts.body(),
                        theme.color("text"), (rect.right - 20, y - 2), anchor="topright")
        y += 30

        physical = [a for a in report.adapters if not a.is_virtual]
        virtual = [a for a in report.adapters if a.is_virtual]

        if not physical:
            theme.draw_text(surface, "没有检测到可用的局域网地址",
                            self.fonts.body(), theme.color("danger"), (rect.x + 20, y))
            y += 32
        else:
            first = physical[0]
            text, level = netinfo.describe_address(first.ip)
            plate = pygame.Rect(rect.x + 18, y, rect.width - 36, 96)
            theme.rounded_rect(surface, plate, theme.color("bg_alt"), radius=theme.RADIUS["lg"])
            theme.rounded_rect(surface, plate, None, radius=theme.RADIUS["lg"],
                               border=theme.color(level if level != "text" else "border_soft"),
                               border_width=2)
            theme.draw_text(surface, "推荐发给室友的地址", self.fonts.tiny(),
                            theme.color("text_dim"), (plate.x + 16, plate.y + 10))
            big = self.fonts.sized(theme.FONT["h1"], True)
            theme.draw_text(surface, first.ip, big, theme.color("accent"),
                            (plate.x + 16, plate.y + 30))
            if first.name:
                theme.draw_text(surface, f"{first.name}　{first.kind_label}",
                                self.fonts.small(), theme.color("text_dim"),
                                (plate.x + 20 + big.size(first.ip)[0], plate.y + 44))
            theme.draw_text(surface, text, self.fonts.tiny(),
                            theme.color(level if level != "text" else "text_dim"),
                            (plate.x + 16, plate.bottom - 22))
            self.copy_button.rect.topleft = (plate.right - 186, plate.y + 28)
            y = plate.bottom + 12

            for extra in physical[1:4]:
                detail, level2 = netinfo.describe_address(extra.ip)
                line = f"{extra.ip}　{extra.name or extra.kind_label}"
                theme.draw_text(surface, theme.truncate(line, self.fonts.small(),
                                                        rect.width - 200),
                                self.fonts.small(), theme.color("text_dim"),
                                (rect.x + 22, y))
                theme.draw_text(surface, "备选" if not extra.is_up else "",
                                self.fonts.tiny(), theme.color("text_mute"),
                                (rect.right - 22, y), anchor="topright")
                y += 24
                if detail and level2 in ("danger", "warning"):
                    theme.draw_text(surface, theme.truncate(detail, self.fonts.tiny(),
                                                            rect.width - 60),
                                    self.fonts.tiny(), theme.color(level2), (rect.x + 34, y))
                    y += 20

        if virtual:
            names = "、".join(sorted({a.name or "虚拟网卡" for a in virtual})[:2])
            more = f" 等 {len(virtual)} 个" if len(virtual) > 2 else ""
            theme.draw_text(
                surface,
                theme.truncate(f"已忽略虚拟网卡：{names}{more}", self.fonts.tiny(),
                               rect.width - 60),
                self.fonts.tiny(), theme.color("text_mute"), (rect.x + 22, y))
            y += 20

        theme.draw_text(surface, "说明：本页不会假装检测防火墙——"
                                 "那需要管理员权限，检测不出来就不给结论。",
                        self.fonts.tiny(), theme.color("text_mute"),
                        (rect.x + 22, rect.bottom - 26))

    # ---- 服务状态

    def _draw_service_panel(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(LEFT_X, SERVICE_Y, LEFT_W, SERVICE_H)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 18, rect.y + 14, rect.width - 36, 22),
                             "服务与连接", icon="server" if icons.has_icon("server")
                             else "rules")

        rows: list[tuple[str, str, str, bool]] = [
            ("服务端端口", self.report.tcp_state, self.report.tcp_ok, False),
            ("房间自动发现", self.report.udp_state, self.report.udp_ok, False),
            ("最近一次连接", self.report.last_connection or "本机还没有连接记录",
             bool(self.report.last_connection_ok), True),
        ]
        y = rect.y + 50
        for label, value, ok, is_text in rows:
            theme.draw_text(surface, label, self.fonts.small(), theme.color("text_dim"),
                            (rect.x + 20, y))
            font = self.fonts.small() if is_text else self.fonts.body()
            max_w = rect.width - 230
            theme.draw_text(surface, theme.truncate(value, font, max_w), font,
                            theme.color("success" if ok else "text"),
                            (rect.right - 20, y - 1), anchor="topright")
            y += 30
        theme.draw_text(surface,
                        "端口被占用时游戏会自动往后找端口，以大厅里显示的数字为准。",
                        self.fonts.tiny(), theme.color("text_mute"),
                        (rect.x + 20, rect.bottom - 26))

    # ---- 下一步怎么办

    def _draw_advice_panel(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(RIGHT_X, PANEL_TOP, RIGHT_W, ADVICE_H)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 18, rect.y + 14, rect.width - 36, 22),
                             "下一步怎么办", icon="help",
                             note="按当前状态生成")

        y = rect.y + 52
        headline = self.report.headline()
        level = "warning" if self.report.warnings else "success"
        box = pygame.Rect(rect.x + 16, y, rect.width - 32, 56)
        theme.rounded_rect(surface, box, theme.color("bg_alt"), radius=theme.RADIUS["md"])
        icons.draw_icon(surface, "alert" if self.report.warnings else "check",
                        pygame.Rect(box.x + 14, box.y + 16, 22, 22),
                        theme.color(level), theme.color("shadow"))
        for i, line in enumerate(theme.wrap_text(headline, self.fonts.small(),
                                                 box.width - 60)[:2]):
            theme.draw_text(surface, line, self.fonts.small(), theme.color(level),
                            (box.x + 48, box.y + 12 + i * 20))
        y = box.bottom + 12

        advice = self.report.advice or ["一切正常：想联机就回主菜单建房或加入房间。"]
        for i, text in enumerate(advice[:4], start=1):
            badge = pygame.Rect(rect.x + 20, y + 2, 20, 20)
            pygame.draw.circle(surface, theme.color("accent"), badge.center, 10)
            theme.draw_text(surface, str(i), self.fonts.micro(), theme.color("text_dark"),
                            badge.center, anchor="center")
            for line in theme.wrap_text(text, self.fonts.small(), rect.width - 80)[:2]:
                theme.draw_text(surface, line, self.fonts.small(),
                                theme.color("text_dim"), (rect.x + 50, y))
                y += 21
            y += 8

    # ---- 测试连接

    def _draw_test_panel(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(RIGHT_X, TEST_Y, RIGHT_W, TEST_H)
        theme.panel(surface, rect, fill="panel", radius=theme.RADIUS["xl"])
        theme.section_header(surface, self.fonts,
                             pygame.Rect(rect.x + 18, rect.y + 14, rect.width - 36, 22),
                             "测试连接", icon="bolt",
                             note="真的连一次，不是猜")
        theme.draw_text(surface,
                        "把房主发的地址粘进来（带不带端口都行），点一下就知道通不通。",
                        self.fonts.small(), theme.color("text_dim"),
                        (rect.x + 18, rect.y + 46))

        # 输入框与按钮的位置在 _build 里算好，这里只画结果
        result_y = rect.y + 156
        tester = self.tester
        if tester is not None:
            self._draw_progress(surface, rect, result_y, tester)
            return
        result = self.last_test
        if result is None:
            theme.draw_text(surface, "还没有测试过。",
                            self.fonts.small(), theme.color("text_mute"),
                            (rect.x + 18, result_y))
            return

        level = "success" if result.ok else {
            diagnose.TEST_REFUSED: "warning",
            diagnose.TEST_TIMEOUT: "danger",
            diagnose.TEST_UNREACHABLE: "warning",
            diagnose.TEST_VERSION: "warning",
            diagnose.TEST_FULL: "warning",
            diagnose.TEST_STARTED: "warning",
            diagnose.TEST_PROTOCOL: "warning",
        }.get(result.status, "danger")
        head = pygame.Rect(rect.x + 16, result_y, rect.width - 32, 44)
        theme.rounded_rect(surface, head, theme.color("bg_alt"), radius=theme.RADIUS["md"])
        theme.rounded_rect(surface, head, None, radius=theme.RADIUS["md"],
                           border=theme.color(level), border_width=2)
        icons.draw_icon(surface, "check" if result.ok else "alert",
                        pygame.Rect(head.x + 12, head.y + 11, 22, 22),
                        theme.color(level), theme.color("shadow"))
        font = self.fonts.sized(theme.FONT["h3"], True)
        theme.draw_text(surface, result.label, font, theme.color(level),
                        (head.x + 44, head.y + 10))
        detail = f"{result.host}:{result.port}　{result.elapsed_ms} ms"
        theme.draw_text(surface, detail, self.fonts.tiny(), theme.color("text_mute"),
                        (head.right - 12, head.y + 15), anchor="topright")

        y = head.bottom + 8
        for line in theme.wrap_text(result.message, self.fonts.small(),
                                    rect.width - 40)[:2]:
            theme.draw_text(surface, line, self.fonts.small(), theme.color("text"),
                            (rect.x + 18, y))
            y += 20
        if result.room.get("room_name"):
            room = result.room
            info = (f"房间「{room.get('room_name')}」　{room.get('players')}/"
                    f"{room.get('max_players')} 人　{room.get('map_name', '')}")
            theme.draw_text(surface, theme.truncate(info, self.fonts.tiny(),
                                                    rect.width - 40),
                            self.fonts.tiny(), theme.color("accent"), (rect.x + 18, y))
            y += 20
        for line in result.advice[:2]:
            for wrapped in theme.wrap_text("· " + line, self.fonts.tiny(),
                                           rect.width - 40)[:2]:
                theme.draw_text(surface, wrapped, self.fonts.tiny(),
                                theme.color("text_dim"), (rect.x + 18, y))
                y += 18

    def _draw_progress(self, surface: pygame.Surface, rect: pygame.Rect, y: int,
                       tester: diagnose.ConnectionTester) -> None:
        theme.draw_text(surface, f"正在连接 {tester.host}:{tester.port} …",
                        self.fonts.body(), theme.color("accent"), (rect.x + 18, y))
        frac = min(1.0, tester.elapsed / max(0.1, tester.timeout))
        theme.progress_bar(surface,
                           pygame.Rect(rect.x + 18, y + 32, rect.width - 36, 8),
                           frac, color_name="accent")
        theme.draw_text(surface, "最多等 4 秒；连不上会告诉你具体是哪种失败。",
                        self.fonts.tiny(), theme.color("text_mute"), (rect.x + 18, y + 48))
