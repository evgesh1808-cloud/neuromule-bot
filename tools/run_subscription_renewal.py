#!/usr/bin/env python3
"""Cron: продление платных подписок с истёкшим subscription_ends_at."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.billing.subscription_renewal import renew_due_subscriptions
from services.repository import init_db


async def main() -> None:
    await init_db()
    n = await renew_due_subscriptions()
    print(f"renewed_subscriptions={n}")


if __name__ == "__main__":
    asyncio.run(main())
