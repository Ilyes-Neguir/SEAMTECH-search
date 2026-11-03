"""Lecture confinée et copie vérifiée des artefacts servis par l'API."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from starlette.responses import FileResponse, StreamingResponse

from .import_pipeline import _racine_scan_confinee
from .plateforme import exiger


def _ouvrir_artefact_confine(chemin: Path, racines: list[Path]) -> int:
    """Ouvre un fichier par composants sans suivre les liens, sous une racine autorisée.

    Cette voie est volontairement réservée aux systèmes POSIX dotés de procfs.
    L'inode ouvert reste épinglé pendant la réponse ; elle ne fige toutefois pas
    les écritures concurrentes dans le même fichier.
    """
    exiger("procfs_fd")
    exiger("scan_ancre_descripteur")
    cible = Path(os.path.abspath(chemin.expanduser()))
    racines_absolues = [Path(os.path.abspath(r.expanduser())) for r in racines]
    candidates = sorted((r for r in racines_absolues if r in cible.parents), key=lambda r: len(r.parts), reverse=True)
    if not candidates:
        raise ValueError("Artefact hors racines")
    racine = candidates[0]
    relatif = cible.relative_to(racine)
    with _racine_scan_confinee(racine) as ancre:
        parent = os.dup(ancre)
        try:
            for composant in relatif.parts[:-1]:
                suivant = os.open(composant, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = suivant
            fd = os.open(relatif.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
    try:
        identite = os.fstat(fd)
        if not stat.S_ISREG(identite.st_mode) or identite.st_nlink != 1:
            raise ValueError("Artefact local non ordinaire ou lié")
        proc = os.stat(f"/proc/self/fd/{fd}")
        if (proc.st_dev, proc.st_ino) != (identite.st_dev, identite.st_ino):
            raise OSError("Descripteur local indisponible")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _copie_flux_verifiee(flux: Any, empreinte: str, limite: int) -> int:
    """Fige les octets servis dans un fichier anonyme borné, puis compare leur SHA.

    La comparaison porte sur la copie effectivement servie, pas sur une
    observation du fichier source suivie d'une seconde ouverture. Le coût en
    disque et en temps est proportionnel à la taille du fichier.
    """
    exiger("procfs_fd")
    if limite < 0 or not isinstance(empreinte, str) or re.fullmatch(r"[0-9a-f]{64}", empreinte) is None:
        raise ValueError("Empreinte ou limite de téléchargement invalide")
    with tempfile.TemporaryFile(prefix="seamtech-download-") as copie:
        digest = hashlib.sha256()
        total = 0
        while True:
            bloc = flux.read(min(1024 * 1024, limite - total + 1))
            if not bloc:
                break
            if not isinstance(bloc, bytes):
                raise ValueError("Contenu d'artefact invalide")
            total += len(bloc)
            if total > limite:
                raise ValueError("Artefact trop volumineux")
            digest.update(bloc)
            copie.write(bloc)
        if digest.hexdigest() != empreinte:
            raise ValueError("Artefact différent de la capture")
        copie.flush()
        os.fchmod(copie.fileno(), 0o400)
        resultat = os.dup(copie.fileno())
        try:
            os.lseek(resultat, 0, os.SEEK_SET)
        except BaseException:
            os.close(resultat)
            raise
        return resultat


class _ReponseFluxFerme(StreamingResponse):
    """Le corps distant est fermé même si le client part avant le premier bloc."""

    def __init__(self, corps: Any, contenu: Any, **kwargs: Any):
        self._corps = corps
        self._ferme = False
        try:
            super().__init__(contenu, **kwargs)
        except BaseException:
            self.fermer()
            raise

    def fermer(self) -> None:
        if self._ferme:
            return
        self._ferme = True
        try:
            self._corps.close()
        except Exception as exc:
            # Pas de chemin, nom d'objet ni détail d'exception dans les logs.
            logging.getLogger(__name__).warning("Fermeture du flux impossible (%s)", type(exc).__name__)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.fermer()


class _ReponseFichierEpingle(FileResponse):
    """FileResponse et plages HTTP sur un descripteur déjà ouvert, fermé même en cas d'erreur."""

    def __init__(self, fd: int, *, filename: str, media_type: str | None = None,
                 headers: dict[str, str] | None = None, ignorer_plage_head: bool = False):
        self._fd_epingle = fd
        self._ignorer_plage_head = ignorer_plage_head
        try:
            super().__init__(f"/proc/self/fd/{fd}", filename=filename, media_type=media_type, headers=headers)
        except BaseException:
            os.close(fd)
            raise

    async def __call__(self, scope, receive, send):
        # pathsend peut déléguer l'ouverture au serveur après notre retour et
        # après la fermeture du descripteur ; forcer l'envoi ASGI normal.
        scope = {**scope, "extensions": {k: v for k, v in scope.get("extensions", {}).items()
                                         if k != "http.response.pathsend"}}
        if self._ignorer_plage_head and scope.get("method", "").upper() == "HEAD":
            scope["headers"] = [(nom, valeur) for nom, valeur in scope.get("headers", []) if nom.lower() != b"range"]
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self._fd_epingle)


