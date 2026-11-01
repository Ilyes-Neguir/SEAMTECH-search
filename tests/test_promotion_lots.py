"""P0.2 / OBS-05 — les fichiers de lots remontent vraiment vers le stockage objet.

Ce que le rapport du 28/10 reprochait, précisément : les octets déposés par lot
restaient sous ``uploads/lots-televerses``, sur le disque local ; leur
promotion vers MinIO/R2 n'était pas démontrée. Un sauvegarde de PostgreSQL
seul, ou de MinIO seul, ne restituait donc pas les pièces.

Ces tests déposent un dossier RÉEL (fiche 7792-SO du corpus de recette), avec
un stockage objet branché sur un magasin en mémoire qui conserve les octets —
donc la vérification post-envoi est réelle, elle relit ce qui a été stocké.

Couvert :

1. fiche ET pièces jointes envoyées, clé d'objet publiée en base ;
2. octets stockés identiques au fichier (SHA-256 relu) ;
3. aucune copie locale supprimée par la promotion ;
4. stockage indisponible : le dossier reste déposé, l'état est ``failed``, la
   reprise (``promouvoir_en_attente``) aboutit ensuite ;
5. intégrité non prouvée : aucune clé n'est publiée (statut non ``uploaded``) ;
6. rejeu de la promotion : aucun doublon d'objet ;
7. sans stockage configuré : statut ``not_configured``, jamais un faux
   « pending » qui laisserait croire à un envoi en cours.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.fiches import depot
from seamtech_search.fiches.promotion import promouvoir_en_attente
from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

RACINE = Path(__file__).resolve().parent.parent
ARCHIVE_7792 = RACINE / "sample_data/CLIENT-7792-SO"
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres


def _sha256(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def base_lot() -> Iterator[dict[str, Any]]:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"promotion_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path(f"/tmp/unused-promotion-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    try:
        yield {"index": index, "url": url_base}
    finally:
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}" WITH (FORCE)')
        finally:
            admin.close()


@pytest.fixture()
def dossier_stage(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Dossier d'affaire déposé, tel qu'il arrive sous uploads/lots-televerses.

    Le nom logique est unique par test : la base est partagée par le module et
    le dépôt est idempotent par (chemin logique, empreinte). Deux tests qui
    déposeraient le même chemin verraient le second répondre ``deja_traite``.
    """
    affaire = "".join(c if c.isalnum() else "-" for c in request.node.name)[:40]
    dossier = tmp_path / "uploads" / "lots-televerses" / "jeton-test" / "fichiers" / "2026" / affaire
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche.pdf")
    (dossier / "croquis.md").write_text("notes atelier, pièce jointe", encoding="utf-8")
    return dossier


def _config(tmp_path: Path, **extra: object) -> AppConfig:
    parametres: dict[str, object] = {
        "root_paths": [tmp_path],
        "min_free_bytes": 0,
        "s3_endpoint_url": "https://s3.invalide",
        "s3_bucket": "seamtech-documents",
        "s3_access_key": "cle-fictive",
        "s3_secret_key": "secret-fictif",
    }
    parametres.update(extra)
    return AppConfig(**parametres)  # type: ignore[arg-type]


class _Magasin:
    """Branche S3StorageClient sur un magasin en mémoire.

    Les MÉTHODES RÉELLES du client tournent (envoi, relecture, empreinte) : seul
    le transport boto3 est remplacé. Un ``MagicMock`` ne prouverait rien sur
    les octets réellement stockés.
    """

    def __init__(self) -> None:
        self.magasin = S3EnMemoire()
        self._patchs: list[Any] = []

    def __enter__(self) -> S3EnMemoire:
        magasin = self.magasin
        self._patchs = [
            patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
            patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
        ]
        for p in self._patchs:
            p.start()
        return magasin

    def __exit__(self, *_exc: object) -> None:
        for p in self._patchs:
            p.stop()


def _documents(index: Any, chemins: list[Path]) -> dict[str, dict[str, Any]]:
    sortie: dict[str, dict[str, Any]] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for chemin in chemins:
                cursor.execute(
                    "SELECT object_key, object_bucket, upload_status, uploaded_at FROM documents WHERE path_key = %s",
                    (os.path.normcase(str(chemin.resolve())),),
                )
                ligne = cursor.fetchone()
                assert ligne is not None, f"document absent du catalogue : {chemin}"
                sortie[str(chemin)] = {
                    "object_key": ligne[0],
                    "object_bucket": ligne[1],
                    "upload_status": ligne[2],
                    "uploaded_at": ligne[3],
                }
    return sortie


