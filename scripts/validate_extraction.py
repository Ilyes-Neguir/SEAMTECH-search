"""Validate extract_structured_pdf against real fabrication documents.

Audit item 1 requires proving the regex fixes against real SEAMTECH sheets,
not just synthetic fixtures. This harness prints the FULL ExtractedData for
every document given and flags fields that are wrong, suspect, or None when
they probably should not be.

Usage:
    python scripts/validate_extraction.py path/to/dossier [more/paths ...]
    python scripts/validate_extraction.py            # defaults to sample_data/

Point it at a folder of real (redacted) fabrication PDFs. For each document
it prints every extracted field, the confidence, the extraction status, and
all warnings, then a REVIEW block listing what a human must check.

Nothing here mutates the documents or the index; extraction only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seamtech_search.config import AppConfig  # noqa: E402
from seamtech_search.import_pipeline import (  # noqa: E402
    _MAX_PLAUSIBLE_FIELD_LENGTH,
    FIELD_PATTERNS,
    extract_structured_pdf,
)

SUPPORTED = {".pdf"}

# Fields the fixed-layout sheets are expected to carry. A None here is a
# candidate miss, not automatically an error: some sheets genuinely lack it.
EXPECTED_FIELDS = ("reference", "material", "quantity", "description")


def discover(paths: list[str]) -> list[Path]:
    """Expand the given paths into a sorted list of PDF files."""
    if not paths:
        paths = [str(REPO_ROOT / "sample_data")]
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.is_dir():
            found.extend(sorted(p for p in candidate.rglob("*") if p.suffix.lower() in SUPPORTED))
        elif candidate.is_file():
            found.append(candidate)
        else:
            print(f"WARNING: {raw} does not exist; skipping.", file=sys.stderr)
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def review_notes(data: Any, text: str) -> list[str]:
    """Human-review flags for one document."""
    notes: list[str] = []
    for field_name in EXPECTED_FIELDS:
        value = getattr(data, field_name, None)
        if value is None:
            notes.append(f"{field_name}: None — no pattern matched. Add the real line to FIELD_PATTERNS.")
            continue
        if field_name != "description":
            # Mirror the shipped heuristic so the report explains *why* it is suspect.
            if len(str(value)) > _MAX_PLAUSIBLE_FIELD_LENGTH:
                notes.append(f"{field_name}: {len(str(value))} chars, over the {_MAX_PLAUSIBLE_FIELD_LENGTH} cap — probable bleed.")
    if data.dimensions is None or (
        data.dimensions.length is None and data.dimensions.width is None
    ):
        notes.append("dimensions: no length/width found at all.")
    elif data.dimensions.unit is None:
        notes.append("dimensions: unit is None — values were NOT converted to mm. Confirm the real unit on the sheet.")
    if data.extraction_status != "success":
        notes.append(f"extraction_status={data.extraction_status} (confidence {data.confidence:.2f}).")
    if not text.strip():
        notes.append("raw_text is empty — scanned PDF, needs OCR.")
    return notes


def main(argv: list[str]) -> int:
    documents = discover(argv[1:])
    if not documents:
        print("No PDFs found. Pass a folder of real fabrication sheets.", file=sys.stderr)
        return 2

    print(f"Validating {len(documents)} document(s).")
    print(f"Configured fields: {', '.join(FIELD_PATTERNS)}\n")

    failures = 0
    for path in documents:
        print("=" * 78)
        print(f"FILE: {path}")
        print("=" * 78)
        config = AppConfig(root_paths=[path.parent])
        try:
            data = extract_structured_pdf(path, config)
        except Exception as exc:  # noqa: BLE001 - harness must not die on one bad file
            failures += 1
            print(f"  EXTRACTION RAISED: {type(exc).__name__}: {exc}\n")
            continue

        print(f"  extraction_status : {data.extraction_status}")
        print(f"  confidence        : {data.confidence:.2f}")
        print(f"  reference         : {data.reference!r}")
        print(f"  material          : {data.material!r}")
        print(f"  quantity          : {data.quantity!r}")
        print(f"  description       : {data.description!r}")
        if data.dimensions is not None:
            dims = data.dimensions.model_dump()
            print(f"  dimensions        : {json.dumps(dims, ensure_ascii=False)}")
        if data.warnings:
            print("  warnings:")
            for warning in data.warnings:
                print(f"    - {warning}")
        print("  --- raw_text (first 1200 chars) ---")
        preview = data.raw_text[:1200]
        for line in preview.splitlines() or [""]:
            print(f"    | {line}")
        if len(data.raw_text) > 1200:
            print(f"    | ... ({len(data.raw_text) - 1200} more chars)")

        notes = review_notes(data, data.raw_text)
        if notes:
            print("  --- REVIEW (a human must confirm these) ---")
            for note in notes:
                print(f"    * {note}")
            failures += 1
        else:
            print("  --- REVIEW: clean, all expected fields present and plausible ---")
        print()

    print("=" * 78)
    if failures:
        print(f"{failures} of {len(documents)} document(s) need review or failed.")
        print("For each flagged field, add the EXACT real-world line to FIELD_PATTERNS,")
        print("LABELED_DIMENSION_PATTERNS or _FIELD_LABEL_WORDS in import_pipeline.py")
        print("(respecting the _FIELD_STOP lookahead) and add one regression test per")
        print("layout variant, following test_field_capture_stops_at_next_column_not_end_of_line.")
    else:
        print(f"All {len(documents)} document(s) extracted cleanly.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
