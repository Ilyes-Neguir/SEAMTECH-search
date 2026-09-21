"""Couche métier « fiche technique » — Lot B (plan v3.0 §10, §13, §17.4-17.5).

Décision de couche (§17.1, actée au Lot A) : PostgreSQL uniquement pour
l'écriture en base (tables fiche_*, référentiels). La LECTURE du PDF
(:mod:`seamtech_search.fiches.extraction`, gabarits, normalisation) est
indépendante du stockage et se teste sans serveur.
"""

from seamtech_search.fiches.modeles import (
    Anomalie,
    ChampExtrait,
    Cotes,
    FicheExtraite,
    Finition,
    Galon,
    Jonction,
    Materiau,
    OptionFiche,
    Renfort,
)

__all__ = [
    "Anomalie",
    "ChampExtrait",
    "Cotes",
    "FicheExtraite",
    "Finition",
    "Galon",
    "Jonction",
    "Materiau",
    "OptionFiche",
    "Renfort",
]
