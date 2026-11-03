import json
import os
from pathlib import Path

import pytest

from seamtech_search.cli import run_index
from seamtech_search.crawler import ScanIncompleteError
from seamtech_search.indexer import ScanAlreadyRunningError, SearchIndex
from seamtech_search.models import Document


def cle(chemin: Path) -> str:
    """Clé de chemin TELLE QUE LE PRODUIT la stocke (pas le chemin brut).

    `Document.path_key` vaut `normcase(normpath(resolve(chemin)))`. Sur Windows,
    `normcase` met en minuscules : interroger la base avec `str(chemin.resolve())`
    ne trouve donc rien, et l'assertion qui suit échoue en
    `TypeError: 'NoneType' object is not subscriptable` — un message qui ne dit
    rien de la vraie cause (run 41523086709). Sur POSIX, `normcase` est
    l'identité : le comportement est inchangé.
    """
    return Document(chemin, chemin.name, chemin.parent, "", 0, 0, False, "").path_key


def test_incomplete_scan_preserves_existing_index(tmp_path: Path) -> None:
    database = tmp_path / "search.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    path = tmp_path / "existing.txt"
    document = Document(
        path=path,
        name=path.name,
        parent_path=tmp_path,
        extension=".txt",
        size=1,
        modified_at=1.0,
        is_dir=False,
        text="important factory reference",
    )
    index.upsert_document(document)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "root_paths": [str(tmp_path / "unavailable-share")],
                "database_path": str(database),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ScanIncompleteError):
        run_index(str(config_path))

    assert index.search("important")
    latest = index.latest_scan()
    assert latest is not None
    assert latest["status"] == "failed"


def test_successful_scan_records_completion(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "reference.txt").write_text("factory reference", encoding="utf-8")
    database = tmp_path / "search.db"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"root_paths": [str(root)], "database_path": str(database)}),
        encoding="utf-8",
    )

    run_index(str(config_path))
    latest = SearchIndex(database).latest_scan()
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["scanned"] >= 2


def test_scan_classifies_pdfs_for_analysis_and_other_files_for_direct_storage(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "drawing.PDF").write_bytes(b"not a real pdf")
    (nested / "model.dwg").write_bytes(b"cad data")
    (nested / "notes.txt").write_text("notes", encoding="utf-8")
    database = tmp_path / "search.db"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"root_paths": [str(root)], "database_path": str(database)}),
        encoding="utf-8",
    )

    run_index(str(config_path))

    index = SearchIndex(database)
    with index.connect() as connection:
        rows = connection.execute("SELECT name, category FROM documents WHERE is_dir = 0 ORDER BY name").fetchall()

    assert [(row["name"], row["category"]) for row in rows] == [
        ("drawing.PDF", "plan_pdf"),
        ("model.dwg", "storage_direct"),
        ("notes.txt", "storage_direct"),
    ]


def test_concurrent_scan_is_rejected(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    with index.scan_lock():
        with pytest.raises(ScanAlreadyRunningError):
            with index.scan_lock():
                pass


def test_scan_snapshot_restores_documents_after_failure(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    path = tmp_path / "existing.txt"
    document = Document(path, path.name, tmp_path, ".txt", 1, 1.0, False, "original")
    index.upsert_document(document)

    with pytest.raises(RuntimeError):
        with index.scan_snapshot():
            index.upsert_document(Document(path, path.name, tmp_path, ".txt", 2, 2.0, False, "changed"))
            raise RuntimeError("simulated scan failure")

    assert index.search("original")
    assert not index.search("changed")


@pytest.mark.parametrize('etape', ['dernier_lot', 'suppression'])
def test_echec_en_fin_de_scan_est_enregistre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, etape: str) -> None:
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'note.txt').write_text('synthétique', encoding='utf-8')
    database = tmp_path / 'search.db'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(root)], 'database_path': str(database)}), encoding='utf-8')
    def panne(*_args, **_kwargs):
        raise OSError('erreur de fin de scan synthétique')
    methode = 'upsert_documents' if etape == 'dernier_lot' else 'remove_missing'
    monkeypatch.setattr(SearchIndex, methode, panne)
    with pytest.raises(OSError, match='fin de scan'):
        run_index(str(config))
    etat = SearchIndex(database).latest_scan()
    assert etat and etat['status'] == 'failed'
    assert 'fin de scan' in etat['error']


