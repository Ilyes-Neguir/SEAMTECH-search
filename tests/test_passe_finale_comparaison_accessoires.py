"""Passe finale — comparaison de voiles, accessoires, notifications.

Vérification indépendante du 29/10/2026, sur PostgreSQL réel pour tout ce qui
touche à la couche métier. Trois fonctions sont éprouvées à leurs bornes :

* **Comparaison même modèle** : cas identique, écart de quelques centimètres,
  modèle hétérogène, données partielles des deux côtés, extraction comparée à
  elle-même. Aucun écart n'est inventé quand une valeur manque.
* **Accessoires** : liaison 1:1 (modèle existant OU nouveau nom, jamais les
  deux), refus explicites, catégorie proposée au dépôt, correction historisée.
* **Notifications** : déclenchement par une demande, lecture, marquage lu
  idempotent, et l'isolation entre comptes (une notification d'autrui est
  indistinguable d'une notification inexistante).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from seamtech_search.accessoires import (
    CODE_GABARIT_ACCESSOIRE,
    AccessoireIntrouvable,
    AccessoireRefuse,
    FicheIntrouvable,
    changer_categorie_fiche,
    construire_extraction_accessoire,
    enregistrer_extraction_accessoire,
    lier_accessoire,
    proposer_categorie,
    resoudre_accessoire_curseur,
)
from seamtech_search.comparaison_voile import (
    ComparaisonRefusee,
    comparer_extractions,
)
from seamtech_search.comptes.comptes import creer_utilisateur
from seamtech_search.extractions_voile import ExtractionVoile, MateriauExtractionVoile
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite

# ---------------------------------------------------------------------------
# Comparaison même modèle — fonction pure, aucun accès base
# ---------------------------------------------------------------------------


def _voile(**remplacements: Any) -> ExtractionVoile:  # noqa: ANN401
    champs: dict[str, Any] = {
        "id_extraction": 1,
        "boat_model_id": 10,
        "type_voile": "Grand-voile",
        "gamme": "Performance",
        "slu_m": 17.20,
        "sle_m": 17.80,
        "sf_m": 6.40,
        "shw_m": 5.20,
        "spa_m2": 55.0,
        "tetiere_m": 0.12,
        "poids_kg": 42.0,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
        "statut_liaison": "lie_automatique",
    }
    champs.update(remplacements)
    return ExtractionVoile(**champs)


def _dimensions(resultat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {ligne["champ"]: ligne for ligne in resultat["blocs"]["dimensions"]}


def test_cas_identique_ne_produit_aucun_ecart() -> None:
    resultat = comparer_extractions(_voile(id_extraction=1), _voile(id_extraction=2))
    assert resultat["comparable"] is True
    assert resultat["avertissements"] == [], resultat["avertissements"]
    assert resultat["synthese"]["dimensions_communes"] == 7
    assert resultat["synthese"]["dimensions_identiques"] == 7
    assert resultat["synthese"]["ecart_max_pct"] == 0.0
    for ligne in _dimensions(resultat).values():
        assert ligne["etat"] == "comparable"
        assert ligne["delta"] == 0.0


def test_ecart_de_quelques_centimetres_est_mesure_au_centimetre() -> None:
    b = _voile(id_extraction=2, slu_m=17.23, spa_m2=55.4)
    resultat = comparer_extractions(_voile(), b)
    dimensions = _dimensions(resultat)
    assert dimensions["slu_m"]["delta_cm"] == pytest.approx(3.0)  # 17,23 − 17,20
    assert dimensions["slu_m"]["delta_pct"] == pytest.approx(0.17, abs=0.01)
    assert dimensions["spa_m2"]["delta_pct"] == pytest.approx(0.73, abs=0.01)
    assert resultat["synthese"]["ecart_max_champ"] in {"slu_m", "spa_m2"}
    assert resultat["synthese"]["dimensions_identiques"] == 5
    # Un écart de 3 cm n'est PAS un signal : aucun avertissement n'est ajouté.
    assert resultat["comparable"] is True


def test_modele_heterogene_n_est_pas_une_comparaison_meme_modele() -> None:
    resultat = comparer_extractions(_voile(), _voile(id_extraction=2, boat_model_id=99))
    assert resultat["meme_modele_bateau"] is False
    assert resultat["comparable"] is False
    assert any("DIFFÉRENTS" in a for a in resultat["avertissements"])


def test_type_de_voile_different_bloque_la_comparabilite() -> None:
    resultat = comparer_extractions(_voile(), _voile(id_extraction=2, type_voile="Génois"))
    assert resultat["meme_type_voile"] is False
    assert resultat["comparable"] is False
    assert any("Types de voile différents" in a for a in resultat["avertissements"])


def test_donnees_partielles_ne_fabriquent_aucun_ecart() -> None:
    """Trois dimensions manquantes d'un côté : ni delta, ni pourcentage."""
    b = _voile(id_extraction=2, sle_m=None, sf_m=None, shw_m=None, tetiere_m=None)
    resultat = comparer_extractions(_voile(), b)
    dimensions = _dimensions(resultat)
    for champ in ("slu_m", "spa_m2", "poids_kg"):
        assert dimensions[champ]["etat"] == "comparable"
        assert dimensions[champ]["delta"] is not None
    for champ in ("sle_m", "sf_m", "shw_m", "tetiere_m"):
        assert dimensions[champ]["etat"] == "manquant_b"
        assert dimensions[champ]["delta"] is None
        assert dimensions[champ]["delta_pct"] is None
    assert resultat["synthese"]["dimensions_communes"] == 3


