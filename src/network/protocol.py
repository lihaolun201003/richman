"""局域网协议：4 字节长度前缀 + UTF-8 JSON。

选择理由（相比 pickle / msgpack）：
- 可读、易调试，抓包能直接看出内容；
- 大富翁是低频状态同步，JSON 的体积完全不是瓶颈；
- 不涉及任意对象反序列化，没有 pickle 的安全与兼容问题。

所有消息都必须带 protocol_version，版本不一致直接拒绝连接，
避免新旧版本混连导致难以定位的错乱。
"""
from __future__ import annotations

import json
import struct
from typing import Any

#: 协议版本。任何不兼容的字段变更都要 +1
PROTOCOL_VERSION = 1

#: 单条消息最大长度（16MB），防止异常数据撑爆内存
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

#: 长度前缀格式：4 字节无符号大端整数
LENGTH_FORMAT = ">I"
LENGTH_SIZE = 4


class MessageType:
    """消息类型常量。"""

    # 握手
    HELLO = "HELLO"
    WELCOME = "WELCOME"
    VERSION_MISMATCH = "VERSION_MISMATCH"

    # 大厅
    JOIN_REQUEST = "JOIN_REQUEST"
    JOIN_ACCEPTED = "JOIN_ACCEPTED"
    JOIN_REJECTED = "JOIN_REJECTED"
    LOBBY_STATE = "LOBBY_STATE"
    PLAYER_READY = "PLAYER_READY"
    SET_CHARACTER = "SET_CHARACTER"
    ADD_AI = "ADD_AI"
    REMOVE_AI = "REMOVE_AI"
    KICK = "KICK"
    LEAVE = "LEAVE"
    GAME_START = "GAME_START"

    # 对局
    COMMAND = "COMMAND"
    COMMAND_RESULT = "COMMAND_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    GAME_EVENT = "GAME_EVENT"
    DECISION = "DECISION"

    # 连接维护
    PING = "PING"
    PONG = "PONG"
    ERROR = "ERROR"
    DISCONNECT = "DISCONNECT"
    RECONNECT = "RECONNECT"
    RECONNECT_OK = "RECONNECT_OK"
    RECONNECT_FAIL = "RECONNECT_FAIL"


#: 需要可靠送达的消息类型（全部走 TCP，这里仅用于日志级别）
QUIET_TYPES = frozenset({MessageType.PING, MessageType.PONG, MessageType.STATE_SNAPSHOT})


def encode(msg_type: str, payload: dict[str, Any] | None = None, seq: int = 0) -> bytes:
    """把消息编码为可发送的字节串。"""
    doc = {
        "protocol_version": PROTOCOL_VERSION,
        "type": msg_type,
        "seq": int(seq),
    }
    if payload:
        doc.update(payload)
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError(f"消息过大：{len(body)} 字节")
    return struct.pack(LENGTH_FORMAT, len(body)) + body


def decode_body(body: bytes) -> dict[str, Any]:
    """解析消息体（不含长度前缀）。"""
    doc = json.loads(body.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("消息必须是 JSON 对象")
    if "type" not in doc:
        raise ValueError("消息缺少 type 字段")
    return doc


class MessageDecoder:
    """流式解码器：把 TCP 字节流切分成一条条完整消息。"""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        """喂入新数据，返回本次能解析出的所有完整消息。"""
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []

        while True:
            if len(self._buffer) < LENGTH_SIZE:
                break
            (length,) = struct.unpack(LENGTH_FORMAT, bytes(self._buffer[:LENGTH_SIZE]))
            if length <= 0 or length > MAX_MESSAGE_BYTES:
                raise ValueError(f"非法消息长度：{length}")
            total = LENGTH_SIZE + length
            if len(self._buffer) < total:
                break
            body = bytes(self._buffer[LENGTH_SIZE:total])
            del self._buffer[:total]
            messages.append(decode_body(body))

        return messages

    def reset(self) -> None:
        self._buffer.clear()


def check_version(doc: dict[str, Any]) -> tuple[bool, str]:
    """检查协议版本。返回 (是否兼容, 说明)。"""
    version = doc.get("protocol_version")
    if version is None:
        return False, "对方没有携带协议版本，可能是不同版本的程序"
    if int(version) != PROTOCOL_VERSION:
        return False, (
            f"协议版本不兼容：本机 {PROTOCOL_VERSION}，对方 {version}。"
            "请让两台电脑使用同一份游戏文件。"
        )
    return True, ""


def make_error(code: str, message: str) -> dict[str, Any]:
    return {"error_code": code, "error_message": message}


#: 统一的中文错误提示
ERROR_MESSAGES = {
    "version_mismatch": "游戏版本不一致，无法联机",
    "room_full": "房间已满",
    "game_started": "游戏已经开始，无法加入",
    "name_taken": "昵称已被使用",
    "not_in_lobby": "房间不在大厅状态",
    "invalid_command": "无效的操作",
    "not_ready": "有玩家尚未准备",
    "not_enough_players": "至少需要 2 名参与者才能开始",
    "not_host": "只有房主可以进行该操作",
    "already_ready": "你已经准备好了",
    "reconnect_failed": "重连失败，无法找到该玩家",
    "server_closed": "房主已关闭房间",
}


def error_text(code: str) -> str:
    return ERROR_MESSAGES.get(code, f"未知错误：{code}")
