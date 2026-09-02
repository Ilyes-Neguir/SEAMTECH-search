from __future__ import annotations

import json
import sys
from pathlib import Path

from .extractors import extract_text


def main() -> None:
    path = Path(sys.argv[1])
    max_chars = int(sys.argv[2])
    max_file_size_bytes = int(sys.argv[3])
    options = json.loads(sys.argv[4])
    text = extract_text(path, max_chars, max_file_size_bytes, **options)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
