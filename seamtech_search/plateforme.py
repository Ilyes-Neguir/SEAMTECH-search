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

import os
import sys
from typing import Any

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


def plateforme_windows() -> bool:
    return os.name == "nt"


def _ignorer(_: Any) -> None:  # pragma: no cover - garde d'importation
    return None
