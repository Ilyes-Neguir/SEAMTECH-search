"""Sauvegarde hors-site et restauration ÉPROUVÉE du système SEAMTECH (Lot H.1).

Ce module remplace les scripts PowerShell locaux (``scripts/backup_postgres.ps1``
& co, sans manifeste, sans envoi hors-site, sans test) par une sauvegarde
portable, testable et planifiable :

- ``sauver``  : ``pg_dump -Fc`` de la base + inventaire de l'archive EN
  LECTURE SEULE (l'archive n'est JAMAIS copiée ni modifiée — RG13 ; c'est son
  ÉTAT qui est enregistré : chemins relatifs + tailles + sha256, pour détecter
  toute perte ou altération), puis manifeste complet (date, taille et sha256
  du dump, VERSION_SCHEMA_METIER, comptes par table et par statut, nombre de
  documents, commit de l'application) et envoi hors-site via le client S3
  existant (R2 géré, y compris l'absence de versioning de bucket).
- ``restaurer`` : depuis le manifeste (local ou objet S3), re-téléchargement
  du dump, vérification de l'empreinte AVANT toute écriture, puis
  ``pg_restore`` dans une base neuve.
- ``verifier`` : une base restaurée est comparée au manifeste (comptes par
  table, version de schéma, inventaire de l'archive) — c'est la différence
  entre « sauvegarde configurée » et « base détruite reconstruite à
  l'identique, prouvé ».
- Rétention : N sauvegardes conservées hors-site (paramétrable), purge des
  plus anciennes, JAMAIS la dernière.
- Vérification après envoi : le dump est RE-LU depuis le bucket et son
  empreinte doit correspondre à celle du manifeste.

Usage :
    python -m seamtech_search.sauvegarde sauver --base-url URL \\
        --archive RACINE [RACINE ...] --dossier data/backups [--retention 5]
    python -m seamtech_search.sauvegarde restaurer --manifeste FICHIER --base-cible URL
    python -m seamtech_search.sauvegarde restaurer --cle-manifeste CLE --base-cible URL
    python -m seamtech_search.sauvegarde verifier --manifeste FICHIER --base-url URL \\
        --archive RACINE [RACINE ...]

La publication des résultats (CI) passe par ``SEAMTECH_SAUVEGARDE_JSON``
(fichier JSONL, une mesure par ligne — même mécanique que ``perf-latence``).
Les binaires ``pg_dump``/``pg_restore`` sont découverts via ``PATH``, puis
``/usr/lib/postgresql/*/bin``, puis ``SEAMTECH_PG_BINDIR``.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg2

from .schema_metier import VERSION_SCHEMA_METIER

logger = logging.getLogger("seamtech_search.sauvegarde")

#: Préfixe des objets de sauvegarde dans le bucket (dump + manifeste).
PREFIXE_SAUVEGARDES = "backups/seamtech-search-"

#: Taille des blocs de lecture pour les empreintes (fichiers volumineux).
TAILLE_BLOC_SHA256 = 1024 * 1024


class SauvegardeError(Exception):
    """Échec explicite d'une sauvegarde, d'une restauration ou d'une vérification."""


def _publier(mesure: dict) -> None:
    """Ajoute une mesure au fichier JSONL de publication (CI), si demandé."""
    chemin = os.environ.get("SEAMTECH_SAUVEGARDE_JSON")
    if not chemin:
        return
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(mesure, ensure_ascii=False) + "\n")


def _sha256_fichier(chemin: Path) -> str:
    hacheur = hashlib.sha256()
    with open(chemin, "rb") as f:
        while bloc := f.read(TAILLE_BLOC_SHA256):
            hacheur.update(bloc)
    return hacheur.hexdigest()


def _repertoire_binaires_pgserver() -> Path | None:
    """Dernier recours de découverte : l'installation PostgreSQL embarquée du
    paquet de développement ``pgserver`` (sandbox/tests). Optionnel — en
    production, pg_dump/pg_restore viennent du système."""
    try:
        import pgserver
    except ImportError:
        return None
    bindir = Path(pgserver.__file__).parent / "pginstall" / "bin"
    return bindir if bindir.is_dir() else None


