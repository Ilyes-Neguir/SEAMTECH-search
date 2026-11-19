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
4. un rebuild **interrompu** est annulé transactionnellement ; si le retour
   arrière échoue, l'instantané est conservé pour investigation, sans preuve
   qu'un rejeu divergent soit sûr ;
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

from tests.helpers_postgres_isole import url_base_jetable
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
    url_base = url_base_jetable(URL_PG, nom_base)

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
    # Comparer les COMPOSANTS, pas la chaîne : sur Windows le chemin stocké
    # porte des « \ » alors que le chemin logique de la pièce porte des « / ».
    # Un « endswith » y échouait pour rien — mesuré au run 43121414811, cause
    # « [('dossier-client/fiche-A.txt', 'C:\...\documents\dossier-client\fiche-A.txt')] ».
    fin = Path(chemin_document).parts[-len(Path(chemin_piece).parts):]
    assert fin == Path(chemin_piece).parts, liens
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

    def rollback_en_panne(_connexion: Any) -> None:
        raise RuntimeError("panne de restauration simulée")

    monkeypatch.setattr(index, "_effectuer_rollback", rollback_en_panne)

    with pytest.raises(ValueError, match="panne simulée du scan") as capture:
        with index.scan_snapshot():
            raise ValueError("panne simulée du scan")

    # 1) L'ERREUR D'ORIGINE est celle qui remonte — jamais l'échec secondaire,
    #    qui est seulement ATTACHÉ (add_note) pour le diagnostic.
    assert isinstance(capture.value, ValueError)
    notes = " ".join(getattr(capture.value, "__notes__", []))
    assert "Retour arrière" in notes and "panne de restauration simulée" in notes, notes

    # 2) L'INSTANTANÉ EST CONSERVÉ. Ici la copie est identique et ne demande
    # aucune écriture ; un rejeu divergent n'est pas déclaré sûr.
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


def test_scan_refuse_ecrasement_instantane_postgres_existant(base_rebuild: dict[str, Any]) -> None:
    index = base_rebuild['index']
    nom = f'scan_backup_documents_{os.getpid()}'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(f'CREATE TABLE "{nom}" (marqueur integer)')
            curseur.execute(f'INSERT INTO "{nom}" VALUES (17)')
    with pytest.raises(RuntimeError, match='instantané'):
        with index.scan_snapshot():
            pass
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(f'SELECT marqueur FROM "{nom}"')
            assert curseur.fetchone()[0] == 17


def test_transaction_scan_postgres_preserve_modification_concurrente(base_rebuild: dict[str, Any]) -> None:
    import psycopg2

    index = base_rebuild['index']
    with index.connect() as c:
        with c.cursor() as cur:
            cur.execute("INSERT INTO documents (path_key,path,name,parent_path,extension,size,modified_at,is_dir) VALUES ('un','/un','un','/','.txt',1,1,false),('deux','/deux','deux','/','.txt',1,1,false)")
    with pytest.raises(ValueError, match='panne scan'):
        with index.scan_transaction():
            with index.connect() as c:
                with c.cursor() as cur:
                    cur.execute("UPDATE documents SET size=2 WHERE path_key='un'")
            autre = psycopg2.connect(base_rebuild['url'])
            try:
                with autre.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '100ms'")
                    with pytest.raises(psycopg2.errors.LockNotAvailable):
                        cur.execute("UPDATE documents SET size=3 WHERE path_key='un'")
                autre.rollback()
                with autre.cursor() as cur:
                    cur.execute("UPDATE documents SET size=3 WHERE path_key='deux'")
                autre.commit()
            finally:
                autre.close()
            raise ValueError('panne scan')
    with index.connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT path_key,size FROM documents WHERE path_key IN ('un','deux') ORDER BY path_key")
            assert cur.fetchall() == [('deux', 3), ('un', 1)]


