#!/usr/bin/env python3

import json
import sys
import time
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: set_live_voice_profile.py <app_root> <assistant_profile_id> <voice_profile_id>",
            file=sys.stderr,
        )
        return 2

    app_root = Path(argv[1]).resolve()
    assistant_profile_id = argv[2].strip()
    voice_profile_id = argv[3].strip()
    if not assistant_profile_id or not voice_profile_id:
        print("assistant_profile_id and voice_profile_id must be non-empty", file=sys.stderr)
        return 2

    assistant_path = app_root / "agents" / "assistants" / f"{assistant_profile_id}.json"
    voice_path = app_root / "agents" / "voices" / f"{voice_profile_id}.json"

    if not assistant_path.exists():
        print(f"assistant profile not found: {assistant_path}", file=sys.stderr)
        return 1
    if not voice_path.exists():
        print(f"voice profile not found: {voice_path}", file=sys.stderr)
        return 1

    assistant_payload = load_json(assistant_path)
    voice_payload = load_json(voice_path)
    old_voice_profile_id = str(assistant_payload.get("voice_profile_id", "") or "").strip()
    assistant_payload["voice_profile_id"] = voice_profile_id
    assistant_payload["updated_at"] = int(time.time())
    save_json(assistant_path, assistant_payload)

    print(
        json.dumps(
            {
                "ok": True,
                "assistant_profile_id": assistant_profile_id,
                "old_voice_profile_id": old_voice_profile_id,
                "new_voice_profile_id": voice_profile_id,
                "voice_display_name": voice_payload.get("display_name", voice_profile_id),
                "tts_engine_profile_id": voice_payload.get("tts_engine_profile_id", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
