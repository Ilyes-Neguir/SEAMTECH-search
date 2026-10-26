"""Lot 7 — Accessoires : extraction dédiée, catégorie, liaison 1:1, recherche.

Les accessoires (coussins, housses, tauds…) deviennent une famille de
documents à part entière, au même niveau d'exigence que les voiles :

* schéma propre (type, matière, couleur, dimensions, quantité) mais même
  discipline de provenance que le Lot 3a : confiance par champ, page/zone/
  valeur brute, unités jamais devinées ;
* catégorie (voile|accessoire) portée par la fiche, PROPOSÉE au dépôt —
  nom de dossier contenant « accessoire » (normalisé) d'abord, famille du
  gabarit gagnant ensuite — toujours avec sa source (dossier|document|
  humain) et corrigeable par l'approbateur : rien n'est deviné en silence ;
* liaison à UN modèle de bateau canonique (automatique si preuve unique,
  sinon décision approbateur — version simplifiée du Lot 3b) ;
* recherche : facette catégorie + filtre, sans toucher aux Fonctions A/B
  (comparaison et confiance restent voile-only).

Aucun PDF réel ici : les fiches accessoires du corpus serviront à la
calibration (mesure locale, hors Git) quand l'arborescence Dropbox du
commanditaire sera en ligne ; le gabarit v1 est générique (libellés).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.accessoires import (
    CODE_GABARIT_ACCESSOIRE,
    AccessoireRefuse,
    ExtractionAccessoire,
    changer_categorie_fiche,
    construire_extraction_accessoire,
    corriger_accessoire,
    enregistrer_extraction_accessoire,
    gabarit_accessoire_v1,
    lier_accessoire,
    lister_extractions_accessoire,
    proposer_categorie,
)
from seamtech_search.comptes import creer_utilisateur, ouvrir_session
from seamtech_search.config import AppConfig
from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche_resultat
from seamtech_search.recherche import rechercher_fiches
from seamtech_search.referentiel_bateaux import assurer_modele
from seamtech_search.schema_metier import (
    MIGRATIONS_METIER,
    SQL_031_ACCESSOIRES,
    TABLES_METIER,
    VERSION_SCHEMA_METIER,
)

JETON_SERVICE = "jeton-service-lot7"


def _fiche_accessoire(**remplacements: Any) -> FicheExtraite:  # noqa: ANN401
    champs = [
        ChampExtrait(champ="fiche.code", valeur_brute="LOT7-ACC-1", valeur_normalisee="LOT7-ACC-1", confiance=0.99, page=1),
        ChampExtrait(champ="accessoire.type", valeur_brute="Coussin de cockpit", valeur_normalisee="Coussin de cockpit", confiance=0.95, page=1),
        ChampExtrait(champ="accessoire.matiere", valeur_brute="Sunbrella Plus", valeur_normalisee="Sunbrella Plus", confiance=0.9, page=1),
        ChampExtrait(champ="accessoire.couleur", valeur_brute="Captain bleu", valeur_normalisee="Captain bleu", confiance=0.85, page=1),
        ChampExtrait(champ="accessoire.dimensions", valeur_brute="1200 mm x 450 mm x 100 mm", valeur_normalisee="1200 mm x 450 mm x 100 mm", confiance=0.8, page=1),
        ChampExtrait(champ="accessoire.quantite", valeur_brute="4", valeur_normalisee="4", confiance=0.9, page=1),
    ]
    donnees: dict[str, Any] = {
        "code": "LOT7-ACC-1",
        "gabarit_code": CODE_GABARIT_ACCESSOIRE,
        "gabarit_version": 1,
        "bateau_nom": "Bavaria",
        "bateau_taille": "45",
        "champs": champs,
    }
    donnees.update(remplacements)
    return FicheExtraite(**donnees)


# ---------------------------------------------------------------------------
# Contrats purs
# ---------------------------------------------------------------------------


def test_proposer_categorie_priorites_et_sources() -> None:
    # Le dossier (acte humain délibéré) prime sur le document.
    assert proposer_categorie("ACCESSOIRES 2024", "FICHE_PORTANT_V1") == ("accessoire", "dossier")
    assert proposer_categorie("Dossier accessoire client", None) == ("accessoire", "dossier")
    assert proposer_categorie("Accessoires", None) == ("accessoire", "dossier")
    # Sinon le document parle (famille du gabarit gagnant).
    assert proposer_categorie("BAVARIA 45 ST123", CODE_GABARIT_ACCESSOIRE) == ("accessoire", "document")
    assert proposer_categorie("BAVARIA 45 ST123", "FICHE_PORTANT_V1") == ("voile", "document")
    assert proposer_categorie(None, None) == ("voile", "document")
    # Normalisation : accents, casse, pluriel, tirets.
    assert proposer_categorie("accéssoires-atelier", None)[0] == "accessoire"


def test_gabarit_accessoire_detection_et_regles() -> None:
    gabarit = gabarit_accessoire_v1()
    assert gabarit.code == CODE_GABARIT_ACCESSOIRE == "FICHE_ACCESSOIRE_V1"
    # normaliser_terme est la normalisation appliquée aux lignes par
    # texte_normalise(pages) — même entrant que le scanner de dépôt.
    from seamtech_search.fiches.extraction import normaliser_terme

    texte_accessoire = normaliser_terme("COUSSIN DE COCKPIT mousse sellerie housse de protection")
    assert gabarit.score_detection(texte_accessoire) >= 2
    texte_voile = normaliser_terme("GRAND VOILE SLU SLE SF SHW guindant bordure chute lattes ris œillets têtière")
    assert gabarit.score_detection(texte_voile) == 0
    cibles = {regle.cible for regle in gabarit.champs}
    assert {
        "accessoire.type",
        "accessoire.matiere",
        "accessoire.couleur",
        "accessoire.dimensions",
        "accessoire.quantite",
    } <= cibles


def test_construire_extraction_accessoire_provenance_et_dimensions() -> None:
    extraction = construire_extraction_accessoire(_fiche_accessoire(), id_fiche=7)
    assert extraction.type_accessoire == "Coussin de cockpit"
    assert extraction.matiere == "Sunbrella Plus"
    assert extraction.couleur == "Captain bleu"
    assert extraction.quantite == 4
    # Dimensions explicitement unitaires : canonisées en mètres.
    assert extraction.longueur_m == pytest.approx(1.2)
    assert extraction.largeur_m == pytest.approx(0.45)
    assert extraction.hauteur_m == pytest.approx(0.1)
    assert extraction.modele_bateau_brut == "Bavaria 45"
    assert extraction.statut_extraction == "complete"
    assert extraction.id_fiche == 7
    champs = {c.nom: c for c in extraction.champs}
    assert champs["type_accessoire"].confiance == pytest.approx(0.95)
    assert champs["type_accessoire"].page == 1


def test_construire_extraction_accessoire_unites_jamais_devinees() -> None:
    fiche = _fiche_accessoire(
        champs=[
            ChampExtrait(champ="accessoire.type", valeur_brute="housse", valeur_normalisee="housse", confiance=0.7, page=1),
            ChampExtrait(champ="accessoire.dimensions", valeur_brute="120 x 45", valeur_normalisee="120 x 45", confiance=0.7, page=1),
        ]
    )
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.dimensions_texte == "120 x 45"
    assert extraction.longueur_m is None and extraction.largeur_m is None
    assert any(a.code == "dimensions_sans_unite" for a in extraction.anomalies)
    assert extraction.statut_extraction == "partielle"

    vide = construire_extraction_accessoire(FicheExtraite(code="LOT7-VIDE"))
    assert vide.type_accessoire is None
    assert vide.statut_extraction == "a_reprendre"


def test_migration_031_contrats_schema() -> None:
    assert VERSION_SCHEMA_METIER == "031_accessoires"
    assert MIGRATIONS_METIER[-1][0] == "031_accessoires"
    assert MIGRATIONS_METIER[-2][0] == "030_evaluations_confiance"
    sql = SQL_031_ACCESSOIRES
    for fragment in (
        "CREATE TABLE IF NOT EXISTS accessory_extractions",
        "CREATE TABLE IF NOT EXISTS accessory_corrections",
        "ADD COLUMN IF NOT EXISTS categorie",
        "categorie IN ('voile', 'accessoire')",
        "categorie_source IN ('dossier', 'document', 'humain')",
        "REFERENCES boat_models(id_modele)",
        "v_fiche_recherche",
    ):
        assert fragment in sql, fragment
    assert len(TABLES_METIER) == 46
    assert TABLES_METIER[-2:] == ("accessory_extractions", "accessory_corrections")


# ---------------------------------------------------------------------------
# Fixture PostgreSQL réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def monde_lot7(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]
    initialiser_gabarits(index)
    comptes: dict[str, dict[str, Any]] = {}
    for cle, identifiant, role in (
        ("lecture", "lot7-lecture", "lecture"),
        ("approbateur", "lot7-approbateur", "approbateur"),
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
            assurer_modele(curseur, "Bavaria 45")

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused-lot7.db",
        database_url=base_recherche["url"],
        auth_token=JETON_SERVICE,
        storage_backend="local",
        web_worker_enabled=False,
    )
    return {
        **base_recherche,
        "client": TestClient(create_app(config)),
        "comptes": comptes,
    }


def _entetes(compte: dict[str, Any]) -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": str(compte["identifiant"]),
        "X-SEAMTECH-ROLE": str(compte["role"]),
        "X-SEAMTECH-SESSION": str(compte["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(compte["jeton"]),
    }


def _accessoire(**remplacements: Any) -> ExtractionAccessoire:  # noqa: ANN401
    donnees: dict[str, Any] = {
        "code_fiche": "LOT7-ACC-9",
        "nom_fichier_source": "accessoire-lot7.pdf",
        "gabarit_code": CODE_GABARIT_ACCESSOIRE,
        "methode_principale": "gabarit",
        "type_accessoire": "housse",
        "matiere": "Sunbrella Plus",
        "couleur": "Captain bleu",
        "quantite": 2,
        "modele_bateau_brut": "Bavaria 45",
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
    }
    donnees.update(remplacements)
    return ExtractionAccessoire(**donnees)


@pytest.mark.postgres
def test_accessoire_persiste_auto_lie_puis_relu(monde_lot7: dict[str, Any]) -> None:
    index = monde_lot7["index"]
    id_acc = enregistrer_extraction_accessoire(index, _accessoire())
    liste = lister_extractions_accessoire(index)
    assert len(liste["extractions"]) == 1
    relu = liste["extractions"][0]
    assert int(relu["id_extraction"]) == id_acc
    # Modèle unique exact : lié automatiquement, comme une voile (Lot 3b).
    assert relu["boat_model_id"] is not None
    assert relu["statut_liaison"] == "lie_automatique"

    id_inconnu = enregistrer_extraction_accessoire(
        index, _accessoire(code_fiche="LOT7-ACC-10", modele_bateau_brut="Prototype inconnu")
    )
    non_liees = lister_extractions_accessoire(index, statut_liaison="non_resolu")
    assert [int(e["id_extraction"]) for e in non_liees["extractions"]] == [id_inconnu]

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_modele FROM boat_models")
            id_modele = int(curseur.fetchone()[0])
    lie = lier_accessoire(
        index,
        id_inconnu,
        id_utilisateur=int(monde_lot7["comptes"]["approbateur"]["id_utilisateur"]),
        id_modele=id_modele,
    )
    assert lie["statut_liaison"] == "lie_humain"
    assert int(lie["boat_model_id"]) == id_modele


@pytest.mark.postgres
def test_correction_accessoire_appliquee_et_historisee(monde_lot7: dict[str, Any]) -> None:
    index = monde_lot7["index"]
    id_utilisateur = int(monde_lot7["comptes"]["approbateur"]["id_utilisateur"])
    id_acc = enregistrer_extraction_accessoire(index, _accessoire())

    corriger_accessoire(
        index, id_acc, id_utilisateur=id_utilisateur, champ="couleur",
        valeur="bleu marine", motif="Teinte relue sur la fiche.",
    )
    relu = lister_extractions_accessoire(index)["extractions"][0]
    assert relu["couleur"] == "bleu marine"

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT COUNT(*) FROM accessory_corrections WHERE id_extraction = %s",
                (id_acc,),
            )
            assert int(curseur.fetchone()[0]) == 1

    with pytest.raises(AccessoireRefuse):
        corriger_accessoire(
            index, id_acc, id_utilisateur=id_utilisateur, champ="quantite",
            valeur=0, motif="Quantité impossible.",
        )
    with pytest.raises(AccessoireRefuse):
        corriger_accessoire(
            index, id_acc, id_utilisateur=id_utilisateur, champ="champ_inconnu",
            valeur="x", motif="Champ hors schéma.",
        )


@pytest.mark.postgres
def test_depot_categorie_projection_et_reclassification(monde_lot7: dict[str, Any]) -> None:
    index = monde_lot7["index"]
    approbateur = int(monde_lot7["comptes"]["approbateur"]["id_utilisateur"])
    # Écriture réelle d'une fiche catégorisée accessoire (chemin de dépôt) :
    # la projection accessoire naît dans la même transaction, aucune voile.
    ecriture = ecrire_fiche_resultat(
        index, _fiche_accessoire(), categorie=("accessoire", "dossier")
    )
    id_fiche = ecriture.id_fiche

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT categorie, categorie_source FROM fiche WHERE id_fiche = %s", (id_fiche,))
            assert curseur.fetchone() == ("accessoire", "dossier")
            curseur.execute("SELECT COUNT(*) FROM accessory_extractions WHERE id_fiche = %s", (id_fiche,))
            assert int(curseur.fetchone()[0]) == 1
            curseur.execute("SELECT COUNT(*) FROM sail_extractions WHERE id_fiche = %s", (id_fiche,))
            assert int(curseur.fetchone()[0]) == 0

    # La recherche filtre et facette par catégorie.
    resultats = rechercher_fiches(index, requete="", filtres={"categorie": "accessoire"})
    assert any(int(f["id_fiche"]) == id_fiche for f in resultats["resultats"])
    facettes = resultats.get("facettes") or {}
    assert any(
        ligne.get("valeur") == "accessoire" for ligne in facettes.get("categorie", [])
    )

    # Reclassification approbateur : la fiche redevient une voile, la
    # projection accessoire (dérivée, sans correction) est remplacée.
    changer_categorie_fiche(index, id_fiche, categorie="voile", id_utilisateur=approbateur)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT categorie, categorie_source FROM fiche WHERE id_fiche = %s", (id_fiche,))
            assert curseur.fetchone() == ("voile", "humain")
            curseur.execute("SELECT COUNT(*) FROM accessory_extractions WHERE id_fiche = %s", (id_fiche,))
            assert int(curseur.fetchone()[0]) == 0
            curseur.execute("SELECT COUNT(*) FROM sail_extractions WHERE id_fiche = %s", (id_fiche,))
            assert int(curseur.fetchone()[0]) == 1


@pytest.mark.postgres
def test_api_accessoires_roles_et_parcours(monde_lot7: dict[str, Any]) -> None:
    client: TestClient = monde_lot7["client"]
    comptes = monde_lot7["comptes"]
    id_acc = enregistrer_extraction_accessoire(
        monde_lot7["index"], _accessoire(modele_bateau_brut="Prototype inconnu")
    )

    reponse = client.get(
        "/accessoires/extractions", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE}
    )
    assert reponse.status_code == 401

    reponse = client.get("/accessoires/extractions", headers=_entetes(comptes["lecture"]))
    assert reponse.status_code == 200
    assert len(reponse.json()["extractions"]) == 1

    with monde_lot7["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT id_modele FROM boat_models")
            id_modele = int(curseur.fetchone()[0])

    reponse = client.post(
        f"/accessoires/extractions/{id_acc}/lier",
        headers=_entetes(comptes["lecture"]),
        json={"id_modele": id_modele},
    )
    assert reponse.status_code == 403

    reponse = client.post(
        f"/accessoires/extractions/{id_acc}/lier",
        headers=_entetes(comptes["approbateur"]),
        json={"id_modele": id_modele},
    )
    assert reponse.status_code == 200
    assert reponse.json()["statut_liaison"] == "lie_humain"

    reponse = client.post(
        f"/accessoires/extractions/{id_acc}/corriger",
        headers=_entetes(comptes["approbateur"]),
        json={"champ": "quantite", "valeur": 3, "motif": "Recompté à l'atelier."},
    )
    assert reponse.status_code == 200

    reponse = client.get(
        "/accessoires/extractions/99999999", headers=_entetes(comptes["lecture"])
    )
    assert reponse.status_code == 404
