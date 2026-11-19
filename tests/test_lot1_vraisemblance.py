"""Lot 1.2 — vraisemblance des champs.

Contrat du plan, appliqué à la lettre :

* **une valeur technique ne peut pas devenir un nom de client** ;
* **un bateau absent du référentiel est signalé, pas enregistré** ;
* **chaque refus est NOMMÉ et devient une anomalie**.

Une règle = un cas qui passe et un cas qui échoue. Les valeurs sont
synthétiques : aucun nom de client réel, aucun bateau réel.
"""

from __future__ import annotations

from seamtech_search.fiches.anomalies import evaluer_anomalies
from seamtech_search.fiches.modeles import Cotes, FicheExtraite
from seamtech_search.qualite.cartographie import cle_normalisation

# Référentiel de bateaux SYNTHÉTIQUE : trois modèles connus, rien de plus.
REFERENTIEL = {
    cle_normalisation("Voilier Fictif 42"),
    cle_normalisation("Bavaria 45"),
    cle_normalisation("First 30"),
}


def _fiche(**remplacements: object) -> FicheExtraite:
    """Fiche minimale, porteuse d'un jeu de cotes « finie » plausible."""
    donnees: dict[str, object] = {
        "code": "LOT1-VRAI-1",
        "client_nom": "Voilerie Fictive",
        "bateau_nom": "Voilier Fictif 42",
        "type_voile_libelle": "Grand-voile",
        "cotes": [Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    }
    donnees.update(remplacements)
    return FicheExtraite(**donnees)  # type: ignore[arg-type]


def _codes(fiche: FicheExtraite, **kwargs: object) -> set[str]:
    return {a.code for a in evaluer_anomalies(fiche, **kwargs)}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Règle (a) — une valeur technique ne peut pas devenir un nom de client
# ---------------------------------------------------------------------------


def test_nom_de_client_plausible_ne_leve_rien() -> None:
    """Cas qui PASSE : un vrai nom de client n'est pas refusé."""
    assert _codes(_fiche()) == set()


def test_client_nomme_comme_un_type_de_voile_est_refuse() -> None:
    """Cas qui ÉCHOUE : « Spinnaker » est une valeur technique, pas un client."""
    fiche = _fiche(client_nom="Spinnaker")
    assert "client_invraisemblable" in _codes(fiche)


def test_client_nomme_comme_une_cote_est_refuse() -> None:
    """Cas qui ÉCHOUE : « Guindant » est une cote, pas un client."""
    assert "client_invraisemblable" in _codes(_fiche(client_nom="Guindant"))


def test_client_nomme_comme_une_mesure_est_refuse() -> None:
    """Cas qui ÉCHOUE : « 12.50 m » est une mesure, pas un client."""
    assert "client_invraisemblable" in _codes(_fiche(client_nom="12.50 m"))


def test_client_nomme_comme_un_nombre_est_refuse() -> None:
    """Cas qui ÉCHOUE : « 101 » est un nombre, pas un client."""
    assert "client_invraisemblable" in _codes(_fiche(client_nom="101"))


def test_client_qui_contient_un_terme_technique_nest_pas_refuse() -> None:
    """Un nom qui CONTIENT un terme technique reste un nom — pas de refus en série."""
    assert "client_invraisemblable" not in _codes(_fiche(client_nom="Voilerie du Guindant"))


def test_client_vide_ne_leve_rien() -> None:
    """Pas de valeur, pas de refus : l'absence n'est pas une invraisemblance."""
    assert "client_invraisemblable" not in _codes(_fiche(client_nom=None))
    assert "client_invraisemblable" not in _codes(_fiche(client_nom="   "))


def test_casse_et_accents_ne_sauvent_pas_une_valeur_technique() -> None:
    """« SPINNAKER » et « spinnaker » sont la même valeur technique."""
    assert "client_invraisemblable" in _codes(_fiche(client_nom="SPINNAKER"))


def test_le_refus_est_nomme() -> None:
    """Exigence du plan : chaque refus est NOMMÉ — la valeur refusée est lisible."""
    fiche = _fiche(client_nom="Spinnaker")
    refus = [a for a in evaluer_anomalies(fiche) if a.code == "client_invraisemblable"]
    assert len(refus) == 1
    assert "Spinnaker" in refus[0].message
    assert refus[0].gravite == "forte"


# ---------------------------------------------------------------------------
# Règle (b) — un bateau absent du référentiel est signalé, pas enregistré
# ---------------------------------------------------------------------------


def test_bateau_connu_du_referentiel_ne_leve_rien() -> None:
    """Cas qui PASSE."""
    assert "bateau_hors_referentiel" not in _codes(
        _fiche(bateau_nom="Voilier Fictif 42"), referentiel_bateaux=REFERENTIEL
    )


def test_bateau_absent_du_referentiel_est_signale() -> None:
    """Cas qui ÉCHOUE : inconnu du référentiel → anomalie."""
    assert "bateau_hors_referentiel" in _codes(
        _fiche(bateau_nom="Modèle Inconnu 99"), referentiel_bateaux=REFERENTIEL
    )


def test_bateau_absent_est_reconnu_malgre_casse_et_accents() -> None:
    """« BAVARIA 45 » et « Bavaria 45 » : même clé, même référentiel."""
    assert "bateau_hors_referentiel" not in _codes(
        _fiche(bateau_nom="BAVARIA 45"), referentiel_bateaux=REFERENTIEL
    )


def test_sans_referentiel_transmis_le_controle_ne_sapplique_pas() -> None:
    """Il ne devine pas ce qu'il n'a pas reçu."""
    assert "bateau_hors_referentiel" not in _codes(_fiche(bateau_nom="Modèle Inconnu 99"))


def test_bateau_vide_ne_leve_rien() -> None:
    assert "bateau_hors_referentiel" not in _codes(
        _fiche(bateau_nom=None), referentiel_bateaux=REFERENTIEL
    )


def test_le_bateau_refuse_est_nomme() -> None:
    """Exigence du plan : le refus est NOMMÉ et dit qu'il n'est pas enregistré."""
    fiche = _fiche(bateau_nom="Modèle Inconnu 99")
    refus = [
        a
        for a in evaluer_anomalies(fiche, referentiel_bateaux=REFERENTIEL)
        if a.code == "bateau_hors_referentiel"
    ]
    assert len(refus) == 1
    assert "Modèle Inconnu 99" in refus[0].message
    assert "signalé" in refus[0].message


# ---------------------------------------------------------------------------
# Portée : la vraisemblance ne dépend pas des cotes
# ---------------------------------------------------------------------------


def test_vraisemblance_sapplique_meme_sans_cotes_finies() -> None:
    """Une fiche sans cotes ne doit PAS échapper au contrôle d'identité."""
    fiche = _fiche(cotes=[], client_nom="Spinnaker")
    codes = _codes(fiche)
    assert "client_invraisemblable" in codes
    # Le contrôle historique « aucun jeu de cotes finie » reste posé.
    assert "champ_manquant" in codes


def test_controles_geometriques_historiques_sont_conserves() -> None:
    """Non-régression : les contrôles RG16 d'origine continuent de s'appliquer."""
    fiche = _fiche(cotes=[Cotes(jeu="finie", slu_m=2.0, sle_m=4.0, sf_m=3.9)])
    assert "cotes_incoherentes" in _codes(fiche)
