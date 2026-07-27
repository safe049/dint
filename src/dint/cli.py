"""CLI entry point for dint."""
from __future__ import annotations

import argparse

import uvicorn

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
        "--no-reload",
        action="store_true",
        help="Disable auto-reload (useful for production)",
    )
    args = parser.parse_args()

    # Ensure settings are loaded (validates .env / config early).
    get_settings()

    print(f"→ Starting dint on http://localhost:{args.port}")
    uvicorn.run(
        "dint.app:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )


if __name__ == "__main__":
    main()