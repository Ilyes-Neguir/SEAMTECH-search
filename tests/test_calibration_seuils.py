"""Lot 3 — calibrer les seuils de validation automatique sur des fiches réelles.

État des lieux, et c'est un blocage : ``config/seuils_confiance.json`` porte
``calibre: false`` et ``fiches_reelles_utilisees: 0``. Tant que cette preuve
manque, la validation groupée est INTERDITE — et elle a raison de l'être : sans
calibration, entériner dix mille fiches d'un coup reviendrait à valider une
erreur systématique du gabarit à l'échelle de l'archive.

Ce module fabrique la preuve. Deux idées le tiennent :

1. **On ne calibre pas une confiance avec des confiances.** Savoir que le moteur
   s'accorde 0,92 n'apprend rien sur son exactitude. Il faut la VÉRITÉ : parmi
   les champs qu'un humain a arbitrés, lesquels étaient justes ? La calibration
   compare donc la confiance annoncée au sort réel du champ.

2. **La preuve précède la décision.** Tant que l'échantillon est trop petit, le
   module refuse de proposer un seuil. Il ne propose JAMAIS un chiffre qu'il ne
   peut pas soutenir — un seuil inventé est pire qu'un seuil absent, parce qu'il
   a l'air d'une mesure.

Un champ non corrigé sur une fiche validée n'est pas prouvé exact : il est
accepté. C'est la meilleure preuve disponible sans relecture intégrale, et le
module le dit au lieu de le masquer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seamtech_search.fiches.calibration import (
    EFFECTIF_MIN_PALIER,
    MIN_FICHES_CALIBRATION,
    CalibrationRefusee,
    Observation,
    appliquer_calibration,
    paliers_exactitude,
    proposition_famille,
    seuil_recommande,
)


def _observations(couples: list[tuple[float, bool]]) -> list[Observation]:
    return [Observation(confiance=confiance, correct=correct) for confiance, correct in couples]


# ---------------------------------------------------------------------------
# Paliers : ce que les chiffres disent, sans fard
# ---------------------------------------------------------------------------


def test_un_palier_compte_les_champs_au_dessus_de_lui() -> None:
    observations = _observations(
        [(0.80, True), (0.90, True), (0.92, True), (0.95, True), (0.97, True), (0.99, True)]
    )
    palier = next(p for p in paliers_exactitude(observations, (0.85,)) if p["seuil"] == 0.85)
    assert palier["nb"] == 5
    assert palier["nb_corrects"] == 5
    assert palier["exactitude"] == 1.0
    assert palier["effectif_suffisant"] is True


def test_un_palier_sans_assez_de_monde_ne_donne_pas_d_exactitude() -> None:
    """Trois champs ne font pas une exactitude : on affiche l'effectif, pas un pourcentage."""
    observations = _observations([(0.90, True), (0.91, True), (0.92, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.90,)) if p["seuil"] == 0.90)
    assert palier["nb"] == 3
    assert palier["exactitude"] is None
    assert palier["effectif_suffisant"] is False


def test_l_exactitude_compte_les_erreurs() -> None:
    observations = _observations([(0.95, True), (0.96, False), (0.97, True), (0.98, True), (0.99, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.95,)) if p["seuil"] == 0.95)
    # 5 champs, dont un corrigé par un humain : 80 %, pas 100 %.
    assert palier["exactitude"] == 0.8


def test_un_palier_vide_ne_fabrique_pas_d_exactitude() -> None:
    observations = _observations([(0.50, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.95,)) if p["seuil"] == 0.95)
    assert palier["nb"] == 0
    assert palier["exactitude"] is None


# ---------------------------------------------------------------------------
# Seuil recommandé : le plus bas que les preuves soutiennent
# ---------------------------------------------------------------------------


def test_le_seuil_est_le_plus_bas_que_les_preuves_soutiennent() -> None:
    """Au-dessus de 0,90 tout est juste et l'échantillon suffit : 0,90."""
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True)]
    )
    assert seuil_recommande(observations) == 0.9


def test_une_erreur_sans_preuve_au_dessus_fait_refuser_le_seuil() -> None:
    """
    Un champ faux à 0,95 et rien de solide au-dessus : le module REFUSE.

    Proposer 0,96 sur la foi d'une seule observation au-dessus serait inventer
    une mesure. Le refus est la bonne réponse, et c'est la plus difficile à
    tenir quand on veut que ça avance.
    """
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True), (0.95, False)]
    )
    assert seuil_recommande(observations) is None


def test_avec_assez_de_preuves_au_dessus_le_seuil_passe_dessus_l_erreur() -> None:
    """Même erreur, mais cinq champs justes au-dessus : le seuil se cale sur eux."""
    observations = _observations(
        [
            (0.90, True),
            (0.91, True),
            (0.92, True),
            (0.93, True),
            (0.94, True),
            (0.95, False),
            (0.96, True),
            (0.97, True),
            (0.98, True),
            (0.99, True),
            (0.995, True),
        ]
    )
    recommande = seuil_recommande(observations)
    assert recommande == 0.96
    assert recommande > 0.95


