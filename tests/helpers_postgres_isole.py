"""Construction d'URL de bases de test sans redirection vers la base source."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from psycopg2 import Error
from psycopg2.extensions import parse_dsn


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
