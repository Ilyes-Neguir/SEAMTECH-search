"""Auto-validation par seuil — la règle qui remplace RG3.

Décision du commanditaire du 2026-11-14 : **l'auto-validation existe, et le
seuil est 90 %.** Score strictement supérieur à 0,90 et aucune anomalie
bloquante ⇒ la fiche arrive « valide ». Sinon « a_valider », comme avant.

Ces tests portent sur la DÉCISION, qui est une fonction pure : ils tournent
sans PostgreSQL. L'écriture en base est prouvée à part, dans
``test_auto_validation_persistance``, qui exige une base réelle.

Un test de seuil qui ne tourne que sur un serveur monté est un test qu'on
ne rejoue presque jamais — et un seuil mal vérifié, ici, publie dans
l'archive de confiance des fiches que personne n'a relues.
"""

from __future__ import annotations

import pytest

from seamtech_search.fiches.anomalies import CODES_BLOQUANTS
from seamtech_search.fiches.auto_validation import (
    GRAVITES_BLOQUANTES,
    SEUIL_AUTO_VALIDATION,
    STATUT_ATTENTE_HUMAINE,
    STATUT_AUTO_VALIDE,
    anomalies_bloquantes,
    decider_statut_arrivee,
    est_anomalie_bloquante,
)
from seamtech_search.fiches.modeles import Anomalie

# --- Les cas de la demande, un par un ---------------------------------------


def test_le_seuil_est_exactement_quatre_vingts_pour_cent() -> None:
    """La valeur est nommée et unique : changer de seuil doit être UN chiffre
    à modifier, pas une chasse dans le code."""
    assert SEUIL_AUTO_VALIDATION == 0.90


def test_score_au_dessus_du_seuil_sans_anomalie_est_auto_valide() -> None:
    """Cas 1 — score > 90 %, aucune anomalie ⇒ validée automatiquement."""
    decision = decider_statut_arrivee(0.913)
    assert decision.statut == STATUT_AUTO_VALIDE
    assert decision.auto_validee is True
    # Le pourquoi est dans la raison : sans lui, le journal dirait « valide »
    # sans dire que personne n'a validé.
    assert decision.raison.startswith("AUTO-VALIDATION —")
    assert "AUCUN approbateur humain" in decision.raison


def test_score_exactement_au_seuil_ne_s_auto_valide_pas() -> None:
    """Cas 2 — 90 % EXACTEMENT ne déclenche PAS l'auto-validation.

    La règle dit « strictement supérieur ». À 0,900 la fiche reste en attente
    humaine. Ce test fixe la frontière : un `>=` écrit par distraction
    publierait dans l'archive de confiance toutes les fiches à 90 % pile.
    """
    decision = decider_statut_arrivee(0.90)
    assert decision.statut == STATUT_ATTENTE_HUMAINE
    assert decision.auto_validee is False
    assert "≤" in decision.raison

    # Un cheveu au-dessus suffit — la frontière est réelle, pas décorative.
    assert decider_statut_arrivee(0.901).statut == STATUT_AUTO_VALIDE


def test_juste_en_dessous_et_juste_au_dessus() -> None:
    """La frontière doit être nette des deux côtés."""
    assert decider_statut_arrivee(0.899).statut == STATUT_ATTENTE_HUMAINE
    assert decider_statut_arrivee(0.900).statut == STATUT_ATTENTE_HUMAINE
    assert decider_statut_arrivee(0.901).statut == STATUT_AUTO_VALIDE
    assert decider_statut_arrivee(1.0).statut == STATUT_AUTO_VALIDE


def test_score_eleve_mais_anomalie_bloquante_reste_en_attente() -> None:
    """Cas 3 — l'anomalie PRIME sur le score."""
    decision = decider_statut_arrivee(
        0.95,
        [Anomalie(code="cotes_incoherentes", gravite="forte", message="guindant > bordure")],
    )
    assert decision.statut == STATUT_ATTENTE_HUMAINE
    assert "cotes_incoherentes" in decision.raison
    # Même avec un score parfait : l'anomalie bloque.
    assert (
        decider_statut_arrivee(1.0, [Anomalie(code="surface_incoherente", gravite="forte")]).statut
        == STATUT_ATTENTE_HUMAINE
    )


def test_score_faible_reste_en_attente_comme_avant() -> None:
    """Cas 4 — score ≤ 90 % : comportement IDENTIQUE à celui d'avant RG3."""
    for score in (0.0, 0.12, 0.5, 0.899, 0.90):
        decision = decider_statut_arrivee(score)
        assert decision.statut == STATUT_ATTENTE_HUMAINE, score
        assert decision.auto_validee is False, score


