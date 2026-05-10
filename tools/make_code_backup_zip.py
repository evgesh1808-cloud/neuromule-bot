"""
Создаёт zip-архив исходников проекта (без venv, __pycache__, .git).

По умолчанию файл пишется в каталог TEMP пользователя (ASCII-путь, удобно на Windows).
В корень репозитория: python tools/make_code_backup_zip.py --here
"""

from __future__ import annotations

import argparse
import os
import time
import zipfile
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {"__pycache__", ".git", ".pytest_cache", "venv", ".venv", "node_modules"}
)


def _is_backup_zip_name(name: str) -> bool:
    return name.startswith("MySuperBot-code-backup-") and name.endswith(".zip")


def main(*, out_dir: Path | None) -> Path:
    root = Path(__file__).resolve().parents[1]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out_dir if out_dir is not None else Path(os.environ.get("TEMP", "."))
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"MySuperBot-code-backup-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if _is_backup_zip_name(path.name):
                continue
            arc = path.relative_to(root)
            zf.write(path, arc)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--here",
        action="store_true",
        help="Сохранить zip в корень репозитория вместо %%TEMP%%",
    )
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    p = main(out_dir=root if args.here else None)
    print(str(p.resolve()))
