"""Plain-text chat logging for dint.

Appends a human-readable record of every chat turn (user message + assistant
reply) to a per-user ``chatlog.txt`` file.

* **local** mode: the log lives at ``<PROJECT_ROOT>/chatlog.txt``.
* **host** mode: each user gets their own ``users/<username>/chatlog.txt``.

The log format is intentionally simple and grep-friendly::

    === 2025-07-28 18:00:00 | session: abc123 ===
    [user]
    What is a derivative?

    [assistant]
    A derivative measures ...

    ---

Logging is best-effort: any I/O error is silently swallowed so it can never
break the chat flow.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from . import scope
from .config import PROJECT_ROOT


def _log_path() -> Path:
    """Return the chatlog file path for the current user scope."""
    key = scope.scope_key()
    if scope.is_host_mode() and key not in ("_anonymous",):
        # Per-user directory under users/<username>/
        safe = key.replace("/", "_").replace("\\", "_")
        d = PROJECT_ROOT / "users" / safe
        d.mkdir(parents=True, exist_ok=True)
        return d / "chatlog.txt"
    # Local mode (or anonymous fallback): project root
    return PROJECT_ROOT / "chatlog.txt"


def log_chat_turn(
    session_id: str,
    user_message: str,
    assistant_reply: str,
    subject: Optional[str] = None,
) -> None:
    """Append one chat turn to the user's chatlog file.

    Parameters
    ----------
    session_id:
        The session identifier.
    user_message:
        The learner's message text.
    assistant_reply:
        dint's reply text.
    subject:
        Optional subject tag for the session.
    """
    try:
        path = _log_path()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        subject_tag = f" | subject: {subject}" if subject else ""

        lines = [
            f"=== {now} | session: {session_id}{subject_tag} ===",
            "[user]",
            user_message.strip(),
            "",
            "[assistant]",
            assistant_reply.strip(),
            "",
            "---",
            "",
        ]
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:  # noqa: BLE001 - logging is best-effort
        pass