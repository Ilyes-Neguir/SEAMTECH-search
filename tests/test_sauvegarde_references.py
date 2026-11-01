"""Sauvegarde des pièces référencées (locaux + objets) — primitives sans promesse « zéro perte »."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from seamtech_search.sauvegarde import (
    SauvegardeError,
    VerrouSauvegarde,
    copier_fichiers_locaux,
    rediger_config,
    restaurer_fichiers_locaux,
    sauver,
)
from tests.helpers_postgres_isole import url_base_jetable


def _pointer_pg_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    """pg_dump du PATH / SEAMTECH_PG_BINDIR / pgserver — sans importer pgserver si absent."""
    if os.environ.get("SEAMTECH_PG_BINDIR"):
        return
    exe = shutil.which("pg_dump")
    if exe:
        monkeypatch.setenv("SEAMTECH_PG_BINDIR", str(Path(exe).parent))
        return
    try:
        import pgserver
    except ImportError:
        return
    bindir = Path(pgserver.__file__).parent / "pginstall" / "bin"
    if bindir.is_dir():
        monkeypatch.setenv("SEAMTECH_PG_BINDIR", str(bindir))


def test_rediger_config_masque_les_secrets() -> None:
    brut = {
        "SEAMTECH_S3_SECRET_KEY": "super-secret",
        "POSTGRES_PASSWORD": "pwd",
        "SEAMTECH_S3_BUCKET": "seamtech-documents",
        "root_paths": ["/data/archive"],
    }
    redige = rediger_config(brut)
    assert redige["SEAMTECH_S3_SECRET_KEY"] == "***"
    assert redige["POSTGRES_PASSWORD"] == "***"
    assert redige["SEAMTECH_S3_BUCKET"] == "seamtech-documents"
    assert redige["root_paths"] == ["/data/archive"]


def test_copier_document_objet_sans_fichier_local_n_est_pas_manquant(tmp_path: Path) -> None:
    """Un objet S3 sans copie locale n'est pas un manquant local (contrat import)."""
    present = tmp_path / "present.txt"
    present.write_text("hello", encoding="utf-8")
    refs = {
        "documents": [
            {"id": 1, "path": str(present), "name": "present.txt", "object_key": ""},
            {
                "id": 2,
                "path": "s3://seamtech-documents/qualif/objet.bin",
                "name": "objet.bin",
                "object_key": "qualif/objet.bin",
            },
        ]
    }
    dest = tmp_path / "bundle"
    dest.mkdir()
    resultat = copier_fichiers_locaux(refs, dest)
    assert resultat["nb_copies"] == 1
    assert resultat["nb_manquants"] == 0
    assert resultat["manquants"] == []


def test_copier_signale_fichier_manquant_sans_l_ignorer(tmp_path: Path) -> None:
    present = tmp_path / "present.txt"
    present.write_text("hello", encoding="utf-8")
    refs = {
        "documents": [
            {"id": 1, "path": str(present), "name": "present.txt"},
            {"id": 2, "path": str(tmp_path / "absent.txt"), "name": "absent.txt"},
        ]
    }
    dest = tmp_path / "bundle"
    dest.mkdir()
    resultat = copier_fichiers_locaux(refs, dest)
    assert resultat["nb_copies"] == 1
    assert resultat["nb_manquants"] == 1
    assert resultat["manquants"][0]["id"] == 2
    assert (dest / "fichiers" / "1" / "present.txt").is_file()


def test_restaurer_fichiers_refuse_empreinte_invalide(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "fichiers" / "1").mkdir(parents=True)
    cible = bundle / "fichiers" / "1" / "a.txt"
    cible.write_text("abc", encoding="utf-8")
    manifeste = {
        "fichiers_locaux": {
            "copies": [
                {
                    "id": 1,
                    "path_relatif": "fichiers/1/a.txt",
                    "sha256": "0" * 64,
                }
            ]
        }
    }
    with pytest.raises(SauvegardeError, match="empreinte"):
        restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")


