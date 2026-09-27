"""版本号：界面、协议握手、存档都从这里取，避免三处各写一份。"""
from __future__ import annotations

APP_VERSION = "Richman v0.4.0-rc1"
VERSION_LABEL = "原创实现 · 局域网联机"
#: 存档格式版本；格式不兼容时 +1
SAVE_VERSION = 2


def version_tuple() -> tuple[int, int, int]:
    """把 'Richman v0.2.0' / 'Richman v0.4.0-rc1' 解析成 (0, 4, 0)。

    只取每段开头的连续数字：'-rc1'、'+build7' 这类后缀不参与比较，
    否则 'v0.4.0-rc1' 会被解析成 (0, 4, 1)，比正式版号还大。
    """
    digits = []
    for part in APP_VERSION.split("v")[-1].split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        digits.append(int(num) if num else 0)
    while len(digits) < 3:
        digits.append(0)
    return tuple(digits[:3])  # type: ignore[return-value]


def short_version() -> str:
    """给玩家看 / 发群里的短版本号，例如 '0.4.0-rc1'。"""
    return APP_VERSION.split("v")[-1]
