"""补齐几张「特定弹窗」的截图（债务 / 商店 / 事件卡 / 道具选目标）。

为什么单独一个脚本：
这四种界面依赖对局中的特定时机（踩到商店格、抽到事件卡、欠钱、用带目标的道具），
随机跑一局不一定遇到。这里用**真实的弹窗类 + 真实数据**构造出来，
外观与游戏里完全一致，只是不用等运气。

用法:
    python tools/v04_extra_shots.py
    python tools/v04_extra_shots.py --out docs/reports/shots_v04
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

DT = 1.0 / 60.0


def shot(surface: pygame.Surface, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pygame.image.save(surface, path)
    print(f"  saved {os.path.relpath(path)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐弹窗截图")
    ap.add_argument("--out", default="docs/reports/shots_v04")
    args = ap.parse_args()

    from src.app import App
    from src.persistence.settings import Settings

    settings = Settings()
    settings.set_resolution(1600, 900)
    settings.set("ui", "show_guide", False)
    settings.set("ui", "guide_done", True)
    app = App(settings)
    app.running = True

    shots = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         args.out)

    # 起一局，拿到真实的 GameState 与手牌
    specs = [
        {"id": "p1", "name": "阿明", "character_id": "char_ajin",
         "color_id": "red", "is_ai": False, "is_host": True},
        {"id": "p2", "name": "电脑1", "character_id": "char_xiaoman",
         "color_id": "blue", "is_ai": True},
        {"id": "p3", "name": "电脑2", "character_id": "char_laochen",
         "color_id": "green", "is_ai": True},
    ]
    app._last_local_options = {"map_file": "default_map.json", "preset": "party"}
    app.start_local_game(specs)
    engine = app.local_engine
    for _ in range(30):
        app.update(DT)
        app.scenes.draw(app.virtual)
    scene = app.scenes.current
    st = engine.state
    me = st.player("p1")

    # ---- 先让局面像样一点：给自己几块地、升一级
    owned = [p for p in st.properties.values()][:3]
    for i, prop in enumerate(owned):
        prop.owner_id = "p1"
        prop.level = i % 3
        prop.mortgaged = i == 2
    st.bump()

    # 1) 债务处理面板（资产面板的 debt 模式）
    from src.ui.asset_panel import AssetPanel

    debt_panel = AssetPanel(
        st, me, lambda action, pid: False,
        on_close=None, title="处理债务", allow_sell=True,
        debt={"amount": 8_400, "reason": "租金 · 云帆中心", "creditor": "p2"},
        on_declare=lambda: None)
    debt_panel.board_view = scene.board_view
    scene.modal = debt_panel
    for _ in range(40):
        debt_panel.update(DT)
        app.scenes.draw(app.virtual)
    shot(app.virtual, os.path.join(shots, "v04_17b_debt_panel.png"))
    scene.modal = None

    # 2) 商店
    from src.ui.dialogs import ShopDialog
    from src.game.cards import TIMING_LABEL

    registry = scene._card_defs
    picks = ["card_shield", "card_portal", "card_steal"]
    offers = []
    for cid in picks:
        card = registry.get(cid)
        if card is None:
            continue
        offers.append({
            "id": cid, "name": card.name, "description": card.description,
            "rarity": card.rarity, "icon": card.icon,
            "timing_label": TIMING_LABEL.get(card.timing, ""),
        })
    if len(offers) < 3:                       # 兜底：列表里随便挑三张
        for cid in registry.ids()[:3]:
            card = registry.get(cid)
            offers.append({
                "id": cid, "name": card.name, "description": card.description,
                "rarity": card.rarity, "icon": card.icon,
                "timing_label": TIMING_LABEL.get(card.timing, ""),
            })
    shop = ShopDialog(
        "海湾商店", offers, me.money,
        on_buy=lambda cid: None, on_leave=lambda: None,
        inventory=len(me.cards), inventory_limit=5,
        price_of=lambda cid: 900)
    scene.modal = shop
    for _ in range(40):
        shop.update(DT)
        app.scenes.draw(app.virtual)
    shot(app.virtual, os.path.join(shots, "v04_18_shop.png"))
    scene.modal = None

    # 3) 机遇事件卡（福运）
    from src.ui.dialogs import ChanceCardDialog

    card_dialog = ChanceCardDialog(
        "天降横财",
        "你帮邻居搬了一整天的家，对方硬塞给你一个红包。",
        "获得 ¥1,800")
    scene.modal = card_dialog
    for _ in range(40):
        card_dialog.update(DT)
        app.scenes.draw(app.virtual)
    shot(app.virtual, os.path.join(shots, "v04_19_chance_card.png"))
    scene.modal = None

    # 4) 道具选择目标
    from src.ui.dialogs import CardTargetDialog

    tiles = list(st.board.tiles[:3]) if hasattr(st.board, "tiles") else []
    pairs = []
    for idx in (6, 12, 18):
        try:
            tile = st.board.tile(idx)
            prop = st.property_at(idx)
        except Exception:
            continue
        label = tile.name
        if prop is not None:
            owner = st.player(prop.owner_id) if prop.owner_id else None
            label += f"（{owner.name if owner else '无主'}）"
        pairs.append((idx, label))
    if pairs:
        target_dialog = CardTargetDialog(
            "传送卡", "立即移动到地图上任意一块地，落地后照常结算。",
            pairs, on_pick=lambda t: None, on_cancel=lambda: None,
            columns=1, icon="portal", timing="自己回合内（掷骰前）")
        scene.modal = target_dialog
        for _ in range(40):
            target_dialog.update(DT)
            app.scenes.draw(app.virtual)
        shot(app.virtual, os.path.join(shots, "v04_20_card_target.png"))
        scene.modal = None

    app._teardown_network()
    pygame.quit()
    print(f"补齐完成：{shots}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
