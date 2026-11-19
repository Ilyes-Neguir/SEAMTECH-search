"""Comparaison de N voiles — attributs à gauche, une colonne par fiche.

Le lot 4b comparait DEUX voiles, colonne « A » contre colonne « B ». L'atelier
compare un GROUPE : trois voiles du même bateau, quatre du même type. Ce module
étend la comparaison à N fiches sans rien reconstruire : mêmes blocs, mêmes
états, mêmes règles d'honnêteté.

Les règles qui n'ont pas bougé, et que ces tests verrouillent à N fiches :

* un écart est une SOUSTRACTION. Il est affiché dès deux fiches, sans
  conditions, sans seuil, sans « à partir de 5 » : c'est de l'arithmétique ;
* une valeur manquante reste manquante. Aucun écart n'est fabriqué à partir de
  rien, et l'écart n'est pas « 0 » parce qu'on n'a pas lu la cote ;
* il n'y a ici AUCUN verdict de réutilisation et AUCUNE cohérence statistique :
  ce sont des écarts. Le verdict est la Fonction B (Lot 5), qui exige un
  effectif et l'écrit ;
* une référence est NOMMÉE. Un pourcentage sans cible ne veut rien dire :
  « +2 % » par rapport à quoi ? La réponse est dans la sortie.

Aucune base, aucun PDF : fonctions pures, exécutées à chaque validation.
"""

from __future__ import annotations

from typing import Any

import pytest

from seamtech_search.comparaison_voile import (
    ComparaisonRefusee,
    comparer_plusieurs,
)
from seamtech_search.extractions_voile import ExtractionVoile, MateriauExtractionVoile

MODELE = 42


def _voile(
    identifiant: int,
    *,
    gamme: str = "Ocean",
    type_voile: str = "Grand voile",
    boat_model_id: int | None = MODELE,
    slu: float | None = 10.0,
    sf: float | None = 3.5,
    spa: float | None = 25.0,
    grammage: float | None = 220.0,
    designation: str = "Dacron 220",
    plausible: bool = True,
) -> ExtractionVoile:
    return ExtractionVoile(
        id_extraction=identifiant,
        cle_idempotence=f"cle-{identifiant}",
        code_fiche=f"F{identifiant}",
        nom_fichier_source=f"fiche-{identifiant}.pdf",
        gabarit_code="FICHE_PORTANT_V1",
        methode_principale="fiche_extraite",
        type_voile=type_voile,
        gamme=gamme,
        modele_bateau_brut=f"Modele {boat_model_id}" if boat_model_id else None,
        boat_model_id=boat_model_id,
        slu_m=slu,
        sf_m=sf,
        spa_m2=spa,
        score_qualite=0.9,
        statut_plausibilite="plausible" if plausible else "implausible",
        materiaux=(
            [MateriauExtractionVoile(role="tissu_principal", niveau=1, designation=designation, grammage_g_m2=grammage)]
            if designation
            else []
        ),
    )


def _ligne(resultat: dict[str, Any], bloc: str, champ: str) -> dict[str, Any]:
    return next(ligne for ligne in resultat["blocs"][bloc] if ligne["champ"] == champ)


# ---------------------------------------------------------------------------
# Refus : une comparaison qui n'a pas de sens est refusée, pas affichée
# ---------------------------------------------------------------------------


def test_une_seule_fiche_est_refusee() -> None:
    with pytest.raises(ComparaisonRefusee, match="au moins deux"):
        comparer_plusieurs([_voile(1)])


def test_liste_vide_est_refusee() -> None:
    with pytest.raises(ComparaisonRefusee):
        comparer_plusieurs([])


def test_la_meme_fiche_deux_fois_est_refusee() -> None:
    with pytest.raises(ComparaisonRefusee, match="elle-même"):
        comparer_plusieurs([_voile(1), _voile(1)])


def test_au_dela_de_douze_colonnes_est_refuse() -> None:
    """Une table de 30 colonnes n'est pas lisible : on refuse, on n'étale pas."""
    with pytest.raises(ComparaisonRefusee, match="12"):
        comparer_plusieurs([_voile(i) for i in range(1, 14)])


# ---------------------------------------------------------------------------
# Référence : un pourcentage sans cible ne veut rien dire
# ---------------------------------------------------------------------------


def test_la_reference_par_defaut_est_la_premiere_colonne() -> None:
    resultat = comparer_plusieurs([_voile(1, slu=10.0), _voile(2, slu=11.0)])
    assert resultat["reference"]["rang"] == 0
    assert resultat["reference"]["id_extraction"] == 1


def test_la_reference_est_nommee_dans_la_sortie() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2)])
    assert resultat["reference"]["code_fiche"] == "F1"
    assert resultat["reference"]["libelle"]


