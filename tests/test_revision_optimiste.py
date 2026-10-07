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
5. une correction SANS révision est REFUSÉE (428) : la protection ne peut pas
   disparaître parce qu'une requête a échoué côté poste (constat n°1 de la revue
   du 2026-10-07). Un client ancien ne peut écrire sans verrou que sous une
   option EXPLICITE (``SEAMTECH_REQUIRE_REVISION=false``), et l'absence de
   protection reste JOURNALISÉE ;
6. les décisions (valider/rejeter/rouvrir) font aussi avancer la révision, et
   sont LIÉES à la révision revue (constat n°3) : approuver un écran périmé est
   REFUSÉ (409), sans écriture ni ligne « valider » au journal ;
7. la validation en lot porte la révision affichée de chaque fiche : une fiche
   qui a bougé depuis la sélection est IGNORÉE avec sa raison.

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


def _etat_en_base(base: dict, code: str) -> dict[str, Any]:
    """statut + révision : ce qui décide si une décision est encore valable."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut, revision FROM fiche WHERE code = %s", (code,))
            ligne = cursor.fetchone()
    return {"statut": str(ligne[0]), "revision": int(ligne[1] if ligne[1] is not None else 1)}


def _journal(base: dict, code: str) -> list[str]:
    """Actions réellement tracées pour la fiche — l'audit ne doit pas mentir."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT v.action FROM fiche_validation v JOIN fiche f ON f.id_fiche = v.id_fiche "
                "WHERE f.code = %s ORDER BY v.id_validation",
                (code,),
            )
            return [str(ligne[0]) for ligne in cursor.fetchall()]


def _remettre_a_valider(client: Any, base: dict, code: str) -> int:
    """Ramène la fiche à « a_valider » et renvoie sa révision courante.

    Les tests de ce module partagent une base jetable unique : chaque test lit
    donc l'état réel au lieu de supposer un numéro de révision.
    """
    etat = _etat_en_base(base, code)
    if etat["statut"] != "a_valider":
        reouverture = _decision(client, code, "rouvrir", etat["revision"])
        assert reouverture.status_code == 200, reouverture.text
        etat = _etat_en_base(base, code)
    return etat["revision"]


def _decision(client: Any, code: str, action: str, revision: Any, **extra: Any):
    corps: dict[str, Any] = dict(extra)
    if revision is not None:
        corps["revision"] = revision
    return client.post(f"/fiches/{code}/{action}", json=corps)


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


def test_sans_revision_la_correction_est_refusee_ferme(base_revision: dict, tmp_path: Path) -> None:
    """CONSTAT N°1 : « la révision n'a pas pu être lue » ne doit JAMAIS devenir
    « écriture non protégée ».

    Le défaut d'origine : l'écran laissait corriger quand la révision manquait
    (``.catch(() => setRevisionFiche(null))``) et le backend acceptait l'omission
    en écrivant sans condition. Une requête de révision ratée — ou un clic avant
    la fin du chargement — désactivait donc la protection anti-écrasement, sans
    que personne ne le sache. On vérifie ici les trois issues :
    refus (428), AUCUNE écriture, AUCUNE ligne au journal.
    """
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    avant = _lire_en_base(base_revision, code, champ, rang)
    journal_avant = _journal(base_revision, code)

    refus = _corriger(client, code, champ, "valeur-sans-revision", rang, None, "poste-tete-en-l-air")
    assert refus.status_code == 428, refus.text
    assert "révision" in refus.json()["detail"]
    # Une révision VIDE est traitée comme une révision absente : même refus.
    vide = client.post(f"/fiches/{code}/corriger", json={"champ": champ, "valeur": "x", "rang": rang, "revision": "  "})
    assert vide.status_code == 428, vide.text

    apres = _lire_en_base(base_revision, code, champ, rang)
    assert apres["valeur"] == avant["valeur"], "une correction refusée ne doit RIEN écrire"
    assert apres["revision"] == avant["revision"], "une correction refusée ne fait pas avancer la révision"
    assert _journal(base_revision, code) == journal_avant
    assert not _compte_existe(base_revision, "poste-tete-en-l-air")


