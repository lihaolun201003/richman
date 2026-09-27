"""债务处理、抵押、赎回、商店与新道具的规则测试。"""
from __future__ import annotations

import pytest

from src.game import bankruptcy, economy
from src.game.commands import Command, CommandType, DecisionKind
from src.game.ledger import Reason
from src.game.phases import GamePhase
from src.game.player import STATUS_CARD_BLOCKED, STATUS_PROTECTED, StatusEffect
from conftest import resolve


# ---------------------------------------------------------------- 抵押 / 赎回

def test_mortgage_requires_empty_land(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    prop.upgrade()
    result = eng._do_mortgage(player, prop.id)
    assert not result.ok
    assert "空地" in result.reason


def test_mortgage_gives_cash_and_blocks_rent(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, other = st.players[0], st.players[1]
    prop = st.property_at(1)
    prop.assign(player.id)
    before = player.money

    result = eng._do_mortgage(player, prop.id)

    assert result.ok
    assert prop.mortgaged
    assert player.money > before
    # 抵押后不能收租
    assert economy.rent_value(st, prop) == 0
    amount, detail = economy.compute_rent_payment(st, other, prop)
    assert amount == 0


def test_cannot_mortgage_twice(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    eng._do_mortgage(player, prop.id)
    result = eng._do_mortgage(player, prop.id)
    assert not result.ok


def test_redeem_pays_110_percent(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    eng._do_mortgage(player, prop.id)
    base = economy.mortgage_base(prop)
    cost = economy.redeem_cost(prop)

    assert cost == int(round(base * 1.1))
    player.money = cost
    result = eng._do_redeem(player, prop.id)

    assert result.ok
    assert not prop.mortgaged
    assert player.money == 0


def test_redeem_needs_enough_cash(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    eng._do_mortgage(player, prop.id)
    player.money = 10
    result = eng._do_redeem(player, prop.id)
    assert not result.ok


def test_downgrade_refunds_half(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    eng._do_upgrade(player, prop.id)
    paid = prop.upgrade_costs[0]
    before = player.money

    result = eng._do_downgrade(player, prop.id)

    assert result.ok
    assert prop.level == 0
    assert player.money == before + int(paid * 0.5)


def test_mortgaged_property_sells_cheaper(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    normal, _ = economy.sell_refund(st, player, prop)
    prop.mortgaged = True
    mortgaged, _ = economy.sell_refund(st, player, prop)
    assert mortgaged < normal


# ---------------------------------------------------------------- 债务处理

def _make_debt_scenario(eng, shortfall_target: int):
    """构造一个「自动变卖救不回、但抵押能救回」的债务场景。"""
    st = eng.state
    player = st.players[0]
    player.money = 0
    st.current_player_id = player.id
    # 一块空地（可抵押） + 一块高级地（只能卖）
    cheap = st.property_at(1)
    cheap.assign(player.id)
    return player, cheap


def test_debt_resolution_triggers_when_mortgage_can_save(engine_no_ai):
    """只要抵押能付清，就应该把决定权交给玩家（保留产权 or 变卖）。"""
    eng = engine_no_ai
    st = eng.state
    player, cheap = _make_debt_scenario(eng, 0)
    mortgage = economy.mortgage_value(st, player, cheap)[0]
    amount = int(mortgage * 0.8)      # 抵押够、现金不够

    out = bankruptcy.pay(st, player, amount, creditor_id=st.players[1].id)
    assert out.deferred, "应转入债务处理而不是直接变卖或破产"

    eng._start_debt_resolution(player, amount, st.players[1].id, "测试欠款")
    assert st.debt is not None
    assert st.phase is GamePhase.DEBT_RESOLUTION
    pd = st.pending_decision
    assert pd is not None and pd.kind == DecisionKind.DEBT_RESOLUTION
    assert any(o.command_type == CommandType.MORTGAGE_PROPERTY for o in pd.options)


def test_debt_resolution_pays_off_after_mortgage(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, cheap = _make_debt_scenario(eng, 0)
    mortgage = economy.mortgage_value(st, player, cheap)[0]
    amount = int(mortgage * 0.8)
    creditor = st.players[1]
    before_creditor = creditor.money

    eng._start_debt_resolution(player, amount, creditor.id, "测试欠款")
    result = resolve(eng, f"mortgage:{cheap.id}")

    assert result.ok
    assert cheap.mortgaged
    # 抵押款够付 → 流程自动结清
    assert st.debt is None
    assert creditor.money > before_creditor


def test_debt_resolution_can_declare_bankruptcy(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, cheap = _make_debt_scenario(eng, 0)
    creditor = st.players[1]
    eng._start_debt_resolution(player, 50000, creditor.id, "测试欠款")
    # 先让救不回来的场景成立：直接宣告破产
    result = resolve(eng, "declare")

    assert result.ok
    assert player.bankrupt
    assert st.debt is None
    assert cheap.owner_id == creditor.id or cheap.owner_id is None


def test_hopeless_debt_bankrupts_immediately(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.money = 0
    out = bankruptcy.pay(st, player, 10 ** 7, creditor_id=st.players[1].id)
    assert out.bankrupt
    assert player.bankrupt
    assert not out.deferred


def test_auto_liquidation_when_no_mortgage_option(engine_no_ai):
    """没有可抵押的空地时（例如地产已升级），仍然自动变卖，不打扰玩家。"""
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.money = 0
    prop = st.property_at(1)
    prop.assign(player.id)
    prop.level = 1                     # 有建筑 → 不能抵押
    refund = economy.sell_refund(st, player, prop)[0]

    out = bankruptcy.pay(st, player, int(refund * 0.8), creditor_id=st.players[1].id)

    assert not out.deferred
    assert not out.bankrupt
    assert out.liquidated
    assert player.money >= 0


# ---------------------------------------------------------------- 商店

def test_shop_offers_cards_and_charges(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    tile = st.board.tile(13)

    eng._start_shop(player, tile)

    assert st.shop is not None
    cards = st.shop["cards"]
    assert 1 <= len(cards) <= 3
    assert st.phase is GamePhase.SHOP
    pd = st.pending_decision
    assert pd is not None and pd.kind == DecisionKind.SHOP
    assert any(o.command_type == CommandType.LEAVE_SHOP for o in pd.options)

    buy_options = [o for o in pd.options if o.command_type == CommandType.BUY_SHOP_CARD]
    assert buy_options
    target = next((o for o in buy_options if o.enabled), None)
    if target is not None:
        cid = target.payload["card_id"]
        price = eng._shop_price(player, eng.card_registry.get(cid))
        before = player.money
        result = resolve(eng, target.id)
        assert result.ok
        assert player.money == before - price
        assert cid in player.cards
        assert st.shop is None


def test_shop_cannot_buy_twice(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    eng._start_shop(player, st.board.tile(13))
    option = next(o for o in st.pending_decision.options
                  if o.command_type == CommandType.BUY_SHOP_CARD and o.enabled)
    resolve(eng, option.id)
    # 商店已关闭，再买应被拒绝
    result = eng._do_buy_shop_card(player, option.payload["card_id"])
    assert not result.ok


def test_shop_blocked_when_inventory_full(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    limit = int(st.rules.get("max_cards_per_player", 5))
    player.cards = ["card_cash"] * limit
    eng._start_shop(player, st.board.tile(13))
    buys = [o for o in st.pending_decision.options
            if o.command_type == CommandType.BUY_SHOP_CARD]
    assert buys and all(not o.enabled for o in buys)


# ---------------------------------------------------------------- 新道具

def test_downgrade_opponent_property(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, target_player = st.players[0], st.players[1]
    st.current_player_id = player.id
    prop = st.property_at(4)
    prop.assign(target_player.id)
    prop.level = 2
    player.cards.append("card_downgrade")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_downgrade", "target": prop.id}))

    assert result.ok
    assert prop.level == 1


def test_downgrade_blocked_by_protection(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, target_player = st.players[0], st.players[1]
    st.current_player_id = player.id
    prop = st.property_at(4)
    prop.assign(target_player.id)
    prop.level = 2
    target_player.add_status(StatusEffect(STATUS_PROTECTED, 3))
    player.cards.append("card_downgrade")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_downgrade", "target": prop.id}))

    assert not result.ok
    assert "card_downgrade" in player.cards
    assert prop.level == 2


def test_swap_property_trades_ownership(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player, other = st.players[0], st.players[1]
    st.current_player_id = player.id
    mine = st.property_at(1)
    mine.assign(player.id)
    theirs = st.property_at(21)
    theirs.assign(other.id)
    player.cards.append("card_swap_property")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id,
                        {"card_id": "card_swap_property", "target": theirs.id}))

    assert result.ok
    assert theirs.owner_id == player.id
    assert mine.owner_id == other.id


def test_extra_turn_card(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.cards.append("card_roll_again")
    before = st.extra_turns
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_roll_again"}))
    assert result.ok
    assert st.extra_turns == before + 1


def test_bail_card_only_in_jail(engine_no_ai):
    from src.game import jail as jail_mod

    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.cards.append("card_bail")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_bail"}))
    assert not result.ok
    assert "card_bail" in player.cards

    jail_mod.put_in_jail(st, player)
    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_bail"}))
    assert result.ok
    assert not player.in_jail


def test_clear_negative_card(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.add_status(StatusEffect("skip_turn", 2))
    player.cards.append("card_clear")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_clear"}))

    assert result.ok
    assert not player.has_status("skip_turn")


def test_wealth_tax_card_takes_from_all(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    for p in st.players[1:]:
        p.money = 10000
    player.cards.append("card_wealth_tax")
    before = player.money

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_wealth_tax"}))

    assert result.ok
    assert player.money > before
    for p in st.players[1:]:
        assert p.money < 10000


def test_card_blocked_status_prevents_use(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.add_status(StatusEffect(STATUS_CARD_BLOCKED, 1))
    player.cards.append("card_cash")

    result = eng._use_card_command(
        player, Command(CommandType.USE_CARD, player.id, {"card_id": "card_cash"}))
    assert not result.ok
    assert "封锁" in result.reason


def test_event_immune_blocks_chance(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    player.add_status(StatusEffect("event_immune", 2))
    before = player.money

    eng._draw_chance(player, "好运街角", "fortune")

    assert not player.has_status("event_immune")
    assert player.money == before          # 没有获得也没有损失


# ---------------------------------------------------------------- 极性

def test_fortune_tile_only_draws_positive(engine_no_ai):
    from src.game import chance as chance_mod

    st = engine_no_ai.state
    reg = engine_no_ai.chance_registry
    for _ in range(120):
        card = chance_mod.draw(st, reg, chance_mod.Polarity.FORTUNE)
        assert card.polarity in (chance_mod.Polarity.FORTUNE, chance_mod.Polarity.NEUTRAL)


def test_disaster_tile_only_draws_negative(engine_no_ai):
    from src.game import chance as chance_mod

    st = engine_no_ai.state
    reg = engine_no_ai.chance_registry
    for _ in range(120):
        card = chance_mod.draw(st, reg, chance_mod.Polarity.DISASTER)
        assert card.polarity in (chance_mod.Polarity.DISASTER, chance_mod.Polarity.NEUTRAL)


# ---------------------------------------------------------------- 流水

def test_ledger_records_every_money_change(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    before_entries = len(st.ledger.entries)
    total_before = sum(p.money for p in st.players)

    prop = st.property_at(1)
    eng._do_buy(player, prop.id)
    eng._do_upgrade(player, prop.id)
    eng._do_mortgage(player, prop.id) if prop.level == 0 else None

    assert len(st.ledger.entries) > before_entries
    # 买地与升级是「玩家 → 银行」，玩家总资金减少
    total_after = sum(p.money for p in st.players)
    assert total_after < total_before
    # 银行净投放应当比开局时更少（支出流向银行）
    assert st.ledger.net_created() < 60000


def test_ledger_summary_categories(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    eng._do_buy(player, st.property_at(1).id)
    summary = st.ledger.player_summary(player.id)
    assert Reason.label(Reason.PROPERTY_PURCHASE) in summary["expense"]
    assert summary["total_expense"] > 0


def test_ledger_survives_serialization(engine_no_ai):
    from src.game import serializer as ser

    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    eng._do_buy(player, st.property_at(1).id)
    n = len(st.ledger.entries)

    restored = ser.state_from_snapshot(st.to_dict())
    assert len(restored.ledger.entries) == n
    assert restored.ledger.player_summary(player.id) == st.ledger.player_summary(player.id)
