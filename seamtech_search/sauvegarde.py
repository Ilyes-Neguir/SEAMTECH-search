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
- Rétention : la purge automatique est suspendue tant qu'une protection
  globale et une restauration coordonnée ne sont pas prouvées ; surveiller
  l'espace disponible. La fonction de rétention reste testable séparément.
- Vérification après envoi : le dump est RE-LU depuis le bucket et son
  empreinte doit correspondre à celle du manifeste.

Usage :
    # SEAMTECH_DATABASE_URL et SEAMTECH_RESTORE_DATABASE_URL sont fournis
    # par le gestionnaire de secrets ; ne pas les afficher dans le terminal.
    python -m seamtech_search.sauvegarde sauver \\
        --archive RACINE [RACINE ...] --dossier data/backups [--retention 5]
    python -m seamtech_search.sauvegarde restaurer --manifeste FICHIER
    python -m seamtech_search.sauvegarde restaurer --cle-manifeste CLE
    python -m seamtech_search.sauvegarde verifier --manifeste FICHIER \\
        --archive RACINE [RACINE ...]

La publication des résultats (CI) passe par ``SEAMTECH_SAUVEGARDE_JSON``
(fichier JSONL, une mesure par ligne — même mécanique que ``perf-latence``).
``sauver`` renvoie actuellement le code 2 : le dump est créé, mais la
protection globale n'est pas qualifiée. ``complet`` est toujours faux tant
que la restauration globale n'est pas prouvée ; ``complet_perimetre_demande``
signale uniquement l'état des copies effectivement demandées.

Les binaires ``pg_dump``/``pg_restore`` sont découverts via ``PATH``, puis
``/usr/lib/postgresql/*/bin``, puis ``SEAMTECH_PG_BINDIR``.
"""

from __future__ import annotations

import argparse
import base64
import errno
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qsl, quote, unquote, urlparse

try:
    import fcntl
except ImportError:  # Windows : le verrou doit refuser plutôt que fonctionner sans exclusion.
    fcntl = None

try:
    # Windows : ``msvcrt.locking`` est un verrou de plage d'octets, non bloquant
    # en mode LK_NBLCK. Il ne s'applique qu'à des FICHIERS — un répertoire ne
    # se verrouille pas. Le parent est donc verrouillé par un fichier posé À
    # CÔTÉ du dossier de sauvegarde (voir ``VerrouSauvegarde``).
    import msvcrt
except ImportError:
    msvcrt = None

import psycopg2
from psycopg2 import sql

from .schema_metier import VERSION_SCHEMA_APPLICATION, VERSION_SCHEMA_METIER, schema_identite_octets_valide_pg

logger = logging.getLogger("seamtech_search.sauvegarde")

#: Préfixe des objets de sauvegarde dans le bucket (dump + manifeste).
PREFIXE_SAUVEGARDES = "backups/seamtech-search-"

#: Taille des blocs de lecture pour les empreintes (fichiers volumineux).
TAILLE_BLOC_SHA256 = 1024 * 1024

#: Nom du fichier verrou (un seul sauver à la fois par dossier).
NOM_VERROU = "sauvegarde.lock"

#: Verrou du PARENT, pour les plateformes sans ``flock`` sur répertoire :
#: verrouiller le dossier lui-même est impossible, on verrouille donc un
#: fichier posé à côté. Le scénaire couvert est le même que sur POSIX : un
#: renommage du dossier de sauvegarde ne doit pas ouvrir la porte à un second
#: détenteur (voir ``VerrouSauvegarde``).
NOM_VERROU_PARENT = ".sauvegarde-parent.lock"

#: Errnos signifiant « la plage est déjà verrouillée » : ``flock`` non bloquant
#: (EWOULDBLOCK, via BlockingIOError) et ``msvcrt.locking`` (EACCES / EDEADLK).
#: Regroupés ici parce que les deux plateformes doivent produire le même
#: message, et qu'un opérateur qui lit « impossible d'acquérir » ne sait pas
#: s'il doit attendre, tuer un processus ou changer de dossier.
_ERRNOS_VERROU_PRIS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK})

#: Nombre d'octets verrouillés par ``msvcrt.locking`` (une plage d'un octet
#: suffit : c'est l'exclusion mutuelle qui compte, pas la taille).
PLAGE_VERROU_WINDOWS = 1

#: Clés de configuration dont la valeur est masquée dans l'instantané.
_RE_SECRET = re.compile(r"(password|secret|token|access_key|secret_key|passwd)", re.I)

#: Perte de données maximale admissible / temps de reprise : DÉCISIONS
#: D'EXPLOITATION À PRENDRE (non figées ici). La stratégie de cohérence
#: actuelle : pg_dump, comptages et références partagent un instantané MVCC
#: exporté. L'archive et les octets locaux/S3 sont copiés ensuite sans gel :
#: leur correspondance avec le dump n'est donc pas garantie. Le booléen
#: « complet » reste faux ; l'état des copies demandées est distinct.
RPO_RTO_A_DECIDER = {
    "rpo": "décision d'exploitation à prendre (période entre deux sauvegardes + durée de copie)",
    "rto": "décision d'exploitation à prendre (durée pg_restore + recopie fichiers/objets + redémarrage)",
    "coherence": "dump et références dans un snapshot MVCC partagé ; copies et inventaire indépendants, sans gel applicatif",
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


def _chemin_absolu_sans_traversee(chemin: Path) -> Path:
    if ".." in chemin.parts:
        raise SauvegardeError("chemin contenant une traversée de répertoire refusé")
    return Path(os.path.abspath(chemin))


def _sha256_flux(flux: BinaryIO) -> str:
    hacheur = hashlib.sha256()
    while bloc := flux.read(TAILLE_BLOC_SHA256):
        hacheur.update(bloc)
    return hacheur.hexdigest()


def _empreintes_flux(flux: BinaryIO) -> tuple[str, str]:
    """SHA-256 (identité du bundle) et MD5 (comparaison à l'ETag du fournisseur).

    Le MD5 n'est pas utilisé comme fonction de sécurité ici : il sert
    uniquement à comparer nos octets à l'ETag « contenu » que renvoie un
    objet S3 déposé en une seule partie (MinIO, R2, AWS). C'est un
    condensé CALCULÉ PAR LE FOURNISSEUR au dépôt, donc indépendant de
    notre catalogue.
    """
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()  # noqa: S324 - comparaison d'ETag, pas de la sécurité
    while bloc := flux.read(TAILLE_BLOC_SHA256):
        sha256.update(bloc)
        md5.update(bloc)
    return sha256.hexdigest(), md5.hexdigest()


def _etag_contenu(etag: str) -> str | None:
    """MD5 hexadécimal porté par un ETag « contenu » (None si multipart).

    Un ETag de la forme ``"d41d8c..."`` est le MD5 des octets ; un ETag
    multipart se termine par ``-N`` et NE prouve pas les octets : on ne
    l'utilise donc jamais comme preuve d'identité.
    """
    brut = (etag or "").strip().strip('"').strip("'")
    if len(brut) != 32:
        return None
    try:
        int(brut, 16)
    except ValueError:
        return None
    return brut.lower()


def _sha256_fichier(chemin: Path) -> str:
    with open(chemin, "rb") as flux:
        return _sha256_flux(flux)


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
    def utilisable(candidat: Path) -> bool:
        return candidat.is_file() and os.access(candidat, os.X_OK)

    bindir_env = os.environ.get("SEAMTECH_PG_BINDIR")
    if bindir_env:
        candidat = Path(bindir_env) / nom
        if utilisable(candidat):
            return str(candidat)
    for candidat in sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{nom}"), reverse=True):
        if utilisable(Path(candidat)):
            return candidat
    bindir_pgserver = _repertoire_binaires_pgserver()
    if bindir_pgserver is not None and utilisable(bindir_pgserver / nom):
        return str(bindir_pgserver / nom)
    raise SauvegardeError(
        f"binaire « {nom} » introuvable (PATH, SEAMTECH_PG_BINDIR, /usr/lib/postgresql/*/bin) — "
        "installez postgresql-client ou pointez SEAMTECH_PG_BINDIR vers le bon répertoire"
    )


def _connecter(base_url: str, *, autocommit: bool = False):
    try:
        connexion = psycopg2.connect(base_url)
    except psycopg2.Error:
        # libpq peut inclure l'URI (et son mot de passe) dans les erreurs de
        # syntaxe. Ne pas exposer cette exception ni son contexte aux logs.
        raise SauvegardeError("Connexion PostgreSQL impossible ou paramètres invalides") from None
    if autocommit:
        connexion.autocommit = True
    return connexion


def _parametres_pg_process(base_url: str) -> tuple[str, dict[str, str]]:
    """Évite d'exposer le mot de passe libpq dans les arguments du processus.

    L'environnement du processus reste accessible aux utilisateurs autorisés
    du même compte : ce n'est pas un coffre-fort ni une isolation du système.
    """
    from psycopg2.extensions import make_dsn, parse_dsn

    try:
        parametres = parse_dsn(base_url)
        mot_de_passe = parametres.pop("password", None)
        dsn = make_dsn(**parametres)
    except (psycopg2.Error, ValueError):
        raise SauvegardeError("Paramètres PostgreSQL invalides") from None
    environnement = os.environ.copy()
    if mot_de_passe is not None:
        environnement["PGPASSWORD"] = mot_de_passe
    return dsn, environnement


def _url_sans_base(base_url: str) -> str:
    """URL de la base administrative sur le même serveur libpq."""
    return _remplacer_nom_base(base_url, "postgres")


def _nom_base(base_url: str) -> str:
    """Vérifie que libpq et le chemin désignent la même base explicite."""
    from psycopg2.extensions import parse_dsn

    try:
        parties = urlparse(base_url)
        if parties.scheme not in {"postgres", "postgresql"} or parties.fragment:
            raise ValueError
        nom = unquote(parties.path.lstrip("/"))
        if not nom or any(cle == "dbname" for cle, _ in parse_qsl(parties.query, keep_blank_values=True)):
            raise ValueError
        if parse_dsn(base_url).get("dbname") != nom:
            raise ValueError
    except (ValueError, psycopg2.Error):
        raise SauvegardeError("URL PostgreSQL sans base explicite ou ambiguë") from None
    return nom


def _remplacer_nom_base(base_url: str, nouveau_nom: str) -> str:
    _nom_base(base_url)
    if not nouveau_nom or "\x00" in nouveau_nom:
        raise SauvegardeError("Nom de base PostgreSQL invalide")
    parties = urlparse(base_url)
    url = f"{parties.scheme}://{parties.netloc}/{quote(nouveau_nom, safe='')}"
    if parties.query:
        url += f"?{parties.query}"
    return url


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


