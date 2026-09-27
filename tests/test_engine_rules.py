"""引擎规则测试：购买、升级、租金流转、经过起点、税收、破产、回合推进。"""
from __future__ import annotations

import pytest

from src.game import bankruptcy, economy
from src.game.commands import Command, CommandType
from src.game.jail import jail_fee
from src.game.phases import GamePhase
from conftest import advance, resolve, run_until_decision, submit


# ---------------------------------------------------------------- 购买

def test_buy_property_success(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    before = player.money

    price, _ = economy.buy_price(st, player, prop)
    result = eng._do_buy(player, prop.id)

    assert result.ok
    assert prop.owner_id == player.id
    assert player.money == before - price
    assert player.stats["properties_bought"] == 1
    # 买地后进入回合结束阶段
    assert st.phase in (GamePhase.TURN_END, GamePhase.TURN_START)


def test_buy_owned_property_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    prop = st.property_at(1)
    prop.assign(st.players[1].id)
    result = eng._do_buy(st.players[0], prop.id)
    assert not result.ok
    assert "已有主人" in result.reason


def test_buy_without_money_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.money = 10
    result = eng._do_buy(player, st.property_at(1).id)
    assert not result.ok
    assert "现金不足" in result.reason


def test_buy_consumes_discount_coupon(engine_no_ai):
    from src.game.player import STATUS_PURCHASE_DISCOUNT, StatusEffect

    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.add_status(StatusEffect(STATUS_PURCHASE_DISCOUNT, 1, payload={"percent": 50}))
    prop = st.property_at(1)
    price, _ = economy.buy_price(st, player, prop)
    assert price < prop.price
    eng._do_buy(player, prop.id)
    assert not player.has_status(STATUS_PURCHASE_DISCOUNT)


# ---------------------------------------------------------------- 升级

def test_upgrade_property(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    before = player.money
    cost, _ = economy.upgrade_cost(st, player, prop)

    result = eng._do_upgrade(player, prop.id)
    assert result.ok
    assert prop.level == 1
    assert player.money == before - cost
    assert player.stats["properties_upgraded"] == 1


def test_upgrade_max_level_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    while not prop.is_max_level:
        eng._do_upgrade(player, prop.id)
    result = eng._do_upgrade(player, prop.id)
    assert not result.ok
    assert "满级" in result.reason


def test_upgrade_others_property_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    prop = st.property_at(1)
    prop.assign(st.players[1].id)
    result = eng._do_upgrade(st.players[0], prop.id)
    assert not result.ok
    assert "不是你的地产" in result.reason


# ---------------------------------------------------------------- 租金

def test_rent_transfers_money(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(4)
    prop.assign(owner.id)
    payer_before, owner_before = payer.money, owner.money

    eng._pay_rent(payer, prop, owner)

    paid = payer_before - payer.money
    assert paid == owner.money - owner_before
    assert paid > 0
    assert payer.stats["rent_paid"] == paid
    assert owner.stats["rent_income"] == paid


def test_rent_free_status_used_once(engine_no_ai):
    from src.game.player import STATUS_FREE_RENT, StatusEffect

    eng = engine_no_ai
    st = eng.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(4)
    prop.assign(owner.id)
    payer.add_status(StatusEffect(STATUS_FREE_RENT, 1))
    before = payer.money

    eng._pay_rent(payer, prop, owner)

    assert payer.money == before
    assert not payer.has_status(STATUS_FREE_RENT)


def test_rent_double_consumed(engine_no_ai):
    from src.game.player import STATUS_RENT_DOUBLE_ONCE, StatusEffect

    eng = engine_no_ai
    st = eng.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(4)
    prop.assign(owner.id)
    owner.add_status(StatusEffect(STATUS_RENT_DOUBLE_ONCE, 1))

    eng._pay_rent(payer, prop, owner)
    assert not owner.has_status(STATUS_RENT_DOUBLE_ONCE)


# ---------------------------------------------------------------- 经过起点

def test_pass_start_bonus(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.position = 34
    st.move_steps = 4
    st.move_path = st.board.path(34, 4)
    before = player.money

    eng._finish_moving()

    expected = st.rules["pass_start_bonus"] + int(player.perk_value("pass_start_bonus"))
    assert player.money == before + expected
    assert player.stats["start_passes"] == 1
    assert player.position == 2


def test_land_on_start_gets_bonus(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.position = 35
    st.move_steps = 1
    st.move_path = [0]
    before = player.money

    eng._finish_moving()

    expected = st.rules["pass_start_bonus"] + int(player.perk_value("pass_start_bonus"))
    assert player.money == before + expected
    assert player.position == 0


def test_no_bonus_without_passing_start(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.position = 1
    st.move_steps = 3
    st.move_path = st.board.path(1, 3)
    before = player.money

    eng._finish_moving()

    assert player.money == before
    assert player.stats["start_passes"] == 0


# ---------------------------------------------------------------- 税收与奖金池

def test_tax_pays_and_feeds_pool(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    tile = st.board.tile(6)
    before = player.money
    pool_before = st.bonus_pool

    eng._resolve_tax(player, tile)

    assert player.money < before
    assert st.bonus_pool > pool_before
    assert player.stats["taxes_paid"] > 0


def test_bonus_pool_claim(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.bonus_pool = 3000
    before = player.money

    eng._resolve_bonus_pool(player, st.board.tile(18))

    assert player.money == before + 3000
    assert st.bonus_pool == 0


# ---------------------------------------------------------------- 破产

def test_bankruptcy_liquidates_first(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    payer = st.players[0]
    payer.money = 100
    prop = st.property_at(1)
    prop.assign(payer.id)
    # 让变卖所得足够还债
    amount = 100 + prop.sell_value - 10

    out = bankruptcy.pay(st, payer, amount, creditor_id=st.players[1].id)

    assert not out.bankrupt
    assert out.liquidated
    assert prop.owner_id is None
    assert payer.money >= 0


def test_bankruptcy_when_cannot_pay(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    payer = st.players[0]
    payer.money = 0
    creditor = st.players[1]
    prop = st.property_at(1)
    prop.assign(payer.id)

    out = bankruptcy.pay(st, payer, 999999, creditor_id=creditor.id)

    assert out.bankrupt
    assert payer.bankrupt
    assert payer.money == 0
    assert payer.bankrupt_order >= 1
    # 资产整体转移给债权人（而不是先变卖再归银行）
    assert prop.owner_id == creditor.id


def test_bankrupt_player_skipped_in_turn_order(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    st.players[1].bankrupt = True
    st.current_player_id = st.players[0].id
    eng._advance_player()
    assert st.current_player_id == st.players[2].id


def test_negative_cash_settled_at_turn_end(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.money = -800
    prop = st.property_at(1)
    prop.assign(player.id)

    eng._end_turn()

    assert player.money >= 0 or player.bankrupt


# ---------------------------------------------------------------- 回合推进

def test_turn_rotation(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    st.current_player_id = st.players[0].id
    order = [st.players[0].id]
    for _ in range(4):
        eng._advance_player()
        order.append(st.current_player_id)
    assert order[1] == st.players[1].id
    assert order[4] == st.players[0].id
    # 绕一圈后轮次 +1
    assert st.round_number == 2


def test_skip_turn_status(engine_no_ai):
    from src.game.player import STATUS_SKIP_TURN, StatusEffect

    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.add_status(StatusEffect(STATUS_SKIP_TURN, 1))
    st.current_player_id = player.id

    eng._begin_turn()

    assert not player.has_status(STATUS_SKIP_TURN)
    assert st.phase is GamePhase.TURN_END


def test_engine_runs_full_game(engine):
    """整局能自然结束并产生赢家。"""
    advance(engine, seconds=1200)
    st = engine.state
    assert st.game_over
    assert st.winner_id is not None
    assert st.player(st.winner_id) is not None
    # 正常结束只剩 1 人；若因到达轮数上限而结束，可能还有多人存活
    assert 1 <= len(st.active_players()) <= len(st.players)
