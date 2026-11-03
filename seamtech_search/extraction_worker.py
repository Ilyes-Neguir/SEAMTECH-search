from __future__ import annotations

import json
import sys
from pathlib import Path

from .extractors import extract_file


def main() -> None:
    donnees = sys.stdin.read(1024 * 1024 + 1)
    if len(donnees) > 1024 * 1024:
        raise ValueError("extraction request too large")
    requete = json.loads(donnees)
    path = Path(requete["path"])
    max_chars = int(requete["max_chars"])
    max_file_size_bytes = int(requete["max_file_size_bytes"])
    options = requete["options"]
    result = extract_file(path, max_chars, max_file_size_bytes, **options)
    json.dump({"text": result.text, "status": result.status, "detail": result.detail}, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not print request contents or paths in worker failures.
        sys.exit(1)
