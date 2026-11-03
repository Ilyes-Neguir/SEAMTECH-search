"""Sauvegarde hors-site (Lot H.1) — tests UNITAIRES sans PostgreSQL.

L'aller-retour complet (base détruite puis reconstruite) est prouvé dans
``tests/test_sauvegarde_restauration.py`` avec une vraie base ; ce fichier
couvre les primitives : découverte des binaires, empreintes, inventaire
archive en LECTURE SEULE, rétention, chargement de manifeste.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seamtech_search import sauvegarde
from seamtech_search.sauvegarde import (
    NOM_VERROU,
    NOM_VERROU_PARENT,
    SauvegardeError,
    _sha256_fichier,
    _trouver_binaire,
    appliquer_retention,
    charger_manifeste,
    inventorier_archive,
)

pytestmark = pytest.mark.sauvegarde


class S3EnMemoire:
    """Client S3 factice pour les tests (mêmes méthodes que S3StorageClient)."""

    def __init__(self) -> None:
        self.objets: dict[str, bytes] = {}

    def upload_file(self, local: Path | str, remote_key: str, avoid_overwrite: bool = True) -> str:
        local = Path(local)
        cle = remote_key
        if avoid_overwrite and cle in self.objets:
            cle = f"{remote_key}.1"
        self.objets[cle] = local.read_bytes()
        return cle

    def download_file(self, remote_key: str, destination: Path | str) -> None:
        destination = Path(destination)
        if remote_key not in self.objets:
            raise RuntimeError(f"objet S3 absent : {remote_key}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objets[remote_key])

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(c for c in self.objets if c.startswith(prefix))

    def delete_file(self, remote_key: str) -> bool:
        return self.objets.pop(remote_key, None) is not None


# ---------------------------------------------------------------------------
# Découverte des binaires pg_dump / pg_restore
# ---------------------------------------------------------------------------


def test_trouver_binaire_prefere_le_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le binaire du PATH gagne — jamais de chemin codé en dur."""
    monkeypatch.setattr(sauvegarde.shutil, "which", lambda nom: f"/usr/bin/{nom}")
    assert _trouver_binaire("pg_dump") == "/usr/bin/pg_dump"


def test_trouver_binaire_via_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sauvegarde.shutil, "which", lambda nom: None)
    binaire = tmp_path / "pg_dump"
    binaire.write_bytes(b"#!/bin/sh\n")
    binaire.chmod(0o700)
    monkeypatch.setenv("SEAMTECH_PG_BINDIR", str(tmp_path))
    assert _trouver_binaire("pg_dump") == str(binaire)


def test_trouver_binaire_absent_est_explicite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans binaire, l'échec est explicite — jamais de repli silencieux."""
    monkeypatch.setattr(sauvegarde.shutil, "which", lambda nom: None)
    monkeypatch.delenv("SEAMTECH_PG_BINDIR", raising=False)
    monkeypatch.setattr(sauvegarde.glob, "glob", lambda motif: [])
    monkeypatch.setattr(sauvegarde, "_repertoire_binaires_pgserver", lambda: None)
    with pytest.raises(SauvegardeError, match="introuvable"):
        _trouver_binaire("pg_dump")


# ---------------------------------------------------------------------------
# Empreintes et inventaire archive (RG13 : lecture seule)
# ---------------------------------------------------------------------------


def test_sha256_fichier(tmp_path: Path) -> None:
    fichier = tmp_path / "binaire.bin"
    fichier.write_bytes(b"\x00\x01" * 5000)
    # Empreinte connue, calculée indépendamment dans le test.
    import hashlib

    assert _sha256_fichier(fichier) == hashlib.sha256(b"\x00\x01" * 5000).hexdigest()


def test_inventaire_archive_complet_et_lecture_seule(tmp_path: Path) -> None:
    racine = tmp_path / "archive"
    (racine / "CLIENT-A").mkdir(parents=True)
    (racine / "CLIENT-A" / "fiche.pdf").write_bytes(b"%PDF-1.4 contenu A")
    (racine / "CLIENT-B").mkdir()
    (racine / "CLIENT-B" / "plan.pdf").write_bytes(b"%PDF-1.4 contenu B")
    mtime_avant = (racine / "CLIENT-A" / "fiche.pdf").stat().st_mtime_ns

    inventaire = inventorier_archive([racine])

    assert inventaire["nb_fichiers"] == 2
    assert inventaire["octets_total"] == len(b"%PDF-1.4 contenu A") + len(b"%PDF-1.4 contenu B")
    chemins = {f["chemin"] for f in inventaire["fichiers"]}
    assert chemins == {"CLIENT-A/fiche.pdf", "CLIENT-B/plan.pdf"}
    assert all(len(f["sha256"]) == 64 for f in inventaire["fichiers"])
    # RG13 : l'archive n'est JAMAIS modifiée — mtime inchangé, rien d'ajouté.
    assert (racine / "CLIENT-A" / "fiche.pdf").stat().st_mtime_ns == mtime_avant
    assert sorted(p.name for p in racine.iterdir()) == ["CLIENT-A", "CLIENT-B"]


