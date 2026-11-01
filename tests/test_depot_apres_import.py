"""Dépôt Lot C d'un dossier DÉJÀ passé par le flux d'import.

Pourquoi ce fichier existe — un défaut vu en CI, pas une hypothèse.

Le job ``recette-corpus-reel`` enchaîne deux gestes sur les MÊMES fichiers :
d'abord l'import (scan → confirmation), ensuite le dépôt Lot C (fiche + pièces
jointes). Les deux écrivent une ligne ``documents`` pour un même chemin, mais
``content_hash`` n'y porte pas la même chose : l'import y met l'empreinte du
TEXTE extrait, le dépôt celle des OCTETS de la pièce. Au dépôt, la condition de
reprise comparait donc deux empreintes de natures différentes et refusait :
« Une pièce existante au même chemin a changé ; revue nécessaire. » — sept
dossiers REF-001..REF-007 refusés alors qu'aucun octet n'avait bougé.

Le PDF de fiche avait déjà été corrigé (comparaison sur ``source_sha256``,
l'identité des octets). Les PIÈCES JOINTES passaient par un autre upsert, sans
cette comparaison : le dépôt restait refusé. Ce fichier le prouve, dans les
conditions qui l'ont produit : un import RÉEL, avec stockage objet configuré
(donc des lignes ``documents`` porteuses d'une clé objet — une ligne « vierge »
serait reprise sans discussion), puis le dépôt.

Second point prouvé : l'upsert ET le rafraîchissement du vecteur de recherche
passent par des colonnes surveillées par le déclencheur
``documents_invalider_source_sha256``. Si l'identité des octets des pièces n'est
pas rétablie EN DERNIER, elle est perdue dès le premier dépôt — le suivant ne
peut plus prouver que rien n'a changé.

Portée volontairement réduite : les fichiers d'EXEMPLES du dépôt (aucune donnée
d'atelier) dans une base PostgreSQL jetable. C'est la séquence de la recette,
reproductible partout.

Compétences mobilisées : ``postgres-patterns`` (base jetable, transaction
réelle), ``tdd-workflow`` (test écrit avant la correction : rouge, puis vert),
``verification-loop`` (la reprise est rejouée : second dépôt), ``redis-patterns``
(non concerné : le dépôt est transactionnel côté base).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.fiches import depot
from tests.test_depot_transactionnel import base_depot  # noqa: F401 - fixture partagée

pytestmark = pytest.mark.postgres

FICHE_EXEMPLE = (
    Path(__file__).resolve().parent.parent / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"
)
NOMENCLATURE_EXEMPLE = (
    Path(__file__).resolve().parent.parent / "sample_data" / "CLIENT-123" / "nomenclature-voile.xlsx"
)


def _dossier_exemple(racine: Path) -> Path:
    """Un dossier d'affaire RECONNAISSABLE : fiche au nom ffab + pièces jointes.

    Les pièces jointes vivent dans des sous-dossiers SANS PDF (gabarit réel de
    l'atelier : fichiers du traceur, accessoires) — c'est ainsi que le dépôt
    Lot C les rattache à la fiche. La nomenclature y est une PIÈCE JOINTE :
    l'import en extrait un résumé, donc une empreinte de contenu différente de
    celle de ses octets — exactement le cas qui faisait refuser le dépôt.
    """
    dossier = racine / "CLIENT-7792-SO"
    (dossier / "PLC").mkdir(parents=True)
    (dossier / "ACCESSOIRES").mkdir(parents=True)
    shutil.copy(FICHE_EXEMPLE, dossier / "fiche-7792-SO_ffab.pdf")
    shutil.copy(NOMENCLATURE_EXEMPLE, dossier / "PLC" / "nomenclature-voile.xlsx")
    (dossier / "ACCESSOIRES" / "toile.txt").write_bytes(b"toile ST792 synthetique\n")
    return dossier


def _config_stockage(base_depot: dict, racine: Path, travail: Path) -> AppConfig:  # noqa: F811
    """Import AVEC stockage objet : les lignes ``documents`` portent une clé."""
    return AppConfig(
        root_paths=[racine, travail],
        database_path=travail / "search.db",
        database_url=base_depot["url"],
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-synthetique",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )


def test_depot_apres_import_du_meme_dossier_est_accepte(base_depot, tmp_path: Path, monkeypatch):  # noqa: F811
    """Import puis dépôt Lot C : les pièces n'ont PAS changé, le dépôt passe."""
    from seamtech_search.import_pipeline import import_folder
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda self, **_kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, "ensure_bucket_exists", lambda self: True)

    index = base_depot["index"]
    racine = tmp_path / "archive"
    dossier = _dossier_exemple(racine)
    config = _config_stockage(base_depot, racine, tmp_path)

    # 1. Le flux d'import décrit les fichiers : contenu extrait + clé objet.
    resultat_import = import_folder(
        source=dossier, config=config, index=index, import_id="import-avant-depot",
    )
    assert resultat_import.status in {"completed", "needs_review"}, resultat_import.status
    assert resultat_import.files, "aucun fichier importé : le scénario n'est pas reproduit"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT count(*) FROM documents WHERE source_sha256 IS NOT NULL "
                "AND object_key IS NOT NULL AND path LIKE %s",
                (f"{dossier}%",),
            )
            assert curseur.fetchone()[0] > 0, (
                "l'import doit établir l'identité des octets ET une clé objet : "
                "c'est cette combinaison qui faisait refuser le dépôt"
            )

    # 2. Le dépôt Lot C du MÊME dossier : aucune pièce n'a changé.
    resultat = depot.deposer_dossier(index, dossier)
    assert resultat["statut"] == "traite", (
        f"dépôt refusé sur un dossier inchangé : {resultat.get('raison')}"
    )
    assert resultat["fiche"], "aucune fiche créée"
    assert int(resultat["pieces"]) >= 1, "le dossier d'exemples porte au moins une pièce jointe"


def test_depot_rejoue_garde_l_identite_des_octets_des_pieces(base_depot, tmp_path: Path):  # noqa: F811
    """Deux dépôts du même dossier : l'identité des pièces survit au premier."""
    index = base_depot["index"]
    racine = tmp_path / "archive"
    dossier = _dossier_exemple(racine)

    premier = depot.deposer_dossier(index, dossier)
    assert premier["statut"] == "traite", premier.get("raison")

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT count(*) FROM documents d JOIN fiche_piece_jointe p ON p.id_document = d.id "
                "WHERE d.parent_path LIKE %s AND d.source_sha256 IS NOT NULL",
                (f"{dossier}%",),
            )
            pieces_identifiees = int(curseur.fetchone()[0])
    assert pieces_identifiees >= 1, (
        "les pièces jointes doivent porter l'identité de leurs octets après le dépôt : "
        "sans elle, le dépôt suivant ne peut plus prouver qu'elles n'ont pas changé"
    )

    second = depot.deposer_dossier(index, dossier)
    assert second["statut"] in {"traite", "deja_traite"}, (
        f"reprise refusée alors que rien n'a changé : {second.get('raison')}"
    )
