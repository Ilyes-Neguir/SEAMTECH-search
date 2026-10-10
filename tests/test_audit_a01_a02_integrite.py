"""Intégrité revue → donnée métier : constats A01 et A02 (audit du 2026-10-08).

A01 — « corrigé » ne corrigeait rien (ou pas la donnée qui sert)
================================================================

``POST /fiches/{code}/corriger`` n'écrivait que la TRACE
(``fiche_champ_extrait.valeur_normalisee``, ``corrige``). Les tables typées
(``fiche_cotes`` en tête) gardaient l'ancienne valeur, et comme le texte de
recherche pondéré et les filtres numériques sont calculés À PARTIR d'elles, une
correction de « 6,6 » en « 7,7 » changeait ce que l'opérateur voyait dans la
revue sans changer ce que l'atelier cherchait : un faux succès.

Ces tests vérifient, sur PostgreSQL réel et par HTTP réel, que la correction :

1. écrit la valeur dans la donnée canonique (``fiche_cotes``, ``fiche_renfort``,
   ``fiche_option``…), pas seulement dans la trace ;
2. rafraîchit le texte de recherche DANS LA MÊME TRANSACTION — la nouvelle
   valeur (formes « 7.7 » ET « 7,7 ») est trouvable, l'ancienne ne l'est plus ;
3. refuse (422) une valeur invalide pour une cible connue, SANS RIEN écrire
   (ni trace, ni donnée, ni journal, ni révision) ;
4. DIT qu'une cible n'est pas supportée au lieu de rapporter un succès muet
   (identité de fiche, clé étrangère, champ composite) ;
5. sait réconcilier une divergence HISTORIQUE, uniquement sur demande explicite
   et avec une trace au journal — jamais en réécrivant l'histoire en silence.

A02 — ré-extraction sans changement de révision
===============================================

Une ré-extraction qui REMPLACE le contenu d'une fiche ``a_valider`` faisait
avancer le contenu sans faire avancer la révision : un écran resté ouvert
pouvait encore valider ce qu'il n'avait jamais affiché. Désormais le
remplacement sur place incrémente ``revision`` dans la même instruction, le
dépôt PUBLIE (``revision``, ``revision_avant``, ``contenu_remplace``), et
valider sur l'ancienne révision est refusé (409) sans écriture ni journal.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

RACINE = Path(__file__).resolve().parent.parent
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)


# --------------------------------------------------------------------------- #
# Base jetable + une fiche réellement déposée (même montage que la revue)
# --------------------------------------------------------------------------- #


def _creer_base(prefixe: str) -> dict[str, Any]:
    """Base jetable + gabarits + les deux dossiers réels du dépôt déposés.

    Deux bases distinctes (A01 et A02) plutôt qu'une seule : les tests A01
    CORRIGENT des champs, et une fiche corrigée refuse par construction toute
    ré-extraction (RG11). Mélanger les deux dans une même base ferait dépendre
    les tests A02 de l'ordre d'exécution — un test qui ne prouve plus rien.
    """
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.fiches import depot
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    nom_base = f"{prefixe}_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"

    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)

    racine_essai = Path(f"/tmp/audit-dossiers-{nom_base}")
    racine_essai.mkdir(parents=True, exist_ok=True)
    codes: dict[str, str] = {}
    for nom, source in (
        ("GENOA", RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"),
        ("SO", RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
    ):
        dossier = racine_essai / nom
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "fiche.pdf").write_bytes(source.read_bytes())
        resultat = depot.deposer_dossier(index, dossier)
        assert resultat.get("statut") == "traite", resultat
        codes[nom] = str(resultat["fiche"])
    assert codes["GENOA"] != codes["SO"], "les deux dossiers doivent donner deux fiches distinctes"

    return {
        "nom_base": nom_base,
        "index": index,
        "url": url_base,
        "genoa": codes["GENOA"],
        "so": codes["SO"],
        "racine_essai": racine_essai,
    }


def _detruire_base(base: dict[str, Any]) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    shutil.rmtree(base["racine_essai"], ignore_errors=True)
    base["index"].close()
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (base["nom_base"],),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{base["nom_base"]}"')
    finally:
        admin.close()


@pytest.fixture(scope="module")
def base_audit() -> Iterator[dict[str, Any]]:
    """Base jetable pour les constats A01 (les tests y CORRIGENT des champs)."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    base = _creer_base("audit_a01")
    try:
        yield base
    finally:
        _detruire_base(base)


