#!/usr/bin/env python3
"""Prepare/activate a per-robot GPT-Live assistant using existing profile APIs."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform"))
import gosha_agent_store as providers
import gosha_assistant_store as assistants
from gosha_live_protocol import EFFORTS, VOICES, session_config
from selfhost_xiaozhi_common import find_claim_by_robot, load_env


def proposed_profiles(robot_id, voice="marin", effort="medium"):
    if not providers.safe_robot_id(robot_id):
        raise ValueError("invalid_robot_id")
    effective = assistants.effective_robot_assistant_config(robot_id)
    assistant = dict(effective.get("assistant_profile") or {})
    provider_id = "live-gpt55-" + robot_id
    assistant_id = "live-assistant-" + robot_id
    provider = {
        "profile_id": provider_id, "display_name": "OpenAI GPT-5.5",
        "base_url": "https://api.openai.com/v1", "model": "gpt-5.5",
        "api_key_env": "OPENAI_API_KEY", "enabled": True, "is_default": False,
        "max_tokens": 4096, "timeout_seconds": 60,
    }
    assistant.update({
        "profile_id": assistant_id, "display_name": "Гоша — GPT-Live-1 / GPT-5.5",
        "assistant_name": assistant.get("assistant_name") or "Гоша",
        "voice_engine": "openai_live", "live_voice": voice, "live_reasoning_effort": effort,
        "provider_profile_id": provider_id, "model_override": "gpt-5.5",
        "is_default": False, "enabled": True,
    })
    return provider, assistant


def apply_profiles(robot_id, provider, assistant):
    if not find_claim_by_robot(robot_id):
        raise ValueError("robot_claim_required")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("openai_api_key_missing")
    session_config(assistant, provider)
    targets = [
        providers.PROFILES_DIR / (provider["profile_id"] + ".json"),
        assistants.ASSISTANTS_DIR / (assistant["profile_id"] + ".json"),
        providers.binding_path(robot_id),
    ]
    originals = {path: path.read_bytes() if path.exists() else None for path in targets}
    backup_dir = providers.APP_ROOT / "voice_live_backups" / uuid.uuid4().hex
    backup_dir.mkdir(parents=True, mode=0o700)
    manifest = []
    for index, (path, data) in enumerate(originals.items()):
        manifest.append({"path": str(path.relative_to(providers.APP_ROOT)), "existed": data is not None, "backup": str(index)})
        if data is not None:
            target = backup_dir / str(index)
            target.write_bytes(data)
            target.chmod(0o600)
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        providers.save_agent_profile(provider["profile_id"], provider)
        assistants.save_assistant_profile(assistant["profile_id"], assistant)
        assistants.save_robot_binding(robot_id, {"assistant_profile_id": assistant["profile_id"]})
    except Exception:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                providers.save_json_atomic(path, json.loads(data))
        raise
    return backup_dir


def main():
    parser = argparse.ArgumentParser(description="GPT-Live-1 + GPT-5.5: план, проверка API и отдельная активация профиля.")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--providers-env", type=Path, help="Защищённый серверный файл; значения не выводятся")
    parser.add_argument("--voice", choices=VOICES, default="marin")
    parser.add_argument("--reasoning-effort", choices=EFFORTS, default="medium")
    parser.add_argument("--probe", action="store_true", help="Короткая платная проверка API без робота")
    parser.add_argument("--apply", action="store_true", help="Проверить API, сохранить профили и выбрать для робота")
    args = parser.parse_args()
    if args.providers_env:
        key = load_env(args.providers_env).get("OPENAI_API_KEY", "")
        if key:
            os.environ["OPENAI_API_KEY"] = key
    provider, assistant = proposed_profiles(args.robot_id, args.voice, args.reasoning_effort)
    result = {"voice_model": "gpt-live-1", "reasoning_model": "gpt-5.5", "voice": args.voice,
              "reasoning_effort": args.reasoning_effort, "api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
              "profile_applied": False, "robot_contacted": False}
    if args.probe or args.apply:
        if args.apply and not find_claim_by_robot(args.robot_id):
            raise ValueError("robot_claim_required")
        from probe_gpt_live import probe
        result["api_probe"] = asyncio.run(probe(session_config(assistant, provider), os.environ.get("OPENAI_API_KEY", "")))
        if not result["api_probe"]["ok"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
    if args.apply:
        result["backup_directory"] = str(apply_profiles(args.robot_id, provider, assistant))
        result["profile_applied"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not leak provider errors, source text or keys.
        print(json.dumps({"ok": False, "error_category": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
