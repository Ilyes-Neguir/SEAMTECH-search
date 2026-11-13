"""Simule Windows CÔTÉ PROCESSUS — ce que ``plugin_simulation_sans_primitives`` ne couvre pas.

Le plugin existant (``ci/plugin_simulation_sans_primitives.py``) éteint les
capacités de ``plateforme.CAPACITES`` : confinement par descripteur, procfs,
flock. Il ne dit rien des PROCESSUS, parce que ces notions n'y figurent pas —
elles viennent de ``signal``, ``os`` et ``multiprocessing``.

C'est pourtant ce qui reste rouge au workflow Windows (étape « fichiers à
processus POSIX ») : ``fork``, ``SIGKILL``, ``killpg``, ``signal.pause``.
Windows n'a aucun de ces quatre, et ``multiprocessing`` n'y connaît que
``spawn``.

Usage (depuis la racine du dépôt) :

    PYTHONPATH=.:ci python -m pytest -p plugin_simulation_processus_windows <sélection>

Ce qu'on en tire : un test qui échoue SOUS ce plugin et passe SANS lui dépend
de la sémantique processus POSIX. C'est une raison ÉTABLIE — pas supposée —
de l'adapter à Windows ou de l'écarter nommément dans ``ci/ecarts-windows.txt``.

Limite dite : ce plugin simule l'ABSENCE des primitives, pas leur SÉMANTIQUE
différente. Un test qui passerait sous Windows pour une autre raison (chemins,
encodage, verrous de fichiers) ne sera pas repéré ici. Il complète l'autre
plugin, il ne le remplace pas.
"""

from __future__ import annotations

import multiprocessing

# --- Ce que Windows n'a pas, du tout -------------------------------------
# Ni ``getuid`` ni ``geteuid`` : les retirer casse ``platformdirs`` à
# l'import, donc la collection entière — on perdrait le signal dans le bruit.
# Leur absence est déjà une cause nommée par ailleurs (ci/ecarts-windows.txt).
ABSENTS_OS = ("fork", "forkpty", "killpg", "getpgid", "setpgid")
ABSENTS_SIGNAL = ("SIGKILL", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGALRM", "pause", "sigwait")

import os  # noqa: E402
import signal  # noqa: E402


def _retirer(module, noms: tuple[str, ...]) -> None:
    for nom in noms:
        if hasattr(module, nom):
            try:
                delattr(module, nom)
            except (AttributeError, TypeError):
                # Attribut en lecture seule ou module figé : on ne force pas,
                # le but est de simuler, pas de casser l'interpréteur.
                pass


_retirer(os, ABSENTS_OS)
_retirer(signal, ABSENTS_SIGNAL)

# --- ``spawn`` seulement -------------------------------------------------
# Sous Windows, ``multiprocessing.get_context("fork")`` lève
# ``ValueError: cannot find context for 'fork'``. On reproduit ce refus : un
# test qui appelle "fork" doit ÉCHOUER ici comme là-bas, pas réussir en
# silence grâce au banc POSIX.

_get_contexte = multiprocessing.get_context


def get_context_windows(methode: str | None = None):  # noqa: ANN202
    if methode is not None and methode != "spawn":
        raise ValueError(f"cannot find context for {methode!r}")
    return _get_contexte("spawn")


get_context_windows.Process = multiprocessing.Process  # type: ignore[attr-defined]
multiprocessing.get_context = get_context_windows  # type: ignore[assignment]
