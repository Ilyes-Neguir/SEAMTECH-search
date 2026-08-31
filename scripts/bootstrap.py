from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"


def main() -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    example_config = CONFIG_DIR / "config.example.json"
    config = CONFIG_DIR / "config.json"
    if not config.exists() and example_config.exists():
        shutil.copyfile(example_config, config)

    print(f"Prepared project structure at {ROOT}")


if __name__ == "__main__":
    main()