def _verrouiller_plage(fd: int) -> None:
    """Verrou exclusif non bloquant d'une plage d'un octet (``msvcrt``).

    Lève ``OSError`` si la plage est déjà verrouillée par un autre
    titulaire : l'appelant le traduit en « une sauvegarde est déjà en cours ».
    """
    if msvcrt is None:  # pragma: no cover - n'arrive que si la plateforme ment
        raise SauvegardeError("Verrouillage sûr indisponible sur cette plateforme")
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, PLAGE_VERROU_WINDOWS)


def _deverrouiller_plage(fd: int) -> None:
    """Libère la plage verrouillée — meilleur effort, jamais bloquant."""
    if msvcrt is None:  # pragma: no cover
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, PLAGE_VERROU_WINDOWS)
    except OSError:  # pragma: no cover - le descripteur peut être déjà fermé
        pass


class VerrouSauvegarde:
    """Un seul ``sauver`` à la fois sur un dossier de destination.

    Verrouille le PARENT, puis le dossier, puis ``sauvegarde.lock`` avec
    ``flock`` non bloquant. Le verrou du fichier conserve la coordination avec
    les anciennes versions qui verrouillent uniquement le fichier. Les trois
    sont relâchés à la sortie.

    Verrouiller le PARENT n'est pas une précaution de plus : c'est ce qui
    empêche le scénario du dossier REMPLACÉ. Sans lui, un détenteur dont le
    dossier est renommé (l'inode verrouillé n'est plus le chemin) laisse un
    second venu verrouiller le nouveau dossier du même nom — deux sauvegardes
    simultanées, sans qu'aucun des deux ne le sache. Le parent, lui, ne bouge
    pas.
    """

    def __init__(self, dossier: Path):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.chemin = self.dossier / NOM_VERROU
        self._flux = None
        self._fd_dossier = None
        self._fd_parent = None
        # Windows : descripteurs verrouillés par ``msvcrt.locking``, dans l'ordre
        # d'acquisition (parent, puis dossier) — libérés en ordre inverse.
        self._fds_verrouilles: list[int] = []
        self._garde_instance = threading.Lock()

    def __enter__(self) -> "VerrouSauvegarde":
        if not self._garde_instance.acquire(blocking=False):
            raise SauvegardeError("Réentrée du verrou de sauvegarde interdite")
        try:
            return self._acquerir()
        except BaseException:
            self._garde_instance.release()
            raise

    @staticmethod
    def _confinement_posix() -> bool:
        """Les primitives d'ancrage par descripteur sont-elles disponibles ?"""
        return (
            fcntl is not None
            and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd
        )

    def _acquerir(self) -> "VerrouSauvegarde":
        if self._confinement_posix():
            return self._acquerir_posix()
        if msvcrt is not None:
            return self._acquerir_windows()
        raise SauvegardeError("Verrouillage sûr indisponible sur cette plateforme")

    def _acquerir_windows(self) -> "VerrouSauvegarde":
        """Verrou exclusif par plage d'octets : le parent, puis le dossier.

        Sur Windows, un répertoire ne se verrouille pas et ``flock`` n'existe
        pas. L'équivalent retenu verrouille deux FICHIERS : l'un dans le parent
        (qui ne bouge pas quand le dossier de sauvegarde est renommé — la
        propriété que le verrou du parent apportait sur POSIX), l'autre dans le
        dossier lui-même. La coordination avec les anciennes versions reste
        assurée : le second fichier est ``sauvegarde.lock``, le même nom.

        Ce qu'on perd, dit franchement : un verrou de plage d'octets est
        libéré à la fermeture du descripteur, y compris si le processus meurt
        — c'est le bon comportement — mais il ne survit pas à une coupure de
        courant pendant l'attente, ce qu'aucun verrou ne fait.
        """
        fds: list[int] = []
        try:
            for chemin in (self.dossier.parent / NOM_VERROU_PARENT, self.chemin):
                fd = os.open(chemin, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOINHERIT", 0), 0o600)
                fds.append(fd)
                _verrouiller_plage(fd)
        except BaseException as erreur:
            for fd in reversed(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass
            return self._refuser_verrou(erreur)
        self._fds_verrouilles = fds
        return self

    def _refuser_verrou(self, erreur: BaseException) -> "VerrouSauvegarde":
        # ``flock`` non bloquant lève BlockingIOError ; ``msvcrt.locking`` lève
        # OSError(EACCES/EDEADLK) — errno normalisé par Python. Les deux veulent
        # dire la même chose, et l'opératrice doit lire la même phrase.
        pris = isinstance(erreur, BlockingIOError) or (
            isinstance(erreur, OSError) and erreur.errno in _ERRNOS_VERROU_PRIS
        )
        if pris:
            raise SauvegardeError("une sauvegarde est déjà en cours — chevauchement refusé") from None
        if isinstance(erreur, OSError):
            raise SauvegardeError("Impossible d'acquérir le verrou de sauvegarde") from None
        raise erreur

    def _acquerir_posix(self) -> "VerrouSauvegarde":
        fd = None
        try:
            # Le parent d'abord : un renommage du dossier de sauvegarde ne doit
            # pas permettre un second détenteur sur le même chemin.
            self._fd_parent = os.open(self.dossier.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fcntl.flock(self._fd_parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd_dossier = os.open(self.dossier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fcntl.flock(self._fd_dossier, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(NOM_VERROU, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                         0o600, dir_fd=self._fd_dossier)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SauvegardeError("Fichier de verrou invalide")
            self._flux = os.fdopen(fd, "r+", encoding="utf-8")
            fd = None
            fcntl.flock(self._flux.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as erreur:
            try:
                if fd is not None:
                    os.close(fd)
            finally:
                try:
                    if self._flux is not None:
                        try:
                            self._flux.close()
                        finally:
                            self._flux = None
                finally:
                    if self._fd_dossier is not None:
                        try:
                            os.close(self._fd_dossier)
                        finally:
                            self._fd_dossier = None
                    if self._fd_parent is not None:
                        try:
                            os.close(self._fd_parent)
                        finally:
                            self._fd_parent = None
            if isinstance(erreur, BlockingIOError):
                raise SauvegardeError("une sauvegarde est déjà en cours — chevauchement refusé") from None
            if isinstance(erreur, OSError):
                raise SauvegardeError("Impossible d'acquérir le verrou de sauvegarde") from None
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fds_verrouilles:
            try:
                for fd in reversed(self._fds_verrouilles):
                    try:
                        _deverrouiller_plage(fd)
                    finally:
                        os.close(fd)
            finally:
                self._fds_verrouilles = []
                self._garde_instance.release()
            return
        try:
            if self._flux is not None:
                try:
                    fcntl.flock(self._flux.fileno(), fcntl.LOCK_UN)
                finally:
                    self._flux.close()
                    self._flux = None
        finally:
            try:
                if self._fd_dossier is not None:
                    try:
                        fcntl.flock(self._fd_dossier, fcntl.LOCK_UN)
                    finally:
                        os.close(self._fd_dossier)
                        self._fd_dossier = None
            finally:
                try:
                    if self._fd_parent is not None:
                        try:
                            fcntl.flock(self._fd_parent, fcntl.LOCK_UN)
                        finally:
                            os.close(self._fd_parent)
                            self._fd_parent = None
                finally:
                    self._garde_instance.release()


def inventorier_references_documents(base_url: str, *, connexion=None) -> dict:
    """Toutes les pièces référencées en base : chemin local et/ou clé objet.

    Ne copie rien. Signale ``present: false`` pour un chemin local absent.
    L'import d'arborescence locale n'impose PAS une copie S3 : un document
    peut n'avoir que ``path`` (contrat actuel, à ne pas transformer).
    """
    connexion_interne = connexion is None
    if connexion_interne:
        connexion = _connecter(base_url)
    try:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT id, path, name, size, object_key, object_bucket, "
                "coalesce(upload_status, ''), coalesce(category, ''), coalesce(is_dir, false), source_sha256 "
                "FROM documents ORDER BY id"
            )
            lignes = cursor.fetchall()
    finally:
        if connexion_interne:
            connexion.close()
    documents: list[dict] = []
    manquants_locaux = 0
    for identifiant, chemin, nom, taille, cle, bucket, upload, categorie, est_dossier, source_sha256 in lignes:
        chemin_txt = str(chemin or "")
        cle_txt = str(cle or "")
        present = bool(chemin_txt) and not chemin_txt.startswith("s3://") and Path(chemin_txt).is_file()
        # Un document déjà stocké comme objet n'est pas un fichier local manquant
        # (l'import local n'impose pas S3 ; l'inverse non plus).
        if not est_dossier and not present and not cle_txt:
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
                "is_dir": bool(est_dossier),
                "source_sha256": source_sha256,
            }
        )
    return {
        "nb_documents": len(documents),
        "nb_locaux_manquants": manquants_locaux,
        "nb_avec_objet": sum(1 for d in documents if d["object_key"]),
        "documents": documents,
    }


def _empreinte_capture_reference(document: dict) -> str | None:
    """Valide l'identité connue du snapshot ; None reste explicitement inconnu."""
    empreinte = document.get("source_sha256")
    if empreinte is not None and (
        not isinstance(empreinte, str) or re.fullmatch(r"[0-9a-f]{64}", empreinte) is None
    ):
        raise SauvegardeError("empreinte de capture du catalogue invalide")
    return empreinte


def copier_fichiers_locaux(references: dict, destination: Path) -> dict:
    """Copie les fichiers locaux présents vers ``destination/fichiers/{id}/nom``.

    Les absents sont listés, jamais ignorés. Aucune purge de la source.
    Les octets ne peuvent pas contredire l'identité connue du snapshot PG.
    """
    documents = references.get("documents") or []
    for document in documents:
        _empreinte_capture_reference(document)
    dest = Path(destination) / "fichiers"
    if not dest.resolve().is_relative_to(Path(destination).resolve()):
        raise SauvegardeError("destination de sauvegarde hors du bundle")
    dest.mkdir(parents=True, exist_ok=True)
    copies: list[dict] = []
    manquants: list[dict] = []
    for doc in documents:
        if doc.get("is_dir"):
            continue
        empreinte_attendue = _empreinte_capture_reference(doc)
        chemin = Path(doc["path"]) if doc.get("path") else None
        chemin_txt = str(doc.get("path") or "")
        if chemin is None or not chemin_txt or chemin_txt.startswith("s3://"):
            if not doc.get("object_key"):
                manquants.append({"id": doc["id"], "path": chemin_txt, "name": doc.get("name")})
            continue
        if doc.get("object_key") and not chemin.is_file():
            continue
        if not chemin.is_file():
            manquants.append({"id": doc["id"], "path": doc["path"], "name": doc.get("name")})
            continue
        identifiant = doc.get("id")
        nom = doc.get("name") or chemin.name
        if (
            not isinstance(identifiant, int) or isinstance(identifiant, bool) or identifiant <= 0
            or not isinstance(nom, str) or nom in {"", ".", ".."}
            or "/" in nom or "\\" in nom or "\x00" in nom
        ):
            raise SauvegardeError("identifiant ou nom de document invalide pour la sauvegarde")
        cible_dir = dest / str(identifiant)
        cible = cible_dir / nom
        if not cible.resolve().is_relative_to(dest.resolve()):
            raise SauvegardeError("destination de sauvegarde hors du bundle")
        try:
            source_absolue = _chemin_absolu_sans_traversee(chemin)
            with _ouvrir_source_bundle(source_absolue.parent, Path(source_absolue.name)) as flux:
                initial = os.fstat(flux.fileno())
                empreinte = _sha256_flux(flux)
                if empreinte_attendue is not None and empreinte != empreinte_attendue:
                    manquants.append({"id": doc["id"], "path": doc["path"], "name": doc.get("name"),
                                      "erreur": "identite_catalogue_differente"})
                    continue
                flux.seek(0)
                _ecrire_copie_confinee(flux, Path(os.path.abspath(cible)), empreinte)
                flux.seek(0)
                if _sha256_flux(flux) != empreinte:
                    raise SauvegardeError("empreinte de la source modifiée pendant la sauvegarde")
                with _ouvrir_source_bundle(source_absolue.parent, Path(source_absolue.name)) as courante:
                    final = os.fstat(courante.fileno())
                    if (initial.st_dev, initial.st_ino) != (final.st_dev, final.st_ino):
                        raise SauvegardeError("identité de la source modifiée pendant la sauvegarde")
            taille_copie = cible.stat().st_size
        except OSError:
            raise SauvegardeError("Impossible de vérifier une copie locale de sauvegarde") from None
        copies.append(
            {
                "id": doc["id"],
                "path_source": doc["path"],
                "path_relatif": str(cible.relative_to(destination)),
                "taille": taille_copie,
                "sha256": empreinte,
                "identite_catalogue": "verifiee" if empreinte_attendue is not None else "inconnue",
                # Relevés à l'instant de la copie : c'est ce qui permet, après
                # le dump, de prouver qu'une source a bougé PENDANT la fenêtre
                # (taille + horodatage, sans relire tous les octets).
                "taille_source": int(initial.st_size),
                "mtime_ns_source": int(initial.st_mtime_ns),
            }
        )
    return {"copies": copies, "manquants": manquants, "nb_copies": len(copies), "nb_manquants": len(manquants)}


def _verifier_derive_locale(copies: list[dict]) -> list[dict]:
    """Les sources copiées ont-elles bougé PENDANT la fenêtre du dump ?

    Les copies sont faites AVANT le dump (donc sur les octets vus au moment du
    snapshot), puis chaque source est revérifiée APRÈS : taille et horodatage
    doivent être identiques. Un écart ne « corrige » rien — il est DIT, et le
    manifeste le porte. C'est la seule posture honnête : sans gel applicatif du
    système de fichiers, une sauvegarde ne peut pas promettre des octets figés,
    elle peut seulement prouver qu'elle a détecté la dérive.
    """
    derive: list[dict] = []
    for copie in copies:
        chemin = copie.get("path_source")
        if not chemin:
            continue
        try:
            actuel = os.stat(chemin)
        except OSError:
            derive.append({"path": str(chemin), "motif": "source disparue après la copie"})
            continue
        if (int(actuel.st_size) != int(copie.get("taille_source", -1))
                or int(actuel.st_mtime_ns) != int(copie.get("mtime_ns_source", -1))):
            derive.append({
                "path": str(chemin),
                "motif": "source modifiée pendant la fenêtre du dump",
                "taille_avant": copie.get("taille_source"),
                "taille_apres": int(actuel.st_size),
            })
    return derive


def _identite_objet_s3(client_s3, cle: str) -> tuple[int, str, str | None, str | None]:
    """Observe l'identité et un éventuel SHA-256 fournisseur de l'objet entier."""
    try:
        lecture = getattr(client_s3, "head_object_avec_checksum", client_s3.head_object)
        entete = lecture(cle)
        taille = entete.get("ContentLength")
        etag = entete.get("ETag")
        version = entete.get("VersionId")
        checksum = entete.get("ChecksumSHA256") if entete.get("ChecksumType") == "FULL_OBJECT" else None
        if checksum is not None:
            decode = base64.b64decode(checksum, validate=True)
            checksum = decode.hex() if len(decode) == 32 else None
    except Exception:
        raise SauvegardeError("métadonnées de l'objet source indisponibles") from None
    if (type(taille) is not int or taille < 0 or not isinstance(etag, str) or not etag
            or (version is not None and (not isinstance(version, str) or not version))):
        raise SauvegardeError("métadonnées de l'objet source invalides")
    return taille, etag, version, checksum


def copier_objets_s3(references: dict, client_s3, destination: Path) -> dict:
    """Télécharge les objets référencés (identité DU BUCKET DOCUMENTS).

    Un objet manquant est signalé. Le substitut S3 n'est pas une qualification
    MinIO : l'appelant documente le fournisseur réellement utilisé.
    L'identité connue du snapshot PG est vérifiée en plus du checksum fournisseur.
    """
    documents = references.get("documents") or []
    for document in documents:
        _empreinte_capture_reference(document)
    dest = Path(destination) / "objets"
    if not dest.resolve().is_relative_to(Path(destination).resolve()):
        raise SauvegardeError("destination de sauvegarde hors du bundle")
    dest.mkdir(parents=True, exist_ok=True)
    copies: list[dict] = []
    manquants: list[dict] = []
    for doc in documents:
        cle = doc.get("object_key") or ""
        if not cle:
            continue
        empreinte_attendue = _empreinte_capture_reference(doc)
        bucket_reference = doc.get("object_bucket") or ""
        if bucket_reference and bucket_reference != getattr(client_s3, "bucket_name", None):
            manquants.append({"id": doc["id"], "object_key": cle, "erreur": "bucket_documents_different"})
            continue
        identifiant = doc.get("id")
        if isinstance(identifiant, bool) or not isinstance(identifiant, int) or identifiant <= 0:
            raise SauvegardeError("identifiant de document invalide pour la sauvegarde")
        nom_objet = Path(cle).name
        if not nom_objet or nom_objet in {".", ".."} or "\\" in nom_objet or "\x00" in nom_objet:
            raise SauvegardeError("nom d'objet invalide pour la sauvegarde")
        cible = dest / str(identifiant) / nom_objet
        if not cible.resolve().is_relative_to(dest.resolve()):
            raise SauvegardeError("destination de sauvegarde hors du bundle")
        try:
            identite_avant = _identite_objet_s3(client_s3, cle)
        except SauvegardeError:
            manquants.append({"id": doc["id"], "object_key": cle, "erreur": "identite_source_indisponible"})
            continue
        # Le téléchargement ne doit jamais suivre les chemins du bundle :
        # leur substitution pourrait écraser un fichier tiers.
        import tempfile

        dossier_temp = os.environ.get("SEAMTECH_SAUVEGARDE_TMP")
        try:
            if dossier_temp:
                Path(dossier_temp).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="seamtech-backup-object-", dir=dossier_temp) as tmp:
                telecharge = Path(tmp) / "objet"
                try:
                    # "null" est la version mutable d'un bucket sans versioning
                    # actif ou suspendu ; elle ne constitue pas une version figée.
                    version = identite_avant[2]
                    if version is not None and version != "null":
                        client_s3.download_file(cle, telecharge, version_id=version)
                    else:
                        client_s3.download_file(cle, telecharge)
                except Exception as exc:
                    manquants.append({"id": doc["id"], "object_key": cle, "erreur": type(exc).__name__})
                    continue
                with _ouvrir_source_bundle(Path(tmp), Path("objet")) as flux:
                    try:
                        identite_apres = _identite_objet_s3(client_s3, cle)
                    except SauvegardeError:
                        manquants.append({"id": doc["id"], "object_key": cle, "erreur": "identite_source_indisponible"})
                        continue
                    if identite_apres != identite_avant or os.fstat(flux.fileno()).st_size != identite_avant[0]:
                        manquants.append({"id": doc["id"], "object_key": cle, "erreur": "source_modifiee_ou_tronquee"})
                        continue
                    empreinte, md5_octets = _empreintes_flux(flux)
                    etag_contenu = _etag_contenu(identite_avant[1])
                    # Preuve d'identité, par ordre de force : le SHA-256 complet
                    # fourni par le stockage s'il existe (objet déposé avec
                    # checksum plein objet), sinon l'ETag « contenu » du
                    # fournisseur (MD5 calculé au dépôt en une seule partie),
                    # sinon l'identité CONNUE du snapshot PostgreSQL. Sans
                    # aucune de ces trois références, les octets téléchargés
                    # ne prouvent rien et la copie n'est pas publiée : jamais
                    # une copie « non prouvée » dans un bundle qui se déclare
                    # complet.
                    if identite_avant[3] is not None:
                        if empreinte != identite_avant[3]:
                            manquants.append({"id": doc["id"], "object_key": cle, "erreur": "integrite_source_non_prouvee"})
                            continue
                        preuve = "checksum_sha256_fournisseur_full_object"
                    elif etag_contenu is not None:
                        if md5_octets != etag_contenu:
                            manquants.append({"id": doc["id"], "object_key": cle, "erreur": "integrite_source_non_prouvee"})
                            continue
                        preuve = "etag_md5_fournisseur"
                    elif empreinte_attendue is not None and empreinte == empreinte_attendue:
                        preuve = "identite_catalogue_source_sha256"
                    else:
                        manquants.append({"id": doc["id"], "object_key": cle, "erreur": "integrite_source_non_prouvee"})
                        continue
                    if empreinte_attendue is not None and empreinte != empreinte_attendue:
                        manquants.append({"id": doc["id"], "object_key": cle, "erreur": "identite_catalogue_differente"})
                        continue
                    flux.seek(0)
                    _ecrire_copie_confinee(flux, _chemin_absolu_sans_traversee(cible), empreinte)
                taille_copie = cible.stat().st_size
        except OSError:
            raise SauvegardeError("Impossible de préparer une copie d'objet de sauvegarde") from None
        copies.append(
            {
                "id": doc["id"],
                "object_key": cle,
                "path_relatif": str(cible.relative_to(destination)),
                "taille": taille_copie,
                "sha256": empreinte,
                "identite_catalogue": "verifiee" if empreinte_attendue is not None else "inconnue",
                "verification": preuve,
            }
        )
    return {"copies": copies, "manquants": manquants, "nb_copies": len(copies), "nb_manquants": len(manquants)}


@contextmanager
def _ouvrir_source_bundle(racine: Path, relatif: Path):
    """Ouvre les composants sans liens, depuis une racine fixée par descripteur."""
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd):
        raise SauvegardeError("Lecture confinée du bundle indisponible sur cette plateforme")
    fd = None
    fichier = None
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for composant in (*racine.parts[1:], *relatif.parts[:-1]):
            nouveau = os.open(composant, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nouveau
        fichier = os.open(relatif.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=fd)
        if not stat.S_ISREG(os.fstat(fichier).st_mode):
            raise SauvegardeError("La source du bundle doit être un fichier ordinaire")
        with os.fdopen(fichier, "rb") as flux:
            fichier = None
            yield flux
    except OSError:
        raise SauvegardeError("Impossible de lire une source confinée du bundle") from None
    finally:
        if fichier is not None:
            os.close(fichier)
        if fd is not None:
            os.close(fd)


def _verifier_copies_bundle(copies: list[dict], racine_sauvegarde: Path) -> list[tuple[dict, Path, str]]:
    """Valide toutes les sources AVANT toute copie ou écriture distante.

    Le manifeste est une entrée non fiable : ni chemin absolu, ni traversée,
    ni lien symbolique vers l'extérieur du bundle ne sont autorisés.
    """
    racine = Path(racine_sauvegarde).resolve()
    valides: list[tuple[dict, Path, str]] = []
    for copie in copies:
        relatif = copie.get("path_relatif")
        if (not isinstance(relatif, str) or not relatif or "\\" in relatif or "\x00" in relatif
                or Path(relatif).is_absolute() or ".." in Path(relatif).parts or not Path(relatif).parts):
            raise SauvegardeError("chemin de bundle invalide")
        chemin_relatif = Path(relatif)
        source = racine / chemin_relatif
        hacheur = hashlib.sha256()
        with _ouvrir_source_bundle(racine, chemin_relatif) as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                hacheur.update(bloc)
        empreinte = hacheur.hexdigest()
        if empreinte != copie.get("sha256"):
            raise SauvegardeError("empreinte du bundle différente du manifeste")
        valides.append((copie, source, empreinte))
    return valides


def _telecharger_bundle_distant(manifeste: dict, client_s3, destination: Path, sections: tuple[str, ...]) -> None:
    """Reconstitue en zone temporaire privée les copies demandées, avec contrôle SHA-256."""
    plan: list[tuple[dict, Path, str]] = []
    cibles: set[Path] = set()
    racine = destination.resolve()
    for section in sections:
        for copie in (manifeste.get(section) or {}).get("copies") or []:
            relatif = copie.get("path_relatif")
            cle = copie.get("cle_s3_sauvegarde")
            if (not isinstance(relatif, str) or not relatif or "\\" in relatif or "\x00" in relatif
                    or Path(relatif).is_absolute() or ".." in Path(relatif).parts
                    or not isinstance(cle, str) or not cle):
                raise SauvegardeError("copie distante invalide dans le manifeste")
            cible = (racine / relatif).resolve()
            if not cible.is_relative_to(racine) or cible == racine or cible in cibles:
                raise SauvegardeError("destination de copie distante invalide ou dupliquée")
            cibles.add(cible)
            plan.append((copie, cible, cle))
    for copie, cible, cle in plan:
        cible.parent.mkdir(parents=True, exist_ok=True)
        client_s3.download_file(cle, cible)
        if _sha256_fichier(cible) != copie.get("sha256"):
            raise SauvegardeError("empreinte de copie distante différente du manifeste")


def _plan_restaurer_fichiers(manifeste: dict, racine_sauvegarde: Path, destination: Path) -> list:
    """Vérifie les entrées et les cibles sans écrire."""
    destination = Path(destination).resolve()
    copies = _verifier_copies_bundle(
        (manifeste.get("fichiers_locaux") or {}).get("copies") or [], racine_sauvegarde
    )
    plan: list[tuple[dict, Path, str, Path]] = []
    cibles: set[Path] = set()
    for copie, source, empreinte in copies:
        identifiant = copie.get("id")
        if isinstance(identifiant, bool) or not isinstance(identifiant, int) or identifiant <= 0:
            raise SauvegardeError("identifiant de document invalide dans le manifeste")
        cible = destination / str(identifiant) / source.name
        resolue = cible.resolve()
        if not resolue.is_relative_to(destination) or cible in cibles:
            raise SauvegardeError("destination de restauration invalide ou dupliquée")
        if os.path.lexists(cible):
            raise SauvegardeError("une cible de restauration existe déjà")
        cibles.add(cible)
        plan.append((copie, source, empreinte, cible))
    return plan


@contextmanager
def _instantane_bundle_prive(racine: Path, source: Path):
    import tempfile

    dossier = os.environ.get("SEAMTECH_SAUVEGARDE_TMP")
    try:
        if dossier:
            Path(dossier).mkdir(parents=True, exist_ok=True)
        temporaire = tempfile.TemporaryDirectory(prefix="seamtech-restore-copy-", dir=dossier)
    except OSError:
        raise SauvegardeError("Impossible de préparer la copie privée du bundle") from None
    with temporaire as repertoire:
        copie = Path(repertoire) / "piece"
        try:
            try:
                relatif = source.relative_to(racine)
            except ValueError:
                raise SauvegardeError("La racine du bundle a changé pendant la restauration") from None
            with _ouvrir_source_bundle(racine, relatif) as entree:
                fd = os.open(copie, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as sortie:
                    shutil.copyfileobj(entree, sortie, length=1024 * 1024)
        except OSError:
            raise SauvegardeError("Impossible de copier une source confinée du bundle") from None
        yield copie


@contextmanager
def _figer_plan_restauration(plan: list, racine_sauvegarde: Path):
    """Fige et revérifie TOUTES les sources avant la première publication."""
    racine = Path(racine_sauvegarde).resolve()
    with ExitStack() as pile:
        figes = []
        for copie, source, empreinte, *reste in plan:
            privee = pile.enter_context(_instantane_bundle_prive(racine, source))
            try:
                identique = _sha256_fichier(privee) == empreinte
            except OSError:
                raise SauvegardeError("Impossible de vérifier une copie privée du bundle") from None
            if not identique:
                raise SauvegardeError("empreinte de copie privée différente du manifeste")
            figes.append((copie, privee, empreinte, *reste))
        yield figes


def _ecrire_copie_confinee(source: Path | BinaryIO, cible: Path, empreinte_attendue: str) -> None:
    """Crée exclusivement un fichier sous des répertoires ouverts sans liens."""
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd
            or os.link not in os.supports_dir_fd or os.link not in os.supports_follow_symlinks):
        raise SauvegardeError("Restauration locale sûre indisponible sur cette plateforme")
    fd_dir = None
    fd_fichier = None
    temporaire = f".seamtech-restore-{uuid.uuid4().hex}"
    identite = None
    try:
        fd_dir = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for partie in cible.parent.parts[1:]:
            try:
                os.mkdir(partie, 0o700, dir_fd=fd_dir)
            except FileExistsError:
                pass
            suivant = os.open(partie, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd_dir)
            os.close(fd_dir)
            fd_dir = suivant
        fd_fichier = os.open(temporaire, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd_dir)
        info = os.fstat(fd_fichier)
        identite = (info.st_dev, info.st_ino)
        entree_contexte = source.open("rb") if isinstance(source, Path) else nullcontext(source)
        with entree_contexte as entree, os.fdopen(fd_fichier, "w+b") as sortie:
            fd_fichier = None
            shutil.copyfileobj(entree, sortie, length=1024 * 1024)
            sortie.flush()
            sortie.seek(0)
            hacheur = hashlib.sha256()
            for bloc in iter(lambda: sortie.read(1024 * 1024), b""):
                hacheur.update(bloc)
            if hacheur.hexdigest() != empreinte_attendue:
                raise SauvegardeError("empreinte du fichier restauré différente du manifeste")
        # Publication atomique, sans remplacer une cible créée en concurrence.
        os.link(temporaire, cible.name, src_dir_fd=fd_dir, dst_dir_fd=fd_dir, follow_symlinks=False)
    except OSError:
        raise SauvegardeError("Impossible de créer la cible de restauration sans écrasement") from None
    finally:
        if fd_fichier is not None:
            os.close(fd_fichier)
        if fd_dir is not None:
            if identite is not None:
                try:
                    info = os.stat(temporaire, dir_fd=fd_dir, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == identite:
                        os.unlink(temporaire, dir_fd=fd_dir)
                except OSError:
                    pass
            os.close(fd_dir)


def _publier_fichiers_restaures(plan: list) -> dict:
    restaures: list[dict] = []
    for copie, source, empreinte, cible in plan:
        _ecrire_copie_confinee(source, cible, empreinte)
        restaures.append({"id": copie["id"], "path": str(cible), "sha256": empreinte})
    return {"nb": len(restaures), "fichiers": restaures}


def restaurer_fichiers_locaux(manifeste: dict, racine_sauvegarde: Path, destination: Path) -> dict:
    """Vérifie puis restaure les copies locales du bundle vers la cible."""
    plan = _plan_restaurer_fichiers(manifeste, racine_sauvegarde, destination)
    with _figer_plan_restauration(plan, racine_sauvegarde) as figes:
        return _publier_fichiers_restaures(figes)


def _plan_restaurer_objets(manifeste: dict, racine_sauvegarde: Path) -> list:
    """Vérifie les objets avant toute écriture."""
    copies = _verifier_copies_bundle(
        (manifeste.get("objets_s3") or {}).get("copies") or [], racine_sauvegarde
    )
    _valider_cles_objets(copies)
    return copies


def _valider_cles_objets(plan: list) -> None:
    empreintes: dict[str, str] = {}
    for copie, _, empreinte, *_ in plan:
        cle = copie.get("object_key")
        if not isinstance(cle, str) or not cle:
            raise SauvegardeError("clé objet absente du manifeste")
        if cle in empreintes and empreintes[cle] != empreinte:
            raise SauvegardeError("clé objet associée à plusieurs empreintes dans le manifeste")
        empreintes[cle] = empreinte


def _verifier_cles_client_objets(plan: list, client_s3) -> None:
    _valider_cles_objets(plan)
    appliquer_prefixe = getattr(client_s3, "_apply_prefix", None)
    if not callable(appliquer_prefixe):
        return  # Les doubles sans préfixe déclarent une clé identique à l'appel.
    for copie, *_ in plan:
        try:
            cle_effective = appliquer_prefixe(copie["object_key"])
        except Exception:
            raise SauvegardeError("Impossible de vérifier les clés de restauration objet") from None
        if cle_effective != copie["object_key"]:
            raise SauvegardeError("Le préfixe du client modifie une clé du manifeste")


def _verifier_publication_objets_exclusive(plan: list, client_s3) -> None:
    """N'accepte que l'interface explicite de création atomique sans écrasement.

    ``S3StorageClient.creer_fichier_exclusif`` l'implémente par une ÉCRITURE
    CONDITIONNELLE du service (``If-None-Match: *`` → 412 en cas de clé
    occupée) : un ``HEAD`` préalable suivi d'un ``PUT`` laisserait une fenêtre
    de concurrence. Le contrat doit être qualifié pour le fournisseur et sa
    configuration retenus (MinIO/R2 et versionnement), en présence d'écrivains
    concurrents — ce qui n'est pas fait ici et reste une limite ouverte.
    """
    if plan and not callable(getattr(client_s3, "creer_fichier_exclusif", None)):
        raise SauvegardeError("Restauration objet refusée : création exclusive atomique non disponible")


def _objet_existe(client_s3, cle: str) -> bool:
    """Vrai si la clé existe déjà — aucune écriture, aucun écrasement.

    Un fournisseur qui IGNORE ``If-None-Match: *`` (S3rver 2.2.9 le fait, et
    certains passerelles/proxys aussi) laisserait ``creer_fichier_exclusif``
    écraser la clé sans rien signaler : la restauration se terminerait « bien »
    en ayant détruit la seule copie restante d'un document. Savoir si la clé
    existe AVANT d'écrire ferme cette brèche ; la création exclusive reste le
    mécanisme atomique pour les fournisseurs qui l'honorent (elle couvre la
    concurrence, ce qu'un HEAD ne fait pas).

    Illisible ou absent : on s'en remet à la création exclusive, qui refuse
    l'écrasement chez tout fournisseur conforme.
    """
    try:
        client_s3.head_object(cle)
    except Exception:
        return False
    return True


def _objet_deja_conforme(client_s3, cle: str, empreinte: str) -> bool:
    """Vrai si la clé existe DÉJÀ avec exactement ces octets (aucune écriture)."""
    import tempfile

    dossier = os.environ.get("SEAMTECH_SAUVEGARDE_TMP")
    try:
        if dossier:
            Path(dossier).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="seamtech-restore-existing-", dir=dossier) as tmp:
            relu = Path(tmp) / "objet"
            client_s3.download_file(cle, relu)
            return _sha256_fichier(relu) == empreinte
    except Exception:
        # On ne sait pas lire l'objet existant : ne jamais écraser.
        return False


def _publier_objets_restaures(plan: list, client_s3) -> dict:
    _verifier_cles_client_objets(plan, client_s3)
    _verifier_publication_objets_exclusive(plan, client_s3)
    restaures: list[dict] = []
    deja_restaures: set[str] = set()
    for copie, source, empreinte in plan:
        cle = copie["object_key"]
        if cle in deja_restaures:
            restaures.append({"object_key": cle, "sha256": empreinte})
            continue
        # Le contrat exige une création atomique de la clé exacte : aucun
        # HEAD préalable ni réécriture automatique sur conflit ne suffit.
        #
        # Mais ce HEAD est AUSSI la seule défense contre un fournisseur qui
        # ignorerait la précondition : sans lui, la création exclusive passerait
        # en écrasant la clé. Voir ``_objet_existe``.
        if _objet_existe(client_s3, cle):
            if not _objet_deja_conforme(client_s3, cle, empreinte):
                raise SauvegardeError(
                    "Restauration objet refusée : la clé existe avec des octets différents "
                    "(aucun écrasement)."
                )
            deja_restaures.add(cle)
            restaures.append({"object_key": cle, "sha256": empreinte, "deja_present": True})
            continue
        try:
            cle_ecrite = client_s3.creer_fichier_exclusif(source, cle)
        except Exception as exc:
            # Clé déjà occupée : c'est une restauration qui N'ÉCRASE JAMAIS.
            # Si l'objet en place a exactement les octets du manifeste, la
            # restauration est déjà faite ; sinon on s'arrête — détruire la
            # seule copie restante d'un document est interdit.
            if type(exc).__name__ != "StorageCleExistante":
                raise SauvegardeError("Création exclusive de l'objet restauré impossible") from None
            if not _objet_deja_conforme(client_s3, cle, empreinte):
                raise SauvegardeError(
                    "Restauration objet refusée : la clé existe avec des octets différents "
                    "(aucun écrasement)."
                ) from None
            deja_restaures.add(cle)
            restaures.append({"object_key": cle, "sha256": empreinte, "deja_present": True})
            continue
        if cle_ecrite != cle:
            raise SauvegardeError("La clé de l'objet restauré diffère de celle du manifeste")
        import tempfile

        dossier = os.environ.get("SEAMTECH_SAUVEGARDE_TMP")
        try:
            if dossier:
                Path(dossier).mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="seamtech-restore-readback-", dir=dossier) as tmp:
                relu = Path(tmp) / "objet"
                client_s3.download_file(cle, relu)
                empreinte_relue = _sha256_fichier(relu)
        except Exception:
            # Un backend peut inclure des chemins ou secrets dans ses exceptions.
            raise SauvegardeError("Impossible de vérifier l'objet restauré par relecture") from None
        if empreinte_relue != empreinte:
            raise SauvegardeError("L'empreinte de l'objet relu diffère du manifeste")
        deja_restaures.add(cle)
        restaures.append({"object_key": cle_ecrite, "sha256": empreinte})
    return {"nb": len(restaures), "objets": restaures}


def restaurer_objets_s3(manifeste: dict, racine_sauvegarde: Path, client_s3) -> dict:
    """Restaure les objets du bundle vers un bucket cible (sources inaccessibles)."""
    copies = _plan_restaurer_objets(manifeste, racine_sauvegarde)
    with _figer_plan_restauration(copies, racine_sauvegarde) as figes:
        return _publier_objets_restaures(figes, client_s3)


#: Motifs ignorés dans une sauvegarde de répertoire de données : ce sont des
#: fichiers VOLATILES (verrous, temporaires d'écriture, pid), dont la copie
#: donnerait une fausse impression d'exhaustivité tout en produisant une
#: restauration inutilisable (un verrou restauré bloque le démarrage).
MOTIFS_VOLATILES = ("*.lock", "*.tmp", "*.pid", "*.lck", ".~lock.*")


def inventorier_data(racines_data: list[Path], exclure: list[Path] | None = None) -> dict:
    """Inventaire COMPLET des répertoires de données (pas seulement le référencé).

    Le rapport du 28/10 reprochait exactement cela : la sauvegarde ne couvrait
    que les fichiers *référencés en base*. Or ``./data`` contient aussi les
    lots téléversés en cours de promotion (``uploads/lots-televerses``), la
    quarantaine, les rapports générés — des octets qui n'ont aucune ligne dans
    ``documents`` et qu'aucune sauvegarde ne restituait.

    Aucune écriture, aucun suivi de lien symbolique sortant : un lien qui sort
    de la racine est signalé, jamais suivi (il ferait entrer un fichier hors
    périmètre dans la sauvegarde).
    """
    racines = [Path(racine) for racine in racines_data or []]
    exclusions = [Path(chemin).resolve() for chemin in (exclure or [])]
    fichiers: list[dict] = []
    ignorés_volatiles = 0
    liens_sortants: list[dict] = []
    absentes: list[str] = []
    for numero, racine in enumerate(racines):
        if not racine.exists():
            absentes.append(str(racine))
            continue
        if not racine.is_dir():
            absentes.append(str(racine))
            continue
        for chemin in sorted(racine.rglob("*")):
            if any(chemin.resolve() == exclusion or exclusion in chemin.resolve().parents for exclusion in exclusions):
                continue
            if chemin.is_symlink():
                try:
                    chemin.resolve().relative_to(racine.resolve())
                except ValueError:
                    liens_sortants.append({"racine": str(racine), "chemin": str(chemin.relative_to(racine))})
                    continue
            if not chemin.is_file():
                continue
            if any(chemin.match(motif) for motif in MOTIFS_VOLATILES):
                ignorés_volatiles += 1
                continue
            try:
                taille = chemin.stat().st_size
                empreinte = _sha256_fichier(chemin)
            except OSError as exc:
                files_illisible = {"racine": str(racine), "chemin": str(chemin.relative_to(racine)), "erreur": type(exc).__name__}
                fichiers.append({**files_illisible, "taille": 0, "sha256": ""})
                continue
            fichiers.append(
                {
                    "racine_index": numero,
                    "racine": str(racine),
                    "chemin": str(chemin.relative_to(racine)),
                    "taille": taille,
                    "sha256": empreinte,
                }
            )
    return {
        "racines": [str(racine) for racine in racines],
        "absentes": absentes,
        "nb_fichiers": len(fichiers),
        "octets": sum(f["taille"] for f in fichiers),
        "ignores_volatiles": ignorés_volatiles,
        "liens_sortants": liens_sortants,
        "illisibles": [f for f in fichiers if not f["sha256"]],
        "fichiers": [f for f in fichiers if f["sha256"]],
    }


def copier_data(racines_data: list[Path], destination: Path, exclure: list[Path] | None = None) -> dict:
    """Copie l'arborescence de données dans le bundle, en conservant les chemins.

    L'arborescence est reproduite à l'identique sous ``data/<n>/`` : une
    restauration remet chaque fichier à SA place, pas dans un vrac indexé par
    identifiant. Les volatiles sont ignorés, les liens sortants signalés.
    """
    inventaire = inventorier_data(racines_data, exclure=exclure)
    base = Path(destination)
    copies: list[dict] = []
    echecs: list[dict] = []
    for fichier in inventaire["fichiers"]:
        source = Path(fichier["racine"]) / fichier["chemin"]
        relatif = Path("data") / str(fichier["racine_index"]) / fichier["chemin"]
        cible = base / relatif
        try:
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)
        except OSError as exc:
            echecs.append({"chemin": fichier["chemin"], "erreur": type(exc).__name__})
            continue
        empreinte = _sha256_fichier(cible)
        if empreinte != fichier["sha256"]:
            echecs.append({"chemin": fichier["chemin"], "erreur": "empreinte copiée différente"})
            continue
        copies.append({**fichier, "path_relatif": relatif.as_posix()})
    return {
        "copies": copies,
        "echecs": echecs,
        "nb_copies": len(copies),
        "nb_echecs": len(echecs),
        "ignores_volatiles": inventaire["ignores_volatiles"],
        "liens_sortants": inventaire["liens_sortants"],
        "absentes": inventaire["absentes"],
    }


def restaurer_data(manifeste: dict, racine_sauvegarde: Path, destination: Path) -> dict:
    """Restaure l'arborescence de données du bundle vers ``destination``.

    Chaque fichier est vérifié (SHA-256 du manifeste) AVANT d'être écrit à sa
    place ; un écart arrête la restauration — mieux vaut zéro fichier restauré
    qu'une arborescence partiellement fausse qu'on croirait complète.
    """
    destination = Path(destination)
    restaures: list[dict] = []
    for copie in (manifeste.get("data") or {}).get("copies") or []:
        source = Path(racine_sauvegarde) / copie["path_relatif"]
        if not source.is_file():
            raise SauvegardeError(f"fichier de données absent du bundle : {copie['path_relatif']}")
        empreinte = _sha256_fichier(source)
        if empreinte != copie["sha256"]:
            raise SauvegardeError(
                f"empreinte données {copie['chemin']} {empreinte[:12]}… ≠ manifeste {copie['sha256'][:12]}…"
            )
        cible = destination / copie["chemin"]
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, cible)
        restaures.append({"chemin": copie["chemin"], "sha256": empreinte, "path": str(cible)})
    return {"nb": len(restaures), "fichiers": restaures}


