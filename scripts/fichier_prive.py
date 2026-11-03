"""Création exclusive d'un fichier confidentiel pour les bancs de test."""
from __future__ import annotations

import os
from pathlib import Path


def ecrire_fichier_prive(chemin: Path, contenu: str) -> None:
    """Crée en 0600 sans écraser un fichier ou suivre un lien préexistant.

    En cas d'échec d'écriture, le fichier partiel reste privé et doit être
    inspecté/supprimé par le propriétaire ; il n'est jamais remplacé en silence.
    """
    drapeaux = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(chemin, drapeaux, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fichier:
        fichier.write(contenu)
