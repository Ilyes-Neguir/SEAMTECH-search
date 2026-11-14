"""Lot 12 — le provisionnement des extensions : épreuves.

Le script ne peut pas être éprouvé « avec contrib » sur ce banc : le
PostgreSQL disponible est fourni par ``pgserver``, distribué sans les modules
contrib, et ils ne sont pas installables ici. La contre-épreuve « extensions
présentes » est donc apportée par la CI, dont l'image ``pgvector/pgvector:pg16``
les inclut — c'est là que ``tests/test_indexer_accent_parity.py`` passe au vert.

Ces épreuves couvrent ce qui reste démontrable sans contrib : la LOGIQUE de
décision. Elles simulent les trois situations que l'opérateur rencontrera.

Elles défendent surtout une idée : **un prérequis manquant doit faire échouer
le déploiement, pas le laisser démarrer amoindri.** C'est la tentation
inverse qui est dangereuse — un script complaisant qui rend 0 en disant
« à peu près ».
"""

from __future__ import annotations

import scripts.provisionner_extensions as pe


class _CurseurFeint:
    """Curseur qui répond selon un état d'extensions fourni à l'avance."""

    def __init__(self, installees: set[str], disponibles: set[str]) -> None:
        self._installees = installees
        self._disponibles = disponibles
        self.ordres: list[str] = []

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.ordres.append(sql)
        if "pg_available_extensions" in sql:
            self._resultat = [(1,)] if params and params[0] in self._disponibles else []
        elif "pg_extension" in sql:
            self._resultat = [(1,)] if params and params[0] in self._installees else []
        elif sql.startswith("CREATE EXTENSION"):
            nom = sql.rsplit(" ", 1)[-1]
            if nom not in self._disponibles:
                raise RuntimeError(f"extension « {nom }» indisponible")
            self._installees.add(nom)
            self._resultat = []
        elif "current_user" in sql:
            self._resultat = [("faux_operateur", "PostgreSQL 16.2 on x86_64")]
        else:
            self._resultat = []

    def fetchone(self):
        return self._resultat[0] if self._resultat else None

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class _ConnexionFeinte:
    def __init__(self, installees: set[str], disponibles: set[str]) -> None:
        self.curseur = _CurseurFeint(set(installees), set(disponibles))

    def cursor(self):
        return self.curseur

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def close(self) -> None:
        pass


def _installer(monkeypatch, installees: set[str], disponibles: set[str]) -> _CurseurFeint:
    connexion = _ConnexionFeinte(installees, disponibles)
    monkeypatch.setattr(pe, "_connexion", lambda url: connexion)
    monkeypatch.setenv("SEAMTECH_DATABASE_URL", "postgresql://faux@127.0.0.1:5432/base")
    return connexion.curseur


def test_unaccent_et_pg_trgm_sont_bloquantes_et_vector_optionnelle() -> None:
    """Baisser une exigence ne doit pas pouvoir se faire en douce."""
    par_nom = {nom: bloquante for nom, _consequence, bloquante in pe.EXTENSIONS}
    assert par_nom["unaccent"] is True, (
        "unaccent n'est PAS optionnelle : sans elle la configuration de recherche "
        "se replie sur « simple » et « regate » ne trouve plus « Régate »."
    )
    assert par_nom["pg_trgm"] is True
    assert par_nom["vector"] is False, "vector est hors de cette fonction : signalée, jamais bloquante"


def test_toutes_presentes_rend_zero(monkeypatch) -> None:
    """Rien à faire : le déploiement passe."""
    _installer(monkeypatch, {"unaccent", "pg_trgm", "vector"}, {"unaccent", "pg_trgm", "vector"})
    assert pe.main([]) == 0


def test_une_bloquante_absente_du_serveur_rend_un(monkeypatch) -> None:
    """Pas installable : le déploiement doit s'arrêter, pas démarrer amoindri."""
    _installer(monkeypatch, set(), set())
    assert pe.main([]) == 1


def test_disponible_mais_non_installee_rend_un_sans_appliquer(monkeypatch) -> None:
    """Le constat seul ne crée rien : il signale."""
    _installer(monkeypatch, set(), {"unaccent", "pg_trgm"})
    assert pe.main([]) == 1


def test_appliquer_cree_les_extensions_disponibles(monkeypatch) -> None:
    """Avec --appliquer et les droits, tout est créé et le code redevient 0."""
    curseur = _installer(monkeypatch, set(), {"unaccent", "pg_trgm"})
    assert pe.main(["--appliquer"]) == 0
    creees = [o for o in curseur.ordres if o.startswith("CREATE EXTENSION")]
    assert len(creees) == 2, f"deux extensions bloquantes à créer, mesuré {creees}"


def test_la_creation_est_idempotente_par_construction(monkeypatch) -> None:
    """Rejouer le script ne doit jamais rien casser : IF NOT EXISTS."""
    curseur = _installer(monkeypatch, set(), {"unaccent", "pg_trgm"})
    pe.main(["--appliquer"])
    pe.main(["--appliquer"])
    for ordre in curseur.ordres:
        if ordre.startswith("CREATE EXTENSION"):
            assert "IF NOT EXISTS" in ordre, (
                f"« {ordre} » n'est pas idempotente : un second passage échouerait, "
                "et un script de déploiement doit pouvoir être rejoué."
            )


def test_une_extension_optionnelle_manquante_ne_bloque_pas(monkeypatch) -> None:
    """Seules les bloquantes arrêtent le déploiement."""
    _installer(monkeypatch, {"unaccent", "pg_trgm"}, {"unaccent", "pg_trgm"})
    assert pe.main([]) == 0


def test_sans_url_le_script_refuse_de_deviner(monkeypatch) -> None:
    monkeypatch.delenv("SEAMTECH_DATABASE_URL", raising=False)
    assert pe.main([]) == 2
