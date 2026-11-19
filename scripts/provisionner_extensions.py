#!/usr/bin/env python3
"""Provisionnement des extensions PostgreSQL — Lot 12.

Pourquoi ce script existe, alors que l'application crée déjà ces extensions
elle-même : parce qu'elle les crée **si elle en a le droit**.
``CREATE EXTENSION`` exige des privilèges élevés. Sur une base de production
où le rôle applicatif n'est pas superutilisateur, l'opération est refusée, et
l'application ne casse pas : elle **dégrade**, avec la conséquence écrite dans
le journal et rendue visible par ``GET /health``.

C'est précisément le danger. Une dégradation silencieuse au démarrage, un
service qui répond 200, et l'opérateur découvre six mois plus tard que
« regate » ne trouvait pas « Régate ». Ce script déplace la question AVANT la
mise en service, au moment où l'on peut encore agir :

    python scripts/provisionner_extensions.py            # constater
    python scripts/provisionner_extensions.py --appliquer # créer, si les droits suivent

Il est **idempotent** : ``CREATE EXTENSION IF NOT EXISTS``, rejouable sans
effet de bord.

**Il échoue bruyamment.** Code 1 si une extension recommandée est absente et
n'a pas pu être créée : un déploiement doit s'arrêter sur un prérequis
manquant, pas démarrer en silence avec une recherche amoindrie.

.. code-block:: shell

    export SEAMTECH_DATABASE_URL=postgresql://superutilisateur@hote:5432/base
    python scripts/provisionner_extensions.py --appliquer

Codes de retour : 0 = tout est disponible, 1 = au moins une extension
recommandée manque (prérequis non rempli), 2 = erreur d'entrée.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Extensions et ce qu'elles conditionnent. Rien n'est inventé ici : la
#: conséquence écrite est celle que le code produit réellement (elle est
#: reprise des avertissements journalisés par l'indexeur).
EXTENSIONS: tuple[tuple[str, str, bool], ...] = (
    (
        "unaccent",
        "recherche insensible aux accents — sans elle, « regate » ne trouve pas "
        "« Régate » (configuration de recherche repliée sur « simple »)",
        True,  # recommandée : bloquante pour une mise en service complète
    ),
    (
        "pg_trgm",
        "tolérance aux fautes de frappe et dédoublonnage par trigrammes — sans "
        "elle, les index correspondants sont omis et la comparaison est plus lente",
        True,
    ),
    (
        "vector",
        "recherche vectorielle (couche métier) — optionnelle hors de cette fonction",
        False,  # optionnelle : signalée, jamais bloquante
    ),
)


def _connexion(url: str):
    import psycopg2

    return psycopg2.connect(url)


def _disponible(curseur, nom: str) -> bool:
    """L'extension est-elle INSTALLABLE, pas seulement listée.

    On interroge ``pg_available_extensions`` : une extension peut être
    installée dans la base, ou disponible, ou totalement absente du serveur
    (cas d'un paquet ``postgresql-contrib`` non installé). Seule la première
    compte, et seule la troisième est irrécupérable par ce script.
    """
    curseur.execute("SELECT 1 FROM pg_available_extensions WHERE name = %s", (nom,))
    return curseur.fetchone() is not None


def _installee(curseur, nom: str) -> bool:
    curseur.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (nom,))
    return curseur.fetchone() is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provisionne les extensions PostgreSQL.")
    parser.add_argument(
        "--appliquer",
        action="store_true",
        help="Crée les extensions manquantes (sans ce drapeau : constat seul).",
    )
    options = parser.parse_args(argv)

    url = os.environ.get("SEAMTECH_DATABASE_URL", "").strip()
    if not url:
        print("SEAMTECH_DATABASE_URL est requis (base de production, rôle privilégié).")
        return 2

    try:
        connexion = _connexion(url)
    except Exception as erreur:  # noqa: BLE001 - le message est la sortie utile
        print(f"Connexion impossible : {type(erreur).__name__}: {erreur}")
        return 2

    bloquantes_manquantes: list[str] = []
    with connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT current_user, version()")
            utilisateur, version = curseur.fetchone()
            print(f"Connecté en tant que {utilisateur}")
            print(f"{version.split(',')[0]}\n")

            for nom, consequence, bloquante in EXTENSIONS:
                installee = _installee(curseur, nom)
                disponible = _disponible(curseur, nom)

                if installee:
                    print(f"  [OK]      {nom:<10} déjà installée")
                    continue

                if not disponible:
                    marqueur = "MANQUE" if bloquante else "option."
                    print(f"  [{marqueur}]  {nom:<10} ABSENTE du serveur — {consequence}")
                    if bloquante:
                        bloquantes_manquantes.append(nom)
                    continue

                if not options.appliquer:
                    marqueur = "À CRÉER" if bloquante else "option."
                    print(f"  [{marqueur}] {nom:<10} disponible mais non installée — {consequence}")
                    if bloquante:
                        bloquantes_manquantes.append(nom)
                    continue

                try:
                    curseur.execute(f"CREATE EXTENSION IF NOT EXISTS {nom}")
                    print(f"  [CRÉÉE]   {nom:<10} installée")
                except Exception as erreur:  # noqa: BLE001 - le refus est l'information
                    print(f"  [REFUSÉE] {nom:<10} {type(erreur).__name__}: {erreur}")
                    print(f"            conséquence : {consequence}")
                    if bloquante:
                        bloquantes_manquantes.append(nom)

    connexion.close()

    print()
    if bloquantes_manquantes:
        print(
            f"PRÉREQUIS NON REMPLIS : {', '.join(bloquantes_manquantes)}.\n"
            f"La recherche fonctionnera, mais amoindrie — et c'est exactement le\n"
            f"genre de dégradation qu'on découvre trop tard. Installer les modules\n"
            f"côté serveur (paquet postgresql-contrib) puis relancer ce script."
        )
        return 1

    if not options.appliquer:
        print("Constat terminé : aucune extension bloquante ne manque "
              "(ou elles sont toutes déjà installées).")
    else:
        print("Toutes les extensions requises sont en place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