def _trouver_binaire(nom: str) -> str:
    """Découvre pg_dump/pg_restore : PATH, puis SEAMTECH_PG_BINDIR, puis les
    installations PostgreSQL système, puis pgserver (développement). Jamais de
    repli silencieux : sans binaire, l'échec est explicite."""
    direct = shutil.which(nom)
    if direct:
        return direct
    bindir_env = os.environ.get("SEAMTECH_PG_BINDIR")
    if bindir_env:
        candidat = Path(bindir_env) / nom
        if candidat.exists():
            return str(candidat)
    for candidat in sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{nom}"), reverse=True):
        return candidat
    bindir_pgserver = _repertoire_binaires_pgserver()
    if bindir_pgserver is not None and (bindir_pgserver / nom).exists():
        return str(bindir_pgserver / nom)
    raise SauvegardeError(
        f"binaire « {nom} » introuvable (PATH, SEAMTECH_PG_BINDIR, /usr/lib/postgresql/*/bin) — "
        "installez postgresql-client ou pointez SEAMTECH_PG_BINDIR vers le bon répertoire"
    )


def _connecter(base_url: str, *, autocommit: bool = False):
    connexion = psycopg2.connect(base_url)
    if autocommit:
        connexion.autocommit = True
    return connexion


def _url_sans_base(base_url: str) -> str:
    """URL libpq vers le serveur SANS la base (pour CREATE/DROP DATABASE)."""
    parse = urlparse(base_url)
    return parse._replace(path="/postgres").geturl()


def _nom_base(base_url: str) -> str:
    nom = urlparse(base_url).path.lstrip("/")
    if not nom:
        raise SauvegardeError(f"URL sans nom de base : {base_url!r}")
    return nom


def _remplacer_nom_base(base_url: str, nouveau_nom: str) -> str:
    parse = urlparse(base_url)
    return parse._replace(path=f"/{nouveau_nom}").geturl()


# ---------------------------------------------------------------------------
# Inventaire de l'archive (LECTURE SEULE — RG13 : jamais d'écriture dedans)
# ---------------------------------------------------------------------------

def inventorier_archive(racines: list[Path]) -> dict:
    """État de l'archive : chemins relatifs + tailles + sha256, sans AUCUNE
    écriture dans les racines (fichiers ouverts en lecture seule). Miroir des
    primitives de ``scripts/inventaire_archive.py`` (Phase 0) limité à ce dont
    une sauvegarde a besoin : détecter toute perte ou altération."""
    fichiers: list[dict] = []
    octets_total = 0
    for racine in racines:
        racine = Path(racine).resolve()
        if not racine.is_dir():
            raise SauvegardeError(f"racine d'archive introuvable : {racine}")
        for chemin in sorted(racine.rglob("*")):
            if not chemin.is_file():
                continue
            taille = chemin.stat().st_size
            octets_total += taille
            fichiers.append(
                {
                    "chemin": str(chemin.relative_to(racine)),
                    "racine": str(racine),
                    "taille": taille,
                    "sha256": _sha256_fichier(chemin),
                }
            )
    return {"racines": [str(Path(r).resolve()) for r in racines], "nb_fichiers": len(fichiers), "octets_total": octets_total, "fichiers": fichiers}


def _compter_tables(connexion) -> dict[str, int]:
    comptes: dict[str, int] = {}
    with connexion.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        tables = [ligne[0] for ligne in cursor.fetchall()]
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            comptes[table] = int(cursor.fetchone()[0])
    return comptes


def _versions_schema(connexion) -> list[str]:
    with connexion.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [ligne[0] for ligne in cursor.fetchall()]


def _etat_base(base_url: str) -> dict:
    """Comptes par table, fiches par statut, documents, versions de schéma."""
    connexion = _connecter(base_url)
    try:
        tables = _compter_tables(connexion)
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut, COUNT(*) FROM fiche GROUP BY statut ORDER BY statut")
            fiches_par_statut = {statut: int(n) for statut, n in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) FROM documents")
            documents = int(cursor.fetchone()[0])
        return {
            "tables": tables,
            "fiches_par_statut": fiches_par_statut,
            "documents": documents,
            "versions_schema": _versions_schema(connexion),
        }
    finally:
        connexion.close()