@pytest.fixture(scope="module")
def base_a02() -> Iterator[dict[str, Any]]:
    """Base jetable pour A02 : fiches INTACTES (aucune correction), donc ré-extractibles."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    base = _creer_base("audit_a02")
    try:
        yield base
    finally:
        _detruire_base(base)


@pytest.fixture()
def client(base_audit: dict, tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base_audit["url"],
    )
    return TestClient(create_app(config))


# --------------------------------------------------------------------------- #
# Lectures directes en base : c'est elle qui fait foi, pas la réponse HTTP
# --------------------------------------------------------------------------- #


def _requete(base: dict, sql: str, parametres: tuple = ()) -> list[tuple]:
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(sql, parametres)
            return list(cursor.fetchall())


def _id_fiche(base: dict, code: str) -> int:
    return int(_requete(base, "SELECT id_fiche FROM fiche WHERE code = %s", (code,))[0][0])


def _revision(base: dict, code: str) -> int:
    return int(_requete(base, "SELECT revision FROM fiche WHERE code = %s", (code,))[0][0])


def _trace(base: dict, code: str, champ: str, rang: int | None = None) -> dict[str, Any]:
    lignes = _requete(
        base,
        "SELECT COALESCE(valeur_normalisee, ''), corrige, corrige_le FROM fiche_champ_extrait "
        "WHERE id_fiche = (SELECT id_fiche FROM fiche WHERE code = %s) AND champ = %s "
        "AND rang IS NOT DISTINCT FROM %s",
        (code, champ, rang),
    )
    assert lignes, f"trace absente : {champ} (rang {rang})"
    return {"valeur": str(lignes[0][0]), "corrige": bool(lignes[0][1]), "corrige_le": lignes[0][2]}


def _corriger(client, code: str, corps: dict[str, Any]):
    payload = {"revision": _revision(client.base_audit, code), **corps}
    return client.post(f"/fiches/{code}/corriger", json=payload)


def _journal(base: dict, code: str) -> list[tuple]:
    """Journal de validation (``fiche_validation``) — l'audit de ce qui a été décidé."""
    return _requete(
        base,
        "SELECT action, etat_apres, commentaire FROM fiche_validation "
        "WHERE id_fiche = (SELECT id_fiche FROM fiche WHERE code = %s) ORDER BY id_validation",
        (code,),
    )


def _champ_cote(base: dict, code: str) -> tuple[str, str, int, str, float]:
    """Un champ ``cotes.<jeu>.<col>`` RÉELLEMENT présent, avec sa valeur canonique."""
    lignes = _requete(
        base,
        """
        SELECT c.champ, c.colonne_cible, c.rang, cd.jeu
        FROM fiche_champ_extrait c
        JOIN fiche f ON f.id_fiche = c.id_fiche
        JOIN fiche_cotes cd ON cd.id_fiche = c.id_fiche
        WHERE f.code = %s AND c.champ LIKE 'cotes.%%' AND c.colonne_cible IN (
            'slu_m','sle_m','sf_m','shw_m','spa_m2','tetiere_cm','poids_kg'
        )
        ORDER BY c.champ, c.rang
        """,
        (code,),
    )
    assert lignes, f"aucune cote exploitable sur {code}"
    champ, colonne, _rang, jeu = (str(lignes[0][0]), str(lignes[0][1]), lignes[0][2], str(lignes[0][3]))
    valeur = _requete(
        base,
        f"SELECT {colonne} FROM fiche_cotes WHERE id_fiche = "
        "(SELECT id_fiche FROM fiche WHERE code = %s) AND jeu = %s",
        (code, jeu),
    )[0][0]
    assert valeur is not None, f"cote {colonne} vide pour {jeu} — la propagation ne serait pas prouvable"
    return champ, colonne, jeu, valeur