def test_cli_scan_postgres_ne_retablit_pas_les_ecritures_concurrentes(base_rebuild: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import psycopg2

    import seamtech_search.cli as cli
    from seamtech_search.models import Document

    index = base_rebuild['index']
    racine = tmp_path / 'fichiers'
    racine.mkdir()
    chemins = [racine / 'un.txt', racine / 'deux.txt']
    for chemin in chemins:
        index.upsert_document(Document(chemin, chemin.name, racine, '.txt', 1, 1, False, 'avant'))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(racine)], 'database_path': str(tmp_path / 'unused.db'),
                                  'database_url': base_rebuild['url']}), encoding='utf-8')
    def crawl_avec_concurrent(*_args):
        yield Document(chemins[0], chemins[0].name, racine, '.txt', 2, 2, False, 'scan')
        autre = psycopg2.connect(base_rebuild['url'])
        try:
            with autre.cursor() as curseur:
                curseur.execute("SET LOCAL lock_timeout = '2s'")
                # Même clé que celle que le produit a écrite : Document.path_key vaut
                # os.path.normcase(str(resolve())) — models.py. Passer le chemin brut ne
                # touchait AUCUNE ligne sur Windows, où normcase met en minuscules et
                # convertit « / » en « \ » : la concurrence n'avait donc pas lieu, et
                # le test échouait sur un tout autre motif (run 43121414811).
                curseur.execute('UPDATE documents SET size=3 WHERE path_key=%s', (Document(chemins[1], chemins[1].name, racine, '.txt', 1, 1, False, 'avant').path_key,))
            autre.commit()
        finally:
            autre.close()
        raise RuntimeError('echec du scan')
    monkeypatch.setattr(cli, 'BATCH_SIZE', 1)
    monkeypatch.setattr(cli, 'crawl', crawl_avec_concurrent)
    with pytest.raises(RuntimeError, match='echec du scan'):
        cli.run_index(str(config))
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            cles = [
                Document(p, p.name, racine, '.txt', 1, 1, False, 'avant').path_key
                for p in (chemins[1], chemins[0])
            ]
            curseur.execute('SELECT path_key,size FROM documents WHERE path_key IN (%s,%s) ORDER BY path_key',
                            tuple(cles))
            # Attendu dans le MÊME ordre que la clé de tri « ORDER BY path_key » :
            # sur Windows les deux clés sont en minuscules, l'ordre alphabétique
            # n'y est donc pas celui des chemins d'origine.
            attendu = sorted([(cles[0], 3), (cles[1], 1)], key=lambda couple: couple[0])
            assert curseur.fetchall() == attendu
    assert index.latest_scan()['status'] == 'failed'


def test_crash_processus_annule_transaction_scan_postgres(base_rebuild: dict[str, Any], tmp_path: Path) -> None:
    index = base_rebuild['index']
    with index.connect() as c:
        with c.cursor() as cur:
            cur.execute("INSERT INTO documents (path_key,path,name,parent_path,extension,size,modified_at,is_dir) VALUES ('crash','/crash','crash','/','.txt',1,1,false)")
    code = """
import os, sys
from pathlib import Path
from seamtech_search.indexer import SearchIndex
index = SearchIndex(Path(sys.argv[1]), sys.argv[2])
with index.scan_transaction():
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("UPDATE documents SET size=9 WHERE path_key='crash'")
    os._exit(23)
"""
    proces = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'unused.db'), base_rebuild['url']],
                            timeout=20, check=False)
    assert proces.returncode == 23
    with index.connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT size FROM documents WHERE path_key='crash'")
            assert cur.fetchone()[0] == 1


def test_scan_snapshot_historique_preserve_ecriture_concurrente(base_rebuild: dict[str, Any]) -> None:
    import psycopg2

    index = base_rebuild['index']
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute("INSERT INTO documents (path_key,path,name,parent_path,extension,size,modified_at,is_dir) VALUES ('snapshot-a','/a','a','/','.txt',1,1,false),('snapshot-b','/b','b','/','.txt',1,1,false)")
    with pytest.raises(RuntimeError, match='panne scan'):
        with index.scan_snapshot():
            with index.connect() as connexion:
                with connexion.cursor() as c:
                    c.execute("UPDATE documents SET size=2 WHERE path_key='snapshot-a'")
            autre = psycopg2.connect(base_rebuild['url'])
            try:
                with autre.cursor() as c:
                    c.execute("UPDATE documents SET size=3 WHERE path_key='snapshot-b'")
                autre.commit()
            finally:
                autre.close()
            raise RuntimeError('panne scan')
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute("SELECT path_key,size FROM documents WHERE path_key IN ('snapshot-a','snapshot-b') ORDER BY path_key")
            assert c.fetchall() == [('snapshot-a',1),('snapshot-b',3)]


def test_ecriture_postgres_attend_rollback_scan_puis_reussit(base_rebuild: dict[str, Any]) -> None:
    import threading

    import psycopg2

    index = base_rebuild['index']
    with index.connect() as c:
        with c.cursor() as curseur:
            curseur.execute("INSERT INTO documents (path_key,path,name,parent_path,extension,size,modified_at,is_dir) VALUES ('attente','/attente','attente','/','.txt',1,1,false)")
    entre = threading.Event()
    termine = threading.Event()
    erreurs = []
    def ecrire():
        try:
            with psycopg2.connect(base_rebuild['url']) as autre:
                with autre.cursor() as curseur:
                    curseur.execute("SET LOCAL lock_timeout = '4s'")
                    entre.set()
                    curseur.execute("UPDATE documents SET size=3 WHERE path_key='attente'")
            termine.set()
        except Exception as erreur:
            erreurs.append(erreur)
    with pytest.raises(RuntimeError, match='panne'):
        with index.scan_transaction():
            with index.connect() as c:
                with c.cursor() as curseur:
                    curseur.execute("UPDATE documents SET size=2 WHERE path_key='attente'")
            thread = threading.Thread(target=ecrire)
            thread.start()
            assert entre.wait(2)
            assert not termine.wait(0.1)
            raise RuntimeError('panne')
    thread.join(timeout=5)
    assert not thread.is_alive() and not erreurs and termine.is_set()
    with index.connect() as c:
        with c.cursor() as curseur:
            curseur.execute("SELECT size FROM documents WHERE path_key='attente'")
            assert curseur.fetchone()[0] == 3
