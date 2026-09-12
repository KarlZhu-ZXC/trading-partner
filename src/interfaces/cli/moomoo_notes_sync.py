"""Compatibility command for the provider-neutral Observation sync implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from interfaces.cli.observation_sync import _parser, _run


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(_parser(moomoo_only=True).parse_args(argv))))


if __name__ == "__main__":
    main()
