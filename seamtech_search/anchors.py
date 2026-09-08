"""Shared technical-PDF classification anchors.

Single source of truth for the deterministic "technical sail-information PDF
vs plan PDF" classifier. Both the incremental crawler
(:mod:`seamtech_search.crawler`) and the reference import pipeline
(:mod:`seamtech_search.import_pipeline`) use these helpers so the two code
paths can never drift apart again.
"""

from __future__ import annotations

# Fixed-layout anchors observed on SEAMTECH technical sheets (FR/EN).
# A PDF is classified as technical when at least THRESHOLD of these anchors
# appear in its normalized text.
TECHNICAL_ANCHORS: tuple[str, ...] = (
    "fiche de fabrication",
    "quantité",
    "quantity",
    "cotes",
    "mesures finies",
    "mesures dessin",
    "material",
    "matière",
    "matériau",
    "materiau",
    "reference",
    "référence",
    "longueur",
    "largeur",
)

TECHNICAL_ANCHOR_THRESHOLD = 2


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase text for anchor matching."""
    return " ".join(text.lower().split())


def matched_anchors(text: str) -> list[str]:
    """Return the distinct technical anchors found in *text* (sorted)."""
    normalized = normalize_text(text)
    return sorted(anchor for anchor in TECHNICAL_ANCHORS if anchor in normalized)


def classify_pdf_text(text: str) -> str:
    """Classify PDF text as ``technical_pdf`` or ``plan_pdf``.

    Deterministic rule: at least :data:`TECHNICAL_ANCHOR_THRESHOLD` distinct
    anchors must be present. Empty/anchor-less text is a plan PDF.
    """
    return "technical_pdf" if len(matched_anchors(text)) >= TECHNICAL_ANCHOR_THRESHOLD else "plan_pdf"
