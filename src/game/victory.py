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


#: 趣味称号：(称号, 判定键, 是否越大越好)
TITLES = [
    ("地产大亨", "property_count", True),
    ("最佳房东", "rent_income", True),
    ("卡牌大师", "cards_used", True),
    ("运势之王", "event_gain", True),
    ("散财童子", "money_spent", True),
    ("守财奴", "money", True),
]


def award_titles(state: GameState, rows: list[dict[str, Any]]) -> dict[str, str]:
    """按统计给玩家发趣味称号。只影响展示，不参与胜负判定。"""
    awards: dict[str, str] = {}
    for title, key, bigger in TITLES:
        best_id, best_value = None, None
        for row in rows:
            value = row.get(key)
            if value is None:
                value = row["stats"].get(key, 0)
            if bigger:
                if best_value is None or value > best_value:
                    best_id, best_value = row["player_id"], value
            elif best_value is None or value < best_value:
                best_id, best_value = row["player_id"], value
        if best_id is not None and best_value:
            awards.setdefault(best_id, title)
    return awards


def unlucky_one(state: GameState, rows: list[dict[str, Any]]) -> str:
    """最早破产的人拿「倒霉蛋」。"""
    bankrupt = [r for r in rows if r["bankrupt"]]
    if not bankrupt:
        return ""
    first = min(bankrupt, key=lambda r: r["bankrupt_order"] if r["bankrupt_order"] > 0 else 999)
    return first["player_id"]


def ranking(state: GameState) -> list[dict[str, Any]]:
    """返回全部玩家的结算排名。

    排序规则：冠军优先 → 破产顺序靠后优先 → 总资产高优先。
    """
    rows: list[dict[str, Any]] = []
    for p in state.players:
        props = state.properties_of(p.id)
        ledger_summary = state.ledger.player_summary(p.id) if hasattr(state, "ledger") else {}
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
            "upgrade_value": sum(x.total_invested - x.price for x in props),
            "total_asset": state.player_asset_value(p.id),
            "stats": dict(p.stats),
            "income": dict(ledger_summary.get("income", {})),
            "expense": dict(ledger_summary.get("expense", {})),
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

    titles = award_titles(state, rows)
    unlucky = unlucky_one(state, rows)
    for r in rows:
        tags = []
        if r["player_id"] in titles:
            tags.append(titles[r["player_id"]])
        if r["player_id"] == unlucky:
            tags.append("倒霉蛋")
        r["titles"] = tags
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
        "flow": _flow_summary(state),
    }


def _flow_summary(state: GameState) -> dict[str, Any]:
    """全场收入 / 支出的分类汇总（结算界面用）。"""
    income: dict[str, int] = {}
    expense: dict[str, int] = {}
    for e in state.ledger.entries:
        bucket = income if e.amount > 0 else expense
        bucket[e.reason] = bucket.get(e.reason, 0) + abs(e.amount)
    top_in = dict(sorted(income.items(), key=lambda kv: -kv[1])[:4])
    top_out = dict(sorted(expense.items(), key=lambda kv: -kv[1])[:4])
    return {"income": top_in, "expense": top_out}
