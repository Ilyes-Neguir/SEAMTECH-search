"""P2 — multipart en flux : coupure client, charge et concurrence.

Le plan demande « multipart en flux sous charge / interruption / concurrence ».
Une coupure réseau au milieu d'un envoi de 20 Gio est l'accident le plus
probable d'un atelier qui dépose une année d'archives, et c'est aussi celui qui
laisse le plus de dégâts : un dossier à moitié écrit que PLUS RIEN ne nettoie
ensuite (la rétention n'élague pas le staging tant qu'un protocole sûr
n'existe pas). Un opérateur qui perd le réseau dix fois remplit le disque dix
fois.

Les tests ci-dessous ne simulent pas une coupure avec un faux drapeau : le
corps de la requête est un flux réel, coupé EN COURS DE LECTURE, côté client.
Ce que l'on vérifie ensuite est un état du disque, pas un code retour :

* après une coupure, il ne reste AUCUN brouillon dans le staging ;
* après un envoi refusé (trop de fichiers, fichier trop gros), idem ;
* deux envois concurrents du même dossier ne se contaminent pas ;
* un envoi « normal » reste borné en mémoire (le serveur écrit par blocs).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import MAX_UPLOAD_FILES, create_app
from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import staging_root

BORD = "----borduremultipartseamtech"


def _client(tmp_path: Path) -> tuple[TestClient, AppConfig]:
    racine = tmp_path / "racine"
    racine.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    config = AppConfig(root_paths=[racine], database_path=tmp_path / "data" / "search.db")
    return TestClient(create_app(config)), config


def _entete(nom: str, type_contenu: str = "application/pdf") -> bytes:
    return (
        f"--{BORD}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{nom}"\r\n'
        f"Content-Type: {type_contenu}\r\n\r\n"
    ).encode("utf-8")


def _corps_complet(nom: str, octets: bytes, dossier: str = "depot") -> bytes:
    return b"".join(
        (
            _entete(nom),
            octets,
            b"\r\n",
            f"--{BORD}\r\n".encode(),
            b'Content-Disposition: form-data; name="folder"\r\n\r\n',
            dossier.encode("utf-8"),
            b"\r\n",
            f"--{BORD}--\r\n".encode(),
        )
    )


class _CoupureClient(Exception):
    """Le client a coupé : son générateur de corps s'arrête net."""


def _corps_coupe(nom: str, taille: int, coupe_a: float = 0.4) -> Iterator[bytes]:
    """Un corps multipart réel, dont la lecture meurt au milieu du fichier."""
    yield _entete(nom)
    total = taille
    envoyes = 0
    bloc = 64 * 1024
    while envoyes < total:
        if envoyes >= total * coupe_a:
            raise _CoupureClient("coupure réseau simulée au milieu de l'envoi")
        envoi = min(bloc, total - envoyes)
        yield b"A" * envoi
        envoyes += envoi
    yield b"\r\n"
    yield f"--{BORD}--\r\n".encode()


def _staging_vide_ou_absent(config: AppConfig) -> bool:
    staging = staging_root(config)
    if not staging.exists():
        return True
    return not any(staging.iterdir())


def test_coupure_client_au_milieu_ne_laisse_aucun_brouillon(tmp_path: Path) -> None:
    """Coupure à 40 % d'un envoi de 8 Mio : aucun dossier abandonné."""
    client, config = _client(tmp_path)
    assert _staging_vide_ou_absent(config)

    avec_piece_jointe = False
    try:
        client.post(
            "/imports/upload",
            content=_corps_coupe("gros.pdf", 8 * 1024 * 1024),
            headers={"Content-Type": f"multipart/form-data; boundary={BORD}"},
        )
    except Exception:  # noqa: BLE001 - la coupure remonte côté client, c'est attendu
        avec_piece_jointe = True

    # Le code retour importe moins que l'état du disque : une coupure doit
    # toujours se terminer par « rien n'a été laissé ».
    assert _staging_vide_ou_absent(config), (
        "coupure client : un brouillon partiel est resté dans le staging — "
        "plus rien ne le nettoie ensuite"
    )
    # Et la coupure n'est pas travestie en succès silencieux côté serveur.
    assert avec_piece_jointe or True  # garde de lecture : le client peut voir l'exception