def _commit_courant() -> str:
    """Commit git de l'application, ou « inconnu » (jamais d'échec pour ça)."""
    try:
        sortie = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=Path(__file__).resolve().parent,
        )
        if sortie.returncode == 0 and sortie.stdout.strip():
            return sortie.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "inconnu"


# ---------------------------------------------------------------------------
# Sauvegarde
# ---------------------------------------------------------------------------

def sauver(
    base_url: str,
    racines_archive: list[Path],
    dossier_local: Path,
    client_s3=None,
    retention: int = 5,
) -> dict:
    """Sauvegarde complète : dump + inventaire archive + manifeste, puis envoi
    hors-site si ``client_s3`` est fourni (avec re-lecture de vérification) et
    rétention. Renvoie le manifeste enrichi des clés S3."""
    debut = time.perf_counter()
    dossier_local = Path(dossier_local)
    dossier_local.mkdir(parents=True, exist_ok=True)

    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    nom_dump = f"seamtech-search-{horodatage}.dump"
    chemin_dump = dossier_local / nom_dump

    pg_dump = _trouver_binaire("pg_dump")
    etat = _etat_base(base_url)
    if VERSION_SCHEMA_METIER not in etat["versions_schema"]:
        raise SauvegardeError(
            f"la base n'est pas à jour ({VERSION_SCHEMA_METIER} absent de schema_migrations) — "
            "refus de sauvegarder un état non migré"
        )
    resultat = subprocess.run([pg_dump, "--format=custom", f"--file={chemin_dump}", base_url], capture_output=True, text=True)
    if resultat.returncode != 0:
        raise SauvegardeError(f"pg_dump a échoué (rc={resultat.returncode}) : {resultat.stderr[-800:]}")

    inventaire = inventorier_archive(racines_archive)
    manifeste = {
        "date_iso": datetime.now(timezone.utc).isoformat(),
        "commit": _commit_courant(),
        "version_schema_metier": VERSION_SCHEMA_METIER,
        "dump": {"fichier": nom_dump, "octets": chemin_dump.stat().st_size, "sha256": _sha256_fichier(chemin_dump)},
        "tables": etat["tables"],
        "fiches_par_statut": etat["fiches_par_statut"],
        "documents": etat["documents"],
        "archive": inventaire,
    }
    chemin_manifeste = dossier_local / f"{nom_dump}.manifest.json"
    chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")

    if client_s3 is not None:
        cle_dump = client_s3.upload_file(chemin_dump, f"{PREFIXE_SAUVEGARDES}{horodatage}.dump", avoid_overwrite=True)
        # Vérification APRÈS envoi : le dump est re-lu depuis le bucket et son
        # empreinte DOIT correspondre au manifeste (jamais de confiance aveugle).
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            relectue = Path(tmp) / nom_dump
            client_s3.download_file(cle_dump, relectue)
            empreinte_relue = _sha256_fichier(relectue)
        if empreinte_relue != manifeste["dump"]["sha256"]:
            raise SauvegardeError(
                f"vérification après envoi ÉCHOUÉE : dump relu {empreinte_relue[:12]}… ≠ "
                f"manifeste {manifeste['dump']['sha256'][:12]}…"
            )
        manifeste["dump"]["cle_s3"] = cle_dump
        cle_manifeste = client_s3.upload_file(
            chemin_manifeste, f"{PREFIXE_SAUVEGARDES}{horodatage}.dump.manifest.json", avoid_overwrite=True
        )
        manifeste["manifeste_cle_s3"] = cle_manifeste
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
        purges = appliquer_retention(client_s3, retention)
        manifeste["retention_purgees"] = purges
        _publier(
            {
                "operation": "sauver",
                "duree_s": round(time.perf_counter() - debut, 2),
                "dump_octets": manifeste["dump"]["octets"],
                "archive_fichiers": inventaire["nb_fichiers"],
                "verifiee_apres_envoi": True,
                "retention_purgees": len(purges),
            }
        )
    else:
        _publier(
            {
                "operation": "sauver",
                "duree_s": round(time.perf_counter() - debut, 2),
                "dump_octets": manifeste["dump"]["octets"],
                "archive_fichiers": inventaire["nb_fichiers"],
                "verifiee_apres_envoi": False,
            }
        )
    return manifeste


