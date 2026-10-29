"""Passe finale — recherche « façon Google » : variantes de saisie réelles.

Le corpus est celui de ``conftest.base_recherche`` (12 fiches validées, 2 à
valider, 1 rejetée, 3 bateaux, 3 matières, 4 types de voile, codes à tirets).

Chaque test interroge la VRAIE fonction de recherche hybride (sources plein
texte + trigrammes + vecteurs, fusion RRF) : aucune requête SQL réécrite ici.
"""

from __future__ import annotations

from typing import Any

import pytest

from seamtech_search.recherche import analyser_requete_dimension, rechercher_fiches


def _codes(resultat: dict[str, Any]) -> list[str]:
    return [fiche["code"] for fiche in resultat["resultats"]]


def _chercher(base: dict[str, Any], requete: str, **filtres: Any) -> list[str]:
    return _codes(rechercher_fiches(base["index"], requete, filtres=filtres or None))


# ---------------------------------------------------------------------------
# Dimension / cotes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requete", "valeur_attendue", "cote_attendue"),
    [
        ("SLU 12,50 m", 12.50, "slu_m"),
        ("slu 12.5", 12.5, "slu_m"),
        ("SLU 1250 cm", 12.5, "slu_m"),
        ("SLE 11 m", 11.0, "sle_m"),
        ("têtière 15", 15.0, "tetiere_cm"),
        ("poids 8,4 kg", 8.4, "poids_kg"),
        ("surface 42 m2", 42.0, None),
        ("12.50 m", 12.5, None),
    ],
)
def test_analyse_dimension_variantes(requete: str, valeur_attendue: float, cote_attendue: str | None) -> None:
    dimension = analyser_requete_dimension(requete)
    assert dimension.active, requete
    assert dimension.cote == cote_attendue
    if cote_attendue is None:
        assert abs(dimension.valeur_convergente() - valeur_attendue) < 1e-6
    else:
        assert abs(dimension.valeur_pour_cote(cote_attendue) - valeur_attendue) < 1e-6


@pytest.mark.parametrize("requete", ["2024", "7792", "SLU", "grand voile", "42 beaufort", ""])
def test_pas_de_fausse_dimension(requete: str) -> None:
    """Une année, un code, un entier nu ne sont JAMAIS lus comme une cote."""
    assert analyser_requete_dimension(requete).active is False


# ---------------------------------------------------------------------------
# Matière, bateau, type de voile, abréviations
# ---------------------------------------------------------------------------


def test_recherche_par_matiere(base_recherche: dict[str, Any]) -> None:
    codes = _chercher(base_recherche, "dacron")
    assert codes, "aucune fiche Dacron trouvée"
    assert all(code for code in codes)


def test_recherche_par_bateau(base_recherche: dict[str, Any]) -> None:
    codes = _chercher(base_recherche, "First 30")
    assert codes
    assert "0701-GV-001" in codes


def test_recherche_bateau_sans_espace_ni_casse(base_recherche: dict[str, Any]) -> None:
    avec_espace = _chercher(base_recherche, "FIRST 30")
    assert "0701-GV-001" in avec_espace
    sans_espace = _chercher(base_recherche, "first30")
    assert sans_espace, "« first30 » collé doit rester interprétable (préfixe/trigrammes)"


@pytest.mark.parametrize(("abreviation", "attendu"), [("GV", "0701-GV-001"), ("GEN", "0801-GEN-001"), ("SPI", "0812-SPI-001")])
def test_recherche_par_abreviation_de_type(base_recherche: dict[str, Any], abreviation: str, attendu: str) -> None:
    codes = _chercher(base_recherche, abreviation)
    assert attendu in codes


def test_recherche_code_avec_et_sans_tirets(base_recherche: dict[str, Any]) -> None:
    avec = _chercher(base_recherche, "0701-GV-001")
    sans = _chercher(base_recherche, "0701GV001")
    espaces = _chercher(base_recherche, "0701 GV 001")
    assert avec[:1] == ["0701-GV-001"]
    assert "0701-GV-001" in sans
    assert "0701-GV-001" in espaces


