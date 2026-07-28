"""Per-request user scoping for dint's two operating modes.

dint runs in one of two modes, chosen on the CLI:

* **local** (default) – a single-user desktop app. There is no login; every
  request implicitly belongs to one fixed local user and all data lives in the
  usual project-root files (``dint.db``, ``settings.json``). This is exactly the
  pre-existing behaviour.
* **host** – a multi-user server. Each visitor registers/logs in and is given an
  opaque session token. Every request is bound to the authenticated user via a
  :class:`contextvars.ContextVar`, and that user's data is stored in their own
  private database file and settings file. Users are fully isolated from one
  another by construction: the entire data layer (``db``, ``settings_store``,
  ``consolidation``) asks :func:`scope_key` for "which user am I serving right
  now?" and never branches on the mode itself.

The ContextVar is set by the auth middleware in :mod:`dint.app` at the start of
each request. Because :func:`asyncio.create_task` copies the current context
into child tasks, background work (reflection, consolidation) automatically runs
in the correct user's scope without any extra plumbing.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Optional

# Environment variable the CLI sets so the flag survives uvicorn spawning a
# separate worker subprocess (which re-imports this module and never runs
# ``cli.main()``). Read at import time so reloaded workers pick it up too.
_HOST_MODE_ENV = "DINT_HOST_MODE"

# Set once at process start by the CLI. ``False`` = local single-user mode.
HOST_MODE: bool = os.environ.get(_HOST_MODE_ENV, "").strip() in ("1", "true", "yes", "on")

# The username bound to the current request / task. ``None`` until the auth
# middleware sets it (host mode) — in local mode it is set to ``LOCAL_USER``.
_current_user: ContextVar[Optional[str]] = ContextVar("dint_current_user", default=None)

# The implicit username used for all data in local (single-user) mode. Keeping it
# as a real, stable key means the data layer can always partition by username
# without ever special-casing the mode.
LOCAL_USER = "local"


def is_host_mode() -> bool:
    """True when running as a multi-user host server."""
    return HOST_MODE


def set_host_mode(enabled: bool) -> None:
    """Enable/disable host mode. Called once by the CLI before the server starts."""
    global HOST_MODE
    HOST_MODE = bool(enabled)


def get_current_user() -> Optional[str]:
    """Return the username bound to the current context, or ``None``."""
    return _current_user.get()


def set_current_user(username: Optional[str]):
    """Bind ``username`` to the current context.

    Returns a reset token that can be passed to :func:`reset_current_user`.
    """
    return _current_user.set(username)


def reset_current_user(token) -> None:
    """Restore the previous user binding (used in a ``finally`` block)."""
    _current_user.reset(token)


def scope_key() -> str:
    """Return the storage scope key for the current request.

    * In **local** mode this is always :data:`LOCAL_USER`, so data lands in the
      familiar project-root files.
    * In **host** mode this is the authenticated username. If no user is bound
      (i.e. an unauthenticated request slipped past the middleware), we fall back
      to a reserved anonymous bucket rather than crashing — the auth layer is
      responsible for rejecting such requests before they reach the data layer.
    """
    if not HOST_MODE:
        return LOCAL_USER
    user = _current_user.get()
    return user or "_anonymous"


def require_user() -> str:
    """Like :func:`scope_key`, but raises if host mode has no bound user.

    Used by code paths that must never operate on the anonymous bucket.
    """
    if not HOST_MODE:
        return LOCAL_USER
    user = _current_user.get()
    if not user:
        raise LookupError("no authenticated user bound to the current context")
    return user