#!/usr/bin/env python3
"""Перегенерация HD-разбора для уже оплатившего пользователя (без списания 💎).

Пример на VDS:
    cd /root/neuromule-bot
    python3 tools/regenerate_hd_report.py 435041303

Бесплатный апгрейд legacy → v3 (быстрый путь):
    python3 tools/regenerate_hd_report.py 435041303 --upgrade-fast

Полный multi-pass Genetic Synthesis (дольше, ~3–8 мин):
    python3 tools/regenerate_hd_report.py 435041303 --full
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _generate_assets(uid: int, report: dict, *, hd_type: str, birth_data: str) -> None:
    from services.hd_logic import (
        build_hd_math_data,
        generate_instagram_stories,
        generate_premium_bodygraph,
    )

    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    defined = math_data.get("defined_centers") or []
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: generate_premium_bodygraph(list(defined), uid),
    )
    try:
        story_paths = await loop.run_in_executor(
            None,
            lambda: generate_instagram_stories(
                uid,
                report,
                math_data=math_data,
                hd_type=resolved_type,
                birth_data=birth_data,
            ),
        )
        print("instagram stories:", story_paths)
    except Exception as exc:
        print("WARN: instagram stories generation failed:", exc)


async def _run(uid: int, *, full: bool, upgrade_fast: bool) -> None:
    from services.hd_logic import (
        build_hd_math_data,
        ensure_modern_hd_report,
        generate_premium_report,
        get_user,
        premium_report_to_json,
        update_user,
    )

    user = await get_user(uid)
    keys = user.keys() if hasattr(user, "keys") else ()
    birth_data = (user["hd_birth_data"] or "").strip() if "hd_birth_data" in keys else ""
    hd_type = (user["hd_type"] or "не указан") if "hd_type" in keys else "не указан"
    has_pro = bool(user["has_pro_analysis"]) if "has_pro_analysis" in keys else False

    if not birth_data:
        raise SystemExit(
            f"uid={uid}: нет hd_birth_data — пользователь должен один раз отправить дату в боте."
        )

    print(f"uid={uid} has_pro_analysis={int(has_pro)} hd_type={hd_type!r} birth={birth_data!r}")

    if upgrade_fast and not full:
        report, upgraded = await ensure_modern_hd_report(uid, user_name="друг")
        if report is None:
            raise SystemExit("ensure_modern_hd_report: отчёт не получен (пустой hd_report_json?)")
        await _generate_assets(uid, report, hd_type=hd_type, birth_data=birth_data)
        print(f"upgrade via ensure_modern_hd_report upgraded={upgraded}")
        print("schema_version:", json.loads(await _raw_json(uid)).get("schema_version"))
        return

    upgrade_mode = not full
    print(f"generating premium report upgrade_mode={upgrade_mode} ...")
    report = await generate_premium_report(
        hd_type,
        birth_data,
        user_name="друг",
        upgrade_mode=upgrade_mode,
    )
    math_data = build_hd_math_data(hd_type, birth_data)
    resolved_type = str(math_data.get("hd_type") or hd_type)
    await update_user(
        uid,
        hd_report_json=premium_report_to_json(report),
        hd_type=resolved_type,
        hd_birth_data=birth_data,
        has_pro_analysis=1,
    )
    await _generate_assets(uid, report, hd_type=resolved_type, birth_data=birth_data)
    meta = report.get("synthesis_meta") or {}
    print("OK regenerated schema v3")
    print("synthesis_meta:", meta)
    print("fast_facts preview:", str(report.get("fast_facts", ""))[:200])


async def _raw_json(uid: int) -> str:
    from services.hd_logic import get_user

    user = await get_user(uid)
    keys = user.keys() if hasattr(user, "keys") else ()
    return user["hd_report_json"] if "hd_report_json" in keys else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate HD premium report for a user")
    parser.add_argument("user_id", type=int, help="Telegram user id")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Полный multi-pass synthesis (upgrade_mode=False)",
    )
    parser.add_argument(
        "--upgrade-fast",
        action="store_true",
        help="Только ensure_modern_hd_report (legacy → v3 fast)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.user_id, full=args.full, upgrade_fast=args.upgrade_fast))
    except SystemExit:
        raise
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise SystemExit(f"FAILED: {exc}") from exc


if __name__ == "__main__":
    main()
