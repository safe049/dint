"""Runtime-editable settings for dint.

Settings come from two layers:

1. ``.env`` / environment variables (the defaults, loaded by :mod:`dint.config`).
2. A ``settings.json`` file at the project root, written by the web UI's
   settings panel. Values here override the environment.

The merged view is what the LLM client and agent actually use, so changes made
in the UI take effect immediately (after resetting the cached OpenAI client).
"""
from __future__ import annotations

import json
from typing import Any

from .config import PROJECT_ROOT, get_settings

_FILE = PROJECT_ROOT / "settings.json"

# Fields the UI is allowed to read and edit.
EDITABLE_FIELDS = (
    "openai_api_key",
    "openai_base_url",
    "dint_model",
    "reflect_model",
    "dint_temperature",
    "max_tool_rounds",
    "web_search_results",
)

_overrides: dict[str, Any] = {}
_loaded = False


def _load() -> None:
    global _overrides, _loaded
    _overrides = {}
    if _FILE.exists():
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _overrides = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
        except (json.JSONDecodeError, OSError):
            _overrides = {}
    _loaded = True


def effective() -> dict[str, Any]:
    """Return the merged settings (env defaults + UI overrides)."""
    if not _loaded:
        _load()
    s = get_settings()
    out: dict[str, Any] = {k: getattr(s, k) for k in EDITABLE_FIELDS}
    for k in EDITABLE_FIELDS:
        v = _overrides.get(k)
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
    return str(value).strip()


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge ``updates`` into the persisted overrides and write to disk.

    Empty strings clear an override (falling back to the environment value),
    except for the API key, where empty means "keep whatever is stored".
    """
    if not _loaded:
        _load()
    for key, value in updates.items():
        if key not in EDITABLE_FIELDS:
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            if key == "openai_api_key":
                continue  # blank key = keep existing
            _overrides.pop(key, None)
            continue
        _overrides[key] = _coerce(key, value)
    _FILE.write_text(
        json.dumps(_overrides, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return effective()


def mask_key(key: str) -> str:
    """Render an API key safe for display in the UI."""
    if not key or key == "sk-replace-me":
        return ""
    if len(key) <= 8:
        return "••••"
    return "••••" + key[-4:]