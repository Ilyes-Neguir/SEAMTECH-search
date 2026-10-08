"""Conflits de destination d'un envoi groupé — constat A05 (audit du 2026-10-08).

Défaut reproduit par la revue : deux fichiers nommés ``same.txt`` dans le MÊME
envoi étaient acceptés (200), écrits l'un après l'autre au même chemin — le
second écrasait le premier — et l'API répondait 200. L'opérateur croyait avoir
déposé deux pièces ; il n'en restait qu'une, sans aucun signal.

Ces tests éprouvent le comportement CORRIGÉ, par HTTP réel :

1. l'envoi est REFUSÉ (409 ``collision_chemins``) avec la liste des conflits,
   AVANT toute écriture : le premier fichier n'a pas non plus été écrit ;
2. l'événement d'audit est posé avec le statut 409 (traçabilité de l'incident) ;
3. un dossier vide n'est pas laissé derrière (rien n'a été créé) ;
4. les familles voisines sont couvertes : ``a`` vs ``a/b.txt`` (fichier contre
   dossier), différence de casse, noms rendus identiques par l'assainissement ;
5. un envoi SAIN passe toujours, y compris avec des sous-dossiers — la garde ne
   doit pas casser le parcours nominal (dépôt d'un dossier complet).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest


def _client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "search.db", min_free_bytes=0)
    return TestClient(create_app(config)), config


def _envoi(entrees: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, io.BytesIO, str]]]:
    """Champ multipart ``files`` (liste) — autant d'entrées que de fichiers."""
    return [("files", (nom, io.BytesIO(contenu), "application/octet-stream")) for nom, contenu in entrees]


def _upload(client, entrees: list[tuple[str, bytes]], folder: str = "envoi"):
    return client.post(
        "/imports/upload",
        files=_envoi(entrees),
        data={"folder": folder},
    )


def config_database(config):
    """Connexion directe à la base de configuration (lecture d'audit)."""
    import sqlite3

    return sqlite3.connect(config.database_path)


def _staging_dir(config) -> Path:
    from seamtech_search.import_pipeline import staging_root

    return staging_root(config)


def test_a05_deux_memes_noms_refuses_et_rien_ecrit(tmp_path: Path) -> None:
    """Le constat A05, tel quel : deux ``same.txt`` — refus, aucune écriture."""
    client, config = _client(tmp_path)
    reponse = _upload(client, [("same.txt", b"premier"), ("same.txt", b"second")])
    assert reponse.status_code == 409, reponse.text
    detail = reponse.json()["detail"]
    assert detail["code"] == "collision_chemins", detail
    assert detail["collisions"], detail
    assert detail["collisions"][0]["type"] == "doublon"
    assert "same.txt" in detail["collisions"][0]["chemins"]

    # RIEN n'a été écrit : ni le second fichier (écrasement), ni le premier.
    racine = _staging_dir(config)
    ecrits = [chemin for chemin in racine.rglob("*") if chemin.is_file()] if racine.exists() else []
    assert ecrits == [], f"aucun fichier ne doit être écrit : {ecrits}"

    # L'incident est TRACÉ (l'exploitant peut le constater après coup) : on lit
    # directement la table d'audit — pas d'API de lecture dédiée.
    import json as _json

    connexion = config_database(config)
    try:
        # NB : ``sqlite3.Cursor`` n'est pas un gestionnaire de contexte — on
        # ferme explicitement (le test échouerait sinon sur un TypeError).
        cursor = connexion.cursor()
        cursor.execute(
            "SELECT status, details FROM audit_log WHERE action = 'import_upload' ORDER BY timestamp DESC LIMIT 5"
        )
        evenements = [
            (str(ligne[0]), _json.loads(ligne[1]) if isinstance(ligne[1], str) else dict(ligne[1] or {}))
            for ligne in cursor.fetchall()
        ]
        cursor.close()
    finally:
        connexion.close()
    refus = [details for statut, details in evenements if statut == "409"]
    assert refus, evenements
    assert refus[0].get("collisions"), refus[0]