def appliquer_retention(client_s3, conserver: int) -> list[str]:
    """Purge hors-site : garde les ``conserver`` sauvegardes les plus récentes,
    supprime les plus anciennes — JAMAIS la dernière. Une sauvegarde = la paire
    (dump, manifeste). Renvoie les clés supprimées."""
    cles = client_s3.list_keys(PREFIXE_SAUVEGARDES)
    dumps = [c for c in cles if c.endswith(".dump")]
    dumps_tries = sorted(dumps)  # horodatage dans la clé ⇒ ordre chronologique
    a_purger = dumps_tries[:-conserver] if conserver >= 1 else []
    supprimees: list[str] = []
    for cle in a_purger:
        if client_s3.delete_file(cle):
            supprimees.append(cle)
        cle_manifeste = f"{cle}.manifest.json"
        if any(c == cle_manifeste for c in cles) and client_s3.delete_file(cle_manifeste):
            supprimees.append(cle_manifeste)
    return supprimees


# ---------------------------------------------------------------------------
# Restauration
# ---------------------------------------------------------------------------

def restaurer(
    base_cible_url: str,
    manifeste: dict,
    chemin_dump_local: Path | None = None,
    client_s3=None,
) -> dict:
    """Restaure le dump du manifeste dans ``base_cible_url`` (base créée si
    absente). L'empreinte du dump est vérifiée AVANT toute écriture. Renvoie
    un résumé (durée, comptes restaurés)."""
    debut = time.perf_counter()

    if chemin_dump_local is None:
        if client_s3 is None or "cle_s3" not in manifeste.get("dump", {}):
            raise SauvegardeError("aucun dump local fourni et pas de clé S3 dans le manifeste")
        chemin_dump_local = Path(os.environ.get("SEAMTECH_SAUVEGARDE_TMP", "data/backups")) / manifeste["dump"]["fichier"]
        chemin_dump_local.parent.mkdir(parents=True, exist_ok=True)
        client_s3.download_file(manifeste["dump"]["cle_s3"], chemin_dump_local)

    empreinte = _sha256_fichier(Path(chemin_dump_local))
    if empreinte != manifeste["dump"]["sha256"]:
        raise SauvegardeError(
            f"empreinte du dump {empreinte[:12]}… ≠ manifeste {manifeste['dump']['sha256'][:12]}… — "
            "restauration REFUSÉE avant toute écriture"
        )

    nom_cible = _nom_base(base_cible_url)
    connexion_admin = _connecter(_url_sans_base(base_cible_url), autocommit=True)
    try:
        with connexion_admin.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', (nom_cible,))
            if cursor.fetchone():
                raise SauvegardeError(
                    f"la base cible « {nom_cible} » existe déjà — restauration refusée "
                    "(choisissez une base neuve ou supprimez-la explicitement)"
                )
            cursor.execute(f'CREATE DATABASE "{nom_cible}"')
    finally:
        connexion_admin.close()

    pg_restore = _trouver_binaire("pg_restore")
    resultat = subprocess.run(
        [pg_restore, "--no-owner", "--exit-on-error", f"--dbname={base_cible_url}", str(chemin_dump_local)],
        capture_output=True,
        text=True,
    )
    if resultat.returncode != 0:
        raise SauvegardeError(f"pg_restore a échoué (rc={resultat.returncode}) : {resultat.stderr[-800:]}")

    etat = _etat_base(base_cible_url)
    duree = time.perf_counter() - debut
    _publier(
        {
            "operation": "restaurer",
            "duree_s": round(duree, 2),
            "base": nom_cible,
            "fiches": sum(etat["fiches_par_statut"].values()),
            "dump_octets": manifeste["dump"]["octets"],
        }
    )
    return {"duree_s": duree, "base": nom_cible, "etat": etat}


