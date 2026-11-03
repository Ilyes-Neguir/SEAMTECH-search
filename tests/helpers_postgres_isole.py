"""Construction d'URL de bases de test sans redirection vers la base source."""
from __future__ import annotations

import re
from contextlib import closing
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import Error
from psycopg2.extensions import parse_dsn

#: Configuration de recherche qui plie les accents (créée par la migration 005
#: quand l'extension ``unaccent`` est installable).
CONFIG_ACCENTS = "seamtech_unaccent"


def parite_accents_active(url: str) -> bool:
    """Cette base plie-t-elle les accents en recherche plein-texte ?

    La parité des accents exige l'extension ``unaccent``, qui fait partie de
    ``postgresql-contrib``. Un banc sans contrib (comme le paquet ``pgserver``
    utilisé ici faute de mieux) ne l'a PAS : l'application dégrade alors vers
    la configuration ``simple``, documentée et assumée, et une recherche
    « GENOIS » ne retrouve plus « Génois ».

    Répondre ``False`` ici ne doit PAS devenir une façon de cacher un échec :
    la contrepartie est ``tests/test_indexer_accent_parity.py``, qui échoue
    bruyamment si une CI complète empruntait le chemin dégradé. Sur un banc
    réduit, l'écart est nommé par le test qui s'en sert.
    """
    try:
        with closing(psycopg2.connect(url)) as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_ts_config WHERE cfgname = %s", (CONFIG_ACCENTS,))
                return cursor.fetchone() is not None
    except psycopg2.Error:
        return False


def url_base_jetable(url_admin: str, nom: str) -> str:
    """Préserve les paramètres libpq, et refuse tout détournement de dbname."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", nom):
        raise ValueError("Nom de base jetable invalide")
    try:
        parties = urlsplit(url_admin)
        if parties.scheme not in {"postgres", "postgresql"} or parties.fragment:
            raise ValueError("URL PostgreSQL requise")
        if not parties.path.startswith("/") or len(parties.path) < 2:
            raise ValueError("Base d'administration requise")
        url = f"{parties.scheme}://{parties.netloc}/{nom}"
        if parties.query:
            url += f"?{parties.query}"
        admin = parse_dsn(url_admin)
        cible = parse_dsn(url)
    except (ValueError, Error):
        raise ValueError("URL PostgreSQL de test invalide") from None
    if admin.pop("dbname", None) == nom or cible.pop("dbname", None) != nom or admin != cible:
        raise ValueError("La base cible n'est pas isolée de la base d'administration")
    return url
