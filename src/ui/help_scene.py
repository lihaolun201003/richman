"""帮助与图鉴：规则说明 + 道具 / 角色 / 地图格 / 片区图鉴。

对第一次上手的室友很关键 —— 不用翻 README 就能看懂规则。
不是解锁系统，纯粹是规则百科。
"""
from __future__ import annotations

from typing import Any

import pygame

from ..game import economy
from ..game.cards import TIMING_LABEL, CardRegistry
from ..game.setup import available_maps, load_characters, load_board
from ..game.tile import TileType
from . import theme
from .scene import Scene
from .widgets import Button

TABS = [
    ("rules", "怎么玩"),
    ("tiles", "地块图鉴"),
    ("cards", "道具图鉴"),
    ("chars", "角色图鉴"),
    ("maps", "地图"),
    ("keys", "快捷键"),
]

TILE_ORDER = [
    TileType.START, TileType.PROPERTY, TileType.STATION, TileType.FORTUNE,
    TileType.DISASTER, TileType.SHOP, TileType.TAX, TileType.JAIL,
    TileType.GO_TO_JAIL, TileType.PARK, TileType.BONUS,
]

TILE_DESC = {
    TileType.START: "起点。经过或停留都能拿到通行奖励（默认 2,000）。游戏从这里开始。",
    TileType.PROPERTY: "普通地产。无主时可以买下；踩到别人的地产要付租金。同一片区的"
                       "地产全部到手后成为「垄断」，该区租金翻倍。",
    TileType.STATION: "交通枢纽。属于高级地产，租金成长比普通地产更快，也是垄断片区的一部分。",
    TileType.FORTUNE: "福运格。抽到的只会是好事：拿钱、前进、获得道具或状态加成。",
    TileType.DISASTER: "灾祸格。抽到的只会是坏事：罚款、后退、被送进看守所等。",
    TileType.SHOP: "商店。随机展示几张道具卡，可以用现金买一张（每人限购一张）。",
    TileType.TAX: "税收格。分为固定金额的营业税和按总资产比例计算的奢侈税。",
    TileType.JAIL: "看守所。只是路过不会有事；被关押的话会停在这里。",
    TileType.GO_TO_JAIL: "治安巡查。踩到会被直接送进看守所。",
    TileType.PARK: "中央公园。停留可以领走城市奖金池里的全部奖金。",
    TileType.BONUS: "奖金池。和中央公园类似，停留即可领取累积奖金。",
}

