"""不依赖 UI 的 AI 自动长局模拟。

用途：在没有人盯着屏幕的情况下，反复跑完整局，找出规则死锁、
状态非法、pending decision 卡死、资金异常等真实问题。

用法:
    python tools/simulate_game.py --games 100 --players 4
    python tools/simulate_game.py --games 20 --verbose
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controllers.ai import AIController  # noqa: E402
from src.game.phases import GamePhase  # noqa: E402
from src.game.setup import create_engine  # noqa: E402
from src.game.state import GameState  # noqa: E402

#: 单帧模拟步长（秒）。AI 有思考延迟，用较小的 dt 保证时序接近真实。
DT = 1.0 / 30.0
#: 单局最大模拟帧数，超过判定为死循环
MAX_FRAMES = 120000


class Anomalies:
    """收集一局中出现的所有异常。"""

    def __init__(self) -> None:
        self.counter: Counter[str] = Counter()
        self.details: list[str] = []

    def add(self, kind: str, detail: str = "") -> None:
        self.counter[kind] += 1
        if detail and len(self.details) < 40:
            self.details.append(f"[{kind}] {detail}")

    @property
    def total(self) -> int:
        return sum(self.counter.values())


def check_invariants(state: GameState, eng, anomalies: Anomalies) -> None:
    """每帧检查核心不变量。

    负现金只在「稳定阶段」检查——事件结算中途出现瞬时负数是设计内的，
    最终必须由债务流程清算掉。
    """
    board_count = state.board.tile_count
    stable = state.phase in (
        GamePhase.TURN_START, GamePhase.WAIT_ROLL,
        GamePhase.WAIT_DECISION, GamePhase.JAIL_DECISION,
    )

    for p in state.players:
        if not (0 <= p.position < board_count):
            anomalies.add("位置越界", f"{p.name} pos={p.position}")
        if p.money < 0 and stable and not p.bankrupt:
            anomalies.add("负现金", f"{p.name} money={p.money} phase={state.phase.value}")

    seen_owner: dict[str, str] = {}
    for key, prop in state.properties.items():
        if prop.owner_id:
            player = state.player(prop.owner_id)
            if player is None:
                anomalies.add("地产指向无效玩家", f"{prop.name} owner={prop.owner_id}")
            elif player.bankrupt:
                anomalies.add("地产属于已破产玩家", f"{prop.name} owner={player.name}")
        if key in seen_owner:
            anomalies.add("地产重复登记", key)
        seen_owner[key] = prop.owner_id or ""

    owners_of_tile: dict[int, str] = {}
    for prop in state.properties.values():
        if prop.owner_id:
            other = owners_of_tile.get(prop.tile_index)
            if other is not None and other != prop.owner_id:
                anomalies.add("同格多个主人", f"tile={prop.tile_index}")
            owners_of_tile[prop.tile_index] = prop.owner_id

    if state.pending_decision is not None:
        pd = state.pending_decision
        if state.player(pd.player_id) is None:
            anomalies.add("决策目标无效", pd.id)
        elif state.player(pd.player_id).bankrupt:
            anomalies.add("向已破产玩家提问", f"{pd.player_id} {pd.kind}")
        if not pd.options:
            anomalies.add("决策无选项", pd.kind)

    if state.phase is GamePhase.GAME_OVER and state.winner_id is None:
        anomalies.add("GameOver 无赢家", "")

    if state.game_over and state.current_player is None:
        anomalies.add("结束后无当前玩家", "")


def simulate_one(
    index: int,
    player_count: int,
    verbose: bool = False,
    max_rounds: int = 300,
) -> dict:
    seed = 1000 + index
    specs = [
        {
            "id": f"p{i + 1}",
            "name": f"AI{i + 1}",
            "character_id": ["char_ajin", "char_xiaoman", "char_laochen",
                             "char_nana", "char_tiedan", "char_drbo"][i % 6],
            "is_ai": True,
        }
        for i in range(player_count)
    ]
    eng = create_engine(specs, seed=seed, anim_speed=1.0, rules_override={"max_rounds": max_rounds})
    for p in eng.state.players:
        eng.bind_controller(p.id, AIController(p.id, seed=seed + p.slot, think_sec=0.25))

    anomalies = Anomalies()
    eng.start()

    frames = 0
    last_phase = eng.state.phase
    phase_changes = 0
    stuck_frames = 0
    decision_seen: set[str] = set()
    start = time.time()

    while not eng.state.game_over and frames < MAX_FRAMES:
        eng.update(DT)
        frames += 1
        if frames % 5 == 0:
            check_invariants(eng.state, eng, anomalies)

        if eng.state.phase is not last_phase:
            phase_changes += 1
            last_phase = eng.state.phase
            stuck_frames = 0
        else:
            stuck_frames += 1
            # 同一个决策停留过久 → 可能是死锁
            pd = eng.state.pending_decision
            if pd is not None:
                if pd.id not in decision_seen:
                    decision_seen.add(pd.id)
                    stuck_frames = 0
                elif stuck_frames > 30 * 120:
                    pd_desc = ""
                    if pd is not None:
                        pd_desc = (f" kind={pd.kind} props={pd.context.get('property_id')} "
                                   f"options={[(o.id, o.command_type, o.enabled) for o in pd.options]}")
                    anomalies.add("决策卡死超时", f"{pd.kind if pd else '-'} "
                                                  f"player={pd.player_id if pd else '-'} "
                                                  f"phase={eng.state.phase.value}{pd_desc}")
                    break
            elif eng.state.phase not in (
                GamePhase.WAIT_ROLL, GamePhase.WAIT_DECISION, GamePhase.JAIL_DECISION,
                GamePhase.MOVING, GamePhase.GAME_OVER,
            ) and stuck_frames > 30 * 40:
                anomalies.add("阶段卡死", f"phase={eng.state.phase.value}")
                break

    elapsed = time.time() - start
    st = eng.state

    if not st.game_over:
        anomalies.add("未正常结束", f"frames={frames} phase={st.phase.value} "
                                    f"round={st.round_number}")

    alives = st.active_players()
    return {
        "index": index,
        "seed": seed,
        "ok": st.game_over and st.winner_id is not None and anomalies.counter.get("未正常结束", 0) == 0,
        "rounds": st.round_number,
        "turns": st.turn_number,
        "frames": frames,
        "elapsed": elapsed,
        "phase_changes": phase_changes,
        "winner": st.player(st.winner_id).name if st.winner_id else None,
        "winner_asset": st.player_asset_value(st.winner_id) if st.winner_id else 0,
        "alive": len(alives),
        "bankrupt": sum(1 for p in st.players if p.bankrupt),
        "anomalies": anomalies.counter,
        "details": anomalies.details,
        "final_money": {p.name: p.money for p in st.players},
        "props_bought": sum(p.stats.get("properties_bought", 0) for p in st.players),
        "props_upgraded": sum(p.stats.get("properties_upgraded", 0) for p in st.players),
        "rent_paid": sum(p.stats.get("rent_paid", 0) for p in st.players),
        "cards_used": sum(p.stats.get("cards_used", 0) for p in st.players),
        "chance_hits": sum(p.stats.get("chance_events", 0) for p in st.players),
        "start_passes": sum(p.stats.get("start_passes", 0) for p in st.players),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 自动长局模拟")
    ap.add_argument("--games", type=int, default=20, help="模拟局数")
    ap.add_argument("--players", type=int, default=4, help="每局玩家数（2-6）")
    ap.add_argument("--max-rounds", type=int, default=200, help="单局最大轮数")
    ap.add_argument("--verbose", action="store_true", help="打印每局详情")
    args = ap.parse_args()

    print(f"开始模拟：{args.games} 局 × {args.players} 名 AI，最大 {args.max_rounds} 轮")
    print("-" * 78)

    results = []
    for i in range(args.games):
        r = simulate_one(i, args.players, verbose=args.verbose, max_rounds=args.max_rounds)
        results.append(r)
        flag = "OK " if r["ok"] else "BAD"
        print(
            f"[{flag}] #{r['index']:3d} seed={r['seed']} 轮={r['rounds']:4d} "
            f"回合={r['turns']:5d} 破产={r['bankrupt']} 赢家={r['winner']} "
            f"资产={r['winner_asset']:>9,} 用时={r['elapsed']:.1f}s"
            + (f" 异常={dict(r['anomalies'])}" if r["anomalies"] else "")
        )
        for d in r["details"][:6]:
            print(f"        {d}")

    print("-" * 78)
    ok_count = sum(1 for r in results if r["ok"])
    rounds = [r["rounds"] for r in results]
    elapsed = [r["elapsed"] for r in results]
    all_anom: Counter[str] = Counter()
    for r in results:
        all_anom.update(r["anomalies"])

    print("汇总")
    print(f"  正常结束      : {ok_count}/{len(results)}")
    print(f"  平均轮数      : {statistics.mean(rounds):.1f}")
    print(f"  最长/最短局   : {max(rounds)} / {min(rounds)}")
    print(f"  平均耗时      : {statistics.mean(elapsed):.2f}s")
    print(f"  平均每局回合  : {statistics.mean([r['turns'] for r in results]):.1f}")
    print(f"  平均破产人数  : {statistics.mean([r['bankrupt'] for r in results]):.2f}")
    print(f"  平均购地次数  : {statistics.mean([r['props_bought'] for r in results]):.1f}")
    print(f"  平均升级次数  : {statistics.mean([r['props_upgraded'] for r in results]):.1f}")
    print(f"  平均收租总额  : {statistics.mean([r['rent_paid'] for r in results]):,.0f}")
    print(f"  平均道具使用  : {statistics.mean([r['cards_used'] for r in results]):.1f}")
    print(f"  平均机遇触发  : {statistics.mean([r['chance_hits'] for r in results]):.1f}")
    print(f"  平均经过起点  : {statistics.mean([r['start_passes'] for r in results]):.1f}")
    print(f"  异常统计      : {dict(all_anom) if all_anom else '无'}")

    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
