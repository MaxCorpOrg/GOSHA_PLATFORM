#!/usr/bin/env python3
import json
import os
import re
import tempfile
import time
from pathlib import Path


APP_ROOT = Path(os.environ.get("APP_ROOT", "/opt/gosha_platform/runtime/app_root")).resolve()
AGENTS_ROOT = APP_ROOT / "agents"
PROFILES_DIR = AGENTS_ROOT / "profiles"
BINDINGS_DIR = AGENTS_ROOT / "bindings"
PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
ROBOT_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
PROVIDER_KIND_OPENAI_COMPATIBLE = "openai_compatible"


def now_ts():
    return int(time.time())


def env_or_file_value(var_name, file_var_name):
    file_path = str(os.environ.get(file_var_name, "") or "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return ""
    return str(os.environ.get(var_name, "") or "").strip()


def ensure_agent_layout():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    BINDINGS_DIR.mkdir(parents=True, exist_ok=True)


def safe_profile_id(profile_id):
    return bool(PROFILE_ID_RE.fullmatch(str(profile_id or "").strip()))


def safe_robot_id(robot_id):
    return bool(ROBOT_ID_RE.fullmatch(str(robot_id or "").strip()))


def profile_path(profile_id):
    if not safe_profile_id(profile_id):
        raise ValueError("invalid profile_id")
    return PROFILES_DIR / f"{profile_id}.json"


def binding_path(robot_id):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    return BINDINGS_DIR / f"{robot_id}.json"


def load_json(path, default):
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except Exception:
        return default
    try:
        data = json.loads(raw)
    except Exception:
        return default
    return data if data is not None else default


def save_json_atomic(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, target)


def supported_provider_catalog():
    return [
        {
            "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
            "name": "OpenAI-совместимый провайдер",
            "description": "Любой узел с совместимым API: OpenAI, DeepSeek, локальные совместимые шлюзы и другие.",
        }
    ]


def default_agent_profile(profile_id=""):
    return {
        "profile_id": str(profile_id or "").strip(),
        "display_name": "",
        "provider_kind": PROVIDER_KIND_OPENAI_COMPATIBLE,
        "base_url": "",
        "model": "",
        "api_key_env": "",
        "enabled": True,
        "is_default": False,
        "temperature": 0.7,
        "max_tokens": 800,
        "top_p": 1.0,
        "timeout_seconds": 30,
        "system_prompt": "",
        "headers": {},
        "metadata": {},
        "created_at": 0,
        "updated_at": 0,
    }


def normalize_headers(raw):
    result = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        clean_value = str(value or "").strip()
        if not clean_value:
            continue
        result[clean_key] = clean_value
    return result


def normalize_profile(profile_id, raw):
    base = default_agent_profile(profile_id)
    payload = raw if isinstance(raw, dict) else {}
    clean_id = str(payload.get("profile_id", profile_id) or profile_id).strip()
    if not safe_profile_id(clean_id):
        raise ValueError("invalid profile_id")
    provider_kind = str(payload.get("provider_kind", base["provider_kind"]) or base["provider_kind"]).strip().lower()
    if provider_kind != PROVIDER_KIND_OPENAI_COMPATIBLE:
        raise ValueError("unsupported provider_kind")
    base["profile_id"] = clean_id
    base["display_name"] = str(payload.get("display_name", clean_id) or clean_id).strip() or clean_id
    base["provider_kind"] = provider_kind
    base["base_url"] = str(payload.get("base_url", "") or "").strip().rstrip("/")
    base["model"] = str(payload.get("model", "") or "").strip()
    base["api_key_env"] = str(payload.get("api_key_env", "") or "").strip()
    base["enabled"] = bool(payload.get("enabled", True))
    base["is_default"] = bool(payload.get("is_default", False))
    base["system_prompt"] = str(payload.get("system_prompt", "") or "").strip()
    base["headers"] = normalize_headers(payload.get("headers", {}))
    base["metadata"] = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    for field, default_value in (("temperature", 0.7), ("top_p", 1.0)):
        try:
            value = float(payload.get(field, default_value))
        except Exception:
            value = default_value
        base[field] = value
    for field, default_value in (("max_tokens", 800), ("timeout_seconds", 30)):
        try:
            value = max(1, int(payload.get(field, default_value)))
        except Exception:
            value = default_value
        base[field] = value
    for field in ("created_at", "updated_at"):
        try:
            base[field] = max(0, int(payload.get(field, 0) or 0))
        except Exception:
            base[field] = 0
    if base["base_url"] and not base["base_url"].startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    return base


def list_agent_profiles():
    ensure_agent_layout()
    items = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        raw = load_json(path, {})
        profile_id = path.stem
        try:
            items.append(normalize_profile(profile_id, raw))
        except Exception:
            continue
    return items


def get_agent_profile(profile_id):
    ensure_agent_layout()
    path = profile_path(profile_id)
    if not path.exists():
        return None
    return normalize_profile(profile_id, load_json(path, {}))


def save_agent_profile(profile_id, payload):
    ensure_agent_layout()
    existing = get_agent_profile(profile_id)
    normalized = normalize_profile(profile_id, payload)
    timestamp = now_ts()
    normalized["created_at"] = existing["created_at"] if existing else timestamp
    normalized["updated_at"] = timestamp
    if normalized["is_default"]:
        for current in list_agent_profiles():
            if current["profile_id"] == normalized["profile_id"] or not current.get("is_default"):
                continue
            current["is_default"] = False
            save_json_atomic(profile_path(current["profile_id"]), current)
    save_json_atomic(profile_path(profile_id), normalized)
    return normalized


def default_profile():
    for profile in list_agent_profiles():
        if profile.get("is_default") and profile.get("enabled"):
            return profile
    return None


def default_binding(robot_id):
    return {
        "robot_id": str(robot_id or "").strip(),
        "active_profile_id": "",
        "fallback_profile_id": "",
        "created_at": 0,
        "updated_at": 0,
    }


def load_robot_binding(robot_id):
    ensure_agent_layout()
    path = binding_path(robot_id)
    raw = load_json(path, {})
    base = default_binding(robot_id)
    if isinstance(raw, dict):
        base["active_profile_id"] = str(raw.get("active_profile_id", "") or "").strip()
        base["fallback_profile_id"] = str(raw.get("fallback_profile_id", "") or "").strip()
        for field in ("created_at", "updated_at"):
            try:
                base[field] = max(0, int(raw.get(field, 0) or 0))
            except Exception:
                pass
    return base


def save_robot_binding(robot_id, active_profile_id, fallback_profile_id=""):
    ensure_agent_layout()
    if active_profile_id:
        profile = get_agent_profile(active_profile_id)
        if not profile:
            raise ValueError("active profile not found")
        if not profile.get("enabled"):
            raise ValueError("active profile is disabled")
    if fallback_profile_id:
        fallback = get_agent_profile(fallback_profile_id)
        if not fallback:
            raise ValueError("fallback profile not found")
        if not fallback.get("enabled"):
            raise ValueError("fallback profile is disabled")
    binding = load_robot_binding(robot_id)
    timestamp = now_ts()
    if binding["created_at"] <= 0:
        binding["created_at"] = timestamp
    binding["updated_at"] = timestamp
    binding["active_profile_id"] = str(active_profile_id or "").strip()
    binding["fallback_profile_id"] = str(fallback_profile_id or "").strip()
    save_json_atomic(binding_path(robot_id), binding)
    return binding


def profile_public_view(profile):
    item = normalize_profile(profile.get("profile_id", ""), profile)
    return {
        "profile_id": item["profile_id"],
        "display_name": item["display_name"],
        "provider_kind": item["provider_kind"],
        "base_url": item["base_url"],
        "model": item["model"],
        "api_key_env": item["api_key_env"],
        "secret_configured": bool(resolve_api_key(item)),
        "enabled": item["enabled"],
        "is_default": item["is_default"],
        "temperature": item["temperature"],
        "max_tokens": item["max_tokens"],
        "top_p": item["top_p"],
        "timeout_seconds": item["timeout_seconds"],
        "system_prompt": item["system_prompt"],
        "headers": item["headers"],
        "metadata": item["metadata"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def resolve_api_key(profile):
    item = normalize_profile(profile.get("profile_id", ""), profile)
    env_name = item.get("api_key_env", "")
    if not env_name:
        return ""
    return str(os.environ.get(env_name, "") or "").strip()


def effective_robot_agent(robot_id):
    if not safe_robot_id(robot_id):
        raise ValueError("invalid robot_id")
    binding = load_robot_binding(robot_id)
    active_profile = get_agent_profile(binding.get("active_profile_id", "")) if binding.get("active_profile_id") else None
    fallback_profile = get_agent_profile(binding.get("fallback_profile_id", "")) if binding.get("fallback_profile_id") else None
    inherited_default = None
    state = "missing"
    warnings = []
    if active_profile and active_profile.get("enabled"):
        state = "ready"
    elif not binding.get("active_profile_id"):
        inherited_default = default_profile()
        if inherited_default:
            state = "default_inherited"
    else:
        warnings.append("Активный профиль не найден или отключён.")
    selected = active_profile if active_profile and active_profile.get("enabled") else inherited_default
    if not selected and fallback_profile and fallback_profile.get("enabled"):
        selected = fallback_profile
        state = "fallback"
    if selected and not resolve_api_key(selected):
        warnings.append("Не найден ключ доступа к ИИ-провайдеру в переменных окружения.")
    return {
        "robot_id": robot_id,
        "state": state,
        "binding": binding,
        "active_profile": profile_public_view(active_profile) if active_profile else None,
        "fallback_profile": profile_public_view(fallback_profile) if fallback_profile else None,
        "effective_profile": profile_public_view(selected) if selected else None,
        "warnings": warnings,
    }


def gateway_health_snapshot():
    profiles = list_agent_profiles()
    defaults = [item for item in profiles if item.get("is_default")]
    enabled = [item for item in profiles if item.get("enabled")]
    return {
        "storage_backend": "file",
        "profiles_count": len(profiles),
        "enabled_profiles_count": len(enabled),
        "default_profiles_count": len(defaults),
        "supported_providers": supported_provider_catalog(),
        "ok": True,
    }
