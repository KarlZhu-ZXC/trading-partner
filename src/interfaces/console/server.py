"""Run the gated local console API on the loopback interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trading-partner-console")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in [1024,65535]")
    uvicorn.run(
        "interfaces.console.api:app",
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