def test_chevauchement_insuffisant_est_signale() -> None:
    """Une seule dimension commune : l'outil le dit au lieu de conclure."""
    b = _voile(
        id_extraction=2,
        sle_m=None,
        sf_m=None,
        shw_m=None,
        spa_m2=None,
        tetiere_m=None,
        poids_kg=None,
    )
    resultat = comparer_extractions(_voile(), b)
    assert resultat["synthese"]["dimensions_communes"] == 1
    assert any("Chevauchement insuffisant" in a for a in resultat["avertissements"])


def test_extraction_comparee_a_elle_meme_refusee() -> None:
    with pytest.raises(ComparaisonRefusee):
        comparer_extractions(_voile(id_extraction=7), _voile(id_extraction=7))


def test_materiaux_presents_d_un_seul_cote() -> None:
    a = _voile(
        id_extraction=1,
        materiaux=[MateriauExtractionVoile(role="Toile", niveau=1, designation="DCX", grammage_g_m2=250.0)],
    )
    b = _voile(
        id_extraction=2,
        materiaux=[MateriauExtractionVoile(role="Toile", niveau=1, designation="DCX", grammage_g_m2=260.0)],
    )
    resultat = comparer_extractions(a, b)
    materiau = resultat["blocs"]["materiaux"][0]
    assert materiau["etat"] == "commun"
    assert materiau["delta_grammage_pct"] == pytest.approx(4.0)

    seul = comparer_extractions(a, _voile(id_extraction=2))
    assert seul["blocs"]["materiaux"][0]["etat"] == "a_seul"
    assert seul["blocs"]["materiaux"][0]["delta_grammage_pct"] is None


def test_extraction_implausible_est_signalee() -> None:
    resultat = comparer_extractions(_voile(), _voile(id_extraction=2, statut_plausibilite="implausible"))
    assert any("implausible" in a for a in resultat["avertissements"])


# ---------------------------------------------------------------------------
# Accessoires — catégorie, extraction, liaison 1:1
# ---------------------------------------------------------------------------


def _compte(index: Any, identifiant: str, role: str = "approbateur") -> int:  # noqa: ANN401
    compte = creer_utilisateur(
        index,
        identifiant,
        f"Compte {identifiant}",
        f"MotDePasse-{identifiant}-2026",
        role=role,
        doit_changer_mot_de_passe=False,
    )
    return int(compte["id_utilisateur"])


