"""显式游戏阶段机。

禁止用十几个布尔量表达流程；所有阶段推进都走这里定义的规则。
"""
from __future__ import annotations

from enum import Enum


class GamePhase(str, Enum):
    """游戏阶段。"""

    GAME_SETUP = "GAME_SETUP"            # 初始化，发牌发钱
    TURN_START = "TURN_START"            # 回合开始（结算状态、判定是否跳过）
    WAIT_ROLL = "WAIT_ROLL"              # 等待当前玩家掷骰（真人点骰子 / AI 决策）
    ROLLING = "ROLLING"                  # 骰子动画中（含监狱掷骰）
    MOVING = "MOVING"                    # 棋子逐格移动中
    ARRIVED = "ARRIVED"                  # 已落格，准备结算
    RESOLVE_TILE = "RESOLVE_TILE"        # 结算落点（购买/租金/事件/税收）
    DEBT_RESOLUTION = "DEBT_RESOLUTION"  # 债务处理：玩家变卖 / 抵押自救
    SHOP = "SHOP"                        # 商店：挑选道具
    WAIT_DECISION = "WAIT_DECISION"      # 等待玩家决策（购买、升级、选卡目标等）
    APPLY_EVENT = "APPLY_EVENT"          # 应用机遇事件效果
    JAIL_DECISION = "JAIL_DECISION"      # 监狱决策（保释/掷骰）
    TURN_END = "TURN_END"                # 回合结束收尾
    GAME_OVER = "GAME_OVER"              # 游戏结束

    @property
    def label(self) -> str:
        return {
            GamePhase.GAME_SETUP: "准备中",
            GamePhase.TURN_START: "回合开始",
            GamePhase.WAIT_ROLL: "等待掷骰",
            GamePhase.ROLLING: "掷骰中",
            GamePhase.MOVING: "移动中",
            GamePhase.ARRIVED: "到达",
            GamePhase.RESOLVE_TILE: "结算地块",
            GamePhase.DEBT_RESOLUTION: "债务处理",
            GamePhase.SHOP: "商店",
            GamePhase.WAIT_DECISION: "等待决策",
            GamePhase.APPLY_EVENT: "应用事件",
            GamePhase.JAIL_DECISION: "看守所决策",
            GamePhase.TURN_END: "回合结束",
            GamePhase.GAME_OVER: "游戏结束",
        }.get(self, self.value)


#: 需要玩家/控制器作出决策的阶段
DECISION_PHASES = frozenset(
    {
        GamePhase.WAIT_ROLL,
        GamePhase.WAIT_DECISION,
        GamePhase.JAIL_DECISION,
        GamePhase.DEBT_RESOLUTION,
        GamePhase.SHOP,
    }
)

#: 自动推进（有固定演出时长）的阶段，值表示演出时长（秒，1.0x 动画速度下）
AUTO_PHASE_DURATIONS: dict[GamePhase, float] = {
    GamePhase.GAME_SETUP: 0.6,
    GamePhase.TURN_START: 0.35,
    GamePhase.ROLLING: 1.1,
    GamePhase.ARRIVED: 0.2,
    GamePhase.RESOLVE_TILE: 0.45,
    GamePhase.DEBT_RESOLUTION: 0.0,
    GamePhase.SHOP: 0.0,
    GamePhase.APPLY_EVENT: 0.9,
    GamePhase.TURN_END: 0.5,
}

#: 正常回合的期望流转顺序，用于合法性与调试检查
NORMAL_TURN_ORDER = [
    GamePhase.TURN_START,
    GamePhase.WAIT_ROLL,
    GamePhase.ROLLING,
    GamePhase.MOVING,
    GamePhase.ARRIVED,
    GamePhase.RESOLVE_TILE,
    GamePhase.TURN_END,
]


def is_auto_phase(phase: GamePhase) -> bool:
    return phase in AUTO_PHASE_DURATIONS


def phase_duration(phase: GamePhase) -> float:
    return AUTO_PHASE_DURATIONS.get(phase, 0.0)