RULE_SECTIONS: list[tuple[str, list[str]]] = [
    ("目标", [
        "让其他所有玩家破产，成为最后存活的人。",
        "如果 200 轮仍未分出胜负，按总资产（现金 + 地产估值）判定胜者。",
    ]),
    ("一个回合", [
        "① 回合开始：结算状态（跳过回合、保护期等）。",
        "② 掷骰：两个六面骰，结果由 Host 生成。",
        "③ 逐格移动：经过起点拿奖励；撞上路障会被迫停下。",
        "④ 落点结算：买地 / 升级 / 付租 / 抽事件 / 缴税 / 进商店 / 被关押。",
        "⑤ 回合结束：清算欠款，切换到下一位玩家。",
    ]),
    ("买地与垄断", [
        "落到无主地产会弹出购买窗口，价格随片区位置递增。",
        "同一片区的全部地产被你拿到 → 该区租金翻倍。",
        "多花点钱在同一个片区，通常比到处买零散的地更划算。",
    ]),
    ("升级", [
        "自己的地产可以升级，最高 3 级：空地 → 小建筑 → 中型建筑 → 高级建筑。",
        "升级界面会直接显示「当前租金 → 升级后租金」和升级费用，方便算收益。",
        "现金不足时不要硬升，留着钱付租金更重要。",
    ]),
    ("抵押与出售", [
        "抵押：把空地抵押给银行换取现金（地价的 60%），保留产权但不能收租，",
        "　　　之后可以按 1.1 倍赎回。这是资金紧张时最划算的选择。",
        "出售：永久失去这块地，回收已投入资金的 70%（抵押中的地只回收 45%）。",
        "有建筑的地要先拆除建筑（返还一半升级费）才能抵押。",
    ]),
    ("欠钱还不上怎么办", [
        "现金不够时游戏会先尝试自动变卖资产抵债。",
        "如果只需抵押就能付清，系统会把决定权交给你 —— 打开债务处理面板，",
        "自己挑哪块地抵押、哪块地出售，直到凑够欠款。",
        "如果连「抵押 + 变卖」都凑不够，就只能破产退出，资产归属债权人。",
    ]),
    ("看守所", [
        "被送进看守所后，每回合可以二选一：支付保释金立即离开，或掷骰子碰运气",
        "（掷出 6 点以上即可离开）。关押满 3 回合会自动付费释放。",
        "保释券道具可以无代价离开。",
    ]),
    ("道具", [
        "每人最多持有 5 张道具，开局发 2 张，之后可以通过机遇事件或商店获得。",
        "每张道具都有使用时机：掷骰前、掷骰后、任意时刻等。",
        "不能用的道具会灰掉，把鼠标放上去会说明原因。",
        "在棋盘上把鼠标移到「我的道具」上可以看说明，按 1～5 直接使用。",
    ]),
    ("奖金池", [
        "税收的一半会注入城市奖金池，某些事件与卡片也会注入。",
        "踩到中央公园或奖金池格，可以一次性领走池里的全部奖金。",
    ]),
    ("联机", [
        "房主创建房间后，把大厅里显示的 IP 和端口告诉朋友。",
        "朋友在同一局域网内选择「加入房间」，输入该 IP 即可。",
        "只有房主运行游戏规则，客户端只负责显示和发送操作，所以双方永远看到同一局。",
        "掉线后 30 秒内可以重连；超时由 AI 接管，不会卡住整局。",
    ]),
]


