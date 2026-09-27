"""用户设置：读写 config/user_settings.json。

默认值来自 config/default.json；用户改动只写 user_settings.json，
这样升级游戏时不会覆盖用户的个人偏好。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from ..utils.logging_setup import get_logger
from ..utils.paths import default_config_path, ensure_dir, user_config_dir

log = get_logger(__name__)

USER_SETTINGS_FILE = "user_settings.json"

#: 动画速度可选项（F3 循环微调用）
ANIM_SPEED_CHOICES = [("0.5x", 0.5), ("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)]

#: 游戏节奏：只影响演出时长（动画 / 棋子移动 / 事件展示 / AI 思考展示），
#: 不改变任何规则、网络 tick 或逻辑结果。
PACE_CHOICES = [("慢", 0.7), ("标准", 1.0), ("快", 1.6)]

#: 字体大小档位
FONT_SCALE_CHOICES = [("小", 0.9), ("默认", 1.0), ("大", 1.15)]

DEFAULT_SETTINGS: dict[str, Any] = {
    "display": {
        "width": 1600,
        "height": 900,
        "fullscreen": False,
        "vsync": True,
    },
    "audio": {
        "master_volume": 0.7,
        "bgm_volume": 0.45,
        "sfx_volume": 0.8,
        "muted": False,
        "sfx_enabled": True,
        "bgm_enabled": True,
    },
    "ui": {
        "animation_speed": 1.0,
        "font_scale": 1.0,
        "debug_overlay": False,
        "show_tooltips": True,
        "detailed_log": True,
        "show_tutorial_hint": True,
        "tutorial_done": False,
    },
    "player": {
        "nickname": "玩家",
        "character_id": "char_ajin",
        "color_id": "",
    },
    "game": {
        "map_file": "default_map.json",
        "preset": "standard",
    },
    "network": {
        "last_host": "127.0.0.1",
        "last_port": 28080,
        "last_room_name": "",
        "auto_discovery": True,
    },
}

#: 允许选择的分辨率
RESOLUTION_CHOICES = [(1280, 720), (1440, 810), (1600, 900), (1920, 1080)]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """把 override 合并进 base 的副本，逐层覆盖。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    """用户设置对象。"""

    def __init__(self, data: dict[str, Any] | None = None, path: str | None = None) -> None:
        self.path = path or os.path.join(user_config_dir(), USER_SETTINGS_FILE)
        self.data = _deep_merge(DEFAULT_SETTINGS, data or {})
        self.loaded_from_disk = False

    # ------------------------------------------------------------ 读写
    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        target = path or os.path.join(user_config_dir(), USER_SETTINGS_FILE)
        data: dict[str, Any] = {}
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("用户设置读取失败，改用默认值：%s", exc)
                data = {}
        else:
            # 首次运行：尝试合并 config/default.json 里的 ui/audio 段
            default_file = default_config_path()
            if os.path.isfile(default_file):
                try:
                    with open(default_file, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                    for key in ("display", "audio", "ui"):
                        if key in doc:
                            data.setdefault(key, doc[key])
                except (OSError, json.JSONDecodeError):
                    pass
        st = cls(data, target)
        st.loaded_from_disk = os.path.isfile(target)
        return st

    def save(self) -> bool:
        try:
            ensure_dir(os.path.dirname(os.path.abspath(self.path)))
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            log.warning("保存用户设置失败：%s", exc)
            return False

    # ------------------------------------------------------------ 访问
    def get(self, section: str, key: str, default: Any = None) -> Any:
        return (self.data.get(section) or {}).get(key, default)

    # ---- 带类型保护的读取：配置文件被手工改错时也不应让游戏起不来
    def _as_float(self, section: str, key: str, default: float) -> float:
        try:
            value = float(self.get(section, key, default))
        except (TypeError, ValueError):
            log.warning("设置 %s.%s 的值无效，使用默认值 %s", section, key, default)
            return float(default)
        return value

    def _as_int(self, section: str, key: str, default: int) -> int:
        try:
            value = int(float(self.get(section, key, default)))
        except (TypeError, ValueError):
            log.warning("设置 %s.%s 的值无效，使用默认值 %s", section, key, default)
            return int(default)
        return value

    def _as_bool(self, section: str, key: str, default: bool) -> bool:
        value = self.get(section, key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(value, (int, float)):
            return bool(value)
        return bool(default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.data.setdefault(section, {})[key] = value

    # ---- 显示
    @property
    def width(self) -> int:
        return self._as_int("display", "width", 1600)

    @property
    def height(self) -> int:
        return self._as_int("display", "height", 900)

    @property
    def fullscreen(self) -> bool:
        return self._as_bool("display", "fullscreen", False)

    def set_resolution(self, width: int, height: int) -> None:
        self.set("display", "width", int(width))
        self.set("display", "height", int(height))

    def set_fullscreen(self, value: bool) -> None:
        self.set("display", "fullscreen", bool(value))

    # ---- 音频
    @property
    def master_volume(self) -> float:
        return self._as_float("audio", "master_volume", 0.7)

    @property
    def bgm_volume(self) -> float:
        return self._as_float("audio", "bgm_volume", 0.45)

    @property
    def sfx_volume(self) -> float:
        return self._as_float("audio", "sfx_volume", 0.8)

    @property
    def muted(self) -> bool:
        return self._as_bool("audio", "muted", False)

    @property
    def sfx_enabled(self) -> bool:
        return self._as_bool("audio", "sfx_enabled", True)

    @property
    def bgm_enabled(self) -> bool:
        return self._as_bool("audio", "bgm_enabled", True)

    # ---- UI
    @property
    def animation_speed(self) -> float:
        return self._as_float("ui", "animation_speed", 1.0)

    @property
    def font_scale(self) -> float:
        return self._as_float("ui", "font_scale", 1.0)

    @property
    def debug_overlay(self) -> bool:
        return self._as_bool("ui", "debug_overlay", False)

    @property
    def show_tooltips(self) -> bool:
        return self._as_bool("ui", "show_tooltips", True)

    @property
    def detailed_log(self) -> bool:
        """是否在事件日志里显示细节行（关闭后只保留关键事件，便于小屏阅读）。"""
        return self._as_bool("ui", "detailed_log", True)

    @property
    def show_tutorial_hint(self) -> bool:
        return self._as_bool("ui", "show_tutorial_hint", True)

    def set_pace(self, speed: float) -> None:
        """设置游戏节奏（本质就是动画速度，语义化入口）。"""
        self.set("ui", "animation_speed", float(speed))

    def pace_label(self) -> str:
        speed = self.animation_speed
        for label, value in PACE_CHOICES:
            if abs(value - speed) < 0.02:
                return label
        return f"{speed:g}x"

    # ---- 玩家
    @property
    def nickname(self) -> str:
        return str(self.get("player", "nickname", "玩家")) or "玩家"

    @property
    def character_id(self) -> str:
        return str(self.get("player", "character_id", "char_ajin"))

    @property
    def color_id(self) -> str:
        return str(self.get("player", "color_id", ""))

    def set_nickname(self, name: str) -> None:
        self.set("player", "nickname", (name or "玩家")[:12])

    def set_character(self, character_id: str, color_id: str = "") -> None:
        self.set("player", "character_id", character_id)
        if color_id:
            self.set("player", "color_id", color_id)

    # ---- 网络
    @property
    def last_host(self) -> str:
        return str(self.get("network", "last_host", "127.0.0.1"))

    @property
    def last_port(self) -> int:
        return self._as_int("network", "last_port", 28080)

    @property
    def last_room_name(self) -> str:
        return str(self.get("network", "last_room_name", ""))

    @property
    def auto_discovery(self) -> bool:
        return self._as_bool("network", "auto_discovery", True)

    def remember_server(self, host: str, port: int) -> None:
        self.set("network", "last_host", host)
        self.set("network", "last_port", int(port))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Settings {self.width}x{self.height} speed={self.animation_speed}>"
