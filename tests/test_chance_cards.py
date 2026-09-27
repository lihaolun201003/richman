"""机遇事件、道具卡、监狱、状态效果测试。"""
from __future__ import annotations

import pytest

from src.game import cards as cards_mod
from src.game import chance as chance_mod
from src.game import jail as jail_mod
from src.game.commands import Command, CommandType, DecisionKind
from src.game.phases import GamePhase
from src.game.player import (
    STATUS_FIXED_DICE,
    STATUS_PROTECTED,
    STATUS_RENT_DOUBLE_ONCE,
    STATUS_SKIP_TURN,
    StatusEffect,
)
from conftest import advance, resolve, run_until_decision, submit


# ---------------------------------------------------------------- 机遇事件

def test_chance_registry_has_30_plus_events(engine):
    reg = engine.chance_registry
    assert len(reg.ids()) >= 30


def test_all_chance_effects_have_handler(engine):
    """每个事件的 effect.kind 都必须能被 apply_effect 处理。"""
    reg = engine.chance_registry
    handled = set()
    st = engine.state
    player = st.players[0]
    player.money = 100000

    for cid in reg.ids():
        card = reg.get(cid)
        followups, text = chance_mod.apply_effect(st, reg, player, card.effect)
        assert text, f"{cid} 没有返回描述文本"
        assert text != "无效果", f"{cid} 的效果 kind={card.effect.get('kind')} 未被处理"
        handled.add(cid)
    assert len(handled) == len(reg.ids())


def test_chance_deck_cycles(engine):
    st = engine.state
    reg = engine.chance_registry
    seen = set()
    for _ in range(200):
        card = chance_mod.draw(st, reg)
        seen.add(card.id)
    # 牌堆应当会重洗，抽到多种事件
    assert len(seen) > 10


def test_chance_gain_money(engine):
    st = engine.state
    player = st.players[0]
    before = player.money
    chance_mod.apply_effect(st, engine.chance_registry, player,
                            {"kind": "gain_money", "amount": 1000})
    assert player.money > before


def test_chance_lose_money(engine):
    st = engine.state
    player = st.players[0]
    before = player.money
    chance_mod.apply_effect(st, engine.chance_registry, player,
                            {"kind": "lose_money", "amount": 500})
    assert player.money == before - 500


def test_chance_collect_from_all(engine):
    st = engine.state
    player = st.players[0]
    others = st.other_active_players(player.id)
    before = player.money
    others_before = [p.money for p in others]

    chance_mod.apply_effect(st, engine.chance_registry, player,
                            {"kind": "collect_from_all", "amount": 300})

    assert player.money > before
    for p, b in zip(others, others_before):
        assert p.money == b - 300


def test_chance_go_to_jail_followup(engine):
    st = engine.state
    player = st.players[0]
    followups, _ = chance_mod.apply_effect(st, engine.chance_registry, player,
                                           {"kind": "go_to_jail"})
    assert followups and followups[0]["action"] == chance_mod.FOLLOWUP_GO_JAIL


def test_chance_move_followups(engine):
    st = engine.state
    player = st.players[0]
    f1, _ = chance_mod.apply_effect(st, engine.chance_registry, player,
                                    {"kind": "move_forward", "steps": 3})
    assert f1[0]["action"] == chance_mod.FOLLOWUP_MOVE and f1[0]["steps"] == 3
    f2, _ = chance_mod.apply_effect(st, engine.chance_registry, player,
                                    {"kind": "move_backward", "steps": 2})
    assert f2[0]["steps"] == -2


def test_chance_effect_never_breaks_state(engine):
    """随机应用大量事件后，核心状态仍然合法。"""
    st = engine.state
    reg = engine.chance_registry
    for _ in range(300):
        player = st.active_players()[st.next_rng().randrange(len(st.active_players()))]
        card = chance_mod.draw(st, reg)
        chance_mod.apply_effect(st, reg, player, card.effect)
        # 直接清偿负现金，模拟引擎行为
        for p in st.active_players():
            if p.money < 0:
                p.money = 0
    for p in st.players:
        assert 0 <= p.position < st.board.tile_count


# ---------------------------------------------------------------- 道具卡

def test_card_registry_has_12_cards(engine):
    assert len(engine.card_registry.ids()) >= 12


def test_card_effects_have_handler(engine):
    reg = engine.card_registry
    kinds = {c.effect.get("kind") for c in reg.all()}
    known = {"status", "gain_money", "teleport", "swap_position", "steal",
             "apply_status_to_target", "place_barrier", "free_upgrade",
             "downgrade_property", "swap_property", "extra_turn", "leave_jail",
             "clear_negative", "wealth_tax"}
    assert kinds <= known, f"存在未实现的卡牌效果：{kinds - known}"


