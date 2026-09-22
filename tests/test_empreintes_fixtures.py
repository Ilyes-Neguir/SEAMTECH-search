"""Garde-fou d'intégrité des documents du dépôt (audit indépendant du 22/09).

Le bac à sable s'est réinitialisé entre deux tours et un commit orphelin
(108 fichiers recréés, dont deux fixtures PDF ALTÉRÉES par la restauration)
a failli être poussé. Ces empreintes épinglent les quatre documents du dépôt :
une fixture restaurée de travers, modifiée ou remplacée rend ce test ROUGE —
exactement le scénario qui a failli se produire.

Empreintes mesurées sur la tête 64e090c (sha256sum), jamais inventées :
elles ont été vérifiées contradictoirement par l'audit indépendant du 22/09.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# (chemin relatif, sha256 complet, taille en octets)
EMPREINTES: tuple[tuple[str, str, int], ...] = (
    # La VRAIE fiche client 7792-SO (copie canonique ; le doublon à la racine
    # de main est voué à suppression — voir docs/verite_terrain/FUSION_MAIN.md).
    (
        "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf",
        "43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40",
        166_990,
    ),
    # Gabarit génois : fixture SYNTHÉTIQUE (documenté comme tel — jamais
    # réglé/déclaré sur la reconstruction).
    (
        "sample_data/CLIENT-GENOA/fiche-genois.pdf",
        "3c073703e4a8e9d5fdcdcfa50781896b4e42f72441a1a94041f2d04e06870804",
        1_584,
    ),
    # Fixture e2e live (semée par seed-live-pg.py via le pipeline réel).
    (
        "frontend/e2e/live-fixtures/CLIENT-E2E-TROIS/fiche-trois.pdf",
        "c250b0771b0ac7b8a6a949ec9dc250aae703b9e6338472b19e558d478f5076ac",
        1_589,
    ),
    # Fiche technique synthétique CLIENT-123 (déjà dans main).
    (
        "sample_data/CLIENT-123/fiche-technique.pdf",
        "abf6aaaaa1833f75585a364d240963232ae46d344e63661286f8e4f5e2355803",
        1_938,
    ),
)


@pytest.mark.parametrize(
    ("chemin_relatif", "sha256_attendu", "taille_attendue"),
    EMPREINTES,
    ids=[c for c, _s, _t in EMPREINTES],
)
def test_empreinte_document(chemin_relatif: str, sha256_attendu: str, taille_attendue: int) -> None:
    chemin = REPO / chemin_relatif
    assert chemin.is_file(), (
        f"{chemin_relatif} absent du dépôt — une restauration de sandbox ou un "
        "nettoyage a déplacé/supprimé un document épinglé"
    )
    contenu = chemin.read_bytes()
    assert len(contenu) == taille_attendue, (
        f"{chemin_relatif} : taille {len(contenu)} ≠ {taille_attendue} octets attendus — "
        "le document a été altéré (arrondi d'encodage, restauration corrompue ?)"
    )
    empreinte = hashlib.sha256(contenu).hexdigest()
    assert empreinte == sha256_attendu, (
        f"{chemin_relatif} : sha256 {empreinte[:12]}… ≠ {sha256_attendu[:12]}… attendu — "
        "le contenu du document a changé. Si la modification est volontaire, "
        "ré-épingler EXPLICITEMENT l'empreinte dans ce test avec sa justification."
    )