# --------------------------------------------------------------------------- #
# A01 — la correction atteint la donnée canonique, la recherche et la trace
# --------------------------------------------------------------------------- #


def test_a01_correction_cote_propage_vers_canonique_et_recherche(base_audit, client) -> None:
    """Le cœur du constat A01 : 6,6 → 7,7 doit changer la DONNÉE, pas la trace seule."""
    client.base_audit = base_audit
    code = base_audit["genoa"]
    champ, colonne, jeu, avant = _champ_cote(base_audit, code)
    revision_avant = _revision(base_audit, code)
    nouvelle = float(avant) + 1.25

    reponse = _corriger(client, code, {"champ": champ, "valeur": str(nouvelle)})
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["propagation"]["statut"] == "appliquee", corps
    assert corps["propagation"]["cible"] == f"fiche_cotes.{colonne}", corps

    # 1. Donnée canonique : la valeur a CHANGÉ en base.
    canonique = _requete(
        base_audit,
        f"SELECT {colonne} FROM fiche_cotes WHERE id_fiche = "
        "(SELECT id_fiche FROM fiche WHERE code = %s) AND jeu = %s",
        (code, jeu),
    )[0][0]
    assert float(canonique) == pytest.approx(nouvelle, abs=1e-3), (canonique, nouvelle)

    # 2. Trace de revue : corrigée, et c'est la référence de l'opérateur.
    trace = _trace(base_audit, code, champ)
    assert trace["corrige"] is True
    assert float(trace["valeur"]) == pytest.approx(nouvelle, abs=1e-3)

    # 3. Texte de recherche : recalculé dans la même transaction — les DEUX
    #    formes (point et virgule) apparaissent, l'ancienne valeur disparaît.
    texte = str(_requete(base_audit, "SELECT champs_texte FROM fiche WHERE code = %s", (code,))[0][0])
    variantes = {f"{nouvelle:.2f}", f"{nouvelle:.2f}".replace(".", ",")}
    assert any(v in texte for v in variantes), (variantes, texte)

    # 4. Et la recherche HTTP le CONSTATE (c'est le parcours réel de l'atelier).
    trouve = client.get("/recherche", params={"q": f"{nouvelle:.2f}".replace(".", ",")})
    assert trouve.status_code == 200, trouve.text
    codes = {str(ligne.get("code")) for ligne in trouve.json().get("resultats", [])}
    assert code in codes, (code, codes)

    # 5. Révision + journal : une correction engage l'audit.
    assert _revision(base_audit, code) == revision_avant + 1
    journal = _journal(base_audit, code)
    assert journal and journal[-1][0] == "corriger" and "appliquee" in str(journal[-1][2]), journal


def test_a01_correction_champ_repete_par_rang(base_audit, client) -> None:
    """Un champ répété (renfort.note par rang) ne touche QUE la ligne visée."""
    client.base_audit = base_audit
    code = base_audit["so"]
    id_fiche = _id_fiche(base_audit, code)
    lignes = _requete(
        base_audit,
        "SELECT champ, rang, colonne_cible FROM fiche_champ_extrait WHERE id_fiche = %s "
        "AND table_cible = 'fiche_renfort' ORDER BY rang",
        (id_fiche,),
    )
    if len(lignes) < 2:
        pytest.skip("le dossier réel n'expose pas plusieurs renforts : rang non exerçable ici")
    champ, rang = str(lignes[1][0]), int(lignes[1][1])
    avant = _requete(
        base_audit,
        "SELECT description FROM fiche_renfort WHERE id_fiche = %s ORDER BY id_renfort",
        (id_fiche,),
    )
    assert len(avant) >= rang

    reponse = _corriger(client, code, {"champ": champ, "valeur": "renfort corrigé", "rang": rang})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["propagation"]["statut"] == "appliquee"
    assert reponse.json()["propagation"]["cle"] == f"renfort_{rang}"

    apres = [
        ligne[0]
        for ligne in _requete(
            base_audit,
            "SELECT description FROM fiche_renfort WHERE id_fiche = %s ORDER BY id_renfort",
            (id_fiche,),
        )
    ]
    assert apres[rang - 1] == "renfort corrigé", apres
    for autre in range(len(apres)):
        if autre != rang - 1:
            assert apres[autre] == avant[autre][0], "un autre rang de renfort a été touché"


