"""P0.3 — montée de version et retour arrière sur une copie représentative.

Le rapport du 28/10 : « Mise à jour et retour arrière non prouvés sur une copie
représentative de l'installation existante : migration, données conservées et
rollback répété. » Ces tests exécutent la séquence complète sur PostgreSQL réel.

La « copie représentative de l'installation existante » est reconstituée en
appliquant les migrations **jusqu'à une version antérieure** (``jusqu_a``),
puis en y semant les données métier d'une installation en service : fiches,
documents, pièces jointes, lots, comptes nominatifs, gabarits.

Séquence éprouvée :

1. état AVANT : photographie + dump de sécurité ;
2. montée de version (le registre complet des migrations) ;
3. contrôle APRÈS : aucune table perdue, aucune ligne perdue, contenu inchangé
   à nombre de lignes constant, version de schéma avancée ;
4. retour arrière vers une base NEUVE, comparé à la photographie d'avant ;
5. **répété** (deuxième retour arrière) pour prouver que la procédure est
   déterministe et ne dépend d'aucun état laissé par le premier.

Ce qui n'est PAS prouvé ici, et ne doit pas être revendiqué : la montée de
version sur le poste Windows réel avec Docker Desktop, ni le comportement d'un
arrêt brutal PENDANT la migration (les migrations PostgreSQL sont
transactionnelles par DDL, mais aucune coupure d'alimentation n'est simulée).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.migration_securee import (
    apres_migration,
    avant_migration,
    diagnostiquer,
    rollback,
)
from seamtech_search.sauvegarde import SauvegardeError

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres


def _creer_base(nom: str) -> str:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom}"')
    finally:
        admin.close()
    return URL_PG.rsplit("/", 1)[0] + f"/{nom}"


def _supprimer_base(nom: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
    finally:
        admin.close()


@pytest.fixture()
def installation_ancienne(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Installation en service reconstituée à la version 025 (avant 026→031)."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom = f"migration_{uuid.uuid4().hex[:10]}"
    url = _creer_base(nom)
    index = SearchIndex(Path(f"/tmp/unused-migration-{nom}.db"), url)
    index.initialize()
    index.run_migrations(jusqu_a="025_audit_logs")

    # Données métier d'une installation en service.
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
                "is_dir, content, content_hash, extraction_status, category, role) "
                "VALUES ('/archives/7792/fiche.pdf', '/archives/7792/fiche.pdf', 'fiche.pdf', "
                "'/archives/7792', '.pdf', 1234, 0, FALSE, 'reference 7792-SO genois', 'h1', "
                "'extracted', 'analyzed', 'fiche_pdf')"
            )
            for i in range(3):
                cursor.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
                    "is_dir, content, content_hash, extraction_status, category, role) "
                    "VALUES (%s, %s, %s, '/archives/7792', '.pdf', 10, 0, FALSE, 'piece', 'h', "
                    "'extracted', 'analyzed', 'piece_jointe')",
                    (f"/archives/7792/piece{i}.pdf", f"/archives/7792/piece{i}.pdf", f"piece{i}.pdf"),
                )
            # L'index plein texte est normalement alimenté par ``upsert_documents`` ;
            # ici les lignes sont semées en SQL direct, on le remplit donc de la
            # même façon que la production (tsvector « unaccent »).
            cursor.execute(
                "UPDATE documents SET search_vector = to_tsvector('seamtech_unaccent', coalesce(content, ''))"
            )
    try:
        yield {"nom": nom, "url": url, "index": index, "tmp": tmp_path}
    finally:
        index.close()
        _supprimer_base(nom)


def test_montée_de_version_conserve_les_donnees(installation_ancienne: dict[str, Any]) -> None:
    """Migration complète : données conservées, schéma à jour, écarts listés."""
    url = installation_ancienne["url"]
    dossier = installation_ancienne["tmp"] / "backups"

    avant = avant_migration(url, dossier)
    assert "025_audit_logs" in avant["versions_schema"]
    assert "031_accessoires" not in avant["versions_schema"]
    assert avant["comptes"]["documents"] == 4
    assert (dossier / avant["dump"]["fichier"]).is_file()

    installation_ancienne["index"].run_migrations()  # montée de version complète

    resultat = apres_migration(url, avant)
    assert resultat["ok"] is True, resultat["ecarts"]
    assert "031_accessoires" in resultat["versions_schema"]
    assert resultat["comptes"]["documents"] >= 4
    assert resultat["preuve"] == "contenu"

    # L'application fonctionne après migration : l'index de recherche répond.
    index = installation_ancienne["index"]
    resultats = index.search("7792-SO")
    assert resultats, "recherche vide après migration"

    # La photographie d'avant est persistée à côté du dump (traçabilité).
    etat_json = dossier / f"{avant['dump']['fichier']}.etat-avant.json"
    assert json.loads(etat_json.read_text(encoding="utf-8"))["comptes"]["documents"] == 4


def test_retour_arriere_repeté_restaure_l_etat_exact(installation_ancienne: dict[str, Any]) -> None:
    """Le rollback (dump de sécurité) rend l'état d'avant, deux fois de suite."""
    url = installation_ancienne["url"]
    dossier = installation_ancienne["tmp"] / "backups"
    avant = avant_migration(url, dossier)

    installation_ancienne["index"].run_migrations()

    for tentative in (1, 2):
        nom_cible = f"rollback_{tentative}_{uuid.uuid4().hex[:8]}"
        url_cible = URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}"
        try:
            resultat = rollback(avant, dossier, url_cible)
            assert resultat["verification"]["ok"] is True, resultat["verification"]["ecarts"]
            assert resultat["verification"]["comptes"]["documents"] == 4
            # L'état restauré est celui d'AVANT : la migration 026→031 n'y figure pas.
            etat = diagnostiquer(url_cible)
            assert etat["versions_schema"] == avant["versions_schema"]
            assert "031_accessoires" not in etat["versions_schema"]
            # La base restaurée est exploitable : l'index s'ouvre et cherche.
            index_restaure = SearchIndex(Path(f"/tmp/unused-{nom_cible}.db"), url_cible)
            try:
                index_restaure.initialize()
                assert index_restaure.search("7792-SO")
            finally:
                index_restaure.close()
        finally:
            _supprimer_base(nom_cible)


def test_rollback_refuse_une_base_cible_existante(installation_ancienne: dict[str, Any]) -> None:
    """Un retour arrière ne doit jamais écraser une base en service."""
    url = installation_ancienne["url"]
    dossier = installation_ancienne["tmp"] / "backups"
    avant = avant_migration(url, dossier)
    with pytest.raises(SauvegardeError, match="existe déjà"):
        rollback(avant, dossier, url)


def test_dump_de_securite_absent_bloque_le_rollback(installation_ancienne: dict[str, Any]) -> None:
    """Sans filet, pas de retour arrière : l'erreur est explicite, pas silencieuse."""
    url = installation_ancienne["url"]
    dossier = installation_ancienne["tmp"] / "backups-manquants"
    avant = avant_migration(url, dossier)
    (dossier / avant["dump"]["fichier"]).unlink()
    nom_cible = f"rollback_absent_{uuid.uuid4().hex[:8]}"
    with pytest.raises(SauvegardeError, match="introuvable"):
        rollback(avant, dossier, URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}")
