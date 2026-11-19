#!/usr/bin/env python3
"""Ligne de commande de la sauvegarde hors-git du corpus — Lot 21, priorité 0.

La logique vit dans ``seamtech_search.sauvegarde_corpus`` (le planificateur,
dans le conteneur, l'importe) ; ce script n'est que le geste à la main sur le
poste où le corpus est produit ::

    python scripts/sauvegarder_corpus.py sauvegarder
    python scripts/sauvegarder_corpus.py restaurer --destination /tmp/essai
    python scripts/sauvegarder_corpus.py verifier          # pour la supervision

``verifier`` rend 1 quand aucune sauvegarde hors-site récente n'est prouvée :
même contrat que ``verifier_sauvegarde_quotidienne.py``, afin qu'une
supervision unique consomme les deux.

Codes de sortie de ``sauvegarder`` : 0 envoyé hors-site, 1 RIEN n'a été envoyé
(pas de stockage distant configuré — ce n'est pas une sauvegarde), 2 échec.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE_DEPOT))

from seamtech_search.sauvegarde_corpus import (  # noqa: E402  (après sys.path)
    SauvegardeCorpusError,
    dossier_corpus,
    restaurer,
    sauvegarder,
    verifier,
)


def _client():
    """Client hors-site de l'environnement, ou None (aucun repli silencieux)."""
    from seamtech_search.sauvegarde import _client_s3_depuis_env

    return _client_s3_depuis_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)

    p_sauver = sous.add_parser("sauvegarder", help="verser le corpus sur le stockage distant")
    p_sauver.add_argument("--corpus", type=Path, default=None, help=argparse.SUPPRESS)

    p_restaurer = sous.add_parser("restaurer", help="restaurer et vérifier les empreintes")
    p_restaurer.add_argument("--destination", type=Path, required=True)
    p_restaurer.add_argument("--horodatage", default=None)

    p_verifier = sous.add_parser("verifier", help="la protection est-elle prouvée ?")
    p_verifier.add_argument("--fraicheur-heures", type=float, default=25.0)

    options = parser.parse_args(argv)

    if options.commande == "sauvegarder":
        try:
            manifeste = sauvegarder(_client(), dossier=options.corpus)
        except SauvegardeCorpusError as exc:
            print(f"Échec : {exc}", file=sys.stderr)
            return 2
        lieu = "hors-site" if manifeste["hors_site"] else "sans hors-site : RIEN n'a été envoyé"
        print(f"corpus sauvegardé ({manifeste['nb_fichiers']} fichier(s)) — {lieu}")
        for entree in manifeste["fichiers"]:
            print(f"  {entree['nom']:<32} {entree['taille']:>8} o  {entree['sha256'][:16]}…")
        if not manifeste["hors_site"]:
            print(
                "\nÉCHEC DE SAUVEGARDE : aucun stockage distant configuré. Une copie sur le même "
                "disque n'est pas une sauvegarde. Configurez le hors-site (SEAMTECH_BACKUP_*).",
                file=sys.stderr,
            )
            return 1
        return 0

    if options.commande == "restaurer":
        try:
            rapport = restaurer(_client(), options.destination, horodatage=options.horodatage)
        except SauvegardeCorpusError as exc:
            print(f"Échec : {exc}", file=sys.stderr)
            return 2
        print(f"restauré depuis {rapport['horodatage']} → {rapport['destination']}")
        for entree in rapport["fichiers"]:
            print(f"  {entree['nom']:<32} {entree['sha256'][:16]}…")
        print(f"intégrité : {rapport['integrite']}")
        return 0

    etat = verifier(_client(), fraicheur_heures=options.fraicheur_heures)
    if not etat["protege"]:
        etat["corpus"] = str(getattr(options, "corpus", None) or dossier_corpus())
    print(json.dumps(etat, ensure_ascii=False, indent=2))
    return 0 if etat["protege"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