def test_sans_preuve_suffisante_aucun_seuil_n_est_propose() -> None:
    """Deux observations ne prouvent rien : pas de seuil, et on le dit."""
    assert seuil_recommande(_observations([(0.99, True), (0.98, True)])) is None


def test_sans_observation_aucun_seuil_n_est_propose() -> None:
    assert seuil_recommande([]) is None


def test_le_seuil_est_toujours_une_confiance_reellement_observee() -> None:
    """On ne sort pas un chiffre du chapeau : le seuil est une valeur vue."""
    observations = _observations([(0.87, True)] * 6)
    assert seuil_recommande(observations) in {o.confiance for o in observations}


def test_le_seuil_effectif_minimal_est_celui_affiche_partout() -> None:
    assert EFFECTIF_MIN_PALIER == 5


# ---------------------------------------------------------------------------
# Proposition : la décision, avec ses raisons
# ---------------------------------------------------------------------------


def test_une_famille_assez_riche_propose_un_seuil_avec_ses_preuves() -> None:
    observations = _observations([(0.92, True), (0.93, True), (0.94, True), (0.95, True), (0.96, True), (0.97, True)])
    proposition = proposition_famille("structurels", 0.98, observations)
    assert proposition["applicable"] is True
    assert proposition["seuil_propose"] == 0.92
    assert proposition["effectif"] == 6
    assert proposition["exactitude"] == 1.0


def test_une_famille_pauvre_refuse_de_se_calibrer() -> None:
    proposition = proposition_famille("cotes", 0.95, _observations([(0.99, True)]))
    assert proposition["applicable"] is False
    assert proposition["seuil_propose"] is None
    assert proposition["raison"]


def test_jamais_de_seuil_sans_exactitude_parfaite_mesuree() -> None:
    """
    Le garde-fou central : si un seul champ s'est révélé faux au-dessus du
    seuil retenu, le seuil n'est pas proposé. Une calibration qui laisse passer
    une erreur connue est pire que pas de calibration.
    """
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True), (0.99, False)]
    )
    proposition = proposition_famille("structurels", 0.98, observations)
    if proposition["applicable"]:
        seuil = proposition["seuil_propose"]
        assert seuil > 0.99, "un seuil sous l'erreur connue laisserait passer cette erreur"


def test_la_proposition_rappelle_le_seuil_actuel() -> None:
    """Savoir d'où l'on part fait partie de la preuve : 0,98 → 0,92 ne se lit pas seul."""
    observations = _observations([(0.92, True)] * 6)
    assert proposition_famille("structurels", 0.98, observations)["seuil_actuel"] == 0.98


def test_la_lecture_dit_l_effectif_et_la_consequence() -> None:
    observations = _observations([(0.92, True)] * 6)
    lecture = proposition_famille("structurels", 0.98, observations)["lecture"]
    assert "6" in lecture
    assert "0.92" in lecture or "0,92" in lecture


# ---------------------------------------------------------------------------
# Écriture : « calibre: true » ne se décrète pas, il se mérite
# ---------------------------------------------------------------------------


def test_ecrire_refuse_sous_le_minimum_de_fiches_reelles(tmp_path: Path) -> None:
    """
    Le garde-fou du garde-fou : déclarer les seuils calibrés sur trois fiches
    ouvrirait la validation groupée sur la foi de trois exemples.
    """
    propositions = [proposition_famille("structurels", 0.98, _observations([(0.92, True)] * 6))]
    with pytest.raises(CalibrationRefusee, match="fiche"):
        appliquer_calibration(tmp_path / "seuils.json", propositions, nb_fiches=MIN_FICHES_CALIBRATION - 1)


def test_ecrire_pose_la_preuve_a_cote_du_seuil(tmp_path: Path) -> None:
    """« calibre: true » sans l'effectif et la date serait une opinion déguisée."""
    chemin = tmp_path / "seuils.json"
    propositions = [proposition_famille("structurels", 0.98, _observations([(0.92, True)] * 6))]
    appliquer_calibration(chemin, propositions, nb_fiches=MIN_FICHES_CALIBRATION)
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    assert donnees["calibre"] is True
    assert donnees["fiches_reelles_utilisees"] == MIN_FICHES_CALIBRATION
    assert donnees["calibre_le"]
    famille = donnees["familles"]["structurels"]
    assert famille["seuil_passage_direct"] == 0.92
    # La preuve voyage AVEC le seuil : personne ne lit un chiffre seul.
    assert famille["preuve"]["effectif"] == 6
    assert famille["preuve"]["seuil_precedent"] == 0.98


def test_ecrire_n_applique_pas_une_famille_refusee(tmp_path: Path) -> None:
    """Une famille sans preuves garde son seuil actuel : on ne comble pas un trou."""
    chemin = tmp_path / "seuils.json"
    chemin.write_text(json.dumps({"familles": {"cotes": {"seuil_passage_direct": 0.95}}}), encoding="utf-8")
    appliquer_calibration(
        chemin,
        [proposition_famille("cotes", 0.95, _observations([(0.99, True)]))],
        nb_fiches=MIN_FICHES_CALIBRATION,
    )
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    assert donnees["familles"]["cotes"]["seuil_passage_direct"] == 0.95
    assert "preuve" not in donnees["familles"]["cotes"]
