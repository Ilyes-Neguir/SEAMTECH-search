"""Lot 3b — résolution du modèle de bateau, corrections et apprentissage local.

Le Lot 3a a livré des extractions de voiles dont le modèle de bateau restait
BRUT et non résolu. Le Lot 3b le résout vers le référentiel canonique du
Lot 2, sans jamais fusionner en silence (« 45 » ≠ « 45.1 ») :

* liaison automatique UNIQUEMENT sur clé exacte, alias exact, ou bateau de la
  fiche déjà lié dont le libellé correspond exactement ;
* tout candidat préfixe/flou rend l'extraction ``ambigu`` : un approbateur
  décide (rattacher, ou créer un nouveau modèle) — la machine ne tranche pas ;
* la décision humaine APPREND un alias (source ``apprentissage_lot3b``) :
  la prochaine extraction portant le même libellé brut se lie toute seule ;
* une liaison humaine n'est jamais écrasée par une ré-extraction (esprit RG11) ;
* l'approbateur corrige les valeurs de l'extraction : historique complet
  (avant/après, auteur, motif), plausibilité RG16 recalculée, propagation à la
  fiche pour les cotes qui y ont une trace (révision obligatoire), et motif de
  correction appris localement (suggéré ensuite, jamais appliqué en silence).

Tout est local : PostgreSQL réel, référentiel du Lot 2, aucune API externe.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from seamtech_search.comptes import creer_utilisateur, ouvrir_session
from seamtech_search.config import AppConfig
from seamtech_search.extractions_voile import (
    ChampExtractionVoile,
    ExtractionVoile,
    enregistrer_extraction_voile,
)
from seamtech_search.liaison_voile import (
    CHAMPS_CORRIGEABLES,
    METHODES_LIAISON,
    STATUTS_LIAISON,
    CorrectionRefusee,
    corriger_extraction,
    detail_extraction,
    lier_extraction,
    lister_extractions,
    resoudre_extractions,
    valider_valeur_correction,
)
from seamtech_search.referentiel_bateaux import assurer_alias, assurer_modele
from seamtech_search.schema_metier import (
    MIGRATIONS_METIER,
    SQL_028_SAIL_EXTRACTIONS,
    SQL_029_EXTRACTION_LIAISON_CORRECTIONS,
    TABLES_METIER,
    VERSION_SCHEMA_METIER,
)

JETON_SERVICE = "jeton-service-lot3b"
CODE_FICHE = "1001-GV-006"
CHAMP_COTE = "cotes.finie.slu_m"


# ---------------------------------------------------------------------------
# Contrats purs (schéma, vocabulaire, validation)
# ---------------------------------------------------------------------------


def test_migration_029_version_tables_et_garde_fous() -> None:
    # Depuis le Lot 7, la version courante est 031 ; la 029 reste figée.
    assert VERSION_SCHEMA_METIER == "031_accessoires"
    assert MIGRATIONS_METIER[-1][0] == "031_accessoires"
    assert MIGRATIONS_METIER[-2][0] == "030_evaluations_confiance"
    assert MIGRATIONS_METIER[-3][0] == "029_extraction_liaison_corrections"
    sql = SQL_029_EXTRACTION_LIAISON_CORRECTIONS
    for fragment in (
        "ADD COLUMN IF NOT EXISTS boat_model_id BIGINT REFERENCES boat_models(id_modele)",
        "ADD COLUMN IF NOT EXISTS statut_liaison TEXT NOT NULL DEFAULT 'non_resolu'",
        "'non_resolu', 'lie_automatique', 'lie_humain', 'ambigu', 'sans_modele'",
        "ADD COLUMN IF NOT EXISTS methode_liaison TEXT",
        "'cle_exacte', 'alias_exact', 'fiche_bateau', 'humain', 'humain_nouveau_modele'",
        "ADD COLUMN IF NOT EXISTS lie_par BIGINT REFERENCES utilisateur(id_utilisateur)",
        "sail_extractions_liaison_coherente",
        "CREATE TABLE IF NOT EXISTS extraction_corrections",
        "id_extraction         BIGINT NOT NULL REFERENCES sail_extractions(id_extraction)",
        "valeur_avant          JSONB",
        "valeur_apres          JSONB",
        "CREATE TABLE IF NOT EXISTS extraction_motifs_appris",
        "occurrences           INTEGER NOT NULL DEFAULT 1 CHECK (occurrences >= 1)",
        "UNIQUE (gabarit_code, champ, valeur_brute_cle, valeur_corrigee)",
    ):
        assert fragment in sql, fragment
    # La migration 028 reste figée : le Lot 3a ne résolvait toujours rien.
    assert "boat_model_id" not in SQL_028_SAIL_EXTRACTIONS
    # La 030 (Lot 5) puis la 031 (Lot 7) ont suivi les deux tables du
    # Lot 3b — l'ordre historique du schéma reste prouvé.
    assert len(TABLES_METIER) == 46
    assert TABLES_METIER[-5:] == (
        "extraction_corrections",
        "extraction_motifs_appris",
        "evaluations_confiance",
        "accessory_extractions",
        "accessory_corrections",
    )


def test_modele_extraction_champs_liaison_par_defaut() -> None:
    extraction = ExtractionVoile()
    assert extraction.boat_model_id is None
    assert extraction.statut_liaison == "non_resolu"
    assert extraction.methode_liaison is None
    assert extraction.lie_le is None
    assert extraction.lie_par is None
    serialisee = extraction.model_dump(mode="json")
    for champ in ("boat_model_id", "statut_liaison", "methode_liaison", "lie_le", "lie_par"):
        assert champ in serialisee
    with pytest.raises(Exception):  # noqa: B017 - le vocabulaire est fermé (Literal)
        ExtractionVoile(statut_liaison="lie_par_magie")  # type: ignore[arg-type]


def test_vocabulaire_liaison_et_champs_corrigables() -> None:
    assert STATUTS_LIAISON == ("non_resolu", "lie_automatique", "lie_humain", "ambigu", "sans_modele")
    assert METHODES_LIAISON == (
        "cle_exacte",
        "alias_exact",
        "fiche_bateau",
        "humain",
        "humain_nouveau_modele",
    )
    assert CHAMPS_CORRIGEABLES == (
        "type_voile",
        "gamme",
        "modele_bateau_brut",
        "taille_bateau_brute",
        "slu_m",
        "sle_m",
        "sf_m",
        "shw_m",
        "spa_m2",
        "tetiere_m",
        "poids_kg",
    )


def test_validation_valeur_correction() -> None:
    assert valider_valeur_correction("slu_m", "6,60") == 6.6
    assert valider_valeur_correction("slu_m", 6.6) == 6.6
    assert valider_valeur_correction("tetiere_m", "0.03") == 0.03
    assert valider_valeur_correction("type_voile", "  Spi Asymétrique ") == "Spi Asymétrique"
    assert valider_valeur_correction("modele_bateau_brut", "Bavaria 45") == "Bavaria 45"
    assert valider_valeur_correction("gamme", None) is None
    for champ, valeur in (
        ("slu_m", "abc"),
        ("slu_m", 0),
        ("slu_m", -2.5),
        ("slu_m", "6,6,6"),
        ("type_voile", "   "),
        ("modele_bateau_brut", ""),
        ("champ_inconnu", "x"),
    ):
        with pytest.raises(CorrectionRefusee):
            valider_valeur_correction(champ, valeur)


# ---------------------------------------------------------------------------
# Fixture PostgreSQL réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def monde_lot3b(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]

    comptes: dict[str, dict[str, Any]] = {}
    for cle, identifiant, role in (
        ("lecture", "lot3b-lecture", "lecture"),
        ("utilisateur", "lot3b-utilisateur", "utilisateur"),
        ("approbateur", "lot3b-approbateur", "approbateur"),
    ):
        compte = creer_utilisateur(
            index,
            identifiant,
            f"Compte {identifiant}",
            f"MotDePasse-{identifiant}-2026",
            role=role,
            doit_changer_mot_de_passe=False,
        )
        session = ouvrir_session(index, int(compte["id_utilisateur"]))
        comptes[cle] = {**compte, **session}

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            bavaria_45 = assurer_modele(curseur, "Bavaria 45")
            bavaria_451 = assurer_modele(curseur, "Bavaria 45.1")
            sun_2000 = assurer_modele(curseur, "Sun 2000")
            assert bavaria_45 is not None and bavaria_451 is not None and sun_2000 is not None
            modeles = {
                "bavaria_45": bavaria_45.id_modele,
                "bavaria_451": bavaria_451.id_modele,
                "sun_2000": sun_2000.id_modele,
            }

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused-lot3b.db",
        database_url=base_recherche["url"],
        auth_token=JETON_SERVICE,
        storage_backend="local",
        web_worker_enabled=False,
    )
    return {
        **base_recherche,
        "client": TestClient(create_app(config)),
        "comptes": comptes,
        "modeles": modeles,
        "compteur": 0,
    }


def _entetes(compte: dict[str, Any]) -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": str(compte["identifiant"]),
        "X-SEAMTECH-ROLE": str(compte["role"]),
        "X-SEAMTECH-SESSION": str(compte["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(compte["jeton"]),
    }


def _enregistrer(monde: dict[str, Any], brut: str | None, **remplacements: Any) -> int:  # noqa: ANN401
    monde["compteur"] += 1
    numero = monde["compteur"]
    donnees: dict[str, Any] = {
        "code_fiche": f"LOT3B-{numero:03d}",
        "nom_fichier_source": f"fiche-lot3b-{numero}.pdf",
        "empreinte_sha256": hashlib.sha256(f"lot3b-{numero}-{brut}".encode()).hexdigest(),
        "gabarit_code": "FICHE_PORTANT_V1",
        "gabarit_version": 2,
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "gamme": "Medium Régate",
        "modele_bateau_brut": brut,
        "slu_m": 6.6,
        "sle_m": 5.5,
        "sf_m": 3.08,
        "shw_m": 3.14,
        "spa_m2": 15.71,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
    }
    donnees.update(remplacements)
    return enregistrer_extraction_voile(monde["index"], ExtractionVoile(**donnees))


def _liaison(monde: dict[str, Any], id_extraction: int) -> tuple[Any, ...]:  # noqa: ANN401
    with monde["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT boat_model_id, statut_liaison, methode_liaison, lie_par "
                "FROM sail_extractions WHERE id_extraction = %s",
                (id_extraction,),
            )
            ligne = curseur.fetchone()
    assert ligne is not None
    return tuple(ligne)


def _valeur(monde: dict[str, Any], sql: str, parametres: tuple[Any, ...]) -> Any:  # noqa: ANN401
    with monde["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(sql, parametres)
            ligne = curseur.fetchone()
    assert ligne is not None
    return ligne[0]


# ---------------------------------------------------------------------------
# Résolution automatique — exacte ou rien
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_liaison_automatique_cle_exacte_jamais_prefixe_ni_flou(
    monde_lot3b: dict[str, Any],
) -> None:
    monde_lot3b_index = monde_lot3b["index"]
    bavaria_45 = monde_lot3b["modeles"]["bavaria_45"]

    id_ex = _enregistrer(monde_lot3b, "bavaria  45")
    lien = _liaison(monde_lot3b, id_ex)
    assert lien == (bavaria_45, "lie_automatique", "cle_exacte", None)

    # Préfixe commun à 45 et 45.1 : ambigu, JAMAIS lié en silence.
    id_prefixe = _enregistrer(monde_lot3b, "Bavaria 4")
    lien_prefixe = _liaison(monde_lot3b, id_prefixe)
    assert lien_prefixe[0] is None and lien_prefixe[1] == "ambigu"
    detail = detail_extraction(monde_lot3b_index, id_prefixe)
    candidats = {int(c["id_modele"]) for c in detail["candidats"]}
    assert {bavaria_45, monde_lot3b["modeles"]["bavaria_451"]} <= candidats

    # Faute de frappe : candidat flou → ambigu, pas de liaison devinée.
    id_flou = _enregistrer(monde_lot3b, "Bavria 45")
    lien_flou = _liaison(monde_lot3b, id_flou)
    assert lien_flou[0] is None and lien_flou[1] == "ambigu"

    # Inconnu du référentiel : non résolu, à traiter par un humain.
    id_inconnu = _enregistrer(monde_lot3b, "Zodiac Pro 900")
    assert _liaison(monde_lot3b, id_inconnu)[1] == "non_resolu"

    # Aucun libellé lu : sans modèle, explicitement.
    id_vide = _enregistrer(monde_lot3b, None)
    assert _liaison(monde_lot3b, id_vide)[1] == "sans_modele"


@pytest.mark.postgres
def test_liaison_via_bateau_de_la_fiche_et_conflit_ambigu(
    monde_lot3b: dict[str, Any],
) -> None:
    index = monde_lot3b["index"]
    id_fiche = int(monde_lot3b["fiches"][CODE_FICHE])
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            modele = assurer_modele(curseur, "Bénéteau First 27 SE")
            assert modele is not None
            curseur.execute(
                "INSERT INTO bateau (nom, taille, boat_model_id) VALUES ('First 27', 'SE', %s) "
                "RETURNING id_bateau",
                (modele.id_modele,),
            )
            id_bateau = int(curseur.fetchone()[0])
            curseur.execute("UPDATE fiche SET id_bateau = %s WHERE id_fiche = %s", (id_bateau, id_fiche))

    # « First 27 SE » n'est ni la clé ni un alias du modèle canonique : seule la
    # fiche (nom + taille du bateau déjà lié) permet la liaison automatique.
    id_ex = _enregistrer(monde_lot3b, "First 27 SE", id_fiche=id_fiche, code_fiche=CODE_FICHE)
    lien = _liaison(monde_lot3b, id_ex)
    assert lien[1:] == ("lie_automatique", "fiche_bateau", None)
    id_modele_fiche = int(lien[0])

    # Un modèle concurrent portant EXACTEMENT ce libellé naît ensuite : la
    # clé exacte et la fiche désaccordent → ambigu, les deux candidats montrés.
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            concurrent = assurer_modele(curseur, "First 27 SE")
            assert concurrent is not None
            id_concurrent = concurrent.id_modele
    id_conflit = _enregistrer(monde_lot3b, "First 27 SE", id_fiche=id_fiche, code_fiche=CODE_FICHE)
    lien_conflit = _liaison(monde_lot3b, id_conflit)
    assert lien_conflit[0] is None and lien_conflit[1] == "ambigu"
    detail = detail_extraction(index, id_conflit)
    assert {id_modele_fiche, id_concurrent} <= {int(c["id_modele"]) for c in detail["candidats"]}


@pytest.mark.postgres
def test_resolution_en_lot_compteurs_et_idempotence(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    id_tardif = _enregistrer(monde_lot3b, "X-Yachts 42")
    assert _liaison(monde_lot3b, id_tardif)[1] == "non_resolu"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            assurer_modele(curseur, "X-Yachts 42")

    bilan = resoudre_extractions(index)
    assert bilan["liees"] >= 1
    assert _liaison(monde_lot3b, id_tardif)[1] == "lie_automatique"

    bilan_2 = resoudre_extractions(index)
    assert bilan_2["liees"] == 0


# ---------------------------------------------------------------------------
# Décision humaine et apprentissage d'alias
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_liaison_humaine_apprend_alias_pour_les_suivantes(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    bavaria_45 = monde_lot3b["modeles"]["bavaria_45"]

    id_ex = _enregistrer(monde_lot3b, "BAV-45 Cruiser")
    assert _liaison(monde_lot3b, id_ex)[1] in ("non_resolu", "ambigu")

    resultat = lier_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        id_modele=bavaria_45,
    )
    lien = _liaison(monde_lot3b, id_ex)
    assert lien == (bavaria_45, "lie_humain", "humain", int(approbateur["id_utilisateur"]))
    assert resultat["alias_appris"] is not None
    assert _valeur(
        monde_lot3b,
        "SELECT source FROM boat_model_aliases WHERE id_alias = %s",
        (int(resultat["alias_appris"]["id_alias"]),),
    ) == "apprentissage_lot3b"

    # Boucle d'apprentissage locale : le même libellé brut se lie désormais seul.
    id_suivante = _enregistrer(monde_lot3b, "BAV-45 Cruiser")
    assert _liaison(monde_lot3b, id_suivante) == (bavaria_45, "lie_automatique", "alias_exact", None)


@pytest.mark.postgres
def test_creation_modele_par_approbateur_et_refus_incoherents(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    id_ex = _enregistrer(monde_lot3b, "SO 349")
    resultat = lier_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        nouveau_modele="Jeanneau Sun Odyssey 349",
    )
    lien = _liaison(monde_lot3b, id_ex)
    assert lien[1:] == ("lie_humain", "humain_nouveau_modele", int(approbateur["id_utilisateur"]))
    assert resultat["modele"]["nom_canonique"] == "Jeanneau Sun Odyssey 349"
    # L'abréviation lue devient un alias appris du nouveau modèle.
    id_suivante = _enregistrer(monde_lot3b, "SO 349")
    assert _liaison(monde_lot3b, id_suivante)[1:3] == ("lie_automatique", "alias_exact")

    id_refus = _enregistrer(monde_lot3b, "Zodiac Pro 900")
    client: TestClient = monde_lot3b["client"]
    reponse = client.post(
        f"/extractions-voile/{id_refus}/lier",
        headers=_entetes(approbateur),
        json={"id_modele": monde_lot3b["modeles"]["bavaria_45"], "nouveau_modele": "Autre"},
    )
    assert reponse.status_code == 422
    reponse = client.post(
        f"/extractions-voile/{id_refus}/lier", headers=_entetes(approbateur), json={}
    )
    assert reponse.status_code == 422
    reponse = client.post(
        f"/extractions-voile/{id_refus}/lier",
        headers=_entetes(approbateur),
        json={"id_modele": 99999999},
    )
    assert reponse.status_code == 404
    assert _liaison(monde_lot3b, id_refus)[1] == "non_resolu"


@pytest.mark.postgres
def test_conflit_alias_refuse_en_409_sans_voler_letiquette(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            assurer_alias(curseur, monde_lot3b["modeles"]["sun_2000"], "Bateau Ecole")
    id_ex = _enregistrer(monde_lot3b, "Bateau Ecole")
    # L'alias exact lie déjà au Sun 2000 ; tenter de le rattacher ailleurs en
    # apprenant le même alias doit heurter le conflit, jamais le voler.
    client: TestClient = monde_lot3b["client"]
    reponse = client.post(
        f"/extractions-voile/{id_ex}/lier",
        headers=_entetes(monde_lot3b["comptes"]["approbateur"]),
        json={"id_modele": monde_lot3b["modeles"]["bavaria_45"]},
    )
    assert reponse.status_code == 409
    assert _liaison(monde_lot3b, id_ex)[0] == monde_lot3b["modeles"]["sun_2000"]


@pytest.mark.postgres
def test_reextraction_necrase_jamais_une_liaison_humaine(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    id_ex = _enregistrer(monde_lot3b, "Voilier inconnu 12m")
    lier_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        nouveau_modele="Prototype 12m Atelier",
    )
    lien_avant = _liaison(monde_lot3b, id_ex)

    # Ré-enregistrer la MÊME source (même empreinte ⇒ même clé) avec un libellé
    # relut différemment ne doit pas défaire la décision humaine.
    empreinte = _valeur(
        monde_lot3b,
        "SELECT empreinte_sha256 FROM sail_extractions WHERE id_extraction = %s",
        (id_ex,),
    )
    id_re = enregistrer_extraction_voile(
        index,
        ExtractionVoile(
            code_fiche="LOT3B-REEX",
            empreinte_sha256=empreinte,
            gabarit_code="FICHE_PORTANT_V1",
            methode_principale="fiche_extraite",
            type_voile="Spi Asymétrique",
            modele_bateau_brut="Sun 2000",
            slu_m=6.6,
            sf_m=3.08,
            statut_extraction="complete",
            statut_plausibilite="plausible",
        ),
    )
    assert id_re == id_ex
    assert _liaison(monde_lot3b, id_ex) == lien_avant


# ---------------------------------------------------------------------------
# Corrections approbateur
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_correction_dimension_historique_plausibilite_et_motif_appris(
    monde_lot3b: dict[str, Any],
) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    # SF (9,00) > SLU (6,60) : géométriquement impossible (RG16).
    id_ex = _enregistrer(
        monde_lot3b, "Sun 2000", sf_m=9.0, statut_plausibilite="implausible"
    )

    resultat = corriger_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        champ="sf_m",
        valeur="3,08",
        motif="Bordure relue sur le PDF : 3,08 m.",
    )
    assert resultat["avant"] == 9.0 and resultat["apres"] == 3.08
    assert resultat["propagation"]["statut"] == "aucune"
    ligne = detail_extraction(index, id_ex)["extraction"]
    assert ligne["sf_m"] == 3.08
    assert ligne["statut_plausibilite"] == "plausible"

    historique = detail_extraction(index, id_ex)["corrections"]
    assert len(historique) == 1
    assert historique[0]["champ"] == "sf_m"
    assert historique[0]["valeur_avant"] == 9.0
    assert historique[0]["valeur_apres"] == 3.08
    assert historique[0]["motif"] == "Bordure relue sur le PDF : 3,08 m."
    assert _valeur(
        monde_lot3b,
        "SELECT occurrences FROM extraction_motifs_appris WHERE champ = 'sf_m'",
        (),
    ) == 1

    # Même erreur relue une seconde fois : le motif monte en occurrences, et il
    # est SUGGÉRÉ (jamais appliqué seul) sur la troisième extraction fautive.
    id_bis = _enregistrer(monde_lot3b, "Sun 2000", sf_m=9.0, statut_plausibilite="implausible")
    corriger_extraction(
        index,
        id_bis,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        champ="sf_m",
        valeur=3.08,
    )
    assert _valeur(
        monde_lot3b,
        "SELECT occurrences FROM extraction_motifs_appris WHERE champ = 'sf_m'",
        (),
    ) == 2
    id_ter = _enregistrer(monde_lot3b, "Sun 2000", sf_m=9.0, statut_plausibilite="implausible")
    suggestions = detail_extraction(index, id_ter)["suggestions_motifs"]
    assert any(
        s["champ"] == "sf_m" and s["valeur_corrigee"] == "3.08" and s["occurrences"] == 2
        for s in suggestions
    )
    # Suggestion seulement : la valeur fautive reste tant qu'un humain n'a rien décidé.
    assert detail_extraction(index, id_ter)["extraction"]["sf_m"] == 9.0


@pytest.mark.postgres
def test_correction_du_libelle_relance_la_resolution(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    id_ex = _enregistrer(monde_lot3b, "Bavria 45")
    assert _liaison(monde_lot3b, id_ex)[1] == "ambigu"

    corriger_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        champ="modele_bateau_brut",
        valeur="Bavaria 45",
        motif="Libellé relu dans l'en-tête du PDF.",
    )
    lien = _liaison(monde_lot3b, id_ex)
    assert lien == (monde_lot3b["modeles"]["bavaria_45"], "lie_automatique", "cle_exacte", None)
    historique = detail_extraction(index, id_ex)["corrections"]
    assert historique[0]["valeur_avant"] == "Bavria 45"
    assert historique[0]["valeur_apres"] == "Bavaria 45"


@pytest.mark.postgres
def test_correction_cote_propagee_a_la_fiche_avec_revision(monde_lot3b: dict[str, Any]) -> None:
    index = monde_lot3b["index"]
    approbateur = monde_lot3b["comptes"]["approbateur"]
    id_fiche = int(monde_lot3b["fiches"][CODE_FICHE])
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m) VALUES (%s, 'finie', 6.6) "
                "ON CONFLICT DO NOTHING",
                (id_fiche,),
            )
            curseur.execute(
                "INSERT INTO fiche_champ_extrait "
                "(id_fiche, champ, rang, table_cible, colonne_cible, valeur_brute, "
                "valeur_normalisee, methode, confiance) "
                "VALUES (%s, %s, NULL, 'fiche_cotes', 'slu_m', '6.6', '6.6', 'test-lot3b', 0.9)",
                (id_fiche, CHAMP_COTE),
            )
            curseur.execute("SELECT revision FROM fiche WHERE id_fiche = %s", (id_fiche,))
            revision = int(curseur.fetchone()[0])

    champs = [
        ChampExtractionVoile(
            champ=CHAMP_COTE,
            table_cible="fiche_cotes",
            colonne_cible="slu_m",
            valeur_brute="6.6",
            valeur_normalisee="6.6",
            confiance=0.9,
        )
    ]
    id_ex = _enregistrer(
        monde_lot3b, "Sun 2000", id_fiche=id_fiche, code_fiche=CODE_FICHE, champs=champs
    )

    # Sans révision : refus net, extraction ET fiche inchangées (atomicité).
    with pytest.raises(HTTPException) as refus:
        corriger_extraction(
            index,
            id_ex,
            id_utilisateur=int(approbateur["id_utilisateur"]),
            champ="slu_m",
            valeur=6.7,
        )
    assert refus.value.status_code == 428
    assert float(
        _valeur(monde_lot3b, "SELECT slu_m FROM sail_extractions WHERE id_extraction = %s", (id_ex,))
    ) == 6.6

    # Révision périmée : 409, rien n'est écrit non plus.
    with pytest.raises(HTTPException) as conflit:
        corriger_extraction(
            index,
            id_ex,
            id_utilisateur=int(approbateur["id_utilisateur"]),
            champ="slu_m",
            valeur=6.7,
            revision_fiche=revision + 5,
        )
    assert conflit.value.status_code == 409

    resultat = corriger_extraction(
        index,
        id_ex,
        id_utilisateur=int(approbateur["id_utilisateur"]),
        champ="slu_m",
        valeur=6.7,
        revision_fiche=revision,
    )
    assert resultat["propagation"]["statut"] == "fiche"
    assert float(
        _valeur(
            monde_lot3b,
            "SELECT slu_m FROM fiche_cotes WHERE id_fiche = %s AND jeu = 'finie'",
            (id_fiche,),
        )
    ) == 6.7
    assert _valeur(monde_lot3b, "SELECT revision FROM fiche WHERE id_fiche = %s", (id_fiche,)) > revision
    assert detail_extraction(index, id_ex)["extraction"]["slu_m"] == 6.7


# ---------------------------------------------------------------------------
# API — rôles et parcours
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_api_extraactions_voile_roles_et_parcours(monde_lot3b: dict[str, Any]) -> None:
    client: TestClient = monde_lot3b["client"]
    comptes = monde_lot3b["comptes"]
    id_ex = _enregistrer(monde_lot3b, "BAV-45 Cruiser")

    # Lecture nominative : possible pour tout compte nominatif.
    reponse = client.get("/extractions-voile", headers=_entetes(comptes["lecture"]))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["total"] >= 1
    assert any(int(e["id_extraction"]) == id_ex for e in corps["extractions"])

    # Jeton de service seul (aucune session nominative) : 401.
    reponse = client.get("/extractions-voile", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
    assert reponse.status_code == 401

    # Mutations : approbateur seul.
    for compte in (comptes["lecture"], comptes["utilisateur"]):
        reponse = client.post(
            f"/extractions-voile/{id_ex}/lier",
            headers=_entetes(compte),
            json={"id_modele": monde_lot3b["modeles"]["bavaria_45"]},
        )
        assert reponse.status_code == 403
        reponse = client.post(
            f"/extractions-voile/{id_ex}/corriger",
            headers=_entetes(compte),
            json={"champ": "sf_m", "valeur": 3.09},
        )
        assert reponse.status_code == 403
        reponse = client.post(
            "/extractions-voile/resoudre-lot", headers=_entetes(compte), json={}
        )
        assert reponse.status_code == 403

    approbateur = comptes["approbateur"]
    reponse = client.get(f"/extractions-voile/{id_ex}", headers=_entetes(approbateur))
    assert reponse.status_code == 200
    detail = reponse.json()
    assert detail["extraction"]["modele_bateau_brut"] == "BAV-45 Cruiser"
    assert detail["modele_lie"] is None

    reponse = client.post(
        f"/extractions-voile/{id_ex}/lier",
        headers=_entetes(approbateur),
        json={"id_modele": monde_lot3b["modeles"]["bavaria_45"]},
    )
    assert reponse.status_code == 200
    reponse = client.get(f"/extractions-voile/{id_ex}", headers=_entetes(approbateur))
    detail = reponse.json()
    assert detail["modele_lie"]["nom_canonique"] == "Bavaria 45"
    assert detail["extraction"]["statut_liaison"] == "lie_humain"

    reponse = client.post(
        f"/extractions-voile/{id_ex}/corriger",
        headers=_entetes(approbateur),
        json={"champ": "sf_m", "valeur": 3.09, "motif": "Relu."},
    )
    assert reponse.status_code == 200

    _enregistrer(monde_lot3b, "Bavaria 4")
    reponse = client.post(
        "/extractions-voile/resoudre-lot", headers=_entetes(approbateur), json={"limite": 50}
    )
    assert reponse.status_code == 200
    assert "liees" in reponse.json()

    reponse = client.get("/extractions-voile/99999999", headers=_entetes(approbateur))
    assert reponse.status_code == 404

    liste = lister_extractions(monde_lot3b["index"], statut_liaison="lie_humain")
    assert any(int(e["id_extraction"]) == id_ex for e in liste["extractions"])