def test_inventaire_archive_chemins_portables(tmp_path: Path) -> None:
    """Les chemins du manifeste sont PORTABLES, pas « natifs à l'OS qui les a
    produits ».

    Pourquoi ce test existe : le manifeste était écrit avec le séparateur de
    l'OS (``str(chemin.relative_to(racine))``). Sur Windows, une archive
    inventoriée donnait ``CLIENT-A\\fiche.pdf`` ; restaurée sur Linux, ce
    séparateur n'en est pas un — ``Path("data") / "CLIENT-A\\fiche.pdf"`` crée
    un fichier DONT LE NOM CONTIENT UN ANTISLASH. La sauvegarde aurait été
    illisible hors de la machine qui l'a produite, ce qui est exactement
    l'inverse du but d'une sauvegarde hors site.

    La réparation est dans le produit (écriture POSIX) et la preuve ici :
    aucun antislash, et un chemin du manifeste est reconstructible par pathlib
    sur n'importe quelle plateforme.
    """
    racine = tmp_path / "archive"
    (racine / "CLIENT-A" / "sous-dossier").mkdir(parents=True)
    (racine / "CLIENT-A" / "sous-dossier" / "fiche.pdf").write_bytes(b"%PDF-1.4 portable")

    inventaire = inventorier_archive([racine])

    chemins = [f["chemin"] for f in inventaire["fichiers"]]
    assert chemins, "l'inventaire doit lister la fiche posée par le test"
    assert all("\\" not in chemin for chemin in chemins), f"antislash dans le manifeste : {chemins}"
    # Le chemin du manifeste est reconstructible tel quel, donc restaurable ailleurs.
    for chemin in chemins:
        assert (racine / chemin).is_file(), f"chemin du manifeste non rejouable : {chemin}"


def test_inventaire_archive_ignore_les_verrous(tmp_path: Path) -> None:
    """Les verrous de sauvegarde ne sont pas un contenu d'archive.

    Pourquoi ce test : l'inventaire parcourait et hachait TOUT, y compris le
    verrou que la sauvegarde venait de poser. Sur POSIX, ``flock`` est
    consultatif : lire le fichier verrouillé passait, et le défaut était
    invisible. Sur Windows, une plage d'octets verrouillée est EXCLUSIVE, même
    pour un autre descripteur du même processus : l'inventaire se prenait
    ``PermissionError`` et la sauvegarde échouait (runs 41560558673 et
    41561075673, ``test_cli_annonce_limites_sauvegarde``).

    Au-delà de la panne : un verrou n'a rien à faire dans un inventaire
    d'archive, sur aucune plateforme.
    """
    racine = tmp_path / "archive"
    (racine / "CLIENT-A").mkdir(parents=True)
    (racine / "CLIENT-A" / "fiche.pdf").write_bytes(b"%PDF-1.4")
    (racine / NOM_VERROU).write_bytes(b"")
    (racine / NOM_VERROU_PARENT).write_bytes(b"")

    inventaire = inventorier_archive([racine])

    chemins = {f["chemin"] for f in inventaire["fichiers"]}
    assert chemins == {"CLIENT-A/fiche.pdf"}, chemins
    assert inventaire["nb_fichiers"] == 1


def test_inventaire_archive_racine_absente(tmp_path: Path) -> None:
    with pytest.raises(SauvegardeError, match="racine d'archive introuvable"):
        inventorier_archive([tmp_path / "inexistante"])


# ---------------------------------------------------------------------------
# Rétention : N conservées, JAMAIS la dernière
# ---------------------------------------------------------------------------


def _semer_sauvegardes(client: S3EnMemoire, n: int) -> None:
    for i in range(n):
        horodatage = f"202609{20 + i:02d}-120000"
        client.objets[f"{sauvegarde.PREFIXE_SAUVEGARDES}{horodatage}.dump"] = b"dump"
        client.objets[f"{sauvegarde.PREFIXE_SAUVEGARDES}{horodatage}.dump.manifest.json"] = b'{"complet": true, "protection_globale_prouvee": true}'


def test_retention_conserve_les_n_dernieres() -> None:
    client = S3EnMemoire()
    _semer_sauvegardes(client, 5)
    purges = appliquer_retention(client, conserver=2)
    restantes = [c for c in client.list_keys(sauvegarde.PREFIXE_SAUVEGARDES) if c.endswith(".dump")]
    assert len(restantes) == 2
    assert restantes[-1].endswith("20260924-120000.dump")  # la plus récente
    assert len(purges) == 6  # 3 dumps + 3 manifestes purgés


def test_retention_jamais_la_derniere() -> None:
    client = S3EnMemoire()
    _semer_sauvegardes(client, 1)
    purges = appliquer_retention(client, conserver=1)
    assert purges == []
    assert len(client.list_keys(sauvegarde.PREFIXE_SAUVEGARDES)) == 2


def test_retention_conserver_zero_ne_detruit_pas_tout() -> None:
    """Garde-fou : conserver < 1 est traité comme 1 — la dernière survit."""
    client = S3EnMemoire()
    _semer_sauvegardes(client, 3)
    appliquer_retention(client, conserver=0)
    assert any(c.endswith(".dump") for c in client.list_keys(sauvegarde.PREFIXE_SAUVEGARDES))