@pytest.mark.parametrize('etape', ['manifeste', 'completion'])
def test_echec_etape_de_suivi_ne_reste_pas_en_cours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, etape: str) -> None:
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'note.txt').write_text('test synthétique', encoding='utf-8')
    database = tmp_path / 'search.db'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(root)], 'database_path': str(database)}), encoding='utf-8')
    original_finish = SearchIndex.finish_scan
    def panne_manifest(*_args, **_kwargs):
        raise OSError('erreur manifeste synthétique')
    def panne_completion(self, scan_id, status, *args, **kwargs):
        if status == 'completed':
            raise OSError('erreur completion synthétique')
        return original_finish(self, scan_id, status, *args, **kwargs)
    if etape == 'manifeste':
        monkeypatch.setattr(SearchIndex, 'stored_manifest', panne_manifest)
    else:
        monkeypatch.setattr(SearchIndex, 'finish_scan', panne_completion)
    with pytest.raises(OSError, match='synthétique'):
        run_index(str(config))
    etat = SearchIndex(database).latest_scan()
    assert etat and etat['status'] == 'failed'


def test_echec_restauration_sqlite_preserve_erreur_et_instantane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = SearchIndex(tmp_path / 'search.db')
    index.initialize()
    def rollback_en_panne(_connexion):
        raise OSError('restauration indisponible synthétique')
    monkeypatch.setattr(index, '_effectuer_rollback', rollback_en_panne)
    with pytest.raises(ValueError, match='panne originale') as erreur:
        with index.scan_snapshot():
            raise ValueError('panne originale')
    assert 'restauration indisponible' in ' '.join(getattr(erreur.value, '__notes__', []))
    assert (tmp_path / 'search.db.scan-backup').exists()


def test_scan_refuse_decraser_sauvegarde_sqlite_a_recuperer(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / 'search.db')
    index.initialize()
    sauvegarde = tmp_path / 'search.db.scan-backup'
    sauvegarde.write_bytes(b'instantane anterieur synthetique')
    with pytest.raises(RuntimeError, match='instantané'):
        with index.scan_snapshot():
            pass
    assert sauvegarde.read_bytes() == b'instantane anterieur synthetique'


def test_echec_suivi_ne_masque_pas_erreur_de_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'note.txt').write_text('test', encoding='utf-8')
    database = tmp_path / 'search.db'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(root)], 'database_path': str(database)}), encoding='utf-8')
    def panne_index(*_args, **_kwargs):
        raise OSError('erreur originale')
    def panne_suivi(*_args, **_kwargs):
        raise RuntimeError('suivi indisponible')
    monkeypatch.setattr(SearchIndex, 'upsert_documents', panne_index)
    monkeypatch.setattr(SearchIndex, 'finish_scan', panne_suivi)
    with pytest.raises(OSError, match='erreur originale') as capture:
        run_index(str(config))
    assert 'suivi indisponible' in ' '.join(getattr(capture.value, '__notes__', []))


def test_transaction_scan_sqlite_n_ecrase_pas_autres_ecritures(tmp_path: Path) -> None:
    import sqlite3

    index = SearchIndex(tmp_path / 'db.sqlite')
    index.initialize()
    with index.connect() as c:
        c.execute('CREATE TABLE autres_donnees (valeur TEXT)')
        c.execute("INSERT INTO autres_donnees VALUES ('initial')")
    doc_path = tmp_path / 'fichier.txt'
    initial = Document(doc_path, doc_path.name, tmp_path, '.txt', 1, 1, False, 'initial')
    index.upsert_document(initial)
    with pytest.raises(ValueError, match='panne scan'):
        with index.scan_transaction():
            index.upsert_document(Document(doc_path, doc_path.name, tmp_path, '.txt', 2, 2, False, 'scan'))
            autre = sqlite3.connect(index.database_path, timeout=0.1)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    autre.execute("UPDATE autres_donnees SET valeur='concurrent'")
            finally:
                autre.close()
            raise ValueError('panne scan')
    with sqlite3.connect(index.database_path) as autre:
        autre.execute("UPDATE autres_donnees SET valeur='concurrent'")
    with index.connect() as c:
        assert c.execute('SELECT size FROM documents WHERE path_key=?', (initial.path_key,)).fetchone()[0] == 1
        assert c.execute('SELECT valeur FROM autres_donnees').fetchone()[0] == 'concurrent'