def test_restaurer_fichiers_ok_compare_empreinte(tmp_path: Path) -> None:
    from seamtech_search.sauvegarde import _sha256_fichier

    bundle = tmp_path / "bundle"
    (bundle / "fichiers" / "7").mkdir(parents=True)
    source = bundle / "fichiers" / "7" / "piece.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    manifeste = {
        "fichiers_locaux": {
            "copies": [
                {
                    "id": 7,
                    "path_relatif": "fichiers/7/piece.pdf",
                    "sha256": _sha256_fichier(source),
                }
            ]
        }
    }
    dest = tmp_path / "restaure"
    resume = restaurer_fichiers_locaux(manifeste, bundle, dest)
    assert resume["nb"] == 1
    restaure = Path(resume["fichiers"][0]["path"])
    assert restaure.is_file()
    assert _sha256_fichier(restaure) == manifeste["fichiers_locaux"]["copies"][0]["sha256"]


def test_verrou_refuse_chevauchement(tmp_path: Path) -> None:
    dossier = tmp_path / "backups"
    with VerrouSauvegarde(dossier):
        with pytest.raises(SauvegardeError, match="chevauchement"):
            with VerrouSauvegarde(dossier):
                pass


def test_destination_inaccessible_est_explicite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fichier = tmp_path / "pas-un-dossier"
    fichier.write_text("x", encoding="utf-8")
    with pytest.raises(SauvegardeError, match="destination inaccessible"):
        sauver("postgresql://x", [tmp_path], fichier, verrouiller=False)


@pytest.mark.postgres
@pytest.mark.sauvegarde
def test_sauvegarde_references_fichier_manquant_n_est_pas_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fichier local référencé absent interdit le statut complet (pas un PASS silencieux)."""
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")

    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    _pointer_pg_dump(monkeypatch)

    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "unused.db", url)
    index.initialize()
    index.run_migrations()
    present = tmp_path / "present.pdf"
    present.write_bytes(b"%PDF-1.4 present")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1, false)",
                ("k-present", str(present), "present.pdf", str(tmp_path), ".pdf", present.stat().st_size),
            )
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1, false)",
                ("k-manquant", str(tmp_path / "manquant.pdf"), "manquant.pdf", str(tmp_path), ".pdf", 10),
            )
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "temoin.txt").write_text("ok", encoding="utf-8")
    backups = tmp_path / "backups"
    try:
        manifeste = sauver(
            url,
            [archive],
            backups,
            client_s3=None,
            inclure_references_base=True,
            verrouiller=True,
        )
        assert manifeste["complet"] is False
        assert manifeste["complet_perimetre_demande"] is False
        assert manifeste["fichiers_locaux"]["nb_manquants"] == 1
        assert manifeste["fichiers_locaux"]["nb_copies"] == 1
    finally:
        index.close()
        _supprimer_base_jetable(nom)


@pytest.mark.postgres
@pytest.mark.sauvegarde
def test_aller_retour_fichiers_vers_cible_distincte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dump + copies locales → DROP sources → restore base NEUVE + fichiers DISTINCTS.

    Preuve : les originaux ne sont plus lisibles ; le bundle suffit ; les
    empreintes correspondent ; la recherche fonctionne sur la base restaurée.
    """
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.recherche import rechercher_fiches
    from seamtech_search.sauvegarde import restaurer, restaurer_fichiers_locaux, verifier
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    _pointer_pg_dump(monkeypatch)

    nom, url = _creer_base_jetable()
    nom_cible = f"{nom}_restauree"
    url_cible = url_base_jetable(url, nom_cible)
    index = SearchIndex(tmp_path / "unused.db", url)
    index.initialize()
    index.run_migrations()

    piece = tmp_path / "origine" / "plan-atelier.pdf"
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"%PDF-1.4 SEAMTECH-PLAN-ATELIER\n")
    from seamtech_search.sauvegarde import _sha256_fichier

    empreinte_origine = _sha256_fichier(piece)

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO type_voile (code, libelle, famille) VALUES ('GEN', 'Génois', 'voile_avant')"
            )
            cursor.execute("INSERT INTO client (nom, chantier) VALUES ('Voilerie Atlantique', 'Brest')")
            cursor.execute("INSERT INTO bateau (nom, taille) VALUES ('First 30', '30 pieds')")
            cursor.execute("SELECT id_client FROM client")
            id_client = int(cursor.fetchone()[0])
            cursor.execute("SELECT id_bateau FROM bateau")
            id_bateau = int(cursor.fetchone()[0])
            cursor.execute(
                "INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, statut) "
                "VALUES ('7792-SO', 'Génois lourd First 30', 1, %s, %s, 'valide') RETURNING id_fiche",
                (id_client, id_bateau),
            )
            id_fiche = int(cursor.fetchone()[0])
            cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir) "
                "VALUES (%s, %s, %s, %s, '.pdf', %s, 1, false)",
                ("k-plan", str(piece), piece.name, str(piece.parent), piece.stat().st_size),
            )

    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "temoin.txt").write_text("temoin-archive", encoding="utf-8")
    backups = tmp_path / "backups"
    try:
        manifeste = sauver(
            url,
            [archive],
            backups,
            client_s3=None,
            inclure_references_base=True,
            verrouiller=True,
        )
        assert manifeste["complet"] is False
        assert manifeste["complet_perimetre_demande"] is True
        assert manifeste["fichiers_locaux"]["nb_copies"] == 1
        assert manifeste["fichiers_locaux"]["copies"][0]["sha256"] == empreinte_origine

        # Les originaux deviennent inaccessibles (pas de lecture source pendant restore).
        piece.unlink()
        assert not piece.exists()

        resume = restaurer(url_cible, manifeste, chemin_dump_local=backups / manifeste["dump"]["fichier"])
        assert resume["base"] == nom_cible
        cible_fichiers = tmp_path / "restaure-fichiers"
        extra = restaurer_fichiers_locaux(manifeste, backups, cible_fichiers)
        assert extra["nb"] == 1
        restaure = Path(extra["fichiers"][0]["path"])
        assert restaure.is_file()
        assert _sha256_fichier(restaure) == empreinte_origine
        assert b"SEAMTECH-PLAN-ATELIER" in restaure.read_bytes()

        resultat = verifier(url_cible, manifeste, [archive])
        assert resultat["ok"], resultat.get("ecarts")

        index_cible = SearchIndex(tmp_path / "unused-cible.db", url_cible)
        try:
            reponse = rechercher_fiches(index_cible, requete="GENOIS")
            codes = [r.get("code") for r in reponse.get("resultats") or []]
            assert "7792-SO" in codes
        finally:
            index_cible.close()
    finally:
        index.close()
        _supprimer_base_jetable(nom)
        _supprimer_base_jetable(nom_cible)