def test_retention_locale_conserve_les_n_dernieres(tmp_path: Path) -> None:
    """Purge locale : conserve les N plus récentes et supprime les plus anciennes."""
    for i in range(5):
        horodatage = f"202609{20 + i:02d}-120000"
        (tmp_path / f"seamtech-search-{horodatage}.dump").write_bytes(b"dump")
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text('{"complet": true, "protection_globale_prouvee": true}', encoding="utf-8")
    purges = appliquer_retention(conserver=2, dossier_local=tmp_path)
    restantes = sorted(tmp_path.glob("*.dump"))
    assert len(restantes) == 2
    assert restantes[-1].name.endswith("20260924-120000.dump")
    manifestes = sorted(tmp_path.glob("*.manifest.json"))
    assert len(manifestes) == 2
    assert len(purges) == 6  # 3 dumps + 3 manifestes


def test_retention_locale_jamais_la_derniere(tmp_path: Path) -> None:
    """Purge locale : une seule sauvegarde présente ne doit jamais être supprimée."""
    (tmp_path / "seamtech-search-20260920-120000.dump").write_bytes(b"dump")
    (tmp_path / "seamtech-search-20260920-120000.dump.manifest.json").write_text('{"complet": true, "protection_globale_prouvee": true}', encoding="utf-8")
    purges = appliquer_retention(conserver=1, dossier_local=tmp_path)
    assert purges == []
    assert len(list(tmp_path.glob("*.dump"))) == 1
    assert len(list(tmp_path.glob("*.manifest.json"))) == 1


def test_retention_locale_conserver_zero_garde_la_derniere(tmp_path: Path) -> None:
    """Purge locale : conserver=0 traité comme 1, la dernière survit."""
    for i in range(3):
        horodatage = f"202609{20 + i:02d}-120000"
        (tmp_path / f"seamtech-search-{horodatage}.dump").write_bytes(b"dump")
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text('{"complet": true, "protection_globale_prouvee": true}', encoding="utf-8")
    purges = appliquer_retention(conserver=0, dossier_local=tmp_path)
    assert len(list(tmp_path.glob("*.dump"))) == 1
    assert len(list(tmp_path.glob("*.manifest.json"))) == 1
    assert len(purges) == 4  # 2 dumps + 2 manifestes purgés


def test_retention_symetrique_locale_et_s3(tmp_path: Path) -> None:
    """Purge symétrique : purge simultanément S3 et le dossier local."""
    client = S3EnMemoire()
    _semer_sauvegardes(client, 4)
    for i in range(4):
        horodatage = f"202609{20 + i:02d}-120000"
        (tmp_path / f"seamtech-search-{horodatage}.dump").write_bytes(b"dump")
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text('{"complet": true, "protection_globale_prouvee": true}', encoding="utf-8")
    purges = appliquer_retention(client_s3=client, conserver=2, dossier_local=tmp_path)
    # S3
    restantes_s3 = [c for c in client.list_keys(sauvegarde.PREFIXE_SAUVEGARDES) if c.endswith(".dump")]
    assert len(restantes_s3) == 2
    # Local
    assert len(list(tmp_path.glob("*.dump"))) == 2
    assert len(list(tmp_path.glob("*.manifest.json"))) == 2
    # Purges totales : (2 dumps + 2 manifestes S3) + (2 dumps + 2 manifestes locaux) = 8
    assert len(purges) == 8


# ---------------------------------------------------------------------------
# Manifestes
# ---------------------------------------------------------------------------


def test_charger_manifeste_local(tmp_path: Path) -> None:
    fichier = tmp_path / "manifeste.json"
    fichier.write_text(json.dumps({"version_schema_metier": "013_recherche_fonds_reel"}), encoding="utf-8")
    manifeste = charger_manifeste(fichier, None, None)
    assert manifeste["version_schema_metier"] == "013_recherche_fonds_reel"


def test_charger_manifeste_sans_source_est_explicite() -> None:
    with pytest.raises(SauvegardeError, match="fournir"):
        charger_manifeste(None, None, None)


def test_publication_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fichier = tmp_path / "mesures.jsonl"
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_JSON", str(fichier))
    sauvegarde._publier({"operation": "sauver", "duree_s": 1.5})
    sauvegarde._publier({"operation": "restaurer", "duree_s": 2.5})
    lignes = [json.loads(brut) for brut in fichier.read_text(encoding="utf-8").splitlines()]
    assert [ligne["operation"] for ligne in lignes] == ["sauver", "restaurer"]


def test_retention_incomplete_recente_ne_remplace_pas_derniere_complete(tmp_path: Path) -> None:
    client = S3EnMemoire()
    for jour, complet in ((20, True), (21, False)):
        nom = f"seamtech-search-202609{jour}-120000.dump"
        (tmp_path / nom).write_bytes(b"dump")
        (tmp_path / f"{nom}.manifest.json").write_text(json.dumps({"complet": complet, "protection_globale_prouvee": complet}), encoding="utf-8")
        client.objets[f"backups/{nom}"] = b"dump"
        client.objets[f"backups/{nom}.manifest.json"] = json.dumps({"complet": complet, "protection_globale_prouvee": complet}).encode()
    appliquer_retention(client, conserver=1, dossier_local=tmp_path)
    assert (tmp_path / "seamtech-search-20260920-120000.dump").exists()
    assert "backups/seamtech-search-20260920-120000.dump" in client.objets


