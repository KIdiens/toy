"""音频链路：半双工互斥（AC-21）、时长上限 / 削顶 / VAD、三档音量、内容峰值体检。"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest
from host.audio.backend import DEFAULT_SAMPLE_RATE, Recording
from host.audio.catalog import (
    PEAK_LIMIT_DBFS,
    ContentCatalog,
    analyze_pcm16,
    read_wav_pcm,
    wav_duration_s,
)
from host.audio.null_backend import NullAudioBackend
from host.audio.player import VOLUME_LEVELS, Player
from host.audio.recorder import MAX_RECORD_SECONDS, Recorder


class _StubClip:
    """最小音频片段（只需 ``duration_s``，与 ``Player.play`` 的鸭子类型契约一致）。"""

    def __init__(self, seconds: float) -> None:
        self.duration_s = seconds


class _FakeBackend:
    """可编程后端：指定录制时长 / 峰值，并记录播放音量。"""

    name = "fake"

    def __init__(self, *, seconds: float = 2.0, peak: int = 0, sample_rate: int = 16000) -> None:
        self.seconds = seconds
        self.peak = peak
        self.sample_rate = sample_rate
        self.played: list[float] = []

    def open(self) -> None: ...
    def close(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def busy(self) -> bool:
        return False

    def record(self, max_seconds: float) -> Recording:
        frames = int(self.seconds * self.sample_rate)
        data = struct.pack("<h", self.peak) * frames
        return Recording(data=data, sample_rate=self.sample_rate, channels=1,
                         duration_s=self.seconds)

    def play(self, clip, *, volume: float = 1.0) -> None:
        self.played.append(volume)


# ------------------------------------------------------------------ AC-21
def test_playback_and_capture_mutually_exclusive() -> None:
    backend = NullAudioBackend()
    player = Player(backend)
    recorder = Recorder(backend, player=player)

    player.play(_StubClip(0.5))
    assert backend.busy is True
    with pytest.raises(RuntimeError, match="半双工"):
        backend.record(3.0)

    recorded = recorder.record(3.0)  # 录制前自动停播
    assert recorded is not None
    assert backend.busy is False


def test_capture_and_playback_share_exactly_one_backend(sim_context) -> None:
    assert sim_context.player.backend is sim_context.recorder.backend, "全局只允许一个音频后端"


# ------------------------------------------------------------------ 录制策略
def test_recording_is_capped_at_fifteen_seconds() -> None:
    recorder = Recorder(_FakeBackend(seconds=20.0))
    result = recorder.record(30.0)

    assert result.duration_s == pytest.approx(MAX_RECORD_SECONDS)
    expected_frames = int(MAX_RECORD_SECONDS * DEFAULT_SAMPLE_RATE)
    assert len(result.data) == expected_frames * 2


def test_default_limit_is_fifteen_seconds() -> None:
    recorder = Recorder(_FakeBackend(seconds=18.0))
    assert recorder.record().duration_s == pytest.approx(MAX_RECORD_SECONDS)


def test_clipping_is_detected() -> None:
    loud = Recorder(_FakeBackend(seconds=1.0, peak=32767)).record(5.0)
    assert loud.clipped is True

    normal = Recorder(_FakeBackend(seconds=1.0, peak=8000)).record(5.0)
    assert normal.clipped is False


def test_silence_is_reported_as_no_speech() -> None:
    recorder = Recorder(NullAudioBackend())
    recorded = recorder.record(2.0)
    assert recorded.has_speech is False, "环境静音不应被当成语音"


# ------------------------------------------------------------------ 音量与进度
def test_three_volume_levels_and_cycle() -> None:
    backend = _FakeBackend()
    player = Player(backend, level="low")
    assert player.cycle_level() == "mid"
    assert player.cycle_level() == "high"
    assert player.cycle_level() == "low"
    assert VOLUME_LEVELS["low"] < VOLUME_LEVELS["mid"] < VOLUME_LEVELS["high"]


def test_unknown_volume_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知音量档位"):
        Player(_FakeBackend(), level="max")


def test_play_passes_current_level_to_backend_and_reports_duration() -> None:
    backend = _FakeBackend()
    player = Player(backend, level="high")
    played_ms = player.play(_StubClip(1.25))

    assert played_ms == 1250
    assert backend.played == [VOLUME_LEVELS["high"]]
    assert player.progress() == {"played_ms": 1250, "total_ms": 1250}


# ------------------------------------------------------------------ 内容库
def _write_wav(path: Path, *, amplitude: int, seconds: float = 0.2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * DEFAULT_SAMPLE_RATE)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(DEFAULT_SAMPLE_RATE)
        handle.writeframes(struct.pack("<h", amplitude) * frames)
    return path


def test_peak_check_flags_clipping_and_loud_tracks(tmp_path: Path) -> None:
    loud = _write_wav(tmp_path / "loud.wav", amplitude=32767)
    pcm, rate, channels = read_wav_pcm(loud)
    assert rate == DEFAULT_SAMPLE_RATE and channels == 1
    report = analyze_pcm16(pcm, channels=channels)
    assert report.clipping is True and report.ok is False and report.reason

    quiet = _write_wav(tmp_path / "quiet.wav", amplitude=6000)
    quiet_pcm, _r, _c = read_wav_pcm(quiet)
    quiet_report = analyze_pcm16(quiet_pcm)
    assert quiet_report.ok is True
    assert quiet_report.peak_dbfs < PEAK_LIMIT_DBFS


def test_catalog_lists_and_analyzes_local_content(tmp_path: Path) -> None:
    _write_wav(tmp_path / "story" / "小熊.wav", amplitude=6000)
    catalog = ContentCatalog(tmp_path)
    items = catalog.list()
    assert [item.id for item in items] == ["story:小熊"]
    assert wav_duration_s(tmp_path / "story" / "小熊.wav") == pytest.approx(0.2)

    assert catalog.analyze("story:小熊").ok is True
    with pytest.raises(ValueError, match="内容不存在"):
        catalog.analyze("story:没有这个")
    with pytest.raises(ValueError, match="未知内容类型"):
        catalog.list("video")