def test_transaction_rebuild_sqlite_annule_ddl_et_ancien_index(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / 'db.sqlite')
    index.initialize()
    path = tmp_path / 'ancien.txt'
    index.upsert_document(Document(path, path.name, tmp_path, '.txt', 1, 1, False, 'ancien'))
    with pytest.raises(RuntimeError, match='panne'):
        with index.scan_transaction():
            index.initialize(rebuild=True)
            index.upsert_document(Document(path, path.name, tmp_path, '.txt', 2, 2, False, 'nouveau'))
            raise RuntimeError('panne')
    assert index.search('ancien')
    assert not index.search('nouveau')


def test_cli_scan_utilise_transaction_sans_ecraser_autre_connexion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    import seamtech_search.cli as cli

    root = tmp_path / 'root'
    root.mkdir()
    base = tmp_path / 'database.sqlite'
    index = SearchIndex(base)
    index.initialize()
    chemin = root / 'document.txt'
    ancien = Document(chemin, chemin.name, root, '.txt', 1, 1, False, 'ancien')
    index.upsert_document(ancien)
    with index.connect() as c:
        c.execute('CREATE TABLE autre_table (valeur TEXT)')
        c.execute("INSERT INTO autre_table VALUES ('avant')")
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(root)], 'database_path': str(base)}), encoding='utf-8')
    def crawl_avec_concurrent(*_args):
        yield Document(chemin, chemin.name, root, '.txt', 2, 2, False, 'scan')
        autre = sqlite3.connect(base, timeout=0.1)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                autre.execute("UPDATE autre_table SET valeur='autre'")
        finally:
            autre.close()
        raise RuntimeError('echec du scan')
    monkeypatch.setattr(cli, 'BATCH_SIZE', 1)
    monkeypatch.setattr(cli, 'crawl', crawl_avec_concurrent)
    with pytest.raises(RuntimeError, match='echec du scan'):
        run_index(str(config))
    assert index.search('ancien')
    with index.connect() as c:
        assert c.execute('SELECT size FROM documents WHERE path_key=?', (ancien.path_key,)).fetchone()[0] == 1
        assert c.execute('SELECT valeur FROM autre_table').fetchone()[0] == 'avant'
    assert index.latest_scan()['status'] == 'failed'


def test_crash_processus_annule_transaction_scan_sqlite(tmp_path: Path) -> None:
    import subprocess
    import sys

    database = tmp_path / 'database.sqlite'
    index = SearchIndex(database)
    index.initialize()
    chemin = tmp_path / 'document.txt'
    index.upsert_document(Document(chemin, chemin.name, tmp_path, '.txt', 1, 1, False, 'avant'))
    code = """
import os, sys
from pathlib import Path
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
base, chemin = map(Path, sys.argv[1:])
index = SearchIndex(base)
with index.scan_transaction():
    index.upsert_document(Document(chemin, chemin.name, chemin.parent, '.txt', 9, 9, False, 'scan'))
    os._exit(23)
"""
    proces = subprocess.run([sys.executable, '-c', code, str(database), str(chemin)], timeout=20, check=False)
    assert proces.returncode == 23
    # La clé stockée est CELLE DU PRODUIT (`normcase` + `normpath` + `resolve`),
    # pas le chemin brut : sur Windows, `normcase` met en minuscules, donc une
    # requête par `str(chemin.resolve())` ne trouve rien et l'assertion
    # suivante échoue en `TypeError: 'NoneType' object is not subscriptable`
    # (vu au run 41523086709). Interroger par la clé du produit n'ajoute aucune
    # tolérance : sur un système insensible à la casse, les deux écritures
    # désignent le même fichier, et sur POSIX `normcase` est l'identité.
    with index.connect() as c:
        assert c.execute('SELECT size FROM documents WHERE path_key=?', (cle(chemin),)).fetchone()[0] == 1


