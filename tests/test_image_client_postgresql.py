"""Le client PostgreSQL de l'image doit avoir la MAJEURE DU SERVEUR.

Pourquoi un test statique sur un Dockerfile : la preuve réelle du
2026-11-13 (job ``integration``) s'est arrêtée net sur
« pg_restore a échoué (rc=1) », et il a fallu un diagnostic ad hoc pour
lire enfin « unrecognized configuration parameter transaction_timeout ».

La cause : l'image applicative installait le ``postgresql-client`` de
Debian — majeure 17 — alors que la composition sert
``pgvector/pgvector:pg16``. Un client plus récent DUMP sans broncher, puis
RESTORE en écrivant des réglages que le serveur ignore. La sauvegarde
réussit, la restauration échoue, et rien ne le signale avant le jour du
sinistre. C'est le pire des défauts de sauvegarde : il ne se voit qu'au
moment où l'on en a besoin.

Un test qui touche au déploiement ne prouve pas qu'une restauration
réussit — seule la preuve réelle le fait — mais il empêche de revenir en
arrière : personne ne réécrira « postgresql-client » sans version en
croyant bien faire.
"""
from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
DOCKERFILE = RACINE / "Dockerfile"
COMPOSE = RACINE / "docker-compose.yml"


def _majeure_client() -> int:
    """La majeure épinglée dans l'image applicative."""
    texte = DOCKERFILE.read_text(encoding="utf-8")
    trouve = re.search(r"^ARG\s+PG_MAJEURE=(\d+)\s*$", texte, re.M)
    assert trouve is not None, (
        "le Dockerfile n'épingle aucune majeure de client PostgreSQL "
        "(« ARG PG_MAJEURE=<n> » attendu) : sans elle, le client Debian par "
        "défaut casse la restauration sans le dire"
    )
    return int(trouve.group(1))


def _majeure_serveur() -> int:
    """La majeure servie par la composition, lue sur le service postgres."""
    lignes = COMPOSE.read_text(encoding="utf-8").splitlines()
    for indice, ligne in enumerate(lignes):
        if re.match(r"^  postgres:\s*$", ligne):
            for suivante in lignes[indice + 1: indice + 15]:
                if re.match(r"^  [a-z]", suivante):
                    break
                image = re.search(r"^\s*image:\s*(\S+)\s*$", suivante)
                if image:
                    majeure = re.search(r"pg(\d+)", image.group(1))
                    assert majeure is not None, f"image sans majeure lisible : {image.group(1)}"
                    return int(majeure.group(1))
    raise AssertionError("service « postgres » ou son image introuvable dans docker-compose.yml")


def test_le_client_a_la_majeure_du_serveur() -> None:
    """Ni plus, ni moins : « au moins la majeure du serveur » ne suffit pas.

    C'est précisément l'ancienne hypothèse, écrite telle quelle dans le
    Dockerfile : « le client Debian (majeure >= serveur 16) sait dumper ».
    Il sait dumper. Il ne sait pas restaurer.
    """
    assert _majeure_client() == _majeure_serveur(), (
        f"client PostgreSQL {_majeure_client()} contre serveur {_majeure_serveur()} : "
        "un client plus récent que le serveur écrit des réglages que le serveur "
        "ignore, la sauvegarde réussit et la restauration échoue"
    )


def test_aucun_client_sans_majeure_dans_l_image() -> None:
    """« postgresql-client » sans version est le défaut qu'on vient de payer."""
    texte = DOCKERFILE.read_text(encoding="utf-8")
    nu = re.search(r"^\s*postgresql-client\s*\\?\s*$", texte, re.M)
    assert nu is None, (
        "le Dockerfile installe « postgresql-client » sans majeure : c'est le "
        "client Debian par défaut, plus récent que le serveur de la composition"
    )


def test_l_image_verifie_la_majeure_a_la_construction() -> None:
    """Sans l'assertion au build, un dépôt injoignable passerait inaperçu.

    Si le dépôt PGDG est indisponible, « apt-get install » échoue et la
    construction s'arrête — mais un jour où il répondrait avec un autre
    paquet, l'image partirait avec la mauvaise majeure sans rien dire.
    """
    texte = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r'pg_dump --version \| grep -q "\) \$\{PG_MAJEURE\}', texte), (
        "la construction de l'image ne vérifie pas la majeure de pg_dump"
    )
    assert re.search(r'pg_restore --version \| grep -q "\) \$\{PG_MAJEURE\}', texte), (
        "la construction de l'image ne vérifie pas la majeure de pg_restore"
    )