def test_manifeste_corrompu_est_refuse(tmp_path: Path) -> None:
    from seamtech_search.sauvegarde import charger_manifeste

    mauvais = tmp_path / "m.json"
    mauvais.write_text("{pas json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        charger_manifeste(mauvais, None, None)


@pytest.mark.postgres
@pytest.mark.sauvegarde
@pytest.mark.s3
def test_aller_retour_objets_minio_sources_indisponibles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostgreSQL + fichier local + objet MinIO officiel ; sources coupées pendant restore.

    Distinct : nouvelle base, nouvel arbre fichiers, objet recréé après
    suppression de la clé source. Pas s3rver/moto.
    """
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    if not os.environ.get("SEAMTECH_S3_ENDPOINT_URL"):
        pytest.skip("SEAMTECH_S3_ENDPOINT_URL absent — MinIO officiel requis")

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.recherche import rechercher_fiches
    from seamtech_search.sauvegarde import (
        _sha256_fichier,
        restaurer,
        restaurer_fichiers_locaux,
        restaurer_objets_s3,
    )
    from seamtech_search.storage import S3StorageClient
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    _pointer_pg_dump(monkeypatch)
    client = S3StorageClient(
        endpoint_url=os.environ["SEAMTECH_S3_ENDPOINT_URL"],
        bucket_name=os.environ.get("SEAMTECH_S3_BUCKET") or "seamtech-documents",
        access_key_id=os.environ.get("SEAMTECH_S3_ACCESS_KEY"),
        secret_access_key=os.environ.get("SEAMTECH_S3_SECRET_KEY"),
        force_path_style=True,
    )
    client.ensure_bucket_exists()

    nom, url = _creer_base_jetable()
    nom_cible = f"{nom}_obj"
    url_cible = url_base_jetable(url, nom_cible)
    index = SearchIndex(tmp_path / "unused.db", url)
    index.initialize()
    index.run_migrations()

    local = tmp_path / "local" / "piece-locale.pdf"
    local.parent.mkdir()
    local.write_bytes(b"%PDF-1.4 LOCAL-QUALIF")
    objet = tmp_path / "objet.bin"
    objet.write_bytes(b"OBJET-MINIO-QUALIF")
    sha_local = _sha256_fichier(local)
    sha_objet = _sha256_fichier(objet)
    cle = f"qualif-docs/{nom}/objet.bin"
    client.upload_file(objet, cle, avoid_overwrite=False)

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("INSERT INTO type_voile (code, libelle, famille) VALUES ('GEN', 'Génois', 'voile_avant')")
            cursor.execute(
                "INSERT INTO fiche (code, titre, id_type_voile, statut) "
                "VALUES ('Q-OBJ', 'Génois objet MinIO', 1, 'valide') RETURNING id_fiche"
            )
            ident = int(cursor.fetchone()[0])
            cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ident,))
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, object_key, object_bucket) "
                "VALUES (%s, %s, %s, %s, '.pdf', %s, 1, false, NULL, NULL)",
                ("k-loc", str(local), local.name, str(local.parent), local.stat().st_size),
            )
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, object_key, object_bucket) "
                "VALUES (%s, %s, %s, %s, '.bin', %s, 1, false, %s, %s)",
                (
                    "k-obj",
                    f"s3://{client.bucket_name}/{cle}",
                    "objet.bin",
                    "s3",
                    objet.stat().st_size,
                    cle,
                    client.bucket_name,
                ),
            )

    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "temoin.txt").write_text("ok", encoding="utf-8")
    backups = tmp_path / "backups"
    try:
        manifeste = sauver(
            url,
            [archive],
            backups,
            client_s3=client,
            inclure_references_base=True,
            client_documents_s3=client,
            verrouiller=True,
        )
        assert manifeste["fichiers_locaux"]["nb_copies"] >= 1
        assert manifeste["fichiers_locaux"]["nb_manquants"] == 0
        assert manifeste["objets_s3"]["nb_copies"] >= 1
        assert manifeste["objets_s3"]["nb_manquants"] == 0
        assert manifeste["complet"] is False
        assert manifeste["complet_perimetre_demande"] is True

        local.unlink()
        assert client.delete_file(cle)
        assert not local.exists()

        restaurer(url_cible, manifeste, chemin_dump_local=backups / manifeste["dump"]["fichier"])
        extra_f = restaurer_fichiers_locaux(manifeste, backups, tmp_path / "restaure-f")
        extra_o = restaurer_objets_s3(manifeste, backups, client)
        assert extra_f["nb"] >= 1
        assert extra_o["nb"] >= 1
        restaure_local = Path(extra_f["fichiers"][0]["path"])
        assert restaure_local != local
        assert _sha256_fichier(restaure_local) == sha_local
        # Objet restauré : retélécharger et comparer.
        dest = tmp_path / "objet-relu.bin"
        client.download_file(cle, dest)
        assert _sha256_fichier(dest) == sha_objet

        index_c = SearchIndex(tmp_path / "cible.db", url_cible)
        try:
            assert "Q-OBJ" in [
                r.get("code") for r in rechercher_fiches(index_c, requete="GENOIS").get("resultats") or []
            ]
        finally:
            index_c.close()
        client.delete_file(cle)
    finally:
        index.close()
        _supprimer_base_jetable(nom)
        _supprimer_base_jetable(nom_cible)


def test_objet_d_un_autre_bucket_ne_peut_pas_etre_sauvegarde_comme_le_bon(tmp_path: Path) -> None:
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        bucket_name = 'bucket-cible'
        appels = 0

        def download_file(self, cle: str, cible: Path) -> None:
            self.appels += 1
            cible.write_bytes(b'objet mais mauvais bucket')

    client = Client()
    refs = {'documents': [{'id': 1, 'object_key': 'cle-commune', 'object_bucket': 'autre-bucket'}]}
    resultat = copier_objets_s3(refs, client, tmp_path)
    assert client.appels == 0
    assert resultat['nb_copies'] == 0
    assert resultat['nb_manquants'] == 1


def test_copie_objet_refuse_lien_sortant_du_bundle(tmp_path: Path) -> None:
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def download_file(self, cle: str, cible: Path) -> None:
            cible.write_bytes(b'synthetique')

    bundle = tmp_path / 'bundle'
    (bundle / 'objets').mkdir(parents=True)
    externe = tmp_path / 'externe'
    externe.mkdir()
    (bundle / 'objets' / '1').symlink_to(externe, target_is_directory=True)
    refs = {'documents': [{'id': 1, 'object_key': 'cle'}]}
    with pytest.raises(SauvegardeError):
        copier_objets_s3(refs, Client(), bundle)
    assert not list(externe.iterdir())


def test_references_sans_source_signalees_mais_repertoire_ignore(tmp_path: Path) -> None:
    refs = {'documents': [
        {'id': 1, 'path': '', 'object_key': '', 'is_dir': False},
        {'id': 2, 'path': 's3://bucket/sans-cle', 'object_key': '', 'is_dir': False},
        {'id': 3, 'path': str(tmp_path), 'object_key': '', 'is_dir': True},
    ]}
    resultat = copier_fichiers_locaux(refs, tmp_path / 'bundle')
    assert resultat['nb_copies'] == 0
    assert {m['id'] for m in resultat['manquants']} == {1, 2}


def test_copie_de_sauvegarde_tronquee_ne_peut_pas_etre_dite_valide(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"contenu-synthetique-complet")
    bundle = tmp_path / "bundle"
    refs = {"documents": [{"id": 1, "path": str(source), "name": "source"}]}
    def copier2_tronque(_source, cible, **_kwargs):
        Path(cible).write_bytes(b"contenu")
        return cible
    def copier_flux_tronque(entree, sortie, **_kwargs):
        sortie.write(entree.read(7))
    monkeypatch.setattr(sauvegarde.shutil, "copy2", copier2_tronque)
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", copier_flux_tronque)
    with pytest.raises(SauvegardeError, match="empreinte"):
        copier_fichiers_locaux(refs, bundle)
    assert not (bundle / "fichiers" / "1" / "source").exists()


def test_copie_de_sauvegarde_ne_remplace_pas_un_fichier_existant(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"nouvelle-copie")
    bundle = tmp_path / "bundle"
    cible = bundle / "fichiers" / "1" / "source"
    cible.parent.mkdir(parents=True)
    cible.write_bytes(b"temoin-preexistant")
    refs = {"documents": [{"id": 1, "path": str(source), "name": "source"}]}
    with pytest.raises(SauvegardeError):
        copier_fichiers_locaux(refs, bundle)
    assert cible.read_bytes() == b"temoin-preexistant"


def test_copie_de_sauvegarde_refuse_source_modifiee_apres_copie(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"contenu-original")
    bundle = tmp_path / "bundle"
    refs = {"documents": [{"id": 1, "path": str(source), "name": "source"}]}
    copier = sauvegarde.shutil.copyfileobj
    def copier_et_muter(entree, sortie, **kwargs):
        copier(entree, sortie, **kwargs)
        source.write_bytes(b"contenu-modifie!")
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", copier_et_muter)
    with pytest.raises(SauvegardeError, match="source modifiée"):
        copier_fichiers_locaux(refs, bundle)
    assert source.read_bytes() == b"contenu-modifie!"


@pytest.mark.parametrize("lien_parent", [False, True])
def test_sauvegarde_refuse_source_via_lien_symbolique(tmp_path, lien_parent):
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "piece").write_bytes(b"exterieur-synthetique")
    if lien_parent:
        lien = tmp_path / "lien"
        lien.symlink_to(externe, target_is_directory=True)
        source = lien / "piece"
    else:
        source = tmp_path / "lien"
        source.symlink_to(externe / "piece")
    refs = {"documents": [{"id": 1, "path": str(source), "name": "piece"}]}
    bundle = tmp_path / "bundle"
    with pytest.raises(SauvegardeError):
        copier_fichiers_locaux(refs, bundle)
    assert not (bundle / "fichiers" / "1" / "piece").exists()
    assert (externe / "piece").read_bytes() == b"exterieur-synthetique"


@pytest.mark.parametrize("substitution", ["lien", "fichier"])
def test_sauvegarde_detecte_source_substituee_apres_ouverture(tmp_path, monkeypatch, substitution):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"octets-communs")
    externe = tmp_path / "externe"
    externe.write_bytes(b"octets-communs")
    refs = {"documents": [{"id": 1, "path": str(source), "name": "source"}]}
    bundle = tmp_path / "bundle"
    ecrire = sauvegarde._ecrire_copie_confinee
    def remplacer(flux, cible, empreinte):
        source.rename(tmp_path / "ancien")
        if substitution == "lien":
            source.symlink_to(externe)
        else:
            source.write_bytes(b"octets-communs")
        return ecrire(flux, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", remplacer)
    with pytest.raises(SauvegardeError):
        copier_fichiers_locaux(refs, bundle)
    assert externe.read_bytes() == b"octets-communs"
    copie = bundle / "fichiers" / "1" / "source"
    if copie.exists():
        assert copie.read_bytes() == b"octets-communs"


def test_source_contenant_parent_et_lien_ne_change_pas_silencieusement_de_cible(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    externe = tmp_path / "externe"
    (externe / "dossier").mkdir(parents=True)
    (base / "piece").write_bytes(b"mauvaise-cible")
    (externe / "piece").write_bytes(b"source-attendue")
    (base / "lien").symlink_to(externe / "dossier", target_is_directory=True)
    source = base / "lien" / ".." / "piece"
    assert source.read_bytes() == b"source-attendue"
    refs = {"documents": [{"id": 1, "path": str(source), "name": "piece"}]}
    with pytest.raises(SauvegardeError):
        copier_fichiers_locaux(refs, tmp_path / "bundle")
    assert not (tmp_path / "bundle" / "fichiers" / "1" / "piece").exists()


def test_telechargement_objet_ne_remplace_pas_copie_existante(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    cible = tmp_path / "bundle" / "objets" / "1" / "piece"
    cible.parent.mkdir(parents=True)
    cible.write_bytes(b"temoin")
    class Client:
        def head_object(self, _cle):
            from tests.test_sauvegarde_objet_checksum import entete
            return entete(b"autre")
        def download_file(self, _cle, destination):
            destination.write_bytes(b"autre")
    with pytest.raises(SauvegardeError):
        copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert cible.read_bytes() == b"temoin"


def test_telechargement_objet_refuse_parent_substitue(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    bundle = tmp_path / "bundle"
    externe = tmp_path / "externe"
    externe.mkdir()
    temoin = externe / "piece"
    temoin.write_bytes(b"temoin")
    dossier_cible = bundle / "objets" / "1"
    class Client:
        def head_object(self, _cle):
            from tests.test_sauvegarde_objet_checksum import entete
            return entete(b"substitution")
        def download_file(self, _cle, destination):
            if dossier_cible.exists():
                dossier_cible.rename(bundle / "objets" / "ancien")
            dossier_cible.parent.mkdir(parents=True, exist_ok=True)
            dossier_cible.symlink_to(externe, target_is_directory=True)
            destination.write_bytes(b"substitution")
    with pytest.raises(SauvegardeError):
        copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), bundle)
    assert temoin.read_bytes() == b"temoin"


def test_telechargement_objet_echec_ne_laisse_pas_de_copie_partielle(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 7, "ETag": '"identite"'}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"partiel")
            raise OSError("erreur synthetique")
    bundle = tmp_path / "bundle"
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), bundle)
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0
    assert not (bundle / "objets" / "1" / "piece").exists()


def test_telechargement_objet_chec_temporaire_ne_divulgue_pas_le_chemin(tmp_path, monkeypatch):
    from seamtech_search.sauvegarde import copier_objets_s3

    dossier = tmp_path / "chemin-confidentiel"
    dossier.write_bytes(b"pas-un-dossier")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(dossier))
    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 1, "ETag": '"identite"'}
        def download_file(self, _cle, _destination):
            pytest.fail("aucun appel sans temporaire disponible")
    with pytest.raises(SauvegardeError) as erreur:
        copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert "chemin-confidentiel" not in str(erreur.value)


def test_telechargement_objet_interruption_nettoie_staging(tmp_path, monkeypatch):
    from seamtech_search.sauvegarde import copier_objets_s3

    travail = tmp_path / "travail"
    travail.mkdir()
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(travail))
    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 7, "ETag": '"identite"'}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"partiel")
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert not list(travail.iterdir())
    assert not (tmp_path / "bundle" / "objets" / "1" / "piece").exists()


def test_copie_objet_refuse_telechargement_tronque_silencieux(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 12, "ETag": '"identite"'}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"court")
    bundle = tmp_path / "bundle"
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), bundle)
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0
    assert not (bundle / "objets" / "1" / "piece").exists()


def test_copie_objet_refuse_changement_identite_pendant_transfert(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        appels = 0
        def head_object(self, _cle):
            self.appels += 1
            return {"ContentLength": 5, "ETag": f'"version-{self.appels}"'}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"objet")
    client = Client()
    bundle = tmp_path / "bundle"
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, client, bundle)
    assert client.appels == 2
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0
    assert not (bundle / "objets" / "1" / "piece").exists()


def test_copie_objet_refuse_source_sans_metadonnees_verifiables(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def download_file(self, _cle, _destination):
            pytest.fail("pas de téléchargement sans métadonnées de source")
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0


def test_copie_objet_refuse_version_changee_meme_etag(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        appels = 0
        def head_object(self, _cle):
            self.appels += 1
            return {"ContentLength": 5, "ETag": '"identique"', "VersionId": f"v{self.appels}"}
        def download_file(self, _cle, destination, *, version_id):
            assert version_id == "v1"
            destination.write_bytes(b"objet")
    bundle = tmp_path / "bundle"
    client = Client()
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, client, bundle)
    assert client.appels == 2
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0
    assert not (bundle / "objets" / "1" / "piece").exists()


def test_copie_objet_refuse_corruption_meme_taille_et_etag(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 5, "ETag": '"identique"'}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"autre")  # source distante synthétique : b'vraie'
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0


@pytest.mark.parametrize("entete", [
    {}, {"ContentLength": True, "ETag": "e"}, {"ContentLength": -1, "ETag": "e"},
    {"ContentLength": 0, "ETag": ""}, {"ContentLength": 0, "ETag": "e", "VersionId": 5},
])
def test_copie_objet_refuse_entete_inexploitable(tmp_path, entete):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            return entete
        def download_file(self, _cle, _destination):
            pytest.fail("téléchargement interdit avec métadonnées invalides")
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path / "bundle")
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0


def test_copie_objet_refuse_echec_de_seconde_verification(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        appels = 0
        def head_object(self, _cle):
            self.appels += 1
            if self.appels == 2:
                raise OSError("secret-synthetique")
            return {"ContentLength": 5, "ETag": "identite"}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"objet")
    bundle = tmp_path / "bundle"
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), bundle)
    assert resultat["nb_manquants"] == 1 and resultat["nb_copies"] == 0
    assert "secret-synthetique" not in str(resultat)
    assert not (bundle / "objets" / "1" / "piece").exists()


def test_copie_objet_epingle_version_durant_aba(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            from tests.test_sauvegarde_objet_checksum import entete
            return {**entete(b"original"), "VersionId": "version-originale"}
        def download_file(self, _cle, destination, *, version_id=None):
            # La version courante a changé pendant le GET avant un retour ABA.
            destination.write_bytes(b"original" if version_id == "version-originale" else b"intrus!!")
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path)
    assert resultat["nb_copies"] == 1
    assert (tmp_path / resultat["copies"][0]["path_relatif"]).read_bytes() == b"original"


def test_copie_objet_refuse_repli_si_client_sans_version(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            return {"ContentLength": 8, "ETag": '"stable"', "VersionId": "version-originale"}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"intrus!!")
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path)
    assert resultat["nb_copies"] == 0 and resultat["nb_manquants"] == 1
    assert not (tmp_path / "objets" / "1" / "piece").exists()


def test_copie_objet_version_null_n_est_pas_epinglee(tmp_path):
    from seamtech_search.sauvegarde import copier_objets_s3

    class Client:
        def head_object(self, _cle):
            from tests.test_sauvegarde_objet_checksum import entete
            return {**entete(b"original"), "VersionId": "null"}
        def download_file(self, _cle, destination):
            destination.write_bytes(b"original")
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, Client(), tmp_path)
    assert resultat["nb_copies"] == 1
