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
import fcntl
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql

from .schema_metier import VERSION_SCHEMA_METIER

logger = logging.getLogger("seamtech_search.sauvegarde")

#: Préfixe des objets de sauvegarde dans le bucket (dump + manifeste).
PREFIXE_SAUVEGARDES = "backups/seamtech-search-"

#: Taille des blocs de lecture pour les empreintes (fichiers volumineux).
TAILLE_BLOC_SHA256 = 1024 * 1024

#: Nom du fichier verrou (un seul sauver à la fois par dossier).
NOM_VERROU = "sauvegarde.lock"

#: Clés de configuration dont la valeur est masquée dans l'instantané.
_RE_SECRET = re.compile(r"(password|secret|token|access_key|secret_key|passwd)", re.I)

#: Perte de données maximale admissible / temps de reprise : DÉCISIONS
#: D'EXPLOITATION À PRENDRE (non figées ici). La stratégie de cohérence
#: retenue : pg_dump (instantané MVCC de la base) PUIS copie des fichiers
#: et objets listés par cet instantané. Les écritures pendant la copie
#: peuvent produire un écart fichiers/base ; les fichiers référencés
#: absents sont signalés et interdisent le statut « complet ».
RPO_RTO_A_DECIDER = {
    "rpo": "décision d'exploitation à prendre (période entre deux sauvegardes + durée de copie)",
    "rto": "décision d'exploitation à prendre (durée pg_restore + recopie fichiers/objets + redémarrage)",
    "coherence": "instantané MVCC PostgreSQL puis copie des références de cet instantané ; pas de gel applicatif automatique",
}


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


class VerrouSauvegarde:
    """Un seul ``sauver`` à la fois sur un dossier de destination.

    Fichier ``sauvegarde.lock`` + ``fcntl.LOCK_EX | LOCK_NB``. Pas de
    chevauchement silencieux. Le verrou est relâché à la sortie, y compris
    après échec (``with`` / ``close``).
    """

    def __init__(self, dossier: Path):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.chemin = self.dossier / NOM_VERROU
        self._flux = None

    def __enter__(self) -> "VerrouSauvegarde":
        self._flux = open(self.chemin, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._flux.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._flux.close()
            self._flux = None
            raise SauvegardeError(
                f"une sauvegarde est déjà en cours sur {self.dossier} "
                f"(verrou {self.chemin}) — chevauchement refusé"
            ) from exc
        self._flux.seek(0)
        self._flux.truncate()
        self._flux.write(f"pid={os.getpid()} debut={datetime.now(timezone.utc).isoformat()}\n")
        self._flux.flush()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._flux is not None:
            try:
                fcntl.flock(self._flux.fileno(), fcntl.LOCK_UN)
            finally:
                self._flux.close()
                self._flux = None


def inventorier_references_documents(base_url: str) -> dict:
    """Toutes les pièces référencées en base : chemin local et/ou clé objet.

    Ne copie rien. Signale ``present: false`` pour un chemin local absent.
    L'import d'arborescence locale n'impose PAS une copie S3 : un document
    peut n'avoir que ``path`` (contrat actuel, à ne pas transformer).
    """
    connexion = _connecter(base_url)
    try:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT id, path, name, size, object_key, object_bucket, "
                "coalesce(upload_status, ''), coalesce(category, '') "
                "FROM documents ORDER BY id"
            )
            lignes = cursor.fetchall()
    finally:
        connexion.close()
    documents: list[dict] = []
    manquants_locaux = 0
    for identifiant, chemin, nom, taille, cle, bucket, upload, categorie in lignes:
        chemin_txt = str(chemin or "")
        cle_txt = str(cle or "")
        present = bool(chemin_txt) and not chemin_txt.startswith("s3://") and Path(chemin_txt).is_file()
        # Un document déjà stocké comme objet n'est pas un fichier local manquant
        # (l'import local n'impose pas S3 ; l'inverse non plus).
        if chemin_txt and not chemin_txt.startswith("s3://") and not present and not cle_txt:
            manquants_locaux += 1
        documents.append(
            {
                "id": int(identifiant),
                "path": chemin_txt,
                "name": nom,
                "taille_declaree": int(taille or 0),
                "object_key": cle_txt,
                "object_bucket": bucket or "",
                "upload_status": upload,
                "category": categorie,
                "local_present": present,
            }
        )
    return {
        "nb_documents": len(documents),
        "nb_locaux_manquants": manquants_locaux,
        "nb_avec_objet": sum(1 for d in documents if d["object_key"]),
        "documents": documents,
    }


