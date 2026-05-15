#!/usr/bin/env python3
import json
import os
import tempfile
import time
from pathlib import Path

import gosha_agent_store as provider_store


APP_ROOT = Path(os.environ.get("APP_ROOT", "/opt/gosha_platform/runtime/app_root")).resolve()
AGENTS_ROOT = APP_ROOT / "agents"
ASSISTANTS_DIR = AGENTS_ROOT / "assistants"
VOICES_DIR = AGENTS_ROOT / "voices"
MEMORY_PROFILES_DIR = AGENTS_ROOT / "memory"
MCP_BUNDLES_DIR = AGENTS_ROOT / "mcp_bundles"
KNOWLEDGE_DIR = AGENTS_ROOT / "knowledge"
SCREENS_DIR = AGENTS_ROOT / "screens"
WAKE_DIR = AGENTS_ROOT / "wake"
APPLY_TARGET_SERVER = "server"
APPLY_TARGET_FIRMWARE_SYNC_REQUIRED = "firmware_sync_required"
DEFERRED_NOTE = "Сохранится в платформе сейчас и будет применено после отдельной синхронизации с прошивкой или следующего OTA-профиля."
KNOWLEDGE_DEFERRED_NOTE = "Профиль базы знаний сохранён, но загрузка и индексация документов будут доведены отдельным серверным контуром."


def now_ts():
    return int(time.time())


def ensure_layout():
    provider_store.ensure_agent_layout()
    for path in (
        ASSISTANTS_DIR,
        VOICES_DIR,
        MEMORY_PROFILES_DIR,
        MCP_BUNDLES_DIR,
        KNOWLEDGE_DIR,
        SCREENS_DIR,
        WAKE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _safe_id(value):
    return provider_store.safe_profile_id(value)


def _load_json(path, default):
    return provider_store.load_json(path, default)


def _save_json_atomic(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, target)


def _id_path(root, profile_id):
    if not _safe_id(profile_id):
        raise ValueError("invalid profile_id")
    return Path(root) / f"{str(profile_id).strip()}.json"


def _text(value, default=""):
    return str(value or default).strip()


def _value(payload, *keys, default=None):
    source = payload if isinstance(payload, dict) else {}
    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)
    return default


def _bool(value, default=False):
    if value is None:
        return bool(default)
    return bool(value)


def _int(value, default=0, min_value=None, max_value=None):
    try:
        result = int(value)
    except Exception:
        result = int(default)
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _float(value, default=0.0, min_value=None, max_value=None):
    try:
        result = float(value)
    except Exception:
        result = float(default)
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _string_list(value):
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [part.strip() for part in value.replace("\r", "\n").replace(",", "\n").split("\n")]
    else:
        items = []
    result = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _validate_ref(profile_id, getter, label, *, require_enabled=True):
    clean_id = _text(profile_id)
    if not clean_id:
        return None
    profile = getter(clean_id)
    if not profile:
        raise ValueError(f"{label} not found")
    if require_enabled and not profile.get("enabled", True):
        raise ValueError(f"{label} is disabled")
    return profile


def _catalog_item(value, label):
    return {"value": str(value), "label": str(label)}


def assistant_language_catalog():
    return [
        _catalog_item("ru-RU", "Русский"),
        _catalog_item("en-US", "Английский"),
        _catalog_item("de-DE", "Немецкий"),
        _catalog_item("fr-FR", "Французский"),
    ]


def voice_type_catalog():
    return [
        _catalog_item("adult", "Взрослый"),
        _catalog_item("child", "Детский"),
        _catalog_item("neutral", "Нейтральный"),
    ]


def memory_type_catalog():
    return [
        _catalog_item("short_term", "Кратковременная память"),
        _catalog_item("hybrid", "Смешанная память"),
        _catalog_item("long_term", "Долговременная память"),
    ]


def knowledge_state_catalog():
    return [
        _catalog_item("not_configured", "Не настроено"),
        _catalog_item("pending", "Ожидает подготовки"),
        _catalog_item("ready", "Готово"),
        _catalog_item("error", "Ошибка"),
    ]