def restaurer_complet(
    base_cible_url: str,
    manifeste: dict,
    racine_sauvegarde: Path,
    *,
    data_cible: Path | None = None,
    fichiers_cible: Path | None = None,
    client_s3=None,
    verifier_apres: bool = True,
    chemin_dump_local: Path | None = None,
) -> dict:
    """Restauration COORDONNÉE : base + arborescence de données + objets, puis contrôle.

    C'est le point que le rapport jugeait « non prouvé » : un dump restauré seul
    ne dit rien des fichiers, et des fichiers restaurés seuls ne disent rien de
    l'état métier. Ici les trois composants sont restaurés depuis le MÊME
    manifeste (donc du même instantané), et le compte rendu dit, composant par
    composant, ce qui a été restauré, vérifié ou refusé.

    Ordre volontaire : base d'abord (elle valide le manifeste et échoue vite),
    puis données, puis objets. Un échec arrête tout et laisse le compte rendu
    des composants déjà traités — jamais un succès partiel tu.
    """
    rapport: dict = {"base": None, "data": None, "fichiers": None, "objets": None, "verification": None}
    if chemin_dump_local is None:
        # Le dump est attendu DANS le bundle, à côté du manifeste : c'est
        # l'agencement produit par `sauver`. Sans ce repli, une restauration
        # locale exigerait une clé S3 pour un dump présent sur le disque.
        candidat = Path(racine_sauvegarde) / manifeste["dump"]["fichier"]
        chemin_dump_local = candidat if candidat.is_file() else None
    rapport["base"] = restaurer(
        base_cible_url, manifeste, chemin_dump_local=chemin_dump_local, client_s3=client_s3
    )
    if data_cible is not None and (manifeste.get("data") or {}).get("copies"):
        rapport["data"] = restaurer_data(manifeste, Path(racine_sauvegarde), Path(data_cible))
    if fichiers_cible is not None and (manifeste.get("fichiers_locaux") or {}).get("copies"):
        rapport["fichiers"] = restaurer_fichiers_locaux(manifeste, Path(racine_sauvegarde), Path(fichiers_cible))
    if client_s3 is not None and (manifeste.get("objets_s3") or {}).get("copies"):
        rapport["objets"] = restaurer_objets_s3(manifeste, Path(racine_sauvegarde), client_s3)
    if verifier_apres:
        rapport["verification"] = verifier(base_cible_url, manifeste)
        if not rapport["verification"]["ok"]:
            raise SauvegardeError(
                "restauration terminée mais NON conforme au manifeste : "
                + "; ".join(rapport["verification"]["ecarts"][:5])
            )
        # P0 — la protection globale est PROUVÉE ici, ou pas du tout : un dump
        # restauré seul ne dit rien des fichiers ni des objets. Ce n'est qu'une
        # restauration coordonnée (base + arborescence + objets du même
        # instantané) CONTRÔLÉE contre le manifeste qui autorise à déclarer la
        # sauvegarde complète, et donc à laisser la rétention élaguer.
        _qualifier_apres_restauration_prouvee(manifeste, Path(racine_sauvegarde), client_s3, rapport)
    return rapport


