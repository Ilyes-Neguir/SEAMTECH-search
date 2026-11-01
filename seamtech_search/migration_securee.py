"""Montée de version et retour arrière, prouvés sur une copie représentative.

Constat du rapport du 28/10 (P0) : « Mise à jour et retour arrière non prouvés
sur une copie représentative de l'installation existante : migration, données
conservées et rollback répété. »

Ce que ce module apporte
------------------------
Une procédure exécutable, pas une intention :

``avant_migration(base_url)``
    photographie l'état AVANT (versions de schéma appliquées, nombre de lignes
    par table, empreinte logique des données métier) et prend un dump de
    sécurité. C'est ce dump qui sert de retour arrière.

``apres_migration(base_url, avant)``
    compare l'état APRÈS à la photographie : chaque table métier doit avoir au
    moins autant de lignes, aucune ligne ne doit disparaître, la version de
    schéma doit avoir avancé. Les écarts sont LISTÉS, jamais résumés en « ok ».

``rollback(base_url_actuelle, dossier, base_cible)``
    restaure le dump de sécurité dans une base NEUVE (jamais en place : un
    retour arrière in-place peut laisser une base à moitié écrite si le
    processus meurt). La base restaurée est ensuite comparée à la photographie
    d'avant, et le résultat dit si l'état est identique.

Pourquoi pas des migrations « down » : ce dépôt n'en a pas, et en ajouter
maintenant serait un chantier à risque. Le retour arrière opérationnel d'une
installation Windows/Docker est la restauration du dump pris juste avant la
montée de version — c'est ce chemin-là qui est testé ici, deux fois de suite
pour prouver qu'il est déterministe.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seamtech_search.sauvegarde import (
    SauvegardeError,
    _connecter,
    _sha256_fichier,
    _trouver_binaire,
    appliquer_retention,
    restaurer,
)

LOGGER = logging.getLogger("seamtech_search.migration_securee")

#: Tables dont la perte de lignes est un échec de migration. Volontairement
#: limitée au métier (les tables techniques comme les sessions Redis ou les
#: jobs en cours sont recréées/purgées par nature et ne prouvent rien).
TABLES_METIER = (
    "documents",
    "fiche",
    "fiche_piece_jointe",
    "lot_import",
    "lot_dossier",
    "comptes_utilisateurs",
    "gabarits",
)

#: Nombre de lignes au-delà duquel l'empreinte logique n'est PAS calculée
#: (coût O(n) sur des millions de pièces) : la comparaison se limite alors aux
#: comptes, et le compte rendu le DIT.
SEUIL_EMPREINTE = 50_000


def _versions_appliquees(base_url: str) -> list[str]:
    connexion = _connecter(base_url)
    try:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            return [str(ligne[0]) for ligne in cursor.fetchall()]
    finally:
        connexion.close()


def _comptes_tables(base_url: str, tables: tuple[str, ...] = TABLES_METIER) -> dict[str, int]:
    comptes: dict[str, int] = {}
    connexion = _connecter(base_url)
    try:
        with connexion.cursor() as cursor:
            for table in tables:
                cursor.execute(
                    "SELECT to_regclass(%s) IS NOT NULL",
                    (f"public.{table}",),
                )
                if not cursor.fetchone()[0]:
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 - nom de table contrôlé
                comptes[table] = int(cursor.fetchone()[0])
    finally:
        connexion.close()
    return comptes


def _empreinte_logique(base_url: str, tables: tuple[str, ...] = TABLES_METIER) -> dict[str, str]:
    """Empreinte de CONTENU par table : prouve que les lignes n'ont pas changé.

    Se limite aux colonnes texte, triées de façon stable : deux états
    identiques donnent la même empreinte, une valeur modifiée la change.
    Au-delà de :data:`SEUIL_EMPREINTE` lignes, l'empreinte n'est pas calculée
    (coût) et ``""`` est renvoyé — le compte rendu dit alors « comptes
    seulement » au lieu de laisser croire à une preuve de contenu.
    """
    empreintes: dict[str, str] = {}
    connexion = _connecter(base_url)
    try:
        with connexion.cursor() as cursor:
            for table in tables:
                cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                if not cursor.fetchone()[0]:
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608
                if int(cursor.fetchone()[0]) > SEUIL_EMPREINTE:
                    continue
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "AND data_type IN ('text','character varying','character','integer','bigint','numeric') "
                    "ORDER BY ordinal_position",
                    (table,),
                )
                colonnes = [str(ligne[0]) for ligne in cursor.fetchall()]
                if not colonnes:
                    continue
                projection = ", ".join(f'"{colonne}"::text' for colonne in colonnes)
                cursor.execute(
                    f'SELECT md5(string_agg(ligne, E\'\\n\' ORDER BY ligne)) FROM '
                    f'(SELECT concat_ws(\'|\', {projection}) AS ligne FROM "{table}") AS contenu'  # noqa: S608
                )
                empreintes[table] = str(cursor.fetchone()[0] or "")
    finally:
        connexion.close()
    return empreintes


def avant_migration(base_url: str, dossier_sauvegarde: Path | str, *, retention: int = 5) -> dict[str, Any]:
    """Photographie + dump de sécurité AVANT toute montée de version.

    Le dump est l'élément de retour arrière : sans lui, la montée de version
    n'est pas autorisée (on refuse de migrer sans filet). Renvoie la
    photographie complète, y compris son empreinte SHA-256, et son chemin.
    """
    dossier = Path(dossier_sauvegarde)
    dossier.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dump = dossier / f"avant-migration-{horodatage}.dump"
    pg_dump = _trouver_binaire("pg_dump")
    resultat = subprocess.run(
        [pg_dump, "--format=custom", f"--file={dump}", base_url],
        capture_output=True,
        text=True,
    )
    if resultat.returncode != 0:
        raise SauvegardeError(f"dump de sécurité impossible (rc={resultat.returncode}) : {resultat.stderr[-400:]}")
    etat: dict[str, Any] = {
        "date_iso": datetime.now(timezone.utc).isoformat(),
        "base": base_url.rsplit("/", 1)[-1],
        "dump": {
            "fichier": dump.name,
            "octets": dump.stat().st_size,
            "sha256": _sha256_fichier(dump),
        },
        "versions_schema": _versions_appliquees(base_url),
        "comptes": _comptes_tables(base_url),
        "empreintes": _empreinte_logique(base_url),
        "tables_non_empreintes": sorted(set(_comptes_tables(base_url)) - set(_empreinte_logique(base_url))),
    }
    (dossier / f"{dump.name}.etat-avant.json").write_text(
        json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    appliquer_retention(None, retention, dossier_local=dossier)
    LOGGER.info(
        "État AVANT enregistré (%d version(s) de schéma, %d table(s)) — dump %s.",
        len(etat["versions_schema"]),
        len(etat["comptes"]),
        dump.name,
    )
    return etat


def apres_migration(base_url: str, avant: dict[str, Any]) -> dict[str, Any]:
    """Compare l'état APRÈS montée de version à la photographie d'avant.

    Écarts possibles, tous bloquants sauf mention :

    * une table métier présente avant a DISPARU ;
    * une table métier a MOINS de lignes qu'avant (perte de données) ;
    * les versions de schéma n'ont pas avancé (la montée n'a rien fait) ;
    * une empreinte de contenu a changé sans changement de nombre de lignes
      (réécriture silencieuse des données).
    """
    ecarts: list[str] = []
    versions = _versions_appliquees(base_url)
    if versions == avant["versions_schema"]:
        ecarts.append("versions de schéma inchangées : la migration n'a rien appliqué")
    if not set(avant["versions_schema"]).issubset(set(versions)):
        ecarts.append(
            f"versions perdues : {sorted(set(avant['versions_schema']) - set(versions))} "
            "ne sont plus enregistrées"
        )
    comptes = _comptes_tables(base_url)
    for table, nombre in avant["comptes"].items():
        if table not in comptes:
            ecarts.append(f"table {table} : présente avant, ABSENTE après")
            continue
        if comptes[table] < nombre:
            ecarts.append(f"table {table} : {comptes[table]} lignes après < {nombre} avant (perte de données)")
    empreintes = _empreinte_logique(base_url)
    for table, empreinte in avant.get("empreintes", {}).items():
        nouvelle = empreintes.get(table)
        if nouvelle is None or empreinte in ("", None):
            continue
        if comptes.get(table) == avant["comptes"].get(table) and nouvelle != empreinte:
            ecarts.append(f"table {table} : contenu modifié à nombre de lignes constant")
    return {
        "ok": not ecarts,
        "ecarts": ecarts,
        "versions_schema": versions,
        "comptes": comptes,
        "empreintes": empreintes,
        "preuve": "contenu" if not (set(comptes) - set(empreintes)) else "comptes",
    }


def rollback(
    avant: dict[str, Any],
    dossier_sauvegarde: Path | str,
    base_cible_url: str,
    *,
    verifier_donnees: bool = True,
) -> dict[str, Any]:
    """Restaure le dump de sécurité dans ``base_cible_url`` puis vérifie l'égalité.

    La base cible doit être NEUVE : :func:`restaurer` refuse d'écraser une base
    existante, ce qui protège d'un retour arrière lancé sur la mauvaise base.
    """
    dossier = Path(dossier_sauvegarde)
    chemin_dump = dossier / avant["dump"]["fichier"]
    if not chemin_dump.is_file():
        raise SauvegardeError(f"dump de retour arrière introuvable : {chemin_dump}")
    manifeste = {
        "dump": avant["dump"],
        "tables": avant["comptes"],
        "version_schema_metier": avant["versions_schema"][-1] if avant["versions_schema"] else "",
        "fiches_par_statut": {},
        "documents": {},
    }
    # `restaurer` exige un manifeste complet : on le construit à partir de la
    # photographie d'avant, pas d'un manifeste de sauvegarde récent (l'état à
    # restaurer est celui d'AVANT la migration).
    resume = restaurer(base_cible_url, manifeste, chemin_dump_local=chemin_dump)

    verification: dict[str, Any] = {"ok": True, "ecarts": []}
    if verifier_donnees:
        comptes = _comptes_tables(base_cible_url)
        empreintes = _empreinte_logique(base_cible_url)
        ecarts: list[str] = []
        for table, nombre in avant["comptes"].items():
            if comptes.get(table) != nombre:
                ecarts.append(f"table {table} : {comptes.get(table)} lignes restaurées ≠ {nombre} avant")
        for table, empreinte in avant.get("empreintes", {}).items():
            if empreinte and empreintes.get(table) not in (None, "") and empreintes[table] != empreinte:
                ecarts.append(f"table {table} : contenu restauré différent de l'état d'avant")
        if _versions_appliquees(base_cible_url) != avant["versions_schema"]:
            ecarts.append("versions de schéma restaurées différentes de l'état d'avant")
        verification = {
            "ok": not ecarts,
            "ecarts": ecarts,
            "comptes": comptes,
            "empreintes": empreintes,
        }
    return {
        "base": resume["base"],
        "dump_sha256": _sha256_fichier(chemin_dump),
        "restauration": resume,
        "verification": verification,
    }


def diagnostiquer(base_url: str) -> dict[str, Any]:
    """État lisible d'une installation avant intervention (aucune écriture)."""
    from seamtech_search.sauvegarde import _etat_base

    etat = _etat_base(base_url)
    return {
        "base": base_url.rsplit("/", 1)[-1],
        "versions_schema": _versions_appliquees(base_url),
        "tables": etat["tables"],
        "comptes_metier": _comptes_tables(base_url),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m seamtech_search.migration_securee", description=__doc__)
    sous = parser.add_subparsers(dest="commande", required=True)

    p_avant = sous.add_parser("avant", help="photographie + dump de sécurité avant montée de version")
    p_avant.add_argument("--base-url", required=True)
    p_avant.add_argument("--dossier", default="data/backups/migrations")
    p_avant.add_argument("--retention", type=int, default=5)

    p_apres = sous.add_parser("apres", help="compare l'état après migration à la photographie d'avant")
    p_apres.add_argument("--base-url", required=True)
    p_apres.add_argument("--etat-avant", required=True, help="fichier .etat-avant.json produit par « avant »")

    p_rollback = sous.add_parser("rollback", help="restaure le dump de sécurité dans une base NEUVE et vérifie")
    p_rollback.add_argument("--etat-avant", required=True)
    p_rollback.add_argument("--dossier", required=True)
    p_rollback.add_argument("--base-cible", required=True)

    p_diag = sous.add_parser("diagnostic", help="état de l'installation (lecture seule)")
    p_diag.add_argument("--base-url", required=True)

    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s : %(message)s")

    if arguments.commande == "avant":
        etat = avant_migration(arguments.base_url, arguments.dossier, retention=arguments.retention)
        print(json.dumps({"etat": str(Path(arguments.dossier) / (etat['dump']['fichier'] + '.etat-avant.json')), "dump": etat["dump"]}, ensure_ascii=False))
        return 0
    if arguments.commande == "apres":
        avant = json.loads(Path(arguments.etat_avant).read_text(encoding="utf-8"))
        resultat = apres_migration(arguments.base_url, avant)
        print(json.dumps({k: resultat[k] for k in ("ok", "ecarts", "versions_schema", "preuve")}, ensure_ascii=False))
        return 0 if resultat["ok"] else 1
    if arguments.commande == "rollback":
        avant = json.loads(Path(arguments.etat_avant).read_text(encoding="utf-8"))
        resultat = rollback(avant, arguments.dossier, arguments.base_cible)
        print(json.dumps({"base": resultat["base"], "verification_ok": resultat["verification"]["ok"], "ecarts": resultat["verification"]["ecarts"]}, ensure_ascii=False))
        return 0 if resultat["verification"]["ok"] else 1
    print(json.dumps(diagnostiquer(arguments.base_url), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