def test_categorie_proposee_puis_reclassee_par_un_humain(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    assert proposer_categorie("ACCESSOIRES 2024", None) == ("accessoire", "dossier")
    assert proposer_categorie("BAVARIA 45", "FICHE_PORTANT_V1") == ("voile", "document")
    ids = base_recherche["fiches"]
    id_fiche = int(ids[sorted(ids)[0]])
    approbateur = _compte(index, "pf-acc-approbateur")
    resultat = changer_categorie_fiche(
        index, id_fiche, categorie="accessoire", id_utilisateur=approbateur
    )
    assert resultat["categorie"] == "accessoire"
    assert resultat["categorie_source"] == "humain"
    with pytest.raises(AccessoireRefuse):
        changer_categorie_fiche(index, id_fiche, categorie="planche", id_utilisateur=approbateur)
    with pytest.raises(FicheIntrouvable):
        changer_categorie_fiche(index, 999_999, categorie="voile", id_utilisateur=approbateur)


def _fiche_accessoire(code: str = "PF-ACC-1", **remplacements: Any) -> FicheExtraite:  # noqa: ANN401
    champs = [
        ChampExtrait(champ="accessoire.type", valeur_brute="Coussin de cockpit", valeur_normalisee="Coussin de cockpit", confiance=0.95, page=1),
        ChampExtrait(champ="accessoire.matiere", valeur_brute="Sunbrella Plus", valeur_normalisee="Sunbrella Plus", confiance=0.9, page=1),
        ChampExtrait(champ="accessoire.dimensions", valeur_brute="1200 mm x 450 mm x 100 mm", valeur_normalisee="1200 mm x 450 mm x 100 mm", confiance=0.8, page=1),
        ChampExtrait(champ="accessoire.quantite", valeur_brute="4", valeur_normalisee="4", confiance=0.9, page=1),
    ]
    donnees: dict[str, Any] = {
        "code": code,
        "gabarit_code": CODE_GABARIT_ACCESSOIRE,
        "gabarit_version": 1,
        "bateau_nom": "Bavaria",
        "bateau_taille": "45",
        "champs": champs,
    }
    donnees.update(remplacements)
    return FicheExtraite(**donnees)


def test_extraction_accessoire_dimensions_et_provenance() -> None:
    extraction = construire_extraction_accessoire(_fiche_accessoire())
    assert extraction.longueur_m == pytest.approx(1.2)
    assert extraction.largeur_m == pytest.approx(0.45)
    assert extraction.hauteur_m == pytest.approx(0.1)
    assert extraction.quantite == 4
    assert extraction.statut_extraction == "complete"
    champs = {c.nom: c for c in extraction.champs}
    assert champs["type_accessoire"].page == 1
    assert champs["type_accessoire"].confiance == pytest.approx(0.95)


def test_extraction_accessoire_sans_unite_ne_devine_rien() -> None:
    fiche = _fiche_accessoire(
        champs=[
            ChampExtrait(champ="accessoire.type", valeur_brute="housse", valeur_normalisee="housse", confiance=0.7, page=1),
            ChampExtrait(champ="accessoire.dimensions", valeur_brute="120 x 45", valeur_normalisee="120 x 45", confiance=0.7, page=1),
        ]
    )
    extraction = construire_extraction_accessoire(fiche)
    assert extraction.longueur_m is None
    assert any(a.code == "dimensions_sans_unite" for a in extraction.anomalies)


def _extraire_en_base(index: Any, code: str) -> int:  # noqa: ANN401
    extraction = construire_extraction_accessoire(_fiche_accessoire(code))
    enregistree = enregistrer_extraction_accessoire(index, extraction)
    return int(enregistree)


def test_liaison_accessoire_refuse_les_demandes_incoherentes(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    id_extraction = _extraire_en_base(index, "PF-ACC-2")
    approbateur = _compte(index, "pf-acc-refus")

    # Ni modèle existant, ni nouveau modèle : rien à décider.
    with pytest.raises(AccessoireRefuse):
        lier_accessoire(index, id_extraction, id_utilisateur=approbateur)
    # Les deux à la fois : ambigu.
    with pytest.raises(AccessoireRefuse):
        lier_accessoire(index, id_extraction, id_utilisateur=approbateur, id_modele=1, nouveau_modele="Bavaria 46")
    # Modèle inexistant : refus explicite, pas de liaison silencieuse.
    with pytest.raises(AccessoireRefuse):
        lier_accessoire(index, id_extraction, id_utilisateur=approbateur, id_modele=999_999)
    # Extraction inexistante : introuvable, jamais créée au passage.
    with pytest.raises(AccessoireIntrouvable):
        lier_accessoire(index, 999_999, id_utilisateur=approbateur, nouveau_modele="Bavaria 46")


def test_liaison_accessoire_a_un_modele_existant(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    id_extraction = _extraire_en_base(index, "PF-ACC-3")
    approbateur = _compte(index, "pf-acc-lieur")
    # Le corpus semé ne contient pas de modèle canonique : on le crée par la
    # voie normale (liaison « nouveau modèle »), puis on relie une DEUXIÈME
    # extraction au modèle désormais existant.
    premier = lier_accessoire(
        index, _extraire_en_base(index, "PF-ACC-AMORCE"), id_utilisateur=approbateur,
        nouveau_modele="Bavaria 45 Reference",
    )
    id_modele = int(premier["boat_model_id"])
    detail = lier_accessoire(index, id_extraction, id_utilisateur=approbateur, id_modele=id_modele)
    assert detail["boat_model_id"] == id_modele
    assert detail["statut_liaison"] == "lie_humain"
    # Un accessoire ne retient qu'UN modèle : pas de liste, pas de second lien.
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "SELECT count(*) FROM accessory_extractions WHERE id_extraction = %s", (id_extraction,)
        )
        assert int(curseur.fetchone()[0]) == 1


def test_liaison_accessoire_par_nouveau_modele_cree_le_referentiel(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    id_extraction = _extraire_en_base(index, "PF-ACC-4")
    approbateur = _compte(index, "pf-acc-nouveau")
    detail = lier_accessoire(
        index, id_extraction, id_utilisateur=approbateur, nouveau_modele="Bavaria 46 Accessoires"
    )
    assert detail["statut_liaison"] == "lie_humain"
    assert detail["methode_liaison"] == "humain_nouveau_modele"
    assert detail["boat_model_id"]


def test_resoudre_accessoire_inconnu_leve_introuvable(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    with index.connect() as connexion, connexion.cursor() as curseur, pytest.raises(AccessoireIntrouvable):
        resoudre_accessoire_curseur(curseur, 999_999)


# ---------------------------------------------------------------------------
# Notifications — déclenchement, lecture, états, isolation
# ---------------------------------------------------------------------------


def _acteur(identifiant: str, role: str, id_utilisateur: int) -> Any:  # noqa: ANN401
    from seamtech_search.securite_requetes import ActeurRequete

    return ActeurRequete(
        type_acteur="utilisateur",
        libelle_audit=identifiant,
        identifiant=identifiant,
        role=role,
        id_utilisateur=id_utilisateur,
    )


def _creer_demande_suppression(index: Any, id_utilisateur: int, code: str) -> dict[str, Any]:  # noqa: ANN401
    from seamtech_search.approbations import creer_demande

    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT revision FROM fiche WHERE code = %s", (code,))
        revision = int(curseur.fetchone()[0])
    acteur = _acteur("pf-notif-demandeur", "utilisateur", id_utilisateur)
    return creer_demande(
        index,
        acteur,
        {"type_demande": "suppression", "code_fiche": code, "motif": "Doublon constaté", "revision": revision},
    )


def test_une_demande_notifie_l_approbateur_puis_se_marque_lue(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    demandeur = _compte(index, "pf-notif-demandeur", role="utilisateur")
    approbateur = _compte(index, "pf-notif-approbateur")
    code = sorted(base_recherche["fiches"])[0]

    from seamtech_search.approbations import lister_notifications, marquer_notification_lue

    acteur_a = _acteur("pf-notif-approbateur", "approbateur", approbateur)
    assert lister_notifications(index, acteur_a)["non_lues"] == 0

    demande = _creer_demande_suppression(index, demandeur, code)
    assert demande["statut"] == "en_attente"

    boite = lister_notifications(index, acteur_a)
    assert boite["non_lues"] == 1
    notification = boite["notifications"][0]
    assert notification["type_notification"] == "nouvelle_demande"
    assert notification["lue"] is False
    assert notification["statut_demande"] == "en_attente"

    lue = marquer_notification_lue(index, acteur_a, int(notification["id_notification"]))
    assert lue["lue"] is True and lue["lue_le"]
    assert lister_notifications(index, acteur_a)["non_lues"] == 0
    # Le marquage est IDEMPOTENT : relire ne rouvre pas la notification.
    encore = marquer_notification_lue(index, acteur_a, int(notification["id_notification"]))
    assert encore["lue"] is True and encore["lue_le"] == lue["lue_le"]


def test_notifications_tout_lire(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    demandeur = _compte(index, "pf-notif-tout-demandeur", role="utilisateur")
    approbateur = _compte(index, "pf-notif-tout-approbateur")
    codes = sorted(base_recherche["fiches"])[:2]

    from seamtech_search.approbations import lister_notifications, marquer_notifications_lues

    for code in codes:
        _creer_demande_suppression(index, demandeur, code)
    acteur_a = _acteur("pf-notif-tout-approbateur", "approbateur", approbateur)
    boite = lister_notifications(index, acteur_a)
    assert boite["non_lues"] == 2, boite["non_lues"]

    resultat = marquer_notifications_lues(index, acteur_a)
    assert resultat["marquees_lues"] == 2
    assert resultat["non_lues"] == 0
    assert lister_notifications(index, acteur_a)["non_lues"] == 0
    # Deuxième appel : rien à marquer, aucune erreur.
    assert marquer_notifications_lues(index, acteur_a)["marquees_lues"] == 0


def test_notification_d_un_autre_compte_est_invisible_et_inaccessible(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    demandeur = _compte(index, "pf-notif-idor-demandeur", role="utilisateur")
    approbateur = _compte(index, "pf-notif-idor-approbateur")
    intrus = _compte(index, "pf-notif-idor-intrus", role="lecture")
    code = sorted(base_recherche["fiches"])[0]

    from seamtech_search.approbations import lister_notifications, marquer_notification_lue

    _creer_demande_suppression(index, demandeur, code)
    acteur_a = _acteur("pf-notif-idor-approbateur", "approbateur", approbateur)
    cible = int(lister_notifications(index, acteur_a)["notifications"][0]["id_notification"])

    acteur_intrus = _acteur("pf-notif-idor-intrus", "lecture", intrus)
    assert lister_notifications(index, acteur_intrus)["non_lues"] == 0
    with pytest.raises(HTTPException) as erreur:
        marquer_notification_lue(index, acteur_intrus, cible)
    # 404 et non 403 : l'existence de la ressource d'autrui n'est pas révélée.
    assert erreur.value.status_code == 404
    # La notification de l'approbateur est restée intacte.
    assert lister_notifications(index, acteur_a)["non_lues"] == 1


def test_notifications_exigent_un_compte_nominatif(base_recherche: dict[str, Any]) -> None:
    """Le jeton de service seul n'a pas de boîte : il n'y a pas d'identité."""
    index = base_recherche["index"]

    from seamtech_search.approbations import lister_notifications
    from seamtech_search.securite_requetes import ActeurRequete

    service = ActeurRequete(type_acteur="service", libelle_audit="service")
    with pytest.raises(HTTPException) as erreur:
        lister_notifications(index, service)
    assert erreur.value.status_code == 401
