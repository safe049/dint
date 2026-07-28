"""CLI entry point for dint."""
from __future__ import annotations

import argparse
import os

import uvicorn

from dint import scope
from dint.config import get_settings


def main() -> None:
    """Launch the dint web server."""
    parser = argparse.ArgumentParser(
        prog="dint",
        description="dint – a personal AI tutor with long-term memory and tool use.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7070,
        help="Port to listen on (default: 7070)",
    )
    parser.add_argument(
        "--multi-user",
        action="store_true",
        help=(
            "Run in host/multi-user mode: each visitor registers an account and "
            "gets isolated memory, skills and knowledge. Without this flag dint "
            "runs in single-user local mode (no login)."
        ),
    )
    args = parser.parse_args()

    # Enable host mode before anything reads the scope flag. We set it both
    # in-process AND via an environment variable: uvicorn spawns a separate
    # worker subprocess (always with reload, and even without it for the
    # imported app object) that re-imports dint.scope and never re-runs this
    # function, so the env var is what actually carries the flag across.
    if args.multi_user:
        os.environ["DINT_HOST_MODE"] = "1"
        scope.set_host_mode(True)
    else:
        os.environ.pop("DINT_HOST_MODE", None)

    # Ensure settings are loaded (validates .env / config early).
    get_settings()

    mode_label = "multi-user (host)" if scope.is_host_mode() else "single-user (local)"
    print(f"→ Starting dint on http://localhost:{args.port} [{mode_label}]")
    uvicorn.run(
        "dint.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()