# ---------------------------------------------------------------------------
# 1-3. Promotion nominale : objet réel, octets vérifiés, aucune purge
# ---------------------------------------------------------------------------


def test_fiche_et_pieces_promues_avec_octets_verifies(base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path) -> None:
    index = base_lot["index"]
    config = _config(tmp_path)
    with _Magasin() as magasin:
        resultat = depot.deposer_dossier(index, dossier_stage, config=config)
        assert resultat["statut"] == "traite", resultat
        promotion = resultat["promotion"]
        assert promotion["statut"] == "uploaded", promotion
        assert promotion["verifies"] == 2 and promotion["objets"] == 2

        fiche = dossier_stage / "fiche.pdf"
        piece = dossier_stage / "croquis.md"
        documents = _documents(index, [fiche, piece])
        for chemin, ligne in documents.items():
            assert ligne["upload_status"] == "uploaded", (chemin, ligne)
            assert ligne["object_key"], (chemin, ligne)
            assert ligne["object_bucket"] == "seamtech-documents"
            assert ligne["uploaded_at"] is not None

        # Les octets stockés sont EXACTEMENT ceux des fichiers locaux.
        for chemin in (fiche, piece):
            objet = magasin.objets[documents[str(chemin)]["object_key"]]
            assert hashlib.sha256(objet["donnees"]).hexdigest() == _sha256(chemin)
            # Les clés gardent la session de téléversement d'origine.
            assert documents[str(chemin)]["object_key"].startswith("lots/jeton-test/")

    # Aucune copie locale n'est supprimée par la promotion.
    assert fiche.exists() and piece.exists()


# ---------------------------------------------------------------------------
# 4. Panne de stockage : le dépôt reste valide, la reprise aboutit
# ---------------------------------------------------------------------------


def test_stockage_indisponible_puis_reprise(base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path) -> None:
    index = base_lot["index"]
    config = _config(tmp_path)
    from seamtech_search.storage import StorageError

    with patch.object(S3StorageClient, "upload_file", side_effect=StorageError("MinIO injoignable")):
        resultat = depot.deposer_dossier(index, dossier_stage, config=config)
    assert resultat["statut"] == "traite", resultat  # la fiche est commitée
    assert resultat["promotion"]["statut"] == "failed"
    fiche = dossier_stage / "fiche.pdf"
    piece = dossier_stage / "croquis.md"
    etats = _documents(index, [fiche, piece])
    assert all(ligne["upload_status"] == "failed" for ligne in etats.values()), etats
    assert all(ligne["object_key"] is None for ligne in etats.values()), etats

    # La reprise envoie ce qui ne l'a pas été, sans redéposer le dossier.
    with _Magasin() as magasin:
        reprise = promouvoir_en_attente(index, config)
        assert reprise["promus"] >= 2, reprise
        etats = _documents(index, [fiche, piece])
        assert all(ligne["upload_status"] == "uploaded" for ligne in etats.values()), etats
        assert len(magasin.objets) >= 2

        # 6. Rejeu : plus aucun candidat, donc aucun doublon d'objet.
        nombre_objets = len(magasin.objets)
        encore = promouvoir_en_attente(index, config)
        assert encore["candidats"] == 0, encore
        assert len(magasin.objets) == nombre_objets


# ---------------------------------------------------------------------------
# 5. Intégrité non prouvée : aucune clé publiée
# ---------------------------------------------------------------------------


def test_integrite_non_prouvee_n_publie_aucune_cle(base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path) -> None:
    from seamtech_search.storage import ResultatVerification

    index = base_lot["index"]
    config = _config(tmp_path)
    with _Magasin():
        with patch.object(
            S3StorageClient,
            "verifier_integrite",
            lambda self, cle, chemin, relire=True: ResultatVerification(
                integrite_prouvee=False, methode="relecture_desactivee", detail="non prouvé"
            ),
        ):
            resultat = depot.deposer_dossier(index, dossier_stage, config=config)
    assert resultat["statut"] == "traite"
    assert resultat["promotion"]["statut"] != "uploaded"
    etats = _documents(index, [dossier_stage / "fiche.pdf", dossier_stage / "croquis.md"])
    for ligne in etats.values():
        assert ligne["object_key"] is None, ligne
        assert ligne["upload_status"] != "uploaded", ligne


# ---------------------------------------------------------------------------
# 7. Sans stockage configuré : statut explicite
# ---------------------------------------------------------------------------


