"""控制器基类。

引擎不关心决策是谁给的：真人点击、AI 启发式、网络消息，
最终都收敛成一个 Command。
"""
from __future__ import annotations

from collections import deque
from typing import Any

from ..game.commands import Command, CommandType, PendingDecision
from ..game.state import GameState


class BaseController:
    """所有控制器的共同接口。"""

    #: 是否是通过网络接入的远程玩家
    is_remote = False

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id

    def poll(
        self,
        decision: PendingDecision | None,
        state: GameState,
        engine: Any,
        dt: float,
    ) -> Command | None:
        """询问控制器是否要提交命令。返回 None 表示「还在思考」。"""
        return None

    def submit(self, command: Command) -> bool:
        """外部投递一个命令（UI 点击 / 网络消息）。返回是否被接受。"""
        return False

    def reset(self) -> None:
        """清空未处理的输入。"""

    def on_event(self, event: Any) -> None:
        """接收引擎广播的事件（用于表现层反馈）。"""

    def cancel_pending(self) -> None:
        """撤销当前未提交的输入（例如决策已过期）。"""

    def describe(self) -> str:
        return "控制器"


class QueuedController(BaseController):
    """基于命令队列的控制器：本地真人点击与远程网络包都走这里。"""

    def __init__(self, player_id: str, max_queue: int = 16) -> None:
        super().__init__(player_id)
        self.queue: deque[Command] = deque(maxlen=max_queue)
        self.rejected: list[str] = []

    def submit(self, command: Command) -> bool:
        if command.player_id != self.player_id:
            command = Command(
                ctype=command.type,
                player_id=self.player_id,
                payload=command.payload,
                command_id=command.command_id,
                client_revision=command.client_revision,
                decision_id=command.decision_id,
            )
        self.queue.append(command)
        return True

    def poll(
        self,
        decision: PendingDecision | None,
        state: GameState,
        engine: Any,
        dt: float,
    ) -> Command | None:
        while self.queue:
            cmd = self.queue.popleft()
            # 丢弃针对已过期决策的命令，避免「旧窗口回答新问题」
            if cmd.decision_id and decision is not None and cmd.decision_id != decision.id:
                self.rejected.append(cmd.command_id)
                continue
            return cmd
        return None

    def reset(self) -> None:
        self.queue.clear()

    def cancel_pending(self) -> None:
        self.queue.clear()

    def describe(self) -> str:
        return "本地玩家"


class LocalController(QueuedController):
    """本机真人玩家：UI 事件处理函数把 Command 投递进来。"""

    def describe(self) -> str:
        return "本地玩家"


class RemoteController(QueuedController):
    """局域网远程玩家：命令来自网络线程。

    额外承担两件事：
    - 掉线标记（由 Host 设置 disconnected）；
    - 决策超时兜底，避免整局卡在某个人身上。
    """

    is_remote = True

    def __init__(self, player_id: str, timeout_sec: float = 90.0) -> None:
        super().__init__(player_id)
        self.disconnected = False
        self.timeout_sec = timeout_sec
        self._decision_id: str | None = None
        self._waited = 0.0

    def mark_disconnected(self) -> None:
        self.disconnected = True

    def mark_connected(self) -> None:
        self.disconnected = False

    def poll(
        self,
        decision: PendingDecision | None,
        state: GameState,
        engine: Any,
        dt: float,
    ) -> Command | None:
        cmd = super().poll(decision, state, engine, dt)
        if cmd is not None:
            return cmd

        if decision is None:
            self._decision_id = None
            self._waited = 0.0
            return None

        if self._decision_id != decision.id:
            self._decision_id = decision.id
            self._waited = 0.0
        self._waited += dt

        if self._waited >= self.timeout_sec:
            self._waited = 0.0
            return self._default_command(decision)
        return None

    def _default_command(self, decision: PendingDecision) -> Command | None:
        """超时后选择最保守的选项（通常是跳过/放弃）。"""
        preferred = ("skip", "roll", "confirm")
        chosen = None
        for pid in preferred:
            opt = decision.option(pid)
            if opt is not None and opt.enabled:
                chosen = opt
                break
        if chosen is None:
            for opt in decision.options:
                if opt.enabled:
                    chosen = opt
                    break
        if chosen is None:
            return None
        return Command(
            ctype=CommandType.RESOLVE_DECISION,
            player_id=self.player_id,
            payload={"option_id": chosen.id},
            decision_id=decision.id,
        )

    def describe(self) -> str:
        return "远程玩家"