def copier_fichiers_locaux(references: dict, destination: Path) -> dict:
    """Copie les fichiers locaux présents vers ``destination/fichiers/{id}/nom``.

    Les absents sont listés, jamais ignorés. Aucune purge de la source.
    """
    dest = Path(destination) / "fichiers"
    dest.mkdir(parents=True, exist_ok=True)
    copies: list[dict] = []
    manquants: list[dict] = []
    for doc in references.get("documents") or []:
        chemin = Path(doc["path"]) if doc.get("path") else None
        chemin_txt = str(doc.get("path") or "")
        if chemin is None or not chemin_txt:
            continue
        if chemin_txt.startswith("s3://") or (doc.get("object_key") and not chemin.is_file()):
            continue
        if not chemin.is_file():
            manquants.append({"id": doc["id"], "path": doc["path"], "name": doc.get("name")})
            continue
        cible_dir = dest / str(doc["id"])
        cible_dir.mkdir(parents=True, exist_ok=True)
        cible = cible_dir / (doc.get("name") or chemin.name)
        shutil.copy2(chemin, cible)
        copies.append(
            {
                "id": doc["id"],
                "path_source": doc["path"],
                "path_relatif": str(cible.relative_to(destination)),
                "taille": cible.stat().st_size,
                "sha256": _sha256_fichier(cible),
            }
        )
    return {"copies": copies, "manquants": manquants, "nb_copies": len(copies), "nb_manquants": len(manquants)}


def copier_objets_s3(references: dict, client_s3, destination: Path) -> dict:
    """Télécharge les objets référencés (identité DU BUCKET DOCUMENTS).

    Un objet manquant est signalé. Le substitut S3 n'est pas une qualification
    MinIO : l'appelant documente le fournisseur réellement utilisé.
    """
    dest = Path(destination) / "objets"
    dest.mkdir(parents=True, exist_ok=True)
    copies: list[dict] = []
    manquants: list[dict] = []
    for doc in references.get("documents") or []:
        cle = doc.get("object_key") or ""
        if not cle:
            continue
        cible = dest / str(doc["id"]) / Path(cle).name
        cible.parent.mkdir(parents=True, exist_ok=True)
        try:
            client_s3.download_file(cle, cible)
        except Exception as exc:
            manquants.append({"id": doc["id"], "object_key": cle, "erreur": type(exc).__name__})
            continue
        copies.append(
            {
                "id": doc["id"],
                "object_key": cle,
                "path_relatif": str(cible.relative_to(destination)),
                "taille": cible.stat().st_size,
                "sha256": _sha256_fichier(cible),
            }
        )
    return {"copies": copies, "manquants": manquants, "nb_copies": len(copies), "nb_manquants": len(manquants)}