def test_sans_stockage_configure_statut_explicite(base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path) -> None:
    index = base_lot["index"]
    config = AppConfig(root_paths=[tmp_path], min_free_bytes=0, s3_endpoint_url=None)
    resultat = depot.deposer_dossier(index, dossier_stage, config=config)
    assert resultat["statut"] == "traite"
    assert resultat["promotion"]["statut"] == "not_configured"
    etats = _documents(index, [dossier_stage / "fiche.pdf", dossier_stage / "croquis.md"])
    assert all(ligne["upload_status"] == "not_configured" for ligne in etats.values()), etats
    assert all(ligne["object_key"] is None for ligne in etats.values()), etats


# ---------------------------------------------------------------------------
# 8. Rejeu (deja_traite) : les copies stagées du rejeu ne s'accumulent pas
# ---------------------------------------------------------------------------


def _rejouer(dossier_stage: Path, tmp_path: Path, jeton: str) -> Path:
    """Même dossier, MÊME chemin logique, mais une nouvelle session de dépôt.

    C'est exactement ce que produit un opérateur qui réenvoie une affaire : le
    jeton de session change, donc le chemin absolu change, mais le chemin
    relatif et le SHA-256 sont identiques — le dépôt doit répondre
    ``deja_traite``.
    """
    # Chemin logique = les trois derniers segments (année / affaire) : c'est
    # lui qui identifie le dossier pour l'idempotence, pas le jeton de session.
    relatif = Path(*dossier_stage.parts[-2:])
    cible = tmp_path / "uploads" / "lots-televerses" / jeton / "fichiers" / relatif
    if cible.exists():
        shutil.rmtree(cible)
    shutil.copytree(dossier_stage, cible)
    return cible


def test_rejeu_supprime_les_copies_stagees_du_rejeu(
    base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path
) -> None:
    index = base_lot["index"]
    config = _config(tmp_path)
    with _Magasin():
        premier = depot.deposer_dossier(index, dossier_stage, config=config)
        assert premier["statut"] == "traite", premier

        rejeu = _rejouer(dossier_stage, tmp_path, "jeton-rejeu")
        assert rejeu.is_dir()
        second = depot.deposer_dossier(index, rejeu, config=config)

    assert second["statut"] == "deja_traite", second
    assert "deja_traite" in (second["raison"] or "")
    # Les copies du rejeu sont parties ; celles du dépôt d'origine restent.
    assert not (rejeu / "fiche.pdf").exists()
    assert not (rejeu / "croquis.md").exists()
    assert second["copies_rejeu_supprimees"] == 2, second
    assert (dossier_stage / "fiche.pdf").exists()
    assert (dossier_stage / "croquis.md").exists()


def test_rejeu_ne_supprime_jamais_une_copie_differente(
    base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path
) -> None:
    """Un fichier modifié entre-temps n'est PAS une copie : il est conservé."""
    index = base_lot["index"]
    config = _config(tmp_path)
    with _Magasin():
        assert depot.deposer_dossier(index, dossier_stage, config=config)["statut"] == "traite"
        rejeu = _rejouer(dossier_stage, tmp_path, "jeton-rejeu-modifie")
        (rejeu / "croquis.md").write_text("notes atelier MODIFIÉES", encoding="utf-8")
        second = depot.deposer_dossier(index, rejeu, config=config)

    assert second["statut"] == "deja_traite", second
    assert not (rejeu / "fiche.pdf").exists(), "la copie identique du PDF de fiche est nettoyée"
    assert (rejeu / "croquis.md").exists(), "un fichier différent ne doit jamais être supprimé"
    assert second["copies_rejeu_supprimees"] == 1, second


def test_archive_par_chemin_jamais_supprimee(
    base_lot: dict[str, Any], dossier_stage: Path, tmp_path: Path
) -> None:
    """Hors session de téléversement (dépôt par chemin), rien n'est supprimé (RG13)."""
    index = base_lot["index"]
    config = _config(tmp_path)
    archive = tmp_path / "archive-lecture-seule" / "2026" / "affaire-chemin"
    archive.parent.mkdir(parents=True)
    shutil.copytree(dossier_stage, archive)
    with _Magasin():
        premier = depot.deposer_dossier(index, archive, config=config)
        second = depot.deposer_dossier(index, archive, config=config)
    assert premier["statut"] == "traite", premier
    assert second["statut"] == "deja_traite", second
    assert second["copies_rejeu_supprimees"] == 0, second
    assert (archive / "fiche.pdf").exists()
    assert (archive / "croquis.md").exists()