def test_ecriture_sans_revision_seulement_sous_option_explicite(
    base_revision: dict, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Le client ancien reste possible — mais il faut le DEMANDER explicitement.

    ``SEAMTECH_REQUIRE_REVISION=false`` (ici posé sur la configuration du
    client) autorise l'écriture inconditionnelle ; elle est alors journalisée
    comme NON PROTÉGÉE. Aucun écran d'atelier n'emprunte ce chemin : l'écran
    envoie toujours la révision qu'il affiche.
    """
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base_revision["url"],
        require_revision=False,
    )
    client = TestClient(create_app(config))
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

    validation = _decision(client, code, "valider", revision_ouverte)
    assert validation.status_code == 200, validation.text
    assert validation.json()["revision"] == revision_ouverte + 1, validation.json()

    # RG11 : corriger une fiche validée est refusé.
    refus = _corriger(client, code, champ, "valeur-apres-validation", rang, revision_ouverte + 1, "poste-A")
    assert refus.status_code == 409, refus.text
    assert "RG11" in str(refus.json()["detail"])

    # Réouverture : la révision avance ENCORE (le contenu redevient modifiable).
    reouverture = _decision(client, code, "rouvrir", revision_ouverte + 1)
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


# ---------------------------------------------------------------------------
# 5. La DÉCISION porte sur la révision que l'opérateur a relue (constat n°3)
# ---------------------------------------------------------------------------


def test_valider_un_ecran_perime_est_refuse_sans_aucune_trace(
    base_revision: dict, tmp_path: Path
) -> None:
    """Scénario de fabrication : A approuve une fiche que B vient de modifier.

    Défaut d'origine : la validation ne portait AUCUNE révision ; l'opérateur A
    validait donc l'état COURANT — y compris une valeur qu'il n'avait jamais
    lue — et le journal enregistrait un « valider » trompeur. Ici :

    1. A relit la fiche (révision N) ;
    2. B corrige un champ (révision N+1) ;
    3. A clique « Valider » sur son écran périmé → 409, RIEN n'est écrit :
       statut toujours a_valider, révision toujours N+1, AUCUNE ligne « valider »
       au journal ;
    4. A recharge et revalide avec la révision N+1 : là seulement la décision
       s'applique.
    """
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision_ouverte = _remettre_a_valider(client, base_revision, code)

    # 2. Le collègue modifie une valeur de fabrication.
    correction = _corriger(client, code, champ, "valeur-du-collegue", rang, revision_ouverte, "poste-B")
    assert correction.status_code == 200, correction.text
    journal_avant = _journal(base_revision, code)

    # 3. A valide avec la révision de SON écran (périmée).
    refus = _decision(client, code, "valider", revision_ouverte)
    assert refus.status_code == 409, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "conflit_decision", detail
    assert detail["decision"] == "valider"
    assert detail["revision_envoyee"] == revision_ouverte
    assert detail["revision_actuelle"] == revision_ouverte + 1
    assert "verrou optimiste de décision" in detail["regle"]

    etat = _etat_en_base(base_revision, code)
    assert etat == {"statut": "a_valider", "revision": revision_ouverte + 1}, etat
    assert _journal(base_revision, code) == journal_avant, (
        "une validation refusée ne doit laisser AUCUNE ligne « valider » : "
        "l'audit de fabrication ne doit pas mentir"
    )

    # 4. Après rechargement (révision à jour), la décision passe pour de bon —
    # et c'est LA seule ligne « valider » ajoutée par ce test.
    avant_decision = _journal(base_revision, code).count("valider")
    reussie = _decision(client, code, "valider", etat["revision"])
    assert reussie.status_code == 200, reussie.text
    assert _etat_en_base(base_revision, code) == {"statut": "valide", "revision": revision_ouverte + 2}
    assert _journal(base_revision, code).count("valider") == avant_decision + 1


def test_rejeter_et_rouvrir_sont_lies_a_la_revision_revue(
    base_revision: dict, tmp_path: Path
) -> None:
    """Mêmes garanties pour le rejet et la réouverture — et refus SANS révision."""
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision_ouverte = _remettre_a_valider(client, base_revision, code)

    # Sans révision : refus des trois décisions (fail closed).
    for action, extra in (("valider", {}), ("rejeter", {"motif": "motif de test"}), ("rouvrir", {})):
        refus = _decision(client, code, action, None, **extra)
        assert refus.status_code == 428, (action, refus.text)
    assert _etat_en_base(base_revision, code)["statut"] == "a_valider"

    # Rejet sur une révision périmée : refusé, aucune trace.
    _corriger(client, code, champ, "valeur-collegue", rang, revision_ouverte, "poste-B")
    journal_avant = _journal(base_revision, code)
    rejet_perime = _decision(client, code, "rejeter", revision_ouverte, motif="pièce illisible")
    assert rejet_perime.status_code == 409, rejet_perime.text
    assert rejet_perime.json()["detail"]["code"] == "conflit_decision"
    assert _journal(base_revision, code) == journal_avant
    assert _etat_en_base(base_revision, code)["statut"] == "a_valider"

    # Rejet à jour : accepté et tracé.
    a_jour = _etat_en_base(base_revision, code)["revision"]
    rejet = _decision(client, code, "rejeter", a_jour, motif="pièce illisible")
    assert rejet.status_code == 200, rejet.text
    assert _etat_en_base(base_revision, code) == {"statut": "rejete", "revision": a_jour + 1}

    # Réouverture sur une révision périmée : refusée.
    rouverture_perimee = _decision(client, code, "rouvrir", a_jour)
    assert rouverture_perimee.status_code == 409, rouverture_perimee.text
    # Réouverture à jour : acceptée.
    rouverture = _decision(client, code, "rouvrir", a_jour + 1)
    assert rouverture.status_code == 200, rouverture.text
    assert _etat_en_base(base_revision, code) == {"statut": "a_valider", "revision": a_jour + 2}


def test_validation_en_lot_ignore_une_fiche_modifiee_depuis_la_selection(
    base_revision: dict, tmp_path: Path
) -> None:
    """Le lot aussi : la sélection porte la révision AFFICHÉE de chaque fiche.

    Une fiche corrigée entre la sélection et le clic est IGNORÉE avec sa raison
    (elle n'est pas validée sur un contenu que personne n'a relu), et une fiche
    dont la révision n'a pas été transmise l'est aussi tant que le verrou est
    obligatoire.
    """
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    revision = _remettre_a_valider(client, base_revision, code)

    def lot(revisions: dict[str, Any] | None, **extra: Any):
        corps: dict[str, Any] = {"codes": [code], "acquittement_humain": True, **extra}
        if revisions is not None:
            corps["revisions"] = revisions
        return client.post("/validation/lot", json=corps)

    # Révision absente : la fiche est IGNORÉE (verrou obligatoire), pas validée.
    ignore = lot(None)
    assert ignore.status_code == 200, ignore.text
    assert ignore.json()["nb_validees"] == 0, ignore.json()
    assert "révision attendue manquante" in ignore.json()["ignorees"][0]["raison"]
    assert _etat_en_base(base_revision, code)["statut"] == "a_valider"

    # Révision périmée : ignorée avec la raison de concurrence, aucune trace.
    _corriger(client, code, champ, "valeur-collegue", rang, revision, "poste-B")
    journal_avant = _journal(base_revision, code)
    perime = lot({code: revision})
    assert perime.status_code == 200, perime.text
    assert perime.json()["nb_validees"] == 0, perime.json()
    raison = perime.json()["ignorees"][0]["raison"]
    assert "concurrence" in raison and "relisez" in raison, raison
    assert _etat_en_base(base_revision, code)["statut"] == "a_valider"
    assert _journal(base_revision, code) == journal_avant

    # Révision affichée à jour : la validation groupée passe.
    a_jour = _etat_en_base(base_revision, code)["revision"]
    ok = lot({code: a_jour})
    assert ok.status_code == 200, ok.text
    assert ok.json()["nb_validees"] == 1, ok.json()
    assert _etat_en_base(base_revision, code) == {"statut": "valide", "revision": a_jour + 1}


# ---------------------------------------------------------------------------
# 6. L'écran lit champs ET révision en UN instantané (constat n°2)
# ---------------------------------------------------------------------------


def _enregistrer_collegue(base: dict, code: str, champ: str, rang: int | None, valeur: str) -> int:
    """Écriture d'un collègue, COMMITÉE entre deux lectures (cas réel)."""
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE fiche_champ_extrait SET valeur_normalisee = %s, corrige = TRUE "
                "WHERE id_fiche = (SELECT id_fiche FROM fiche WHERE code = %s) AND champ = %s "
                "AND rang IS NOT DISTINCT FROM %s",
                (valeur, code, champ, rang),
            )
            cursor.execute(
                "UPDATE fiche SET revision = revision + 1, updated_at = now() WHERE code = %s RETURNING revision",
                (code,),
            )
            return int(cursor.fetchone()[0])