def test_score_absent_n_auto_valide_jamais() -> None:
    """Un score indisponible (aucun champ extrait) n'est pas un score élevé.

    C'est le piège évident d'une comparaison sur `float | None` : sans ce
    test, `None` serait traité comme 0 (donc en attente) — par accident.
    On fixe le comportement et la raison, pour qu'il ne change pas par
    effet de bord.
    """
    decision = decider_statut_arrivee(None)
    assert decision.statut == STATUT_ATTENTE_HUMAINE
    assert "indisponible" in decision.raison


# --- Ce qui est « bloquant », et ce qui ne l'est pas -------------------------


def test_chaque_code_bloquant_nomme_est_bloquant() -> None:
    """`CODES_BLOQUANTS` est la définition existante du produit — l'auto-
    validation doit la respecter, pas s'en inventer une autre."""
    for code in CODES_BLOQUANTS:
        assert est_anomalie_bloquante(Anomalie(code=code, gravite="forte")), code
        # Et le code bloque MÊME si la gravité ne le dit pas.
        assert est_anomalie_bloquante(Anomalie(code=code, gravite="faible")), code


@pytest.mark.parametrize(
    ("code", "gravite"),
    [
        ("client_invraisemblable", "forte"),
        ("champ_obligatoire_manquant", "forte"),
        ("cotes_incoherentes", "forte"),
        ("surface_incoherente", "forte"),
        ("orientations_pdf_mixtes", "forte"),
        ("orientation_pdf_non_verifiee", "forte"),
        ("gabarit_inconnu", "forte"),
    ],
)
def test_les_anomalies_fortes_du_pipeline_sont_bloquantes(code: str, gravite: str) -> None:
    """La liste exacte des anomalies « fortes » déjà émises par le pipeline.

    Une anomalie forte manquante ici signifierait qu'une fiche portant un
    défaut grave peut entrer dans l'archive de confiance sans relecture.
    Une anomalie forte AJOUTÉE au pipeline sans mise à jour de cette liste
    passerait inaperçue — c'est pourquoi ce test est explicite.
    """
    assert est_anomalie_bloquante(Anomalie(code=code, gravite=gravite))


@pytest.mark.parametrize(
    ("code", "gravite"),
    [
        ("bateau_hors_referentiel", "moyenne"),
        ("reference_discordante", "moyenne"),
        ("champ_manquant", "moyenne"),
        ("cote_hors_plage", "moyenne"),
        ("cotes_incoherentes", "moyenne"),
        ("type_voile_non_traduit", "moyenne"),
        ("unite_corrigee", "faible"),
    ],
)
def test_les_anomalies_non_bloquantes_ne_bloquent_pas(code: str, gravite: str) -> None:
    """Une anomalie « moyenne » n'empêche pas l'auto-validation.

    C'est le pendant du test précédent : si tout bloquait, le seuil de 90 %
    ne servirait plus à rien.
    """
    assert not est_anomalie_bloquante(Anomalie(code=code, gravite=gravite))
    assert decider_statut_arrivee(0.95, [Anomalie(code=code, gravite=gravite)]).auto_validee


def test_l_absence_de_fiche_de_fabrication_est_bloquante() -> None:
    """`fiche_de_fabrication_absente` est émise avec gravité « haute », un mot
    hors nomenclature (`Anomalie.gravite` annonce faible|moyenne|forte).

    C'est un ÉCHEC D'EXTRACTION majeur : la traiter comme non bloquante
    auto-validerait des fiches dont on n'a pas trouvé la fiche de
    fabrication. Signalé au commanditaire ; en attendant, elle bloque.
    """
    assert est_anomalie_bloquante(Anomalie(code="fiche_de_fabrication_absente", gravite="haute"))


def test_une_gravite_inconnue_ne_bloque_pas() -> None:
    """Garde-fou inverse : « haute » ne doit pas absorber toutes les gravités
    inconnues, sinon n'importe quel mot non documenté bloquerait — et
    l'auto-validation ne se déclencherait plus jamais."""
    for gravite in ("faible", "moyenne", "inconnue", "", "FORTE"):
        assert not est_anomalie_bloquante(Anomalie(code="quelque_chose", gravite=gravite)), gravite

    # Et réciproquement : les deux gravités reconnues bloquent bien. La
    # casse compte — « FORTE » ne doit pas être absorbée par erreur.
    for gravite in ("forte", "haute"):
        assert est_anomalie_bloquante(Anomalie(code="quelque_chose", gravite=gravite)), gravite

    # « haute » est traitée comme bloquante SANS faire partie de la
    # nomenclature documentée : c'est une exception assumée et signalée.
    assert "haute" not in GRAVITES_BLOQUANTES
    assert "forte" in GRAVITES_BLOQUANTES


