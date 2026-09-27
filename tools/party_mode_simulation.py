"""聚会模式 / 节奏预设的模拟对比工具。

回答一个具体问题：**「5～6 人局到底要打多久，怎么才能压到 35～50 分钟？」**

它不是随便调个参数跑一跑，而是：
1. 用真实引擎、真实阶段时长、真实 AI 控制器跑完整局（不做任何规则简化）；
2. 记录引擎时钟（真实秒）、每回合耗时、AI 决策耗时、破产轮次、经济数据；
3. 按「真人决策比 AI 慢」的保守假设把 AI 局换算成**真人局估计时长**；
4. 把不同预设 / 不同人数 / 不同地图放在一起对比。

用法:
    python tools/party_mode_simulation.py                     # 默认 6 组对比
    python tools/party_mode_simulation.py --games 4            # 每组 4 局
    python tools/party_mode_simulation.py --presets party --players 5,6
    python tools/party_mode_simulation.py --out docs/reports/party_mode_report.md
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controllers.ai import AIController  # noqa: E402
from src.game.setup import create_engine, preset_by_key  # noqa: E402

#: 模拟步长：1/30 秒，与真实主循环一致，保证阶段时长误差 < 5%
DT = 1.0 / 30.0
#: 单局最大帧数（防止死循环；120000 帧 = 真实 66 分钟）
MAX_FRAMES = 200_000

#: 真人决策与浏览时间的保守假设（用于把 AI 局换算成真人局）
HUMAN_SEC_PER_DECISION = 2.5
HUMAN_BROWSE_SEC_PER_TURN = 1.2

CHARACTERS = ["char_ajin", "char_xiaoman", "char_laochen", "char_nana",
              "char_tiedan", "char_drbo"]


def run_one(preset: str, players: int, map_file: str, ai_instead_of_human: int,
            seed: int) -> dict[str, Any]:
    """跑一局并返回统计。

    ai_instead_of_human：把前 N 个座位当成「真人座位」在结算里换算（模拟时仍由 AI 操作）。
    """
    specs = [
        {
            "id": f"p{i + 1}",
            "name": f"玩家{i + 1}",
            "character_id": CHARACTERS[i % len(CHARACTERS)],
            "is_ai": True,
        }
        for i in range(players)
    ]
    engine = create_engine(specs, seed=seed, anim_speed=1.0, map_file=map_file,
                           preset=preset)
    think = float(engine.state.rules.get("ai_think_sec", 0.45) or 0.45)
    for p in engine.state.players:
        engine.bind_controller(p.id, AIController(p.id, seed=seed + p.slot,
                                                  think_sec=think))
    engine.start()

    frames = 0
    while not engine.state.game_over and frames < MAX_FRAMES:
        engine.update(DT)
        frames += 1

    st = engine.state
    a = st.analytics

    def estimate(human_seats: int) -> float:
        """把「全 AI 局」换算成「其中 human_seats 个座位是真人」的估计时长。

        换掉的是 AI 的思考等待，换成真人的决策 + 浏览时间；
        演出时间（骰子 / 移动 / 结算）一分不少，因为真人局里它照样要放。
        """
        ratio = max(0.0, min(1.0, human_seats / max(1, players)))
        turns_human = turns_total * ratio
        decisions_human = decisions_total * ratio
        extra = (decisions_human * HUMAN_SEC_PER_DECISION
                 + turns_human * HUMAN_BROWSE_SEC_PER_TURN)
        return max(0.0, st.clock - a.think_time_ai) + extra

    turns_total = sum(a.turns_by_player.values()) or st.turn_number
    decisions_total = a.decisions_ai + a.decisions_human
    avg_dec_per_turn = decisions_total / max(1, turns_total)

    return {
        "preset": preset,
        "preset_name": preset_by_key(preset).get("name", preset),
        "players": players,
        "map": map_file,
        "seed": seed,
        "rounds": st.round_number,
        "turns": st.turn_number,
        "finished": st.game_over,
        "winner": st.player(st.winner_id).name if st.winner_id else "",
        "engine_seconds": st.clock,                       # 引擎真实时钟（AI 局）
        "est_human_seconds": estimate(ai_instead_of_human),      # 指定真人数
        "est_human_all": estimate(players),                      # 全员真人
        "est_human_mixed": estimate(max(1, players // 2)),       # 半数真人
        "first_bankrupt_round": a.first_bankrupt_round,
        "last_bankrupt_round": a.last_bankrupt_round,
        "bankrupt_count": sum(1 for p in st.players if p.bankrupt),
        "think_ai": a.think_time_ai,
        "think_human": a.think_time_human,
        "move_time": a.move_time,
        "anim_time": a.anim_time,
        "decisions": decisions_total,
        "avg_dec_per_turn": avg_dec_per_turn,
        "turn_times": dict(a.turns_by_player),
        "rent_total": a.rent_total,
        "bought_total": a.bought_total,
        "upgrade_total": a.upgrade_total,
        "card_used_total": a.card_used_total,
        "sold_total": a.sold_total,
        "avg_cash": a.avg_cash,
        "final_cash": [p.money for p in st.players],
        "final_assets": [st.player_asset_value(p.id) for p in st.players],
        "milestones": list(a.milestones),
        "breakdown": a.time_breakdown(),
        "frames": frames,
        "wall_seconds": 0.0,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return {
        "games": len(rows),
        "rounds": statistics.mean(r["rounds"] for r in rows),
        "turns": statistics.mean(r["turns"] for r in rows),
        "engine_min": statistics.mean(r["engine_seconds"] for r in rows) / 60.0,
        "est_human_min": statistics.mean(r["est_human_seconds"] for r in rows) / 60.0,
        "est_all_min": statistics.mean(r["est_human_all"] for r in rows) / 60.0,
        "est_all_min_min": min(r["est_human_all"] for r in rows) / 60.0,
        "est_all_min_max": max(r["est_human_all"] for r in rows) / 60.0,
        "est_mixed_min": statistics.mean(r["est_human_mixed"] for r in rows) / 60.0,
        "finish_rate": sum(1 for r in rows if r["finished"]) / len(rows),
        "first_bankrupt": statistics.mean(
            [r["first_bankrupt_round"] for r in rows if r["first_bankrupt_round"]]) 
        if any(r["first_bankrupt_round"] for r in rows) else 0.0,
        "last_bankrupt": statistics.mean(
            [r["last_bankrupt_round"] for r in rows if r["last_bankrupt_round"]])
        if any(r["last_bankrupt_round"] for r in rows) else 0.0,
        "bankrupt": statistics.mean(r["bankrupt_count"] for r in rows),
        "think_ai_share": statistics.mean(
            (r["think_ai"] / r["engine_seconds"] * 100.0) if r["engine_seconds"] else 0
            for r in rows),
        "move_share": statistics.mean(
            (r["move_time"] / r["engine_seconds"] * 100.0) if r["engine_seconds"] else 0
            for r in rows),
        "anim_share": statistics.mean(
            (r["anim_time"] / r["engine_seconds"] * 100.0) if r["engine_seconds"] else 0
            for r in rows),
        "rent": statistics.mean(r["rent_total"] for r in rows),
        "bought": statistics.mean(r["bought_total"] for r in rows),
        "upgraded": statistics.mean(r["upgrade_total"] for r in rows),
        "cards": statistics.mean(r["card_used_total"] for r in rows),
        "sold": statistics.mean(r["sold_total"] for r in rows),
        "avg_cash": statistics.mean(r["avg_cash"] for r in rows),
        "dec_per_turn": statistics.mean(r["avg_dec_per_turn"] for r in rows),
        "winners": Counter(r["winner"] for r in rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="聚会模式 / 节奏预设模拟对比")
    ap.add_argument("--games", type=int, default=3, help="每个组合模拟多少局")
    ap.add_argument("--players", default="", help="只跑指定人数，例如 5,6")
    ap.add_argument("--presets", default="", help="只跑指定预设，例如 party,standard")
    ap.add_argument("--human-seats", type=int, default=1,
                    help="把多少个座位当作真人来估计时长")
    ap.add_argument("--out", default="", help="把 Markdown 报告写到这个路径")
    args = ap.parse_args()

    combos: list[tuple[str, int, str]] = []
    want_players = [int(x) for x in args.players.split(",") if x.strip()]
    want_presets = [x.strip() for x in args.presets.split(",") if x.strip()]
    if want_players and want_presets:
        for p in want_players:
            for pr in want_presets:
                combos.append((pr, p, "default_map.json"))
    else:
        combos = [
            ("standard", 4, "default_map.json"),
            ("quick", 4, "default_map.json"),
            ("standard", 5, "map_seaside.json"),
            ("party", 5, "map_seaside.json"),
            ("standard", 6, "map_seaside.json"),
            ("party", 6, "map_seaside.json"),
        ]

    print("=" * 92)
    print(f"节奏模拟：{len(combos)} 个组合 × {args.games} 局（全部座位由 AI 操作）")
    print(f"真人局估算假设：每次决策 {HUMAN_SEC_PER_DECISION}s，"
          f"每回合浏览 {HUMAN_BROWSE_SEC_PER_TURN}s，按 {args.human_seats} 个真人座位换算")
    print("=" * 92)

    all_rows: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for preset, players, map_file in combos:
        rows: list[dict[str, Any]] = []
        for g in range(args.games):
            seed = 4200 + g * 37 + players * 7
            t0 = time.time()
            r = run_one(preset, players, map_file, args.human_seats, seed)
            r["wall_seconds"] = time.time() - t0
            rows.append(r)
            print(f"  [{preset:8s} {players}人 {map_file:18s} #{g + 1}] "
                  f"轮={r['rounds']:4d} 回合={r['turns']:4d} "
                  f"引擎={r['engine_seconds'] / 60:5.1f}min "
                  f"估计真人={r['est_human_seconds'] / 60:5.1f}min "
                  f"首破产=第{r['first_bankrupt_round']:3d}轮 "
                  f"破产={r['bankrupt_count']} "
                  f"{'完成' if r['finished'] else '未结束!'}")
        all_rows[(preset, players, map_file)] = rows

    lines: list[str] = []
    lines.append("## 汇总对比\n")
    lines.append("口径说明：**全员真人**＝假设所有座位都由人操作（室友局）；"
                 "**半数真人**＝一半座位由 AI 代打；**纯 AI**＝引擎真实时钟。\n")
    lines.append("| 预设 | 人数 | 平均轮数 | 平均回合 | 纯 AI 时长 | 半数真人 | **全员真人** | "
                 "首破产轮 | 破产人数 | 破产/购地/升级/道具 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    summary_map: dict[tuple[str, int, str], dict[str, Any]] = {}
    for key, rows in all_rows.items():
        s = summarize(rows)
        summary_map[key] = s
        preset, players, map_file = key
        lines.append(
            f"| {preset} | {players} | "
            f"{s['rounds']:.1f} | {s['turns']:.0f} | {s['engine_min']:.1f} 分钟 | "
            f"{s['est_mixed_min']:.1f} 分钟 | "
            f"**{s['est_all_min']:.1f} 分钟**"
            f"（{s['est_all_min_min']:.0f}~{s['est_all_min_max']:.0f}） | "
            f"{s['first_bankrupt']:.0f} | {s['bankrupt']:.1f} | "
            f"{s['bankrupt']:.1f}/{s['bought']:.0f}/{s['upgraded']:.0f}/{s['cards']:.1f} |")
        print("-" * 92)
        print(f"{preset} / {players} 人 / {map_file}")
        print(f"  平均轮数 {s['rounds']:.1f}　平均回合 {s['turns']:.0f}　"
              f"完成率 {s['finish_rate'] * 100:.0f}%")
        print(f"  纯 AI 时长 {s['engine_min']:.1f} 分钟　"
              f"半数真人 {s['est_mixed_min']:.1f} 分钟　"
              f"全员真人 {s['est_all_min']:.1f} 分钟")
        print(f"  时间去向：AI 决策 {s['think_ai_share']:.0f}%　"
              f"移动 {s['move_share']:.0f}%　演出 {s['anim_share']:.0f}%")
        print(f"  首破产 第 {s['first_bankrupt']:.0f} 轮　"
              f"末破产 第 {s['last_bankrupt']:.0f} 轮　平均破产 {s['bankrupt']:.1f} 人")
        print(f"  每回合决策数 {s['dec_per_turn']:.1f}　"
              f"租金/购地/升级/道具 {s['rent']:.0f}/{s['bought']:.0f}/"
              f"{s['upgraded']:.0f}/{s['cards']:.1f}　平均现金 {s['avg_cash']:,.0f}")
        print(f"  胜者分布 {dict(s['winners'])}")

    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("# 节奏预设模拟报告\n\n")
            f.write(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- 每组局数：{args.games}（全部座位由 AI 操作，真实引擎与真实阶段时长）\n")
            f.write(f"- 真人换算：每次决策 {HUMAN_SEC_PER_DECISION}s + "
                    f"每回合浏览 {HUMAN_BROWSE_SEC_PER_TURN}s\n")
            f.write("- 「纯 AI 时长」= 引擎时钟累计的真实秒数（含全部演出，不含真人思考）\n\n")
            f.write("\n".join(lines))
            f.write("\n\n## 逐局明细\n\n")
            f.write("| 预设 | 人数 | 局 | 轮 | 回合 | 纯AI(s) | 全员真人(s) | "
                    "首破产轮 | 末破产轮 | 破产 | 租金 | 升级 | 道具 | 胜者 |\n")
            f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
                    "--- | --- | --- | --- |\n")
            for key, rows in all_rows.items():
                preset, players, map_file = key
                for i, r in enumerate(rows, 1):
                    f.write(f"| {preset} | {players} | {i} | {r['rounds']} | "
                            f"{r['turns']} | {r['engine_seconds']:.0f} | "
                            f"{r['est_human_all']:.0f} | "
                            f"{r['first_bankrupt_round']} | {r['last_bankrupt_round']} | "
                            f"{r['bankrupt_count']} | {r['rent_total']} | "
                            f"{r['upgrade_total']} | {r['card_used_total']} | "
                            f"{r['winner']} |\n")
        print("=" * 92)
        print(f"报告已写入 {out}")

    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
