"""DEP — préparation du déploiement hors ligne sur le serveur d'entreprise.

Le plan directeur se termine par DEP : tout ce qui PEUT être préparé et
prouvé hors du serveur l'est ici, pour que le jour J il ne reste que des
gestes mécaniques. Gardes statiques déterministes (suite standard, aucun
service) :

* le script de transfert produit bundle Git + images + SHA256SUMS +
  manifeste, refuse un arbre Git non propre (un paquet fige un SHA), et
  sait vérifier/charger côté serveur — sans jamais transporter `.env` ;
* le runbook DEP enchaîne les outils réels existants (preflight lecture
  seule, inventaire avec dédup SHA-256 AVANT OCR, dépôt par vagues) et
  porte la formule de dimensionnement disque, la reprise d'ingestion et
  la désactivation du compte de secours.
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "preparer_transfert_hors_ligne.sh"
RUNBOOK = RACINE / "docs" / "PLAN_DIRECTEUR_V2_DEP.md"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_transfert_bundle_images_empreintes() -> None:
    source = _script()
    for fragment in (
        "git bundle create",
        "docker save -o",
        "docker compose build",
        "SHA256SUMS",
        "MANIFESTE.txt",
        "git bundle verify",
        "docker load -i",
        "pgvector/pgvector:pg16",
        "redis:7-alpine",
        "construire_image_minio.sh",
    ):
        assert fragment in source, fragment


def test_script_transfert_gardes_de_securite() -> None:
    source = _script()
    # Un paquet fige un SHA commité : arbre sale = refus.
    assert "git status --porcelain" in source
    # Aucun secret transporté : le paquet ne touche jamais à .env.
    assert ".env" in source  # mentionné dans le commentaire d'interdiction
    assert "cp .env" not in source and "cat .env" not in source
    # Modes explicites et erreurs bruyantes.
    for mode in ("preparer", "verifier", "charger"):
        assert mode in source
    assert "set -euo pipefail" in source


def test_runbook_dep_enchaine_les_outils_reels() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for fragment in (
        "preflight_archive.py",
        "inventaire_archive.py",
        "SHA-256",
        "avant" ,
        "OCR",
        "SEAMTECH_UI_PASSWORD",
        "secours",
        "inventaire.json",
    ):
        assert fragment in runbook, fragment


def test_runbook_dep_dimensionnement_vagues_reprise() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    # Formule de dimensionnement (plan) : 3× le corpus + 20 Go de marge.
    assert "3 ×" in runbook and "20 Go" in runbook
    # Ingestion par vagues priorisées et reprise après interruption.
    assert "vague" in runbook.lower()
    assert "reprise" in runbook.lower()
    # Le correctif gabarits/calibration reste un chantier nommé, pas caché.
    assert "gabarit" in runbook.lower()
