"""经济规则测试：购地价、租金、升级、税收、垄断、角色加成。"""
from __future__ import annotations

import pytest

from src.game import economy
from src.game.player import (
    STATUS_FREE_RENT,
    STATUS_PURCHASE_DISCOUNT,
    STATUS_RENT_DISCOUNT_ONCE,
    STATUS_RENT_DOUBLE_ONCE,
    STATUS_UPGRADE_DISCOUNT,
    StatusEffect,
)
from src.game.tile import TileType


def test_property_definitions_loaded(engine):
    st = engine.state
    assert len(st.properties) == 24
    assert all(p.owner_id is None for p in st.properties.values())
    # 每个可购买格都应有对应地产
    for tile in st.board.purchasable_tiles():
        assert st.property_at(tile.index) is not None


def test_buy_price_base(engine):
    st = engine.state
    player = st.players[0]
    prop = st.property_at(1)
    price, detail = economy.buy_price(st, player, prop)
    assert price == prop.price
    assert detail["final"] == prop.price


def test_buy_price_with_character_discount(engine):
    st = engine.state
    # 老陈：购地 -8%
    player = st.players[0]
    player.perk = {"kind": "buy_discount", "value": 0.08}
    prop = st.property_at(1)
    price, detail = economy.buy_price(st, player, prop)
    assert price < prop.price
    assert price == pytest.approx(int(round(prop.price * 0.92)), abs=1)


def test_buy_price_with_coupon(engine):
    st = engine.state
    player = st.players[0]
    player.perk = {}
    player.add_status(StatusEffect(STATUS_PURCHASE_DISCOUNT, 1, payload={"percent": 50}))
    prop = st.property_at(1)
    price, detail = economy.buy_price(st, player, prop)
    assert price == prop.price // 2


def test_upgrade_cost_scales(engine):
    st = engine.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    cost0, _ = economy.upgrade_cost(st, player, prop)
    prop.upgrade()
    cost1, _ = economy.upgrade_cost(st, player, prop)
    assert cost1 > cost0


def test_upgrade_discount_character(engine):
    st = engine.state
    player = st.players[0]
    player.perk = {"kind": "upgrade_discount", "value": 0.10}
    prop = st.property_at(1)
    prop.assign(player.id)
    plain, _ = economy.upgrade_cost(st, player, prop)
    player.perk = {}
    full, _ = economy.upgrade_cost(st, player, prop)
    assert plain < full


def test_rent_table_increases(engine):
    st = engine.state
    prop = st.property_at(1)
    assert prop.rent_table == sorted(prop.rent_table)
    assert prop.base_rent == prop.rent_table[0]
    prop.assign("p1")
    prop.upgrade()
    assert prop.base_rent > prop.rent_table[0]


def test_district_monopoly_doubles_rent(engine):
    st = engine.state
    player = st.players[0]
    # 东城片区 = tile 1, 2
    p1 = st.property_at(1)
    p2 = st.property_at(2)
    owner = st.players[0].id
    p1.assign(owner)
    base = economy.rent_value(st, p1)
    assert base == p1.base_rent
    p2.assign(owner)
    doubled = economy.rent_value(st, p1)
    assert doubled == int(round(p1.base_rent * st.district_bonus))
    assert doubled == base * 2


def test_rent_double_status(engine):
    st = engine.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(1)
    prop.assign(owner.id)
    base_amount, _ = economy.compute_rent_payment(st, payer, prop)

    owner.add_status(StatusEffect(STATUS_RENT_DOUBLE_ONCE, 1))
    doubled, detail = economy.compute_rent_payment(st, payer, prop)
    assert doubled == base_amount * 2
    assert detail["doubled"] is True


def test_rent_half_status(engine):
    st = engine.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(1)
    prop.assign(owner.id)
    base_amount, _ = economy.compute_rent_payment(st, payer, prop)

    payer.add_status(StatusEffect(STATUS_RENT_DISCOUNT_ONCE, 1))
    half, detail = economy.compute_rent_payment(st, payer, prop)
    assert half == pytest.approx(base_amount // 2, abs=1)
    assert detail["halved"] is True


def test_free_rent_status(engine):
    st = engine.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(1)
    prop.assign(owner.id)
    payer.add_status(StatusEffect(STATUS_FREE_RENT, 1))
    amount, detail = economy.compute_rent_payment(st, payer, prop)
    assert amount == 0
    assert detail["free"] is True


def test_rent_income_bonus_character(engine):
    st = engine.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(1)
    prop.assign(owner.id)
    plain, _ = economy.compute_rent_payment(st, payer, prop)
    owner.perk = {"kind": "rent_income_bonus", "value": 0.12}
    boosted, _ = economy.compute_rent_payment(st, payer, prop)
    assert boosted > plain


def test_rent_payment_discount_character(engine):
    st = engine.state
    payer, owner = st.players[1], st.players[0]
    prop = st.property_at(1)
    prop.assign(owner.id)
    plain, _ = economy.compute_rent_payment(st, payer, prop)
    payer.perk = {"kind": "rent_payment_discount", "value": 0.10}
    cheaper, _ = economy.compute_rent_payment(st, payer, prop)
    assert cheaper < plain


def test_tax_amounts(engine):
    st = engine.state
    player = st.players[0]
    fixed, label = economy.tax_amount(st, player, TileType.TAX, 6)
    assert fixed == st.rules["tax_fixed"]
    assert "营业税" in label

    ratio, label2 = economy.tax_amount(st, player, TileType.TAX, 26)
    assert ratio > 0
    assert "奢侈" in label2


def test_sell_value_below_investment(engine):
    st = engine.state
    prop = st.property_at(4)
    prop.assign("p1")
    assert prop.sell_value < prop.total_invested
    prop.upgrade()
    assert prop.sell_value > prop.price