class HelpScene(Scene):
    """帮助与图鉴。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.tab = "rules"
        self.scroll = 0.0
        self.max_scroll = 0.0
        self.back_target = "menu"
        self.cards: CardRegistry | None = None
        self.characters: list[dict] = []
        self.icon_cache: dict[str, pygame.Surface] = {}
        self.widgets = [
            Button(pygame.Rect(140, 812, 200, 52), "返回",
                   on_click=self._back, style="secondary"),
        ]
        self.tab_rects: list[tuple[str, pygame.Rect]] = []
        self._load_data()

    def _load_data(self) -> None:
        try:
            from ..game.setup import load_card_registry

            self.cards = load_card_registry()
        except Exception:
            self.cards = None
        try:
            self.characters = load_characters()["characters"]
        except Exception:
            self.characters = []

    # ------------------------------------------------------------ 生命周期

    def on_enter(self, **kwargs: Any) -> None:
        self.back_target = kwargs.get("back", "menu")
        self.scroll = 0.0

    def _back(self) -> None:
        self.app.scenes.switch_to(self.back_target)

    # ------------------------------------------------------------ 输入

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._back()
                return
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0.0, min(self.max_scroll, self.scroll - event.y * 56))
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, rect in self.tab_rects:
                if rect.collidepoint(event.pos):
                    self.tab = key
                    self.scroll = 0.0
                    return
        super().handle_event(event)

    # ------------------------------------------------------------ 绘制

    def draw(self, surface: pygame.Surface) -> None:
        theme.vgradient(surface, pygame.Rect(0, 0, 1600, 900), (26, 36, 54), (18, 24, 36))
        theme.page_title(surface, self.fonts, "规则 · 图鉴",
                         "第一次玩先看「怎么玩」，其余按需查阅", divider_after=False)

        self._draw_tabs(surface)

        body = pygame.Rect(140, 190, 1320, 600)
        theme.rounded_rect(surface, body, theme.color("panel", 150), radius=16)
        clip = surface.get_clip()
        surface.set_clip(body.inflate(-8, -8))
        content = pygame.Rect(body.x + 24, body.y + 20 - int(self.scroll),
                              body.width - 60, body.height)
        drawer = {
            "rules": self._draw_rules,
            "tiles": self._draw_tiles,
            "cards": self._draw_cards,
            "chars": self._draw_characters,
            "maps": self._draw_maps,
            "keys": self._draw_keys,
        }.get(self.tab, self._draw_rules)
        used = drawer(surface, content)
        surface.set_clip(clip)
        self.max_scroll = max(0.0, used - body.height + 40)

        if self.max_scroll > 0:
            bar = pygame.Rect(body.right - 10, body.y + 10, 6, body.height - 20)
            theme.rounded_rect(surface, bar, theme.color("bg_alt"), radius=3)
            ratio = body.height / max(1.0, used + 40)
            handle_h = max(30, int(bar.height * ratio))
            pos = self.scroll / max(1.0, self.max_scroll)
            handle = pygame.Rect(bar.x, bar.y + int((bar.height - handle_h) * pos),
                                 bar.width, handle_h)
            theme.rounded_rect(surface, handle, theme.color("border"), radius=3)

        self.draw_widgets(surface)

    def _draw_tabs(self, surface: pygame.Surface) -> None:
        x = 140
        self.tab_rects = []
        for key, label in TABS:
            font = self.fonts.sized(17, True)
            w = font.size(label)[0] + 40
            rect = pygame.Rect(x, 136, w, 40)
            active = key == self.tab
            theme.rounded_rect(surface, rect,
                               theme.color("accent") if active else theme.color("panel_alt"),
                               radius=10)
            theme.draw_text(surface, label, font,
                            theme.color("text_dark") if active else theme.color("text_dim"),
                            rect.center, anchor="center")
            self.tab_rects.append((key, rect))
            x += w + 10

    # ------------------------------------------------------------ 各页

    def _draw_rules(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        y = rect.y
        for title, lines in RULE_SECTIONS:
            theme.draw_text(surface, title, self.fonts.h2(), theme.color("accent"),
                            (rect.x, y))
            y += 32
            for line in lines:
                theme.draw_text(surface, line, self.fonts.body(), theme.color("text_dim"),
                                (rect.x + 10, y))
                y += 25
            y += 16
        return y - rect.y

    def _draw_tiles(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        y = rect.y
        cols = 2
        col_w = (rect.width - 20) // cols
        for i, ttype in enumerate(TILE_ORDER):
            col, row = i % cols, i // cols
            x = rect.x + col * (col_w + 20)
            yy = y + row * 96
            color_name = theme.TILE_TYPE_COLORS.get(ttype.value, "t_property")
            accent = theme.color(color_name)
            theme.rounded_rect(surface, pygame.Rect(x, yy, 4, 24), accent, radius=2)
            theme.draw_text(surface, ttype.label, self.fonts.h3(), theme.color("text"),
                            (x + 14, yy))
            theme.draw_wrapped(surface, TILE_DESC.get(ttype, ""), self.fonts.small(),
                               theme.color("text_dim"),
                               pygame.Rect(x + 14, yy + 28, col_w - 20, 62))
        rows = (len(TILE_ORDER) + cols - 1) // cols
        return y + rows * 96 - rect.y

    def _draw_cards(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        if self.cards is None:
            return 0
        y = rect.y
        for card in self.cards.all():
            accent = {"common": "info", "uncommon": "accent",
                      "rare": "danger"}.get(card.rarity, "info")
            theme.rounded_rect(surface, pygame.Rect(rect.x, y, 4, 22),
                               theme.color(accent), radius=2)
            theme.draw_text(surface, card.name, self.fonts.h3(), theme.color("text"),
                            (rect.x + 14, y - 2))
            timing = TIMING_LABEL.get(card.timing, "")
            price = f"商店价 {card.shop_price:,}" if card.shop_price else ""
            meta = " · ".join(x for x in (timing, price) if x)
            theme.draw_text(surface, meta, self.fonts.tiny(), theme.color("text_mute"),
                            (rect.right - 20, y + 2), anchor="topright")
            theme.draw_text(surface, card.description, self.fonts.small(),
                            theme.color("text_dim"), (rect.x + 14, y + 24))
            y += 56
        return y - rect.y

    def _draw_characters(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        """角色页：直接复用角色选择用的同一张能力卡，避免两处各画一套。"""
        from .character_cards import draw_character_card

        y = rect.y
        cols = 2
        col_w = (rect.width - 20) // cols
        card_h = 128
        for i, char in enumerate(self.characters):
            col, row = i % cols, i // cols
            x = rect.x + col * (col_w + 20)
            yy = y + row * (card_h + 14)
            draw_character_card(surface, self.fonts,
                                pygame.Rect(x, yy, col_w, card_h), char["id"])
        rows = (len(self.characters) + cols - 1) // cols
        return y + rows * (card_h + 14) - rect.y
        
    def _draw_maps(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        y = rect.y
        try:
            maps = available_maps()
        except Exception:
            maps = []
        for info in maps:
            box = pygame.Rect(rect.x, y, rect.width - 20, 150)
            theme.rounded_rect(surface, box, theme.color("bg_alt"), radius=14)
            theme.rounded_rect(surface, box, None, radius=14,
                               border=theme.color("border_soft"), border_width=1)
            theme.draw_text(surface, info["name"], self.fonts.h2(), theme.color("text"),
                            (box.x + 20, box.y + 16))
            theme.draw_text(surface, info["description"], self.fonts.small(),
                            theme.color("text_dim"), (box.x + 20, box.y + 46))
            facts = [
                f"{info['tile_count']} 格",
                f"{info['cols']}×{info['rows']} 网格",
                f"{info['property_count']} 处地产",
                f"推荐 {info['recommended']}",
            ]
            fx = box.x + 20
            for fact in facts:
                theme.draw_text(surface, fact, self.fonts.tiny(), theme.color("text_mute"),
                                (fx, box.y + 78))
                fx += 140
            # 迷你棋盘示意
            try:
                board = load_board(info["file"])
                self._draw_mini_board(surface, board, pygame.Rect(
                    box.right - 190, box.y + 24, 150, 100))
            except Exception:
                pass
            y += 166
        return y - rect.y

    def _draw_mini_board(self, surface: pygame.Surface, board,
                         area: pygame.Rect) -> None:
        cell = max(4, min(area.width // board.cols, area.height // board.rows))
        x0 = area.x + (area.width - cell * board.cols) // 2
        y0 = area.y + (area.height - cell * board.rows) // 2
        for tile in board:
            from .board_view import grid_position_for

            corners = list(getattr(board, "corners", []))
            if len(corners) != 4:
                corners = [0, board.cols - 1, board.cols + board.rows - 2,
                           2 * board.cols + board.rows - 3]
            col, row = grid_position_for(board.cols, board.rows, corners, tile.index)
            rect = pygame.Rect(x0 + col * cell, y0 + row * cell, cell - 1, cell - 1)
            accent = theme.color(theme.TILE_TYPE_COLORS.get(tile.type.value, "t_property"))
            theme.rounded_rect(surface, rect, accent, radius=2)

    def _draw_keys(self, surface: pygame.Surface, rect: pygame.Rect) -> int:
        rows = [
            ("空格", "掷骰子 / 确认主要操作"),
            ("ESC", "返回 / 暂停菜单（选卡目标时先取消选择）"),
            ("I", "打开资产面板（升级 / 出售 / 抵押 / 赎回）"),
            ("H", "打开规则与图鉴"),
            ("1 ~ 5", "使用第 N 张道具卡"),
            ("R", "在大厅里切换准备状态"),
            ("Enter", "主菜单快速开始单机"),
            ("F1", "调试信息（阶段 / 修订号 / 状态哈希 / 网络状态）"),
            ("F2", "静音开关"),
            ("F3", "循环切换动画速度 0.5× → 1× → 1.5× → 2×"),
            ("F11", "全屏 / 窗口切换"),
        ]
        y = rect.y
        for key, desc in rows:
            box = pygame.Rect(rect.x, y, 120, 34)
            theme.rounded_rect(surface, box, theme.color("panel_hi"), radius=8)
            theme.draw_text(surface, key, self.fonts.body(), theme.color("text"),
                            box.center, anchor="center")
            theme.draw_text(surface, desc, self.fonts.body(), theme.color("text_dim"),
                            (rect.x + 140, y + 6))
            y += 46
        return y - rect.y
