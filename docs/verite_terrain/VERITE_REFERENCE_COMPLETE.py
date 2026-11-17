"""VÉRITÉ TERRAIN ÉTENDUE — fiche de référence 1042-EX (SYNTHÉTIQUE).

PROVENANCE : chaque valeur est relue sur la fiche synthétique régénérée par
scripts/generer_fiche_reference_synthetique.py : 74 valeurs retenues,
0 écartées. Les 67 valeurs techniques sont celles de la fiche d'origine ;
les 7 valeurs identifiantes y sont remplacées par des valeurs fictives.
Aucune valeur n'est devinée ni déduite de l'extraction seule (RG6).

Source unique dans le paquet : seamtech_search.fiches.verite_reference.
Ce fichier ré-exporte la vérité terrain pour la documentation et les scripts
d'analyse externe sans dupliquer la définition.
"""

from __future__ import annotations

from seamtech_search.fiches.verite_reference import VERITE_REFERENCE, VERITE_REFERENCE_COMPLETE

__all__ = ["VERITE_REFERENCE", "VERITE_REFERENCE_COMPLETE"]