def test_interruption_clavier_scan_annule_et_indique_echec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import seamtech_search.cli as cli

    root = tmp_path / 'root'
    root.mkdir()
    base = tmp_path / 'db.sqlite'
    index = SearchIndex(base)
    index.initialize()
    chemin = root / 'document.txt'
    index.upsert_document(Document(chemin, chemin.name, root, '.txt', 1, 1, False, 'avant'))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'root_paths': [str(root)], 'database_path': str(base)}), encoding='utf-8')
    def interrompre(*_args):
        yield Document(chemin, chemin.name, root, '.txt', 2, 2, False, 'nouveau')
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, 'BATCH_SIZE', 1)
    monkeypatch.setattr(cli, 'crawl', interrompre)
    with pytest.raises(KeyboardInterrupt):
        run_index(str(config))
    with index.connect() as c:
        assert c.execute('SELECT size FROM documents WHERE path_key=?', (cle(chemin),)).fetchone()[0] == 1
    assert index.latest_scan()['status'] == 'failed'


def test_scan_snapshot_historique_ne_restaure_pas_toute_la_base(tmp_path: Path) -> None:
    import sqlite3

    index = SearchIndex(tmp_path / 'database.sqlite')
    index.initialize()
    with index.connect() as connexion:
        connexion.execute('CREATE TABLE autre_table (valeur TEXT)')
        connexion.execute("INSERT INTO autre_table VALUES ('avant')")
    with pytest.raises(RuntimeError, match='panne scan'):
        with index.scan_snapshot():
            autre = sqlite3.connect(index.database_path, timeout=0.1)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    autre.execute("UPDATE autre_table SET valeur='concurrent'")
            finally:
                autre.close()
            raise RuntimeError('panne scan')
    with index.connect() as connexion:
        connexion.execute("UPDATE autre_table SET valeur='concurrent'")
    with index.connect() as connexion:
        assert connexion.execute('SELECT valeur FROM autre_table').fetchone()[0] == 'concurrent'


def test_echec_nettoyage_instantane_ne_masque_pas_echec_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = SearchIndex(tmp_path / 'search.db')
    index.initialize()
    def nettoyage_en_panne(*_args):
        raise OSError('nettoyage en panne')
    monkeypatch.setattr(index, '_supprimer_instantane_scan', nettoyage_en_panne)
    with pytest.raises(ValueError, match='erreur originale') as capture:
        with index.scan_snapshot():
            raise ValueError('erreur originale')
    assert 'nettoyage en panne' in ' '.join(getattr(capture.value, '__notes__', []))
    assert (tmp_path / 'search.db.scan-backup').exists()


def test_ecriture_sqlite_attend_scan_puis_reussit(tmp_path: Path) -> None:
    import sqlite3
    import threading

    index = SearchIndex(tmp_path / 'database.sqlite')
    index.initialize()
    with index.connect() as c:
        c.execute('CREATE TABLE operation_concurrente (valeur TEXT)')
    entre = threading.Event()
    termine = threading.Event()
    erreurs = []
    def ecrire():
        try:
            with sqlite3.connect(index.database_path, timeout=5) as autre:
                entre.set()
                autre.execute("INSERT INTO operation_concurrente VALUES ('concurrent')")
            termine.set()
        except Exception as erreur:
            erreurs.append(erreur)
    with index.scan_transaction():
        thread = threading.Thread(target=ecrire)
        thread.start()
        assert entre.wait(2)
        assert not termine.wait(0.1)
    thread.join(timeout=5)
    assert not thread.is_alive() and not erreurs and termine.is_set()
    with index.connect() as c:
        assert c.execute('SELECT valeur FROM operation_concurrente').fetchone()[0] == 'concurrent'


@pytest.mark.parametrize("forme", ["staging", "enfant"])
def test_scan_refuse_une_racine_dans_le_staging_sans_modifier_lindex(tmp_path: Path, forme: str) -> None:
    """Une racine À L'INTÉRIEUR du staging est refusée : ce serait indexer le staging.

    Refus sans dégât : l'index existant reste intact, le fichier n'est pas
    touché, et le scan est enregistré « failed » (jamais un faux succès).
    """
    database = tmp_path / "data" / "search.db"
    staging = database.parent / "uploads"
    source = staging / "lot"
    source.mkdir(parents=True)
    piece = source / "piece.txt"
    piece.write_text("source synthétique", encoding="utf-8")
    roots = {"staging": [staging], "enfant": [source]}[forme]
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    index.upsert_document(Document(piece, piece.name, source, '.txt', 1, 1.0, False, 'index precieux'))
    before = index.search('precieux')
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"root_paths": [str(root) for root in roots], "database_path": str(database)}))
    with pytest.raises(ScanIncompleteError, match="staging"):
        run_index(str(config_path))
    assert index.search('precieux') == before
    assert index.latest_scan()["status"] == "failed"
    assert piece.read_text(encoding="utf-8") == "source synthétique"
    index.close()