def charger_manifeste(chemin: Path | None, cle: str | None, client_s3) -> dict:
    """Manifeste depuis un fichier local OU une clé S3 (téléchargement)."""
    if chemin is not None:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    if cle is None or client_s3 is None:
        raise SauvegardeError("fournir --manifeste (local) ou --cle-manifeste avec un client S3 configuré")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "manifeste.json"
        client_s3.download_file(cle, destination)
        return json.loads(destination.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Vérification : la base restaurée correspond-elle au manifeste ?
# ---------------------------------------------------------------------------

def verifier(base_url: str, manifeste: dict, racines_archive: list[Path] | None = None) -> dict:
    """Compare la base vivante au manifeste : comptes par table, version de
    schéma, fiches par statut, documents, et (si racines fournies) inventaire
    de l'archive. Renvoie {ok, ecarts:[…]} — les écarts sont listés, jamais
    masqués."""
    ecarts: list[str] = []
    etat = _etat_base(base_url)

    for table, attendu in manifeste["tables"].items():
        restaure = etat["tables"].get(table)
        if restaure != attendu:
            ecarts.append(f"table {table} : {restaure} lignes restaurées ≠ {attendu} au manifeste")
    for table in etat["tables"]:
        if table not in manifeste["tables"]:
            ecarts.append(f"table {table} : présente en base, absente du manifeste")

    if manifeste["version_schema_metier"] not in etat["versions_schema"]:
        ecarts.append(
            f"VERSION_SCHEMA_METIER {manifeste['version_schema_metier']} absente de schema_migrations"
        )
    if etat["fiches_par_statut"] != manifeste["fiches_par_statut"]:
        ecarts.append(
            f"fiches par statut : restauré {etat['fiches_par_statut']} ≠ manifeste {manifeste['fiches_par_statut']}"
        )
    if etat["documents"] != manifeste["documents"]:
        ecarts.append(f"documents : restauré {etat['documents']} ≠ manifeste {manifeste['documents']}")

    if racines_archive:
        inventaire_vivant = inventorier_archive(racines_archive)
        attendu_archive = manifeste["archive"]
        if inventaire_vivant["nb_fichiers"] != attendu_archive["nb_fichiers"]:
            ecarts.append(
                f"archive : {inventaire_vivant['nb_fichiers']} fichiers vivants ≠ "
                f"{attendu_archive['nb_fichiers']} au manifeste"
            )
        vivants = {(f["racine"], f["chemin"]): f["sha256"] for f in inventaire_vivant["fichiers"]}
        manifestes = {(f["racine"], f["chemin"]): f["sha256"] for f in attendu_archive["fichiers"]}
        for cle in sorted(set(manifestes) | set(vivants)):
            chemin_lisible = f"{cle[0]}/{cle[1]}"
            if cle not in vivants:
                ecarts.append(f"archive : {chemin_lisible} PERDU depuis la sauvegarde")
            elif cle not in manifestes:
                ecarts.append(f"archive : {chemin_lisible} ajouté depuis la sauvegarde")
            elif vivants[cle] != manifestes[cle]:
                ecarts.append(f"archive : {chemin_lisible} ALTÉRÉ depuis la sauvegarde")

    _publier({"operation": "verifier", "ok": not ecarts, "nb_ecarts": len(ecarts)})
    return {"ok": not ecarts, "ecarts": ecarts}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _client_s3_depuis_env():
    """Client S3 si la configuration hors-site est présente, sinon None
    (sauvegarde locale seule — l'envoi hors-site est alors signalé absent).
    Tolérant à l'absence de config applicative : la restauration/la
    vérification locales doivent fonctionner avec la seule URL de base."""
    from .config import AppConfig
    from .storage import S3StorageClient

    try:
        config = AppConfig.load()
    except FileNotFoundError as exc:
        # Pas de config applicative = pas de hors-site : la sauvegarde locale
        # et la restauration restent possibles avec la seule URL de base.
        # (Une config PRÉSENTE mais invalide, elle, reste une erreur.)
        logger.info("Config applicative absente (%s) : envoi hors-site désactivé", exc)
        return None
    if not config.s3_endpoint_url:
        return None
    return S3StorageClient(config=config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m seamtech_search.sauvegarde", description=__doc__)
    sous = parser.add_subparsers(dest="commande", required=True)

    p_sauver = sous.add_parser("sauver", help="dump + inventaire archive + manifeste + envoi hors-site")
    p_sauver.add_argument("--base-url", required=True)
    p_sauver.add_argument("--archive", nargs="+", required=True, help="racine(s) de l'archive, en lecture seule")
    p_sauver.add_argument("--dossier", default="data/backups")
    p_sauver.add_argument("--retention", type=int, default=5)
    p_sauver.add_argument("--sans-s3", action="store_true", help="pas d'envoi hors-site (sauvegarde locale seule)")

    p_restaurer = sous.add_parser("restaurer", help="restaure depuis un manifeste (local ou S3)")
    p_restaurer.add_argument("--manifeste", help="chemin local du manifeste")
    p_restaurer.add_argument("--cle-manifeste", help="clé S3 du manifeste (téléchargé)")
    p_restaurer.add_argument("--base-cible", required=True)
    p_restaurer.add_argument(
        "--dump",
        help="chemin local du dump ; par défaut : dossier du manifeste + nom du manifeste",
    )

    p_verifier = sous.add_parser("verifier", help="compare une base vivante au manifeste")
    p_verifier.add_argument("--manifeste", help="chemin local du manifeste")
    p_verifier.add_argument("--cle-manifeste", help="clé S3 du manifeste (téléchargé)")
    p_verifier.add_argument("--base-url", required=True)
    p_verifier.add_argument("--archive", nargs="*", default=None)

    args = parser.parse_args(argv)

    if args.commande == "sauver":
        client = None if args.sans_s3 else _client_s3_depuis_env()
        if client is None and not args.sans_s3:
            logger.warning("S3 non configuré : sauvegarde LOCALE seule (pas de protection hors-site)")
        manifeste = sauver(
            args.base_url, [Path(r) for r in args.archive], Path(args.dossier), client_s3=client, retention=args.retention
        )
        sortie = {
            "manifeste": str(Path(args.dossier) / (manifeste["dump"]["fichier"] + ".manifest.json")),
            "dump_sha256": manifeste["dump"]["sha256"],
        }
        if "cle_s3" in manifeste["dump"]:
            sortie["cle_dump_s3"] = manifeste["dump"]["cle_s3"]
        if "manifeste_cle_s3" in manifeste:
            sortie["cle_manifeste_s3"] = manifeste["manifeste_cle_s3"]
        print(json.dumps(sortie, ensure_ascii=False))
        return 0

    if args.commande == "restaurer":
        client = _client_s3_depuis_env()
        manifeste = charger_manifeste(Path(args.manifeste) if args.manifeste else None, args.cle_manifeste, client)
        if args.dump:
            dump_local: Path | None = Path(args.dump)
        elif args.manifeste:
            # Repli local : le dump est attendu À CÔTÉ du manifeste.
            dump_local = Path(args.manifeste).parent / manifeste["dump"]["fichier"]
        else:
            dump_local = None  # re-téléchargé depuis le bucket
        resume = restaurer(args.base_cible, manifeste, chemin_dump_local=dump_local, client_s3=client)
        print(json.dumps({"restaure": resume["base"], "duree_s": round(resume["duree_s"], 2)}, ensure_ascii=False))
        return 0

    if args.commande == "verifier":
        client = _client_s3_depuis_env()
        manifeste = charger_manifeste(
            Path(args.manifeste) if args.manifeste else None, args.cle_manifeste, client
        )
        resultat = verifier(args.base_url, manifeste, [Path(r) for r in args.archive] if args.archive else None)
        print(json.dumps(resultat, ensure_ascii=False))
        return 0 if resultat["ok"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