def wake_mode_catalog():
    return [
        _catalog_item("disabled", "Без пробуждения"),
        _catalog_item("standard", "Стандартное пробуждение"),
        _catalog_item("custom", "Пользовательское пробуждение"),
    ]


def screen_theme_catalog():
    return [
        _catalog_item("dark", "Тёмная тема"),
        _catalog_item("light", "Светлая тема"),
        _catalog_item("system", "Системная тема"),
    ]


def screen_face_family_catalog():
    return [
        _catalog_item("otto", "Стандартные лица OTTO"),
        _catalog_item("gosha", "Лица Гоши"),
        _catalog_item("minimal", "Минимальный стиль"),
    ]


def default_assistant_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "assistant_name": "",
        "role_template": "",
        "role_description": "",
        "system_prompt": "",
        "dialogue_language": "ru-RU",
        "provider_profile_id": "",
        "model_override": "",
        "voice_profile_id": "",
        "memory_profile_id": "",
        "mcp_bundle_id": "",
        "knowledge_profile_id": "",
        "enabled": True,
        "is_default": False,
        "created_at": 0,
        "updated_at": 0,
    }


def default_voice_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "voice_name": "",
        "provider_label": "",
        "language": "ru-RU",
        "voice_type": "adult",
        "speech_rate": 1.0,
        "pitch": 1.0,
        "enabled": True,
        "created_at": 0,
        "updated_at": 0,
    }


def default_memory_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "memory_type": "short_term",
        "memory_by_speaker": False,
        "allow_clear_memory": True,
        "enabled": True,
        "created_at": 0,
        "updated_at": 0,
    }


def default_mcp_bundle(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "official_services": [],
        "custom_services": [],
        "enabled": True,
        "created_at": 0,
        "updated_at": 0,
    }


def default_knowledge_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "mode": "directory_manifest",
        "entries": [],
        "indexing_state": "not_configured",
        "enabled": True,
        "created_at": 0,
        "updated_at": 0,
    }


def default_screen_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "theme": "dark",
        "face_family": "gosha",
        "brightness": 80,
        "state_emotions": {
            "idle": "neutral",
            "listening": "happy",
            "thinking": "thinking",
            "speaking": "talking",
            "error": "sad",
            "network": "link",
        },
        "enabled": True,
        "apply_mode": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
        "created_at": 0,
        "updated_at": 0,
    }


def default_wake_profile(profile_id=""):
    return {
        "profile_id": _text(profile_id),
        "display_name": "",
        "wake_name": "GOSHA",
        "wake_display_name": "Гоша",
        "mode": "custom",
        "sensitivity": 20,
        "enabled": True,
        "apply_mode": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
        "created_at": 0,
        "updated_at": 0,
    }


def _normalize_common(root_default, profile_id, raw):
    base = dict(root_default(profile_id))
    payload = raw if isinstance(raw, dict) else {}
    clean_id = _text(_value(payload, "profile_id", default=profile_id), profile_id)
    if not _safe_id(clean_id):
        raise ValueError("invalid profile_id")
    base["profile_id"] = clean_id
    base["display_name"] = _text(_value(payload, "display_name", "name", default=clean_id), clean_id) or clean_id
    base["enabled"] = _bool(_value(payload, "enabled", default=True), True)
    for field in ("created_at", "updated_at"):
        base[field] = _int(payload.get(field, 0), 0, 0)
    return base, payload


