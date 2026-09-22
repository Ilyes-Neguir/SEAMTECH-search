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
        client.objets[f"{sauvegarde.PREFIXE_SAUVEGARDES}{horodatage}.dump.manifest.json"] = b"{}"


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
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text("{}", encoding="utf-8")
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
    (tmp_path / "seamtech-search-20260920-120000.dump.manifest.json").write_text("{}", encoding="utf-8")
    purges = appliquer_retention(conserver=1, dossier_local=tmp_path)
    assert purges == []
    assert len(list(tmp_path.glob("*.dump"))) == 1
    assert len(list(tmp_path.glob("*.manifest.json"))) == 1


def test_retention_locale_conserver_zero_garde_la_derniere(tmp_path: Path) -> None:
    """Purge locale : conserver=0 traité comme 1, la dernière survit."""
    for i in range(3):
        horodatage = f"202609{20 + i:02d}-120000"
        (tmp_path / f"seamtech-search-{horodatage}.dump").write_bytes(b"dump")
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text("{}", encoding="utf-8")
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
        (tmp_path / f"seamtech-search-{horodatage}.dump.manifest.json").write_text("{}", encoding="utf-8")
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
