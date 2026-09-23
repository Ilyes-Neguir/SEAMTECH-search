"""Détection de doublons — Lot L.1 (« doublons vus AVANT validation »).

Exposé public : ``doublons_exacts``, ``doublons_probables``,
``enregistrer_liens``, ``liens_dune_fiche``, ``doublons_par_code``
(voir :mod:`seamtech_search.dedup.detection`).
"""

from .detection import (
    CandidatDoublon,
    ResultatProbables,
    doublons_exacts,
    doublons_par_code,
    doublons_probables,
    enregistrer_liens,
    liens_dune_fiche,
    normaliser_titre,
)

__all__ = [
    "CandidatDoublon",
    "ResultatProbables",
    "doublons_exacts",
    "doublons_probables",
    "doublons_par_code",
    "enregistrer_liens",
    "liens_dune_fiche",
    "normaliser_titre",
]
