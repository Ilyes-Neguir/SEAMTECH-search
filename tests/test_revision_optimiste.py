"""Verrou optimiste sur les corrections de fiche (revue indépendante, 2026-10-07).

Défaut corrigé, en une phrase : ``corriger_champ`` écrivait
``UPDATE fiche_champ_extrait … WHERE id_champ = %s`` sans comparer l'état lu.
Deux opérateurs ouvrant la même fiche (deux postes, deux onglets) et corrigeant
le même champ aboutissaient donc à « dernier écrivain gagne » : la correction du
premier DISPARAISSAIT sans trace, et le second n'en était jamais averti.

Ce que ces tests éprouvent, sur PostgreSQL réel et par HTTP réel :

1. ``/fiches/{code}`` publie la **révision** de la fiche ;
2. une correction qui porte la révision lue aboutit et **fait avancer** la
   révision ;
3. une correction **périmée** est REFUSÉE (409) — la valeur du collègue est
   conservée intacte, en base et dans la réponse de conflit ;
4. le conflit est *concurrentiel*, pas séquentiel : deux clients enregistrent
   EN MÊME TEMPS (départ synchronisé) — une seule correction passe ;
5. un client qui n'envoie pas de révision garde l'ancien comportement, mais
   l'absence de verrou est JOURNALISÉE (jamais présentée comme une protection) ;
6. les décisions (valider/rouvrir) font aussi avancer la révision : un poste
   resté sur l'ancienne révision ne peut plus écrire après une décision.

Aucun double, aucune simulation de conflit : c'est la base qui arbitre.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

RACINE = Path(__file__).resolve().parent.parent
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)


# ---------------------------------------------------------------------------
# Base jetable + une fiche réelle déposée par le moteur du Lot C (RG3)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_revision() -> Iterator[dict[str, Any]]:
    """Base jetable (migrations + gabarits) et UNE fiche ``a_valider`` réelle."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"revision_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"

    from seamtech_search.fiches import depot
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path(f"/tmp/unused-revision-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)

    racine_essai = Path(f"/tmp/revision-dossiers-{nom_base}")
    dossier = racine_essai / "CLIENT-GENOA"
    dossier.mkdir(parents=True)
    source = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"
    (dossier / "fiche.pdf").write_bytes(source.read_bytes())
    resultat = depot.deposer_dossier(index, dossier)
    assert resultat.get("statut") == "traite", resultat
    code = str(resultat["fiche"])

    try:
        yield {"index": index, "url": url_base, "code": code, "racine_essai": racine_essai}
    finally:
        shutil.rmtree(racine_essai, ignore_errors=True)
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


def _client(base: dict, tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base["url"],
    )
    return TestClient(create_app(config))


def _champ_corrigeable(base: dict, code: str) -> tuple[str, int | None, str]:
    """Premier champ texte de la fiche (nom, rang, valeur courante)."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT c.champ, c.rang, COALESCE(c.valeur_normalisee, '') FROM fiche_champ_extrait c "
                "JOIN fiche f ON f.id_fiche = c.id_fiche WHERE f.code = %s "
                "AND c.champ NOT LIKE 'cotes.%%' ORDER BY c.id_champ LIMIT 1",
                (code,),
            )
            ligne = cursor.fetchone()
    assert ligne is not None, f"aucun champ corrigeable sur {code}"
    return str(ligne[0]), (None if ligne[1] is None else int(ligne[1])), str(ligne[2])


def _lire_en_base(base: dict, code: str, champ: str, rang: int | None) -> dict[str, Any]:
    """Lecture DIRECTE en base : c'est elle qui fait foi, pas l'interface."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT revision FROM fiche WHERE code = %s", (code,))
            revision = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COALESCE(valeur_normalisee, ''), corrige, corrige_le "
                "FROM fiche_champ_extrait WHERE id_fiche = (SELECT id_fiche FROM fiche WHERE code = %s) "
                "AND champ = %s AND rang IS NOT DISTINCT FROM %s",
                (code, champ, rang),
            )
            ligne = cursor.fetchone()
    return {
        "revision": revision,
        "valeur": str(ligne[0]) if ligne else None,
        "corrige": bool(ligne[1]) if ligne else None,
        "corrige_le": ligne[2] if ligne else None,
    }


def _corriger(
    client: Any, code: str, champ: str, valeur: str, rang: int | None, revision: Any,
    utilisateur: str | None = None,
):
    corps: dict[str, Any] = {"champ": champ, "valeur": valeur}
    if rang is not None:
        corps["rang"] = rang
    if revision is not None:
        corps["revision"] = revision
    if utilisateur is not None:
        corps["utilisateur"] = utilisateur
    return client.post(f"/fiches/{code}/corriger", json=corps)


