"""对局装配：从数据文件构建一局可运行的 GameEngine。

单机模式与 LAN Host 模式共用这里，保证规则完全一致。
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..utils.paths import data_path
from . import cards as cards_mod
from . import chance as chance_mod
from .board import Board
from .engine import GameEngine
from .player import Player, pick_color
from .property import Property, load_property_definitions
from .state import GameState

_DATA_CACHE: dict[str, Any] = {}


def default_rules() -> dict[str, Any]:
    """从 config/default.json 读取 gameplay 配置。"""
    path = os.path.join(os.path.dirname(data_path()), "config", "default.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return dict(doc.get("gameplay", {}))
    except Exception:
        return {
            "starting_money": 15000,
            "pass_start_bonus": 2000,
            "jail_release_fee": 1500,
            "jail_max_turns": 3,
            "jail_dice_needed": 6,
            "tax_fixed": 1200,
            "tax_asset_rate": 0.04,
            "max_cards_per_player": 5,
            "max_rounds": 300,
            "ai_safety_reserve": 2500,
        }


def load_board(map_file: str = "default_map.json") -> Board:
    key = f"board:{map_file}"
    if key not in _DATA_CACHE:
        _DATA_CACHE[key] = Board.load(data_path(map_file))
    return _DATA_CACHE[key]


def load_properties() -> tuple[dict[str, Property], dict[str, list[str]], float]:
    """读取地产定义并生成一份全新的地产实例（owner 为空）。"""
    props, districts, bonus = load_property_definitions(data_path("properties.json"))
    fresh: dict[str, Property] = {}
    for p in props:
        fresh[p.id] = Property.from_dict(p.to_dict())
    district_map: dict[str, list[str]] = {
        name: [f"p{idx:02d}" for idx in idxs] for name, idxs in districts.items()
    }
    return fresh, district_map, float(bonus)


def load_chance_registry() -> chance_mod.ChanceRegistry:
    if "chance" not in _DATA_CACHE:
        _DATA_CACHE["chance"] = chance_mod.ChanceRegistry.load(data_path("chance_events.json"))
    return _DATA_CACHE["chance"]


def load_card_registry() -> cards_mod.CardRegistry:
    if "cards" not in _DATA_CACHE:
        _DATA_CACHE["cards"] = cards_mod.CardRegistry.load(data_path("cards.json"))
    return _DATA_CACHE["cards"]


def load_characters() -> dict[str, Any]:
    if "chars" not in _DATA_CACHE:
        with open(data_path("characters.json"), "r", encoding="utf-8") as f:
            _DATA_CACHE["chars"] = json.load(f)
    return _DATA_CACHE["chars"]


def character_by_id(char_id: str) -> dict[str, Any] | None:
    doc = load_characters()
    for c in doc["characters"]:
        if c["id"] == char_id:
            return c
    return None


def palette() -> list[dict[str, Any]]:
    return list(load_characters()["player_palette"])


def make_player(
    player_id: str,
    name: str,
    character_id: str,
    color: dict[str, Any],
    slot: int,
    is_ai: bool,
    is_host: bool,
) -> Player:
    char = character_by_id(character_id) or {}
    return Player(
        player_id=player_id,
        name=name,
        character_id=character_id,
        color_id=color["id"],
        slot=slot,
        is_ai=is_ai,
        is_host=is_host,
        money=0,
        perk=dict(char.get("perk") or {}),
    )


def create_engine(
    player_specs: list[dict[str, Any]],
    seed: int | None = None,
    anim_speed: float = 1.0,
    map_file: str = "default_map.json",
    rules_override: dict[str, Any] | None = None,
) -> GameEngine:
    """player_specs: [{id, name, character_id, color_id|None, is_ai, is_host}]"""
    board = load_board(map_file)
    properties, district_map, bonus = load_properties()
    chance_reg = load_chance_registry()
    card_reg = load_card_registry()

    used_colors: set[str] = set()
    pal = palette()
    players: list[Player] = []
    for i, spec in enumerate(player_specs):
        color_id = spec.get("color_id")
        if color_id:
            color = next((c for c in pal if c["id"] == color_id), pal[i % len(pal)])
            used_colors.add(color["id"])
        else:
            color = pick_color(pal, used_colors)
        players.append(
            make_player(
                player_id=spec.get("id") or f"p{i + 1}",
                name=spec.get("name") or f"玩家{i + 1}",
                character_id=spec.get("character_id") or pal[i % len(pal)]["id"],
                color=color,
                slot=i,
                is_ai=bool(spec.get("is_ai", False)),
                is_host=bool(spec.get("is_host", False)),
            )
        )

    rules = default_rules()
    if rules_override:
        rules.update(rules_override)
    rules["_card_ids"] = card_reg.ids()
    rules["_card_names"] = {c.id: c.name for c in card_reg.all()}
    rules["move_tile_sec"] = 0.17

    state = GameState(
        match_id=__import__("uuid").uuid4().hex[:8],
        board=board,
        players=players,
        properties=properties,
        district_props=district_map,
        district_bonus=bonus,
        rules=rules,
        seed=seed,
    )
    engine = GameEngine(state, chance_reg, card_reg, anim_speed=anim_speed)

    # 绑定控制器：AI 玩家自动挂 AIController，其余由调用方接管
    from ..controllers.ai import AIController

    for p in players:
        if p.is_ai:
            engine.bind_controller(p.id, AIController(p.id, seed=(seed or 1) + p.slot))
    return engine


def engine_from_state(state: GameState, anim_speed: float = 1.0) -> GameEngine:
    """从存档恢复引擎（控制器由调用方重新绑定）。"""
    chance_reg = load_chance_registry()
    card_reg = load_card_registry()
    if "_card_ids" not in state.rules:
        state.rules["_card_ids"] = card_reg.ids()
        state.rules["_card_names"] = {c.id: c.name for c in card_reg.all()}
    state.rules.setdefault("move_tile_sec", 0.17)
    return GameEngine(state, chance_reg, card_reg, anim_speed=anim_speed)
