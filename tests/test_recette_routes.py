"""Recette de TOUTES les routes : le versant écrit, rejoué à chaque commit.

Le versant « application réelle » est `scripts/recette_routes.py --base-url …`,
joué en CI contre la composition (PostgreSQL, Redis, MinIO) ; ses observations
sont publiées en annotations, seul canal lisible depuis ici.

Ce test-ci court en SQLite, donc une partie des routes répondra 503
« PostgreSQL uniquement ». Il n'exige pas qu'elles fonctionnent : il exige
qu'aucune ne se comporte MAL. Deux propriétés, et elles sont les seules
qu'on puisse tenir sans la pile :

1. **aucune route ne plante** (500). Une erreur doit être NOMMÉE.
2. **aucune route ne laisse passer un appel sans jeton** (2xx). La barrière
   d'authentification est le premier des contrôles ; si elle cède quelque
   part, tout le reste est décoratif.

La liste des routes n'est pas écrite ici : elle est extraite du code à chaque
exécution. Une route ajoutée et oubliée se fait recetter malgré elle — c'est
tout l'intérêt.
"""

from __future__ import annotations

import pytest

from scripts.recette_routes import JETON, Rapport, _sonde_testclient, balayer


@pytest.fixture(scope="module")
def rapport() -> Rapport:
    return balayer(_sonde_testclient(), racine=None)  # type: ignore[arg-type]


def test_la_liste_des_routes_est_extraite_du_code(rapport: Rapport) -> None:
    """La recette porte sur TOUTES les routes, pas sur celles dont on se souvient."""
    assert rapport.routes >= 116, rapport.routes
    assert len(rapport.observations) >= rapport.routes * 4 - 8


def test_aucune_route_ne_plante(rapport: Rapport) -> None:
    """Un 500 est une exception non gérée : l'application ne sait plus quoi dire.

    Les 503 ne comptent pas : l'application signale elle-même qu'une
    dépendance est absente de CET environnement. Confondre les deux noierait
    les vrais défauts sous 162 fausses alertes.
    """
    assert not rapport.pannes, [
        f"{o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait[:160]}" for o in rapport.pannes[:10]
    ]


def test_aucune_route_n_est_ouverte_sans_jeton(rapport: Rapport) -> None:
    """Sans jeton, rien ne passe — sauf /live et /ready, qui sont des sondes.

    C'est le contrôle le plus important de cette recette : une barrière
    d'authentification qui cède sur UNE route annule tous les contrôles de
    rôle qu'on pourrait écrire ailleurs.
    """
    assert not rapport.refus_absents, [
        f"{o.methode} {o.chemin} répond {o.statut} sans jeton" for o in rapport.refus_absents[:10]
    ]


def test_aucun_5xx_sans_raison_lisible(rapport: Rapport) -> None:
    """Un 503 qui ne dit pas pourquoi est un 503 qu'il faudra déboguer un jour."""
    assert not rapport.a_examiner, [
        f"{o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait[:160]}" for o in rapport.a_examiner[:10]
    ]


def test_chaque_route_est_sondee_quatre_fois() -> None:
    """Nominal, sans jeton, paramètre invalide, ressource absente — pour toutes."""
    rapport = balayer(_sonde_testclient(), racine=None)  # type: ignore[arg-type]
    couples = {(o.methode, o.modele) for o in rapport.observations}
    sondes = {}
    for o in rapport.observations:
        sondes.setdefault((o.methode, o.modele), set()).add(o.sonde)
    insuffisants = [c for c in couples if len(sondes[c]) < 4]
    assert not insuffisants, insuffisants[:10]


def test_le_jeton_de_recette_n_est_pas_celui_de_la_production() -> None:
    """Un jeton de recette qui fonctionnerait en production serait une porte ouverte."""
    assert "production" not in JETON.lower()


def test_la_recette_tourne_avant_le_demontage_de_la_pile() -> None:
    """Une recette jouée sur une pile démontée rend un bilan qui ne veut rien dire.

    Au premier essai, l'étape avait été placée EN DERNIER du job, après
    « Run docker compose integration test » — étape qui finit par
    `docker compose down -v`. Résultat : 472 sondes, 472 erreurs de
    connexion, et des annotations annonçant « 0 plantage » sur une pile
    morte. Le bilan le plus rassurant est parfois le plus vide ; c'est
    pourquoi l'ordre des étapes est désormais un test, pas une habitude.
    """
    import pathlib

    import yaml

    chemin = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    if not chemin.exists():
        pytest.skip("Workflow CI absent de ce dépôt")
    try:
        workflow = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except ImportError:
        pytest.skip("PyYAML indisponible")

    noms = [etape.get("name") or "(sans nom)" for etape in workflow["jobs"]["integration"]["steps"]]
    recette = "Recette de TOUTES les routes, contre la pile réelle"
    integration = "Run docker compose integration test"
    assert recette in noms, f"étape « {recette} » absente du job integration"
    assert integration in noms, f"étape « {integration} » absente du job integration"
    assert noms.index(recette) < noms.index(integration), (
        "la recette doit tourner AVANT l'intégration : celle-ci démonte la pile "
        f"(docker compose down -v) en fin d'étape. Ordre actuel : {noms[-3:]}"
    )