def test_anomalies_bloquantes_conserve_l_ordre_et_filtre() -> None:
    anomalies = [
        Anomalie(code="reference_discordante", gravite="moyenne"),
        Anomalie(code="surface_incoherente", gravite="forte"),
        Anomalie(code="cote_hors_plage", gravite="moyenne"),
        Anomalie(code="gabarit_inconnu", gravite="forte"),
    ]
    retenues = anomalies_bloquantes(anomalies)
    assert [a.code for a in retenues] == ["surface_incoherente", "gabarit_inconnu"]


def test_la_raison_liste_les_anomalies_bloquantes() -> None:
    """Le journal doit dire LESQUELLES : « une anomalie bloque » sans la
    nommer obligerait l'exploitante à rouvrir la base pour comprendre."""
    decision = decider_statut_arrivee(
        0.99,
        [
            Anomalie(code="surface_incoherente", gravite="forte"),
            Anomalie(code="cotes_incoherentes", gravite="forte"),
            Anomalie(code="bateau_hors_referentiel", gravite="moyenne"),
        ],
    )
    assert "surface_incoherente" in decision.raison
    assert "cotes_incoherentes" in decision.raison
    # Et ne compte que les bloquantes.
    assert "2 anomalie(s) bloquante(s)" in decision.raison


# --- Traçabilité -------------------------------------------------------------


def test_une_auto_validation_ne_peut_pas_etre_confondue_avec_une_validation_humaine() -> None:
    """Item I2 : une action doit être rattachée à un utilisateur nommé. Une
    auto-validation n'a PAS d'utilisateur — le journal doit le DIRE, pas
    inventer un acteur.

    On ne peut pas prouver ici l'absence d'`id_utilisateur` en base (il faut
    PostgreSQL) : c'est l'objet de `test_auto_validation_persistance`. Ce
    test fixe que la RAISON produite par la règle porte la mention
    explicitement, pour qu'elle survive à un copier-coller du message.
    """
    decision = decider_statut_arrivee(0.91)
    assert decision.auto_validee
    assert "AUCUN approbateur humain" in decision.raison
    assert "décision automatique" in decision.raison


def test_la_raison_d_une_attente_humaine_ne_parle_pas_d_auto_validation() -> None:
    """Une fiche laissée en attente ne doit pas être journalisée comme auto-
    validée : le journal est lu par l'exploitante, pas par la machine."""
    for decision in (
        decider_statut_arrivee(0.5),
        decider_statut_arrivee(None),
        decider_statut_arrivee(0.99, [Anomalie(code="gabarit_inconnu", gravite="forte")]),
    ):
        assert not decision.raison.startswith("AUTO-VALIDATION —")
        assert decision.statut == STATUT_ATTENTE_HUMAINE


# --- Robustesse ---------------------------------------------------------------


def test_la_decision_est_pure() -> None:
    """Deux appels identiques rendent la même chose : la règle ne lit ni
    l'heure, ni la base, ni la configuration. Sans cela, rejouer une
    extraction donnerait un statut différent à l'identique."""
    anomalies = [Anomalie(code="cote_hors_plage", gravite="moyenne")]
    assert decider_statut_arrivee(0.9, anomalies) == decider_statut_arrivee(0.9, anomalies)


def test_la_decision_ne_modifie_pas_les_anomalies() -> None:
    """La règle lit, elle n'écrit pas : une anomalie ajoutée par la décision
    ferait croire à un contrôle qui n'a pas eu lieu."""
    anomalies = [Anomalie(code="reference_discordante", gravite="moyenne")]
    copie = list(anomalies)
    decider_statut_arrivee(0.95, anomalies)
    assert anomalies == copie


def test_un_score_est_toujours_dans_l_echelle_de_confiance() -> None:
    """Le seuil est exprimé sur l'échelle 0–1 de `ChampExtrait.confiance`
    (contrainte pydantic `ge=0.0, le=1.0`), pas sur 0–100. Un seuil de 80
    sur cette échelle auto-validerait tout ; le test le rend impossible."""
    from seamtech_search.fiches.modeles import ChampExtrait

    with pytest.raises(Exception):
        ChampExtrait(champ="x", confiance=80)
    assert 0.0 < SEUIL_AUTO_VALIDATION < 1.0