def test_retention_manifeste_indisponible_interdit_purge(tmp_path: Path) -> None:
    client = S3EnMemoire()
    _semer_sauvegardes(client, 2)
    client.objets.pop(f"{sauvegarde.PREFIXE_SAUVEGARDES}20260921-120000.dump.manifest.json")
    for jour in (20, 21):
        (tmp_path / f"seamtech-search-202609{jour}-120000.dump").write_bytes(b"dump")
    avant = dict(client.objets)
    appliquer_retention(client, conserver=1, dossier_local=tmp_path)
    assert client.objets == avant
    assert len(list(tmp_path.glob('*.dump'))) == 2


def test_retention_conserve_manifeste_si_suppression_dump_refusee() -> None:
    class ClientSuppressionRefusee(S3EnMemoire):
        def delete_file(self, remote_key: str) -> bool:
            if remote_key.endswith('.dump'):
                return False
            return super().delete_file(remote_key)

    client = ClientSuppressionRefusee()
    _semer_sauvegardes(client, 2)
    avant = dict(client.objets)
    appliquer_retention(client, conserver=1)
    assert client.objets == avant


def test_retention_ancienne_sans_qualification_globale_ne_purge_rien(tmp_path: Path) -> None:
    client = S3EnMemoire()
    _semer_sauvegardes(client, 3)
    for cle in client.objets:
        if cle.endswith(".manifest.json"):
            client.objets[cle] = b'{"complet": true}'
    for i in range(3):
        nom = f"seamtech-search-202609{20 + i:02d}-120000.dump"
        (tmp_path / nom).write_bytes(b"dump")
        (tmp_path / f"{nom}.manifest.json").write_text('{"complet": true}', encoding="utf-8")
    avant_s3 = dict(client.objets)
    avant_fichiers = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert appliquer_retention(client, conserver=1, dossier_local=tmp_path) == []
    assert client.objets == avant_s3
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == avant_fichiers


@pytest.mark.parametrize("etat", [
    {"complet": True, "protection_globale_prouvee": False},
    {"complet": False, "protection_globale_prouvee": True},
    {"complet": True, "protection_globale_prouvee": "true"},
])
def test_retention_refuse_un_manifeste_contradictoire_ou_non_booleen(etat):
    client = S3EnMemoire()
    _semer_sauvegardes(client, 3)
    cle = f"{sauvegarde.PREFIXE_SAUVEGARDES}20260921-120000.dump.manifest.json"
    client.objets[cle] = json.dumps(etat).encode()
    avant = dict(client.objets)
    assert appliquer_retention(client, conserver=1) == []
    assert client.objets == avant


def test_nom_base_manquant_ne_fuite_pas_les_identifiants():
    url = "postgresql://utilisateur:synthetique-secret@localhost:5432/"
    with pytest.raises(sauvegarde.SauvegardeError) as erreur:
        sauvegarde._nom_base(url)
    assert "synthetique-secret" not in str(erreur.value)
    assert "utilisateur" not in str(erreur.value)


def test_connexion_invalide_ne_divulgue_pas_uri_dans_la_trace():
    import traceback

    url = "postgresql://u:synthetique-secret@[invalid/base"
    try:
        sauvegarde._connecter(url)
    except Exception:
        message = traceback.format_exc()
    else:
        pytest.fail("une URI invalide doit échouer")
    assert "synthetique-secret" not in message


@pytest.mark.parametrize("query", ["dbname=production", "dbname=cible_neuve", "%64bname=production"])
def test_restauration_refuse_dbname_query_divergent_avant_connexion(tmp_path, monkeypatch, query):
    import hashlib

    from seamtech_search import sauvegarde

    dump = tmp_path / "sauvegarde.dump"
    contenu = b"dump-synthetique"
    dump.write_bytes(contenu)
    manifeste = {"dump": {"sha256": hashlib.sha256(contenu).hexdigest()}}
    def interdiction(*_args, **_kwargs):
        raise AssertionError("Ne doit pas contacter une base tierce")
    monkeypatch.setattr(sauvegarde, "_connecter", interdiction)
    with pytest.raises(sauvegarde.SauvegardeError):
        sauvegarde.restaurer(f"postgresql://u:p@localhost/cible_neuve?{query}", manifeste,
                             chemin_dump_local=dump)


def test_urls_postgres_socket_restaurer_conservent_options():
    from psycopg2.extensions import parse_dsn

    from seamtech_search import sauvegarde

    url = "postgresql:///cible_neuve?host=/tmp&port=55432&user=postgres&sslmode=disable"
    admin = parse_dsn(sauvegarde._url_sans_base(url))
    cible = parse_dsn(sauvegarde._remplacer_nom_base(url, "autre_cible"))
    assert admin["dbname"] == "postgres" and cible["dbname"] == "autre_cible"
    assert admin["host"] == cible["host"] == "/tmp"
    assert admin["port"] == cible["port"] == "55432"
    assert admin["sslmode"] == cible["sslmode"] == "disable"


def test_restauration_utilise_octets_verifies_meme_si_dump_change(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    dump = tmp_path / "dump"
    original = b"dump-synthetique-authentique"
    dump.write_bytes(original)
    manifeste = {"dump": {"sha256": hashlib.sha256(original).hexdigest(), "octets": len(original)}}
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, *_args, **_kwargs): pass
        def fetchone(self): return None
    class Connexion:
        def cursor(self): return Cursor()
        def close(self): pass
    def connecter(*_args, **_kwargs):
        dump.write_bytes(b"dump-synthetique-substitue")
        return Connexion()
    vus = []
    def restaurer_processus(args, **_kwargs):
        vus.append(Path(args[-1]).read_bytes())
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(sauvegarde, "_connecter", connecter)
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda _nom: "pg_restore")
    monkeypatch.setattr(sauvegarde.subprocess, "run", restaurer_processus)
    monkeypatch.setattr(sauvegarde, "_etat_base", lambda *_args, **_kwargs: {"tables": {}, "fiches_par_statut": {}})
    sauvegarde.restaurer("postgresql://u:p@localhost/cible", manifeste, chemin_dump_local=dump)
    assert vus == [original, original]