def _compte_existe(base: dict, identifiant: str) -> bool:
    """Le compte opérateur a-t-il été créé ? Une écriture REFUSÉE ne doit créer
    AUCUNE trace, pas même la ligne « utilisateur » de son auteur."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM utilisateur WHERE identifiant = %s", (identifiant,))
            return int(cursor.fetchone()[0]) > 0


# ---------------------------------------------------------------------------
# 1. La révision est publiée et fait avancer la fiche
# ---------------------------------------------------------------------------


def test_la_fiche_publie_sa_revision(base_revision: dict, tmp_path: Path) -> None:
    """/fiches/{code} expose la révision : sans elle, aucun poste ne peut la renvoyer."""
    client = _client(base_revision, tmp_path)
    reponse = client.get(f"/fiches/{base_revision['code']}")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["revision"] == 1, corps


def test_une_correction_a_jour_aboutit_et_fait_avancer_la_revision(
    base_revision: dict, tmp_path: Path
) -> None:
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    avant = _lire_en_base(base_revision, code, champ, rang)

    reponse = _corriger(client, code, champ, "valeur-A", rang, avant["revision"])
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["revision"] == avant["revision"] + 1, corps

    apres = _lire_en_base(base_revision, code, champ, rang)
    assert apres["valeur"] == "valeur-A"
    assert apres["corrige"] is True
    assert apres["revision"] == avant["revision"] + 1


def test_une_correction_perimee_est_refusee_et_le_travail_du_collegue_est_preserve(
    base_revision: dict, tmp_path: Path
) -> None:
    """LE DÉFAUT CORRIGÉ : A enregistre, B (révision périmée) est refusé.

    La correction de A reste intacte en base ; la réponse 409 porte la valeur de
    A, son auteur et la règle appliquée — B peut comprendre et se reprendre.
    """
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision_ouverte = _lire_en_base(base_revision, code, champ, rang)["revision"]

    # A ouvre et enregistre.
    reponse_a = _corriger(client, code, champ, "valeur-de-A", rang, revision_ouverte, "poste-A")
    assert reponse_a.status_code == 200, reponse_a.text

    # B, resté sur la révision d'ouverture, soumet SA correction.
    reponse_b = _corriger(client, code, champ, "valeur-de-B", rang, revision_ouverte, "poste-B")
    assert reponse_b.status_code == 409, reponse_b.text
    detail = reponse_b.json()["detail"]
    assert detail["code"] == "conflit_revision", detail
    assert detail["revision_envoyee"] == revision_ouverte
    assert detail["revision_actuelle"] == revision_ouverte + 1
    assert detail["valeur_actuelle"] == "valeur-de-A", detail
    assert detail["corrige_par"] == "poste-A", detail
    assert detail["corrige_le"], detail
    assert "verrou optimiste" in detail["regle"]
    # Une écriture refusée ne laisse AUCUNE trace : pas même la ligne
    # « utilisateur » de l'auteur, créée seulement après l'arbitrage.
    assert not _compte_existe(base_revision, "poste-B"), (
        "un refus ne doit créer aucun compte : l'arbitrage précède toute écriture"
    )

    # LE POINT DÉCISIF : rien n'a été écrit — la valeur de A est toujours là.
    apres = _lire_en_base(base_revision, code, champ, rang)
    assert apres["valeur"] == "valeur-de-A"
    assert apres["revision"] == revision_ouverte + 1

    # B se reprend IMMÉDIATEMENT avec la révision à jour : il ne perd pas son
    # travail, il le rejoue sur un état juste.
    reprise = _corriger(client, code, champ, "valeur-de-B", rang, detail["revision_actuelle"], "poste-B")
    assert reprise.status_code == 200, reprise.text
    assert _lire_en_base(base_revision, code, champ, rang)["valeur"] == "valeur-de-B"


# ---------------------------------------------------------------------------
# 2. Concurrence RÉELLE : deux enregistrements simultanés, une seule écriture
# ---------------------------------------------------------------------------


def test_deux_enregistrements_simultanes_une_seule_correction_passe(
    base_revision: dict, tmp_path: Path
) -> None:
    """Le cœur de la preuve : deux postes enregistrent EN MÊME TEMPS.

    Deux clients HTTP distincts (deux connexions, deux sessions) lisent la même
    révision, puis envoient leur correction en parallèle (départ synchronisé par
    une barrière). Le verdict est rendu par la BASE (compare-and-swap sur
    ``fiche.revision``), pas par un test qui simulerait un conflit : exactement
    un 200 et un 409, et la valeur finale est celle du gagnant.
    """
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision_ouverte = _lire_en_base(base_revision, code, champ, rang)["revision"]

    clients = {nom: _client(base_revision, tmp_path) for nom in ("poste-A", "poste-B")}
    barriere = threading.Barrier(2)
    resultats: dict[str, Any] = {}

    def enregistrer(nom: str) -> None:
        barriere.wait()  # départ simultané, pas séquentiel
        try:
            resultats[nom] = _corriger(
                clients[nom], code, champ, f"valeur-{nom}", rang, revision_ouverte, nom
            )
        except Exception as erreur:  # pragma: no cover - diagnostic seulement
            resultats[nom] = erreur

    fils = [threading.Thread(target=enregistrer, args=(nom,)) for nom in clients]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=60)
        assert not fil.is_alive(), "un enregistrement concurrent ne s'est jamais terminé"

    verdicts = {nom: getattr(reponse, "status_code", str(reponse)) for nom, reponse in resultats.items()}
    assert sorted(str(v) for v in verdicts.values()) == ["200", "409"], f"attendu un 200 et un 409, mesuré {verdicts}"

    gagnant = next(nom for nom, reponse in resultats.items() if reponse.status_code == 200)
    perdant = next(nom for nom, reponse in resultats.items() if reponse.status_code == 409)

    # La base porte la valeur du gagnant, et RIEN du perdant.
    apres = _lire_en_base(base_revision, code, champ, rang)
    assert apres["valeur"] == f"valeur-{gagnant}", apres
    # Une seule correction appliquée : la révision n'avance que d'un cran.
    assert apres["revision"] == revision_ouverte + 1, apres

    # Le perdant reçoit la valeur du gagnant : il comprend ce qui s'est passé.
    detail = resultats[perdant].json()["detail"]
    assert detail["valeur_actuelle"] == f"valeur-{gagnant}", detail
    assert detail["corrige_par"] == gagnant, detail


# ---------------------------------------------------------------------------
# 3. Clients anciens : comportement conservé, absence de verrou DITE
# ---------------------------------------------------------------------------


def test_sans_revision_le_comportement_est_conserve_mais_journalise_comme_non_protege(
    base_revision: dict, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ne jamais présenter une écriture sans verrou comme une protection."""
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    avant = _lire_en_base(base_revision, code, champ, rang)

    with caplog.at_level("WARNING", logger="seamtech_search.fiches.routes"):
        reponse = _corriger(client, code, champ, "valeur-sans-verrou", rang, None, "poste-sans-verrou")
    assert reponse.status_code == 200, reponse.text
    apres = _lire_en_base(base_revision, code, champ, rang)
    assert apres["valeur"] == "valeur-sans-verrou"
    # La révision avance quand même : l'écriture est réelle.
    assert apres["revision"] == avant["revision"] + 1
    assert any("SANS révision fournie" in message for message in caplog.messages), caplog.messages

    # Une révision mal formée est refusée AVANT toute écriture (422, pas 500).
    for invalide in ("abc", -1, 0):
        refus = _corriger(client, code, champ, "valeur-invalide", rang, invalide)
        assert refus.status_code == 422, (invalide, refus.text)
    assert _lire_en_base(base_revision, code, champ, rang)["valeur"] == "valeur-sans-verrou"


