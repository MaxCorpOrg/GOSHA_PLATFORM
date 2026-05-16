#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

MANAGED_MARKERS = ("managed-by-gosha", "GoshaProxyLLM:")
DEFAULT_MODEL_NAME = "deepseek-v4-flash"
DEFAULT_TTS_ENGINE_PROFILE_ID = "tts-engine-edge-default"
DEFAULT_TTS_KIND = "edge_tts"
DEFAULT_TTS_MODULE = "EdgeTTS"
DEFAULT_TTS_TYPE = "edge"
DEFAULT_TTS_VOICE = "ru-RU-SvetlanaNeural"
DEFAULT_PROMPT_LINES = [
    "Ты — голосовой ассистент по имени Гоша.",
    "Всегда отвечай только по-русски, если оператор прямо не попросил другой язык.",
    "Никогда сам не переходи на китайский, японский или английский.",
    "Если фраза распознана неуверенно или выглядит искажённой, коротко попроси повторить по-русски.",
    "Отвечай доброжелательно, короткими понятными фразами.",
]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clamp_float(value, default, min_value, max_value):
    try:
        result = float(value)
    except Exception:
        result = float(default)
    if result < min_value:
        return min_value
    if result > max_value:
        return max_value
    return result


def rate_to_edge(rate_multiplier):
    percent = round((rate_multiplier - 1.0) * 100)
    return f"{percent:+d}%"


def pitch_to_edge(pitch_multiplier):
    hz = round((pitch_multiplier - 1.0) * 50)
    return f"{hz:+d}Hz"


def normalize_model(base_url: str, model_name: str) -> str:
    base = str(base_url or "").strip().lower()
    model = str(model_name or "").strip()
    if "api.deepseek.com" in base and model in ("", "deepseek-chat", "deepseek-reasoner", "gosha-assistant"):
        return DEFAULT_MODEL_NAME
    return model or DEFAULT_MODEL_NAME