@pytest.mark.parametrize("forme", ["parent", "parent_apres_autre"])
def test_scan_elague_le_staging_sous_une_racine_sans_supprimer_les_lignes(tmp_path: Path, forme: str) -> None:
    """Une racine qui CONTIENT le staging est balayée en l'élaguant.

    Exigences cumulées (elles sont la raison d'être de l'élagage) :

    1. le reste de la racine EST indexé (une exclusion ne doit pas vider le scan) ;
    2. aucun document du staging n'entre dans l'index ;
    3. les lignes du staging DÉJÀ indexées ne sont PAS supprimées — l'élagage
       n'est pas une suppression (``remove_missing`` reçoit les préfixes) ;
    4. le fichier de travail lui-même n'est pas modifié.
    """
    database = tmp_path / "data" / "search.db"
    staging = database.parent / "uploads"
    source = staging / "lot"
    source.mkdir(parents=True)
    piece = source / "piece.txt"
    piece.write_text("source synthétique", encoding="utf-8")
    # Un document ORDINAIRE sous la même racine : il doit, lui, être indexé.
    (database.parent / "archive.txt").write_text("document archive ordinaire", encoding="utf-8")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "autre.txt").write_text("autre", encoding="utf-8")
    roots = {"parent": [database.parent], "parent_apres_autre": [externe, database.parent]}[forme]
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    # Ligne « précieuse » préexistante DANS le staging : l'élagage ne doit pas
    # la faire disparaître (c'est la réserve qui rendait l'exclusion risquée).
    index.upsert_document(Document(piece, piece.name, source, '.txt', 1, 1.0, False, 'index precieux'))
    before = index.search('precieux')
    assert before, "la ligne de départ doit exister pour que le test prouve quelque chose"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"root_paths": [str(root) for root in roots], "database_path": str(database)}))

    resume = run_index(str(config_path))

    assert resume["scanned"] >= 1, "une racine qui contient le staging doit quand même être balayée"
    # Le score FTS dépend du corpus (une autre racine a pu être balayée) :
    # ce qui doit être invariant, c'est la PRÉSENCE de la ligne et son chemin.
    assert [ligne["path"] for ligne in index.search('precieux')] == [ligne["path"] for ligne in before], (
        "l'élagage du staging a supprimé une ligne déjà indexée : exclusion devenue perte de données"
    )
    assert index.latest_scan()["status"] == "completed"
    assert piece.read_text(encoding="utf-8") == "source synthétique"
    # Le dossier ordinaire de la même racine EST indexé (l'élagage est ciblé).
    assert index.search("ordinaire"), "l'élagage du staging a vidé le scan : le reste de la racine n'est plus indexé"
    # Aucun NOUVEAU document du staging : la seule ligne du staging est celle
    # qui existait déjà (ni doublon indexé, ni suppression).
    staging_key = os.path.normcase(str(piece))
    with index.connect() as connexion:
        if index.is_postgres:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT path_key FROM documents")
                cles = [str(ligne[0]) for ligne in curseur.fetchall()]
        else:
            cles = [str(ligne["path_key"]) for ligne in connexion.execute("SELECT path_key FROM documents").fetchall()]
    sous_staging = [cle for cle in cles if os.path.normcase(str(staging)) in cle]
    assert sous_staging == [staging_key], (
        f"le staging a été partiellement indexé ou purgé : {sous_staging}"
    )
    index.close()


