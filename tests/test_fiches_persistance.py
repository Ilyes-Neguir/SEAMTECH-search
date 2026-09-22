"""Lot B — écriture transactionnelle en base (PostgreSQL réel, RG3/RG11).

Chaque test part d'une base jetable migrée (mêmes règles que la suite Lot A) :
les écritures du Lot B (fiche, cotes, matériaux, galons, jonctions, finitions,
options, renforts, champs extraits, mesures libres, anomalies) doivent être
idempotentes et ne JAMAIS écraser une fiche validée.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES, charger_gabarits, initialiser_gabarits
from seamtech_search.fiches.persistance import (
    ecrire_fiche,
    evaluer_verite,
    initialiser_verite_7792,
    verifier_non_regression,
)
from seamtech_search.indexer import SearchIndex

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def base_fiches() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable, migrations 006-009 + gabarits + vérité 7792."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"fiches_test_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()
    partie = DATABASE_URL.rsplit("/", 1)
    url_base = f"{partie[0]}/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-fiches-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    initialiser_verite_7792(index, PDF_7792)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base}
    finally:
        index.close()  # rend les connexions du pool avant de supprimer la base
        administrateur = psycopg2.connect(DATABASE_URL)
        administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with administrateur.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            administrateur.close()


@pytest.fixture(scope="module")
def fiche_7792() -> Any:
    return extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))


def _comptes(index: SearchIndex, code: str) -> dict[str, int]:
    """Nombre de lignes par table enfant pour la fiche code (non-régression RG11)."""
    requetes = {
        "fiche": "SELECT COUNT(*) FROM fiche WHERE code = %s",
        "cotes": "SELECT COUNT(*) FROM fiche_cotes c JOIN fiche f ON f.id_fiche = c.id_fiche WHERE f.code = %s",
        "materiaux": "SELECT COUNT(*) FROM fiche_materiau m JOIN fiche f ON f.id_fiche = m.id_fiche WHERE f.code = %s",
        "galons": "SELECT COUNT(*) FROM fiche_galon g JOIN fiche f ON f.id_fiche = g.id_fiche WHERE f.code = %s",
        "jonctions": "SELECT COUNT(*) FROM fiche_jonction j JOIN fiche f ON f.id_fiche = j.id_fiche WHERE f.code = %s",
        "finitions": "SELECT COUNT(*) FROM fiche_finition fi JOIN fiche f ON f.id_fiche = fi.id_fiche WHERE f.code = %s",
        "options": "SELECT COUNT(*) FROM fiche_option o JOIN fiche f ON f.id_fiche = o.id_fiche WHERE f.code = %s",
        "renforts": "SELECT COUNT(*) FROM fiche_renfort r JOIN fiche f ON f.id_fiche = r.id_fiche WHERE f.code = %s",
        "champs": "SELECT COUNT(*) FROM fiche_champ_extrait e JOIN fiche f ON f.id_fiche = e.id_fiche WHERE f.code = %s",
        "anomalies": "SELECT COUNT(*) FROM fiche_anomalie a JOIN fiche f ON f.id_fiche = a.id_fiche WHERE f.code = %s",
    }
    comptes: dict[str, int] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for nom, requete in requetes.items():
                cursor.execute(requete, (code,))
                comptes[nom] = int(cursor.fetchone()[0])
    return comptes


class TestEcritureTransactionnelle:
    def test_fiche_ecrite_en_statut_a_valider(self, base_fiches, fiche_7792) -> None:
        fiche = fiche_7792.model_copy(update={"code": "7792-SO-ETAT"})
        id_fiche, action = ecrire_fiche(base_fiches["index"], fiche)
        assert action == "creee" and id_fiche > 0
        index = base_fiches["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT statut, score_qualite, id_gabarit FROM fiche WHERE id_fiche = %s", (id_fiche,))
                statut, score, id_gabarit = cursor.fetchone()
        # RG3 : le statut d'arrivée est TOUJOURS a_valider — jamais valide.
        assert statut == "a_valider"
        assert score is not None and 0.0 <= float(score) <= 1.0
        assert id_gabarit is not None

    def test_toutes_les_tables_enfants_remplies(self, base_fiches, fiche_7792) -> None:
        ecrire_fiche(base_fiches["index"], fiche_7792)
        comptes = _comptes(base_fiches["index"], fiche_7792.code)
        assert comptes["fiche"] == 1
        # Document RÉEL : les deux jeux de cotes sont imprimés.
        assert comptes["cotes"] == 2  # jeux « dessin » + « finie »
        assert comptes["materiaux"] == 4  # épaisseurs 01-04 du bloc réel
        assert comptes["galons"] == 3  # guindant + chute + bordure
        assert comptes["jonctions"] == 4  # laizes + horizontale + verticale + surplus
        assert comptes["finitions"] == 3  # amure + écoute + drisse
        # Colonne centrale (emmagasineur, bout de manœuvre, chaussette, sac,
        # v-trim) + bloc velcro/retenue/anti-UV.
        assert comptes["options"] == 8
        assert comptes["renforts"] == 3  # note « 2 x œillets… + dacron + dacron »
        assert comptes["champs"] == len(fiche_7792.tous_les_champs())
        assert comptes["anomalies"] == 0

    def test_zones_et_version_gabarit_tracées(self, base_fiches, fiche_7792) -> None:
        ecrire_fiche(base_fiches["index"], fiche_7792)
        index = base_fiches["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT zone, version_gabarit, methode, confiance, page FROM fiche_champ_extrait "
                    "WHERE champ = 'fiche.code' LIMIT 1"
                )
                zone, version, methode, confiance, page = cursor.fetchone()
        assert zone is not None and zone["page"] == 0 and zone["x1"] > zone["x0"]
        assert version == 2  # gabarit réglé sur le document réel (Tâche 2)
        assert methode == "gabarit" and float(confiance) > 0.5 and page == 0

    def test_referentiels_resolus_ou_crees(self, base_fiches, fiche_7792) -> None:
        ecrire_fiche(base_fiches["index"], fiche_7792)
        index = base_fiches["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT c.nom, b.nom, b.taille, t.libelle, m.nom FROM fiche f "
                    "JOIN client c ON c.id_client = f.id_client "
                    "JOIN bateau b ON b.id_bateau = f.id_bateau "
                    "JOIN type_voile t ON t.id_type_voile = f.id_type_voile "
                    "LEFT JOIN fiche_materiau fm ON fm.id_fiche = f.id_fiche AND fm.niveau = 1 "
                    "LEFT JOIN materiau m ON m.id_materiau = fm.id_materiau "
                    "WHERE f.code = %s",
                    (fiche_7792.code,),
                )
                client, bateau, taille, type_voile, materiau = cursor.fetchone()
        assert (client, bateau, taille, type_voile) == ("Sailonet", "29er", "15'", "Spi Asymétrique")
        assert materiau == "Monofilm K903"


class TestIdempotenceRG11:
    def test_rejouer_ne_duplique_rien(self, base_fiches, fiche_7792) -> None:
        index = base_fiches["index"]
        fiche = fiche_7792.model_copy(update={"code": "7792-SO-REJEU"})
        _, premiere = ecrire_fiche(index, fiche)
        comptes_1 = _comptes(index, fiche.code)
        _, seconde = ecrire_fiche(index, fiche)
        comptes_2 = _comptes(index, fiche.code)
        assert premiere == "creee" and seconde == "remplacee"
        assert comptes_1 == comptes_2, "le rejeu a dupliqué des lignes (RG11)"
        assert comptes_1["fiche"] == 1

    def test_rejeu_re_lit_les_valeurs_a_identique(self, base_fiches, fiche_7792) -> None:
        index = base_fiches["index"]
        ecrire_fiche(index, fiche_7792)
        ecrire_fiche(index, fiche_7792)
        finie = next(c for c in fiche_7792.cotes if c.jeu == "finie")
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT slu_m, sf_m FROM fiche_cotes c JOIN fiche f ON f.id_fiche = c.id_fiche WHERE f.code = %s",
                    (fiche_7792.code,),
                )
                slu, sf = cursor.fetchone()
        assert (float(slu), float(sf)) == (finie.slu_m, finie.sf_m)

    def test_fiche_validee_jamais_ecrasee(self, base_fiches, fiche_7792) -> None:
        index = base_fiches["index"]
        id_fiche, _ = ecrire_fiche(index, fiche_7792)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("UPDATE fiche SET statut = 'valide', titre = 'VALIDÉE PAR UN HUMAIN' WHERE id_fiche = %s", (id_fiche,))
        avant = _comptes(index, fiche_7792.code)
        id_rejeu, action = ecrire_fiche(index, fiche_7792)
        apres = _comptes(index, fiche_7792.code)
        # RG11 : rejeu sur fiche validée → aucun changement, aucun doublon
        assert action == "conservee_validee" and id_rejeu == id_fiche
        assert avant == apres
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT statut, titre FROM fiche WHERE id_fiche = %s", (id_fiche,))
                statut, titre = cursor.fetchone()
        assert statut == "valide" and titre == "VALIDÉE PAR UN HUMAIN"

    def test_fiche_sans_code_refusee(self, base_fiches) -> None:
        from seamtech_search.fiches.modeles import FicheExtraite

        with pytest.raises(ValueError, match="sans code"):
            ecrire_fiche(base_fiches["index"], FicheExtraite(code=None))


class TestBancGabaritTest:
    def test_non_regression_7792_conforme(self, base_fiches, fiche_7792) -> None:
        resultat = verifier_non_regression(base_fiches["index"], fiche_7792)
        assert resultat["taux"] >= 0.90, resultat["ecarts"]
        assert resultat["conforme"]

    def test_gabarits_enregistres_et_actifs(self, base_fiches) -> None:
        codes = {g.code for g in charger_gabarits(base_fiches["index"])}
        assert {"FICHE_PORTANT_V1", "FICHE_GENOIS_V1"} <= codes

    def test_verite_7792_avec_empreinte_pdf(self, base_fiches) -> None:
        import hashlib

        index = base_fiches["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT empreinte_pdf, attendu FROM gabarit_test WHERE nom_fichier = 'fiche-7792-SO_ffab.pdf'"
                )
                empreinte, attendu = cursor.fetchone()
        assert empreinte == hashlib.sha256(PDF_7792.read_bytes()).hexdigest()
        assert attendu["fiche.code"] == "7792-SO"

    def test_evaluer_verite_direct(self, fiche_7792) -> None:
        taux, ecarts = evaluer_verite(fiche_7792, {"fiche.code": "7792-SO", "cotes.finie.slu_m": 6.6, "client": "Autre"})
        assert taux == round(2 / 3, 4)
        assert len(ecarts) == 1 and ecarts[0]["champ"] == "client"