def test_a01_option_corrigee_synchronise_texte_et_booleen(base_audit, client) -> None:
    """Option : la valeur texte ET son booléen disent la même chose après correction."""
    client.base_audit = base_audit
    code = base_audit["so"]
    id_fiche = _id_fiche(base_audit, code)
    lignes = _requete(
        base_audit,
        "SELECT champ, colonne_cible FROM fiche_champ_extrait WHERE id_fiche = %s "
        "AND table_cible = 'fiche_option' ORDER BY id_champ LIMIT 1",
        (id_fiche,),
    )
    if not lignes:
        pytest.skip("le dossier réel n'expose aucune option : cible non exerçable ici")
    champ, colonne_cible = str(lignes[0][0]), str(lignes[0][1])

    reponse = _corriger(client, code, {"champ": champ, "valeur": "oui"})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["propagation"]["statut"] == "appliquee", reponse.text

    valeur_texte, valeur_bool = _requete(
        base_audit,
        "SELECT valeur_texte, valeur_bool FROM fiche_option WHERE id_fiche = %s AND code = %s",
        (id_fiche, colonne_cible),
    )[0]
    assert valeur_texte == "oui", valeur_texte
    assert valeur_bool is True, valeur_bool


def test_a01_valeur_invalide_refusee_sans_aucune_ecriture(base_audit, client) -> None:
    """Valeur impossible pour une cible connue : 422, et RIEN n'a bougé."""
    client.base_audit = base_audit
    code = base_audit["genoa"]
    champ, colonne, jeu, avant = _champ_cote(base_audit, code)
    revision_avant = _revision(base_audit, code)
    trace_avant = _trace(base_audit, code, champ)
    journal_avant = len(_journal(base_audit, code))

    reponse = _corriger(client, code, {"champ": champ, "valeur": "pas un nombre"})
    assert reponse.status_code == 422, reponse.text

    canonique = _requete(
        base_audit,
        f"SELECT {colonne} FROM fiche_cotes WHERE id_fiche = "
        "(SELECT id_fiche FROM fiche WHERE code = %s) AND jeu = %s",
        (code, jeu),
    )[0][0]
    assert float(canonique) == pytest.approx(float(avant), abs=1e-3), "donnée canonique modifiée"
    assert _trace(base_audit, code, champ) == trace_avant, "trace modifiée"
    assert _revision(base_audit, code) == revision_avant, "révision avancée sans écriture"
    assert len(_journal(base_audit, code)) == journal_avant, "journal écrit sans écriture"


def test_a01_unite_incoherente_refusee(base_audit, client) -> None:
    """« 50 mm » dans une colonne en mètres : refus, pas conversion silencieuse."""
    client.base_audit = base_audit
    code = base_audit["genoa"]
    champ, colonne, _jeu, _avant = _champ_cote(base_audit, code)
    if colonne not in {"slu_m", "sle_m", "sf_m", "shw_m"}:
        pytest.skip(f"la première cote disponible ({colonne}) n'est pas en mètres")
    reponse = _corriger(client, code, {"champ": champ, "valeur": "50 mm"})
    assert reponse.status_code == 422, reponse.text
    assert "mm" in reponse.text


