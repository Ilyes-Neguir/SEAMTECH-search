"""CLI des comptes nominatifs — Lot L.2.

    python3 -m seamtech_search.comptes.cli creer --identifiant imrane --nom "I. N." --role operateur
    python3 -m seamtech_search.comptes.cli lister [--json]
    python3 -m seamtech_search.comptes.cli desactiver --identifiant imrane
    python3 -m seamtech_search.comptes.cli reinitialiser-mot-de-passe --identifiant imrane
    python3 -m seamtech_search.comptes.cli sessions --identifiant imrane
    python3 -m seamtech_search.comptes.cli revoquer-session --id-session 12
    python3 -m seamtech_search.comptes.cli verifier --identifiant imrane   # teste un mot de passe

Deux règles de conception, non négociables :

* le mot de passe est TOUJOURS lu sur l'entrée standard, JAMAIS en argument de
  ligne de commande — un mot de passe en argument finit dans l'historique du
  shell et dans ``ps``, où tout le monde le lit ;
* aucune commande ne supprime quoi que ce soit. ``desactiver`` pose
  ``actif=false`` et révoque les sessions ouvertes ; la ligne du compte et
  l'historique de validation restent en place (traçabilité §10.1).

Base de données : ``--database-url``, sinon ``SEAMTECH_TEST_DATABASE_URL`` puis
``SEAMTECH_DATABASE_URL`` (même ordre que le reste de l'outillage).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from seamtech_search.comptes.comptes import (
    creer_utilisateur,
    desactiver_utilisateur,
    lister_utilisateurs,
    reinitialiser_mot_de_passe,
    revoquer_session,
    sessions_dun_utilisateur,
    verifier_identifiants,
)

MESSAGE_SANS_POSTGRES = (
    "Erreur : les comptes nominatifs vivent dans le schéma métier PostgreSQL.\n"
    "SQLite n'a ni `utilisateur` ni `session_ui` (décision de couche §17.1).\n"
    "Renseignez --database-url ou SEAMTECH_DATABASE_URL."
)


def _url_base(argument: str | None) -> str | None:
    return argument or os.environ.get("SEAMTECH_TEST_DATABASE_URL") or os.environ.get("SEAMTECH_DATABASE_URL")


def _index_pour(url: str | None) -> Any:  # noqa: ANN401
    from pathlib import Path

    from seamtech_search.indexer import SearchIndex

    if not url:
        raise SystemExit(MESSAGE_SANS_POSTGRES)
    # Le premier argument est le chemin SQLite (ignoré en mode PostgreSQL, mais
    # obligatoire dans la signature) : passer l'URL à cette place la ferait
    # prendre pour un chemin de fichier et `is_postgres` resterait faux.
    index = SearchIndex(Path("unused-comptes-cli.db"), url)
    if not index.is_postgres:
        raise SystemExit(MESSAGE_SANS_POSTGRES)
    return index


def _lire_mot_de_passe(invite: str, confirmation: bool = True) -> str:
    """Mot de passe lu sur STDIN, jamais en argument. Double saisie par défaut."""
    if not sys.stdin.isatty():
        # Permet `printf '%s' motdepasse | …` et les tests automatisés.
        valeur = sys.stdin.readline().rstrip("\n")
        if not valeur:
            raise SystemExit("Erreur : mot de passe vide sur l'entrée standard.")
        return valeur
    premier = getpass.getpass(invite)
    if not premier:
        raise SystemExit("Erreur : mot de passe vide.")
    if confirmation:
        second = getpass.getpass("Confirmation : ")
        if premier != second:
            raise SystemExit("Erreur : les deux saisies diffèrent.")
    return premier


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="comptes",
        description="Gestion des comptes nominatifs (PostgreSQL) — Lot L.2.",
    )
    analyseur.add_argument("--database-url", default=None, help="URL PostgreSQL (sinon variables d'environnement).")
    analyseur.add_argument("--json", action="store_true", help="Sortie JSON brute (scripts).")
    sous = analyseur.add_subparsers(dest="commande", required=True)

    p_creer = sous.add_parser("creer", help="Crée un compte nominatif.")
    p_creer.add_argument("--identifiant", required=True)
    p_creer.add_argument("--nom", required=True)
    p_creer.add_argument("--role", default="operateur", choices=("operateur", "administrateur"))
    p_creer.add_argument(
        "--mot-de-passe-definitif",
        action="store_true",
        help="N'impose pas le changement à la prochaine connexion (par défaut il est imposé).",
    )

    sous.add_parser("lister", help="Liste les comptes (jamais les empreintes).")

    p_desactiver = sous.add_parser("desactiver", help="Désactive un compte et révoque ses sessions.")
    p_desactiver.add_argument("--identifiant", required=True)

    p_reinit = sous.add_parser("reinitialiser-mot-de-passe", help="Remplace le mot de passe (changement forcé).")
    p_reinit.add_argument("--identifiant", required=True)

    p_sessions = sous.add_parser("sessions", help="Sessions d'un compte.")
    p_sessions.add_argument("--identifiant", required=True)

    p_revoquer = sous.add_parser("revoquer-session", help="Révoque une session par son identifiant.")
    p_revoquer.add_argument("--id-session", type=int, required=True)

    p_verifier = sous.add_parser("verifier", help="Vérifie un mot de passe (diagnostic, aucune écriture).")
    p_verifier.add_argument("--identifiant", required=True)
    return analyseur


def executer(arguments: argparse.Namespace) -> int:
    index = _index_pour(_url_base(arguments.database_url))
    commande = arguments.commande

    if commande == "creer":
        mot_de_passe = _lire_mot_de_passe(f"Mot de passe pour « {arguments.identifiant} » : ")
        resultat = creer_utilisateur(
            index,
            arguments.identifiant,
            arguments.nom,
            mot_de_passe,
            arguments.role,
            doit_changer_mot_de_passe=not arguments.mot_de_passe_definitif,
        )
        if arguments.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        else:
            print(f"Compte « {resultat['identifiant']} » créé (rôle {resultat['role']}, id {resultat['id_utilisateur']}).")
            if resultat["doit_changer_mot_de_passe"]:
                print("Changement de mot de passe imposé à la première connexion.")
        return 0

    if commande == "lister":
        comptes = lister_utilisateurs(index)
        if arguments.json:
            print(json.dumps(comptes, ensure_ascii=False, indent=2))
            return 0
        if not comptes:
            print("Aucun compte nominatif : l'application n'est accessible que par le compte de secours.")
            return 0
        print(f"{'IDENTIFIANT':<20} {'RÔLE':<15} {'ACTIF':<6} {'DERNIÈRE CONNEXION':<26} NOM")
        for compte in comptes:
            print(
                f"{compte['identifiant']:<20} {compte['role']:<15} "
                f"{'oui' if compte['actif'] else 'non':<6} {compte['derniere_connexion'] or '—':<26} {compte['nom'] or '—'}"
            )
        return 0

    if commande == "desactiver":
        resultat = desactiver_utilisateur(index, arguments.identifiant)
        if arguments.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        else:
            print(
                f"Compte « {resultat['identifiant']} » désactivé ; "
                f"{resultat['sessions_revoquees']} session(s) révoquée(s). "
                "Aucune donnée n'a été supprimée."
            )
        return 0

    if commande == "reinitialiser-mot-de-passe":
        mot_de_passe = _lire_mot_de_passe(f"Nouveau mot de passe pour « {arguments.identifiant} » : ")
        resultat = reinitialiser_mot_de_passe(index, arguments.identifiant, mot_de_passe)
        if arguments.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        else:
            print(f"Mot de passe de « {resultat['identifiant']} » remplacé ; changement imposé à la prochaine connexion.")
        return 0

    if commande == "sessions":
        sessions = sessions_dun_utilisateur(index, arguments.identifiant)
        if arguments.json:
            print(json.dumps(sessions, ensure_ascii=False, indent=2))
            return 0
        if not sessions:
            print(f"Aucune session pour « {arguments.identifiant} ».")
            return 0
        print(f"{'ID':<6} {'CRÉÉE LE':<26} {'EXPIRE LE':<26} {'RÉVOQUÉE':<9} AGENT")
        for session in sessions:
            print(
                f"{session['id_session']:<6} {session['cree_le'] or '—':<26} {session['expire_le'] or '—':<26} "
                f"{'oui' if session['revoquee'] else 'non':<9} {session['user_agent'] or '—'}"
            )
        return 0

    if commande == "revoquer-session":
        resultat = revoquer_session(index, arguments.id_session)
        if arguments.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        else:
            if resultat["deja_revoquee"]:
                print(f"Session {resultat['id_session']} déjà révoquée (aucune écriture ajoutée).")
            else:
                print(f"Session {resultat['id_session']} révoquée : le cookie correspondant ne vaut plus rien.")
        return 0

    if commande == "verifier":
        mot_de_passe = _lire_mot_de_passe(f"Mot de passe de « {arguments.identifiant} » : ", confirmation=False)
        try:
            utilisateur = verifier_identifiants(index, arguments.identifiant, mot_de_passe)
        except Exception as erreur:  # noqa: BLE001 - toute erreur = échec de vérification
            print(f"Échec : {erreur}")
            return 1
        print(f"Succès : « {utilisateur['identifiant']} » (rôle {utilisateur['role']}).")
        return 0

    raise SystemExit(f"Commande inconnue : {commande}")


def main(argv: list[str] | None = None) -> int:
    arguments = construire_analyseur().parse_args(argv)
    return executer(arguments)


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
