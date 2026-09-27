"""剪贴板：把「本机 IP:端口」一键复制给玩家。

为什么要自己写：
- pygame 2 已经移除了 `pygame.scrap` 的写接口，没有一个跨版本的官方 API；
- 直接依赖 pyperclip 之类会增加打包体积与不确定性。

因此这里用最小实现：Windows 下走 ctypes 调 user32/kernel32（不引入任何新依赖），
其他平台尝试 pygame.scrap 与系统命令。**全部失败也不抛异常**，
调用方根据返回值决定是提示「已复制」还是「请手动记下」。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

from .logging_setup import get_logger

log = get_logger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _copy_windows(text: str) -> bool:
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
        finally:
            user32.CloseClipboard()
        return True
    except Exception as exc:  # pragma: no cover - 平台相关
        log.debug("Windows 剪贴板写入失败：%s", exc)
        return False


def _copy_pygame(text: str) -> bool:
    try:
        import pygame

        scrap = getattr(pygame, "scrap", None)
        if scrap is None:
            return False
        if not scrap.get_init():
            scrap.init()
        scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
        return True
    except Exception:
        return False


def _copy_command(text: str) -> bool:
    commands = []
    if sys.platform == "darwin":
        commands.append(["pbcopy"])
    else:
        if os.environ.get("WAYLAND_DISPLAY"):
            commands.append(["wl-copy"])
        commands.append(["xclip", "-selection", "clipboard"])
        commands.append(["xsel", "--clipboard", "--input"])
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, input=text.encode("utf-8"), timeout=2,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def copy_text(text: str) -> bool:
    """把文本写入系统剪贴板。返回是否成功（失败不抛异常）。"""
    if not text:
        return False
    if sys.platform.startswith("win"):
        if _copy_windows(text):
            return True
    if _copy_pygame(text):
        return True
    return _copy_command(text)


def clipboard_available() -> bool:
    """粗略判断当前环境是否有可能支持剪贴板（用于决定要不要显示复制按钮）。"""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