def test_a01_cible_non_supportee_est_dite_jamais_un_succes_muet(base_audit, client) -> None:
    """Identité de fiche : la trace est corrigée, et la réponse DIT que la donnée métier n'a pas suivi."""
    client.base_audit = base_audit
    code = base_audit["genoa"]
    id_fiche = _id_fiche(base_audit, code)
    lignes = _requete(
        base_audit,
        "SELECT champ FROM fiche_champ_extrait WHERE id_fiche = %s AND table_cible = 'fiche' "
        "AND colonne_cible IN ('code','client','bateau','commande','traitement','fichier_source') "
        "ORDER BY id_champ LIMIT 1",
        (id_fiche,),
    )
    assert lignes, "le dossier réel devrait porter au moins un champ d'identité"
    champ = str(lignes[0][0])
    avant = _trace(base_audit, code, champ)

    reponse = _corriger(client, code, {"champ": champ, "valeur": "valeur de revue"})
    assert reponse.status_code == 200, reponse.text
    propagation = reponse.json()["propagation"]
    assert propagation["statut"] == "non_supportee", propagation
    assert propagation.get("raison"), "une cible non supportée doit être expliquée"

    trace = _trace(base_audit, code, champ)
    assert trace["corrige"] is True and trace["valeur"] == "valeur de revue"
    # La donnée canonique d'identité n'a PAS été touchée.
    assert _requete(base_audit, "SELECT code FROM fiche WHERE code = %s", (code,))[0][0] == code
    assert avant["valeur"] != trace["valeur"] or avant["corrige"] is False
    journal = _journal(base_audit, code)
    assert "non_supportee" in str(journal[-1][2]), journal[-1]


def test_a01_divergence_historique_diagnostiquee_puis_reconciliee_explicitement(base_audit, client) -> None:
    """Divergence historique : diagnostiquée, jamais réparée en silence (exigence produit)."""
    from seamtech_search.fiches.corrections_canoniques import (
        diagnostiquer_divergences,
        reconcilier_fiche,
    )

    client.base_audit = base_audit
    code = base_audit["genoa"]
    champ, colonne, jeu, avant = _champ_cote(base_audit, code)
    nouvelle = float(avant) + 2.0
    reponse = _corriger(client, code, {"champ": champ, "valeur": str(nouvelle)})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["propagation"]["statut"] == "appliquee"

    # On force une divergence HISTORIQUE : la donnée canonique revient à
    # l'ancienne valeur, comme si la correction n'avait jamais atteint la donnée
    # (exactement l'état laissé par le défaut A01 avant correction).
    id_fiche = _id_fiche(base_audit, code)
    with base_audit["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                f"UPDATE fiche_cotes SET {colonne} = %s WHERE id_fiche = %s AND jeu = %s",
                (float(avant), id_fiche, jeu),
            )
        connexion.commit()

    divergences = diagnostiquer_divergences(base_audit["index"])
    miennes = [
        d
        for d in divergences
        if d["code"] == code and d["champ"] == champ and d["statut"] == "divergente"
    ]
    assert miennes, f"divergence non détectée : {divergences}"

    # Réconciliation SANS confirmation explicite : refusée.
    with pytest.raises(Exception):
        reconcilier_fiche(base_audit["index"], code)

    # …et avec confirmation : la donnée canonique rejoint la trace, et c'est JOURNALISÉ.
    resultat = reconcilier_fiche(base_audit["index"], code, confirmer=True)
    assert resultat["appliquees"], resultat
    valeur = _requete(
        base_audit,
        f"SELECT {colonne} FROM fiche_cotes WHERE id_fiche = %s AND jeu = %s",
        (id_fiche, jeu),
    )[0][0]
    assert float(valeur) == pytest.approx(nouvelle, abs=1e-3)
    restantes = [
        d
        for d in diagnostiquer_divergences(base_audit["index"])
        if d["code"] == code and d["champ"] == champ and d["statut"] == "divergente"
    ]
    assert restantes == [], restantes
    journal = _journal(base_audit, code)
    assert any(ligne[0] == "reconcilier" for ligne in journal), journal