def normalize_assistant_profile(profile_id, raw):
    base, payload = _normalize_common(default_assistant_profile, profile_id, raw)
    base["assistant_name"] = _text(_value(payload, "assistant_name", "assistant_label", default=base["display_name"]), base["display_name"]) or base["display_name"]
    base["role_template"] = _text(_value(payload, "role_template", default=""))
    base["role_description"] = _text(_value(payload, "role_description", "role_introduction", default=""))
    base["system_prompt"] = _text(_value(payload, "system_prompt", "prompt", default=""))
    base["dialogue_language"] = _text(_value(payload, "dialogue_language", "language", default="ru-RU"), "ru-RU") or "ru-RU"
    base["provider_profile_id"] = _text(_value(payload, "provider_profile_id", "active_profile_id", default=""))
    base["model_override"] = _text(_value(payload, "model_override", "model", default=""))
    base["voice_profile_id"] = _text(_value(payload, "voice_profile_id", default=""))
    base["memory_profile_id"] = _text(_value(payload, "memory_profile_id", default=""))
    base["mcp_bundle_id"] = _text(_value(payload, "mcp_bundle_id", default=""))
    base["knowledge_profile_id"] = _text(_value(payload, "knowledge_profile_id", default=""))
    base["is_default"] = _bool(_value(payload, "is_default", "default", default=False), False)
    if base["provider_profile_id"]:
        _validate_ref(base["provider_profile_id"], provider_store.get_agent_profile, "provider profile")
    for ref_key, getter, label in (
        ("voice_profile_id", get_voice_profile, "voice profile"),
        ("memory_profile_id", get_memory_profile, "memory profile"),
        ("mcp_bundle_id", get_mcp_bundle, "mcp bundle"),
        ("knowledge_profile_id", get_knowledge_profile, "knowledge profile"),
    ):
        if base[ref_key]:
            _validate_ref(base[ref_key], getter, label)
    return base


def normalize_voice_profile(profile_id, raw):
    base, payload = _normalize_common(default_voice_profile, profile_id, raw)
    base["voice_name"] = _text(_value(payload, "voice_name", "provider_voice_name", default=""))
    base["provider_label"] = _text(_value(payload, "provider_label", "voice_role", default=""))
    base["language"] = _text(_value(payload, "language", "dialogue_language", default="ru-RU"), "ru-RU") or "ru-RU"
    voice_type = _text(_value(payload, "voice_type", "type", default="adult"), "adult") or "adult"
    if voice_type not in {item["value"] for item in voice_type_catalog()}:
        voice_type = "adult"
    base["voice_type"] = voice_type
    base["speech_rate"] = _float(_value(payload, "speech_rate", "speech_speed", default=1.0), 1.0, 0.5, 2.0)
    base["pitch"] = _float(_value(payload, "pitch", "voice_pitch", default=1.0), 1.0, 0.5, 2.0)
    return base


def normalize_memory_profile(profile_id, raw):
    base, payload = _normalize_common(default_memory_profile, profile_id, raw)
    memory_type = _text(_value(payload, "memory_type", "type", default="short_term"), "short_term") or "short_term"
    if memory_type not in {item["value"] for item in memory_type_catalog()}:
        memory_type = "short_term"
    base["memory_type"] = memory_type
    base["memory_by_speaker"] = _bool(_value(payload, "memory_by_speaker", default=False), False)
    base["allow_clear_memory"] = _bool(_value(payload, "allow_clear_memory", "allow_clear", default=True), True)
    return base


def normalize_mcp_bundle(profile_id, raw):
    base, payload = _normalize_common(default_mcp_bundle, profile_id, raw)
    base["official_services"] = _string_list(payload.get("official_services", []))
    base["custom_services"] = _string_list(payload.get("custom_services", []))
    return base


def normalize_knowledge_profile(profile_id, raw):
    base, payload = _normalize_common(default_knowledge_profile, profile_id, raw)
    base["mode"] = _text(_value(payload, "mode", default="directory_manifest"), "directory_manifest") or "directory_manifest"
    state = _text(_value(payload, "indexing_state", "state", default="not_configured"), "not_configured") or "not_configured"
    if state not in {item["value"] for item in knowledge_state_catalog()}:
        state = "not_configured"
    base["indexing_state"] = state
    base["entries"] = _string_list(_value(payload, "entries", "documents", default=[]))
    return base


def normalize_screen_profile(profile_id, raw):
    base, payload = _normalize_common(default_screen_profile, profile_id, raw)
    theme = _text(payload.get("theme", "dark"), "dark") or "dark"
    if theme not in {item["value"] for item in screen_theme_catalog()}:
        theme = "dark"
    base["theme"] = theme
    face_family = _text(payload.get("face_family", "gosha"), "gosha") or "gosha"
    if face_family not in {item["value"] for item in screen_face_family_catalog()}:
        face_family = "gosha"
    base["face_family"] = face_family
    base["brightness"] = _int(_value(payload, "brightness", default=80), 80, 1, 100)
    raw_states = _value(payload, "state_emotions", "status_faces", default={})
    raw_states = raw_states if isinstance(raw_states, dict) else {}
    default_states = default_screen_profile(profile_id)["state_emotions"]
    state_emotions = {}
    for key, fallback in default_states.items():
        state_emotions[key] = _text(raw_states.get(key, fallback), fallback) or fallback
    base["state_emotions"] = state_emotions
    base["apply_mode"] = APPLY_TARGET_FIRMWARE_SYNC_REQUIRED
    return base


