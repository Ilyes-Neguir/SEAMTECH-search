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
    url_cible = url.replace(f"/{nom}?", f"/{nom_cible}?") if "?" in url else url.rsplit("/", 1)[0] + f"/{nom_cible}"
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
        assert manifeste["complet"] is True
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
    url_cible = url.replace(f"/{nom}?", f"/{nom_cible}?") if "?" in url else url.rsplit("/", 1)[0] + f"/{nom_cible}"
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
        assert manifeste["objets_s3"]["nb_copies"] >= 1
        assert manifeste["complet"] is True

        local.unlink()
        assert client.delete_file(cle)
        assert not local.exists()

        restaurer(url_cible, manifeste, chemin_dump_local=backups / manifeste["dump"]["fichier"])
        extra_f = restaurer_fichiers_locaux(manifeste, backups, tmp_path / "restaure-f")
        extra_o = restaurer_objets_s3(manifeste, backups, client)
        assert extra_f["nb"] >= 1
        assert extra_o["nb"] >= 1
        assert _sha256_fichier(Path(extra_f["fichiers"][0]["path"])) == sha_local
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