def _qualifier_apres_restauration_prouvee(
    manifeste: dict,
    racine_sauvegarde: Path,
    client_s3,
    rapport: dict,
) -> None:
    """Marque le manifeste « protection globale prouvée » après contrôle.

    La preuve n'est pas déclarative : elle n'est écrite que si la restauration
    coordonnée a été contrôlée composant par composant contre le manifeste. Tant
    que ce n'est pas le cas, ``complet`` reste faux et AUCUNE purge automatique
    n'est autorisée — c'est le sens du ``return 2`` de la commande ``sauver``.
    """
    verification = rapport.get("verification") or {}
    if verification.get("ok") is not True:
        return
    composants = [
        nom
        for nom in ("base", "data", "fichiers", "objets")
        if rapport.get(nom) is not None
    ]
    manifeste["composants_restaures"] = composants
    manifeste["complet_perimetre_demande"] = True
    manifeste["protection_globale_prouvee"] = True
    manifeste["retention_automatique_autorisee"] = True
    manifeste["complet"] = True
    manifeste["preuve"] = {
        "restauration": "coordonnée (base + données + objets du même manifeste)",
        "controle": "conformité au manifeste après restauration",
        "verification": "ok",
    }
    chemin = racine_sauvegarde / f"{manifeste['dump']['fichier']}.manifest.json"
    ecrit = False
    try:
        chemin.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
        ecrit = True
    except OSError as erreur:
        logger.warning("Manifeste non réécrit après preuve de restauration (%s).", type(erreur).__name__)
    manifeste["preuve"]["manifeste_local_reecrit"] = ecrit
    # Côté objet, la preuve n'est publiée que si elle peut être RELUE : un
    # manifeste distant non mis à jour laisse la rétention conservative.
    if client_s3 is not None and manifeste.get("manifeste_cle_s3"):
        try:
            import tempfile

            empreinte = _sha256_fichier(chemin)
            cle = client_s3.upload_file(chemin, str(manifeste["manifeste_cle_s3"]))
            with tempfile.TemporaryDirectory() as dossier_temporaire:
                relu = Path(dossier_temporaire) / "manifeste.json"
                client_s3.download_file(cle, relu)
                manifeste["preuve"]["manifeste_objet_republie"] = _sha256_fichier(relu) == empreinte
        except Exception as erreur:  # publication impossible : rester prudent
            logger.warning("Manifeste non republié après preuve (%s).", type(erreur).__name__)
            manifeste["preuve"]["manifeste_objet_republie"] = False


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


