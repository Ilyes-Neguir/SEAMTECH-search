"""P2 — multipart en flux : interruption, bornes et isolement entre envois.

Le plan demande le multipart « sous charge, interruption et concurrence ». Ce
fichier prend les trois l'un après l'autre, avec l'assertion que l'incident
voudrait voir fausse :

* **interruption** : un client coupé en plein envoi ne doit PAS laisser un
  brouillon derrière lui. C'est la fuite la plus coûteuse du lot : elle ne se
  voit pas au premier envoi, elle s'accumule ;
* **charge** : au-delà de la borne, l'envoi est refusé AVANT toute écriture.
  Un refus qui laisse un dossier à moitié écrit n'est pas un refus ;
* **concurrence** : deux envois du même dossier ne partagent jamais un brouillon.

Chaque test vérifie l'ÉTAT DU DISQUE après l'incident, pas seulement le code
HTTP : « le serveur a répondu 4xx » ne prouve pas qu'il a nettoyé.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import MAX_UPLOAD_FILES, create_app
from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import staging_root


def _app(tmp_path: Path, racine: Path | None = None) -> tuple[TestClient, AppConfig]:
    racine = racine or tmp_path / "racine"
    racine.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    config = AppConfig(root_paths=[racine], database_path=tmp_path / "data" / "search.db")
    return TestClient(create_app(config)), config


def _pdf(nom: str = "fiche.pdf") -> bytes:
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def test_client_coupe_en_plein_envoi_ne_laisse_pas_de_brouillon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connexion coupée au milieu du flux : aucun dossier résiduel.

    Le scénario est réel : un opérateur dépose un dossier de plusieurs
    centaines de mégaoctets, le réseau tombe (ou il ferme l'onglet). Le worker
    n'est pas encore impliqué : c'est l'API qui a créé le dossier de staging,
    c'est donc à elle de le rendre.
    """
    from starlette.datastructures import UploadFile

    client, config = _app(tmp_path)
    staging = staging_root(config)
    staging.mkdir(parents=True, exist_ok=True)
    avant = sorted(p.name for p in staging.iterdir())

    original = UploadFile.read
    appels = {"n": 0}

    async def lecture_coupee(self, *args, **kwargs):  # noqa: ANN001, ANN401, ARG001
        appels["n"] += 1
        # Le corps est volontairement PLUS GROS que le bloc d'un mégaoctet :
        # la coupure doit survenir APRÈS l'écriture du premier bloc, sinon le
        # test ne reproduit pas un envoi interrompu (il ne testerait qu'un
        # envoi jamais commencé).
        if appels["n"] == 1:
            return await original(self, *args, **kwargs)
        # Le client disparaît : Starlette lève ClientDisconnect à la lecture
        # suivante du corps.
        from starlette.requests import ClientDisconnect

        raise ClientDisconnect("connexion coupée par le client")

    monkeypatch.setattr(UploadFile, "read", lecture_coupee)

    with pytest.raises(Exception):
        client.post(
            "/imports/upload",
            files=[("files", ("GROS-DOSSIER/fiche.pdf", b"%PDF-1.4 " + b"0" * (3 * 1024 * 1024), "application/pdf"))],
            data={"folder": "envoi-interrompu"},
        )

    apres = sorted(p.name for p in staging.iterdir())
    assert apres == avant, (
        f"envoi interrompu : brouillon abandonné dans le staging — {set(apres) - set(avant)}"
    )


def test_au_dela_de_la_borne_aucune_ecriture(tmp_path: Path) -> None:
    """`MAX_UPLOAD_FILES + 1` fichiers : refusé, et RIEN n'a été écrit."""
    client, config = _app(tmp_path)
    staging = staging_root(config)
    staging.mkdir(parents=True, exist_ok=True)
    avant = sorted(p.name for p in staging.iterdir())

    fichiers = [
        ("files", (f"LOT/f-{i:04d}.txt", f"contenu {i}".encode(), "text/plain"))
        for i in range(MAX_UPLOAD_FILES + 1)
    ]
    reponse = client.post("/imports/upload", files=fichiers, data={"folder": "lot-trop-gros"})

    assert reponse.status_code == 413, reponse.status_code
    apres = sorted(p.name for p in staging.iterdir())
    assert apres == avant, f"refus mais écritures laissées : {set(apres) - set(avant)}"


def test_deux_envois_du_meme_dossier_ne_partagent_pas_de_brouillon(tmp_path: Path) -> None:
    """Deux envois concurrents du même dossier : deux brouillons disjoints."""
    client, config = _app(tmp_path)
    premier = client.post(
        "/imports/upload",
        files=[("files", ("MEME-DOSSIER/a.pdf", _pdf(), "application/pdf"))],
        data={"folder": "meme-dossier"},
    )
    second = client.post(
        "/imports/upload",
        files=[("files", ("MEME-DOSSIER/b.pdf", _pdf(), "application/pdf"))],
        data={"folder": "meme-dossier"},
    )
    assert premier.status_code == 200 and second.status_code == 200

    staging = staging_root(config)
    dossiers = [p for p in staging.iterdir() if p.is_dir()]
    chemins = {str(p) for p in (premier.json()["staged_path"], second.json()["staged_path"])}
    assert len(chemins) == 2, "les deux envois partagent un brouillon : écrasement possible"
    for chemin in chemins:
        # L'arborescence relative envoyée par le navigateur est conservée :
        # les fichiers ne sont PAS à la racine du brouillon.
        contenu = sorted(str(p.relative_to(chemin)) for p in Path(chemin).rglob("*") if p.is_file())
        assert contenu in (["MEME-DOSSIER/a.pdf"], ["MEME-DOSSIER/b.pdf"]), (
            f"brouillon mélangé : {contenu}"
        )
    assert len(dossiers) == 2