def test_restauration_refuse_lien_et_nettoie_la_copie_en_cas_d_erreur(tmp_path, monkeypatch):
    import hashlib

    fichier = tmp_path / "source"
    fichier.write_bytes(b"source-synthetique")
    lien = tmp_path / "lien"
    lien.symlink_to(fichier)
    travail = tmp_path / "travail"
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(travail))
    manifeste = {"dump": {"sha256": hashlib.sha256(fichier.read_bytes()).hexdigest()}}
    def interdiction(*_args, **_kwargs):
        raise AssertionError("ne pas contacter PostgreSQL")
    monkeypatch.setattr(sauvegarde, "_connecter", interdiction)
    with pytest.raises(sauvegarde.SauvegardeError):
        sauvegarde.restaurer("postgresql://u:p@localhost/cible", manifeste, chemin_dump_local=lien)
    assert fichier.read_bytes() == b"source-synthetique"
    assert not list(travail.glob("seamtech-restore-copy-*"))


def test_restauration_nettoie_la_copie_si_empreinte_incorrecte(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    dump.write_bytes(b"non-conforme")
    travail = tmp_path / "travail"
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(travail))
    monkeypatch.setattr(sauvegarde, "_connecter", lambda *_a, **_k: pytest.fail("connexion non autorisée"))
    with pytest.raises(sauvegarde.SauvegardeError, match="empreinte"):
        sauvegarde.restaurer("postgresql://u:p@localhost/cible", {"dump": {"sha256": "0" * 64}}, dump)
    assert not list(travail.glob("seamtech-restore-copy-*"))


def test_copie_dump_privee_permissions_et_nettoyage(tmp_path, monkeypatch):
    import os
    import stat

    source = tmp_path / "source"
    source.write_bytes(b"contenu-synthetique")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    with sauvegarde._instantane_dump_prive(source) as copie:
        assert copie.read_bytes() == source.read_bytes()
        if os.name == "posix":
            assert stat.S_IMODE(copie.stat().st_mode) == 0o600
            assert stat.S_IMODE(copie.parent.stat().st_mode) == 0o700
    assert not copie.exists() and not copie.parent.exists()


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="FIFO indisponible")
def test_restauration_refuse_fifo_sans_attendre_ecrivain(tmp_path, monkeypatch):
    import os

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    monkeypatch.setattr(sauvegarde, "_connecter", lambda *_a, **_k: pytest.fail("connexion interdite"))
    with pytest.raises(sauvegarde.SauvegardeError):
        sauvegarde.restaurer("postgresql://u:p@localhost/cible", {"dump": {"sha256": "0" * 64}}, fifo)
    assert not list(tmp_path.glob("seamtech-restore-copy-*"))


def test_copie_dump_disque_plein_nettoie_et_ne_divulgue_pas_le_chemin(tmp_path, monkeypatch):
    source = tmp_path / "nom-sensible"
    source.write_bytes(b"contenu-synthetique")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    def disque_plein(*_args, **_kwargs):
        raise OSError(28, "espace insuffisant", str(source))
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", disque_plein)
    monkeypatch.setattr(sauvegarde, "_connecter", lambda *_a, **_k: pytest.fail("connexion interdite"))
    with pytest.raises(sauvegarde.SauvegardeError) as erreur:
        sauvegarde.restaurer("postgresql://u:p@localhost/cible", {"dump": {"sha256": "0" * 64}}, source)
    assert "nom-sensible" not in str(erreur.value)
    assert not list(tmp_path.glob("seamtech-restore-copy-*"))


def test_verrou_sauvegarde_refuse_lien_sans_toucher_tiers(tmp_path):
    dossier = tmp_path / "backup"
    dossier.mkdir()
    temoin = tmp_path / "temoin"
    temoin.write_bytes(b"precieux-synthetique")
    (dossier / sauvegarde.NOM_VERROU).symlink_to(temoin)
    with pytest.raises(SauvegardeError):
        with sauvegarde.VerrouSauvegarde(dossier):
            pytest.fail("lien de verrou suivi")
    assert temoin.read_bytes() == b"precieux-synthetique"


def test_verrou_ne_modifie_pas_fichier_regulier_existant(tmp_path):
    dossier = tmp_path / "backup"
    dossier.mkdir()
    temoin = dossier / sauvegarde.NOM_VERROU
    temoin.write_bytes(b"precieux-synthetique")
    with sauvegarde.VerrouSauvegarde(dossier):
        assert temoin.read_bytes() == b"precieux-synthetique"
    assert temoin.read_bytes() == b"precieux-synthetique"


def test_verrou_ferme_flux_si_flock_echoue(tmp_path, monkeypatch):
    import errno
    from types import SimpleNamespace

    def erreur(*_args):
        raise OSError(errno.EIO, "panne-synthetique")
    verrou = sauvegarde.VerrouSauvegarde(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sauvegarde, "fcntl", SimpleNamespace(flock=erreur, LOCK_EX=2, LOCK_NB=4))
        with pytest.raises(SauvegardeError):
            verrou.__enter__()
    assert verrou._flux is None
    with verrou:
        assert verrou._flux is not None


