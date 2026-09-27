"""对局分析：把「这一局到底把时间花在哪」变成可以读的数字。

为什么要单独一个模块：
v0.3 的结论是「6 人局约 69 分钟」，但没人知道这 69 分钟里有多少是
AI 思考、多少是移动动画、多少是玩家自己在想。没有这组数字，
任何「加快节奏」的改动都是拍脑袋。

三条约束：
- **只统计，不参与规则**：本模块不做任何状态修改，也不影响胜负；
- **固定大小**：只用累计值与定长列表，不能随局数线性膨胀（否则存档会越来越大）；
- **可序列化**：跟随 `GameState.to_dict()` 一起走存档与快照（客户端要显示时间线）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import EventType
from .phases import GamePhase

#: 现金采样点上限（用于算「平均现金」，太多没意义）
MAX_CASH_SAMPLES = 240
#: 里程碑上限：只保留真正有意义的事件
MAX_MILESTONES = 40

#: 里程碑类型（决定 UI 上的图标与颜色）
MILE_LANDMARK = "landmark"      # 首次有人破产 / 结束
MILE_MONOPOLY = "monopoly"      # 完成片区垄断
MILE_WEALTH = "wealth"          # 资产里程碑
MILE_EVENT = "event"            # 其它值得记住的事
MILE_COMEBACK = "comeback"      # 从破产边缘回来


@dataclass
class MatchAnalytics:
    """一局的时间与行为统计。"""

    # ---- 时间（秒）
    turn_time_by_player: dict[str, float] = field(default_factory=dict)
    turns_by_player: dict[str, int] = field(default_factory=dict)
    think_time_ai: float = 0.0          # 累计「AI 决策耗时」
    think_time_human: float = 0.0       # 累计「真人决策等待」
    decisions_ai: int = 0
    decisions_human: int = 0
    move_time: float = 0.0              # 棋子移动动画累计
    anim_time: float = 0.0              # 掷骰 / 结算 / 过场累计
    # ---- 破产
    bankrupt_log: list[dict[str, Any]] = field(default_factory=list)
    # ---- 里程碑
    milestones: list[dict[str, Any]] = field(default_factory=list)
    # ---- 经济
    rent_total: int = 0
    upgrade_total: int = 0
    bought_total: int = 0
    sold_total: int = 0
    mortgage_total: int = 0
    card_used_total: int = 0
    cash_samples: list[int] = field(default_factory=list)

    _seen_monopoly: set[str] = field(default_factory=set, repr=False)
    _seen_wealth: set[int] = field(default_factory=set, repr=False)

    # ------------------------------------------------------------ 记录

    def add_turn_time(self, player_id: str, seconds: float) -> None:
        self.turn_time_by_player[player_id] = (
            self.turn_time_by_player.get(player_id, 0.0) + max(0.0, seconds))
        self.turns_by_player[player_id] = self.turns_by_player.get(player_id, 0) + 1

    def add_decision(self, is_ai: bool, seconds: float) -> None:
        if is_ai:
            self.think_time_ai += max(0.0, seconds)
            self.decisions_ai += 1
        else:
            self.think_time_human += max(0.0, seconds)
            self.decisions_human += 1

    def sample_cash(self, total_cash: int) -> None:
        if len(self.cash_samples) < MAX_CASH_SAMPLES:
            self.cash_samples.append(int(total_cash))

    def add_milestone(self, round_number: int, turn_number: int, text: str,
                      kind: str = MILE_EVENT, player_id: str = "") -> None:
        if len(self.milestones) >= MAX_MILESTONES:
            return
        self.milestones.append({
            "round": int(round_number),
            "turn": int(turn_number),
            "text": text,
            "kind": kind,
            "player_id": player_id,
        })

    def milestone_once(self, key: str, round_number: int, turn_number: int,
                       text: str, kind: str = MILE_EVENT,
                       player_id: str = "") -> None:
        """同一个 key 只记一次（例如「首次垄断」这种一次性事件）。"""
        if key in self._seen_monopoly:
            return
        self._seen_monopoly.add(key)
        self.add_milestone(round_number, turn_number, text, kind, player_id)

    def wealth_milestone(self, amount: int, round_number: int, turn_number: int,
                         text: str, player_id: str = "") -> None:
        """资产里程碑：同一个金额档位只记一次。"""
        if amount in self._seen_wealth:
            return
        self._seen_wealth.add(amount)
        self.add_milestone(round_number, turn_number, text, MILE_WEALTH, player_id)

    def record_bankruptcy(self, player_id: str, name: str, round_number: int,
                          debt: int, assets: int, properties: int,
                          reason: str = "", creditor: str = "") -> None:
        self.bankrupt_log.append({
            "player_id": player_id,
            "name": name,
            "round": int(round_number),
            "debt": int(debt),
            "assets": int(assets),
            "properties": int(properties),
            "reason": reason,
            "creditor": creditor,
        })
        self.add_milestone(round_number, 0, f"{name} 破产退出", MILE_LANDMARK, player_id)

    # ------------------------------------------------------------ 查询

    @property
    def bankrupt_rounds(self) -> list[int]:
        return [int(b["round"]) for b in self.bankrupt_log]

    @property
    def first_bankrupt_round(self) -> int:
        rounds = self.bankrupt_rounds
        return rounds[0] if rounds else 0

    @property
    def last_bankrupt_round(self) -> int:
        rounds = self.bankrupt_rounds
        return rounds[-1] if rounds else 0

    def player_turn_avg(self, player_id: str) -> float:
        count = self.turns_by_player.get(player_id, 0)
        if not count:
            return 0.0
        return self.turn_time_by_player.get(player_id, 0.0) / count

    @property
    def avg_cash(self) -> int:
        if not self.cash_samples:
            return 0
        return int(sum(self.cash_samples) / len(self.cash_samples))

    @property
    def total_measured(self) -> float:
        """所有被统计到的展示/等待时间之和（用于「时间都去哪了」）。"""
        return (sum(self.turn_time_by_player.values()) + self.move_time
                + self.anim_time)

    def time_breakdown(self) -> list[tuple[str, float]]:
        """时间去向（降序）：给结算与报告用。"""
        rows = [
            ("真人思考/操作", self.think_time_human),
            ("AI 决策", self.think_time_ai),
            ("移动动画", self.move_time),
            ("掷骰与结算演出", self.anim_time),
        ]
        rows.sort(key=lambda kv: -kv[1])
        return rows

    def summary(self) -> dict[str, Any]:
        total = self.total_measured
        rows = []
        for label, value in self.time_breakdown():
            ratio = (value / total * 100.0) if total > 0 else 0.0
            rows.append({"label": label, "seconds": round(value, 1),
                         "ratio": round(ratio, 1)})
        return {
            "turn_time_by_player": {k: round(v, 1)
                                    for k, v in self.turn_time_by_player.items()},
            "turns_by_player": dict(self.turns_by_player),
            "think_time_ai": round(self.think_time_ai, 1),
            "think_time_human": round(self.think_time_human, 1),
            "decisions_ai": self.decisions_ai,
            "decisions_human": self.decisions_human,
            "move_time": round(self.move_time, 1),
            "anim_time": round(self.anim_time, 1),
            "bankrupt_log": list(self.bankrupt_log),
            "first_bankrupt_round": self.first_bankrupt_round,
            "last_bankrupt_round": self.last_bankrupt_round,
            "milestones": list(self.milestones),
            "rent_total": self.rent_total,
            "upgrade_total": self.upgrade_total,
            "bought_total": self.bought_total,
            "sold_total": self.sold_total,
            "mortgage_total": self.mortgage_total,
            "card_used_total": self.card_used_total,
            "avg_cash": self.avg_cash,
            "breakdown": rows,
        }

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_time_by_player": dict(self.turn_time_by_player),
            "turns_by_player": dict(self.turns_by_player),
            "think_time_ai": self.think_time_ai,
            "think_time_human": self.think_time_human,
            "decisions_ai": self.decisions_ai,
            "decisions_human": self.decisions_human,
            "move_time": self.move_time,
            "anim_time": self.anim_time,
            "bankrupt_log": [dict(b) for b in self.bankrupt_log],
            "milestones": [dict(m) for m in self.milestones],
            "rent_total": self.rent_total,
            "upgrade_total": self.upgrade_total,
            "bought_total": self.bought_total,
            "sold_total": self.sold_total,
            "mortgage_total": self.mortgage_total,
            "card_used_total": self.card_used_total,
            "cash_samples": list(self.cash_samples),
            "seen_monopoly": sorted(self._seen_monopoly),
            "seen_wealth": sorted(self._seen_wealth),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "MatchAnalytics":
        a = cls()
        if not d:
            return a
        a.turn_time_by_player = {str(k): float(v)
                                 for k, v in (d.get("turn_time_by_player") or {}).items()}
        a.turns_by_player = {str(k): int(v)
                             for k, v in (d.get("turns_by_player") or {}).items()}
        a.think_time_ai = float(d.get("think_time_ai", 0.0))
        a.think_time_human = float(d.get("think_time_human", 0.0))
        a.decisions_ai = int(d.get("decisions_ai", 0))
        a.decisions_human = int(d.get("decisions_human", 0))
        a.move_time = float(d.get("move_time", 0.0))
        a.anim_time = float(d.get("anim_time", 0.0))
        a.bankrupt_log = [dict(x) for x in (d.get("bankrupt_log") or [])]
        a.milestones = [dict(x) for x in (d.get("milestones") or [])]
        a.rent_total = int(d.get("rent_total", 0))
        a.upgrade_total = int(d.get("upgrade_total", 0))
        a.bought_total = int(d.get("bought_total", 0))
        a.sold_total = int(d.get("sold_total", 0))
        a.mortgage_total = int(d.get("mortgage_total", 0))
        a.card_used_total = int(d.get("card_used_total", 0))
        a.cash_samples = [int(v) for v in (d.get("cash_samples") or [])]
        a._seen_monopoly = {str(x) for x in (d.get("seen_monopoly") or [])}
        a._seen_wealth = {int(x) for x in (d.get("seen_wealth") or [])}
        return a


#: 资产里程碑档位（首次达到时记一笔，只记一次）
WEALTH_STEPS = (30000, 60000, 100000)


def milestone_for_event(analytics: MatchAnalytics, state: Any, ev: Any) -> None:
    """把一条引擎事件翻译成「玩家会想记住的里程碑」。

    只在真正有意义的事件上记录 —— 流水账会让时间线变得没人看。
    这里只读事件与状态，不修改任何规则数据。
    """
    etype = ev.type
    data = ev.data or {}
    player = state.player(ev.player_id)
    name = player.name if player else "玩家"
    rnd = int(getattr(state, "round_number", 0))
    turn = int(getattr(state, "turn_number", 0))

    # 时间线只记「有意义的事」，所以这里只挑少数事件类型，不做流水账
    if etype == EventType.PROPERTY_BOUGHT:
        analytics.bought_total += 1
    elif etype == EventType.PROPERTY_UPGRADED:
        analytics.upgrade_total += 1
    elif etype == EventType.RENT_PAID:
        analytics.rent_total += 1
    elif etype == EventType.PROPERTY_SOLD:
        analytics.sold_total += 1
    elif etype == EventType.PROPERTY_MORTGAGED:
        analytics.mortgage_total += 1
    elif etype == EventType.CARD_USED:
        analytics.card_used_total += 1
    elif etype == EventType.BANKRUPT and player is not None:
        analytics.record_bankruptcy(
            player.id, player.name, rnd,
            debt=int(data.get("debt", 0) or 0),
            assets=int(data.get("assets", 0) or 0),
            properties=int(data.get("properties", 0) or 0),
            reason=str(data.get("reason", "")),
            creditor=str(data.get("creditor_name", "") or ""),
        )
    elif etype == EventType.PROPERTY_MONOPOLY or data.get("district"):
        district = str(data.get("district", "") or "")
        if district:
            analytics.milestone_once(
                f"mono:{player.id if player else ev.player_id}:{district}",
                rnd, turn, f"{name} 集齐「{district}」，该区租金翻倍",
                MILE_MONOPOLY, ev.player_id)

    # 资产里程碑：每次有人越过一档就记一笔
    if player is not None and not player.bankrupt:
        assets = state.player_asset_value(player.id)
        for step in WEALTH_STEPS:
            if assets >= step:
                analytics.wealth_milestone(
                    step, rnd, turn, f"{name} 总资产突破 {step:,}", player.id)


def phase_time_bucket(phase: GamePhase) -> str:
    """把阶段归类到「时间去哪了」的桶里。"""
    if phase is GamePhase.MOVING:
        return "move"
    if phase in (GamePhase.ROLLING, GamePhase.ARRIVED, GamePhase.RESOLVE_TILE,
                 GamePhase.APPLY_EVENT, GamePhase.TURN_START, GamePhase.TURN_END,
                 GamePhase.GAME_SETUP):
        return "anim"
    return "turn"
