"""自动重连：客户端掉线后按固定节奏尝试恢复，直到宽限期结束。

为什么要有这一层：
v0.2 里 `GameClient.reconnect()` 写好了却**从来没有被调用过** ——
掉线只会弹一句「连接已断开」然后把人踢回主菜单，本局直接作废。
现在由 `AutoReconnector` 负责节奏（什么时候重试、还剩多少时间），
由 `App` 负责界面提示，由 `GameHost` 负责宽限期与 AI 接管。

时间参数与 Host 保持一致（默认 30 秒），因此：
    掉线 → 客户端每 3 秒重试 → Host 侧 30 秒内仍然是「掉线」状态
         → 重连成功则恢复 RemoteController；超过 30 秒 Host 交给 AI 接管，
           此时客户端仍可重连并接回操作（Host 会摘掉 AI）。
这个类不碰网络细节，只调用 `client.reconnect()`，方便单测替换。
"""
from __future__ import annotations

import time
from typing import Any

from ..utils.logging_setup import get_logger

log = get_logger(__name__)

#: 客户端重连总时长（与 GameHost.RECONNECT_GRACE_SEC 对齐）
DEFAULT_GRACE_SEC = 30.0
#: 两次尝试之间的间隔
DEFAULT_INTERVAL = 3.0

STATUS_IDLE = "idle"
STATUS_RETRYING = "retrying"
STATUS_RECOVERED = "recovered"
STATUS_EXHAUSTED = "exhausted"


class AutoReconnector:
    """按时间节奏尝试重连的调度器。"""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        grace_sec: float = DEFAULT_GRACE_SEC,
        interval: float = DEFAULT_INTERVAL,
        clock=time.time,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.token = token
        self.grace_sec = float(grace_sec)
        self.interval = float(interval)
        self._clock = clock
        self.started_at = clock()
        self.status = STATUS_RETRYING
        self.attempts = 0
        self.last_error = ""
        self._next_attempt = self.started_at

    # ------------------------------------------------------------ 查询

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self.started_at)

    @property
    def remaining(self) -> float:
        return max(0.0, self.grace_sec - self.elapsed)

    @property
    def progress(self) -> float:
        if self.grace_sec <= 0:
            return 1.0
        return min(1.0, self.elapsed / self.grace_sec)

    @property
    def active(self) -> bool:
        return self.status == STATUS_RETRYING

    def status_text(self) -> str:
        if self.status == STATUS_RETRYING:
            return f"正在尝试重新连接…（第 {self.attempts} 次）"
        if self.status == STATUS_RECOVERED:
            return "已重新连接"
        if self.status == STATUS_EXHAUSTED:
            return "重连超时"
        return ""

    def hint_text(self) -> str:
        if self.status == STATUS_RETRYING:
            return (f"房主会保留你的座位 {int(self.grace_sec)} 秒；"
                    f"倒计时结束后由 AI 接管，本局不会中断。")
        return self.last_error or ""

    # ------------------------------------------------------------ 调度

    def tick(self, client: Any) -> str:
        """推进一次重连尝试。返回当前状态。"""
        if self.status in (STATUS_RECOVERED, STATUS_EXHAUSTED):
            return self.status
        now = self._clock()
        if client is not None and getattr(client, "connected", False):
            self.status = STATUS_RECOVERED
            return self.status
        if now >= self.started_at + self.grace_sec:
            self.status = STATUS_EXHAUSTED
            return self.status
        if now < self._next_attempt:
            return self.status
        self._next_attempt = now + self.interval
        self.attempts += 1
        if client is None:
            self.status = STATUS_EXHAUSTED
            return self.status
        try:
            client.reconnect(self.host, self.port, self.token)
            log.info("第 %d 次重连尝试已发出（%s:%s）", self.attempts, self.host, self.port)
        except Exception as exc:
            self.last_error = str(exc)
            log.info("重连尝试失败：%s", exc)
        return self.status

    def cancel(self) -> None:
        self.status = STATUS_IDLE