# ---------------------------------------------------------------------------
# 4. Une décision fait avancer la révision : un poste périmé ne peut plus écrire
# ---------------------------------------------------------------------------


def test_une_decision_invalide_la_revision_des_postes_restes_ouverts(
    base_revision: dict, tmp_path: Path
) -> None:
    """Après une validation, la révision du poste resté ouvert est périmée.

    Deux garanties se cumulent : la transition de statut (RG11, 409) et le verrou
    de révision. On vérifie ici que la décision AVANCE la révision, donc qu'un
    poste resté ouvert est arrêté sur le verrou même après réouverture.
    """
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision_ouverte = _lire_en_base(base_revision, code, champ, rang)["revision"]

    validation = client.post(f"/fiches/{code}/valider", json={})
    assert validation.status_code == 200, validation.text
    assert validation.json()["revision"] == revision_ouverte + 1, validation.json()

    # RG11 : corriger une fiche validée est refusé.
    refus = _corriger(client, code, champ, "valeur-apres-validation", rang, revision_ouverte + 1, "poste-A")
    assert refus.status_code == 409, refus.text
    assert "RG11" in str(refus.json()["detail"])

    # Réouverture : la révision avance ENCORE (le contenu redevient modifiable).
    reouverture = client.post(f"/fiches/{code}/rouvrir", json={})
    assert reouverture.status_code == 200, reouverture.text
    revision_apres_reouverture = reouverture.json()["revision"]
    assert revision_apres_reouverture == revision_ouverte + 2, reouverture.json()

    # Le poste resté sur la révision d'ouverture est arrêté NET (verrou de révision).
    perime = _corriger(client, code, champ, "valeur-perimee", rang, revision_ouverte, "poste-B")
    assert perime.status_code == 409, perime.text
    assert perime.json()["detail"]["code"] == "conflit_revision"
    assert perime.json()["detail"]["revision_actuelle"] == revision_apres_reouverture
    assert _lire_en_base(base_revision, code, champ, rang)["valeur"] != "valeur-perimee"

    # Avec la révision à jour, la correction passe de nouveau.
    a_jour = _corriger(client, code, champ, "valeur-a-jour", rang, revision_apres_reouverture, "poste-B")
    assert a_jour.status_code == 200, a_jour.text
