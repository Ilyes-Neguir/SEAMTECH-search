"""Dépôt en lot d'une ARBORESCENCE de dossiers (tout un Dropbox, une année…).

Pourquoi ce module
------------------
Le dépôt dossier par dossier obligeait l'atelier à ouvrir 300 écrans. Ici, un
seul appel parcourt une arborescence locale (un Dropbox synchronisé, une année
« VOILES 2026 », un disque client) et dépose **chaque** dossier qui contient au
moins un PDF.

Ce que le module ne fait PAS
----------------------------
Il n'invente rien : il n'ignore aucun dossier, ne fusionne rien, et rapporte
pour chaque dossier soit ``traite``, soit ``deja_traite``, soit ``echec`` avec
la raison exacte renvoyée par l'application. Un dossier sans PDF est listé
comme tel (``ignore``) au lieu de disparaître silencieusement du rapport.

RG14_EXCEPTION
-------------
Ce module ouvre une connexion HTTP, mais UNIQUEMENT vers l'API SEAMTECH de la
machine (``127.0.0.1`` par défaut), et UNIQUEMENT depuis la commande opérateur
``deposer-arborescence``. Aucun appel sortant n'a lieu au runtime de
l'application.

Découpage d'un dossier
----------------------
Un « dossier » est le répertoire le plus PROFOND contenant des PDF : ainsi
``/VOILES 2026/BAVARIA 38 ST735/`` est un dossier même s'il contient un
sous-répertoire ``TISSU`` (qui, sans PDF, n'est pas un dossier déposable). Un
répertoire qui contient à la fois des PDF et des sous-répertoires avec PDF
compte pour lui-même **et** pour ses sous-répertoires : rien n'est perdu.
"""

from __future__ import annotations

import csv
import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

EXTENSIONS_DOCUMENT = (".pdf",)


@dataclass(frozen=True)
class DossierArborescence:
    """Un dossier candidat au dépôt, avec ses fichiers réellement présents."""

    chemin: Path
    fichiers: tuple[Path, ...]

    @property
    def nb_pdf(self) -> int:
        return sum(1 for f in self.fichiers if f.suffix.lower() in EXTENSIONS_DOCUMENT)


@dataclass
class ResultatArborescence:
    """Ligne de rapport pour un dossier : ce qui s'est passé, sans arrondi."""

    dossier: str
    nb_fichiers: int
    statut: str
    fiche: str | None = None
    pieces: int | None = None
    raison: str = ""
    fichiers: tuple[str, ...] = field(default_factory=tuple)


def _fichiers_du_dossier(dossier: Path) -> tuple[Path, ...]:
    """Fichiers POSÉS DANS ce répertoire (pas dans ses sous-répertoires)."""
    return tuple(sorted(p for p in dossier.iterdir() if p.is_file() and not p.name.startswith(".")))


def lister_dossiers(racine: Path | str) -> list[DossierArborescence]:
    """Tous les répertoires déposables sous ``racine``, triés, sans exception.

    Un répertoire est déposable s'il contient au moins un PDF. La racine
    elle-même est examinée : un Dropbox « à plat » fonctionne donc aussi.
    """
    base = Path(racine)
    if not base.is_dir():
        raise NotADirectoryError(f"racine introuvable ou non répertoire : {base}")
    trouves: list[DossierArborescence] = []
    for repertoire in sorted(p for p in base.rglob("*") if p.is_dir()):
        fichiers = _fichiers_du_dossier(repertoire)
        if any(f.suffix.lower() in EXTENSIONS_DOCUMENT for f in fichiers):
            trouves.append(DossierArborescence(chemin=repertoire, fichiers=fichiers))
    if not trouves:
        fichiers_racine = _fichiers_du_dossier(base)
        if any(f.suffix.lower() in EXTENSIONS_DOCUMENT for f in fichiers_racine):
            trouves.append(DossierArborescence(chemin=base, fichiers=fichiers_racine))
    return trouves


def _deposer_un(
    url: str, jeton: str, dossier: Path, delai: float
) -> tuple[str, str | None, int | None, str]:
    """POST /imports/dossier. Rend (statut, fiche, pièces, raison)."""
    corps = json.dumps({"dossier": str(dossier)}).encode("utf-8")
    requete = urllib.request.Request(
        url.rstrip("/") + "/imports/dossier",
        data=corps,
        headers={"X-SEAMTECH-TOKEN": jeton, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(requete, timeout=delai) as reponse:
            charge = json.load(reponse)
    except urllib.error.HTTPError as erreur:  # pragma: no cover - dépend du serveur
        detail = erreur.read().decode("utf-8", "replace")[:300]
        return "echec", None, None, f"HTTP {erreur.code} : {detail}"
    except (urllib.error.URLError, TimeoutError) as erreur:  # pragma: no cover
        return "echec", None, None, f"application injoignable : {erreur}"
    return (
        str(charge.get("statut") or "echec"),
        charge.get("fiche"),
        charge.get("pieces"),
        str(charge.get("raison") or ""),
    )


def deposer_arborescence(
    racine: Path | str,
    url_api: str,
    jeton: str,
    *,
    delai: float = 180.0,
    rapport_csv: Path | None = None,
    progression: Iterable[DossierArborescence] | None = None,
) -> list[ResultatArborescence]:
    """Dépose TOUS les dossiers de ``racine`` et rend une ligne par dossier."""
    dossiers = list(progression) if progression is not None else lister_dossiers(racine)
    resultats: list[ResultatArborescence] = []
    for candidat in dossiers:
        statut, fiche, pieces, raison = _deposer_un(url_api, jeton, candidat.chemin, delai)
        resultats.append(
            ResultatArborescence(
                dossier=str(candidat.chemin),
                nb_fichiers=len(candidat.fichiers),
                statut=statut,
                fiche=fiche,
                pieces=pieces,
                raison=raison,
                fichiers=tuple(f.name for f in candidat.fichiers),
            )
        )
    if rapport_csv is not None:
        ecrire_rapport_csv(resultats, rapport_csv)
    return resultats


def ecrire_rapport_csv(resultats: Iterable[ResultatArborescence], chemin: Path | str) -> Path:
    """Rapport lisible dans Excel (l'atelier vit sous Windows)."""
    destination = Path(chemin)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as flux:
        graveur = csv.writer(flux, delimiter=";")
        graveur.writerow(["dossier", "nb_fichiers", "statut", "fiche", "pieces", "raison", "fichiers"])
        for r in resultats:
            graveur.writerow(
                [r.dossier, r.nb_fichiers, r.statut, r.fiche or "", r.pieces if r.pieces is not None else "", r.raison, " | ".join(r.fichiers)]
            )
    return destination


def resume(resultats: Iterable[ResultatArborescence]) -> dict[str, int]:
    """Compte par statut, y compris les statuts inattendus (aucun n'est caché)."""
    comptes: dict[str, int] = {}
    for r in resultats:
        comptes[r.statut] = comptes.get(r.statut, 0) + 1
    return comptes


def iterer_dossiers_sans_pdf(racine: Path | str) -> Iterator[Path]:
    """Répertoires FEUILLES sans aucun PDF : à montrer, pas à ignorer."""
    base = Path(racine)
    for repertoire in sorted(p for p in base.rglob("*") if p.is_dir()):
        if any(p.is_dir() for p in repertoire.iterdir()):
            continue
        if any(f.suffix.lower() in EXTENSIONS_DOCUMENT for f in _fichiers_du_dossier(repertoire)):
            continue
        yield repertoire