def test_scan_elague_aussi_la_quarantine(tmp_path: Path) -> None:
    """La quarantaine est une zone de travail, exactement comme le staging."""
    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl

    base = tmp_path / "racine"
    data = base / "data"
    (data / "quarantine" / "lot").mkdir(parents=True)
    (data / "quarantine" / "lot" / "incomplet.txt").write_text("import inachevé", encoding="utf-8")
    archive = base / "archive"
    archive.mkdir()
    (archive / "bon.txt").write_text("document archive", encoding="utf-8")
    config = AppConfig(root_paths=[base], database_path=data / "index.db")
    documents = list(crawl(config))
    chemins = [str(doc.path) for doc in documents]
    assert not any("quarantine" in chemin for chemin in chemins), f"la quarantaine a été indexée : {chemins}"
    assert any(chemin.endswith("bon.txt") for chemin in chemins), f"l'archive n'a pas été indexée : {chemins}"


def test_scan_refuse_alias_staging_et_accepte_racine_independante(tmp_path: Path) -> None:
    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl

    data = tmp_path / 'data'
    source = data / 'uploads' / 'lot'
    source.mkdir(parents=True)
    (source / 'piece.txt').write_text('synthétique')
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip('liens symboliques indisponibles')
    config = AppConfig(root_paths=[alias], database_path=data / 'db.sqlite')
    with pytest.raises(ScanIncompleteError, match='staging'):
        list(crawl(config))
    archive = tmp_path / 'archive'
    archive.mkdir()
    fichier = archive / 'autre.txt'
    fichier.write_text('indépendant')
    config = AppConfig(root_paths=[archive], database_path=data / 'db.sqlite')
    assert fichier in [doc.path for doc in crawl(config)]


def test_staging_absent_puis_cree_est_toujours_exclu(tmp_path: Path) -> None:
    """Le staging peut apparaître PENDANT le parcours : l'exclusion ne dépend pas de son existence.

    L'ancienne règle refusait tout recouvrement « même si le staging n'existe
    pas encore ». L'élagage tient la même promesse sans condamner la racine :
    que le staging existe ou non au départ, ses fichiers n'entrent pas dans
    l'index — et une racine située DANS le staging reste refusée.
    """
    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl

    base = tmp_path / 'racine'
    data = base / 'data'
    data.mkdir(parents=True)
    archive = base / 'archive'
    archive.mkdir()
    (archive / 'bon.txt').write_text('document archive', encoding='utf-8')
    config = AppConfig(root_paths=[base], database_path=data / 'index.db')
    assert not (data / 'uploads').exists(), "le staging ne doit pas exister au départ"

    # 1. Staging ABSENT : le scan se déroule (l'ancienne règle le refusait).
    chemins = [str(doc.path) for doc in crawl(config)]
    assert any(chemin.endswith('bon.txt') for chemin in chemins), f"archive non indexée : {chemins}"

    # 2. Staging apparu : ses fichiers restent exclus, le reste toujours indexé.
    staging = data / 'uploads' / 'lot'
    staging.mkdir(parents=True)
    (staging / 'piece.txt').write_text('dépôt en cours', encoding='utf-8')
    chemins = [str(doc.path) for doc in crawl(config)]
    assert not any('uploads' in chemin for chemin in chemins), f"le staging a été indexé : {chemins}"
    assert any(chemin.endswith('bon.txt') for chemin in chemins), f"archive non indexée : {chemins}"

    # 3. Une racine DANS le staging reste un refus explicite.
    config_dans_staging = AppConfig(root_paths=[staging], database_path=data / 'index.db')
    with pytest.raises(ScanIncompleteError, match='staging'):
        next(crawl(config_dans_staging))


def test_cli_scan_et_rebuild_maintiennent_la_migration_identite(tmp_path: Path):
    import json

    from seamtech_search.cli import run_index
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive / 'piece.txt').write_text('contenu synthetique')
    cfg = AppConfig(root_paths=[archive], database_path=tmp_path / 'index.db', min_free_bytes=0)
    chemin_cfg = tmp_path / 'config.json'
    chemin_cfg.write_text(json.dumps(cfg.model_dump(mode='json')))
    run_index(str(chemin_cfg))
    index = SearchIndex(cfg.database_path)
    assert index.health_details()['schema_application_a_jour'] is True
    run_index(str(chemin_cfg), rebuild=True)
    assert index.health_details()['schema_application_a_jour'] is True
    index.close()
