"""Rebuild d'index SÛR — PostgreSQL réel (défaut E-40).

Défaut corrigé, en une phrase : ``seamtech_search index --rebuild`` était
**inutilisable sur PostgreSQL**. ``DROP TABLE documents`` était refusé
(``chunk.id_document`` en CASCADE et ``fiche_piece_jointe.id_document`` en
NO ACTION la référencent), puis le chemin de retour en arrière échouait à son
tour (``TRUNCATE TABLE documents`` → même refus) — **masquant l'erreur
d'origine** derrière une erreur secondaire sans rapport.

Pour un produit de recherche, reconstruire l'index est une opération
d'exploitation courante : elle ne peut ni échouer sur une base peuplée, ni
casser les liens fiche ↔ document.

Ce que ces tests éprouvent — les cinq preuves demandées par la revue :

1. le rebuild **aboutit** sur une base PostgreSQL peuplée, par le CLI RÉEL ;
2. fiches, pièces jointes et liens **restent valides** (les identifiants des
   documents sont conservés : un lien ne devient jamais orphelin) ;
3. la **recherche** et le **téléchargement** continuent de fonctionner ;
4. un rebuild **interrompu** — et même un retour arrière qui échoue — laisse un
   état **récupérable** : l'instantané est conservé ;
5. l'**erreur d'origine est préservée** — jamais remplacée par l'échec
   secondaire (elle est re-levée telle quelle, avec le détail du retour arrière
   attaché en note, ``add_note``).

Contrôle négatif (le test doit échouer sur le code d'AVANT) : sur le code
d'origine, ``test_cli_rebuild_aboutit_sur_une_base_postgres_peuplee`` échoue
avec ``psycopg2.errors.DependentObjectsStillExist: cannot drop table documents
because other objects depend on it`` — c'est exactement le défaut E-40.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from tests.reconstruire_aide import (
    ecrire_config,
    id_document,
    identifiants_documents,
    indexer,
    lien_piece_resolu,
    peupler_dossiers,
)

RACINE = Path(__file__).resolve().parent.parent
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)


def _creer_fiche_avec_piece(index: Any, code: str, id_document_: int, chemin: str) -> int:
    """Fiche + pièce jointe liée à UN document — comme le fait le dépôt réel.

    Le lien est exactement celui que le téléchargement utilise
    (``fiche_piece_jointe.id_document → documents.id``).
    """
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("INSERT INTO fiche (code, statut) VALUES (%s, %s) RETURNING id_fiche", (code, "a_valider"))
            id_fiche = int(curseur.fetchone()[0])
            curseur.execute(
                "INSERT INTO fiche_piece_jointe "
                "(id_fiche, id_document, chemin, role, empreinte_sha256, taille_octets) "
                "VALUES (%s, %s, %s, 'fiche', %s, %s)",
                (id_fiche, id_document_, chemin, "a" * 64, 128),
            )
    return id_fiche


@pytest.fixture()
def base_rebuild() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable, migrée, peuplée par l'indexeur RÉEL."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    nom_base = f"rebuild_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as curseur:
            curseur.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"

    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    try:
        yield {"index": index, "url": url_base, "nom": nom_base}
    finally:
        index.close()
        from tests.conftest import _supprimer_base_jetable

        _supprimer_base_jetable(nom_base)


# Preuve 1 : l'opération qui était cassée aboutit, sur une base peuplée, par le
# CLI réel (celui que l'exploitant lance).
def test_cli_rebuild_aboutit_sur_une_base_postgres_peuplee(base_rebuild: dict[str, Any], tmp_path: Path) -> None:
    from seamtech_search.config import AppConfig

    index = base_rebuild["index"]
    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    chemin_config = ecrire_config(tmp_path, racine, base_rebuild["url"])
    config = AppConfig.load(str(chemin_config))
    indexer(index, config)
    avant = identifiants_documents(index)
    assert len(avant) >= 2, f"index initial incomplet : {avant}"
    _creer_fiche_avec_piece(
        index, "FICHE-E40", id_document(index, "dossier-client/fiche-A.txt"), "dossier-client/fiche-A.txt"
    )

    resultat = subprocess.run(
        [sys.executable, "-m", "seamtech_search", "index", "--config", str(chemin_config), "--rebuild"],
        cwd=RACINE,
        env={**os.environ, "SEAMTECH_DATABASE_URL": base_rebuild["url"]},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert resultat.returncode == 0, f"le CLI a échoué :\n{resultat.stdout}\n{resultat.stderr}"
    assert "cannot drop table" not in resultat.stderr, resultat.stderr
    assert "Traceback" not in resultat.stderr, resultat.stderr
    assert "Indexing complete" in resultat.stderr, resultat.stderr
    assert "mode « en_place »" in resultat.stderr, resultat.stderr

    # Le rebuild a bien tout RÉ-EXTRAIT (pas sauté via le manifeste) et les
    # identifiants sont ceux d'avant : les liens des fiches restent valides.
    assert identifiants_documents(index) == avant


# Preuve 2 : fiches, pièces jointes et liens restent valides.
def test_rebuild_preserve_fiches_pieces_et_identifiants(base_rebuild: dict[str, Any], tmp_path: Path) -> None:
    from seamtech_search.config import AppConfig

    index = base_rebuild["index"]
    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    config = AppConfig.load(str(ecrire_config(tmp_path, racine, base_rebuild["url"])))
    indexer(index, config)
    avant = identifiants_documents(index)
    id_fiche = _creer_fiche_avec_piece(
        index, "FICHE-E40", id_document(index, "dossier-client/fiche-A.txt"), "dossier-client/fiche-A.txt"
    )

    indexer(index, config, rebuild=True)

    assert identifiants_documents(index) == avant, "les identifiants ont changé : des liens seraient orphelins"
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT code, statut FROM fiche WHERE id_fiche = %s", (id_fiche,))
            assert curseur.fetchone() == ("FICHE-E40", "a_valider")
    liens = lien_piece_resolu(index, id_fiche)
    assert len(liens) == 1, f"le lien fiche → document ne se résout plus : {liens}"


# Preuve 3 : recherche ET téléchargement restent fonctionnels.
def test_rebuild_laisse_la_recherche_et_le_telechargement_fonctionnels(
    base_rebuild: dict[str, Any], tmp_path: Path
) -> None:
    from seamtech_search.config import AppConfig

    index = base_rebuild["index"]
    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    config = AppConfig.load(str(ecrire_config(tmp_path, racine, base_rebuild["url"])))
    indexer(index, config)
    id_fiche = _creer_fiche_avec_piece(
        index, "FICHE-E40", id_document(index, "dossier-client/fiche-A.txt"), "dossier-client/fiche-A.txt"
    )

    indexer(index, config, rebuild=True)

    # RECHERCHE : le contenu ré-extrait est bien indexé.
    resultats = index.search("monofilm")
    assert resultats, "la recherche ne trouve plus le document après rebuild"
    assert any(ligne["name"] == "fiche-A.txt" for ligne in resultats), resultats

    # TÉLÉCHARGEMENT : la pièce jointe de la fiche résout vers un document du
    # catalogue, et ce document porte toujours le chemin du fichier : c'est
    # cette jointure que l'API utilise pour servir la pièce (l'invariant menacé
    # par le DROP). Le parcours navigateur complet de téléchargement est prouvé
    # séparément par frontend/e2e/hors-ligne.spec.ts.
    liens = lien_piece_resolu(index, id_fiche)
    assert len(liens) == 1, liens
    chemin_piece, chemin_document = liens[0]
    assert chemin_document.endswith(chemin_piece), liens
    assert (racine / chemin_piece).exists(), "le fichier à télécharger n'existe plus"
    assert (racine / chemin_piece).read_text(encoding="utf-8").startswith("Fiche A")


# Un document disparu du disque mais RÉFÉRENCÉ par une fiche est conservé ;
# un document disparu et non référencé est bien retiré.
def test_rebuild_ne_supprime_pas_un_document_reference_absent_du_disque(
    base_rebuild: dict[str, Any], tmp_path: Path
) -> None:
    from seamtech_search.config import AppConfig

    index = base_rebuild["index"]
    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    config = AppConfig.load(str(ecrire_config(tmp_path, racine, base_rebuild["url"])))
    indexer(index, config)
    id_fiche = _creer_fiche_avec_piece(
        index, "FICHE-E40", id_document(index, "dossier-client/fiche-A.txt"), "dossier-client/fiche-A.txt"
    )
    id_reference = id_document(index, "dossier-client/fiche-A.txt")
    id_note = id_document(index, "dossier-client/note-B.txt")

    # LES DEUX fichiers disparaissent du disque : celui qui est référencé par la
    # fiche doit survivre, celui qui ne l'est pas doit partir.
    (racine / "dossier-client" / "fiche-A.txt").unlink()
    (racine / "dossier-client" / "note-B.txt").unlink()

    resultat = indexer(index, config, rebuild=True)

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT 1 FROM documents WHERE id = %s", (id_reference,))
            assert curseur.fetchone() is not None, "document référencé supprimé : lien cassé"
            curseur.execute("SELECT 1 FROM documents WHERE id = %s", (id_note,))
            assert curseur.fetchone() is None, "document non référencé conservé à tort"
    assert resultat["removed"] == 1, resultat
    assert len(lien_piece_resolu(index, id_fiche)) == 1


# Preuve 4 (volet A) : un scan interrompu rend l'état d'origine à l'identique.
def test_rebuild_interrompu_restaure_l_instantane_a_l_identique(base_rebuild: dict[str, Any]) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir,"
                " content) VALUES ('reste/test.txt', '/reste/test.txt', 'test.txt', '/reste', '.txt', 3, 1.0, false,"
                " 'abc') RETURNING id"
            )
            id_existant = int(curseur.fetchone()[0])
    avant = identifiants_documents(index)

    with pytest.raises(ValueError, match="panne simulée"):
        with index.scan_snapshot():
            with index.connect() as connexion:
                with connexion.cursor() as curseur:
                    # Ce que ferait un scan : ajouts, modifications… puis panne.
                    curseur.execute(
                        "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at,"
                        " is_dir, content) VALUES ('ajoute/par-le-scan.txt', '/x', 'x.txt', '/ajoute', '.txt', 1,"
                        " 1.0, false, 'x')"
                    )
                    curseur.execute("UPDATE documents SET name = 'renomme.txt' WHERE id = %s", (id_existant,))
            raise ValueError("panne simulée du scan")

    assert identifiants_documents(index) == avant, "l'instantané n'a pas rendu l'état d'origine"
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT name FROM documents WHERE id = %s", (id_existant,))
            assert curseur.fetchone()[0] == "test.txt", "valeur modifiée non restaurée"
            curseur.execute("SELECT to_regclass('public.scan_backup_documents_%s')", (os.getpid(),))
            assert curseur.fetchone()[0] is None, "instantané laissé derrière un retour arrière réussi"
            # La séquence reste utilisable : un identifiant neuf ne collisionne pas.
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir,"
                " content) VALUES ('apres/restauration.txt', '/y', 'y.txt', '/apres', '.txt', 1, 1.0, false, 'y')"
            )


# Preuves 4 (volet B) et 5 : un retour arrière qui ÉCHOUE conserve l'instantané
# et laisse remonter l'ERREUR D'ORIGINE, pas la panne secondaire.
def test_retour_arriere_rate_conserve_l_instantane_et_l_erreur_dorigine(
    base_rebuild: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    index = base_rebuild["index"]
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir,"
                " content) VALUES ('origine/a.txt', '/a', 'a.txt', '/origine', '.txt', 1, 1.0, false, 'a')"
            )

    def restauration_en_panne(*_args: Any, **_kwargs: Any) -> dict[str, int]:
        raise RuntimeError("panne de restauration simulée")

    monkeypatch.setattr(index, "_restaurer_documents", restauration_en_panne)

    with pytest.raises(ValueError, match="panne simulée du scan") as capture:
        with index.scan_snapshot():
            raise ValueError("panne simulée du scan")

    # 1) L'ERREUR D'ORIGINE est celle qui remonte — jamais l'échec secondaire,
    #    qui est seulement ATTACHÉ (add_note) pour le diagnostic.
    assert isinstance(capture.value, ValueError)
    notes = " ".join(getattr(capture.value, "__notes__", []))
    assert "Retour arrière" in notes and "panne de restauration simulée" in notes, notes

    # 2) L'INSTANTANÉ EST CONSERVÉ : l'état reste récupérable à la main.
    monkeypatch.undo()
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT to_regclass('public.scan_backup_documents_%s')", (os.getpid(),))
            nom_instantane = curseur.fetchone()[0]
            assert nom_instantane is not None, "instantané supprimé alors que le retour arrière a échoué"
            rapport = index._restaurer_documents(curseur, nom_instantane)
            assert rapport["colonnes"] > 0
            curseur.execute("SELECT COUNT(*) FROM documents WHERE path_key = 'origine/a.txt'")
            assert int(curseur.fetchone()[0]) == 1