def normalize_wake_profile(profile_id, raw):
    base, payload = _normalize_common(default_wake_profile, profile_id, raw)
    mode = _text(_value(payload, "mode", "wake_mode", default="custom"), "custom") or "custom"
    if mode not in {item["value"] for item in wake_mode_catalog()}:
        mode = "custom"
    base["wake_name"] = _text(_value(payload, "wake_name", "wake_word", default="GOSHA"), "GOSHA") or "GOSHA"
    base["wake_display_name"] = _text(_value(payload, "wake_display_name", "displayed_wake_name", default="Гоша"), "Гоша") or "Гоша"
    base["mode"] = mode
    base["sensitivity"] = _int(_value(payload, "sensitivity", default=20), 20, 1, 99)
    base["apply_mode"] = APPLY_TARGET_FIRMWARE_SYNC_REQUIRED
    return base


def _list_profiles(root, normalize_fn):
    ensure_layout()
    items = []
    for path in sorted(Path(root).glob("*.json")):
        raw = _load_json(path, {})
        try:
            items.append(normalize_fn(path.stem, raw))
        except Exception:
            continue
    return items


def _get_profile(root, profile_id, normalize_fn):
    ensure_layout()
    path = _id_path(root, profile_id)
    if not path.exists():
        return None
    return normalize_fn(profile_id, _load_json(path, {}))


def _save_profile(root, profile_id, payload, normalize_fn, *, unique_default=False):
    ensure_layout()
    existing = _get_profile(root, profile_id, normalize_fn)
    normalized = normalize_fn(profile_id, payload)
    timestamp = now_ts()
    normalized["created_at"] = existing["created_at"] if existing else timestamp
    normalized["updated_at"] = timestamp
    if unique_default and normalized.get("is_default"):
        for current in _list_profiles(root, normalize_fn):
            if current["profile_id"] == normalized["profile_id"] or not current.get("is_default"):
                continue
            current["is_default"] = False
            _save_json_atomic(_id_path(root, current["profile_id"]), current)
    _save_json_atomic(_id_path(root, profile_id), normalized)
    return normalized


def list_assistant_profiles():
    return _list_profiles(ASSISTANTS_DIR, normalize_assistant_profile)


def get_assistant_profile(profile_id):
    return _get_profile(ASSISTANTS_DIR, profile_id, normalize_assistant_profile)


def save_assistant_profile(profile_id, payload):
    return _save_profile(ASSISTANTS_DIR, profile_id, payload, normalize_assistant_profile, unique_default=True)


def default_assistant():
    for item in list_assistant_profiles():
        if item.get("is_default") and item.get("enabled"):
            return item
    return None


def list_voice_profiles():
    return _list_profiles(VOICES_DIR, normalize_voice_profile)


def get_voice_profile(profile_id):
    return _get_profile(VOICES_DIR, profile_id, normalize_voice_profile)


def save_voice_profile(profile_id, payload):
    return _save_profile(VOICES_DIR, profile_id, payload, normalize_voice_profile)


def list_memory_profiles():
    return _list_profiles(MEMORY_PROFILES_DIR, normalize_memory_profile)


def get_memory_profile(profile_id):
    return _get_profile(MEMORY_PROFILES_DIR, profile_id, normalize_memory_profile)


def save_memory_profile(profile_id, payload):
    return _save_profile(MEMORY_PROFILES_DIR, profile_id, payload, normalize_memory_profile)


def list_mcp_bundles():
    return _list_profiles(MCP_BUNDLES_DIR, normalize_mcp_bundle)


def get_mcp_bundle(profile_id):
    return _get_profile(MCP_BUNDLES_DIR, profile_id, normalize_mcp_bundle)


