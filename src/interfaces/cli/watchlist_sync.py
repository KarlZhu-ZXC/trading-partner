"""Full active-source Watchlist Hub synchronization command."""

from __future__ import annotations

import asyncio
import json

from bootstrap import build_default_application


async def _run() -> int:
    container = build_default_application()
    try:
        envelope = await container.services.watchlist.sync_all()
        if not envelope.ok or envelope.data is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "errors": [error.code for error in envelope.errors],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    **envelope.data.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        await container.aclose()


def main() -> None:
    """Run one full refresh and return a scheduler-friendly exit status."""

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
