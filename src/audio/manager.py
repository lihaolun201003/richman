"""音频管理：程序化音效 + BGM 接口。

设计原则：
- 不依赖任何外部音频文件，因此永远不会因为「资源缺失」崩溃；
- 音效由程序生成的简单波形合成，保证真的有声音；
- 接口完整：play_bgm / play_sfx / 三档音量 / 静音。

如果以后要接入真实音频资源，只要把 assets/audio 下的同名文件放进去，
_load_or_synth 会优先使用真实文件。
"""
from __future__ import annotations

import array
import math
import os
from typing import Any

from ..utils.logging_setup import get_logger
from ..utils.paths import assets_path

log = get_logger(__name__)

#: 合成参数
SAMPLE_RATE = 22050
BITS = -16
CHANNELS = 1


class AudioManager:
    """统一的音频出口。"""

    def __init__(self) -> None:
        self.enabled = False
        self.master_volume = 0.7
        self.bgm_volume = 0.45
        self.sfx_volume = 0.8
        self.muted = False
        self.sounds: dict[str, Any] = {}
        self.current_bgm: str = ""
        self._bgm_channel = None
        self.error: str = ""

    # ------------------------------------------------------------ 初始化

    def init(self) -> bool:
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=SAMPLE_RATE, size=BITS, channels=CHANNELS,
                                  buffer=512)
            self.enabled = True
            self._build_sounds()
            return True
        except Exception as exc:
            self.error = str(exc)
            self.enabled = False
            log.warning("音频初始化失败（将静音运行）：%s", exc)
            return False

    def _build_sounds(self) -> None:
        """优先用 assets/audio 下的文件，没有就程序合成。"""
        specs = {
            "click": ("click.wav", dict(freq=880, ms=70, wave="square", decay=6.0)),
            "hover": ("hover.wav", dict(freq=1200, ms=40, wave="sine", decay=8.0, volume=0.4)),
            "dice": ("dice.wav", dict(freq=240, ms=260, wave="noise", decay=3.0)),
            "move": ("move.wav", dict(freq=660, ms=90, wave="sine", decay=5.0, volume=0.5)),
            "coin": ("coin.wav", dict(freq=1046, ms=180, wave="sine", decay=4.0)),
            "buy": ("buy.wav", dict(freq=784, ms=260, wave="triangle", decay=3.5)),
            "upgrade": ("upgrade.wav", dict(freq=988, ms=300, wave="triangle", decay=3.0)),
            "rent": ("rent.wav", dict(freq=392, ms=260, wave="sine", decay=3.5)),
            "chance": ("chance.wav", dict(freq=1318, ms=340, wave="sine", decay=2.6)),
            "jail": ("jail.wav", dict(freq=180, ms=420, wave="square", decay=2.2)),
            "bankrupt": ("bankrupt.wav", dict(freq=140, ms=700, wave="saw", decay=1.6)),
            "win": ("win.wav", dict(freq=1046, ms=700, wave="triangle", decay=1.6)),
            "error": ("error.wav", dict(freq=200, ms=200, wave="square", decay=4.0)),
            "turn": ("turn.wav", dict(freq=740, ms=150, wave="sine", decay=4.0)),
        }
        for name, (filename, kwargs) in specs.items():
            sound = self._load_file(filename)
            if sound is None:
                sound = self._synth(**kwargs)
            if sound is not None:
                self.sounds[name] = sound

    def _load_file(self, filename: str):
        path = assets_path("audio", filename)
        if not os.path.isfile(path):
            return None
        try:
            import pygame

            return pygame.mixer.Sound(path)
        except Exception as exc:
            log.debug("音频文件加载失败 %s：%s", filename, exc)
            return None

    # ------------------------------------------------------------ 合成

    def _synth(
        self,
        freq: float = 440.0,
        ms: int = 150,
        wave: str = "sine",
        decay: float = 4.0,
        volume: float = 0.6,
        harmonics: tuple[float, ...] = (1.0,),
    ):
        """合成一段简单波形。失败返回 None（不影响游戏运行）。"""
        if not self.enabled:
            return None
        try:
            import pygame

            count = int(SAMPLE_RATE * ms / 1000.0)
            if count <= 0:
                return None
            buf = array.array("h")
            amp = 32767 * max(0.0, min(1.0, volume))
            rng_state = 0x12345678
            for i in range(count):
                t = i / float(SAMPLE_RATE)
                env = math.exp(-decay * (i / float(count)))
                if wave == "square":
                    value = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
                elif wave == "saw":
                    phase = (freq * t) % 1.0
                    value = 2.0 * phase - 1.0
                elif wave == "triangle":
                    phase = (freq * t) % 1.0
                    value = 4.0 * abs(phase - 0.5) - 1.0
                elif wave == "noise":
                    # 线性同余伪随机，够用且不引入依赖
                    rng_state = (1103515245 * rng_state + 12345) & 0x7FFFFFFF
                    value = (rng_state / 0x3FFFFFFF) - 1.0
                    # 噪声混一点低频让它更像骰子碰撞
                    value = 0.7 * value + 0.3 * math.sin(2 * math.pi * freq * t)
                else:
                    value = 0.0
                    for h, weight in enumerate(harmonics, start=1):
                        value += weight * math.sin(2 * math.pi * freq * h * t)
                    value /= max(1e-6, sum(harmonics))
                buf.append(int(max(-32767, min(32767, value * env * amp))))
            return pygame.mixer.Sound(buffer=buf.tobytes())
        except Exception as exc:
            log.debug("合成音效失败：%s", exc)
            return None

    # ------------------------------------------------------------ 播放

    def _effective(self, kind: str) -> float:
        if self.muted or not self.enabled:
            return 0.0
        base = self.bgm_volume if kind == "bgm" else self.sfx_volume
        return max(0.0, min(1.0, base * self.master_volume))

    def play_sfx(self, name: str, volume: float = 1.0) -> None:
        if not self.enabled or self.muted:
            return
        sound = self.sounds.get(name)
        if sound is None:
            return
        try:
            sound.set_volume(self._effective("sfx") * max(0.0, min(1.0, volume)))
            sound.play()
        except Exception as exc:  # pragma: no cover
            log.debug("播放音效失败 %s：%s", name, exc)

    def play_bgm(self, name: str = "main", loop: bool = True) -> None:
        """BGM 接口。当前没有背景音乐资源，调用是安全的空操作。"""
        self.current_bgm = name
        # 预留：把 assets/audio/bgm_<name>.ogg 放进来即可自动播放
        path = assets_path("audio", f"bgm_{name}.ogg")
        if not os.path.isfile(path) or not self.enabled:
            return
        try:
            import pygame

            if self._bgm_channel is None:
                self._bgm_channel = pygame.mixer.Channel(0)
            sound = pygame.mixer.Sound(path)
            sound.set_volume(self._effective("bgm"))
            self._bgm_channel.play(sound, loops=-1 if loop else 0)
        except Exception as exc:
            log.debug("BGM 播放失败：%s", exc)

    def stop_bgm(self) -> None:
        self.current_bgm = ""
        if self._bgm_channel is not None:
            try:
                self._bgm_channel.stop()
            except Exception:
                pass

    # ------------------------------------------------------------ 音量

    def set_master_volume(self, value: float) -> None:
        self.master_volume = max(0.0, min(1.0, float(value)))
        self._refresh_bgm_volume()

    def set_bgm_volume(self, value: float) -> None:
        self.bgm_volume = max(0.0, min(1.0, float(value)))
        self._refresh_bgm_volume()

    def set_sfx_volume(self, value: float) -> None:
        self.sfx_volume = max(0.0, min(1.0, float(value)))

    def set_muted(self, value: bool) -> None:
        self.muted = bool(value)
        if self.muted:
            self.stop_bgm()
            self.current_bgm = ""
        self._refresh_bgm_volume()

    def _refresh_bgm_volume(self) -> None:
        if self._bgm_channel is not None:
            try:
                self._bgm_channel.set_volume(self._effective("bgm"))
            except Exception:
                pass

    def apply_settings(self, settings) -> None:
        """从 Settings 同步全部音量。"""
        self.set_master_volume(settings.master_volume)
        self.set_bgm_volume(settings.bgm_volume)
        self.set_sfx_volume(settings.sfx_volume)
        self.set_muted(settings.muted)

    def toggle_mute(self) -> bool:
        self.set_muted(not self.muted)
        return self.muted

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return f"音频不可用（{self.error or '未知原因'}）"
        if self.muted:
            return "已静音"
        return f"主音量 {int(self.master_volume * 100)}%"


#: 全局单例，UI 直接使用
audio = AudioManager()