def save_mcp_bundle(profile_id, payload):
    return _save_profile(MCP_BUNDLES_DIR, profile_id, payload, normalize_mcp_bundle)


def list_knowledge_profiles():
    return _list_profiles(KNOWLEDGE_DIR, normalize_knowledge_profile)


def get_knowledge_profile(profile_id):
    return _get_profile(KNOWLEDGE_DIR, profile_id, normalize_knowledge_profile)


def save_knowledge_profile(profile_id, payload):
    return _save_profile(KNOWLEDGE_DIR, profile_id, payload, normalize_knowledge_profile)


def list_screen_profiles():
    return _list_profiles(SCREENS_DIR, normalize_screen_profile)


def get_screen_profile(profile_id):
    return _get_profile(SCREENS_DIR, profile_id, normalize_screen_profile)


def save_screen_profile(profile_id, payload):
    return _save_profile(SCREENS_DIR, profile_id, payload, normalize_screen_profile)


def list_wake_profiles():
    return _list_profiles(WAKE_DIR, normalize_wake_profile)


def get_wake_profile(profile_id):
    return _get_profile(WAKE_DIR, profile_id, normalize_wake_profile)


def save_wake_profile(profile_id, payload):
    return _save_profile(WAKE_DIR, profile_id, payload, normalize_wake_profile)


def public_assistant_profile(profile):
    item = normalize_assistant_profile(profile.get("profile_id", ""), profile)
    provider_profile = provider_store.get_agent_profile(item.get("provider_profile_id", "")) if item.get("provider_profile_id") else None
    return {
        **item,
        "apply_target": APPLY_TARGET_SERVER,
        "provider_profile": provider_store.profile_public_view(provider_profile) if provider_profile else None,
    }


def public_voice_profile(profile):
    item = normalize_voice_profile(profile.get("profile_id", ""), profile)
    return {**item, "apply_target": APPLY_TARGET_SERVER}


def public_memory_profile(profile):
    item = normalize_memory_profile(profile.get("profile_id", ""), profile)
    return {**item, "apply_target": APPLY_TARGET_SERVER}


def public_mcp_bundle(profile):
    item = normalize_mcp_bundle(profile.get("profile_id", ""), profile)
    return {**item, "apply_target": APPLY_TARGET_SERVER}


def public_knowledge_profile(profile):
    item = normalize_knowledge_profile(profile.get("profile_id", ""), profile)
    return {
        **item,
        "apply_target": APPLY_TARGET_SERVER,
        "deferred_note": KNOWLEDGE_DEFERRED_NOTE,
    }


def public_screen_profile(profile):
    item = normalize_screen_profile(profile.get("profile_id", ""), profile)
    return {
        **item,
        "apply_target": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
        "deferred_note": DEFERRED_NOTE,
    }


def public_wake_profile(profile):
    item = normalize_wake_profile(profile.get("profile_id", ""), profile)
    return {
        **item,
        "apply_target": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
        "deferred_note": DEFERRED_NOTE,
    }


def default_robot_binding(robot_id):
    base = dict(provider_store.default_binding(robot_id))
    base.update(
        {
            "assistant_profile_id": "",
            "voice_profile_id": "",
            "memory_profile_id": "",
            "mcp_bundle_id": "",
            "knowledge_profile_id": "",
            "screen_profile_id": "",
            "wake_profile_id": "",
        }
    )
    return base


def load_robot_binding(robot_id):
    ensure_layout()
    raw = _load_json(provider_store.binding_path(robot_id), {})
    base = default_robot_binding(robot_id)
    if isinstance(raw, dict):
        for key in (
            "active_profile_id",
            "fallback_profile_id",
            "assistant_profile_id",
            "voice_profile_id",
            "memory_profile_id",
            "mcp_bundle_id",
            "knowledge_profile_id",
            "screen_profile_id",
            "wake_profile_id",
        ):
            base[key] = _text(raw.get(key, ""))
        for field in ("created_at", "updated_at"):
            base[field] = _int(raw.get(field, 0), 0, 0)
    return base