def test_verrou_refuse_lien_dur_sans_modifier_fichier(tmp_path):
    import os

    temoin = tmp_path / "temoin"
    temoin.write_bytes(b"precieux-synthetique")
    dossier = tmp_path / "backup"
    dossier.mkdir()
    os.link(temoin, dossier / sauvegarde.NOM_VERROU)
    with pytest.raises(SauvegardeError):
        with sauvegarde.VerrouSauvegarde(dossier):
            pytest.fail("lien dur non autorisé")
    assert temoin.read_bytes() == b"precieux-synthetique"


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="FIFO indisponible")
def test_verrou_refuse_fifo_sans_blocage(tmp_path):
    import os

    os.mkfifo(tmp_path / sauvegarde.NOM_VERROU)
    with pytest.raises(SauvegardeError):
        with sauvegarde.VerrouSauvegarde(tmp_path):
            pytest.fail("FIFO non autorisée")


def test_verrou_absence_fcntl_echoue_sans_creer_fichier(tmp_path, monkeypatch):
    monkeypatch.setattr(sauvegarde, "fcntl", None)
    with pytest.raises(SauvegardeError, match="indisponible"):
        with sauvegarde.VerrouSauvegarde(tmp_path):
            pytest.fail("aucun repli sans verrou")
    assert not (tmp_path / sauvegarde.NOM_VERROU).exists()


def test_verrou_processus_concurrent_refuse_puis_reussi(tmp_path):
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from seamtech_search.sauvegarde import VerrouSauvegarde, SauvegardeError\n"
        "import sys\n"
        "try:\n"
        "    with VerrouSauvegarde(Path(sys.argv[1])): pass\n"
        "except SauvegardeError:\n"
        "    sys.exit(9)\n"
    )
    with sauvegarde.VerrouSauvegarde(tmp_path):
        resultat = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10)
        assert resultat.returncode == 9
    resultat = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10)
    assert resultat.returncode == 0, resultat.stderr.decode(errors="replace")


def test_module_sauvegarde_importable_sans_fcntl():
    import subprocess
    import sys

    script = (
        "import builtins\n"
        "original = builtins.__import__\n"
        "def importer(nom, *args, **kwargs):\n"
        "    if nom == 'fcntl': raise ImportError('indisponible-synthetique')\n"
        "    return original(nom, *args, **kwargs)\n"
        "builtins.__import__ = importer\n"
        "from seamtech_search import sauvegarde\n"
        "assert sauvegarde.fcntl is None\n"
    )
    resultat = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=10)
    assert resultat.returncode == 0, resultat.stderr.decode(errors="replace")


def test_verrou_remplace_pendant_execution_ne_permet_pas_chevauchement(tmp_path):
    with sauvegarde.VerrouSauvegarde(tmp_path):
        (tmp_path / sauvegarde.NOM_VERROU).unlink()
        with pytest.raises(SauvegardeError):
            with sauvegarde.VerrouSauvegarde(tmp_path):
                pass


def test_verrou_ne_perd_pas_exclusion_sur_reentree_meme_objet(tmp_path):
    verrou = sauvegarde.VerrouSauvegarde(tmp_path)
    with verrou:
        with pytest.raises(SauvegardeError):
            with verrou:
                pytest.fail("réentrée non autorisée")
        with pytest.raises(SauvegardeError):
            with sauvegarde.VerrouSauvegarde(tmp_path):
                pytest.fail("exclusion perdue")


def test_verrou_meme_objet_concurrence_avant_ouverture(tmp_path, monkeypatch):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    verrou = sauvegarde.VerrouSauvegarde(tmp_path)
    attend = threading.Event()
    poursuivre = threading.Event()
    ouvrir = os.open
    def ouverture_retardee(path, *args, **kwargs):
        if path == sauvegarde.NOM_VERROU and threading.current_thread().name.endswith("_0"):
            attend.set()
            if not poursuivre.wait(timeout=5):
                raise RuntimeError("barrière de test expirée")
        return ouvrir(path, *args, **kwargs)
    monkeypatch.setattr(sauvegarde.os, "supports_dir_fd", os.supports_dir_fd | {ouverture_retardee})
    monkeypatch.setattr(sauvegarde.os, "open", ouverture_retardee)
    def executer():
        try:
            with verrou:
                return "ok"
        except SauvegardeError:
            return "refus"
    with ThreadPoolExecutor(max_workers=2) as pool:
        premier = pool.submit(executer)
        assert attend.wait(timeout=5)
        try:
            second = pool.submit(executer).result(timeout=5)
        finally:
            poursuivre.set()
        assert premier.result(timeout=5) == "ok"
    assert second == "refus"


def test_suppression_verrou_ne_permet_pas_deuxieme_processus(tmp_path):
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from seamtech_search.sauvegarde import VerrouSauvegarde, SauvegardeError\n"
        "import sys\n"
        "try:\n"
        "    with VerrouSauvegarde(Path(sys.argv[1])): pass\n"
        "except SauvegardeError:\n"
        "    sys.exit(9)\n"
    )
    with sauvegarde.VerrouSauvegarde(tmp_path):
        (tmp_path / sauvegarde.NOM_VERROU).unlink()
        resultat = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10)
        assert resultat.returncode == 9
    resultat = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, timeout=10)
    assert resultat.returncode == 0, resultat.stderr.decode(errors="replace")


