"""One-off helper to extract sections from telegram_bot.py (dev tooling)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "platforms/telegram_bot.py").read_text(encoding="utf-8").splitlines()


def dedent_block(start: int, end: int, out_path: Path) -> None:
    chunk = lines[start - 1 : end]
    out: list[str] = []
    for line in chunk:
        if line.startswith("    "):
            out.append(line[4:])
        else:
            out.append(line)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(out_path.name, len(out), "lines")


if __name__ == "__main__":
    dedent_block(698, 1008, ROOT / "platforms/handlers/_extract_start_admin.txt")
    dedent_block(1009, 1133, ROOT / "platforms/handlers/_extract_menu_support.txt")
    dedent_block(1134, 1292, ROOT / "platforms/handlers/_extract_generation_cb.txt")
    dedent_block(1293, 1652, ROOT / "platforms/handlers/_extract_hd.txt")
    dedent_block(1653, 1874, ROOT / "platforms/handlers/_extract_generation_fsm.txt")
    dedent_block(1875, 2058, ROOT / "platforms/handlers/_extract_payment_misc.txt")