def test_une_reference_explicite_deplace_les_ecarts() -> None:
    """Les écarts suivent la cible : +10 % de la première, −9,1 % de la dernière."""
    une, deux = _voile(1, slu=10.0), _voile(2, slu=11.0)
    depuis_premiere = _ligne(comparer_plusieurs([une, deux]), "dimensions", "slu_m")
    depuis_derniere = _ligne(comparer_plusieurs([une, deux], id_reference=2), "dimensions", "slu_m")
    assert depuis_premiere["deltas_pct"] == [None, 10.0]
    assert depuis_derniere["deltas_pct"] == [-9.09, None]


def test_une_reference_absente_du_groupe_est_signalee_et_tombe_sur_la_premiere() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2)], id_reference=999)
    assert resultat["reference"]["rang"] == 0
    assert any("999" in texte for texte in resultat["avertissements"])


# ---------------------------------------------------------------------------
# Écarts : de l'arithmétique, affichée dès deux fiches
# ---------------------------------------------------------------------------


def test_les_ecarts_sont_affiches_des_deux_fiches_sans_condition_d_effectif() -> None:
    resultat = comparer_plusieurs([_voile(1, slu=10.0), _voile(2, slu=10.5)])
    ligne = _ligne(resultat, "dimensions", "slu_m")
    assert ligne["deltas"] == [None, 0.5]
    assert ligne["deltas_pct"] == [None, 5.0]


def test_la_reference_n_a_pas_d_ecart_avec_elle_meme() -> None:
    ligne = _ligne(comparer_plusieurs([_voile(1), _voile(2), _voile(3)]), "dimensions", "slu_m")
    assert ligne["deltas"][0] is None


def test_les_longueurs_portent_l_ecart_en_centimetres() -> None:
    """L'atelier parle en cm : 0,05 m de guindant, c'est 5 cm sur la table."""
    ligne = _ligne(comparer_plusieurs([_voile(1, slu=10.0), _voile(2, slu=10.05)]), "dimensions", "slu_m")
    assert ligne["deltas_cm"] == [None, 5.0]


def test_une_surface_ne_porte_pas_d_ecart_en_centimetres() -> None:
    ligne = _ligne(comparer_plusieurs([_voile(1), _voile(2, spa=26.0)]), "dimensions", "spa_m2")
    assert ligne["deltas_cm"] == [None, None]


def test_l_etendue_et_les_bornes_sont_calculees_sur_le_groupe() -> None:
    groupe = [_voile(1, slu=10.0), _voile(2, slu=10.4), _voile(3, slu=9.7)]
    ligne = _ligne(comparer_plusieurs(groupe), "dimensions", "slu_m")
    assert ligne["min"] == 9.7
    assert ligne["max"] == 10.4
    assert ligne["etendue"] == 0.7


# ---------------------------------------------------------------------------
# Honnêteté : une valeur absente ne fabrique aucun écart
# ---------------------------------------------------------------------------


def test_une_cote_manquante_reste_manquante_et_ne_calcule_rien() -> None:
    ligne = _ligne(comparer_plusieurs([_voile(1, slu=10.0), _voile(2, slu=None)]), "dimensions", "slu_m")
    assert ligne["etats"] == ["reference", "manquant"]
    assert ligne["valeurs"][1] is None
    assert ligne["deltas"][1] is None
    assert ligne["deltas_pct"][1] is None


def test_l_identite_est_inconnue_et_non_vraie_quand_une_valeur_manque() -> None:
    """« Je ne sais pas » n'est pas « c'est identique »."""
    ligne = _ligne(comparer_plusieurs([_voile(1, gamme="Ocean"), _voile(2, gamme=None)]), "identification", "gamme")
    assert ligne["identique"] is None


def test_l_identite_est_vraie_quand_toutes_les_valeurs_sont_egales() -> None:
    groupe = [_voile(1, gamme="Ocean"), _voile(2, gamme="Ocean"), _voile(3, gamme="Ocean")]
    assert _ligne(comparer_plusieurs(groupe), "identification", "gamme")["identique"] is True


def test_l_identite_de_texte_ignore_la_casse_et_les_accents() -> None:
    groupe = [_voile(1, type_voile="Génois"), _voile(2, type_voile="genois")]
    assert _ligne(comparer_plusieurs(groupe), "identification", "type_voile")["identique"] is True


def test_l_identite_est_fausse_quand_une_seule_fiche_differ_e() -> None:
    groupe = [_voile(1, gamme="Ocean"), _voile(2, gamme="Ocean"), _voile(3, gamme="Racing")]
    ligne = _ligne(comparer_plusieurs(groupe), "identification", "gamme")
    assert ligne["identique"] is False


def test_l_effectif_est_ecrit_sur_chaque_ligne() -> None:
    """Le seuil statistique (≥ 5) se lit sur cette valeur, jamais en devinant."""
    ligne = _ligne(comparer_plusieurs([_voile(i) for i in range(1, 6)]), "dimensions", "slu_m")
    assert ligne["nb_fiches_avec_valeur"] == 5


# ---------------------------------------------------------------------------
# Avertissements : comparer ce qui n'est pas comparable se dit
# ---------------------------------------------------------------------------


