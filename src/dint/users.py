"""User accounts, password hashing and session tokens for host mode.

This module is only exercised when dint runs in **host** mode (multi-user
server). In local single-user mode none of it is used — there is no login and
all data belongs to the implicit :data:`dint.scope.LOCAL_USER`.

Design notes
------------
* **No third-party dependencies.** Passwords are hashed with PBKDF2-HMAC-SHA256
  from the standard library, which is more than adequate for a self-hosted
  tutor. Hashes are stored in the portable ``pbkdf2_sha256$<iters>$<salt>$<hash>``
  format so they can be verified without keeping any external state.
* **Accounts** live in a single ``accounts.json`` file at the project root. It
  maps ``username -> {password_hash, created_at}``. This is deliberately simple:
  a flat file is easy to back up, inspect and migrate, and avoids a second
  database that would itself need per-user partitioning.
* **Sessions** are opaque random tokens held in an in-memory dict mapping
  ``token -> username``. They expire after a fixed TTL. Keeping them in memory
  (rather than a cookie-signed JWT) means logout is instant and server-side
  revocation is trivial; the trade-off is that sessions don't survive a process
  restart, which is acceptable for this app.
* **Per-user data layout.** Each user gets their own directory under
  ``users/<username>/`` containing ``dint.db`` and ``settings.json``. Usernames
  are sanitised to a filesystem-safe form so a hostile registration cannot write
  outside the data directory.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from .config import PROJECT_ROOT

# --------------------------------------------------------------------------- #
# Storage locations
# --------------------------------------------------------------------------- #
_ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
_USERS_DIR = PROJECT_ROOT / "users"

# Session lifetime: 30 days of inactivity.
_SESSION_TTL_SECONDS = 30 * 24 * 3600

# PBKDF2 iteration count. 200k is a reasonable modern floor for SHA-256.
_PBKDF2_ITERATIONS = 200_000

# Usernames: 1-32 chars, letters/digits plus ``._-``, must start alphanumeric.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

# Reserved names that would collide with internal scope keys or files.
_RESERVED_NAMES = {"local", "_anonymous", "admin"}

_lock = threading.Lock()

# In-memory session table: token -> {"username": str, "expires": float}.
_sessions: dict[str, dict] = {}


class AuthError(Exception):
    """Raised for any authentication / registration failure shown to the user."""


# --------------------------------------------------------------------------- #
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only)
# --------------------------------------------------------------------------- #
def _hash_password(password: str, salt: Optional[bytes] = None,
                   iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Return a stored hash string ``pbkdf2_sha256$<iters>$<salt>$<hash>``."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, salt.hex(), dk.hex()
    )


def _verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash string."""
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# --------------------------------------------------------------------------- #
# Accounts file
# --------------------------------------------------------------------------- #
def _load_accounts() -> dict[str, dict]:
    if not _ACCOUNTS_FILE.exists():
        return {}
    try:
        data = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_accounts(accounts: dict[str, dict]) -> None:
    _ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _ACCOUNTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(accounts, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(_ACCOUNTS_FILE)


def _validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise AuthError("username is required")
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "username must be 1-32 characters: letters, digits, '.', '_', '-', "
            "starting with a letter or digit"
        )
    if username.lower() in _RESERVED_NAMES:
        raise AuthError("that username is reserved")
    return username


def register_user(username: str, password: str) -> str:
    """Create a new account. Returns the normalised username.

    Raises :class:`AuthError` on a bad username, weak password, or a name that
    is already taken.
    """
    username = _validate_username(username)
    if not password or len(password) < 6:
        raise AuthError("password must be at least 6 characters")
    with _lock:
        accounts = _load_accounts()
        # Case-insensitive uniqueness so "Alice" and "alice" can't coexist.
        if any(name.lower() == username.lower() for name in accounts):
            raise AuthError("username already taken")
        accounts[username] = {
            "password_hash": _hash_password(password),
            "created_at": time.time(),
        }
        _save_accounts(accounts)
    return username


def authenticate(username: str, password: str) -> str:
    """Verify credentials. Returns the canonical stored username on success.

    Raises :class:`AuthError` if the account doesn't exist or the password is
    wrong (a single generic message, so we don't leak which usernames exist).
    """
    username = (username or "").strip()
    with _lock:
        accounts = _load_accounts()
        record = None
        canonical = None
        for name, rec in accounts.items():
            if name.lower() == username.lower():
                record = rec
                canonical = name
                break
    if record is None or not _verify_password(password or "", record["password_hash"]):
        raise AuthError("invalid username or password")
    return canonical


# --------------------------------------------------------------------------- #
# Sessions (opaque tokens, in-memory)
# --------------------------------------------------------------------------- #
def create_session(username: str) -> str:
    """Mint a new session token for ``username``."""
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = {
            "username": username,
            "expires": time.time() + _SESSION_TTL_SECONDS,
        }
    return token


def resolve_session(token: Optional[str]) -> Optional[str]:
    """Return the username for a valid, unexpired token, else ``None``."""
    if not token:
        return None
    with _lock:
        entry = _sessions.get(token)
        if entry is None:
            return None
        if entry["expires"] < time.time():
            _sessions.pop(token, None)
            return None
        return entry["username"]


def revoke_session(token: Optional[str]) -> None:
    """Log out: drop the given token if present."""
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


# --------------------------------------------------------------------------- #
# High-level helpers used by the HTTP layer (dint.app)
# --------------------------------------------------------------------------- #
def register(username: str, password: str) -> str:
    """Create an account and immediately mint a session token.

    Raises :class:`ValueError` (with a user-facing message) on any failure so
    the API layer can translate it straight into an HTTP 400.
    """
    try:
        canonical = register_user(username, password)
    except AuthError as exc:
        raise ValueError(str(exc)) from exc
    return create_session(canonical)


def login(username: str, password: str) -> Optional[str]:
    """Verify credentials and return a fresh session token, or ``None``."""
    try:
        canonical = authenticate(username, password)
    except AuthError:
        return None
    return create_session(canonical)


# --------------------------------------------------------------------------- #
# Per-user data layout
# --------------------------------------------------------------------------- #
def user_data_dir(username: str) -> Path:
    """Return (and create) the private data directory for ``username``."""
    # ``username`` has already passed _validate_username, so it is filesystem
    # safe; we still guard against path separators defensively.
    safe = username.replace("/", "_").replace("\\", "_")
    d = _USERS_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_db_path(username: str) -> Path:
    """SQLite file for ``username``."""
    return user_data_dir(username) / "dint.db"


def user_settings_path(username: str) -> Path:
    """settings.json file for ``username``."""
    return user_data_dir(username) / "settings.json"