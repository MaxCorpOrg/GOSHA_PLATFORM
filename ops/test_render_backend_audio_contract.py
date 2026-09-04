#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_SCRIPT = REPO_ROOT / "ops" / "render_backend_config.py"


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_app_root(root):
    app_root = root / "app_root"
    env_root = root / "env"
    env_root.mkdir(parents=True)
    (env_root / "panel.env").write_text("GOSHA_BACKEND_PROXY_PROFILE_ID=deepseek-test\n", encoding="utf-8")

    write_json(
        app_root / "agents" / "profiles" / "deepseek-test.json",
        {
            "profile_id": "deepseek-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
    )
    write_json(
        app_root / "agents" / "assistants" / "assistant-gosha-default.json",
        {
            "profile_id": "assistant-gosha-default",
            "display_name": "Gosha default",
            "voice_profile_id": "voice-ru-silero-kseniya",
            "is_default": True,
            "enabled": True,
        },
    )
    write_json(
        app_root / "agents" / "voices" / "voice-ru-silero-kseniya.json",
        {
            "profile_id": "voice-ru-silero-kseniya",
            "tts_engine_profile_id": "tts-engine-silero-live-test",
            "voice_name": "kseniya",
            "speech_rate": 1.0,
            "pitch": 1.0,
            "enabled": True,
        },
    )
    write_json(
        app_root / "agents" / "tts_engines" / "tts-engine-silero-live-test.json",
        {
            "profile_id": "tts-engine-silero-live-test",
            "engine_kind": "silero_tts",
            "module_name": "SileroTTS",
            "runtime_state": "ready",
            "enabled": True,
            "config": {
                "model_id": "v5_5_ru",
                "speaker": "kseniya",
                "sample_rate": 24000,
                "device": "cpu",
                "cache_dir": "/opt/xiaozhi-esp32-server/models/silero",
                "language": "ru",
                "use_ssml": True,
                "put_accent": True,
                "put_yo": True,
                "num_threads": 2,
            },
        },
    )
    return app_root


def render_config(audio_sample_rate=None):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        app_root = prepare_app_root(root)
        config_path = root / ".config.yaml"
        args = [
            sys.executable,
            "-B",
            str(RENDER_SCRIPT),
            str(config_path),
            "ws://voice.example.invalid/xiaozhi/v1/",
            "18876",
            "test-token",
            str(app_root),
            "VoskASR",
            "/opt/xiaozhi-esp32-server/models/vosk/vosk-model-small-ru-0.22",
        ]
        if audio_sample_rate is not None:
            args.append(str(audio_sample_rate))
        result = subprocess.run(args, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return config_path.read_text(encoding="utf-8")


def assert_xiaozhi_sample_rate(text, sample_rate):
    expected = (
        "xiaozhi:\n"
        "  audio_params:\n"
        "    format: opus\n"
        f"    sample_rate: {sample_rate}\n"
        "    channels: 1\n"
        "    frame_duration: 60\n"
    )
    assert expected in text


def test_default_gosha_v1_audio_contract_is_16khz():
    text = render_config()
    assert_xiaozhi_sample_rate(text, 16000)
    assert "selected_module:\n  ASR: VoskASR\n  LLM: GoshaProxyLLM\n  TTS: SileroTTS\n" in text
    assert "TTS:\n  SileroTTS:\n    type: silero_local\n" in text
    assert "\n    sample_rate: 24000\n" in text


def test_supported_audio_sample_rate_override_is_explicit():
    text = render_config(audio_sample_rate=24000)
    assert_xiaozhi_sample_rate(text, 24000)


def test_unsupported_audio_sample_rate_falls_back_to_gosha_v1_default():
    text = render_config(audio_sample_rate=44100)
    assert_xiaozhi_sample_rate(text, 16000)


def test_installer_keeps_device_audio_rate_separate_from_silero_model_rate():
    install_script = (REPO_ROOT / "ops" / "install_server.sh").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / "backend" / "selfhost-backend.env.example").read_text(encoding="utf-8")

    assert 'AUDIO_SAMPLE_RATE="${SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE:-16000}"' in install_script
    assert 'SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE=${AUDIO_SAMPLE_RATE}' in install_script
    assert '"SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE" "${AUDIO_SAMPLE_RATE}"' in install_script
    assert "SELFHOST_XIAOZHI_AUDIO_SAMPLE_RATE=16000" in env_example
    assert "SELFHOST_XIAOZHI_SILERO_SAMPLE_RATE=24000" in env_example


if __name__ == "__main__":
    test_default_gosha_v1_audio_contract_is_16khz()
    test_supported_audio_sample_rate_override_is_explicit()
    test_unsupported_audio_sample_rate_falls_back_to_gosha_v1_default()
    test_installer_keeps_device_audio_rate_separate_from_silero_model_rate()
    print("render backend audio contract tests: OK")
