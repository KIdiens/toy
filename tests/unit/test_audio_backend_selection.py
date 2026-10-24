"""音频配置到真实后端的装配；以设备替身验收，不访问麦克风或扬声器。"""
from __future__ import annotations

import io
import struct
import sys
import wave

import pytest
from host.adapters.runtime import build_context
from host.audio.backend import Recording, recording_wav
from host.audio.null_backend import NullAudioBackend
from host.audio.sd_backend import SoundDeviceBackend
from host.config.settings import load_settings
from host.core.errors import ConfigError
from host.core.events import Command, CommandKind
from host.core.state_machine import Trigger
from host.main import build_env, main, parse_args

from tests.audio_devices import FakePortAudioError, FakeSoundDevice


def test_audio_defaults_and_cli_overrides_environment():
    assert load_settings({}).audio.backend == "null"
    args = parse_args([
        "--simulate", "--audio", "sd", "--audio-input-device", "2",
        "--audio-output-device", "USB Speaker", "--audio-sample-rate", "24000",
    ])
    settings = load_settings(build_env(args, {"AUDIO_BACKEND": "null"}))
    assert settings.audio.backend == "sd"
    assert settings.audio.input_device == 2
    assert settings.audio.output_device == "USB Speaker"
    assert settings.audio.sample_rate == 24000
    assert settings.redacted()["audio"]["backend"] == "sd"


@pytest.mark.parametrize("env, field", [
    ({"AUDIO_BACKEND": "typo"}, "AUDIO_BACKEND"),
    ({"AUDIO_SAMPLE_RATE": "0"}, "AUDIO_SAMPLE_RATE"),
    ({"AUDIO_SAMPLE_RATE": "192001"}, "AUDIO_SAMPLE_RATE"),
    ({"AUDIO_INPUT_DEVICE": "-1"}, "AUDIO_INPUT_DEVICE"),
])
def test_invalid_audio_configuration_is_rejected(env, field):
    with pytest.raises(ValueError, match=field):
        load_settings(env)


def test_default_context_does_not_import_sounddevice(tmp_path, monkeypatch):
    # 缺少真实依赖时，默认仿真仍应能启动并完成一次录制。
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "soundfile", None)
    context = build_context(load_settings({"DB_PATH": str(tmp_path / "null.db")}), simulate=True)
    try:
        assert isinstance(context.player.backend, NullAudioBackend)
        assert context.recorder.backend is context.player.backend
        assert context.recorder.record(0.5).duration_s == 0.5
    finally:
        context.close()


@pytest.fixture()
def audio_device(monkeypatch):
    module = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module, module.calls


def test_selected_audio_runs_pcm_to_asr_and_tts_to_selected_output(tmp_path, audio_device):
    _, calls = audio_device
    settings = load_settings({
        "DB_PATH": str(tmp_path / "sd.db"), "AUDIO_BACKEND": "sd",
        "AUDIO_INPUT_DEVICE": "2", "AUDIO_OUTPUT_DEVICE": "5",
        "AUDIO_SAMPLE_RATE": "24000",
    })
    context = build_context(settings, simulate=True)
    try:
        assert isinstance(context.player.backend, SoundDeviceBackend)
        assert context.recorder.backend is context.player.backend
        assert calls["input"] == [{
            "device": 2, "channels": 1, "dtype": "int16", "samplerate": 24000,
        }]
        assert calls["output"][0]["device"] == 5
        context.engine.apply(Trigger.DIALOG_PRESS)
        recording = context.recorder.record(0.5)
        context.engine.apply(Trigger.REC_STOP)  # 与真实录音→思考阶段一致，再检查 PCM/WAV。
        captured = []

        def transcribe(wav, lang=None):
            with wave.open(io.BytesIO(wav), "rb") as handle:
                captured.append((handle.getframerate(), handle.getnframes(),
                                 handle.readframes(handle.getnframes())))
            return "你好"

        context.router.asr.transcribe = transcribe
        generation = context.state_machine.generation
        context._rec_started_gen = generation
        context._last_recording_gen = generation
        context._last_recording = recording
        context._rec_ready.set()
        result = context._respond(Command(CommandKind.NET_REQUEST, generation=generation))
        assert captured == [(24000, 12000, struct.pack("<h", 6000) * 12000)]
        context.player.play(result.clip)
        assert calls["record"][0]["device"] == 2
        assert calls["play"][0][1]["device"] == 5
        assert calls["play"][0][0].size > 0
    finally:
        context.close()
    assert calls["streams"] and all(stream.closed for stream in calls["streams"])


def test_unavailable_device_fails_startup_without_starting_web(
    tmp_path, monkeypatch, capsys, audio_device,
):
    module, _ = audio_device

    def unavailable(**kwargs):
        raise FakePortAudioError("no input device")

    module.check_input_settings = unavailable
    monkeypatch.setenv("DB_PATH", str(tmp_path / "unavailable.db"))
    monkeypatch.setattr("host.main.uvicorn.run", lambda *a, **k: pytest.fail("不应启动网页"))
    assert main(["--simulate", "--audio", "sd"]) == 3
    err = capsys.readouterr().err
    assert "AUDIO_INPUT_DEVICE" in err and "--audio null" in err
    assert not (tmp_path / "unavailable.db").exists()


def test_later_assembly_failure_releases_audio(tmp_path, monkeypatch, audio_device):
    _, calls = audio_device
    closed = []
    original_close = SoundDeviceBackend.close

    def close(self):
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(SoundDeviceBackend, "close", close)

    def fail(_self):
        raise RuntimeError("invalid role pack")

    monkeypatch.setattr("host.roles.registry.RoleRegistry.load_all", fail)
    with pytest.raises(RuntimeError, match="invalid role pack"):
        build_context(load_settings({
            "DB_PATH": str(tmp_path / "failed.db"), "AUDIO_BACKEND": "sd",
        }), simulate=True)
    assert len(closed) == 1 and not closed[0]._open
    assert calls["streams"] == []
    # Windows 上连接未关闭会阻止重命名文件；清理后数据库文件应可移动。
    (tmp_path / "failed.db").rename(tmp_path / "closed.db")


@pytest.mark.parametrize("rate,channels", [(16000, 1), (24000, 2)])
def test_wav_encapsulation_preserves_pcm_and_metadata(rate, channels):
    pcm = struct.pack("<h", 1234) * 100 * channels
    result = recording_wav(Recording(data=pcm, sample_rate=rate, channels=channels))
    with wave.open(io.BytesIO(result), "rb") as handle:
        assert handle.getframerate() == rate
        assert handle.getnchannels() == channels
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 100
        assert handle.readframes(100) == pcm
    assert recording_wav(Recording(data=result, sample_rate=rate)) == result


def test_invalid_pcm_is_not_mislabeled_as_wav():
    with pytest.raises(ValueError, match="采样率"):
        recording_wav(Recording(data=b"\0\0", sample_rate=0))
    with pytest.raises(ValueError, match="不完整采样帧"):
        recording_wav(Recording(data=b"\0", sample_rate=16000))


def test_missing_real_audio_dependencies_are_reported(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(ConfigError, match="缺少音频依赖 sounddevice"):
        build_context(load_settings({
            "DB_PATH": str(tmp_path / "missing.db"), "AUDIO_BACKEND": "sd",
        }), simulate=True)
    assert not (tmp_path / "missing.db").exists()
