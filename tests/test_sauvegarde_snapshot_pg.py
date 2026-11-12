"""Le dump PG et les références copiées doivent provenir du même instantané."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from seamtech_search import sauvegarde
from seamtech_search.indexer import SearchIndex
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable


def _est_pg_dump(chemin: str) -> bool:
    """Reconnaît le binaire ``pg_dump`` sur n'importe quel système.

    Sur Windows, ``shutil.which`` rend « pg_dump.EXE » : comparer le nom exact
    faisait échouer ces tests pour une raison étrangère à ce qu'ils éprouvent —
    mesuré au run 43121414811, « assert 'pg_dump.EXE' == 'pg_dump' ». Le nom
    varie en casse et en suffixe ; ``stem`` retire le suffixe, ``lower``
    normalise la casse. L'exigence reste entière : le binaire doit s'appeler
    pg_dump, et seulement pg_dump.
    """
    nom = Path(chemin).name
    # Pas ``.stem`` : il retire N'IMPORTE QUELLE extension, et « pg_dump.bak »
    # serait reconnu comme pg_dump. Seul le suffixe exécutable de Windows est
    # retiré, et encore, explicitement.
    if nom.lower().endswith(".exe"):
        nom = nom[: -len(".exe")]
    return nom.lower() == "pg_dump"

pytestmark = [pytest.mark.postgres, pytest.mark.sauvegarde]


@pytest.mark.parametrize("phase", ["avant_dump", "apres_dump"])
def test_reference_inseree_apres_snapshot_absente_du_bundle(tmp_path: Path, monkeypatch, phase: str):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("SEAMTECH_TEST_DATABASE_URL absent")
    nom, url = _creer_base_jetable()
    nom_cible, url_cible = _creer_base_jetable()
    _supprimer_base_jetable(nom_cible)
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    source = tmp_path / "source"
    source.mkdir()
    (source / "avant.txt").write_text("avant", encoding="utf-8")
    (source / "apres.txt").write_text("apres", encoding="utf-8")

    def ajouter(nom_fichier):
        fichier = source / nom_fichier
        with index.connect() as conn:
            with conn.cursor() as curseur:
                curseur.execute(
                    "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1,false)",
                    (nom_fichier, str(fichier), nom_fichier, str(source), ".txt", fichier.stat().st_size),
                )

    ajouter("avant.txt")
    original = subprocess.run
    etat_original = sauvegarde._etat_base
    ajoute = False

    def etat_et_ecriture(*args, **kwargs):
        nonlocal ajoute
        etat = etat_original(*args, **kwargs)
        if phase == "avant_dump" and not ajoute:
            ajouter("apres.txt")
            ajoute = True
        return etat

    monkeypatch.setattr(sauvegarde, "_etat_base", etat_et_ecriture)

    def dump_et_ecriture(args, **kwargs):
        nonlocal ajoute
        resultat = original(args, **kwargs)
        if phase == "apres_dump" and _est_pg_dump(str(args[0])) and resultat.returncode == 0:
            ajouter("apres.txt")
            ajoute = True
        return resultat

    monkeypatch.setattr(sauvegarde.subprocess, "run", dump_et_ecriture)
    try:
        dossier = tmp_path / "backup"
        manifeste = sauvegarde.sauver(url, [source], dossier, inclure_references_base=True)
        assert ajoute
        sauvegarde.restaurer(url_cible, manifeste, chemin_dump_local=dossier / manifeste["dump"]["fichier"])
        restaure = SearchIndex(tmp_path / "cible.db", url_cible)
        try:
            with restaure.connect() as conn:
                with conn.cursor() as curseur:
                    curseur.execute("SELECT count(*) FROM documents")
                    compte_dump = curseur.fetchone()[0]
        finally:
            restaure.close()
        assert compte_dump == 1
        assert manifeste["references"]["nb_documents"] == compte_dump
        assert manifeste["fichiers_locaux"]["nb_copies"] == compte_dump
    finally:
        index.close()
        _supprimer_base_jetable(nom)
        _supprimer_base_jetable(nom_cible)


def test_echec_dump_libere_snapshot_sans_manifeste(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("SEAMTECH_TEST_DATABASE_URL absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    connexion_reelle = sauvegarde._connecter
    connexions = []

    def connecter(*args, **kwargs):
        connexion = connexion_reelle(*args, **kwargs)
        connexions.append(connexion)
        return connexion

    def echec_dump(args, **kwargs):
        assert _est_pg_dump(str(args[0]))
        assert any(arg.startswith("--snapshot=") for arg in args)
        assert connexions[-1].closed == 0
        return SimpleNamespace(returncode=1, stderr="panne synthétique")

    monkeypatch.setattr(sauvegarde, "_connecter", connecter)
    monkeypatch.setattr(sauvegarde.subprocess, "run", echec_dump)
    try:
        dossier = tmp_path / "backup"
        with pytest.raises(sauvegarde.SauvegardeError, match="pg_dump a échoué"):
            sauvegarde.sauver(url, [tmp_path], dossier, inclure_references_base=True)
        assert connexions and all(conn.closed for conn in connexions)
        assert not list(dossier.glob("*.manifest.json"))
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_mutation_locale_apres_snapshot_ne_donne_pas_copie_incoherente_complete(tmp_path: Path, monkeypatch):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("SEAMTECH_TEST_DATABASE_URL absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    fichier = tmp_path / "piece.txt"
    fichier.write_bytes(b"avant")
    with index.connect() as conn:
        with conn.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,false)",
                ("piece", str(fichier), fichier.name, str(tmp_path), ".txt", 5),
            )
    original = subprocess.run

    def dump_et_mutation(args, **kwargs):
        resultat = original(args, **kwargs)
        if _est_pg_dump(str(args[0])) and resultat.returncode == 0:
            fichier.write_bytes(b"apres")  # même taille, contenu distinct
        return resultat

    monkeypatch.setattr(sauvegarde.subprocess, "run", dump_et_mutation)
    try:
        dossier = tmp_path / "backup"
        manifeste = sauvegarde.sauver(url, [], dossier, inclure_references_base=True)
        copie = dossier / manifeste["fichiers_locaux"]["copies"][0]["path_relatif"]
        assert manifeste["complet"] is False  # fail-closed ne corrige pas les octets
        assert copie.read_bytes() == b"avant"
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_restauration_refuse_cible_detournee_et_preserve_base_existante(base_recherche, tmp_path):
    import hashlib
    import uuid
    from urllib.parse import urlparse

    import psycopg2

    from seamtech_search import sauvegarde

    nom_nouveau = "restauration_test_" + uuid.uuid4().hex[:10]
    base = urlparse(base_recherche["url"])
    query = f"{base.query}&" if base.query else ""
    url = f"{base.scheme}://{base.netloc}/{nom_nouveau}?{query}dbname={base_recherche['nom']}"
    dump = tmp_path / "sauvegarde.dump"
    dump.write_bytes(b"octets-synthetiques")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest()}}
    with pytest.raises(sauvegarde.SauvegardeError, match="ambiguë"):
        sauvegarde.restaurer(url, manifeste, chemin_dump_local=dump)
    with base_recherche["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT COUNT(*) FROM fiche")
            assert curseur.fetchone()[0] == len(base_recherche["fiches"])
    with psycopg2.connect(base_recherche["url"]) as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_nouveau,))
            assert curseur.fetchone() is None


def test_copie_locale_tronquee_interdit_manifeste_pg(tmp_path, monkeypatch):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    fichier = tmp_path / "piece.txt"
    fichier.write_bytes(b"contenu-synthetique-complet")
    with index.connect() as conn:
        with conn.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,false)",
                ("piece", str(fichier), fichier.name, str(tmp_path), ".txt", fichier.stat().st_size),
            )
    def troncature(entree, sortie, **_kwargs):
        sortie.write(entree.read(3))
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", troncature)
    try:
        dossier = tmp_path / "backup"
        with pytest.raises(sauvegarde.SauvegardeError, match="empreinte"):
            sauvegarde.sauver(url, [], dossier, inclure_references_base=True)
        assert not list(dossier.glob("*.manifest.json"))
        assert fichier.read_bytes() == b"contenu-synthetique-complet"
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_source_substituee_interdit_manifeste_pg(tmp_path, monkeypatch):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    source = tmp_path / "piece.txt"
    source.write_bytes(b"contenu-synthetique")
    externe = tmp_path / "externe.txt"
    externe.write_bytes(b"contenu-synthetique")
    with index.connect() as conn:
        with conn.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,false)",
                ("piece", str(source), source.name, str(tmp_path), ".txt", source.stat().st_size),
            )
    ecrire = sauvegarde._ecrire_copie_confinee
    def substituer(flux, cible, empreinte):
        source.rename(tmp_path / "ancien.txt")
        source.symlink_to(externe)
        return ecrire(flux, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", substituer)
    try:
        dossier = tmp_path / "backup"
        with pytest.raises(sauvegarde.SauvegardeError):
            sauvegarde.sauver(url, [], dossier, inclure_references_base=True)
        assert not list(dossier.glob("*.manifest.json"))
        assert externe.read_bytes() == b"contenu-synthetique"
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_sauvegarde_refuse_declencheur_identite_desactive(base_recherche, tmp_path: Path):
    index = base_recherche['index']
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('ALTER TABLE documents DISABLE TRIGGER documents_invalider_source_sha256')
    dossier = tmp_path / 'backup'
    with pytest.raises(sauvegarde.SauvegardeError, match='identité|déclencheur'):
        sauvegarde.sauver(base_recherche['url'], [], dossier)
    assert not list(dossier.glob('*.dump')) and not list(dossier.glob('*.manifest.json'))


def test_sauvegarde_ne_copie_pas_source_divergente_de_capture_pg(tmp_path: Path):
    """Une identité d'octets dans le dump doit borner la copie locale.

    Cas DÉTERMINISTE : la source diverge déjà AU MOMENT de la copie (ses
    octets ne sont plus l'empreinte que porte le catalogue). Elle ne doit pas
    entrer dans le bundle comme si elle était le document connu.
    """
    import hashlib

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    fichier = tmp_path / "piece.txt"
    fichier.write_bytes(b"avant")
    sha_initial = hashlib.sha256(fichier.read_bytes()).hexdigest()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir,source_sha256) "
            "VALUES (%s,%s,%s,%s,%s,5,1,false,%s)",
            ("piece", str(fichier), fichier.name, str(tmp_path), ".txt", sha_initial),
        )
    fichier.write_bytes(b"apres")  # même taille, autres octets
    try:
        dossier = tmp_path / "bundle"
        manifeste = sauvegarde.sauver(url, [], dossier, inclure_references_base=True)
        assert not manifeste['complet'] and not manifeste['complet_perimetre_demande']
        assert manifeste['fichiers_locaux']['nb_copies'] == 0
        assert manifeste['fichiers_locaux']['nb_manquants'] == 1
        assert manifeste['fichiers_locaux']['manquants'][0]['erreur'] == 'identite_catalogue_differente'
        assert fichier.read_bytes() == b"apres"
        assert not list((dossier / 'bundles').rglob('piece.txt'))
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_copie_fidele_au_snapshot_malgre_mutation_pendant_le_dump(tmp_path: Path, monkeypatch):
    """Mutation PENDANT la fenêtre : la copie reste celle du snapshot, et c'est DIT.

    Les copies sont prises dans la transaction du snapshot, donc AVANT le
    dump : le bundle porte les octets que le catalogue connaît (b"avant"),
    et la source modifiée ensuite est signalée (``derive_octets_locaux``)
    au lieu d'entrer silencieusement dans une sauvegarde incohérente.
    """
    import hashlib

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    fichier = tmp_path / "piece.txt"
    fichier.write_bytes(b"avant")
    sha_initial = hashlib.sha256(fichier.read_bytes()).hexdigest()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir,source_sha256) "
            "VALUES (%s,%s,%s,%s,%s,5,1,false,%s)",
            ("piece", str(fichier), fichier.name, str(tmp_path), ".txt", sha_initial),
        )
    original = subprocess.run

    def dump_et_mutation(args, **kwargs):
        resultat = original(args, **kwargs)
        if _est_pg_dump(str(args[0])) and resultat.returncode == 0:
            fichier.write_bytes(b"apres")  # même taille, autres octets
        return resultat

    monkeypatch.setattr(sauvegarde.subprocess, "run", dump_et_mutation)
    try:
        dossier = tmp_path / "bundle"
        manifeste = sauvegarde.sauver(url, [], dossier, inclure_references_base=True)
        copie = dossier / manifeste["fichiers_locaux"]["copies"][0]["path_relatif"]
        # Les octets du bundle sont ceux du snapshot, pas ceux d'après.
        assert copie.read_bytes() == b"avant"
        assert manifeste["fichiers_locaux"]["copies"][0]["sha256"] == sha_initial
        # …et la dérive est NOMMÉE : jamais absorbée en silence.
        assert manifeste["derive_octets_locaux"]["detectee"] is True
        assert manifeste["derive_octets_locaux"]["nb_fichiers"] == 1
        assert manifeste["derive_octets_locaux"]["fichiers"][0]["path"] == str(fichier)
        assert not manifeste["complet"] and not manifeste["complet_perimetre_demande"]
    finally:
        index.close()
        _supprimer_base_jetable(nom)


@pytest.mark.parametrize('objet_correspond', [False, True])
def test_sha_snapshot_propage_aux_copies_locale_et_objet_pg(tmp_path: Path, objet_correspond: bool):
    """Les deux copies comparent aux identités du même snapshot que pg_dump."""
    import base64
    import hashlib

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    fichier = tmp_path / 'local.txt'
    local = b'contenu-local'
    initial = b'contenu-objet'
    actuel = initial if objet_correspond else b'autre--objet'
    fichier.write_bytes(local)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir,source_sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s,1,false,%s)",
            ('local', str(fichier), fichier.name, str(tmp_path), '.txt', len(local), hashlib.sha256(local).hexdigest()),
        )
        curseur.execute(
            "INSERT INTO documents(path_key,path,name,parent_path,extension,size,modified_at,is_dir,"
            "source_sha256,object_key,object_bucket) VALUES (%s,%s,%s,%s,%s,%s,1,false,%s,%s,%s)",
            ('objet', 's3://bucket-synthetique/piece', 'piece', '', '', len(initial),
             hashlib.sha256(initial).hexdigest(), 'piece', 'bucket-synthetique'),
        )

    class Client:
        bucket_name = 'bucket-synthetique'

        def head_object(self, _cle):
            return {'ContentLength': len(actuel), 'ETag': 'opaque', 'ChecksumType': 'FULL_OBJECT',
                    'ChecksumSHA256': base64.b64encode(hashlib.sha256(actuel).digest()).decode()}

        def download_file(self, _cle, destination):
            destination.write_bytes(actuel)

    try:
        dossier = tmp_path / 'bundle'
        manifeste = sauvegarde.sauver(url, [], dossier, inclure_references_base=True, client_documents_s3=Client())
        assert manifeste['complet'] is False
        assert manifeste['complet_perimetre_demande'] is objet_correspond
        copie_locale = manifeste['fichiers_locaux']['copies'][0]
        assert copie_locale['identite_catalogue'] == 'verifiee'
        assert (dossier / copie_locale['path_relatif']).read_bytes() == local
        objets = manifeste['objets_s3']
        assert objets['nb_copies'] == int(objet_correspond)
        assert objets['nb_manquants'] == int(not objet_correspond)
        if objet_correspond:
            copie_objet = objets['copies'][0]
            assert copie_objet['identite_catalogue'] == 'verifiee'
            assert (dossier / copie_objet['path_relatif']).read_bytes() == initial
        else:
            assert objets['manquants'][0]['erreur'] == 'identite_catalogue_differente'
        assert fichier.read_bytes() == local
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_est_pg_dump_reconnait_le_binaire_de_chaque_plateforme():
    """Le comparateur tient sur Windows, où le binaire s'appelle « pg_dump.EXE ».

    Sans lui, quatre épreuves de ce fichier échouaient sur Windows pour une
    raison étrangère à ce qu'elles éprouvent (run 43121414811 : « assert
    'pg_dump.EXE' == 'pg_dump' »). Il ne s'agit pas d'assouplir l'exigence :
    un autre binaire doit toujours être écarté.
    """
    from tests.test_sauvegarde_snapshot_pg import _est_pg_dump

    # Pas de chemin Windows absolu dans cette liste : sur POSIX la barre
    # oblique inverse n'est PAS un séparateur, et le cas serait faux ici tout
    # en étant juste là-bas. Les variantes qui comptent sont la casse et le
    # suffixe — c'est exactement ce qui cassait.
    for nom in ("pg_dump", "pg_dump.EXE", "pg_dump.exe", "/usr/lib/postgresql/16/bin/pg_dump", "bin/pg_dump.EXE"):
        assert _est_pg_dump(nom), f"{nom} doit être reconnu comme pg_dump"
    for nom in ("pg_restore", "pg_dumpall", "pg_restore.EXE", "mon_pg_dump", "", "pg_dump.bak"):
        assert not _est_pg_dump(nom), f"{nom} ne doit PAS être reconnu comme pg_dump"
