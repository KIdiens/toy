"""播放器：三档音量、播放进度、停止（与 ``Recorder`` 共享后端构成半双工）。

- 音量三档 ``low/mid/high``（§7.1.1 音量键），映射到 0..1 的线性系数；音量值真源在本模块，
  状态机只负责「按当前状态路由音量意图」。
- ``play`` 立即返回时长（真实后端内部阻塞到播完；空后端零等待）。
- ``stop`` 可被录音请求打断：``Recorder.record`` 会先调用本类的 ``stop``（AC-21）。
"""
from __future__ import annotations

from threading import Event

from host.audio.backend import DEFAULT_SAMPLE_RATE

VOLUME_LEVELS: dict[str, float] = {"low": 0.3, "mid": 0.6, "high": 1.0}
LEVEL_ORDER: tuple[str, ...] = ("low", "mid", "high")
DEFAULT_LEVEL: str = "mid"


class Player:
    """在共享后端上串行化播放；维护音量档位与播放进度。"""

    def __init__(self, backend, *, level: str = DEFAULT_LEVEL, clock=None) -> None:
        if level not in VOLUME_LEVELS:
            raise ValueError(f"未知音量档位: {level}")
        self.backend = backend
        self.clock = clock
        self._level = level
        self.playing = False
        self.progress_ms = 0
        self.total_ms = 0
        self.last_played_ms = 0

    @property
    def level(self) -> str:
        return self._level

    @property
    def volume(self) -> float:
        return VOLUME_LEVELS[self._level]

    def set_level(self, level: str) -> str:
        if level not in VOLUME_LEVELS:
            raise ValueError(f"未知音量档位: {level}")
        self._level = level
        return self._level

    def peek_next_level(self) -> str:
        """**不改变状态**地返回下一档。

        音量键必须「先落库、再生效」，所以需要先拿到目标档位交给 `patch_settings`：
        若先 `cycle_level()` 再写库，写库失败就会留下「档位已变、设置未落库」的分叉。
        """
        index = LEVEL_ORDER.index(self._level)
        return LEVEL_ORDER[(index + 1) % len(LEVEL_ORDER)]

    def cycle_level(self) -> str:
        """低→中→高→低（音量键的内部迁移）。

        生产路径**不**直接调用它——按键改档走 ``patch_settings``，由那里统一
        「先落库、再 set_level」，保证持久化与运行期档位同成同败。
        """
        return self.set_level(self.peek_next_level())

    def play(self, clip, *, sample_rate: int | None = None, cancel: Event | None = None) -> int:
        """播放片段，返回时长毫秒。真实后端内部阻塞；空后端立即返回。"""
        if cancel is not None and cancel.is_set():
            return 0
        self.backend.open()
        duration = int(float(getattr(clip, "duration_s", 0.0)) * 1000)
        self.total_ms = duration
        self.progress_ms = 0
        self.playing = True
        try:
            if cancel is not None and getattr(self.backend, "supports_cancel", False):
                self.backend.play(clip, volume=self.volume, cancel=cancel)
            else:
                self.backend.play(clip, volume=self.volume)
        finally:
            self.playing = False
        if cancel is not None and cancel.is_set():
            self.progress_ms = self.last_played_ms = 0
            return 0  # 中断不算作整段已经播放完成。
        self.progress_ms = duration
        self.last_played_ms = duration
        return duration

    def stop(self) -> None:
        self.backend.stop()
        self.playing = False
        self.progress_ms = 0

    def progress(self) -> dict[str, int]:
        return {"played_ms": self.progress_ms, "total_ms": self.total_ms}


def default_sample_rate() -> int:
    return DEFAULT_SAMPLE_RATE
