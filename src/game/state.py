"""GameState：整局游戏的唯一权威状态。

设计要点：
- revision 单调递增，客户端只接受更新的快照；
- 所有随机数由 seed + counter 决定，从而完全可序列化、可复现；
- 状态本身不持有任何 pygame / 网络对象，可安全地深拷贝、存盘、广播。
"""
from __future__ import annotations

import json
import random
import time
import uuid
from typing import Any

from .board import Board
from .commands import PendingDecision
from .ledger import EconomyLedger
from .dice import DiceResult
from .events import GameEvent
from .phases import GamePhase
from .player import Player
from .property import Property

#: 事件日志保留的最大条数
MAX_LOG_ENTRIES = 400


class GameState:
    """一局游戏的全部状态。"""

    def __init__(
        self,
        match_id: str,
        board: Board,
        players: list[Player],
        properties: dict[str, Property],
        district_props: dict[str, list[str]] | None = None,
        district_bonus: float = 2.0,
        rules: dict[str, Any] | None = None,
        seed: int | None = None,
    ) -> None:
        self.match_id = match_id
        self.revision = 0
        self.phase: GamePhase = GamePhase.GAME_SETUP
        self.round_number = 1
        self.turn_number = 1
        self.current_player_id: str | None = None
        self.winner_id: str | None = None
        self.game_over = False

        self.board = board
        self.players: list[Player] = players
        self.properties: dict[str, Property] = properties
        self.district_props = district_props or {}
        self.district_bonus = float(district_bonus)

        self.rules: dict[str, Any] = rules or {}
        #: 本局采用的预设与地图（用于界面展示与存档记录）
        self.preset: str = str(self.rules.get("preset", "standard"))
        self.map_file: str = str(self.rules.get("_map_file", "default_map.json"))

        # 随机
        self.seed = int(seed if seed is not None else random.randint(1, 2**31 - 1))
        self.rng_counter = 0

        # 机遇牌堆
        self.chance_deck: list[str] = []
        self.chance_discard: list[str] = []

        # 骰子与移动
        self.dice: DiceResult | None = None
        self.move_from: int = 0
        self.move_path: list[int] = []
        self.move_steps: int = 0
        self.move_player_id: str | None = None

        # 决策与债务
        self.pending_decision: PendingDecision | None = None
        self.pending_effect: dict[str, Any] | None = None   # 需要额外目标的效果
        self.debt: dict[str, Any] | None = None
        self.shop: dict[str, Any] | None = None              # 当前商店展示的卡片
        self.extra_turns = 0           # 额外回合（再掷一次）
        self.turn_rolled = False       # 本回合是否已掷骰
        self.doubles_count = 0         # 连续双数次数
        self.chance_depth = 0          # 机遇链深度，防止无限连锁
        self.move_source = "dice"      # dice / chance / jail / card
        self.move_after = "resolve"    # resolve / turn_end
        self.last_chance_id: str | None = None
        self.last_chance_text: str = ""

        # 奖金池与路障
        self.bonus_pool: int = 0
        self.barriers: dict[str, int] = {}   # tile_index(str) -> 剩余回合

        # 阶段计时
        self.phase_timer = 0.0
        self.phase_duration = 0.0
        self.decision_timer = 0.0      # 当前决策已等待的时间（防挂机卡局）
        self.clock = 0.0               # Host 单调游戏时钟（秒）

        # 资金流水（所有 money 变化都经它记账）
        self.ledger = EconomyLedger()

        # 日志
        self.event_log: list[GameEvent] = []
        self.last_event: GameEvent | None = None
        self.event_seq = 0
        self.decision_seq = 0

        # 统计
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.bankrupt_counter = 0
        self.total_rounds_played = 0

    # ------------------------------------------------------------ 玩家
    def player(self, player_id: str | None) -> Player | None:
        if player_id is None:
            return None
        for p in self.players:
            if p.id == player_id:
                return p
        return None

    def require_player(self, player_id: str) -> Player:
        p = self.player(player_id)
        if p is None:
            raise KeyError(f"玩家不存在: {player_id}")
        return p

    @property
    def current_player(self) -> Player | None:
        return self.player(self.current_player_id)

    def active_players(self) -> list[Player]:
        return [p for p in self.players if not p.bankrupt]

    def active_player_ids(self) -> list[str]:
        return [p.id for p in self.active_players()]

    def connected_players(self) -> list[Player]:
        return [p for p in self.players if not p.disconnected]

    def player_index(self, player_id: str) -> int:
        for i, p in enumerate(self.players):
            if p.id == player_id:
                return i
        return -1

    def other_active_players(self, player_id: str) -> list[Player]:
        return [p for p in self.players if not p.bankrupt and p.id != player_id]

    # ------------------------------------------------------------ 地产
    def property_at(self, tile_index: int) -> Property | None:
        for prop in self.properties.values():
            if prop.tile_index == tile_index:
                return prop
        return None

    def properties_of(self, player_id: str) -> list[Property]:
        return [p for p in self.properties.values() if p.owner_id == player_id]

    def owned_count(self, player_id: str) -> int:
        return sum(1 for p in self.properties.values() if p.owner_id == player_id)

    def unowned_properties(self) -> list[Property]:
        return [p for p in self.properties.values() if p.owner_id is None]

    def district_owned_all(self, player_id: str, district: str | None) -> bool:
        """玩家是否垄断了某分组。"""
        if not district:
            return False
        ids = self.district_props.get(district, [])
        if not ids:
            return False
        for pid in ids:
            prop = self.properties.get(pid)
            if prop is None or prop.owner_id != player_id:
                return False
        return True

    def player_asset_value(self, player_id: str) -> int:
        """玩家总资产 = 现金 + 地产估值。"""
        p = self.player(player_id)
        cash = p.money if p else 0
        return cash + sum(prop.asset_value for prop in self.properties_of(player_id))

    # ------------------------------------------------------------ 随机
    def next_rng(self) -> random.Random:
        """返回一个由 seed+counter 决定的随机源，保证可复现。"""
        self.rng_counter += 1
        return random.Random(f"richman:{self.seed}:{self.rng_counter}")

    # ------------------------------------------------------------ 阶段
    def set_phase(self, phase: GamePhase, duration: float = 0.0) -> None:
        self.phase = phase
        self.phase_timer = 0.0
        self.phase_duration = max(0.0, duration)
        self.decision_timer = 0.0

    def bump(self) -> None:
        """标记状态已变化，revision 递增。"""
        self.revision += 1

    # ------------------------------------------------------------ 日志
    def log(
        self,
        etype: str,
        message: str,
        player_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> GameEvent:
        self.event_seq += 1
        ev = GameEvent(
            event_id=f"e{self.event_seq}",
            etype=etype,
            message=message,
            player_id=player_id,
            data=data or {},
            revision=self.revision,
            tick=self.clock,
            seq=self.event_seq,
        )
        self.event_log.append(ev)
        if len(self.event_log) > MAX_LOG_ENTRIES:
            del self.event_log[: len(self.event_log) - MAX_LOG_ENTRIES]
        self.last_event = ev
        return ev

    def recent_log(self, count: int = 12) -> list[GameEvent]:
        return self.event_log[-count:]

    # ------------------------------------------------------------ 路障
    def barrier_at(self, tile_index: int) -> bool:
        return str(tile_index) in self.barriers

    def tick_barriers(self) -> None:
        expired = [k for k, v in self.barriers.items() if v <= 1]
        for k in expired:
            del self.barriers[k]
        for k in list(self.barriers.keys()):
            if k not in expired:
                self.barriers[k] -= 1

    # ------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "revision": self.revision,
            "phase": self.phase.value,
            "round_number": self.round_number,
            "turn_number": self.turn_number,
            "current_player_id": self.current_player_id,
            "winner_id": self.winner_id,
            "game_over": self.game_over,
            "seed": self.seed,
            "rng_counter": self.rng_counter,
            "rules": dict(self.rules),
            "preset": self.preset,
            "map_file": self.map_file,
            "board": self.board.to_dict(),
            "players": [p.to_dict() for p in self.players],
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "district_props": {k: list(v) for k, v in self.district_props.items()},
            "district_bonus": self.district_bonus,
            "chance_deck": list(self.chance_deck),
            "chance_discard": list(self.chance_discard),
            "dice": self.dice.to_dict() if self.dice else None,
            "move_from": self.move_from,
            "move_path": list(self.move_path),
            "move_steps": self.move_steps,
            "move_player_id": self.move_player_id,
            "pending_decision": self.pending_decision.to_dict() if self.pending_decision else None,
            "pending_effect": dict(self.pending_effect) if self.pending_effect else None,
            "debt": dict(self.debt) if self.debt else None,
            "shop": dict(self.shop) if self.shop else None,
            "extra_turns": self.extra_turns,
            "turn_rolled": self.turn_rolled,
            "doubles_count": self.doubles_count,
            "chance_depth": self.chance_depth,
            "move_source": self.move_source,
            "move_after": self.move_after,
            "last_chance_id": self.last_chance_id,
            "last_chance_text": self.last_chance_text,
            "bonus_pool": self.bonus_pool,
            "barriers": dict(self.barriers),
            "phase_timer": self.phase_timer,
            "phase_duration": self.phase_duration,
            "decision_timer": self.decision_timer,
            "clock": self.clock,
            "ledger": self.ledger.to_dict(),
            "event_log": [e.to_dict() for e in self.event_log],
            "event_seq": self.event_seq,
            "decision_seq": self.decision_seq,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "bankrupt_counter": self.bankrupt_counter,
            "total_rounds_played": self.total_rounds_played,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GameState":
        board = Board.from_dict(d["board"])
        players = [Player.from_dict(p) for p in d["players"]]
        properties = {k: Property.from_dict(v) for k, v in d["properties"].items()}
        st = cls(
            match_id=d.get("match_id", uuid.uuid4().hex[:8]),
            board=board,
            players=players,
            properties=properties,
            district_props={k: list(v) for k, v in (d.get("district_props") or {}).items()},
            district_bonus=float(d.get("district_bonus", 2.0)),
            rules=dict(d.get("rules") or {}),
            seed=int(d.get("seed", 1)),
        )
        st.revision = int(d.get("revision", 0))
        st.phase = GamePhase(d.get("phase", GamePhase.GAME_SETUP.value))
        st.round_number = int(d.get("round_number", 1))
        st.turn_number = int(d.get("turn_number", 1))
        st.current_player_id = d.get("current_player_id")
        st.winner_id = d.get("winner_id")
        st.game_over = bool(d.get("game_over", False))
        st.rng_counter = int(d.get("rng_counter", 0))
        st.preset = str(d.get("preset", st.rules.get("preset", "standard")))
        st.map_file = str(d.get("map_file", st.rules.get("_map_file", "default_map.json")))
        st.chance_deck = list(d.get("chance_deck") or [])
        st.chance_discard = list(d.get("chance_discard") or [])
        st.dice = DiceResult.from_dict(d["dice"]) if d.get("dice") else None
        st.move_from = int(d.get("move_from", 0))
        st.move_path = [int(v) for v in (d.get("move_path") or [])]
        st.move_steps = int(d.get("move_steps", 0))
        st.move_player_id = d.get("move_player_id")
        pd = d.get("pending_decision")
        st.pending_decision = PendingDecision.from_dict(pd) if pd else None
        st.pending_effect = dict(d["pending_effect"]) if d.get("pending_effect") else None
        st.debt = dict(d["debt"]) if d.get("debt") else None
        st.shop = dict(d["shop"]) if d.get("shop") else None
        st.extra_turns = int(d.get("extra_turns", 0))
        st.turn_rolled = bool(d.get("turn_rolled", False))
        st.doubles_count = int(d.get("doubles_count", 0))
        st.chance_depth = int(d.get("chance_depth", 0))
        st.move_source = d.get("move_source", "dice")
        st.move_after = d.get("move_after", "resolve")
        st.last_chance_id = d.get("last_chance_id")
        st.last_chance_text = d.get("last_chance_text", "")
        st.bonus_pool = int(d.get("bonus_pool", 0))
        st.barriers = {str(k): int(v) for k, v in (d.get("barriers") or {}).items()}
        st.phase_timer = float(d.get("phase_timer", 0.0))
        st.phase_duration = float(d.get("phase_duration", 0.0))
        st.decision_timer = float(d.get("decision_timer", 0.0))
        st.clock = float(d.get("clock", 0.0))
        st.ledger.load_dict(d.get("ledger") or {})
        st.event_log = [GameEvent.from_dict(e) for e in (d.get("event_log") or [])]
        st.event_seq = int(d.get("event_seq", 0))
        st.decision_seq = int(d.get("decision_seq", 0))
        st.started_at = float(d.get("started_at", time.time()))
        st.ended_at = d.get("ended_at")
        st.bankrupt_counter = int(d.get("bankrupt_counter", 0))
        st.total_rounds_played = int(d.get("total_rounds_played", 0))
        return st

    def clone(self) -> "GameState":
        """深拷贝一份状态（AI 推演 / 回滚用）。"""
        return GameState.from_dict(json.loads(json.dumps(self.to_dict())))

    def canonical_hash(self) -> str:
        """核心状态的规范化哈希，用于 Host/Client 一致性探针。"""
        import hashlib

        payload = {
            "phase": self.phase.value,
            "round": self.round_number,
            "turn": self.turn_number,
            "cur": self.current_player_id,
            "rev": self.revision,
            "players": [
                [p.id, p.money, p.position, p.bankrupt, p.in_jail, p.jail_turns, sorted(p.cards)]
                for p in sorted(self.players, key=lambda x: x.id)
            ],
            "props": [
                [k, v.owner_id, v.level]
                for k, v in sorted(self.properties.items())
            ],
            "pool": self.bonus_pool,
            "over": self.game_over,
            "winner": self.winner_id,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def diff_summary(self, other: "GameState") -> list[str]:
        """返回与另一份状态的关键差异描述（调试用）。"""
        diffs: list[str] = []
        if self.phase != other.phase:
            diffs.append(f"phase {self.phase.value} != {other.phase.value}")
        if self.round_number != other.round_number:
            diffs.append(f"round {self.round_number} != {other.round_number}")
        if self.current_player_id != other.current_player_id:
            diffs.append(f"current {self.current_player_id} != {other.current_player_id}")
        if self.bonus_pool != other.bonus_pool:
            diffs.append(f"pool {self.bonus_pool} != {other.bonus_pool}")
        mine = {p.id: p for p in self.players}
        theirs = {p.id: p for p in other.players}
        for pid, p in mine.items():
            q = theirs.get(pid)
            if q is None:
                diffs.append(f"missing player {pid}")
                continue
            if p.money != q.money:
                diffs.append(f"{p.name}.money {p.money} != {q.money}")
            if p.position != q.position:
                diffs.append(f"{p.name}.pos {p.position} != {q.position}")
            if p.bankrupt != q.bankrupt:
                diffs.append(f"{p.name}.bankrupt {p.bankrupt} != {q.bankrupt}")
        for key, prop in self.properties.items():
            other_prop = other.properties.get(key)
            if other_prop is None:
                continue
            if prop.owner_id != other_prop.owner_id or prop.level != other_prop.level:
                diffs.append(
                    f"{prop.name} {prop.owner_id}/{prop.level} != "
                    f"{other_prop.owner_id}/{other_prop.level}"
                )
        return diffs

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<GameState rev={self.revision} phase={self.phase.value} "
            f"round={self.round_number} players={len(self.players)}>"
        )
