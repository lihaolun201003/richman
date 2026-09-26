"""存档：单机保存/读取，以及自动存档。

存档内容 = 完整 GameState（含 seed 与 rng_counter，因此读档后可继续复现随机）。
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..game import serializer as ser
from ..game.engine import GameEngine
from ..game.setup import engine_from_state
from ..utils.logging_setup import get_logger
from ..utils.paths import ensure_dir, saves_path

log = get_logger(__name__)

AUTOSAVE_NAME = "autosave.json"


def autosave_path() -> str:
    ensure_dir(saves_path())
    return saves_path(AUTOSAVE_NAME)


def make_meta(state) -> dict[str, Any]:
    """存档头信息，用于在 UI 中展示。"""
    return {
        "match_id": state.match_id,
        "round": state.round_number,
        "turn": state.turn_number,
        "players": [p.name for p in state.players],
        "map_id": state.board.map_id,
        "map_name": state.board.name,
        "saved_revision": state.revision,
    }


def autosave(engine: GameEngine) -> bool:
    """自动存档：每次回合结束时调用。失败不抛出，只在日志里记一笔。"""
    try:
        ser.save_game(engine.state, autosave_path(), make_meta(engine.state))
        return True
    except Exception as exc:
        log.warning("自动存档失败：%s", exc)
        return False


def save_to_slot(engine: GameEngine, name: str) -> str:
    """保存到指定名称的存档槽。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "save"
    if not safe.endswith(".json"):
        safe += ".json"
    ensure_dir(saves_path())
    path = saves_path(safe)
    ser.save_game(engine.state, path, make_meta(engine.state))
    return path


def load_engine(path: str, anim_speed: float = 1.0) -> GameEngine:
    """读取存档并重建引擎（控制器由调用方绑定）。

    anim_speed 必须由调用方传入用户当前的动画速度设置，
    否则读档后速度会悄悄退回 1.0x。
    """
    state, meta = ser.load_game(path)
    engine = engine_from_state(state, anim_speed=anim_speed)
    engine.set_anim_speed(anim_speed)
    engine.state.log("INFO", f"已读取存档（第 {state.round_number} 轮）")
    return engine


def load_autosave() -> GameEngine | None:
    path = autosave_path()
    if not os.path.isfile(path):
        return None
    try:
        return load_engine(path)
    except Exception as exc:
        log.warning("读取自动存档失败：%s", exc)
        return None


def list_slots() -> list[dict[str, Any]]:
    return ser.list_saves(saves_path())


def has_autosave() -> bool:
    return os.path.isfile(autosave_path())


def autosave_age_text() -> str:
    path = autosave_path()
    if not os.path.isfile(path):
        return ""
    delta = time.time() - os.path.getmtime(path)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"
