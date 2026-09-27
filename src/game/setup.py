"""对局装配：从数据文件构建一局可运行的 GameEngine。

单机模式与 LAN Host 模式共用这里，保证规则完全一致。
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..utils.paths import data_path, default_config_path
from . import cards as cards_mod
from . import chance as chance_mod
from .board import Board
from .engine import GameEngine
from .player import Player, pick_color
from .property import Property, load_property_definitions
from .state import GameState

_DATA_CACHE: dict[str, Any] = {}


def load_config() -> dict[str, Any]:
    """读取 config/default.json（带缓存）。"""
    if "config" not in _DATA_CACHE:
        path = default_config_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                _DATA_CACHE["config"] = json.load(f)
        except Exception:
            _DATA_CACHE["config"] = {}
    return _DATA_CACHE["config"]


def preset_list() -> list[dict[str, Any]]:
    """返回全部可选预设（给 UI 用）。

    这里必须回传**全部会影响玩家判断的字段**，而不只是名字与资金：
    v0.3 的界面因为字段缺失，把「经过起点 +2,000」显示成了「+0」、
    「初始手牌 2 张」显示成了「0 张」—— 玩家据此判断节奏会完全判断错。
    """
    presets = load_config().get("presets") or {}
    base = load_config().get("gameplay") or {}
    out = []
    for key, data in presets.items():
        merged = dict(base)
        merged.update(data)
        out.append({
            "key": key,
            "name": data.get("name", key),
            "desc": data.get("desc", ""),
            "starting_money": merged.get("starting_money", 15000),
            "pass_start_bonus": merged.get("pass_start_bonus", 2000),
            "max_rounds": merged.get("max_rounds", 200),
            "starting_cards": merged.get("starting_cards", 2),
            "shop_base_price": merged.get("shop_base_price", 1200),
            "bonus_pool_base": merged.get("bonus_pool_base", 1500),
            "phase_scale": merged.get("phase_scale", 1.0),
            "ai_think_sec": merged.get("ai_think_sec", 0.45),
        })
    # 保证 standard 在最前，其余按固定顺序（快速 → 标准 → 休闲 → 聚会）
    order = {"quick": 0, "standard": 1, "casual": 2, "party": 3}
    out.sort(key=lambda x: (order.get(x["key"], 9), x["key"]))
    return out


def preset_by_key(key: str) -> dict[str, Any]:
    for item in preset_list():
        if item["key"] == key:
            return item
    return {}


#: 人数 → 推荐的节奏预设（只用于「建议」，不强制）
def recommend_preset(player_count: int) -> tuple[str, str]:
    """返回 (预设 key, 给玩家的一句话理由)。"""
    if player_count >= 5:
        return "party", (f"{player_count} 人一局如果用标准节奏通常要 50 分钟以上，"
                         "推荐「聚会局」：35～50 分钟打完一整局。")
    if player_count <= 3:
        return "standard", ""
    return "standard", ""


def preset_timeline_estimate(preset_key: str, player_count: int) -> str:
    """给界面用的一句时长估计（粗略但诚实：基于每回合演出 + 轮数上限）。"""
    import json

    from ..utils.paths import data_path

    path = data_path("default_map.json")
    tile_count = 36
    try:
        with open(path, "r", encoding="utf-8") as f:
            tile_count = len(json.load(f).get("tiles") or []) or 36
    except Exception:
        pass

    item = preset_by_key(preset_key)
    rounds = int(item.get("max_rounds", 200))
    phase_scale = float(item.get("phase_scale", 1.0) or 1.0)
    # 每回合固定演出 ≈ 2.45s（掷骰 + 结算 + 过场），再加移动 ≈ 7 格 × 0.17s
    per_turn = (2.45 * phase_scale) + min(1.6, tile_count * 0.045)
    # 真人决策与看牌时间：每个自己的回合约 2.5 秒
    per_turn += 2.5 / max(2, player_count)
    total_min = rounds * per_turn * player_count / 60.0
    return f"参考时长：约 {int(total_min * 0.7)}～{int(total_min * 1.2)} 分钟"


def default_rules(preset: str = "standard") -> dict[str, Any]:
    """默认规则 = gameplay 配置 + 选中预设的覆盖值。"""
    doc = load_config()
    rules = dict(doc.get("gameplay", {}))
    presets = doc.get("presets") or {}
    chosen = presets.get(preset) or presets.get("standard") or {}
    for key, value in chosen.items():
        if key in ("name", "desc"):
            continue
        rules[key] = value
    rules["preset"] = preset if preset in presets else "standard"
    return rules


def _fallback_rules() -> dict[str, Any]:
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


def map_files() -> list[tuple[str, str]]:
    """返回 [(显示名, 文件名)] —— 扫描 data 目录下所有地图。"""
    out: list[tuple[str, str]] = []
    data_dir = data_path()
    for name in sorted(os.listdir(data_dir)):
        if not name.endswith(".json"):
            continue
        if name == "default_map.json" or name.startswith("map_"):
            out.append((name, name))
    return out


def load_board(map_file: str = "default_map.json") -> Board:
    """载入地图。传 'map_seaside.json' 之类的文件名，或地图 id。"""
    if not map_file.endswith(".json"):
        map_file = ("default_map.json" if map_file == "city_default"
                    else f"map_{map_file}.json")
    key = f"board:{map_file}"
    if key not in _DATA_CACHE:
        _DATA_CACHE[key] = Board.load(data_path(map_file))
    return _DATA_CACHE[key]


def available_maps() -> list[dict]:
    """给 UI 用的地图清单（名称 / 尺寸 / 格数 / 推荐人数 / 文件名）。"""
    out = []
    for _fname, filename in map_files():
        board = load_board(filename)
        out.append({
            "file": filename,
            "id": board.map_id,
            "name": board.name,
            "description": board.description,
            "cols": board.cols,
            "rows": board.rows,
            "tile_count": board.tile_count,
            "recommended": board.recommended,
            "property_count": len(board.purchasable_tiles()),
        })
    return out


def load_properties(map_id: str = "city_default") -> tuple[dict[str, Property],
                                                            dict[str, list[str]], float]:
    """读取指定地图的地产定义并生成全新的地产实例（owner 为空）。"""
    with open(data_path("properties.json"), "r", encoding="utf-8") as f:
        doc = json.load(f)
    bonus = float(doc.get("district_bonus_multiplier", 2.0))
    maps = doc.get("maps") or {}
    entry = maps.get(map_id) or {}
    if not entry:
        # 兼容旧格式（单一地图）
        props_defs = doc.get("properties", [])
        districts_raw = doc.get("districts", {})
    else:
        props_defs = entry.get("properties", [])
        districts_raw = entry.get("districts", {})

    fresh: dict[str, Property] = {}
    for p in props_defs:
        prop = Property.from_dict(p)
        fresh[prop.id] = prop
    district_map = {
        name: [f"p{int(idx):02d}" for idx in idxs]
        for name, idxs in districts_raw.items()
    }
    return fresh, district_map, bonus


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
    preset: str = "standard",
) -> GameEngine:
    """player_specs: [{id, name, character_id, color_id|None, is_ai, is_host}]"""
    board = load_board(map_file)
    properties, district_map, bonus = load_properties(board.map_id)
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

    rules = default_rules(preset)
    if rules_override:
        rules.update(rules_override)
    rules["_map_file"] = map_file
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

    think = float(rules.get("ai_think_sec", 0.45) or 0.45)
    for p in players:
        if p.is_ai:
            engine.bind_controller(
                p.id, AIController(p.id, seed=(seed or 1) + p.slot, think_sec=think))
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