# --------------------------------------------------------------------------- #
# A02 — ré-extraction, révision, écran périmé
# --------------------------------------------------------------------------- #


def test_a02_reextraction_avance_la_revision_et_invalide_un_ecran_perime(base_a02) -> None:
    """Une fiche a_valider ré-extraite change de contenu ET de révision."""
    from seamtech_search.fiches import depot

    code = base_a02["genoa"]  # fiche non corrigée : la ré-extraction est permise
    revision_avant = _revision(base_a02, code)

    # Même PDF, AUTRE chemin : la clé d'idempotence (chemin + empreinte) diffère,
    # donc le dossier est réellement ré-extraite et la fiche remplacée sur place.
    autre = Path(base_a02["racine_essai"]) / "rejeu-autre-chemin"
    autre.mkdir(parents=True, exist_ok=True)
    (autre / "fiche.pdf").write_bytes((RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf").read_bytes())
    resultat = depot.deposer_dossier(base_a02["index"], autre)
    assert resultat.get("statut") == "traite", resultat
    assert resultat.get("contenu_remplace") is True, resultat
    assert resultat.get("revision") == revision_avant + 1, resultat
    assert resultat.get("revision_avant") == revision_avant, resultat
    assert _revision(base_a02, code) == revision_avant + 1

    # L'écran resté sur l'ancienne révision ne peut PAS valider ce qu'il n'a pas relu.
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[Path(base_a02["racine_essai"])],
        database_path=Path(f"/tmp/unused-a02-{uuid.uuid4().hex[:6]}.db"),
        database_url=base_a02["url"],
    )
    client = TestClient(create_app(config))
    journal_avant = len(_journal(base_a02, code))

    refuse = client.post(
        f"/fiches/{code}/valider", json={"revision": revision_avant, "utilisateur": "poste-perime"}
    )
    assert refuse.status_code == 409, refuse.text
    assert _revision(base_a02, code) == revision_avant + 1, "la révision a bougé sur un refus"
    assert len(_journal(base_a02, code)) == journal_avant, "un refus ne doit rien journaliser"

    accepte = client.post(
        f"/fiches/{code}/valider",
        json={"revision": revision_avant + 1, "utilisateur": "poste-a-jour"},
    )
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["revision"] == revision_avant + 2
    assert _requete(base_a02, "SELECT statut FROM fiche WHERE code = %s", (code,))[0][0] == "valide"


def test_a02_reextraction_refusee_quand_un_humain_a_corrige(base_audit) -> None:
    """RG11 : une correction humaine n'est jamais écrasée par une ré-éxtraction."""
    from seamtech_search.fiches import depot

    code = base_audit["genoa"]
    id_fiche = _id_fiche(base_audit, code)
    assert _requete(
        base_audit,
        "SELECT COUNT(*) FROM fiche_champ_extrait WHERE id_fiche = %s AND corrige",
        (id_fiche,),
    )[0][0] >= 1, "ce test suppose que le test A01 a corrigé au moins un champ de cette fiche"

    revision_avant = _revision(base_audit, code)
    autre = Path(base_audit["racine_essai"]) / "rejeu-apres-correction"
    autre.mkdir(parents=True, exist_ok=True)
    (autre / "fiche.pdf").write_bytes((RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf").read_bytes())

    # Le dépôt REFUSE (RG11) : refus explicite, jamais une ré-écriture qui
    # effacerait la correction d'un humain.
    resultat = depot.deposer_dossier(base_audit["index"], autre)
    assert resultat.get("statut") == "echec", resultat
    assert "RG11" in str(resultat.get("raison")), resultat
    # La fiche est INTACTE : ni contenu, ni révision, ni corrections perdus.
    assert _revision(base_audit, code) == revision_avant
    assert _requete(
        base_audit,
        "SELECT COUNT(*) FROM fiche_champ_extrait WHERE id_fiche = %s AND corrige",
        (id_fiche,),
    )[0][0] >= 1