def test_des_modeles_differents_sont_signales() -> None:
    resultat = comparer_plusieurs([_voile(1, boat_model_id=1), _voile(2, boat_model_id=2)])
    assert resultat["comparable"] is False
    assert any("modèle" in texte.lower() for texte in resultat["avertissements"])


def test_un_modele_non_lie_n_est_jamais_dit_comparable() -> None:
    resultat = comparer_plusieurs([_voile(1, boat_model_id=None), _voile(2, boat_model_id=None)])
    assert resultat["comparable"] is False
    assert any("modèle" in texte.lower() for texte in resultat["avertissements"])


def test_des_types_de_voile_differents_sont_signales() -> None:
    resultat = comparer_plusieurs([_voile(1, type_voile="Grand voile"), _voile(2, type_voile="Génois")])
    assert any("type" in texte.lower() for texte in resultat["avertissements"])


def test_meme_modele_et_meme_type_donne_comparable() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2), _voile(3)])
    assert resultat["comparable"] is True
    assert not [texte for texte in resultat["avertissements"] if "modèle" in texte.lower()]


def test_une_fiche_implausible_est_signalee() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2, plausible=False)])
    assert any("implausible" in texte.lower() for texte in resultat["avertissements"])


def test_une_seule_cote_commune_est_signalee() -> None:
    """Une cote seule ne permet pas de comparer utilement : on le dit."""
    resultat = comparer_plusieurs([_voile(1, sf=None, spa=None), _voile(2, sf=None, spa=None)])
    assert any("chevauchement" in texte.lower() for texte in resultat["avertissements"])


# ---------------------------------------------------------------------------
# Forme de la sortie : attributs à gauche, une colonne par fiche
# ---------------------------------------------------------------------------


def test_chaque_ligne_porte_autant_de_valeurs_que_de_fiches() -> None:
    resultat = comparer_plusieurs([_voile(i) for i in range(1, 5)])
    for bloc in resultat["blocs"].values():
        for ligne in bloc:
            assert len(ligne["valeurs"]) == 4, ligne["champ"]


def test_une_colonne_par_fiche_dans_l_ordre_demande() -> None:
    resultat = comparer_plusieurs([_voile(3), _voile(1), _voile(2)])
    assert [colonne["id_extraction"] for colonne in resultat["colonnes"]] == [3, 1, 2]


def test_les_quatre_blocs_sont_presents() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2)])
    assert set(resultat["blocs"]) == {"identification", "dimensions", "materiaux", "qualite"}


def test_la_synthese_nomme_le_plus_grand_ecart() -> None:
    resultat = comparer_plusieurs([_voile(1, slu=10.0, sf=3.5), _voile(2, slu=10.1, sf=3.6)])
    assert resultat["synthese"]["ecart_max_champ"] == "sf_m"
    assert resultat["synthese"]["ecart_max_pct"] == pytest.approx(2.86, abs=0.01)


def test_sans_aucune_cote_commune_la_synthese_ne_fabrique_pas_d_ecart_max() -> None:
    resultat = comparer_plusieurs([_voile(1, slu=None, sf=None, spa=None), _voile(2, slu=None, sf=None, spa=None)])
    assert resultat["synthese"]["ecart_max_pct"] is None
    assert resultat["synthese"]["ecart_max_champ"] is None


# ---------------------------------------------------------------------------
# Matériaux : une ligne par attribut, jamais une case piège
# ---------------------------------------------------------------------------


def test_les_materiaux_donnent_une_ligne_de_designation_et_une_de_grammage() -> None:
    bloc = comparer_plusieurs([_voile(1), _voile(2, grammage=240.0)])["blocs"]["materiaux"]
    champs = [ligne["champ"] for ligne in bloc]
    assert champs == ["materiau.designation", "materiau.grammage"]


def test_le_grammage_porte_un_ecart_en_pourcent() -> None:
    ligne = _ligne(comparer_plusieurs([_voile(1, grammage=200.0), _voile(2, grammage=220.0)]), "materiaux", "materiau.grammage")
    assert ligne["deltas_pct"] == [None, 10.0]


def test_un_materiau_absent_d_une_fiche_reste_manquant() -> None:
    resultat = comparer_plusieurs([_voile(1), _voile(2, designation="")])
    ligne = _ligne(resultat, "materiaux", "materiau.designation")
    assert ligne["etats"] == ["reference", "manquant"]


def test_deux_niveaux_de_materiaux_donnent_deux_paires_de_lignes() -> None:
    fiche = _voile(1)
    fiche.materiaux.append(MateriauExtractionVoile(role="renfort", niveau=2, designation="Renfort", grammage_g_m2=300.0))
    autre = _voile(2)
    autre.materiaux.append(MateriauExtractionVoile(role="renfort", niveau=2, designation="Renfort", grammage_g_m2=300.0))
    bloc = comparer_plusieurs([fiche, autre])["blocs"]["materiaux"]
    assert len(bloc) == 4