def _etat_base(base_url: str, *, connexion=None) -> dict:
    """Comptes par table, fiches par statut, documents, versions de schéma."""
    connexion_interne = connexion is None
    if connexion_interne:
        connexion = _connecter(base_url)
    try:
        tables = _compter_tables(connexion)
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut, COUNT(*) FROM fiche GROUP BY statut ORDER BY statut")
            fiches_par_statut = {statut: int(n) for statut, n in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) FROM documents")
            documents = int(cursor.fetchone()[0])
            identite_valide = schema_identite_octets_valide_pg(cursor)
        return {
            "tables": tables,
            "fiches_par_statut": fiches_par_statut,
            "documents": documents,
            "versions_schema": _versions_schema(connexion),
            "schema_identite_octets_valide": identite_valide,
        }
    finally:
        if connexion_interne:
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
    racines_data: list[Path] | None = None,
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
            racines_data=racines_data,
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
    racines_data: list[Path] | None = None,
) -> dict:
    horodatage = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}-{uuid.uuid4().hex}"
    nom_dump = f"seamtech-search-{horodatage}.dump"
    chemin_dump = dossier_local / nom_dump

    pg_dump = _trouver_binaire("pg_dump")
    # Le snapshot exporté reste vivant jusqu'à la fin du dump ET de la lecture
    # des références. Ce mécanisme coordonne uniquement les données PostgreSQL,
    # pas les octets du filesystem ou des buckets copiés ensuite.
    connexion = _connecter(base_url)
    try:
        connexion.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with connexion.cursor() as cursor:
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        etat = _etat_base(base_url, connexion=connexion)
        for version in (VERSION_SCHEMA_METIER, VERSION_SCHEMA_APPLICATION):
            if version not in etat["versions_schema"]:
                raise SauvegardeError(
                    f"la base n'est pas à jour ({version} absent de schema_migrations) — "
                    "refus de sauvegarder un état non migré"
                )
        if not etat.get("schema_identite_octets_valide", False):
            raise SauvegardeError("Schéma d'identité des octets incomplet : colonne ou déclencheur absent ou désactivé")
        refs = inventorier_references_documents(base_url, connexion=connexion) if inclure_references_base else None
        # Les octets locaux sont copiés AVANT le dump, dans la MÊME transaction
        # de snapshot : la copie porte sur l'état vu au moment du snapshot, et
        # non sur un état postérieur que la base ne connaîtrait pas encore.
        # Copier après le dump donnait l'inverse : un fichier modifié entre les
        # deux entrait dans le bundle sans que rien ne le signale.
        lot_local: dict | None = None
        if inclure_references_base:
            assert refs is not None
            lot_local = copier_fichiers_locaux(refs, dossier_local / "bundles" / nom_dump)
        dsn_public, environnement = _parametres_pg_process(base_url)
        resultat = subprocess.run(
            [pg_dump, "--format=custom", f"--file={chemin_dump}", f"--snapshot={snapshot}", dsn_public],
            capture_output=True, text=True, env=environnement,
        )
        if resultat.returncode != 0:
            # stderr peut contenir une URI ou d'autres secrets de configuration.
            raise SauvegardeError(f"pg_dump a échoué (rc={resultat.returncode})")
        if lot_local is not None:
            lot_local["derive"] = _verifier_derive_locale(lot_local["copies"])
    finally:
        connexion.close()

    inventaire = inventorier_archive(racines_archive)
    manifeste: dict = {
        "date_iso": datetime.now(timezone.utc).isoformat(),
        "commit": _commit_courant(),
        "version_schema_metier": VERSION_SCHEMA_METIER,
        "version_schema_application": VERSION_SCHEMA_APPLICATION,
        "dump": {"fichier": nom_dump, "octets": chemin_dump.stat().st_size, "sha256": _sha256_fichier(chemin_dump)},
        "tables": etat["tables"],
        "fiches_par_statut": etat["fiches_par_statut"],
        "documents": etat["documents"],
        "archive": inventaire,
        "rpo_rto": RPO_RTO_A_DECIDER,
        "coherence": (
            "dump, références et copies locales dans un snapshot MVCC partagé ; "
            "les copies précédent le dump et sont revérifiées après (dérive détectée "
            "et nommée, jamais corrigée) ; inventaire archive indépendant, sans gel applicatif"
        ),
        "coherence_references_snapshot": inclure_references_base,
        "references_incluses": inclure_references_base,
        "protection_globale_prouvee": False,
        "retention_automatique_autorisee": False,
        "complet": False,
        "complet_perimetre_demande": True,
    }
    # P0.1 : l'arborescence de données est sauvegardée EN ENTIER (pas seulement
    # ce que la base référence). C'est ce qui couvre les lots téléversés en
    # attente de promotion, la quarantaine et les rapports générés.
    if racines_data:
        lot_data = copier_data(
            [Path(racine) for racine in racines_data],
            dossier_local,
            exclure=[dossier_local],
        )
        manifeste["data"] = {
            "nb_copies": lot_data["nb_copies"],
            "nb_echecs": lot_data["nb_echecs"],
            "ignores_volatiles": lot_data["ignores_volatiles"],
            "liens_sortants": lot_data["liens_sortants"],
            "absentes": lot_data["absentes"],
            "echecs": lot_data["echecs"],
            "copies": lot_data["copies"],
        }
        if lot_data["nb_echecs"]:
            manifeste["complet"] = False
    if inclure_references_base:
        assert refs is not None
        manifeste["references"] = {
            "nb_documents": refs["nb_documents"],
            "nb_locaux_manquants": refs["nb_locaux_manquants"],
            "nb_avec_objet": refs["nb_avec_objet"],
        }
        dossier_bundle = dossier_local / "bundles" / nom_dump
        fichiers = lot_local
        assert fichiers is not None
        for copie in fichiers["copies"]:
            copie["path_relatif"] = str(Path("bundles") / nom_dump / copie["path_relatif"])
        manifeste["fichiers_locaux"] = {
            "nb_copies": fichiers["nb_copies"],
            "nb_manquants": fichiers["nb_manquants"],
            "copies": fichiers["copies"],
            "manquants": fichiers["manquants"],
        }
        if fichiers["nb_manquants"]:
            manifeste["complet_perimetre_demande"] = False
        # Les copies précèdent le dump et sont revérifiées après : une source
        # modifiée pendant la fenêtre est NOMMÉE, jamais absorbée en silence.
        manifeste["derive_octets_locaux"] = {
            "detectee": bool(fichiers.get("derive")),
            "nb_fichiers": len(fichiers.get("derive") or []),
            "fichiers": (fichiers.get("derive") or [])[:50],
            "methode": (
                "copies prises dans la transaction du snapshot, taille et horodatage "
                "de chaque source revérifiés après le dump"
            ),
        }
        if fichiers.get("derive"):
            manifeste["complet"] = False
            manifeste["complet_perimetre_demande"] = False
        if client_documents_s3 is not None:
            objets = copier_objets_s3(refs, client_documents_s3, dossier_bundle)
            for copie in objets["copies"]:
                copie["path_relatif"] = str(Path("bundles") / nom_dump / copie["path_relatif"])
            manifeste["objets_s3"] = {
                "nb_copies": objets["nb_copies"],
                "nb_manquants": objets["nb_manquants"],
                "copies": objets["copies"],
                "manquants": objets["manquants"],
            }
            if objets["nb_manquants"]:
                manifeste["complet_perimetre_demande"] = False
        else:
            objets_non_copies = [
                {"id": doc["id"], "object_key": doc["object_key"], "erreur": "client documents S3 non fourni"}
                for doc in refs["documents"] if doc.get("object_key")
            ]
            manifeste["objets_s3"] = {
                "nb_copies": 0, "nb_manquants": len(objets_non_copies),
                "copies": [], "manquants": objets_non_copies,
                "note": "client documents S3 non fourni",
            }
            if objets_non_copies:
                manifeste["complet_perimetre_demande"] = False
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
        # Chaque copie référencée reçoit une clé spécifique à cette génération.
        # Les clés ne contiennent ni chemin d'origine ni nom de document.
        # Aucun manifeste n'est publié et aucune rétention n'a lieu si une
        # copie envoyée ne peut pas être relue à l'identique.
        for section, categorie in (("fichiers_locaux", "fichiers"), ("objets_s3", "objets")):
            copies = (manifeste.get(section) or {}).get("copies") or []
            for copie, source, empreinte in _verifier_copies_bundle(copies, dossier_local):
                cle = f"{PREFIXE_SAUVEGARDES}{horodatage}/{categorie}/{copie['id']}"
                cle_ecrite = client_s3.upload_file(source, cle, avoid_overwrite=True)
                with tempfile.TemporaryDirectory() as tmp:
                    relecture = Path(tmp) / "copie"
                    client_s3.download_file(cle_ecrite, relecture)
                    if _sha256_fichier(relecture) != empreinte:
                        raise SauvegardeError("vérification après envoi d'une copie ÉCHOUÉE")
                copie["cle_s3_sauvegarde"] = cle_ecrite
        # Le fichier envoyé doit contenir la clé réellement attribuée au dump,
        # y compris lorsque le stockage a évité une collision de nom.
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
        empreinte_manifeste = _sha256_fichier(chemin_manifeste)
        cle_manifeste = client_s3.upload_file(
            chemin_manifeste, f"{PREFIXE_SAUVEGARDES}{horodatage}.dump.manifest.json", avoid_overwrite=True
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifeste_relu = Path(tmp) / "manifeste.json"
            client_s3.download_file(cle_manifeste, manifeste_relu)
            if _sha256_fichier(manifeste_relu) != empreinte_manifeste:
                raise SauvegardeError("vérification après envoi du manifeste ÉCHOUÉE")
        manifeste["manifeste_cle_s3"] = cle_manifeste
        chemin_manifeste.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding="utf-8")
        # Une copie partielle/non coordonnée ne doit jamais remplacer une
        # sauvegarde globale lors de l'élagage automatique.
        purges = appliquer_retention(client_s3, retention, dossier_local=dossier_local) if manifeste["complet"] and manifeste["protection_globale_prouvee"] and manifeste["retention_automatique_autorisee"] else []
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
        purges = appliquer_retention(None, retention, dossier_local=dossier_local) if manifeste["complet"] and manifeste["protection_globale_prouvee"] and manifeste["retention_automatique_autorisee"] else []
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
    import tempfile

    nb_a_garder = max(1, int(conserver))
    supprimees: list[str] = []
    # Construire l'intégralité du plan AVANT les suppressions. Un manifeste
    # absent, illisible ou d'état inconnu interdit toute purge automatique.
    complets_s3: list[str] = []
    complets_locaux: list[Path] = []
    cles: list[str] = []
    if client_s3 is not None:
        cles = client_s3.list_keys(PREFIXE_SAUVEGARDES)
        with tempfile.TemporaryDirectory(prefix="seamtech-retention-") as tmp:
            for cle in sorted(c for c in cles if c.endswith(".dump")):
                cle_manifeste = f"{cle}.manifest.json"
                if cle_manifeste not in cles:
                    return []
                chemin = Path(tmp) / "manifeste.json"
                try:
                    client_s3.download_file(cle_manifeste, chemin)
                    etat = json.loads(chemin.read_text(encoding="utf-8"))
                    complet = etat.get("complet")
                    qualification = etat.get("protection_globale_prouvee")
                except (OSError, ValueError, AttributeError, RuntimeError):
                    return []
                if type(complet) is not bool or type(qualification) is not bool or complet != qualification:
                    return []
                if complet and qualification:
                    complets_s3.append(cle)

    if dossier_local is not None:
        dossier = Path(dossier_local)
        if dossier.is_dir():
            for dump_f in sorted(dossier.glob("*.dump")):
                chemin = dossier / f"{dump_f.name}.manifest.json"
                try:
                    etat = json.loads(chemin.read_text(encoding="utf-8"))
                    complet = etat.get("complet")
                    qualification = etat.get("protection_globale_prouvee")
                except (OSError, ValueError, AttributeError):
                    return []
                if type(complet) is not bool or type(qualification) is not bool or complet != qualification:
                    return []
                if complet and qualification:
                    complets_locaux.append(dump_f)

    if client_s3 is not None:
        for cle in complets_s3[:-nb_a_garder]:
            if not client_s3.delete_file(cle):
                continue
            supprimees.append(cle)
            cle_manifeste = f"{cle}.manifest.json"
            if client_s3.delete_file(cle_manifeste):
                supprimees.append(cle_manifeste)

    for dump_f in complets_locaux[:-nb_a_garder]:
        try:
            dump_f.unlink()
            supprimees.append(str(dump_f))
        except OSError:
            continue
        chemin = dump_f.parent / f"{dump_f.name}.manifest.json"
        try:
            chemin.unlink()
            supprimees.append(str(chemin))
        except OSError:
            pass

    return supprimees


