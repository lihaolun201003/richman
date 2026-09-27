"""经济平衡分析：自动跑多组 AI 对局，输出可读的平衡报告。

关注的是「真实玩法」而不是测试数量：
一局打多久、钱从哪来、破产为什么发生、领先者会不会滚雪球。

用法:
    python tools/balance_simulation.py
    python tools/balance_simulation.py --games 30 --out docs/reports/balance_report_v01.md
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controllers.ai import AIController  # noqa: E402
from src.game import economy  # noqa: E402
from src.game.setup import create_engine, load_board, load_properties, preset_list  # noqa: E402

DT = 1.0 / 30.0
MAX_FRAMES = 200000

#: 每回合的大致真实耗时（秒），用于把轮数换算成「分钟」。
#: 由阶段时长（骰子 1.15s + 移动 ~1.2s + 结算 ~1.1s + 回合收尾 0.5s）
#: 加上 AI 思考 0.45s 估算得到；真人玩家的思考时间因人而异，这里给一个保守值。
SECONDS_PER_TURN = 4.0

CHARS = ["char_ajin", "char_xiaoman", "char_laochen", "char_nana",
         "char_tiedan", "char_drbo", "char_afei", "char_panghu",
         "char_zhou", "char_azhen", "char_qiang", "char_xiaozhao"]


def run_one(index: int, player_count: int, map_file: str, preset: str) -> dict:
    seed = 5000 + index
    specs = [{
        "id": f"p{i + 1}",
        "name": f"AI{i + 1}",
        "character_id": CHARS[i % len(CHARS)],
        "is_ai": True,
    } for i in range(player_count)]

    eng = create_engine(specs, seed=seed, anim_speed=10.0,
                        map_file=map_file, preset=preset)
    for p in eng.state.players:
        eng.bind_controller(p.id, AIController(p.id, seed=seed + p.slot, think_sec=0.0))
    eng.start()

    first_bankrupt_round = None
    frames = 0
    while not eng.state.game_over and frames < MAX_FRAMES:
        before = eng.state.bankrupt_counter
        eng.update(DT)
        frames += 1
        if first_bankrupt_round is None and eng.state.bankrupt_counter > before:
            first_bankrupt_round = eng.state.round_number

    st = eng.state
    props = list(st.properties.values())
    bought = sum(1 for p in props if p.owner_id is not None)
    upgraded = sum(p.level for p in props)

    # 资金流统计
    ledger = st.ledger
    income_by_reason: dict[str, int] = defaultdict(int)
    expense_by_reason: dict[str, int] = defaultdict(int)
    for e in ledger.entries:
        bucket = income_by_reason if e.amount > 0 else expense_by_reason
        bucket[e.reason] += abs(e.amount)

    rent_total = income_by_reason.get("租金", 0)
    chance_gain = income_by_reason.get("机遇事件", 0)
    chance_loss = expense_by_reason.get("机遇事件", 0)
    card_gain = income_by_reason.get("道具", 0)
    card_loss = expense_by_reason.get("道具", 0)
    start_reward = income_by_reason.get("经过起点", 0)
    grand_income = sum(income_by_reason.values()) or 1

    # 雪球指标：冠军资产 / 其余玩家平均资产
    winner = st.player(st.winner_id) if st.winner_id else None
    if winner is not None:
        winner_asset = st.player_asset_value(winner.id)
        # 只跟「没破产的对手」比：破产玩家资产恒为 0，
        # 拿他们当分母会得到几万倍的假数据。
        others = [st.player_asset_value(p.id) for p in st.players
                  if p.id != winner.id and not p.bankrupt]
        base = statistics.mean(others) if others else 0
        snowball = (winner_asset / base) if base > 0 else 1.0
        snowball = min(snowball, 99.0)
    else:
        winner_asset, snowball = 0, 1.0

    # 破产原因：取该玩家「最后一笔支出」的类别——
    # 累计最大支出往往只是买地，不代表真正压垮他的那笔钱。
    bankruptcy_reasons: Counter = Counter()
    for p in st.players:
        if not p.bankrupt:
            continue
        last = next((e for e in reversed(ledger.entries)
                     if e.player_id == p.id and e.amount < 0), None)
        bankruptcy_reasons[last.reason if last else "未知"] += 1

    return {
        "ok": st.game_over and st.winner_id is not None,
        "rounds": st.round_number,
        "turns": st.turn_number,
        "first_bankrupt": first_bankrupt_round or st.round_number,
        "bankrupt": sum(1 for p in st.players if p.bankrupt),
        "winner_asset": winner_asset,
        "snowball": snowball,
        "props_total": len(props),
        "props_bought": bought,
        "prop_levels": upgraded,
        "avg_price": int(statistics.mean([p.price for p in props])) if props else 0,
        "rent_share": rent_total / grand_income,
        "chance_net": chance_gain - chance_loss,
        "chance_share": (chance_gain + chance_loss) / grand_income,
        "card_net": card_gain - card_loss,
        "card_share": (card_gain + card_loss) / grand_income,
        "start_share": start_reward / grand_income,
        "mortgage_count": sum(1 for e in ledger.entries if e.category == "mortgage"),
        "shop_count": sum(1 for e in ledger.entries if e.category == "shop"),
        "redeem_count": sum(1 for e in ledger.entries if e.category == "redeem"),
        "bank_net": ledger.net_created(),
        "income_top": dict(sorted(income_by_reason.items(),
                                  key=lambda kv: -kv[1])[:6]),
        "expense_top": dict(sorted(expense_by_reason.items(),
                                   key=lambda kv: -kv[1])[:6]),
        "bankruptcy_reasons": dict(bankruptcy_reasons),
        "cards_used": sum(p.stats.get("cards_used", 0) for p in st.players),
    }


def fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def estimate_minutes(rounds: int, players: int) -> float:
    return rounds * players * SECONDS_PER_TURN / 60.0


def main() -> int:
    ap = argparse.ArgumentParser(description="经济平衡分析")
    ap.add_argument("--games", type=int, default=20, help="每组局数")
    ap.add_argument("--out", default="docs/reports/balance_report_v01.md")
    ap.add_argument("--quick", action="store_true", help="只跑 4 人局")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    presets = preset_list()
    standard = next((p for p in presets if p["key"] == "standard"), presets[0])
    groups = [(2, "default_map.json"), (3, "default_map.json"), (4, "default_map.json"),
              (6, "default_map.json")]
    if not args.quick:
        groups.append((4, "map_seaside.json"))

    print(f"平衡分析：每组 {args.games} 局")
    results: dict[str, list[dict]] = {}
    for players, map_file in groups:
        board = load_board(map_file)
        label = f"{players} 人 · {board.name}"
        rows = []
        for i in range(args.games):
            rows.append(run_one(i, players, map_file, standard["key"]))
        results[label] = rows
        ok = sum(1 for r in rows if r["ok"])
        print(f"  {label}: {ok}/{len(rows)} 完成，"
              f"平均 {statistics.mean([r['rounds'] for r in rows]):.0f} 轮")

    # ---------------- 生成报告
    lines: list[str] = []
    lines.append("# Richman 经济平衡报告（v0.2）\n")
    lines.append(f"- 每组样本：{args.games} 局，全部由 AI 自动对打")
    lines.append(f"- 规则预设：{standard['name']}")
    lines.append(f"- 时间估算基准：每回合约 {SECONDS_PER_TURN:.1f} 秒"
                 "（含骰子 / 移动 / 结算动画与 AI 思考）\n")

    lines.append("## 总体\n")
    lines.append("| 场景 | 完成率 | 平均轮数 | 估算时长 | 首次破产 | 平均破产人数 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for label, rows in results.items():
        players = int(label.split(" ")[0])
        ok = sum(1 for r in rows if r["ok"])
        rounds = statistics.mean([r["rounds"] for r in rows])
        minutes = estimate_minutes(rounds, players)
        first = statistics.mean([r["first_bankrupt"] for r in rows])
        bankrupt = statistics.mean([r["bankrupt"] for r in rows])
        lines.append(f"| {label} | {ok}/{len(rows)} | {rounds:.0f} | "
                     f"{minutes:.0f} 分钟 | 第 {first:.0f} 轮 | {bankrupt:.1f} 人 |")

    lines.append("\n## 目标区间\n")
    lines.append("- 典型 4 人局目标：**25～50 分钟**")
    four = results.get("4 人 · 城市之光")
    if four:
        rounds = statistics.mean([r["rounds"] for r in four])
        minutes = estimate_minutes(rounds, 4)
        verdict = "在目标区间内" if 25 <= minutes <= 50 else (
            "偏短" if minutes < 25 else "偏长")
        lines.append(f"- 实测 4 人局：{rounds:.0f} 轮 ≈ **{minutes:.0f} 分钟** → {verdict}")
        longest = max(r["rounds"] for r in four)
        lines.append(f"- 最长一局：{longest} 轮 ≈ {estimate_minutes(longest, 4):.0f} 分钟")

    lines.append("\n## 地产与升级\n")
    lines.append("| 场景 | 地产总数 | 卖出（持有）| 持有率 | 平均等级 | 平均地价 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for label, rows in results.items():
        total = statistics.mean([r["props_total"] for r in rows])
        bought = statistics.mean([r["props_bought"] for r in rows])
        levels = statistics.mean([r["prop_levels"] for r in rows])
        avg_price = statistics.mean([r["avg_price"] for r in rows])
        lines.append(f"| {label} | {total:.0f} | {bought:.0f} | "
                     f"{bought / max(1.0, total) * 100:.0f}% | "
                     f"{levels / max(1.0, bought):.2f} | {avg_price:,.0f} |")

    lines.append("\n## 资金流构成（占全部收入的比例）\n")
    lines.append("| 场景 | 租金 | 经过起点 | 机遇事件 | 道具 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for label, rows in results.items():
        lines.append(
            f"| {label} | {fmt_pct(statistics.mean([r['rent_share'] for r in rows]))} | "
            f"{fmt_pct(statistics.mean([r['start_share'] for r in rows]))} | "
            f"{fmt_pct(statistics.mean([r['chance_share'] for r in rows]))} | "
            f"{fmt_pct(statistics.mean([r['card_share'] for r in rows]))} |")

    lines.append("\n## 新系统的实际使用情况\n")
    lines.append("| 场景 | 抵押次数 | 赎回次数 | 商店购买 | 道具使用 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for label, rows in results.items():
        lines.append(
            f"| {label} | {statistics.mean([r['mortgage_count'] for r in rows]):.1f} | "
            f"{statistics.mean([r['redeem_count'] for r in rows]):.1f} | "
            f"{statistics.mean([r['shop_count'] for r in rows]):.1f} | "
            f"{statistics.mean([r['cards_used'] for r in rows]):.1f} |")

    lines.append("\n## 雪球效应\n")
    lines.append("冠军总资产 ÷ 其余玩家平均总资产（越接近 1 越均衡，>3 说明领先者很难被追上）\n")
    for label, rows in results.items():
        vals = [r["snowball"] for r in rows]
        lines.append(f"- **{label}**：平均 {statistics.mean(vals):.2f}，"
                     f"最高 {max(vals):.2f}，最低 {min(vals):.2f}")

    lines.append("\n## 破产原因（破产玩家最大支出类别）\n")
    total_reasons: Counter = Counter()
    for rows in results.values():
        for r in rows:
            total_reasons.update(r["bankruptcy_reasons"])
    if total_reasons:
        for reason, count in total_reasons.most_common():
            lines.append(f"- {reason}：{count} 次")
    else:
        lines.append("- 样本中没有破产发生")

    lines.append("\n## 银行净投放\n")
    lines.append("正数表示系统向玩家净注入资金（起点奖励等），负数表示净回收（税收、购地）。\n")
    for label, rows in results.items():
        net = statistics.mean([r["bank_net"] for r in rows])
        lines.append(f"- **{label}**：{net:,.0f}")

    lines.append("\n## 结论与调整建议\n")
    if four:
        rounds4 = statistics.mean([r["rounds"] for r in four])
        minutes4 = estimate_minutes(rounds4, 4)
        if minutes4 > 50:
            lines.append("- 4 人局偏长，建议提高 Lv2/Lv3 租金系数或降低 `max_rounds`。")
        elif minutes4 < 25:
            lines.append("- 4 人局偏短，建议降低租金系数或提高初始资金。")
        else:
            lines.append("- 4 人局时长落在目标区间，暂不需要调整租金系数。")
        snow = statistics.mean([r["snowball"] for r in four])
        if snow > 3.5:
            lines.append(f"- 雪球偏强（{snow:.2f}），可以考虑加强落后玩家的机遇权重。")
        else:
            lines.append(f"- 雪球强度 {snow:.2f}，处于可接受范围。")
    lines.append("- 抵押与债务处理已接入生产流程；赎回次数偏低说明 AI 更倾向"
                 "长期持有抵押地（当前实现会在现金充裕时赎回）。")

    out_path = os.path.join(root, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
