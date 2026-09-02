from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seamtech_search.cli import run_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark a real SEAMTECH indexing run")
    parser.add_argument("--config", default="config/config.json")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    result = run_index(args.config, rebuild=args.rebuild)
    result["config"] = str(Path(args.config).resolve())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()