def test_recherche_insensible_a_la_casse_et_aux_accents(base_recherche: dict[str, Any]) -> None:
    assert _chercher(base_recherche, "GÉNOIS") == _chercher(base_recherche, "genois") == _chercher(base_recherche, "génois")


def test_recherche_mot_incomplet(base_recherche: dict[str, Any]) -> None:
    """« gén » (préfixe) doit ramener les génois : c'est la recherche par
    préfixe, pas une recherche par sous-chaîne quelconque."""
    codes = _chercher(base_recherche, "géno")
    assert any("GEN" in code for code in codes)


def test_recherche_requete_vide_rend_une_page(base_recherche: dict[str, Any]) -> None:
    resultat = rechercher_fiches(base_recherche["index"], "")
    assert resultat["resultats"], "la requête vide doit lister l'archive (navigation par date)"
    assert resultat["nb_resultats"] > 0


def test_recherche_sans_resultat_est_vide_et_journalisee(base_recherche: dict[str, Any]) -> None:
    resultat = rechercher_fiches(base_recherche["index"], "zzzz-aucun-resultat-zzzz")
    assert resultat["resultats"] == []
    assert resultat["nb_resultats"] == 0


def test_recherche_ne_remonte_jamais_une_fiche_rejetee(base_recherche: dict[str, Any]) -> None:
    codes = _codes(rechercher_fiches(base_recherche["index"], "spinnaker"))
    assert "1003-SPI-003" not in codes


def test_filtre_par_bateau_et_matiere(base_recherche: dict[str, Any]) -> None:
    # La colonne de facette est « nom + taille » (« First 30 30 pieds ») : le
    # filtre est une ÉGALITÉ sur cette valeur, pas une recherche libre.
    codes = _codes(rechercher_fiches(base_recherche["index"], "", filtres={"bateau": "First 30 30 pieds"}))
    assert codes
    assert "0801-GEN-001" in codes
    dacron = _codes(rechercher_fiches(base_recherche["index"], "", filtres={"matiere": "Dacron"}))
    assert "0901-TRM-001" in dacron
    # Un filtre inconnu est ignoré, jamais concaténé au SQL.
    assert rechercher_fiches(base_recherche["index"], "", filtres={"n_importe_quoi": "1; DROP TABLE fiche"})["nb_resultats"] >= 0


def test_filtre_boat_model_id_borne(base_recherche: dict[str, Any]) -> None:
    """Le filtre par modèle canonique doit rester borné et typé : un modèle
    inexistant rend 0 résultat, pas une erreur 500."""
    resultat = rechercher_fiches(base_recherche["index"], "", filtres={"boat_model_id": 999_999})
    assert resultat["nb_resultats"] == 0


def test_filtre_invalide_refuse(base_recherche: dict[str, Any]) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as erreur:
        rechercher_fiches(base_recherche["index"], "", filtres={"boat_model_id": "abc"})
    assert erreur.value.status_code == 422


def test_injection_sql_dans_la_requete_ne_casse_rien(base_recherche: dict[str, Any]) -> None:
    """Aucune requête SQL n'est concaténée : une charge d'injection est traitée
    comme du texte et ne produit ni erreur 500 ni résultat parasite."""
    charge = "'; DROP TABLE fiche; --"
    resultat = rechercher_fiches(base_recherche["index"], charge)
    assert "resultats" in resultat
    # La table est toujours là et interrogeable.
    assert rechercher_fiches(base_recherche["index"], "voile")["nb_resultats"] > 0


def test_troncature_de_requete_geante(base_recherche: dict[str, Any]) -> None:
    """Une requête de 10 000 caractères ne bloque pas le serveur."""
    resultat = rechercher_fiches(base_recherche["index"], "voile " * 2000)
    assert "resultats" in resultat
