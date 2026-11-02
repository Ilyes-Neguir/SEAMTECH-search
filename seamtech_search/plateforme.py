"""Ce que la PLATEFORME permet — dit une seule fois, et de façon testable.

Pourquoi ce module existe.

Le produit confine ses accès fichiers avec des primitives POSIX : ancrage d'un
répertoire par descripteur (``dir_fd``), ouverture sans suivi de lien
(``O_NOFOLLOW``), publication par ``/proc/self/fd``, verrous ``fcntl.flock``.
Ces primitives n'ont pas d'équivalent Windows : il n'existe pas de
« même chose en plus lent », il n'existe rien.

La conséquence était disséminée dans cinq endroits du code, chacun avec sa
condition et son message, et surtout **invisible jusqu'à l'incident** : sur
Windows, l'import d'un dossier échauait au milieu du parcours avec un
``OSError: Secure folder scanning is not supported on this platform`` dont rien
ne disait s'il fallait changer de poste, de configuration ou de produit (vu
pendant la qualification Windows, run 41365859490).

Ce module ne PORTE rien sur Windows — il rend la limite :

* **énumérable** : ``capacites()`` dit ce qui est disponible et ce qui ne
  l'est pas, sur la plateforme courante ;
* **opposable** : ``exiger()`` lève un ``OSError`` dont le message nomme la
  capacité manquante ET la conséquence, au lieu d'un refus anonyme ;
* **testable** : la branche Windows est prouvée sur un poste POSIX en forçant
  ``os.name`` (voir ``tests/test_plateforme.py``) — sans quoi elle ne serait
  vérifiée nulle part avant d'être rencontrée par l'utilisatrice.

Ce qui n'est PAS dit ici : ce module ne prétend pas que tout le reste marche
partout. Il ne couvre que les primitives de système de fichiers énumérées
ci-dessous.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

LOGGER = logging.getLogger(__name__)

#: Capacités reconnues, avec la conséquence de leur absence. Le texte est
#: repris tel quel dans le message d'erreur : c'est lui qu'un exploitant lit.
CAPACITES: dict[str, str] = {
    "scan_ancre_descripteur": (
        "scan d'un dossier ancré par descripteur (dir_fd + O_NOFOLLOW) : "
        "sans lui, l'import d'un dossier refuse au lieu de traverser des liens"
    ),
    "procfs_fd": (
        "publication et service des artefacts par /proc/self/fd : sans lui, "
        "un fichier servi ne peut pas être épinglé le temps de la réponse"
    ),
    "verrou_flock": (
        "verrou d'import exclusif (fcntl.flock) : sans lui, deux mutations "
        "concurrentes du même import ne peuvent pas être refusées"
    ),
    "processus_posix": (
        "fork() et SIGKILL : sans eux, les tests de crash de processus "
        "n'ont pas d'équivalent"
    ),
}


def _procfs_disponible() -> bool:
    return os.name == "posix" and os.path.isdir("/proc/self/fd")


def _descripteurs_confines() -> bool:
    return (
        os.name == "posix"
        and all(hasattr(os, nom) for nom in ("O_NOFOLLOW", "O_DIRECTORY"))
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )


def _flock_disponible() -> bool:
    if os.name != "posix":
        return False
    try:
        import fcntl  # noqa: F401  (la simple importabilité est la capacité)
    except ImportError:
        return False
    return True


def capacites() -> dict[str, bool]:
    """Rend, pour chaque capacité reconnue, si la plateforme la fournit."""
    return {
        "scan_ancre_descripteur": _descripteurs_confines(),
        "procfs_fd": _procfs_disponible(),
        "verrou_flock": _flock_disponible(),
        "processus_posix": os.name == "posix" and hasattr(__import__("signal"), "SIGKILL"),
    }


def exiger(capacite: str) -> None:
    """Lève ``OSError`` si la capacité manque, avec un message opposable.

    Le message nomme la capacité, la conséquence et la plateforme : un refus
    qui ne dit pas ce qu'il faut changer coûte une journée à l'atelier.
    """
    if capacite not in CAPACITES:
        raise ValueError(f"capacité inconnue : {capacite!r} (connues : {', '.join(CAPACITES)})")
    if capacites().get(capacite):
        return
    raise OSError(
        f"Capacité de plateforme absente : {capacite} — {CAPACITES[capacite]} "
        f"(plateforme : {sys.platform}, os.name={os.name}). "
        "Cette limite vient du système, pas de la configuration : elle ne se "
        "contourne pas par un réglage."
    )


def resume() -> str:
    """Une ligne par capacité, pour un journal ou un rapport d'installation."""
    etat = capacites()
    return " ; ".join(f"{nom}={'oui' if etat[nom] else 'non'}" for nom in CAPACITES)


def pid_vivant(pid: int) -> bool:
    """Vrai si le processus ``pid`` existe — et SANS JAMAIS le terminer.

    Pourquoi cette fonction existe, et pourquoi elle n'est pas un détail.

    ``os.kill(pid, 0)`` est l'idiome POSIX pour « ce PID vit-il encore ? » : le
    signal 0 n'est pas délivré, il sert uniquement à tester l'existence. Sur
    WINDOWS, ``os.kill`` ne connaît pas le signal 0 : il termine le processus
    avec le code de sortie reçu (``TerminateProcess(handle, sig)``). Autrement
    dit, un TEST DE VIE y devient une MISE À MORT — et comme les PID sont
    recyclés, le processus tué n'est même pas forcément celui dont on a lu
    l'identifiant dans un fichier de verrou.

    L'idiome était employé à deux endroits du produit (verrou de scan SQLite,
    verrou de la nuit OCR). Sur un poste Windows, reprendre un scan après un
    crash pouvait donc tuer un processus vivant.

    Garantie : cette fonction ne tue rien. Sur POSIX elle garde l'idiome
    (signal 0). Sur Windows elle interroge le noyau par ``ctypes``
    (``OpenProcess`` + ``GetExitCodeProcess``). Sur une plateforme inconnue,
    elle répond « vivant » : un verrou périmé reste un verrou, c'est un faux
    positif bénin ; supprimer le verrou d'un processus vivant ne l'est pas.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Le processus existe mais ne nous appartient pas : il vit.
            return True
        except OSError:
            return False
        return True
    if os.name == "nt":
        return _pid_vivant_windows(pid)
    LOGGER.warning(
        "plateforme %s : impossible de tester la vie du PID %s sans risque ; "
        "le verrou est conservé par prudence",
        sys.platform, pid,
    )
    return True


def _pid_vivant_windows(pid: int) -> bool:
    """Interroge le noyau Windows ; ne tue rien, même en cas d'échec."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # pragma: no cover - ctypes fait partie de l'interpréteur
        return True
    try:
        noyau = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        handle = noyau.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            erreur = ctypes.get_last_error()
            # Accès refusé : le processus existe (protégé ou d'un autre
            # utilisateur). Paramètre invalide : il n'existe pas.
            return erreur == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not noyau.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == STILL_ACTIVE
        finally:
            noyau.CloseHandle(handle)
    except Exception:
        # On ne sait pas : on ne supprime rien.
        return True


def plateforme_windows() -> bool:
    return os.name == "nt"


def _ignorer(_: Any) -> None:  # pragma: no cover - garde d'importation
    return None
