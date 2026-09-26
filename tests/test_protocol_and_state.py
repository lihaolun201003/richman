"""序列化、协议、决策 ID、revision、非法命令拒绝、Host 权威性测试。"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from src.game import serializer as ser
from src.game.commands import Command, CommandType, DecisionKind, PendingDecision
from src.game.phases import GamePhase
from src.game.setup import create_engine, engine_from_state
from src.network import protocol as proto
from src.network.transport import MessageDecoder
from conftest import advance, resolve, run_until_decision, submit


# ---------------------------------------------------------------- 序列化

def test_state_roundtrip(engine):
    st = engine.state
    data = st.to_dict()
    text = json.dumps(data, ensure_ascii=False)
    restored = ser.state_from_snapshot(json.loads(text))

    assert restored.revision == st.revision
    assert restored.phase == st.phase
    assert restored.round_number == st.round_number
    assert restored.current_player_id == st.current_player_id
    assert restored.seed == st.seed
    assert restored.rng_counter == st.rng_counter
    assert len(restored.players) == len(st.players)
    assert len(restored.properties) == len(st.properties)
    assert restored.canonical_hash() == st.canonical_hash()


def test_full_state_survives_json(engine):
    """跑几十回合后再序列化，确保没有不可 JSON 化的字段。"""
    advance(engine, seconds=120)
    doc = engine.state.to_dict()
    text = json.dumps(doc, ensure_ascii=False)
    assert len(text) > 1000
    restored = ser.state_from_snapshot(json.loads(text))
    assert restored.canonical_hash() == engine.state.canonical_hash()


def test_save_and_load_file(engine):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "save.json")
        ser.save_game(engine.state, path, {"note": "test"})
        assert os.path.isfile(path)

        state, meta = ser.load_game(path)
        assert meta["note"] == "test"
        assert state.canonical_hash() == engine.state.canonical_hash()

        # 恢复出来的引擎能继续运行
        eng2 = engine_from_state(state)
        from src.controllers.ai import AIController

        for p in eng2.state.players:
            eng2.bind_controller(p.id, AIController(p.id, seed=1, think_sec=0.0))
        before = eng2.state.revision
        for _ in range(120):
            eng2.update(1 / 30)
        assert eng2.state.revision > before


def test_save_file_version_guard(engine):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "future.json")
        ser.save_game(engine.state, path)
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        doc["save_version"] = ser.SAVE_VERSION + 5
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        with pytest.raises(ser.SnapshotError):
            ser.load_game(path)


def test_revision_only_increases(engine):
    st = engine.state
    seen = []
    for _ in range(60):
        engine.update(1 / 30)
        seen.append(st.revision)
    assert seen == sorted(seen)
    assert seen[-1] > seen[0]


def test_snapshot_accept_rule(engine):
    st = engine.state
    snap = st.to_dict()
    assert ser.should_accept_snapshot(-1, snap)
    assert not ser.should_accept_snapshot(st.revision, snap)
    assert not ser.should_accept_snapshot(st.revision + 10, snap)


# ---------------------------------------------------------------- 协议

def test_protocol_encode_decode_roundtrip():
    payload = {"command": {"type": "ROLL_DICE", "player_id": "p1"}}
    data = proto.encode(proto.MessageType.COMMAND, payload)
    assert len(data) > 4

    decoder = MessageDecoder()
    msgs = decoder.feed(data)
    assert len(msgs) == 1
    assert msgs[0]["type"] == proto.MessageType.COMMAND
    assert msgs[0]["protocol_version"] == proto.PROTOCOL_VERSION
    assert msgs[0]["command"]["player_id"] == "p1"


def test_protocol_handles_fragmented_stream():
    data = proto.encode(proto.MessageType.PING, {"n": 1}) + proto.encode(
        proto.MessageType.PONG, {"n": 2})
    decoder = MessageDecoder()
    out = []
    # 每次喂 3 字节，模拟 TCP 拆包
    for i in range(0, len(data), 3):
        out.extend(decoder.feed(data[i:i + 3]))
    assert [m["type"] for m in out] == [proto.MessageType.PING, proto.MessageType.PONG]


def test_protocol_handles_multiple_in_one_packet():
    data = b"".join(proto.encode(proto.MessageType.PING, {"n": i}) for i in range(5))
    decoder = MessageDecoder()
    msgs = decoder.feed(data)
    assert len(msgs) == 5
    assert [m["n"] for m in msgs] == [0, 1, 2, 3, 4]


def test_protocol_rejects_bad_length():
    import struct

    decoder = MessageDecoder()
    bad = struct.pack(">I", 0) + b"x"
    with pytest.raises(ValueError):
        decoder.feed(bad)


def test_protocol_version_check():
    ok, _ = proto.check_version({"protocol_version": proto.PROTOCOL_VERSION})
    assert ok
    bad, reason = proto.check_version({"protocol_version": proto.PROTOCOL_VERSION + 1})
    assert not bad and reason
    missing, reason2 = proto.check_version({})
    assert not missing and reason2


def test_protocol_utf8_chinese():
    data = proto.encode(proto.MessageType.GAME_EVENT, {"event": {"message": "张三购买了星河商街"}})
    msgs = MessageDecoder().feed(data)
    assert msgs[0]["event"]["message"] == "张三购买了星河商街"


# ---------------------------------------------------------------- 决策与非法命令

def test_decision_has_unique_id(engine):
    st = engine.state
    advance(engine, seconds=2)
    ids = set()
    for _ in range(400):
        if st.pending_decision is not None:
            ids.add(st.pending_decision.id)
            resolve(engine, st.pending_decision.options[0].id)
        engine.update(1 / 30)
        if st.game_over or len(ids) > 6:
            break
    assert len(ids) > 3


def test_stale_decision_id_rejected(engine):
    st = engine.state
    run_until_decision(engine)
    pd = st.pending_decision
    assert pd is not None
    result = engine.submit_command(Command(
        ctype=CommandType.RESOLVE_DECISION,
        player_id=pd.player_id,
        payload={"option_id": pd.options[0].id},
        decision_id="d999999",       # 过期 ID
    ))
    assert not result.ok
    assert "过期" in result.reason
    # 决策必须还在，不能被吃掉
    assert st.pending_decision is not None


def test_decision_not_mine_rejected(engine):
    st = engine.state
    run_until_decision(engine)
    pd = st.pending_decision
    other = next(p for p in st.players if p.id != pd.player_id)
    result = engine.submit_command(Command(
        ctype=CommandType.RESOLVE_DECISION,
        player_id=other.id,
        payload={"option_id": pd.options[0].id},
        decision_id=pd.id,
    ))
    assert not result.ok
    assert "不是你的决策" in result.reason


def test_invalid_option_rejected(engine):
    st = engine.state
    run_until_decision(engine)
    pd = st.pending_decision
    result = engine.submit_command(Command(
        ctype=CommandType.RESOLVE_DECISION,
        player_id=pd.player_id,
        payload={"option_id": "no_such_option"},
        decision_id=pd.id,
    ))
    assert not result.ok
    assert st.pending_decision is not None


def test_disabled_option_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.money = 10
    st.current_player_id = player.id
    prop = st.property_at(1)
    eng._offer_purchase(player, prop)
    pd = st.pending_decision
    assert pd is not None
    buy = pd.option("buy")
    assert buy is not None and not buy.enabled

    result = resolve(eng, "buy")
    assert not result.ok
    assert st.pending_decision is not None


def test_duplicate_command_ignored(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    cmd = Command(CommandType.END_TURN, player.id, {}, command_id="fixed-id-001")
    first = eng.submit_command(cmd)
    second = eng.submit_command(Command(CommandType.END_TURN, player.id, {},
                                        command_id="fixed-id-001"))
    assert second.ok is False
    assert "重复" in second.reason


def test_command_wrong_phase_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    st.current_player_id = player.id
    st.set_phase(GamePhase.MOVING, 1.0)
    result = eng.submit_command(Command(CommandType.BUY_PROPERTY, player.id,
                                        {"property_id": "p01"}))
    assert not result.ok


def test_bankrupt_player_cannot_act(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    player.bankrupt = True
    result = eng.submit_command(Command(CommandType.SURRENDER, player.id))
    assert not result.ok
    assert "破产" in result.reason


def test_unknown_command_rejected(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    result = eng.submit_command(Command("NOT_A_REAL_COMMAND", st.players[0].id))
    assert not result.ok


def test_decision_phase_never_gets_stuck(engine):
    """长局中不应出现「阶段在等决策、但决策为空」的死锁。"""
    st = engine.state
    stuck = 0
    for i in range(6000):
        engine.update(1 / 30)
        if st.game_over:
            break
        if st.phase in (GamePhase.WAIT_ROLL, GamePhase.WAIT_DECISION,
                        GamePhase.JAIL_DECISION) and st.pending_decision is None:
            stuck += 1
    assert stuck == 0, f"出现 {stuck} 次决策阶段缺少决策"


def test_surrender_ends_participation(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    player = st.players[0]
    prop = st.property_at(1)
    prop.assign(player.id)
    result = eng.submit_command(Command(CommandType.SURRENDER, player.id))
    assert result.ok
    assert player.bankrupt
    assert prop.owner_id is None


def test_only_one_winner_after_others_bankrupt(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    for p in st.players[1:]:
        eng.submit_command(Command(CommandType.SURRENDER, p.id))
    assert st.game_over
    assert st.winner_id == st.players[0].id


def test_declaration_of_game_over_sets_phase(engine_no_ai):
    eng = engine_no_ai
    st = eng.state
    for p in st.players[1:]:
        eng._surrender(p)
    assert st.game_over
    assert st.phase is GamePhase.GAME_OVER
    assert st.ended_at is not None


# ---------------------------------------------------------------- AI

def test_ai_never_sends_illegal_command(engine):
    """AI 跑完整局，引擎不应记录任何命令异常。"""
    st = engine.state
    advance(engine, seconds=1500)
    assert st.game_over
    # 日志里不应有"命令执行异常"
    bad = [e for e in st.event_log if "异常" in e.message]
    assert not bad, [e.message for e in bad[:5]]


def test_ai_buys_properties(engine):
    st = engine.state
    advance(engine, seconds=400)
    bought = sum(p.stats.get("properties_bought", 0) for p in st.players)
    assert bought > 3, "AI 应该会买地"


def test_ai_upgrades_properties(engine):
    st = engine.state
    advance(engine, seconds=600)
    upgraded = sum(p.stats.get("properties_upgraded", 0) for p in st.players)
    assert upgraded > 2, "AI 应该会升级地产"


def test_ai_uses_cards(engine):
    st = engine.state
    advance(engine, seconds=800)
    used = sum(p.stats.get("cards_used", 0) for p in st.players)
    assert used > 0, "AI 应该会用道具"


def test_ai_targets_are_valid(engine):
    """AI 选目标时不会选中自己或无效目标。"""
    st = engine.state
    for _ in range(4000):
        engine.update(1 / 30)
        if st.game_over:
            break
        pd = st.pending_decision
        if pd is None:
            continue
        for opt in pd.options:
            if not opt.enabled:
                continue
            tid = opt.payload.get("target_id")
            if tid is not None:
                assert st.player(str(tid)) is not None
                assert str(tid) != pd.player_id
