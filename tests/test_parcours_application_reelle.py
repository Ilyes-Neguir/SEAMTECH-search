"""Navigation RÉELLE dans l'application, sur une base PostgreSQL réelle.

Pourquoi ce fichier : les tests de parcours existants exercent des fonctions,
celui-ci exerce l'application. On parle à l'ASGI comme le ferait un
navigateur, et derrière il y a un vrai PostgreSQL 16 avec le vrai corpus de
l'Étape 0 déposé dedans. Une doublure ne peut pas révéler ce qui casse entre
deux briques assemblées, et deux régressions de ce projet n'ont été visibles
QUE sur PostgreSQL réel.

On n'utilise pas de navigateur : ce qui compte ici, ce sont les requêtes HTTP
et ce qu'elles rendent depuis la base. Le rendu de la page regarde le front.

La base est JETABLE : créée pour la session, supprimée ensuite.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

#: URL de la base réellement servie, renseignée par la fixture. Elle
#: permet à une épreuve d'interroger le serveur PAR ELLE-MÊME et de
#: confronter sa réponse à celle de l'application.
_URL_SERVIE: list[str] = []


def _url_base_servie() -> str:
    return _URL_SERVIE[-1]



def _creer_base_jetable(nom: str) -> str:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()
    from urllib.parse import urlsplit

    parties = urlsplit(os.environ["SEAMTECH_TEST_DATABASE_URL"])
    return f"{parties.scheme}://{parties.netloc}/{nom}?{parties.query}"


def _supprimer_base_jetable(nom: str) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
    admin.close()


@pytest.fixture(scope="module")
def application():
    """L'application montée, servie par-dessus une base PostgreSQL réelle."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom = f"parcours_{uuid.uuid4().hex[:12]}"
    url = _creer_base_jetable(nom)

    from seamtech_search.indexer import SearchIndex

    base_sqlite = pathlib.Path(tempfile.gettempdir()) / f"{nom}.db"
    ix = SearchIndex(base_sqlite, url)
    ix.initialize()
    ix.run_migrations()

    import etape0_corpus as e0

    depot = e0.deposer(e0.charger_accumulateur(), ix)
    ix.close()

    from seamtech_search.config import AppConfig

    # AppConfig est CONSTRUIT, pas chargé. `AppConfig.load()` lit
    # config/config.json, fichier que .gitignore exclut du dépôt : en
    # intégration continue il n'existe pas, et SEAMTECH_ROOT_PATHS n'y est pas
    # défini — l'appel levait donc FileNotFoundError et faisait échouer toute
    # la session. C'est exactement ce qui est arrivé à l'étape « coverage ».
    config = AppConfig(
        root_paths=[pathlib.Path(tempfile.gettempdir())],
        database_path=base_sqlite,
        database_url=url,
        auth_token="",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-synthetique",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )

    from seamtech_search.api import create_app

    app = create_app(config)
    # Le client de test de FastAPI, et non un httpx.ASGITransport monté à la
    # main : httpx 0.28 a retiré l'entrée synchrone que ce montage utilisait.
    from fastapi.testclient import TestClient

    client = TestClient(app)

    _URL_SERVIE.append(url)
    yield client, depot

    client.close()
    _URL_SERVIE.clear()
    _supprimer_base_jetable(nom)


# ---------------------------------------------------------------------------
# Ce que l'application dit d'elle-même
# ---------------------------------------------------------------------------


def test_l_application_repond(application):
    """Point de départ : si l'application ne répond pas, rien d'autre ne compte."""
    client, _depot = application
    reponse = client.get("/live")
    assert reponse.status_code == 200, reponse.text
    assert reponse.json().get("status") == "alive"


def test_l_etat_de_sante_prouve_que_c_est_bien_postgresql(application):
    """/health interroge la base : c'est LA preuve que la connexion est réelle.

    On n'accepte pas un simple « ok » : on exige que l'application nomme le
    moteur et donne sa version. Un « ok » pourrait être rendu sans toucher la
    base, et c'est exactement le genre de preuve molle qu'on refuse ici.
    """
    client, depot = application
    corps = client.get("/health").json()
    assert corps.get("backend") == "postgresql"
    assert corps.get("database_integrity") == "ok"
    assert int(corps.get("database_bytes") or 0) > 0
    # On ne se contente pas de « la chaîne contient PostgreSQL » : une version
    # fabriquée passerait. On exige que l'application rapporte EXACTEMENT ce
    # que le serveur déclare, lu ici-même par une connexion indépendante.

    import psycopg2

    url = _url_base_servie()
    with psycopg2.connect(url) as cx:
        with cx.cursor() as cur:
            cur.execute("SELECT version()")
            version_serveur = cur.fetchone()[0]
    assert corps.get("version_postgresql") == version_serveur, (
        "l'application rapporte une version qu'elle n'a pas lue sur le serveur"
    )


def test_le_corpus_est_bien_dans_la_base_que_l_application_sert(application):
    """Les 28 fiches déposées sont dans CETTE base, pas dans une autre."""
    _client, depot = application
    assert depot["fiche_total"] == depot["fiches_ecrites"] >= 1
    assert depot["sail_extractions_total"] == depot["extractions_ecrites"] >= 1


# ---------------------------------------------------------------------------
# Le constat que la navigation a fait apparaître
# ---------------------------------------------------------------------------


def test_le_corpus_depose_est_retrouvable(application):
    """Une fiche déposée est cherchable immédiatement — décision produit.

    Ce test a d'abord énoncé le CONTRAIRE, et c'est lui qui a révélé le
    défaut : les 28 fiches étaient dans `fiche`, la vue les comptait, et
    `/recherche` n'en rendait aucune. Deux causes, toutes deux dans mon
    outil de dépôt, aucune dans le pipeline :

      1. `search_vector` restait NULL. Ce rafraîchissement n'a lieu ni par
         trigger ni automatiquement : c'est le code qui l'appelle. L'omettre
         rendait le corpus invisible bien qu'écrit.
      2. le statut de dépôt était `importe`, qui n'est pas dans le
         vocabulaire du produit (RG3 : l'arrivée est toujours `a_valider`).
         La recherche ne rend que `valide` et `a_valider` — le statut
         « importe » excluait donc tout, même vecteur rempli.

    Le constat est maintenant retourné : le corpus déposé est retrouvable,
    et ce test le verrouille pour qu'il ne redevienne pas invisible.
    """
    client, depot = application
    corps = client.get("/recherche", params={"limit": 100}).json()
    assert "nb_resultats" in corps and "facettes" in corps
    assert corps["nb_resultats"] == depot["fiche_total"], (
        f"sur {depot['fiche_total']} fiches déposées, la recherche n'en rend "
        f"que {corps['nb_resultats']} : une fiche déposée doit être retrouvable"
    )


def test_le_total_et_la_pagination_franchissent_le_plafond_de_tirage(application):
    """Au-delà de 100 fiches, le total annoncé doit rester le VRAI total.

    Défaut trouvé au Lot 15, et il n'était visible qu'au-dessus de 100 fiches :
    ``PROFONDEUR_SOURCES = 100`` bornait ce que chaque source remontait avant
    fusion, et ``nb_resultats`` valait ``len(fusion)`` — le nombre de candidats
    TIRÉS, pas le nombre de correspondances. Un corpus de 123 fiches annonçait
    donc 100 résultats, la pagination s'arrêtait avant la fin, et les facettes
    étaient calculées sur un échantillon tronqué. Silencieusement.

    La correction fait venir le total d'un ``COUNT(*)`` et porte la profondeur
    de tirage jusqu'à ``offset + limit``.

    **Vérifié par mutation** : revenir à ``total = len(fusion)`` fait échouer
    ce test sur « assert 100 == 123 » — très exactement le message de la CI.
    Un test qui ne peut pas échouer ne prouve rien.
    """
    client, depot = application
    attendu = depot["fiche_total"]

    # Garde-fou d'à-propos : sous 100 fiches, PROFONDEUR_SOURCES ne borne plus
    # rien et ce test perd sa raison d'être. Il doit alors le DIRE, et non
    # passer en silence.
    assert attendu > 100, (
        f"ce test ne prouve plus rien sous 100 fiches (corpus : {attendu}) ; "
        "le plafond de tirage n'est alors jamais atteint"
    )

    # 1. Le total annoncé est le vrai total, pas un échantillon.
    corps = client.get("/recherche", params={"limit": 100}).json()
    assert corps["nb_resultats"] == attendu, (
        f"la recherche annonce {corps['nb_resultats']} résultats sur {attendu} fiches : "
        "le total est un échantillon tronqué, pas un décompte"
    )
    assert corps["total_exact"] is True, "le total annoncé n'est pas présenté comme exact"

    # 2. La pagination FRANCHIT le plafond : la page d'après n'est pas vide.
    page = client.get("/recherche", params={"limit": 20, "offset": 100}).json()
    assert page["resultats"], (
        "la page au-delà de la 100ᵉ fiche est vide : les fiches au-delà du "
        "plafond de tirage sont inatteignables"
    )
    assert page["nb_resultats"] == attendu, "le total varie selon la page demandée"

    # 3. Au-delà de la fin : page vide, total toujours exact.
    apres = client.get("/recherche", params={"limit": 20, "offset": attendu}).json()
    assert apres["resultats"] == []
    assert apres["nb_resultats"] == attendu

    # 4. Les facettes ne dépendent PAS de la taille de page : elles portent sur
    #    l'ensemble des correspondances, jamais sur la page affichée. C'est
    #    l'invariant qui trahissait le plus silencieusement le défaut.
    petit = client.get("/recherche", params={"limit": 10}).json()["facettes"]
    grand = client.get("/recherche", params={"limit": 100}).json()["facettes"]
    assert petit == grand, "les facettes sont calculées sur la page, pas sur le corpus"


def test_la_recherche_plein_texte_trouve_le_corpus(application):
    """Au-delà du comptage : une vraie requête rend de vraies fiches."""
    client, depot = application
    corps = client.get("/recherche", params={"q": "enrouleur", "limit": 100}).json()
    assert corps["nb_resultats"] > 0, (
        "« enrouleur » est une gamme portée par plusieurs fiches du corpus : "
        "une recherche qui n'en rend aucune ne lit pas la base"
    )
    for resultat in corps["resultats"]:
        assert resultat.get("code"), f"résultat sans code exploitable : {resultat}"


def test_les_routes_de_resolution_exigent_un_compte_nominatif(application):
    """La porte de résolution est fermée sans compte : c'est une propriété.

    Puisque c'est la résolution qui rendra le corpus retrouvable, la route qui
    la commande doit être gardée. On verrouille le garde.
    """
    client, _depot = application
    reponse = client.get("/extractions-voile")
    assert reponse.status_code == 401, (
        f"les extractions doivent exiger un compte nominatif, reçu {reponse.status_code}"
    )


def test_la_comparaison_reclame_deux_fiches(application):
    """Une comparaison sans deux termes est une erreur de l'appelant, pas un crash."""
    client, _depot = application
    reponse = client.get("/comparaison-voiles")
    assert reponse.status_code == 422, reponse.text
