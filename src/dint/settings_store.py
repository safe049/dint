"""Runtime-editable settings for dint.

Settings come from two layers:

1. ``.env`` / environment variables (the defaults, loaded by :mod:`dint.config`).
2. A ``settings.json`` file, written by the web UI's settings panel. Values here
   override the environment.

The merged view is what the LLM client and agent actually use, so changes made
in the UI take effect immediately (after resetting the cached OpenAI client).

Per-user scoping
----------------
Like the database, settings are partitioned per storage scope:

* **local** mode keeps the single project-root ``settings.json`` (unchanged).
* **host** mode gives each user their own ``users/<name>/settings.json``.

In **host** mode the LLM *connection* settings (``openai_api_key`` and
``openai_base_url``) are owned by the operator via the environment / ``.env``
and are deliberately hidden from and non-editable by end users — a tenant must
not be able to read or swap the operator's API key. The model choice and
behaviour knobs remain per-user editable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import scope, users
from .config import PROJECT_ROOT, get_settings

# Fields the UI is allowed to read and edit (in local mode, all of them).
EDITABLE_FIELDS = (
    "openai_api_key",
    "openai_base_url",
    "dint_model",
    "reflect_model",
    "dint_temperature",
    "max_tool_rounds",
    "max_tool_calls_per_turn",   
    "max_reflect_updates",           
    "web_search_results",
)

# Connection fields that are operator-owned and hidden in host mode.
HOST_HIDDEN_FIELDS = ("openai_api_key", "openai_base_url")

# Per-scope override caches: scope_key -> {field: value}.
_overrides: dict[str, dict[str, Any]] = {}
_loaded: set[str] = set()


def _settings_file(key: str) -> Path:
    """Return the settings.json path for a scope key."""
    if not scope.is_host_mode() or key == scope.LOCAL_USER:
        return PROJECT_ROOT / "settings.json"
    return users.user_settings_path(key)


def visible_fields() -> tuple[str, ...]:
    """Fields exposed to the UI for the current mode."""
    if scope.is_host_mode():
        return tuple(f for f in EDITABLE_FIELDS if f not in HOST_HIDDEN_FIELDS)
    return EDITABLE_FIELDS


def _load(key: str) -> dict[str, Any]:
    """Load (and cache) the override dict for a scope key."""
    if key in _loaded:
        return _overrides.get(key, {})
    overrides: dict[str, Any] = {}
    path = _settings_file(key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                overrides = {
                    k: v for k, v in data.items() if k in EDITABLE_FIELDS
                }
        except (json.JSONDecodeError, OSError):
            overrides = {}
    _overrides[key] = overrides
    _loaded.add(key)
    return overrides


def effective() -> dict[str, Any]:
    """Return the merged settings (env defaults + UI overrides) for this scope.

    In host mode the operator-owned connection fields always come from the
    environment and are never surfaced from per-user overrides.
    """
    key = scope.scope_key()
    overrides = _load(key)
    s = get_settings()
    out: dict[str, Any] = {k: getattr(s, k) for k in EDITABLE_FIELDS}
    for k in EDITABLE_FIELDS:
        if scope.is_host_mode() and k in HOST_HIDDEN_FIELDS:
            continue  # operator-owned; env value already in ``out``
        v = overrides.get(k)
        if v is not None and v != "":
            out[k] = v
    if not out.get("reflect_model"):
        out["reflect_model"] = out["dint_model"]
    return out


def _coerce(key: str, value: Any) -> Any:
    """Validate/coerce a single field, raising ValueError on bad input."""
    if key in ("dint_temperature",):
        v = float(value)
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v
    if key in ("max_tool_rounds", "web_search_results"):
        v = int(value)
        lo, hi = (1, 24) if key == "max_tool_rounds" else (1, 10)
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return v
    if key in ("max_tool_calls_per_turn",):
        v = int(value)
        if not 1 <= v <= 20:
            raise ValueError("max_tool_calls_per_turn must be between 1 and 20")
        return v
    if key in ("max_reflect_updates",):
        v = int(value)
        if not 1 <= v <= 12:
            raise ValueError("max_reflect_updates must be between 1 and 12")
        return v
    return str(value).strip()        


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge ``updates`` into this scope's persisted overrides and write to disk.

    Empty strings clear an override (falling back to the environment value),
    except for the API key, where empty means "keep whatever is stored".

    In host mode the operator-owned connection fields are ignored entirely.
    """
    key = scope.scope_key()
    overrides = _load(key)
    for k, value in updates.items():
        if k not in EDITABLE_FIELDS:
            continue
        if scope.is_host_mode() and k in HOST_HIDDEN_FIELDS:
            continue  # tenants cannot change the operator's connection config
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            if k == "openai_api_key":
                continue  # blank key = keep existing
            overrides.pop(k, None)
            continue
        overrides[k] = _coerce(k, value)
    _settings_file(key).write_text(
        json.dumps(overrides, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return effective()


def mask_key(key: str) -> str:
    """Render an API key safe for display in the UI."""
    if not key or key == "sk-replace-me":
        return ""
    if len(key) <= 8:
        return "••••"
    return "••••" + key[-4:]