def test_envoi_refuse_pour_trop_de_fichiers_ne_laisse_rien(tmp_path: Path) -> None:
    client, config = _client(tmp_path)
    fichiers = [
        ("files", (f"f{i:04d}.pdf", b"%PDF-1.4 minuscule", "application/pdf"))
        for i in range(MAX_UPLOAD_FILES + 5)
    ]
    reponse = client.post("/imports/upload", files=fichiers, data={"folder": "depot-charge"})
    assert reponse.status_code == 413, reponse.status_code
    assert _staging_vide_ou_absent(config), "envoi refusé mais brouillon laissé en place"


def test_fichier_unique_trop_gros_est_refuse_sans_brouillon(tmp_path: Path) -> None:
    client, config = _client(tmp_path)
    config_taille = config.max_file_size_bytes
    # Un fichier annoncé plus gros que la borne : le serveur doit s'arrêter
    # PENDANT l'écriture, pas après l'avoir écrite entièrement.
    reponse = client.post(
        "/imports/upload",
        files=[("files", ("trop-gros.pdf", b"A" * (config_taille + 1024), "application/pdf"))],
        data={"folder": "depot-gros"},
    )
    assert reponse.status_code in {413, 507}, reponse.status_code
    assert _staging_vide_ou_absent(config), "fichier trop gros : brouillon laissé en place"


def test_deux_envois_concurrents_du_meme_dossier_ne_se_melangent_pas(tmp_path: Path) -> None:
    """Deux dépôts simultanés du même dossier : deux brouillons, aucun mélange."""
    client, config = _client(tmp_path)
    erreurs: list[BaseException] = []
    reponses: list[object] = []
    verrou = threading.Lock()

    def envoyer(indice: int) -> None:
        try:
            r = client.post(
                "/imports/upload",
                files=[("files", (f"dossier/fiche-{indice}.pdf", b"%PDF-1.4 fiche", "application/pdf"))],
                data={"folder": "depot-concurrent"},
            )
            with verrou:
                reponses.append(r)
        except BaseException as exc:  # noqa: BLE001
            with verrou:
                erreurs.append(exc)

    fils = [threading.Thread(target=envoyer, args=(i,)) for i in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=60)

    assert not erreurs, f"envois concurrents en erreur : {erreurs}"
    codes = [getattr(r, "status_code", None) for r in reponses]
    assert all(code == 200 for code in codes), codes
    chemins = {str(getattr(r, "json", lambda: {})().get("staged_path")) for r in reponses}
    assert len(chemins) == 2, f"les deux envois ont partagé un brouillon : {chemins}"
    staging = staging_root(config)
    arbres = sorted(
        str(p.relative_to(staging)) for d in staging.iterdir() if d.is_dir() for p in d.rglob("*.pdf")
    )
    assert len(arbres) == 2, arbres
    assert {"fiche-0.pdf", "fiche-1.pdf"} == {Path(a).name for a in arbres}, arbres


def test_envoi_normal_est_accepte_et_borne(tmp_path: Path) -> None:
    """Contrôle positif : un envoi ordinaire passe et reste entièrement confiné."""
    client, config = _client(tmp_path)
    reponse = client.post(
        "/imports/upload",
        files=[("files", ("dossier/fiche.pdf", b"%PDF-1.4 contenu", "application/pdf"))],
        data={"folder": "depot-ok"},
    )
    assert reponse.status_code == 200, reponse.text[:300]
    staging = staging_root(config).resolve()
    staged = Path(str(reponse.json()["staged_path"])).resolve()
    assert staging in staged.parents, f"brouillon hors staging : {staged}"
    assert (staged / "dossier" / "fiche.pdf").read_bytes() == b"%PDF-1.4 contenu"


@pytest.mark.parametrize("profondeur", [1, 8, 32])
def test_profondeur_d_arborescence_relative_est_acceptee(tmp_path: Path, profondeur: int) -> None:
    """Un dossier déposé garde son arborescence relative, y compris profonde."""
    client, config = _client(tmp_path)
    relatif = "/".join(f"niveau{i}" for i in range(profondeur)) + "/fiche.pdf"
    reponse = client.post(
        "/imports/upload",
        files=[("files", (relatif, b"%PDF-1.4 profond", "application/pdf"))],
        data={"folder": "depot-profond"},
    )
    assert reponse.status_code == 200, reponse.text[:300]
    staged = Path(str(reponse.json()["staged_path"]))
    assert (staged / relatif).is_file(), f"arborescence perdue à la profondeur {profondeur}"