def save_robot_binding(robot_id, payload):
    if not provider_store.safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    ensure_layout()
    binding = load_robot_binding(robot_id)
    updates = payload if isinstance(payload, dict) else {}
    for key, getter, label in (
        ("active_profile_id", provider_store.get_agent_profile, "active profile"),
        ("fallback_profile_id", provider_store.get_agent_profile, "fallback profile"),
        ("assistant_profile_id", get_assistant_profile, "assistant profile"),
        ("voice_profile_id", get_voice_profile, "voice profile"),
        ("memory_profile_id", get_memory_profile, "memory profile"),
        ("mcp_bundle_id", get_mcp_bundle, "mcp bundle"),
        ("knowledge_profile_id", get_knowledge_profile, "knowledge profile"),
        ("screen_profile_id", get_screen_profile, "screen profile"),
        ("wake_profile_id", get_wake_profile, "wake profile"),
    ):
        value = _text(updates.get(key, binding.get(key, "")))
        if value:
            _validate_ref(value, getter, label)
        binding[key] = value
    timestamp = now_ts()
    if binding["created_at"] <= 0:
        binding["created_at"] = timestamp
    binding["updated_at"] = timestamp
    _save_json_atomic(provider_store.binding_path(robot_id), binding)
    return binding


def _resolve_with_override(binding_value, assistant_value, getter, public_fn, warnings, label):
    selected_id = _text(binding_value) or _text(assistant_value)
    source = "binding" if _text(binding_value) else ("assistant_profile" if _text(assistant_value) else "")
    if not selected_id:
        return None, source
    profile = getter(selected_id)
    if not profile:
        warnings.append(f"{label}: профиль `{selected_id}` не найден.")
        return None, source
    if not profile.get("enabled", True):
        warnings.append(f"{label}: профиль `{selected_id}` отключён.")
        return None, source
    return public_fn(profile), source


