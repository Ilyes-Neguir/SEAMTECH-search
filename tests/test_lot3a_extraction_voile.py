"""Lot 3a — extraction PDF structurée d'une voile (migration 028).

Le contrat prouvé ici est celui du plan directeur v2, pas seulement un
module isolé :

* une fiche extraite par gabarit devient une extraction de voile normalisée
  et traçable (valeur brute, valeur canonique, méthode, confiance 0–1,
  page et zone) ;
* les longueurs explicites en mm/cm, les surfaces et les masses sont
  converties vers m / m² / kg sans jamais deviner une unité absente ;
* ``modele_bateau_brut`` reste le libellé lu, non résolu : aucun
  ``boat_model_id`` n'entre dans le Lot 3a (la résolution est le Lot 3b) ;
* les contrôles géométriques restent indépendants de la confiance ;
* l'écriture est idempotente dans ``sail_extractions`` et suit les
  transactions existantes d'écriture de fiche et de dépôt ;
* le rapport d'or CLI mesure séparément la vérité historique 7792 (74
  cibles) et les champs normalisés du Lot 3a, avec échec réel sous le seuil.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.extractions_voile import (
    VERSION_EXTRACTEUR_VOILE,
    construire_extraction_voile,
    enregistrer_extraction_voile,
    evaluer_extraction_or,
    extraire_voile_avec_fiche,
    extraire_voile_depuis_pdf,
    lier_extraction_voile_document_curseur,
    lire_extraction_voile_par_fiche,
)
from seamtech_search.fiches.modeles import (
    Anomalie,
    ChampExtrait,
    Cotes,
    FicheExtraite,
    Materiau,
    Zone,
)
from seamtech_search.fiches.verite_7792 import VERITE_7792
from seamtech_search.schema_metier import (
    MIGRATIONS_METIER,
    SQL_028_SAIL_EXTRACTIONS,
    TABLES_METIER,
    VERSION_SCHEMA_METIER,
)

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
PDF_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"
SCRIPT_RAPPORT_OR = RACINE / "scripts/rapport_extraction_or.py"

ATTENDU_7792_LOT3A: dict[str, object] = {
    "code_fiche": "7792-SO",
    "type_voile": "Spi Asymétrique",
    "gamme": "Medium Régate",
    "modele_bateau_brut": "29er (15')",
    "taille_bateau_brute": "15'",
    "dimensions.slu_m": 6.6,
    "dimensions.sle_m": 5.5,
    "dimensions.sf_m": 3.08,
    "dimensions.shw_m": 3.14,
    "dimensions.spa_m2": 15.71,
    "dimensions.tetiere_m": 0.03,
    "dimensions.poids_kg": 0.7,
    "materiaux.designations_contient": "Monofilm K903",
    "gabarit_code": "FICHE_PORTANT_V1",
    "gabarit_version": 2,
    "statut_extraction": "complete",
    "statut_plausibilite": "plausible",
}

ATTENDU_GENOIS_LOT3A: dict[str, object] = {
    "code_fiche": "0901-MM",
    "type_voile": "Génois",
    "modele_bateau_brut": "Sun 2000",
    "dimensions.slu_m": 8.2,
    "dimensions.sf_m": 3.9,
    "dimensions.spa_m2": 18.2,
    "materiaux.principal": "Dacron 260",
    "gabarit_code": "FICHE_GENOIS_V1",
    "gabarit_version": 1,
    "statut_extraction": "complete",
    "statut_plausibilite": "plausible",
}


@pytest.fixture(scope="module")
def extraction_7792():
    return extraire_voile_avec_fiche(PDF_7792)


@pytest.fixture(scope="module")
def extraction_genois():
    return extraire_voile_depuis_pdf(PDF_GENOIS)


def _zone() -> Zone:
    return Zone(x0=10.0, y0=20.0, x1=90.0, y1=32.0, page=2)


def _trace(
    champ: str,
    brut: str,
    normalise: str,
    *,
    colonne: str,
    confiance: float = 0.91,
) -> ChampExtrait:
    return ChampExtrait(
        champ=champ,
        valeur_brute=brut,
        valeur_normalisee=normalise,
        methode="gabarit",
        confiance=confiance,
        page=2,
        zone=_zone(),
        table_cible="fiche_cotes" if champ.startswith("cotes.") else "fiche",
        colonne_cible=colonne,
    )


def _fiche_unites_explicites() -> FicheExtraite:
    """Fiche synthétique dont certaines valeurs typées sont volontairement
    restées dans l'unité lue : la couche Lot 3a doit canoniser depuis la
    valeur brute explicite, sans toucher au libellé bateau brut."""

    cotes = Cotes(
        jeu="finie",
        slu_m=6600.0,
        sle_m=550.0,
        sf_m=3.08,
        shw_m=3140.0,
        spa_m2=157100.0,
        tetiere_cm=30.0,
        poids_kg=700.0,
        champs=[
            _trace("cotes.finie.slu_m", "6600 mm", "6600", colonne="slu_m"),
            _trace("cotes.finie.sle_m", "550 cm", "550", colonne="sle_m"),
            _trace("cotes.finie.sf_m", "3,08 m", "3.08", colonne="sf_m"),
            _trace("cotes.finie.shw_m", "3140 mm", "3140", colonne="shw_m"),
            _trace("cotes.finie.spa_m2", "157100 cm²", "157100", colonne="spa_m2"),
            _trace("cotes.finie.tetiere_cm", "30 mm", "30", colonne="tetiere_cm"),
            _trace("cotes.finie.poids_kg", "700 g", "700", colonne="poids_kg"),
        ],
    )
    materiau = Materiau(
        role="tissu_principal",
        designation="Dacron 260 g/m²",
        grammage_g_m2=260.0,
        champs=[
            ChampExtrait(
                champ="materiau.tissu_principal",
                valeur_brute="Dacron 260 g/m²",
                valeur_normalisee="Dacron 260 g/m²",
                methode="gabarit",
                confiance=0.93,
                page=2,
                zone=_zone(),
                table_cible="fiche_materiau",
                colonne_cible="designation",
            )
        ],
    )
    return FicheExtraite(
        code="LOT3A-UNITES",
        type_voile_libelle="Spi Asymétrique",
        gamme="Medium",
        bateau_nom="Bavaria",
        bateau_taille="45",
        cotes=[cotes],
        materiaux=[materiau],
        champs=[
            _trace(
                "fiche.bateau",
                "BAVARIA-45",
                "Bavaria | 45",
                colonne="bateau",
                confiance=0.94,
            ),
            _trace(
                "fiche.designation",
                "Spi Asymétrique Medium",
                "Spi Asymétrique | Medium",
                colonne="designation",
                confiance=0.92,
            ),
        ],
        gabarit_code="FICHE_PORTANT_V1",
        gabarit_version=2,
        fichier_pdf="/archive/confidentielle/client/fiche-lot3a.pdf",
    )


# ---------------------------------------------------------------------------
# Migration 028 — contrat de schéma
# ---------------------------------------------------------------------------


def test_migration_028_sail_extractions_sans_resolution_bateau() -> None:
    # La 028 reste figée ; depuis le Lot 5, la version du schéma est 030 et
    # la résolution vit dans les migrations suivantes (SQL_029+), pas dans
    # la 028 — le contrat 3a « aucune résolution bateau » reste prouvé sur
    # le SQL de la 028 lui-même.
    assert VERSION_SCHEMA_METIER == "030_evaluations_confiance"
    assert dict(MIGRATIONS_METIER)["028_sail_extractions"] == SQL_028_SAIL_EXTRACTIONS
    assert "sail_extractions" in TABLES_METIER
    assert len(TABLES_METIER) == 44

    sql = SQL_028_SAIL_EXTRACTIONS
    assert "CREATE TABLE IF NOT EXISTS sail_extractions" in sql
    for colonne in (
        "cle_idempotence",
        "modele_bateau_brut",
        "slu_m",
        "sle_m",
        "sf_m",
        "shw_m",
        "spa_m2",
        "tetiere_m",
        "poids_kg",
        "materiaux",
        "champs",
        "anomalies",
        "statut_extraction",
        "statut_plausibilite",
    ):
        assert colonne in sql
    assert "'complete', 'partielle', 'a_reprendre'" in sql
    assert "'plausible', 'a_verifier', 'implausible', 'indeterminee'" in sql
    assert "confiance_moyenne BETWEEN 0 AND 1" in sql
    assert "UNIQUE" in sql and "cle_idempotence" in sql
    # Frontière de lot explicite : 3a conserve le libellé brut, 3b résoudra.
    assert "boat_model_id" not in sql

    import pglast

    pglast.parse_sql(sql)


def test_sql_persistance_lot3a_grammaire_postgres() -> None:
    import pglast

    from seamtech_search import extractions_voile

    for nom in (
        "_SQL_UPSERT_EXTRACTION_VOILE",
        "_SQL_LIRE_EXTRACTION_PAR_FICHE",
        "_SQL_LIER_EXTRACTION_DOCUMENT",
        "_SQL_CLE_EXISTANTE_EXTRACTION_VOILE",
    ):
        sql = getattr(extractions_voile, nom)
        # pglast ne connaît pas les paramètres psycopg2 : on les remplace par
        # des littéraux SQL pour valider la grammaire, jamais les données.
        pglast.parse_sql(sql.replace("%s::jsonb", "'[]'::jsonb").replace("%s", "NULL"))


# ---------------------------------------------------------------------------
# Construction normalisée — unités, provenance, plausibilité
# ---------------------------------------------------------------------------


def test_construction_convertit_unites_explicites_et_preserve_le_brut() -> None:
    extraction = construire_extraction_voile(_fiche_unites_explicites())

    assert extraction.version_extracteur == VERSION_EXTRACTEUR_VOILE
    assert extraction.code_fiche == "LOT3A-UNITES"
    assert extraction.type_voile == "Spi Asymétrique"
    assert extraction.gamme == "Medium"
    assert extraction.modele_bateau_brut == "BAVARIA-45"  # jamais « Bavaria 45 »
    assert extraction.taille_bateau_brute == "45"
    # Le constructeur Lot 3a ne résout toujours rien : la liaison naît vide
    # et sera arbitrée par le Lot 3b (déterministe ou approbateur).
    assert extraction.boat_model_id is None
    assert extraction.statut_liaison == "non_resolu"

    assert extraction.slu_m == pytest.approx(6.6)
    assert extraction.sle_m == pytest.approx(5.5)
    assert extraction.sf_m == pytest.approx(3.08)
    assert extraction.shw_m == pytest.approx(3.14)
    assert extraction.spa_m2 == pytest.approx(15.71)
    assert extraction.tetiere_m == pytest.approx(0.03)
    assert extraction.poids_kg == pytest.approx(0.7)
    assert extraction.statut_extraction == "complete"
    assert extraction.statut_plausibilite == "plausible"
    assert extraction.materiaux[0].designation == "Dacron 260 g/m²"
    assert extraction.materiaux[0].grammage_g_m2 == pytest.approx(260.0)

    slu = extraction.champ("cotes.finie.slu_m")
    assert slu is not None
    assert slu.valeur_brute == "6600 mm"
    assert slu.unite_brute == "mm"
    assert slu.unite_canonique == "m"
    assert slu.valeur_canonique == pytest.approx(6.6)
    assert slu.methode == "gabarit"
    assert slu.confiance == pytest.approx(0.91)
    assert slu.page == 2
    assert slu.zone is not None and slu.zone.x1 > slu.zone.x0

    # La provenance publique ne doit pas fuiter le chemin d'archive complet.
    assert extraction.nom_fichier_source == "fiche-lot3a.pdf"
    assert "/archive/confidentielle" not in extraction.model_dump_json()


def test_construction_signale_manquants_et_implausibilite_sans_inventer() -> None:
    fiche = FicheExtraite(
        code="LOT3A-IMPLAUSIBLE",
        type_voile_libelle="Spi Asymétrique",
        cotes=[
            Cotes(
                jeu="finie",
                slu_m=3.0,
                sle_m=2.0,
                sf_m=4.0,
                spa_m2=100.0,
                champs=[
                    _trace("cotes.finie.slu_m", "3.0", "3.0", colonne="slu_m"),
                    _trace("cotes.finie.sle_m", "2.0", "2.0", colonne="sle_m"),
                    _trace("cotes.finie.sf_m", "4.0", "4.0", colonne="sf_m"),
                    _trace("cotes.finie.spa_m2", "100.0", "100.0", colonne="spa_m2"),
                ],
            )
        ],
        gabarit_code="FICHE_PORTANT_V1",
        gabarit_version=2,
        fichier_pdf="implausible.pdf",
    )

    extraction = construire_extraction_voile(fiche)

    assert extraction.modele_bateau_brut is None
    assert extraction.materiaux == []
    assert extraction.statut_extraction == "partielle"
    assert extraction.statut_plausibilite == "implausible"
    codes = {anomalie.code for anomalie in extraction.anomalies}
    assert "modele_bateau_manquant" in codes
    assert "materiau_manquant" in codes
    assert codes & {"cotes_incoherentes", "surface_incoherente"}

    bateau = extraction.champ("fiche.bateau")
    assert bateau is not None
    assert bateau.etat == "manquant"
    assert bateau.confiance is None  # aucun 0 déguisé en mesure


def test_gabarit_inconnu_reste_a_reprendre_et_preserve_les_mesures_libres() -> None:
    libre = ChampExtrait(
        champ="libre.guindant",
        valeur_brute="Guindant : 8,20",
        valeur_normalisee="8.2",
        methode="gabarit",
        confiance=0.6,
        page=0,
        zone=_zone(),
    )
    fiche = FicheExtraite(
        mesures_libres=[libre],
        anomalies=[
            Anomalie(
                code="gabarit_inconnu",
                gravite="forte",
                message="Aucun gabarit ne reconnaît ce PDF.",
            )
        ],
        fichier_pdf="inconnu.pdf",
    )

    extraction = construire_extraction_voile(fiche)

    assert extraction.statut_extraction == "a_reprendre"
    assert extraction.statut_plausibilite == "indeterminee"
    assert extraction.methode_principale == "gabarit_inconnu"
    assert extraction.type_voile is None
    assert extraction.modele_bateau_brut is None
    assert extraction.slu_m is None, "une mesure libre ne devient pas une cote structurée"
    trace = extraction.champ("libre.guindant")
    assert trace is not None and trace.valeur_brute == "Guindant : 8,20"


def _fiche_simple_lot3a(**remplacements: Any) -> FicheExtraite:
    fiche = _fiche_unites_explicites()
    for nom, valeur in remplacements.items():
        setattr(fiche, nom, valeur)
    return fiche


def test_unite_absente_hors_plage_n_est_jamais_rescalee() -> None:
    fiche = _fiche_simple_lot3a()
    fiche.cotes = [
        Cotes(
            jeu="finie",
            slu_m=6600.0,
            sf_m=3080.0,
            champs=[
                _trace("cotes.finie.slu_m", "6600", "6600", colonne="slu_m"),
                _trace("cotes.finie.sf_m", "3080", "3080", colonne="sf_m"),
            ],
        )
    ]

    extraction = construire_extraction_voile(fiche)

    assert extraction.slu_m == pytest.approx(6600.0), "6600 sans unité ne devient pas 6,6 m par devinette"
    assert extraction.sf_m == pytest.approx(3080.0)
    assert extraction.statut_plausibilite == "implausible"
    assert "unite_suspecte" in {anomalie.code for anomalie in extraction.anomalies}


def test_valeur_negative_et_unite_incompatible_ne_cassent_pas_extraction() -> None:
    fiche = _fiche_simple_lot3a()
    fiche.cotes = [
        Cotes(
            jeu="finie",
            slu_m=-3.0,
            sle_m=None,
            sf_m=2.0,
            champs=[
                _trace("cotes.finie.slu_m", "-3 m", "-3", colonne="slu_m"),
                _trace("cotes.finie.sle_m", "5.5 kg", "5.5", colonne="sle_m"),
                _trace("cotes.finie.sf_m", "2 m", "2", colonne="sf_m"),
            ],
        )
    ]

    extraction = construire_extraction_voile(fiche)

    assert extraction.slu_m is None
    assert extraction.sle_m is None, "des kilogrammes ne sont pas une chute en mètres"
    assert extraction.sf_m == pytest.approx(2.0)
    assert extraction.statut_plausibilite == "implausible"
    codes = {anomalie.code for anomalie in extraction.anomalies}
    assert {"cote_hors_plage", "unite_incompatible"} <= codes
    slu = extraction.champ("cotes.finie.slu_m")
    assert slu is not None and slu.valeur_canonique == pytest.approx(-3.0)


def test_designation_inconnue_et_taille_textuelle_sans_trace() -> None:
    fiche = _fiche_simple_lot3a(
        type_voile_libelle=None,
        gamme="Voile spéciale X",
        bateau_nom="Bavaria",
        bateau_taille="Cruiser",
    )
    fiche.champs = [
        _trace(
            "fiche.designation",
            "Voile spéciale X",
            "Voile spéciale X",
            colonne="designation",
        )
    ]

    extraction = construire_extraction_voile(fiche)

    assert extraction.type_voile is None
    assert extraction.modele_bateau_brut == "Bavaria Cruiser"
    assert extraction.statut_extraction == "partielle"
    assert "type_voile_manquant" in {anomalie.code for anomalie in extraction.anomalies}


# ---------------------------------------------------------------------------
# PDF réels/synthétiques déjà versionnés + évaluateur d'or
# ---------------------------------------------------------------------------


def test_pdf_7792_reel_extraction_normalisee_et_or_74_cibles(extraction_7792) -> None:
    fiche, extraction = extraction_7792

    empreinte = hashlib.sha256(PDF_7792.read_bytes()).hexdigest()
    assert extraction.empreinte_sha256 == empreinte
    assert extraction.nom_fichier_source == PDF_7792.name
    assert extraction.code_fiche == "7792-SO"
    assert extraction.modele_bateau_brut == "29er (15')"
    assert extraction.taille_bateau_brute == "15'"
    assert extraction.type_voile == "Spi Asymétrique"
    assert extraction.gamme == "Medium Régate"
    assert extraction.slu_m == pytest.approx(6.6)
    assert extraction.sle_m == pytest.approx(5.5)
    assert extraction.sf_m == pytest.approx(3.08)
    assert extraction.shw_m == pytest.approx(3.14)
    assert extraction.spa_m2 == pytest.approx(15.71)
    assert extraction.tetiere_m == pytest.approx(0.03)  # jeu dessin : 3 cm
    assert extraction.poids_kg == pytest.approx(0.7)
    assert any(m.designation == "Monofilm K903" for m in extraction.materiaux)
    assert extraction.statut_extraction == "complete"
    assert extraction.statut_plausibilite == "plausible"

    slu = extraction.champ("cotes.finie.slu_m")
    assert slu is not None
    assert slu.valeur_brute == "6,60 m"
    assert slu.methode == "gabarit"
    assert 0.0 <= float(slu.confiance or 0.0) <= 1.0
    assert slu.page == 0 and slu.zone is not None

    rapport_lot3a = evaluer_extraction_or(extraction, ATTENDU_7792_LOT3A)
    assert rapport_lot3a["total"] == len(ATTENDU_7792_LOT3A)
    assert rapport_lot3a["corrects"] == len(ATTENDU_7792_LOT3A)
    assert rapport_lot3a["taux"] == pytest.approx(1.0)

    rapport_fiche = evaluer_extraction_or(extraction, VERITE_7792, fiche=fiche)
    assert rapport_fiche["total"] == 74
    assert rapport_fiche["corrects"] == 74
    assert rapport_fiche["taux"] == pytest.approx(1.0)


def test_pdf_genois_synthetique_extraction_normalisee(extraction_genois) -> None:
    extraction = extraction_genois

    assert extraction.empreinte_sha256 == hashlib.sha256(PDF_GENOIS.read_bytes()).hexdigest()
    assert extraction.code_fiche == "0901-MM"
    assert extraction.modele_bateau_brut == "Sun 2000"
    assert extraction.type_voile == "Génois"
    assert extraction.slu_m == pytest.approx(8.2)
    assert extraction.sf_m == pytest.approx(3.9)
    assert extraction.spa_m2 == pytest.approx(18.2)
    assert extraction.materiaux[0].designation == "Dacron 260"

    rapport = evaluer_extraction_or(extraction, ATTENDU_GENOIS_LOT3A)
    assert rapport["taux"] == pytest.approx(1.0), rapport["details"]


def test_evaluateur_or_ne_simule_jamais_un_succes(extraction_genois) -> None:
    rapport = evaluer_extraction_or(
        extraction_genois,
        {
            "modele_bateau_brut": "Sun 2000",
            "dimensions.slu_m": 999.0,
            "dimensions.sle_m": 7.0,
        },
    )

    assert rapport["total"] == 3
    assert rapport["corrects"] == 1
    assert rapport["taux"] == pytest.approx(1 / 3)
    details = {detail["champ"]: detail for detail in rapport["details"]}
    assert details["modele_bateau_brut"]["correct"] is True
    assert details["dimensions.slu_m"]["correct"] is False
    assert details["dimensions.sle_m"]["correct"] is False
    assert details["dimensions.sle_m"]["obtenu"] is None


# ---------------------------------------------------------------------------
# Rapport CLI d'or
# ---------------------------------------------------------------------------


def _executer_rapport_or(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_RAPPORT_OR), *arguments],
        cwd=RACINE,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_cli_rapport_or_par_defaut_json_100_pourcent() -> None:
    resultat = _executer_rapport_or("--format", "json")

    assert resultat.returncode == 0, resultat.stderr
    rapport = json.loads(resultat.stdout)
    assert rapport["version_extracteur"] == VERSION_EXTRACTEUR_VOILE
    assert rapport["seuil"] == pytest.approx(1.0)
    assert rapport["conforme"] is True
    assert rapport["resume"]["taux"] == pytest.approx(1.0)

    echantillons = {echantillon["nom"]: echantillon for echantillon in rapport["echantillons"]}
    assert {"7792-SO", "GENOIS-SYNTHETIQUE"} <= set(echantillons)
    assert echantillons["7792-SO"]["evaluation_lot3a"]["taux"] == pytest.approx(1.0)
    assert echantillons["7792-SO"]["evaluation_fiche"]["total"] == 74
    assert echantillons["7792-SO"]["evaluation_fiche"]["taux"] == pytest.approx(1.0)
    assert echantillons["GENOIS-SYNTHETIQUE"]["evaluation_lot3a"]["taux"] == pytest.approx(1.0)
    assert "/" not in echantillons["7792-SO"]["nom_fichier"]


def test_cli_rapport_or_pdf_sans_attendu_ne_fabrique_pas_de_taux() -> None:
    resultat = _executer_rapport_or("--pdf", str(PDF_GENOIS), "--format", "json")

    assert resultat.returncode == 0, resultat.stderr
    rapport = json.loads(resultat.stdout)
    echantillon = rapport["echantillons"][0]
    assert echantillon["evaluation_lot3a"] is None
    assert echantillon["extraction"]["dimensions"]["slu_m"] == pytest.approx(8.2)
    assert rapport["resume"]["total_champs"] == 0
    assert rapport["resume"]["taux"] is None


def test_cli_rapport_or_manifeste_faux_echoue_sous_le_seuil(tmp_path: Path) -> None:
    manifeste = tmp_path / "manifeste-or.json"
    manifeste.write_text(
        json.dumps(
            {
                "echantillons": [
                    {
                        "nom": "GENOIS-FAUX",
                        "pdf": str(PDF_GENOIS),
                        "attendu": {"dimensions.slu_m": 999.0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    resultat = _executer_rapport_or("--manifeste", str(manifeste), "--format", "json")

    assert resultat.returncode == 1
    rapport = json.loads(resultat.stdout)
    assert rapport["conforme"] is False
    assert rapport["resume"]["taux"] == pytest.approx(0.0)
    detail = rapport["echantillons"][0]["evaluation_lot3a"]["details"][0]
    assert detail["champ"] == "dimensions.slu_m"
    assert detail["correct"] is False


# ---------------------------------------------------------------------------
# PostgreSQL réel — persistance, idempotence, fiche et dépôt
# ---------------------------------------------------------------------------


def _lire_ligne_extraction(index: Any, id_fiche: int) -> tuple[Any, ...]:  # noqa: ANN401
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT id_extraction, id_document, code_fiche, modele_bateau_brut, "
                "type_voile, slu_m, sf_m, materiaux, champs, anomalies, "
                "statut_extraction, statut_plausibilite, empreinte_sha256 "
                "FROM sail_extractions WHERE id_fiche = %s",
                (id_fiche,),
            )
            lignes = curseur.fetchall()
    assert len(lignes) == 1
    return lignes[0]


@pytest.mark.postgres
def test_postgres_ecrire_fiche_cree_une_extraction_idempotente(
    base_recherche: dict[str, Any],
) -> None:
    from seamtech_search.fiches.persistance import ecrire_fiche

    index = base_recherche["index"]
    fiche = _fiche_unites_explicites()
    # La table historique fiche_cotes borne spa_m2 à NUMERIC(8,3) : la
    # valeur typée de ce test d'intégration tient dans cette borne, tandis
    # que la valeur brute explicite (157100 cm²) prouve la canonisation
    # Lot 3a à 15,71 m². Le cas 157100 typé reste couvert en pur ci-dessus.
    fiche.cotes[0].spa_m2 = 15710.0

    id_fiche, action = ecrire_fiche(index, fiche)
    assert action == "creee"
    premiere = _lire_ligne_extraction(index, id_fiche)
    assert premiere[2] == "LOT3A-UNITES"
    assert premiere[3] == "BAVARIA-45"
    assert premiere[4] == "Spi Asymétrique"
    assert float(premiere[5]) == pytest.approx(6.6)
    assert float(premiere[6]) == pytest.approx(3.08)
    assert premiere[7][0]["designation"] == "Dacron 260 g/m²"
    slu = next(champ for champ in premiere[8] if champ["champ"] == "cotes.finie.slu_m")
    assert slu["unite_brute"] == "mm"
    assert float(slu["valeur_canonique"]) == pytest.approx(6.6)
    assert premiere[10] == "complete" and premiere[11] == "plausible"

    relue = lire_extraction_voile_par_fiche(index, id_fiche)
    assert relue is not None
    assert relue.id_extraction == int(premiere[0])
    assert relue.modele_bateau_brut == "BAVARIA-45"

    id_rejeu, _ = ecrire_fiche(index, fiche)
    assert id_rejeu == id_fiche
    seconde = _lire_ligne_extraction(index, id_fiche)
    assert int(seconde[0]) == int(premiere[0]), "le rejeu ne doit pas dupliquer"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sail_extractions' AND column_name = 'boat_model_id'"
            )
            # Au Lot 3a cette colonne ne devait pas exister ; le Lot 3b l'a
            # ajoutée (migration 029). La résolution qu'elle porte est
            # prouvée par tests/test_lot3b_liaison_voile.py.
            assert curseur.fetchone() is not None


@pytest.mark.postgres
def test_postgres_enregistrement_autonome_et_liaison_document(
    base_recherche: dict[str, Any],
) -> None:
    index = base_recherche["index"]
    fiche, extraction = extraire_voile_avec_fiche(PDF_GENOIS)

    id_extraction = enregistrer_extraction_voile(index, extraction)
    assert enregistrer_extraction_voile(index, extraction) == id_extraction

    extraction_maj = extraction.model_copy(update={"sf_m": 4.0})
    assert enregistrer_extraction_voile(index, extraction_maj) == id_extraction

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_fiche FROM fiche ORDER BY id_fiche LIMIT 1")
            id_fiche = int(curseur.fetchone()[0])
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, content, search_vector, id_fiche, role) "
                "VALUES (%s, %s, %s, '/lot3a', '.pdf', 1, 0, FALSE, '', ''::tsvector, %s, 'fiche_pdf') "
                "RETURNING id",
                ("/lot3a/fiche-genois.pdf", "/lot3a/fiche-genois.pdf", PDF_GENOIS.name, id_fiche),
            )
            id_document = int(curseur.fetchone()[0])
            curseur.execute(
                "UPDATE sail_extractions SET id_fiche = %s WHERE id_extraction = %s",
                (id_fiche, id_extraction),
            )
            assert lier_extraction_voile_document_curseur(
                curseur,
                id_fiche=id_fiche,
                id_document=id_document,
                empreinte_sha256=extraction.empreinte_sha256,
                nom_fichier_source=PDF_GENOIS.name,
            ) == 1
            curseur.execute(
                "SELECT id_document, sf_m, empreinte_sha256 FROM sail_extractions WHERE id_extraction = %s",
                (id_extraction,),
            )
            ligne = curseur.fetchone()

    assert int(ligne[0]) == id_document
    assert float(ligne[1]) == pytest.approx(4.0)
    assert ligne[2] == extraction.empreinte_sha256
    _ = fiche  # l'extraction autonome ne crée aucune fiche implicite


@pytest.mark.postgres
def test_postgres_depot_lie_extraction_au_document_source(
    base_recherche: dict[str, Any], tmp_path: Path
) -> None:
    from seamtech_search.fiches.depot import deposer_dossier
    from seamtech_search.fiches.gabarits import initialiser_gabarits

    index = base_recherche["index"]
    initialiser_gabarits(index)
    dossier = tmp_path / "CLIENT-GENOA-LOT3A"
    dossier.mkdir()
    shutil.copy2(PDF_GENOIS, dossier / PDF_GENOIS.name)

    resultat = deposer_dossier(index, dossier)

    assert resultat["statut"] == "traite", resultat
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT e.id_document, e.modele_bateau_brut, e.slu_m, e.sf_m, e.empreinte_sha256, d.name "
                "FROM sail_extractions e JOIN fiche f ON f.id_fiche = e.id_fiche "
                "JOIN documents d ON d.id = e.id_document WHERE f.code = '0901-MM'"
            )
            lignes = curseur.fetchall()

    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne[1] == "Sun 2000"
    assert float(ligne[2]) == pytest.approx(8.2)
    assert float(ligne[3]) == pytest.approx(3.9)
    assert ligne[4] == hashlib.sha256(PDF_GENOIS.read_bytes()).hexdigest()
    assert ligne[5] == PDF_GENOIS.name