class _IndexEspion:
    """Index qui exécute une action APRÈS la première requête SQL d'un appel.

    C'est l'outil déterministe du constat n°2 : on ne « simule » pas un
    entrelacement au hasard, on l'impose exactement entre la première et la
    deuxième requête. Une lecture en DEUX requêtes produit alors forcément une
    paire incohérente ; une lecture en UNE requête reste cohérente.
    """

    def __init__(self, index: Any, action: Any) -> None:
        import contextlib

        self.index_reel = index
        self.action = action
        self.executes = 0
        self._contextlib = contextlib
        # Délègue TOUT le reste (is_postgres, close, migrations…) à l'index réel :
        # l'espion n'observe qu'une chose — le nombre de requêtes par appel.
        self.is_postgres = getattr(index, "is_postgres", False)

    def connect(self) -> Any:
        contexte = self.index_reel.connect()

        @self._contextlib.contextmanager
        def _enveloppe() -> Any:
            with contexte as connexion:
                yield _ConnexionEspion(self, connexion)

        return _enveloppe()

    def _execute(self) -> None:
        self.executes += 1
        if self.executes == 1 and self.action is not None:
            action, self.action = self.action, None
            action()


class _ConnexionEspion:
    def __init__(self, parent: _IndexEspion, connexion: Any) -> None:
        self._parent = parent
        self._connexion = connexion

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return _CurseurEspion(self._parent, self._connexion.cursor(*args, **kwargs))

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._connexion, nom)