def effective_robot_assistant_config(robot_id):
    if not provider_store.safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    binding = load_robot_binding(robot_id)
    warnings = []
    legacy = provider_store.effective_robot_agent(robot_id)
    assistant = get_assistant_profile(binding.get("assistant_profile_id", "")) if binding.get("assistant_profile_id") else None
    if not assistant and not binding.get("assistant_profile_id"):
        assistant = default_assistant()
    assistant_view = public_assistant_profile(assistant) if assistant and assistant.get("enabled", True) else None
    if binding.get("assistant_profile_id") and not assistant_view:
        warnings.append("Профиль ассистента не найден или отключён.")

    provider_view = None
    provider_source = "legacy_agent"
    provider_error = ""
    if assistant_view and assistant_view.get("provider_profile_id"):
        provider_source = "assistant_profile"
        provider = provider_store.get_agent_profile(assistant_view.get("provider_profile_id", ""))
        if not provider:
            provider_error = "provider profile not found"
            warnings.append("У выбранного ассистента не найден профиль поставщика ИИ.")
        elif not provider.get("enabled"):
            provider_error = "provider profile is disabled"
            warnings.append("У выбранного ассистента профиль поставщика ИИ отключён.")
        else:
            provider_view = provider_store.profile_public_view(provider)
            if not provider_store.resolve_api_key(provider):
                warnings.append("У выбранного поставщика ИИ не найден ключ доступа в переменных окружения.")
    else:
        provider_view = legacy.get("effective_profile")
        warnings.extend(legacy.get("warnings", []))

    voice_view, voice_source = _resolve_with_override(
        binding.get("voice_profile_id", ""),
        (assistant_view or {}).get("voice_profile_id", ""),
        get_voice_profile,
        public_voice_profile,
        warnings,
        "Голос",
    )
    memory_view, memory_source = _resolve_with_override(
        binding.get("memory_profile_id", ""),
        (assistant_view or {}).get("memory_profile_id", ""),
        get_memory_profile,
        public_memory_profile,
        warnings,
        "Память",
    )
    mcp_view, mcp_source = _resolve_with_override(
        binding.get("mcp_bundle_id", ""),
        (assistant_view or {}).get("mcp_bundle_id", ""),
        get_mcp_bundle,
        public_mcp_bundle,
        warnings,
        "Набор MCP",
    )
    knowledge_view, knowledge_source = _resolve_with_override(
        binding.get("knowledge_profile_id", ""),
        (assistant_view or {}).get("knowledge_profile_id", ""),
        get_knowledge_profile,
        public_knowledge_profile,
        warnings,
        "База знаний",
    )
    screen_view, screen_source = _resolve_with_override(
        binding.get("screen_profile_id", ""),
        "",
        get_screen_profile,
        public_screen_profile,
        warnings,
        "Экран",
    )
    wake_view, wake_source = _resolve_with_override(
        binding.get("wake_profile_id", ""),
        "",
        get_wake_profile,
        public_wake_profile,
        warnings,
        "Пробуждение",
    )

    if screen_view:
        warnings.append(screen_view["deferred_note"])
    if wake_view:
        warnings.append(wake_view["deferred_note"])
    if knowledge_view and knowledge_view.get("indexing_state") != "ready":
        warnings.append(KNOWLEDGE_DEFERRED_NOTE)

    sections = {
        "assistant": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": "binding_or_default",
            "profile": assistant_view,
        },
        "provider": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": provider_source,
            "profile": provider_view,
            "error": provider_error,
        },
        "voice": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": voice_source,
            "profile": voice_view,
        },
        "memory": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": memory_source,
            "profile": memory_view,
        },
        "mcp": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": mcp_source,
            "profile": mcp_view,
        },
        "knowledge": {
            "apply_target": APPLY_TARGET_SERVER,
            "source": knowledge_source,
            "profile": knowledge_view,
        },
        "screen": {
            "apply_target": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
            "source": screen_source,
            "profile": screen_view,
        },
        "wake": {
            "apply_target": APPLY_TARGET_FIRMWARE_SYNC_REQUIRED,
            "source": wake_source,
            "profile": wake_view,
        },
    }
    ready_provider = bool(provider_view and not provider_error)
    state = "ready" if ready_provider else "missing"
    if ready_provider and (screen_view or wake_view):
        state = "server_ready_pending_firmware"
    elif assistant_view and not ready_provider:
        state = "provider_missing"
    elif not assistant_view and legacy.get("effective_profile"):
        state = legacy.get("state", "default_inherited")
    return {
        "robot_id": robot_id,
        "state": state,
        "binding": binding,
        "legacy_agent": legacy,
        "assistant_profile": assistant_view,
        "provider_profile": provider_view,
        "voice_profile": voice_view,
        "memory_profile": memory_view,
        "mcp_bundle": mcp_view,
        "knowledge_profile": knowledge_view,
        "screen_profile": screen_view,
        "wake_profile": wake_view,
        "effective_config": sections,
        "warnings": list(dict.fromkeys([item for item in warnings if _text(item)])),
    }


def catalog_snapshot():
    return {
        "providers": [provider_store.profile_public_view(item) for item in provider_store.list_agent_profiles()],
        "assistants": [public_assistant_profile(item) for item in list_assistant_profiles()],
        "voices": [public_voice_profile(item) for item in list_voice_profiles()],
        "memory_profiles": [public_memory_profile(item) for item in list_memory_profiles()],
        "mcp_bundles": [public_mcp_bundle(item) for item in list_mcp_bundles()],
        "knowledge_profiles": [public_knowledge_profile(item) for item in list_knowledge_profiles()],
        "screen_profiles": [public_screen_profile(item) for item in list_screen_profiles()],
        "wake_profiles": [public_wake_profile(item) for item in list_wake_profiles()],
        "catalogs": {
            "dialogue_languages": assistant_language_catalog(),
            "voice_types": voice_type_catalog(),
            "memory_types": memory_type_catalog(),
            "knowledge_states": knowledge_state_catalog(),
            "wake_modes": wake_mode_catalog(),
            "screen_themes": screen_theme_catalog(),
            "screen_face_families": screen_face_family_catalog(),
            "apply_targets": [
                _catalog_item(APPLY_TARGET_SERVER, "Применяется на сервере"),
                _catalog_item(APPLY_TARGET_FIRMWARE_SYNC_REQUIRED, "Требует отдельной синхронизации с прошивкой"),
            ],
        },
    }
