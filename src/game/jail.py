"""看守所（监狱）机制。

规则：
- 踩到「治安巡查」格或抽到对应机遇事件会被关押；
- 关押期间每回合可以二选一：支付保释金离开，或掷骰子（掷出 >= jail_dice_needed 离开）；
- 关押满 jail_max_turns 回合后自动支付保释金离开（钱不够则继续关押，但不会再累加）；
- 看守所格本身只是「路过没事」的位置。
"""
from __future__ import annotations

from .player import Player
from .state import GameState


def jail_fee(state: GameState, player: Player) -> int:
    """该玩家的保释金（含角色折扣）。"""
    base = int(state.rules.get("jail_release_fee", 1500))
    rate = player.perk_value("jail_fee_discount")
    if rate:
        base = int(round(base * (1.0 - rate)))
    return max(0, base)


def jail_max_turns(state: GameState) -> int:
    return int(state.rules.get("jail_max_turns", 3))


def jail_needed_roll(state: GameState) -> int:
    return int(state.rules.get("jail_dice_needed", 6))


def put_in_jail(state: GameState, player: Player) -> None:
    """把玩家送进看守所。已在内则重置关押计数。"""
    player.position = state.board.jail_index()
    player.in_jail = True
    player.jail_turns = 0
    player.stats["jail_visits"] = player.stats.get("jail_visits", 0) + 1


def release_from_jail(player: Player) -> None:
    player.in_jail = False
    player.jail_turns = 0


def is_auto_release(state: GameState, player: Player) -> bool:
    """关押期满且付得起保释金时自动释放。"""
    if not player.in_jail:
        return False
    if player.jail_turns < jail_max_turns(state):
        return False
    return player.money >= jail_fee(state, player)


def jail_status_text(state: GameState, player: Player) -> str:
    if not player.in_jail:
        return ""
    left = max(0, jail_max_turns(state) - player.jail_turns)
    return f"关押中（剩 {left} 回合 / 保释金 {jail_fee(state, player)}）"