def test_verrou_reste_compatible_avec_processus_ancien(tmp_path):
    import fcntl

    chemin = tmp_path / sauvegarde.NOM_VERROU
    chemin.write_bytes(b"temoin")
    with chemin.open("r+") as ancien:
        fcntl.flock(ancien.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SauvegardeError, match="chevauchement"):
            with sauvegarde.VerrouSauvegarde(tmp_path):
                pytest.fail("verrou historique contourné")
    with sauvegarde.VerrouSauvegarde(tmp_path):
        assert chemin.read_bytes() == b"temoin"


def test_erreur_ouverture_fichier_lache_verrou_dossier(tmp_path, monkeypatch):
    import errno
    import os

    ouvrir = os.open
    def erreur(path, *args, **kwargs):
        if path == sauvegarde.NOM_VERROU:
            raise OSError(errno.EIO, "panne-synthetique")
        return ouvrir(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {erreur})
        patch.setattr(os, "open", erreur)
        verrou = sauvegarde.VerrouSauvegarde(tmp_path)
        with pytest.raises(SauvegardeError):
            verrou.__enter__()
        assert verrou._flux is None and verrou._fd_dossier is None
    with sauvegarde.VerrouSauvegarde(tmp_path):
        pass


def test_verrou_refuse_dossier_symlink(tmp_path):
    cible = tmp_path / "reel"
    cible.mkdir()
    lien = tmp_path / "lien"
    lien.symlink_to(cible, target_is_directory=True)
    with pytest.raises(SauvegardeError):
        with sauvegarde.VerrouSauvegarde(lien):
            pytest.fail("lien de dossier suivi")
    assert not list(cible.iterdir())