class _CurseurEspion:
    def __init__(self, parent: _IndexEspion, curseur: Any) -> None:
        self._parent = parent
        self._curseur = curseur

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        resultat = self._curseur.execute(*args, **kwargs)
        self._parent._execute()
        return resultat

    def __enter__(self) -> "_CurseurEspion":
        self._curseur.__enter__()
        return self

    def __exit__(self, *exception: Any) -> Any:
        return self._curseur.__exit__(*exception)

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._curseur, nom)


def test_etat_fiche_lit_champs_et_revision_dans_le_meme_instantane(
    base_revision: dict, tmp_path: Path
) -> None:
    """Deux requêtes séparées = valeurs périmées + révision fraîche.

    Le test impose l'entrelacement du défaut (le collègue enregistre entre les
    deux lectures) et vérifie que ``/fiches/{code}/etat`` n'y est pas exposé :
    sa charge utile reste, en toutes circonstances, l'état d'UN seul instantané.
    """
    from seamtech_search.fiches import routes

    index = base_revision["index"]
    code = base_revision["code"]
    champ, rang, valeur_initiale = _champ_corrigeable(base_revision, code)
    assert _lire_en_base(base_revision, code, champ, rang)["valeur"] == valeur_initiale

    # DÉFAUT (l'ancien écran) : champs puis révision, avec une écriture entre les deux.
    champs = routes.champs_de_fiche(index, code)
    revision_collegue = _enregistrer_collegue(base_revision, code, champ, rang, "valeur-du-collegue")
    detail = routes.detail_fiche(index, code)
    valeur_lue = next(c["valeur_normalisee"] for c in champs if c["champ"] == champ)
    assert (valeur_lue, detail["revision"]) == (valeur_initiale, revision_collegue), (
        "le test doit bien reproduire l'entrelacement : anciennes valeurs + nouvelle révision"
    )

    # CORRECTIF : l'instantané unique ne peut pas apparier les deux.
    espion = _IndexEspion(
        index,
        action=lambda: _enregistrer_collegue(base_revision, code, champ, rang, "valeur-encore-une-autre"),
    )
    instantane = routes.etat_fiche(espion, code)
    assert espion.executes == 1, "l'état doit être lu en UNE seule requête (instantané PostgreSQL)"
    valeur_instantane = next(c["valeur_normalisee"] for c in instantane["champs"] if c["champ"] == champ)
    assert instantane["revision"] == revision_collegue, instantane["revision"]
    assert valeur_instantane == "valeur-du-collegue", (
        "la révision renvoyée doit être celle des valeurs renvoyées"
    )

    # Et la lecture suivante voit bien le travail du collègue : rien n'est caché.
    apres = routes.etat_fiche(index, code)
    valeur_apres = next(c["valeur_normalisee"] for c in apres["champs"] if c["champ"] == champ)
    assert (valeur_apres, apres["revision"]) == ("valeur-encore-une-autre", revision_collegue + 1)


def test_etat_fiche_expose_le_statut_la_revision_et_les_champs_par_http(
    base_revision: dict, tmp_path: Path
) -> None:
    """La route HTTP : une seule réponse porte statut, révision et champs."""
    client = _client(base_revision, tmp_path)
    code = base_revision["code"]
    revision_courante = _remettre_a_valider(client, base_revision, code)
    reponse = client.get(f"/fiches/{code}/etat")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["code"] == code
    assert corps["statut"] == "a_valider"
    # L'instantané HTTP reflète EXACTEMENT l'état que la base vient d'exposer.
    assert corps["revision"] == revision_courante
    assert corps["champs"], "les champs doivent accompagner la révision"

    # Après correction, la révision ET la valeur avancent ENSEMBLE dans la même réponse.
    champ, rang, _ = _champ_corrigeable(base_revision, code)
    assert _corriger(client, code, champ, "valeur-instantane", rang, corps["revision"]).status_code == 200
    suivant = client.get(f"/fiches/{code}/etat").json()
    assert suivant["revision"] == corps["revision"] + 1
    valeur = next(c["valeur_normalisee"] for c in suivant["champs"] if c["champ"] == champ)
    assert valeur == "valeur-instantane"

    # Fiche inconnue : 404 clair, pas une 500.
    assert client.get("/fiches/INCONNUE-9999/etat").status_code == 404