def should_skip_existing(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    existing = config_path.read_text(encoding="utf-8", errors="ignore").strip()
    if existing in ("", "{}", "null"):
        return False
    return not any(marker in existing for marker in MANAGED_MARKERS)


def resolve_proxy_profile(app_root: Path) -> tuple[str, Path | None, dict]:
    profile_id = ""
    panel_env_path = app_root.parent / "env" / "panel.env"
    if panel_env_path.exists():
        try:
            for line in panel_env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("GOSHA_BACKEND_PROXY_PROFILE_ID="):
                    profile_id = line.split("=", 1)[1].strip()
                    break
        except Exception:
            profile_id = ""
    if not profile_id:
        return "", None, {}
    profile_path = app_root / "agents" / "profiles" / f"{profile_id}.json"
    return profile_id, profile_path, load_json(profile_path)


def resolve_default_assistant(app_root: Path) -> dict:
    assistants_dir = app_root / "agents" / "assistants"
    for path in sorted(assistants_dir.glob("*.json")):
        payload = load_json(path)
        if payload.get("enabled", True) and payload.get("is_default"):
            return payload
    return {}


def resolve_voice_profile_id(app_root: Path, assistant_payload: dict) -> str:
    voice_profile_id = str(assistant_payload.get("voice_profile_id", "") or "").strip()
    if voice_profile_id:
        return voice_profile_id
    bindings_dir = app_root / "agents" / "bindings"
    for path in sorted(bindings_dir.glob("*.json")):
        payload = load_json(path)
        candidate = str(payload.get("voice_profile_id", "") or "").strip()
        if candidate:
            return candidate
    return ""


def resolve_tts_selection(app_root: Path, voice_profile_id: str) -> tuple[str, dict, str, str, str]:
    voices_dir = app_root / "agents" / "voices"
    tts_engines_dir = app_root / "agents" / "tts_engines"
    voice_payload = load_json(voices_dir / f"{voice_profile_id}.json") if voice_profile_id else {}
    requested_profile_id = str(voice_payload.get("tts_engine_profile_id", "") or "").strip() or DEFAULT_TTS_ENGINE_PROFILE_ID
    tts_engine_payload = load_json(tts_engines_dir / f"{requested_profile_id}.json") if requested_profile_id else {}
    requested_kind = str(tts_engine_payload.get("engine_kind", "") or "").strip() or DEFAULT_TTS_KIND
    effective_module = str(tts_engine_payload.get("module_name", "") or "").strip() or DEFAULT_TTS_MODULE
    runtime_state = str(tts_engine_payload.get("runtime_state", "") or "").strip() or "ready"
    enabled = bool(tts_engine_payload.get("enabled", True))

    if requested_kind != DEFAULT_TTS_KIND:
        return requested_profile_id, voice_payload, DEFAULT_TTS_KIND, DEFAULT_TTS_MODULE, "requested_engine_not_live"
    if runtime_state != "ready":
        return requested_profile_id, voice_payload, DEFAULT_TTS_KIND, DEFAULT_TTS_MODULE, "requested_profile_not_ready"
    if not enabled:
        return requested_profile_id, voice_payload, DEFAULT_TTS_KIND, DEFAULT_TTS_MODULE, "requested_profile_disabled"
    return requested_profile_id, voice_payload, requested_kind, effective_module, "ready"


def resolve_prompt_lines(default_assistant_payload: dict) -> list[str]:
    custom_prompt = str(default_assistant_payload.get("system_prompt", "") or "").strip()
    if not custom_prompt:
        return list(DEFAULT_PROMPT_LINES)
    lines = [line.rstrip() for line in custom_prompt.splitlines()]
    return [line for line in lines if line] or [custom_prompt]


def build_asr_lines(asr_provider_key: str, tts_module: str, vosk_model_path: str) -> list[str]:
    if asr_provider_key == "VoskASR":
        return [
            "selected_module:",
            "  ASR: VoskASR",
            "  LLM: GoshaProxyLLM",
            f"  TTS: {tts_module}",
            "ASR:",
            "  VoskASR:",
            "    type: vosk",
            f"    model_path: {vosk_model_path}",
            "    output_dir: tmp/",
        ]
    return [
        "selected_module:",
        "  ASR: FunASR",
        "  LLM: GoshaProxyLLM",
        f"  TTS: {tts_module}",
        "ASR:",
        "  FunASR:",
        "    language: ru",
    ]


def update_profile_model(profile_path: Path | None, profile_payload: dict, model_name: str) -> None:
    if not profile_path or not profile_payload:
        return
    if str(profile_payload.get("model", "") or "").strip() == model_name:
        return
    profile_payload["model"] = model_name
    profile_path.write_text(json.dumps(profile_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_config(config_path: Path, ws_url: str, panel_port: str, proxy_token: str, app_root: Path, asr_provider_key: str, vosk_model_path: str) -> None:
    if should_skip_existing(config_path):
        return

    _, profile_path, profile_payload = resolve_proxy_profile(app_root)
    model_name = normalize_model(profile_payload.get("base_url", ""), profile_payload.get("model", ""))
    update_profile_model(profile_path, profile_payload, model_name)

    default_assistant_payload = resolve_default_assistant(app_root)
    voice_profile_id = resolve_voice_profile_id(app_root, default_assistant_payload)
    requested_tts_engine_profile_id, voice_payload, effective_tts_kind, effective_tts_module, effective_tts_runtime = resolve_tts_selection(app_root, voice_profile_id)

    tts_voice_name = str(voice_payload.get("voice_name", "") or "").strip() or DEFAULT_TTS_VOICE
    tts_speech_rate = clamp_float(voice_payload.get("speech_rate", 1.0), 1.0, 0.5, 2.0)
    tts_pitch = clamp_float(voice_payload.get("pitch", 1.0), 1.0, 0.5, 2.0)
    tts_rate = rate_to_edge(tts_speech_rate)
    tts_pitch_hz = pitch_to_edge(tts_pitch)
    prompt_lines = resolve_prompt_lines(default_assistant_payload)
    asr_lines = build_asr_lines(asr_provider_key, effective_tts_module, vosk_model_path)

    config_path.write_text(
        "\n".join(
            [
                "# managed-by-gosha",
                f"# requested-tts-engine-profile: {requested_tts_engine_profile_id or DEFAULT_TTS_ENGINE_PROFILE_ID}",
                f"# effective-tts-kind: {effective_tts_kind}",
                f"# effective-tts-module: {effective_tts_module}",
                f"# effective-tts-runtime: {effective_tts_runtime}",
                "server:",
                f"  websocket: {ws_url}",
                "prompt: |",
                *[f"  {line}" for line in prompt_lines],
                *asr_lines,
                "LLM:",
                "  GoshaProxyLLM:",
                "    type: openai",
                f"    model_name: {model_name}",
                f"    url: http://host.docker.internal:{panel_port}/api/internal/openai/v1",
                f"    api_key: {proxy_token}",
                "TTS:",
                f"  {effective_tts_module}:",
                f"    type: {DEFAULT_TTS_TYPE}",
                f"    voice: {tts_voice_name}",
                f"    speech_rate: {tts_speech_rate}",
                f"    pitch: {tts_pitch}",
                f"    rate: {tts_rate}",
                f"    pitch_hz: {tts_pitch_hz}",
                "    output_dir: tmp/",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 8:
        print(
            "usage: render_backend_config.py <config_path> <ws_url> <panel_port> <proxy_token> <app_root> <asr_provider_key> <vosk_model_path>",
            file=sys.stderr,
        )
        return 2

    config_path = Path(argv[1])
    ws_url = argv[2]
    panel_port = argv[3]
    proxy_token = argv[4]
    app_root = Path(argv[5])
    asr_provider_key = argv[6]
    vosk_model_path = argv[7]
    render_config(config_path, ws_url, panel_port, proxy_token, app_root, asr_provider_key, vosk_model_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