def test_verrou_double_libere_apres_mort_du_processus(tmp_path):
    import select
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from seamtech_search.sauvegarde import VerrouSauvegarde\n"
        "import sys,time\n"
        "with VerrouSauvegarde(Path(sys.argv[1])):\n"
        "    print('pret', flush=True)\n"
        "    time.sleep(60)\n"
    )
    enfant = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert enfant.stdout is not None
        assert select.select([enfant.stdout], [], [], 10)[0], "processus de test non prêt"
        assert enfant.stdout.readline().strip() == "pret"
        with pytest.raises(SauvegardeError):
            with sauvegarde.VerrouSauvegarde(tmp_path):
                pytest.fail("processus encore actif")
    finally:
        enfant.kill()
        enfant.wait(timeout=10)
        if enfant.stdout:
            enfant.stdout.close()
    with sauvegarde.VerrouSauvegarde(tmp_path):
        pass


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Une ancienne version qui ne verrouille QUE le fichier, fichier ensuite "
        "supprimé : rien dans le système de fichiers ne désigne plus son verrou "
        "(inode détaché). Le verrou du parent couvre le dossier remplacé, pas ce "
        "cas-là ; le départager supposerait un inventaire des descripteurs ouverts "
        "par processus, hors de portée d'un verrou POSIX portable."
    ),
)
def test_version_mixte_et_fichier_efface_refuse_chevauchement(tmp_path):
    import fcntl

    chemin = tmp_path / sauvegarde.NOM_VERROU
    chemin.write_bytes(b"temoin")
    with chemin.open("r+") as ancien:
        fcntl.flock(ancien.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        chemin.unlink()
        with pytest.raises(SauvegardeError):
            with sauvegarde.VerrouSauvegarde(tmp_path):
                pass


def test_remplacement_dossier_sauvegarde_refuse_chevauchement(tmp_path):
    dossier = tmp_path / "backup"
    dossier.mkdir()
    with sauvegarde.VerrouSauvegarde(dossier):
        dossier.rename(tmp_path / "backup-original")
        dossier.mkdir()
        with pytest.raises(SauvegardeError):
            with sauvegarde.VerrouSauvegarde(dossier):
                pass


def test_interruption_acquisition_verrou_ferme_descripteurs(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def interrompre(*_args):
        raise KeyboardInterrupt
    verrou = sauvegarde.VerrouSauvegarde(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sauvegarde, "fcntl", SimpleNamespace(flock=interrompre, LOCK_EX=2, LOCK_NB=4))
        with pytest.raises(KeyboardInterrupt):
            verrou.__enter__()
    assert verrou._flux is None and verrou._fd_dossier is None
    with verrou:
        pass


def test_interruption_verrou_fichier_relache_aussi_dossier(tmp_path, monkeypatch):
    import fcntl
    from types import SimpleNamespace

    appels = 0
    def interrompre_deuxieme(fd, flags):
        nonlocal appels
        appels += 1
        if appels == 2:
            raise KeyboardInterrupt
        return fcntl.flock(fd, flags)
    verrou = sauvegarde.VerrouSauvegarde(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sauvegarde, "fcntl", SimpleNamespace(flock=interrompre_deuxieme,
                                                          LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))
        with pytest.raises(KeyboardInterrupt):
            verrou.__enter__()
    assert verrou._flux is None and verrou._fd_dossier is None
    with sauvegarde.VerrouSauvegarde(tmp_path):
        pass


def test_restauration_sans_pg_restore_ne_cree_pas_de_base(tmp_path, monkeypatch):
    import hashlib

    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    appels = []
    def connexion(*_args, **_kwargs):
        appels.append("connexion")
        pytest.fail("aucune connexion avant validation du binaire")
    def binaire(_nom):
        raise SauvegardeError("binaire indisponible")
    monkeypatch.setattr(sauvegarde, "_connecter", connexion)
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", binaire)
    with pytest.raises(SauvegardeError, match="binaire"):
        sauvegarde.restaurer("postgresql://user:secret@localhost/cible", manifeste, dump)
    assert appels == []


def test_trouver_binaire_ignore_fichier_non_executable(monkeypatch, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("droits exécutables POSIX")
    faux = tmp_path / "pg_restore"
    faux.write_bytes(b"non-executable")
    faux.chmod(0o600)
    monkeypatch.setenv("SEAMTECH_PG_BINDIR", str(tmp_path))
    monkeypatch.setattr(sauvegarde.shutil, "which", lambda _nom: None)
    monkeypatch.setattr(sauvegarde.glob, "glob", lambda _motif: [])
    monkeypatch.setattr(sauvegarde, "_repertoire_binaires_pgserver", lambda: None)
    with pytest.raises(SauvegardeError, match="introuvable"):
        sauvegarde._trouver_binaire("pg_restore")


@pytest.mark.parametrize("taille", [None, -1, True, "19", 999])
def test_restauration_refuse_taille_dump_invalide_avant_connexion(tmp_path, monkeypatch, taille):
    import hashlib

    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": taille}}
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda _nom: "pg_restore")
    def connexion(*_args, **_kwargs):
        pytest.fail("aucune connexion avant validation de la taille")
    monkeypatch.setattr(sauvegarde, "_connecter", connexion)
    with pytest.raises(SauvegardeError, match="taille"):
        sauvegarde.restaurer("postgresql://user@localhost/cible", manifeste, dump)


def test_restauration_refuse_archive_incompatible_avant_connexion(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    def commande(args, **_kwargs):
        assert "--file=-" in args
        return SimpleNamespace(returncode=1)
    def connexion(*_args, **_kwargs):
        pytest.fail("aucune connexion avant verification de l'archive")
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda _nom: "pg_restore")
    monkeypatch.setattr(sauvegarde.subprocess, "run", commande)
    monkeypatch.setattr(sauvegarde, "_connecter", connexion)
    with pytest.raises(SauvegardeError, match="archive"):
        sauvegarde.restaurer("postgresql://user@localhost/cible", manifeste, dump)


def test_restauration_binaire_non_lancable_echoue_avant_connexion(tmp_path, monkeypatch):
    import hashlib

    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    faux = tmp_path / "pg_restore"
    faux.write_bytes(b"programme non executable par le systeme")
    faux.chmod(0o700)
    def connexion(*_args, **_kwargs):
        pytest.fail("aucune connexion avant verification du binaire")
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda _nom: str(faux))
    monkeypatch.setattr(sauvegarde, "_connecter", connexion)
    with pytest.raises(SauvegardeError, match="pg_restore"):
        sauvegarde.restaurer("postgresql://user@localhost/cible", manifeste, dump)


def test_restauration_echec_lancement_apres_creation_signale_base_partielle(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, *_args, **_kwargs): pass
        def fetchone(self): return None
    class Connexion:
        def cursor(self): return Cursor()
        def close(self): pass
    def commande(args, **_kwargs):
        if "--file=-" in args:
            return SimpleNamespace(returncode=0)
        raise OSError("secret-synthetique dans erreur")
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda _nom: "pg_restore")
    monkeypatch.setattr(sauvegarde.subprocess, "run", commande)
    monkeypatch.setattr(sauvegarde, "_connecter", lambda *_a, **_kw: Connexion())
    with pytest.raises(SauvegardeError, match="partielle") as erreur:
        sauvegarde.restaurer("postgresql://user@localhost/cible", manifeste, dump)
    assert "secret-synthetique" not in str(erreur.value)


def test_restauration_refuse_schema_physique_invalide_pour_nouveau_manifeste(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    dump = tmp_path / 'dump'
    donnees = b'dump-synthetique'
    dump.write_bytes(donnees)
    manifeste = {'dump': {'sha256': hashlib.sha256(donnees).hexdigest(), 'octets': len(donnees)},
                 'version_schema_application': sauvegarde.VERSION_SCHEMA_APPLICATION}
    class Curseur:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, *_args): pass
        def fetchone(self): return None
    class Connexion:
        def cursor(self): return Curseur()
        def close(self): pass
    monkeypatch.setattr(sauvegarde, '_connecter', lambda *_args, **_kwargs: Connexion())
    monkeypatch.setattr(sauvegarde, '_trouver_binaire', lambda _nom: 'pg_restore')
    monkeypatch.setattr(sauvegarde.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(sauvegarde, '_etat_base', lambda *_args, **_kwargs: {
        'tables': {}, 'fiches_par_statut': {}, 'versions_schema': [sauvegarde.VERSION_SCHEMA_APPLICATION],
        'schema_identite_octets_valide': False,
    })
    with pytest.raises(sauvegarde.SauvegardeError, match='identité|déclencheur'):
        sauvegarde.restaurer('postgresql://synthetique/cible', manifeste, chemin_dump_local=dump)
