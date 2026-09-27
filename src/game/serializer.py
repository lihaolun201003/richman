"""状态序列化：网络快照、存档、以及协议版本校验。

所有跨进程/跨进程边界的状态传递都必须经过这里，
避免散落各处的裸 dict 拼装。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from .state import GameState

#: 存档格式版本，与网络 protocol_version 独立
SAVE_VERSION = 1


class SnapshotError(Exception):
    """快照格式错误。"""


def state_to_snapshot(state: GameState) -> dict[str, Any]:
    """把 GameState 编码为可 JSON 序列化的快照。"""
    return state.to_dict()


def state_from_snapshot(snapshot: dict[str, Any]) -> GameState:
    """从快照还原 GameState。"""
    if not isinstance(snapshot, dict) or "board" not in snapshot:
        raise SnapshotError("快照缺少 board 字段")
    return GameState.from_dict(snapshot)


def snapshot_revision(snapshot: dict[str, Any]) -> int:
    return int(snapshot.get("revision", -1))


def should_accept_snapshot(local_revision: int, snapshot: dict[str, Any]) -> bool:
    """只接受更新的快照，禁止旧数据回滚。"""
    return snapshot_revision(snapshot) > local_revision


def dumps(snapshot: dict[str, Any], compact: bool = True) -> str:
    if compact:
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


def loads(text: str) -> dict[str, Any]:
    return json.loads(text)


# ------------------------------------------------------------------ 存档

def save_game(state: GameState, path: str, meta: dict[str, Any] | None = None) -> str:
    """把当前状态写入存档文件，返回文件路径。"""
    doc = {
        "save_version": SAVE_VERSION,
        "saved_at": time.time(),
        "saved_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta or {},
        "state": state_to_snapshot(state),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # 临时文件名带上进程号：同一台电脑双开（房主 + 客户端）时两个实例会写同一个
    # autosave.json，共用 `.tmp` 会把对方的临时文件覆盖掉，导致「找不到文件」。
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    except OSError:
        _remove_quietly(tmp)
        raise
    # Windows 上目标文件可能正被另一个进程 / 杀毒软件短暂占用，
    # os.replace 会直接抛错；这里做几次短暂重试，仍然失败才向上报。
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return path
        except OSError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    _remove_quietly(tmp)
    raise last_error if last_error else OSError("保存失败")


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def read_save_meta(path: str) -> dict[str, Any]:
    """只读存档头，用于存档列表展示。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        st = doc.get("state", {})
        players = st.get("players", [])
        return {
            "ok": True,
            "path": path,
            "save_version": doc.get("save_version", 0),
            "saved_at": doc.get("saved_at", 0),
            "saved_at_text": doc.get("saved_at_text", ""),
            "match_id": st.get("match_id", ""),
            "round": st.get("round_number", 0),
            "phase": st.get("phase", ""),
            "player_count": len(players),
            "players": [p.get("name", "?") for p in players],
            "map": (st.get("board") or {}).get("name", ""),
            "meta": doc.get("meta", {}),
        }
    except Exception as exc:
        return {"ok": False, "path": path, "error": str(exc)}


def load_game(path: str) -> tuple[GameState, dict[str, Any]]:
    """读取存档，返回 (state, meta)。"""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    version = int(doc.get("save_version", 0))
    if version > SAVE_VERSION:
        raise SnapshotError(
            f"存档版本 {version} 高于当前程序支持的 {SAVE_VERSION}，请更新游戏"
        )
    state = state_from_snapshot(doc["state"])
    return state, doc.get("meta", {})


def list_saves(saves_dir: str) -> list[dict[str, Any]]:
    """列出存档目录下的所有存档（按时间倒序）。"""
    if not os.path.isdir(saves_dir):
        return []
    items: list[dict[str, Any]] = []
    for name in os.listdir(saves_dir):
        if not name.endswith(".json"):
            continue
        items.append(read_save_meta(os.path.join(saves_dir, name)))
    items.sort(key=lambda d: d.get("saved_at", 0), reverse=True)
    return items
