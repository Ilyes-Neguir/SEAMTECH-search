"""Shared technical-PDF classification anchors.

Single source of truth for the deterministic "technical sail-information PDF
vs plan PDF" classifier. Both the incremental crawler
(:mod:`seamtech_search.crawler`) and the reference import pipeline
(:mod:`seamtech_search.import_pipeline`) use these helpers so the two code
paths can never drift apart again.

Règle d'arbitrage entre signaux (documentée, corpus réel 2026-09-28) :

1. **Signal textuel** (ce module) — décide seul ``is_technical`` au parcours
   upload (``scan_folder``). Il ne lit que des LIBELLÉS génériques de
   voilerie, jamais de valeurs métier.
2. **Signal structurel** (:mod:`seamtech_search.detection_fiches`, lexique
   ``config/lexique_fiches.json``) — vue indépendante (vocabulaire + grille)
   utilisée par l'inventaire d'archive ; elle recense, ne classe pas.
3. **Signal gabarit** (:mod:`seamtech_search.fiches.gabarits`) — la preuve la
   plus forte : un gabarit reconnu identifie une famille de mise en page de
   fiche. Il décide du choix du PDF de fiche dans le dépôt de dossier.

Le signal textuel reste le décideur d'``is_technical`` car il est le seul
disponible avant toute sélection ; les signaux 2 et 3 never bloquent un
import : en cas de désaccord, l'opérateur garde la sélection manuelle et le
dossier reste ``needs_review``. Avant l'enrichissement ci-dessous, les 7
fiches réelles du corpus ne portaient qu'UNE ancre forte (« fiche de
fabrication ») et sortaient toutes « plan » : l'enrichissement ajoute les
libellés génériques de voilerie observés sur ces fiches (bord, accastillage,
assemblage). Limites documentées : (a) le seuil faible (3 ancres sans ancre
forte) peut classer « technique » un document qui cumule trois libellés de
voilerie sans en être une — l'opérateur voit la liste des ancres dans la
réponse de scan et corrige à la sélection ; (b) un libellé absent du texte
(scan sans OCR) reste invisible pour ce signal — la détection structurelle
et le filet de reprise couvrent ce cas.
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
    # Enrichissement corpus réel (2026-09-28) : libellés génériques de
    # voilerie observés sur les fiches de fabrication réelles, absents des
    # plans du même corpus. Ce sont des LIBELLÉS, jamais des valeurs métier.
    # « guindant », « chute », « lattes » sont volontairement ABSENTS de
    # cette liste : les plans de découpe du corpus réel les portent aussi.
    "bordure",  # bord de voile (leech/foot) — 0/7 plans du corpus réel
    "type de voile",  # en-tête des fiches atelier — 0/7 plans
    "ralingue",  # cordage de guindant — 0/7 plans
    "sangle",  # point d'ancrage — 0/7 plans
    "amure",  # point d'ancrage — 0/7 plans
    "drisse",  # point d'ancrage — 0/7 plans
    "écoute",
    "ecoute",  # point d'ancrage (formes avec/sans accent selon le PDF)
    "penons",  # indicateurs de vent — 0/7 plans
    "zig zag",  # couture voilerie — 0/7 plans
    "montage",  # assemblage atelier — 0/7 plans
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
