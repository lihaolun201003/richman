"""胜负判定与结算。"""
from __future__ import annotations

from typing import Any

from .player import Player
from .state import GameState


def check_winner(state: GameState) -> str | None:
    """只剩一名未破产玩家时返回其 id，否则返回 None。"""
    alive = state.active_players()
    if len(alive) <= 1:
        return alive[0].id if alive else None
    return None


def check_max_rounds(state: GameState) -> str | None:
    """达到最大轮数时按总资产排名判定胜者。"""
    limit = int(state.rules.get("max_rounds", 300))
    if state.round_number < limit:
        return None
    ranked = sorted(state.active_players(), key=lambda p: _score(state, p), reverse=True)
    return ranked[0].id if ranked else None


def _score(state: GameState, player: Player) -> tuple[int, int, int]:
    return (
        state.player_asset_value(player.id),
        len(state.properties_of(player.id)),
        player.money,
    )


def ranking(state: GameState) -> list[dict[str, Any]]:
    """返回全部玩家的结算排名。

    排序规则：冠军优先 → 破产顺序靠后优先 → 总资产高优先。
    """
    rows: list[dict[str, Any]] = []
    for p in state.players:
        props = state.properties_of(p.id)
        rows.append({
            "player_id": p.id,
            "name": p.name,
            "color_id": p.color_id,
            "character_id": p.character_id,
            "is_ai": p.is_ai,
            "bankrupt": p.bankrupt,
            "bankrupt_order": p.bankrupt_order,
            "money": p.money,
            "property_count": len(props),
            "property_value": sum(x.asset_value for x in props),
            "total_asset": state.player_asset_value(p.id),
            "stats": dict(p.stats),
            "winner": p.id == state.winner_id,
        })

    def sort_key(r: dict[str, Any]):
        if r["winner"]:
            return (0, 0, -r["total_asset"])
        if r["bankrupt"]:
            # 破产越晚（order 越大）排名越高
            return (2, -int(r["bankrupt_order"]), -r["total_asset"])
        return (1, 0, -r["total_asset"])

    rows.sort(key=sort_key)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def summary_text(state: GameState) -> str:
    """一句话结算文案。"""
    winner = state.player(state.winner_id)
    if winner is None:
        return "游戏结束，没有产生赢家"
    return f"{winner.name} 获胜！共进行 {state.round_number} 轮"


def final_stats(state: GameState) -> dict[str, Any]:
    """整局汇总统计。"""
    total_rent = sum(p.stats.get("rent_income", 0) for p in state.players)
    total_rent_paid = sum(p.stats.get("rent_paid", 0) for p in state.players)
    return {
        "rounds": state.round_number,
        "turn": state.turn_number,
        "players": len(state.players),
        "bankrupt_count": sum(1 for p in state.players if p.bankrupt),
        "total_rent_income": total_rent,
        "total_rent_paid": total_rent_paid,
        "total_bought": sum(p.stats.get("properties_bought", 0) for p in state.players),
        "total_upgraded": sum(p.stats.get("properties_upgraded", 0) for p in state.players),
        "total_cards_used": sum(p.stats.get("cards_used", 0) for p in state.players),
        "total_chance": sum(p.stats.get("chance_events", 0) for p in state.players),
        "total_start_passes": sum(p.stats.get("start_passes", 0) for p in state.players),
        "duration_sec": (state.ended_at or 0) - state.started_at if state.ended_at else 0,
        "bonus_pool": state.bonus_pool,
    }