# ---------------------------------------------------------------------------
# Restauration
# ---------------------------------------------------------------------------

@contextmanager
def _instantane_dump_prive(chemin: Path):
    """Copie privée vérifiable : pg_restore ne relit jamais le chemin d'origine."""
    import tempfile

    dossier = os.environ.get("SEAMTECH_SAUVEGARDE_TMP")
    try:
        if dossier:
            Path(dossier).mkdir(parents=True, exist_ok=True)
        temporaire = tempfile.TemporaryDirectory(prefix="seamtech-restore-copy-", dir=dossier)
    except OSError:
        raise SauvegardeError("Impossible de préparer la copie privée du dump") from None
    with temporaire as repertoire:
        copie = Path(repertoire) / "dump"
        try:
            chemin_absolu = _chemin_absolu_sans_traversee(chemin)
            with _ouvrir_source_bundle(chemin_absolu.parent, Path(chemin_absolu.name)) as source:
                fd_copie = os.open(copie, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd_copie, "wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        except OSError:
            raise SauvegardeError("Impossible de copier de manière sûre le dump de restauration") from None
        yield copie


def _nom_dump_manifeste(manifeste: dict) -> str:
    nom = (manifeste.get("dump") or {}).get("fichier")
    if (not isinstance(nom, str) or nom in {"", ".", ".."}
            or "/" in nom or "\\" in nom or "\x00" in nom):
        raise SauvegardeError("nom du dump invalide dans le manifeste")
    return nom


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
        nom_dump = _nom_dump_manifeste(manifeste)
        # Un répertoire privé évite de remplacer un fichier ou de suivre un
        # lien déjà présent à un chemin contrôlé par le manifeste.
        import tempfile

        dossier_temp = Path(os.environ.get("SEAMTECH_SAUVEGARDE_TMP", "data/backups"))
        dossier_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="seamtech-restore-", dir=dossier_temp) as tmp:
            chemin_telecharge = Path(tmp) / nom_dump
            client_s3.download_file(manifeste["dump"]["cle_s3"], chemin_telecharge)
            return restaurer(base_cible_url, manifeste, chemin_dump_local=chemin_telecharge)

    with _instantane_dump_prive(Path(chemin_dump_local)) as copie:
        return _restaurer_dump_verifie(base_cible_url, manifeste, copie, debut)