def restaurer_fichiers_locaux(manifeste: dict, racine_sauvegarde: Path, destination: Path) -> dict:
    """Restaure les copies locales du bundle vers une cible DISTINCTE.

    Ne lit pas les sources d'origine. Vérifie chaque empreinte. Refuse
    d'écrire si une empreinte ne correspond pas.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    restaures: list[dict] = []
    for copie in (manifeste.get("fichiers_locaux") or {}).get("copies") or []:
        source = Path(racine_sauvegarde) / copie["path_relatif"]
        if not source.is_file():
            raise SauvegardeError(f"fichier du bundle absent : {copie['path_relatif']}")
        empreinte = _sha256_fichier(source)
        if empreinte != copie["sha256"]:
            raise SauvegardeError(
                f"empreinte bundle {copie['path_relatif']} {empreinte[:12]}… ≠ manifeste {copie['sha256'][:12]}…"
            )
        cible = destination / str(copie["id"]) / Path(copie["path_relatif"]).name
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, cible)
        restaures.append({"id": copie["id"], "path": str(cible), "sha256": empreinte})
    return {"nb": len(restaures), "fichiers": restaures}


def restaurer_objets_s3(manifeste: dict, racine_sauvegarde: Path, client_s3) -> dict:
    """Restaure les objets du bundle vers un bucket cible (sources inaccessibles)."""
    restaures: list[dict] = []
    for copie in (manifeste.get("objets_s3") or {}).get("copies") or []:
        source = Path(racine_sauvegarde) / copie["path_relatif"]
        if not source.is_file():
            raise SauvegardeError(f"objet du bundle absent : {copie['path_relatif']}")
        empreinte = _sha256_fichier(source)
        if empreinte != copie["sha256"]:
            raise SauvegardeError(
                f"empreinte objet {copie['path_relatif']} {empreinte[:12]}… ≠ manifeste {copie['sha256'][:12]}…"
            )
        cle = copie["object_key"]
        # Restaurer DOIT réécrire la clé d'origine (les sources sont coupées).
        # avoid_overwrite=True irait sur une clé -2 et laisserait le catalogue
        # pointer vers un objet absent.
        cle_ecrite = client_s3.upload_file(source, cle, avoid_overwrite=False)
        restaures.append({"object_key": cle_ecrite, "sha256": empreinte})
    return {"nb": len(restaures), "objets": restaures}


def rediger_config(valeurs: dict) -> dict:
    """Instantané de configuration sans secrets (valeurs masquées)."""
    redige: dict = {}
    for cle, valeur in valeurs.items():
        if _RE_SECRET.search(str(cle)):
            redige[cle] = "***"
        else:
            redige[cle] = valeur
    return redige


def _compter_tables(connexion) -> dict[str, int]:
    comptes: dict[str, int] = {}
    with connexion.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        tables = [ligne[0] for ligne in cursor.fetchall()]
        for table in tables:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
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
    *,
    inclure_references_base: bool = False,
    client_documents_s3=None,
    verrouiller: bool = True,
) -> dict:
    """Sauvegarde : dump + inventaire archive + (optionnel) copies des pièces
    référencées + manifeste, puis envoi hors-site si ``client_s3`` est fourni.

    ``inclure_references_base`` : copie les fichiers locaux et objets S3
    réellement référencés par ``documents`` (contrat : l'import local n'impose
    PAS une réplication S3). Les absents sont listés ; ``complet`` est faux
    s'il en reste. ``verrouiller`` empêche deux sauver() sur le même dossier.
    """
    debut = time.perf_counter()
    dossier_local = Path(dossier_local)
    try:
        dossier_local.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SauvegardeError(f"destination inaccessible : {dossier_local} ({exc})") from exc

    verrou: VerrouSauvegarde | None = VerrouSauvegarde(dossier_local) if verrouiller else None
    if verrou is not None:
        verrou.__enter__()
    try:
        return _sauver_corps(
            base_url,
            racines_archive,
            dossier_local,
            client_s3=client_s3,
            retention=retention,
            inclure_references_base=inclure_references_base,
            client_documents_s3=client_documents_s3,
            debut=debut,
        )
    finally:
        if verrou is not None:
            verrou.__exit__(None, None, None)


def _sauver_corps(
    base_url: str,
    racines_archive: list[Path],
    dossier_local: Path,
    client_s3=None,
    retention: int = 5,
    *,
    inclure_references_base: bool = False,
    client_documents_s3=None,
    debut: float = 0.0,
) -> dict:
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
    manifeste: dict = {
        "date_iso": datetime.now(timezone.utc).isoformat(),
        "commit": _commit_courant(),
        "version_schema_metier": VERSION_SCHEMA_METIER,
        "dump": {"fichier": nom_dump, "octets": chemin_dump.stat().st_size, "sha256": _sha256_fichier(chemin_dump)},
        "tables": etat["tables"],
        "fiches_par_statut": etat["fiches_par_statut"],
        "documents": etat["documents"],
        "archive": inventaire,
        "rpo_rto": RPO_RTO_A_DECIDER,
        "coherence": "pg_dump MVCC puis copie des références de cet instantané",
        "complet": True,
    }
    if inclure_references_base:
        refs = inventorier_references_documents(base_url)
        manifeste["references"] = {
            "nb_documents": refs["nb_documents"],
            "nb_locaux_manquants": refs["nb_locaux_manquants"],
            "nb_avec_objet": refs["nb_avec_objet"],
        }
        fichiers = copier_fichiers_locaux(refs, dossier_local)
        manifeste["fichiers_locaux"] = {
            "nb_copies": fichiers["nb_copies"],
            "nb_manquants": fichiers["nb_manquants"],
            "copies": fichiers["copies"],
            "manquants": fichiers["manquants"],
        }
        if fichiers["nb_manquants"]:
            manifeste["complet"] = False
        if client_documents_s3 is not None:
            objets = copier_objets_s3(refs, client_documents_s3, dossier_local)
            manifeste["objets_s3"] = {
                "nb_copies": objets["nb_copies"],
                "nb_manquants": objets["nb_manquants"],
                "copies": objets["copies"],
                "manquants": objets["manquants"],
            }
            if objets["nb_manquants"]:
                manifeste["complet"] = False
        else:
            manifeste["objets_s3"] = {"nb_copies": 0, "nb_manquants": 0, "copies": [], "manquants": [], "note": "client documents S3 non fourni"}
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
        purges = appliquer_retention(client_s3, retention, dossier_local=dossier_local)
        manifeste["retention_purgees"] = purges
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
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
        purges = appliquer_retention(None, retention, dossier_local=dossier_local)
        manifeste["retention_purgees"] = purges
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
        _publier(
            {
                "operation": "sauver",
                "duree_s": round(time.perf_counter() - debut, 2),
                "dump_octets": manifeste["dump"]["octets"],
                "archive_fichiers": inventaire["nb_fichiers"],
                "verifiee_apres_envoi": False,
                "retention_purgees": len(purges),
            }
        )
    return manifeste


def appliquer_retention(
    client_s3=None,
    conserver: int = 5,
    dossier_local: Path | str | None = None,
) -> list[str]:
    """Purge symétrique (S3 et local) : garde les ``conserver`` sauvegardes
    les plus récentes, supprime les plus anciennes — JAMAIS la dernière.
    Une sauvegarde = la paire (dump, manifeste).
    Renvoie les identifiants supprimés (clés S3 et/ou chemins locaux)."""
    nb_a_garder = max(1, int(conserver))
    supprimees: list[str] = []

    if client_s3 is not None:
        cles = client_s3.list_keys(PREFIXE_SAUVEGARDES)
        dumps = [c for c in cles if c.endswith(".dump")]
        dumps_tries = sorted(dumps)  # horodatage dans la clé ⇒ ordre chronologique
        a_purger_s3 = dumps_tries[:-nb_a_garder]
        for cle in a_purger_s3:
            if client_s3.delete_file(cle):
                supprimees.append(cle)
            cle_manifeste = f"{cle}.manifest.json"
            if any(c == cle_manifeste for c in cles) and client_s3.delete_file(cle_manifeste):
                supprimees.append(cle_manifeste)

    if dossier_local is not None:
        dossier = Path(dossier_local)
        if dossier.is_dir():
            dumps_locaux = sorted(dossier.glob("*.dump"))
            a_purger_locaux = dumps_locaux[:-nb_a_garder]
            for dump_f in a_purger_locaux:
                try:
                    dump_f.unlink()
                    supprimees.append(str(dump_f))
                except OSError:
                    pass
                for cand in [dossier / f"{dump_f.name}.manifest.json", dump_f.with_suffix(".manifest.json")]:
                    if cand.is_file():
                        try:
                            cand.unlink()
                            supprimees.append(str(cand))
                        except OSError:
                            pass

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
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom_cible)))
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
    vérification locales doivent fonctionner avec la seule URL de base.

    IDENTITÉ : depuis le correctif de séparation des identités (revue du
    2026-10-07), l'application ne dispose que de droits sur SON bucket
    (``SEAMTECH_S3_BUCKET``). La sauvegarde hors-site vit dans un AUTRE bucket
    et utilise donc, quand elles sont fournies, les variables dédiées
    ``SEAMTECH_BACKUP_ACCESS_KEY`` / ``SEAMTECH_BACKUP_SECRET_KEY`` (et
    ``SEAMTECH_BACKUP_BUCKET``), provisionnées par
    ``scripts/provisionner_stockage.sh``. Sans ces variables, le repli est
    l'identité APPLICATIVE, jamais l'administrateur : l'envoi échouera
    franchement (AccessDenied) si elle n'a pas de droits sur le bucket de
    sauvegarde — c'est préférable à une élévation de privilèges silencieuse.
    """
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

    acces_sauvegarde = (os.environ.get("SEAMTECH_BACKUP_ACCESS_KEY") or "").strip()
    secret_sauvegarde = (os.environ.get("SEAMTECH_BACKUP_SECRET_KEY") or "").strip()
    bucket_sauvegarde = (os.environ.get("SEAMTECH_BACKUP_BUCKET") or "").strip()
    # Copie : la configuration chargée n'est jamais modifiée sur place (un
    # appelant qui la garde ne doit pas hériter de l'identité de sauvegarde).
    remplacements: dict[str, str] = {}
    if acces_sauvegarde and secret_sauvegarde:
        remplacements["s3_access_key"] = acces_sauvegarde
        remplacements["s3_secret_key"] = secret_sauvegarde
        logger.info("Identité de sauvegarde DÉDIÉE utilisée (SEAMTECH_BACKUP_ACCESS_KEY)")
    else:
        logger.warning(
            "SEAMTECH_BACKUP_ACCESS_KEY/SECRET_KEY absents : repli sur l'identité APPLICATIVE "
            "(SEAMTECH_S3_ACCESS_KEY). Aucun repli sur l'administrateur. Si cette identité n'a "
            "pas de droits sur le bucket de sauvegarde, l'envoi hors-site échouera — c'est le "
            "signe qu'il faut provisionner l'identité de sauvegarde."
        )
    if bucket_sauvegarde:
        remplacements["s3_bucket"] = bucket_sauvegarde
        logger.info("Bucket de sauvegarde : %s", bucket_sauvegarde)
    if remplacements:
        config = config.model_copy(update=remplacements)
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
    p_sauver.add_argument(
        "--avec-references",
        action="store_true",
        help="copie les fichiers locaux et objets S3 réellement référencés par documents",
    )

    p_restaurer = sous.add_parser("restaurer", help="restaure depuis un manifeste (local ou S3)")
    p_restaurer.add_argument("--manifeste", help="chemin local du manifeste")
    p_restaurer.add_argument("--cle-manifeste", help="clé S3 du manifeste (téléchargé)")
    p_restaurer.add_argument("--base-cible", required=True)
    p_restaurer.add_argument(
        "--dump",
        help="chemin local du dump ; par défaut : dossier du manifeste + nom du manifeste",
    )
    p_restaurer.add_argument(
        "--fichiers-cible",
        help="répertoire DISTINCT où restaurer les copies locales du bundle (sources d'origine non lues)",
    )
    p_restaurer.add_argument(
        "--restaurer-objets",
        action="store_true",
        help="restaure les objets du bundle vers le bucket documents configuré",
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
        client_documents = None
        if args.avec_references:
            # Identité du bucket DOCUMENTS (pas celle de sauvegarde) : l'import
            # local n'impose pas S3 ; on ne copie que les objets déjà présents.
            try:
                from .config import AppConfig
                from .storage import S3StorageClient

                client_documents = S3StorageClient(config=AppConfig.load())
            except Exception as exc:  # noqa: BLE001 — CLI : S3 documents optionnel
                logger.warning("client documents S3 indisponible (%s) : copies locales seulement", type(exc).__name__)
        manifeste = sauver(
            args.base_url,
            [Path(r) for r in args.archive],
            Path(args.dossier),
            client_s3=client,
            retention=args.retention,
            inclure_references_base=bool(args.avec_references),
            client_documents_s3=client_documents,
        )
        sortie = {
            "manifeste": str(Path(args.dossier) / (manifeste["dump"]["fichier"] + ".manifest.json")),
            "dump_sha256": manifeste["dump"]["sha256"],
            "complet": manifeste.get("complet", True),
        }
        if "cle_s3" in manifeste["dump"]:
            sortie["cle_dump_s3"] = manifeste["dump"]["cle_s3"]
        if "manifeste_cle_s3" in manifeste:
            sortie["cle_manifeste_s3"] = manifeste["manifeste_cle_s3"]
        print(json.dumps(sortie, ensure_ascii=False))
        return 0 if manifeste.get("complet", True) else 2

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
        extra: dict = {}
        racine_bundle = Path(args.manifeste).parent if args.manifeste else Path(args.dump).parent if args.dump else None
        if args.fichiers_cible:
            if racine_bundle is None:
                raise SauvegardeError("restauration des fichiers : fournir --manifeste (bundle local)")
            extra["fichiers"] = restaurer_fichiers_locaux(manifeste, racine_bundle, Path(args.fichiers_cible))
        if args.restaurer_objets:
            if racine_bundle is None:
                raise SauvegardeError("restauration des objets : fournir --manifeste (bundle local)")
            from .config import AppConfig
            from .storage import S3StorageClient

            try:
                config_docs = AppConfig.load()
            except FileNotFoundError as exc:
                raise SauvegardeError(
                    "restauration des objets : configuration applicative absente "
                    "(identité du bucket DOCUMENTS, distincte de la sauvegarde)"
                ) from exc
            extra["objets"] = restaurer_objets_s3(manifeste, racine_bundle, S3StorageClient(config=config_docs))
        print(
            json.dumps(
                {"restaure": resume["base"], "duree_s": round(resume["duree_s"], 2), **extra},
                ensure_ascii=False,
                default=str,
            )
        )
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