def test_a05_fichier_contre_dossier_refuse(tmp_path: Path) -> None:
    """``a`` (fichier) et ``a/b.txt`` : la destination ne peut pas être les deux."""
    client, config = _client(tmp_path)
    reponse = _upload(client, [("a", b"fichier"), ("a/b.txt", b"sous-dossier")])
    assert reponse.status_code == 409, reponse.text
    types = {conflit["type"] for conflit in reponse.json()["detail"]["collisions"]}
    assert "fichier_vs_dossier" in types, reponse.text
    racine = _staging_dir(config)
    assert not racine.exists() or not [c for c in racine.rglob("*") if c.is_file()]


def test_a05_casse_et_assainissement_refuses(tmp_path: Path) -> None:
    """``Rapport.pdf`` vs ``rapport.pdf`` : le même fichier selon l'hôte — refusé partout."""
    client, _config = _client(tmp_path)
    reponse = _upload(client, [("Rapport.pdf", b"a"), ("rapport.pdf", b"b")])
    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["collisions"][0]["type"] == "doublon"

    # Deux noms distincts mais rendus identiques par l'assainissement du chemin.
    client, _config = _client(tmp_path / "second")
    reponse = _upload(client, [("compte:rendu.txt", b"a"), ("compte?rendu.txt", b"b")])
    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["collisions"][0]["type"] == "doublon"


def test_a05_envoi_sain_avec_sous_dossiers_passe(tmp_path: Path) -> None:
    """La garde ne casse pas le parcours nominal : un dossier complet passe intact."""
    client, config = _client(tmp_path)
    reponse = _upload(
        client,
        [
            ("dossier/fiche.pdf", b"%PDF-1.4 contenu"),
            ("dossier/annexe/notes.txt", b"notes"),
            ("dossier/photos/voile.jpg", b"\xff\xd8image"),
            ("lisez-moi.txt", b"merci"),
        ],
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps.get("staged_path"), corps

    ecrits = sorted(
        str(chemin.relative_to(config.database_path.parent)) for chemin in Path(corps["staged_path"]).rglob("*")
        if chemin.is_file()
    )
    assert len(ecrits) == 4, ecrits
    assert any(chemin.endswith("dossier/fiche.pdf") for chemin in ecrits), ecrits
    assert any(chemin.endswith("dossier/photos/voile.jpg") for chemin in ecrits), ecrits
    # Contenu intact : le fichier de sous-dossier n'a pas été tronqué.
    assert (Path(corps["staged_path"]) / "dossier/annexe/notes.txt").read_bytes() == b"notes"


def test_a05_trois_doublons_listes_en_entier(tmp_path: Path) -> None:
    """La liste est COMPLÈTE : l'opérateur renomme en une fois, pas de devinette."""
    client, _config = _client(tmp_path)
    reponse = _upload(
        client,
        [
            ("x.txt", b"1"),
            ("x.txt", b"2"),
            ("x.txt", b"3"),
            ("y/fiche.pdf", b"a"),
            ("y/fiche.pdf", b"b"),
        ],
    )
    assert reponse.status_code == 409, reponse.text
    collisions = reponse.json()["detail"]["collisions"]
    # Une entrée par ÉCRASEMENT évité : les 2e et 3e ``x.txt``, et le 2e ``y/fiche.pdf``.
    cles = sorted(conflit["cle"] for conflit in collisions)
    assert cles == ["x.txt", "x.txt", "y/fiche.pdf"], collisions
    assert {conflit["type"] for conflit in collisions} == {"doublon"}, collisions


@pytest.mark.parametrize(
    "nom",
    ["../evasion.txt", "sous/../../evasion.txt", "C:\\Windows\\system.ini", "/etc/passwd"],
)
def test_a05_les_chemins_hostiles_restent_confines(tmp_path: Path, nom: str) -> None:
    """Un nom hostile est assaini ET ne peut pas sortir du staging."""
    client, config = _client(tmp_path)
    reponse = _upload(client, [(nom, b"contenu")])
    assert reponse.status_code in {200, 409}, reponse.text
    if reponse.status_code != 200:
        return
    staged = Path(reponse.json()["staged_path"]).resolve()
    racine = _staging_dir(config).resolve()
    assert staged.is_relative_to(racine), (staged, racine)
    for chemin in staged.rglob("*"):
        assert chemin.resolve().is_relative_to(racine), chemin
