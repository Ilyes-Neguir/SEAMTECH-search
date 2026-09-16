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

# Anchors that are highly specific to SEAMTECH fabrication sheets.
STRONG_ANCHORS: tuple[str, ...] = (
    "fiche de fabrication",
    "mesures finies",
    "mesures dessin",
    "cotes",
)

TECHNICAL_ANCHOR_THRESHOLD = 2
TECHNICAL_ANCHOR_THRESHOLD_WEAK = 3


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase text for anchor matching."""
    return " ".join(text.lower().split())


def matched_anchors(text: str) -> list[str]:
    """Return the distinct technical anchors found in *text* (sorted)."""
    normalized = normalize_text(text)
    return sorted(anchor for anchor in TECHNICAL_ANCHORS if anchor in normalized)


def classify_pdf_text(text: str) -> str:
    """Classify PDF text as ``technical_pdf`` or ``plan_pdf``.

    Stricter rule than the original threshold-2 check:
    - At least 2 distinct anchors, with at least one strong anchor (fiche de fabrication,
      mesures finies/dessin, cotes), OR
    - At least 3 distinct anchors if no strong anchor is present.
    This prevents generic PDFs containing only e.g. 'reference' + 'longueur' from
    being misclassified as technical.
    """
    anchors = matched_anchors(text)
    if not anchors:
        return "plan_pdf"
    has_strong = any(s in anchors for s in STRONG_ANCHORS)
    if has_strong:
        return "technical_pdf" if len(anchors) >= TECHNICAL_ANCHOR_THRESHOLD else "plan_pdf"
    return "technical_pdf" if len(anchors) >= TECHNICAL_ANCHOR_THRESHOLD_WEAK else "plan_pdf"
