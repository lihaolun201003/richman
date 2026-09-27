"""版本号：界面、协议握手、存档都从这里取，避免三处各写一份。"""
from __future__ import annotations

APP_VERSION = "Richman v0.2.0"
VERSION_LABEL = "原创实现 · 局域网联机"
#: 存档格式版本；格式不兼容时 +1
SAVE_VERSION = 2


def version_tuple() -> tuple[int, int, int]:
    """把 'Richman v0.2.0' 解析成 (0, 2, 0)，用于存档迁移判断。"""
    digits = []
    for part in APP_VERSION.split("v")[-1].split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        digits.append(int(num) if num else 0)
    while len(digits) < 3:
        digits.append(0)
    return tuple(digits[:3])  # type: ignore[return-value]
