"""Preuve de restauration contre le FOURNISSEUR RÉEL — à exécuter sur site.

Pourquoi ce script existe, et pourquoi il n'a PAS été exécuté ici :

toute la preuve de restauration produite jusqu'à présent l'a été contre un
émulateur local (s3rver, ou le magasin en mémoire du dépôt). Un émulateur
qualifie le MÉCANISME — est-ce que le code lit, écrit, relit et compare — il
ne qualifie PAS le fournisseur. Ce dernier seul décide de choses que le code
ne maîtrise pas : le comportement d'un objet multipart, la stabilité d'un
ETag, la disponibilité d'un checksum plein objet, la sémantique du
versioning, et la conservation réelle des octets dans le temps.

Ce script fait donc le trajet COMPLET contre le fournisseur configuré :

    sauvegarder (corpus + base + données + pièces jointes)
      → vérifier (chaque pièce relue et confrontée à son empreinte)
      → restaurer vers des cibles DISTINCTES
      → comparer les octets
      → verdict

Il N'A PAS ÉTÉ EXÉCUTÉ DANS L'ENVIRONNEMENT DE DÉVELOPPEMENT, et il ne
prétend pas l'avoir été. Deux raisons, vérifiées et non supposées :

1. **le réseau de l'environnement de développement est restreint** à une
   liste blanche (github.com, codeload.github.com, api.github.com,
   registry.npmjs.org, pypi.org, files.pythonhosted.org). Mesuré :
   ``api.github.com`` répond HTTP 200, ``s3.amazonaws.com`` échoue dès la
   négociation TLS. Aucun fournisseur objet n'est joignable ;
2. **aucune identité de production n'y est présente**, et c'est tant mieux —
   un identifiant de stockage ne se colle pas dans un rapport.

Ce qu'il faut pour l'exécuter sur site, et rien de moins :

    config/config.json          s3_endpoint_url, s3_bucket,
                                s3_access_key, s3_secret_key
                                (ou les variables d'environnement équivalentes)
    SEAMTECH_DATABASE_URL       la base source
    SEAMTECH_RESTORE_DATABASE_URL   une base CIBLE DISTINCTE, qui n'existe PAS
                                encore (le script refuse une base existante)
    SEAMTECH_BACKUP_ACCESS_KEY  identité du bucket de sauvegarde —
    SEAMTECH_BACKUP_SECRET_KEY  DIFFÉRENTE de l'identité applicative
    SEAMTECH_BACKUP_BUCKET      (facultatif ; défaut : le bucket applicatif)

Usage :

    python scripts/preuve_restauration_reelle.py

Le script refuse de démarrer s'il manque quoi que ce soit, et NOMME ce qui
manque : une preuve qui échoue en silence ne vaut pas mieux qu'une preuve
qu'on n'a pas faite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def _manquants() -> list[str]:
    """Ce qui manque pour que la preuve soit possible. Nommé, jamais supposé."""
    manque: list[str] = []
    if not os.environ.get("SEAMTECH_DATABASE_URL"):
        manque.append("SEAMTECH_DATABASE_URL (base source)")
    if not os.environ.get("SEAMTECH_RESTORE_DATABASE_URL"):
        manque.append("SEAMTECH_RESTORE_DATABASE_URL (base CIBLE DISTINCTE, à créer par le script)")
    try:
        from seamtech_search.config import AppConfig

        config = AppConfig.load()
        if not config.s3_endpoint_url:
            manque.append("config: s3_endpoint_url (le fournisseur réel)")
        if not config.s3_access_key or not config.s3_secret_key:
            manque.append("config: s3_access_key / s3_secret_key (identité applicative)")
    except FileNotFoundError:
        # Nommer les CHAMPS, pas seulement le fichier : celui qui exécutera ce
        # script sur site n'a pas à deviner ce que la config doit contenir.
        manque.append(
            "config/config.json absent — fournir au moins : s3_endpoint_url, "
            "s3_bucket, s3_access_key, s3_secret_key"
        )
    if not (os.environ.get("SEAMTECH_BACKUP_ACCESS_KEY") and os.environ.get("SEAMTECH_BACKUP_SECRET_KEY")):
        manque.append(
            "SEAMTECH_BACKUP_ACCESS_KEY / SEAMTECH_BACKUP_SECRET_KEY "
            "(identité DÉDIÉE au bucket de sauvegarde — provisionnée par scripts/provisionner_stockage.sh)"
        )
    return manque


def _base_existe(url: str) -> bool:
    """La base cible doit être NEUVE : une restauration qui écrase une base
    vivante est une destruction, pas une reprise."""
    import psycopg2

    from seamtech_search.sauvegarde import _nom_base, _url_sans_base

    nom = _nom_base(url)
    try:
        connexion = psycopg2.connect(_url_sans_base(url))
    except Exception:  # noqa: BLE001 — le refus doit rester lisible
        return False
    try:
        connexion.set_session(autocommit=True)
        with connexion.cursor() as curseur:
            curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom,))
            return curseur.fetchone() is not None
    finally:
        connexion.close()


def _client_sauvegarde():
    from seamtech_search.sauvegarde import _client_s3_depuis_env

    return _client_s3_depuis_env()


def executer(dossier_preuve: Path | None = None) -> dict:
    """Le trajet complet. Rend un rapport ; ne lève pas pour un échec de preuve."""
    from seamtech_search import sauvegarde_corpus
    from seamtech_search.config import AppConfig
    from seamtech_search.etat_exploitation import dossier_sauvegardes
    from seamtech_search.sauvegarde_complete import NATURE, client_documents_ou_echec, restaurer, sauvegarder, verifier

    config = AppConfig.load()
    client = _client_sauvegarde()
    if client is None:
        return {"verdict": "IMPOSSIBLE", "raison": "aucun client de sauvegarde hors-site — voir _client_s3_depuis_env"}

    rapport: dict = {"etapes": {}}
    cible = os.environ["SEAMTECH_RESTORE_DATABASE_URL"]

    # 1. Le corpus, hors-site.
    dossier_corpus = sauvegarde_corpus.dossier_corpus()
    if dossier_corpus.is_dir():
        rapport["etapes"]["corpus"] = sauvegarde_corpus.sauvegarder(client, dossier=dossier_corpus)
    else:
        rapport["etapes"]["corpus"] = {"nb_fichiers": 0, "note": "corpus absent"}

    # 2. La sauvegarde COMPLÈTE.
    manifeste = sauvegarder(config, client_s3=client, client_documents=client_documents_ou_echec())
    rapport["etapes"]["sauvegarde_complete"] = {
        "nature": manifeste.get(NATURE) or manifeste.get("nature"),
        "documents_locaux": (manifeste.get("fichiers_locaux") or {}).get("nb_copies", 0),
        "documents_objets": (manifeste.get("objets_s3") or {}).get("nb_copies", 0),
        "donnees_copies": (manifeste.get("data") or {}).get("nb_copies", 0),
    }

    # 3. La preuve : chaque pièce relue, empreinte confrontée.
    chemin_manifeste = (
        dossier_sauvegardes(config) / (manifeste["dump"]["fichier"] + ".manifest.json")
    )
    rapport["etapes"]["verifier"] = verifier(chemin_manifeste)

    # 4. Restauration vers des cibles DISTINCTES.
    with tempfile.TemporaryDirectory(prefix="seamtech-preuve-") as tmp:
        rapport["etapes"]["restauration"] = restaurer(
            chemin_manifeste,
            base_cible=cible,
            fichiers_cible=Path(tmp) / "documents",
            data_cible=Path(tmp) / "data",
            client_documents=client_documents_ou_echec(),
        )

    # 5. Verdict.
    preuve = rapport["etapes"]["verifier"]
    restauration = rapport["etapes"]["restauration"]
    rapport["verdict"] = (
        "PROUVÉ"
        if preuve.get("ok") is True
        and (restauration.get("fichiers") or {}).get("nb", 0) > 0
        and (restauration.get("verification") or {}).get("ok") is True
        else "NON PROUVÉ"
    )
    return rapport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preuve_restauration_reelle",
        description="Trajet sauvegarder → vérifier → restaurer contre le fournisseur RÉEL.",
    )
    parser.add_argument(
        "--ignorer-prerequis",
        action="store_true",
        help="ne PAS contrôler les prérequis (déconseillé : l'échec sera obscur)",
    )
    args = parser.parse_args(argv)

    manque = [] if args.ignorer_prerequis else _manquants()
    if manque:
        print("PRÉREQUIS MANQUANTS — la preuve ne peut pas être faite ici :", file=sys.stderr)
        for item in manque:
            print(f"  · {item}", file=sys.stderr)
        print("\nVoir l'en-tête de ce script pour la liste exacte et pourquoi.", file=sys.stderr)
        return 2

    if _base_existe(os.environ["SEAMTECH_RESTORE_DATABASE_URL"]):
        print(
            "REFUS : la base cible existe déjà. Une restauration écrase ce qu'elle trouve ; "
            "fournir une base CIBLE DISTINCTE et inexistante.",
            file=sys.stderr,
        )
        return 2

    rapport = executer()
    print(json.dumps(rapport, ensure_ascii=False, indent=1, default=str))
    return 0 if rapport.get("verdict") == "PROUVÉ" else 1


if __name__ == "__main__":
    raise SystemExit(main())