def test_use_card_gain_money(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    st.set_phase(GamePhase.WAIT_ROLL, 0.0)
    player.cards.append("card_cash")
    before = player.money

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_cash"}))

    assert result.ok
    assert player.money == before + 1500
    assert "card_cash" not in player.cards
    assert player.stats["cards_used"] == 1


def test_use_card_not_own_turn_rejected(engine_no_ai):
    """非「任意时刻」的卡在别人回合不能使用。"""
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = st.players[1].id
    player.cards.append("card_double_rent")      # timing = pre_roll
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_double_rent"}))
    assert not result.ok
    assert "掷骰前" in result.reason or "自己回合" in result.reason


def test_any_turn_card_allowed_off_turn(engine_no_ai):
    """标注 any_turn 的应急卡（现金卡）在别人回合也能用。"""
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = st.players[1].id
    player.cards.append("card_cash")
    before = player.money
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_cash"}))
    assert result.ok
    assert player.money > before


def test_use_card_without_owning_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_cash"}))
    assert not result.ok
    assert "没有这张道具" in result.reason


def test_card_requires_target(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.cards.append("card_rob")
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_rob"}))
    assert not result.ok
    assert "目标" in result.reason


def test_card_steal_target(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    target = st.players[1]
    st.current_player_id = player.id
    player.cards.append("card_rob")
    before, t_before = player.money, target.money

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_rob", "target": target.id}))

    assert result.ok
    assert player.money > before
    assert target.money < t_before


def test_card_steal_blocked_by_protection(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    target = st.players[1]
    st.current_player_id = player.id
    player.cards.append("card_rob")
    target.add_status(StatusEffect(STATUS_PROTECTED, 3))
    before = target.money

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_rob", "target": target.id}))

    assert not result.ok
    assert "保护" in result.reason
    # 失败时道具应退回
    assert "card_rob" in player.cards
    assert target.money == before


def test_card_swap_positions(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, target = st.players[0], st.players[1]
    st.current_player_id = player.id
    player.position, target.position = 3, 20
    player.cards.append("card_swap")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_swap", "target": target.id}))

    assert result.ok
    assert player.position == 20
    assert target.position == 3


def test_card_barrier_blocks_movement(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.cards.append("card_barrier")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_barrier", "target": 5}))

    assert result.ok
    assert st.barrier_at(5)


def test_card_cannot_barrier_start(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.cards.append("card_barrier")
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_barrier", "target": 0}))
    assert not result.ok


def test_card_free_upgrade(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    prop = st.property_at(1)
    prop.assign(player.id)
    player.cards.append("card_free_upgrade")
    before = player.money

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_free_upgrade", "target": prop.id}))

    assert result.ok
    assert prop.level == 1
    assert player.money == before


def test_card_targets_are_valid(engine):
    st = engine.state
    player = st.players[0]
    reg = engine.card_registry
    for card in reg.all():
        targets = cards_mod.valid_targets(st, player, card)
        if card.needs_target is None:
            assert targets == []
        elif card.needs_target == "tile":
            assert len(targets) == st.board.tile_count
        elif card.needs_target == "player":
            assert all(t != player.id for t in targets)
        elif card.needs_target == "dice_value":
            assert targets == list(range(2, 13))


# ---------------------------------------------------------------- 监狱

def test_go_to_jail_tile(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.position = 28
    eng._send_to_jail(player)
    assert player.in_jail
    assert player.position == st.board.jail_index()
    assert player.stats["jail_visits"] == 1


def test_jail_decision_offered(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    jail_mod.put_in_jail(st, player)
    st.current_player_id = player.id

    eng._wait_roll()

    pd = st.pending_decision
    assert pd is not None
    assert pd.kind == DecisionKind.JAIL
    assert {o.id for o in pd.options} == {"pay", "roll"}
    assert st.phase is GamePhase.JAIL_DECISION


def test_jail_pay_releases(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    jail_mod.put_in_jail(st, player)
    st.current_player_id = player.id
    eng._wait_roll()
    fee = jail_mod.jail_fee(st, player)
    before = player.money

    resolve(eng, "pay")

    assert not player.in_jail
    assert player.money == before - fee


def test_jail_roll_success_releases(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    jail_mod.put_in_jail(st, player)
    st.current_player_id = player.id
    # 固定骰子，确保掷出足够点数
    player.add_status(StatusEffect(STATUS_FIXED_DICE, 1, payload={"value": 6}))
    eng._wait_roll()

    resolve(eng, "roll")

    assert not player.in_jail


def test_jail_roll_fail_keeps_in_jail(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    jail_mod.put_in_jail(st, player)
    st.current_player_id = player.id
    player.add_status(StatusEffect(STATUS_FIXED_DICE, 1, payload={"value": 2}))
    eng._wait_roll()

    resolve(eng, "roll")

    assert player.in_jail
    assert player.jail_turns == 1


def test_jail_auto_release_after_max_turns(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    jail_mod.put_in_jail(st, player)
    player.jail_turns = jail_mod.jail_max_turns(st)
    st.current_player_id = player.id
    before = player.money

    eng._wait_roll()

    assert not player.in_jail
    assert player.money < before


def test_jail_fee_character_discount(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.perk = {}
    plain = jail_mod.jail_fee(st, player)
    player.perk = {"kind": "jail_fee_discount", "value": 0.5}
    discounted = jail_mod.jail_fee(st, player)
    assert discounted == plain // 2