def _restaurer_dump_verifie(base_cible_url: str, manifeste: dict, chemin_dump: Path, debut: float) -> dict:
    try:
        empreinte = _sha256_fichier(chemin_dump)
    except OSError:
        raise SauvegardeError("Impossible de vérifier la copie privée du dump") from None
    if empreinte != manifeste["dump"]["sha256"]:
        raise SauvegardeError(
            f"empreinte du dump {empreinte[:12]}… ≠ manifeste {manifeste['dump']['sha256'][:12]}… — "
            "restauration REFUSÉE avant toute écriture"
        )

    nom_cible = _nom_base(base_cible_url)
    taille_attendue = manifeste["dump"].get("octets")
    try:
        taille_copie = chemin_dump.stat().st_size
    except OSError:
        raise SauvegardeError("Impossible de vérifier la taille de la copie privée du dump") from None
    if type(taille_attendue) is not int or taille_attendue < 0 or taille_attendue != taille_copie:
        raise SauvegardeError("taille du dump invalide ou différente du manifeste — restauration refusée")
    pg_restore = _trouver_binaire("pg_restore")
    dsn_public, environnement = _parametres_pg_process(base_cible_url)
    # Lit et décompresse les données de l'archive sans exécuter les commandes
    # SQL ni exposer leur contenu ou les diagnostics potentiellement sensibles.
    try:
        controle = subprocess.run(
            [pg_restore, "--exit-on-error", "--file=-", str(chemin_dump)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise SauvegardeError("Impossible de lancer pg_restore pour vérifier l'archive") from None
    if controle.returncode != 0:
        raise SauvegardeError("archive illisible ou incompatible avec pg_restore — restauration refusée")
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

    try:
        resultat = subprocess.run(
            [pg_restore, "--no-owner", "--exit-on-error", f"--dbname={dsn_public}", str(chemin_dump)],
            capture_output=True,
            text=True,
            env=environnement,
        )
    except OSError:
        raise SauvegardeError("Impossible de lancer pg_restore ; la base cible peut être partielle") from None
    if resultat.returncode != 0:
        # stderr peut contenir une URI ou d'autres secrets de configuration.
        raise SauvegardeError(f"pg_restore a échoué (rc={resultat.returncode}) ; la base cible peut être partielle")

    etat = _etat_base(base_cible_url)
    version_application = manifeste.get("version_schema_application")
    if version_application and (
        version_application not in etat["versions_schema"] or not etat.get("schema_identite_octets_valide", False)
    ):
        raise SauvegardeError(
            "Restauration incomplète : schéma d'identité des octets absent ou déclencheur désactivé ; "
            "la base cible peut être partielle"
        )
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
    version_application = manifeste.get("version_schema_application")
    if version_application and version_application not in etat["versions_schema"]:
        ecarts.append(f"VERSION_SCHEMA_APPLICATION {version_application} absente de schema_migrations")
    if version_application and not etat.get("schema_identite_octets_valide", False):
        ecarts.append("Schéma d'identité des octets incomplet : colonne ou déclencheur absent ou désactivé")
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
    base_url_env = os.environ.get("SEAMTECH_DATABASE_URL")
    p_sauver.add_argument("--base-url", default=base_url_env, required=not bool(base_url_env),
                          help="URL PostgreSQL ; préférer SEAMTECH_DATABASE_URL pour éviter le mot de passe en argument")
    p_sauver.add_argument("--archive", nargs="+", required=True, help="racine(s) de l'archive, en lecture seule")
    p_sauver.add_argument("--dossier", default="data/backups")
    p_sauver.add_argument("--retention", type=int, default=5)
    p_sauver.add_argument("--sans-s3", action="store_true", help="pas d'envoi hors-site (sauvegarde locale seule)")
    p_sauver.add_argument(
        "--avec-references",
        action="store_true",
        help="copie les fichiers locaux et objets S3 réellement référencés par documents",
    )
    p_sauver.add_argument(
        "--data",
        nargs="*",
        default=None,
        help="répertoire(s) de données à sauvegarder EN ENTIER (ex. ./data : lots téléversés, quarantaine)",
    )

    p_restaurer = sous.add_parser("restaurer", help="restaure depuis un manifeste (local ou S3)")
    p_restaurer.add_argument("--manifeste", help="chemin local du manifeste")
    p_restaurer.add_argument("--cle-manifeste", help="clé S3 du manifeste (téléchargé)")
    cible_url_env = os.environ.get("SEAMTECH_RESTORE_DATABASE_URL")
    p_restaurer.add_argument("--base-cible", default=cible_url_env, required=not bool(cible_url_env),
                             help="base distincte ; préférer SEAMTECH_RESTORE_DATABASE_URL")
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
    p_restaurer.add_argument(
        "--data-cible",
        help="répertoire où remettre l'arborescence de données du bundle (./data)",
    )
    p_restaurer.add_argument(
        "--coordonnee",
        action="store_true",
        help="restauration COORDONNÉE (base + données + objets) avec contrôle final du manifeste",
    )

    p_verifier = sous.add_parser("verifier", help="compare une base vivante au manifeste")
    p_verifier.add_argument("--manifeste", help="chemin local du manifeste")
    p_verifier.add_argument("--cle-manifeste", help="clé S3 du manifeste (téléchargé)")
    p_verifier.add_argument("--base-url", default=base_url_env, required=not bool(base_url_env),
                            help="URL PostgreSQL ; préférer SEAMTECH_DATABASE_URL")
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
            racines_data=[Path(r) for r in (args.data or [])] or None,
        )
        sortie = {
            "manifeste": str(Path(args.dossier) / (manifeste["dump"]["fichier"] + ".manifest.json")),
            "dump_sha256": manifeste["dump"]["sha256"],
            "complet": manifeste.get("complet", False),
            "complet_perimetre_demande": manifeste.get("complet_perimetre_demande", False),
            "protection_globale_prouvee": manifeste.get("protection_globale_prouvee", False),
            "coherence_references_snapshot": manifeste.get("coherence_references_snapshot", False),
            "references_incluses": manifeste.get("references_incluses", False),
            "retention_automatique_autorisee": manifeste.get("retention_automatique_autorisee", False),
        }
        if "cle_s3" in manifeste["dump"]:
            sortie["cle_dump_s3"] = manifeste["dump"]["cle_s3"]
        if "manifeste_cle_s3" in manifeste:
            sortie["cle_manifeste_s3"] = manifeste["manifeste_cle_s3"]
        print(json.dumps(sortie, ensure_ascii=False))
        if manifeste.get("protection_globale_prouvee") is not True:
            logger.warning("Sauvegarde non qualifiée comme protection globale : rétention automatique suspendue.")
            return 2
        return 0 if manifeste.get("complet") is True else 2

    if args.commande == "restaurer":
        client = _client_s3_depuis_env()
        manifeste = charger_manifeste(Path(args.manifeste) if args.manifeste else None, args.cle_manifeste, client)
        if args.coordonnee:
            racine_bundle = Path(args.manifeste).parent if args.manifeste else None
            if racine_bundle is None:
                raise SauvegardeError("restauration coordonnée : fournir --manifeste (bundle local)")
            rapport = restaurer_complet(
                args.base_cible,
                manifeste,
                racine_bundle,
                data_cible=Path(args.data_cible) if args.data_cible else None,
                fichiers_cible=Path(args.fichiers_cible) if args.fichiers_cible else None,
                client_s3=client if args.restaurer_objets else None,
            )
            print(
                json.dumps(
                    {
                        "base": rapport["base"]["base"],
                        "data": (rapport["data"] or {}).get("nb", 0),
                        "fichiers": (rapport["fichiers"] or {}).get("nb", 0),
                        "objets": (rapport["objets"] or {}).get("nb", 0),
                        "verification_ok": (rapport["verification"] or {}).get("ok"),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.dump:
            dump_local: Path | None = Path(args.dump)
        elif args.manifeste:
            # Repli local : le dump est attendu À CÔTÉ du manifeste.
            dump_local = Path(args.manifeste).parent / _nom_dump_manifeste(manifeste)
        else:
            dump_local = None  # re-téléchargé depuis le bucket
        import tempfile
        from contextlib import ExitStack

        with ExitStack() as pile:
            racine_bundle = Path(args.manifeste).parent if args.manifeste else Path(args.dump).parent if args.dump else None
            if (args.fichiers_cible or args.restaurer_objets) and racine_bundle is None:
                if client is None:
                    raise SauvegardeError("restauration des pièces : client de sauvegarde absent")
                racine_bundle = Path(pile.enter_context(tempfile.TemporaryDirectory(prefix="seamtech-bundle-")))
                sections = tuple(section for active, section in (
                    (args.fichiers_cible, "fichiers_locaux"), (args.restaurer_objets, "objets_s3")
                ) if active)
                _telecharger_bundle_distant(manifeste, client, racine_bundle, sections)
            fichiers_figes = None
            objets_figes = None
            if args.fichiers_cible:
                plan_fichiers = _plan_restaurer_fichiers(manifeste, racine_bundle, Path(args.fichiers_cible))
                fichiers_figes = pile.enter_context(_figer_plan_restauration(plan_fichiers, racine_bundle))
            if args.restaurer_objets:
                plan_objets = _plan_restaurer_objets(manifeste, racine_bundle)
                objets_figes = pile.enter_context(_figer_plan_restauration(plan_objets, racine_bundle))
                from .config import AppConfig
                from .storage import S3StorageClient

                try:
                    config_docs = AppConfig.load()
                except FileNotFoundError as exc:
                    raise SauvegardeError(
                        "restauration des objets : configuration applicative absente "
                        "(identité du bucket DOCUMENTS, distincte de la sauvegarde)"
                    ) from exc
                client_documents = S3StorageClient(config=config_docs)
                _verifier_cles_client_objets(objets_figes, client_documents)
                _verifier_publication_objets_exclusive(objets_figes, client_documents)
            resume = restaurer(args.base_cible, manifeste, chemin_dump_local=dump_local, client_s3=client)
            extra: dict = {}
            if fichiers_figes is not None:
                extra["fichiers"] = _publier_fichiers_restaures(fichiers_figes)
            if objets_figes is not None:
                extra["objets"] = _publier_objets_restaures(objets_figes, client_documents)
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
