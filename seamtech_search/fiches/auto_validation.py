"""Statut d'arrivée d'une fiche : auto-validation au-dessus d'un seuil.

Cette règle REMPLACE **RG3** (« le dépôt ne valide jamais »), qui posait le
statut d'arrivée à ``a_valider`` sans condition. Décision du commanditaire
du 2026-11-14 : **l'auto-validation existe, et le seuil est 80 %.**

Nouvelle règle
--------------

* Le score de confiance de l'extraction est **strictement supérieur à 80 %**
  ET aucune anomalie bloquante n'est détectée → la fiche arrive en
  ``valide``, sans intervention de l'approbateur.
* Sinon (score ≤ 80 %, ou anomalie bloquante) → la fiche reste
  ``a_valider`` : comportement identique à celui d'avant.

Le seuil est STRICTEMENT supérieur : un score de **exactement** 0,800 ne
déclenche PAS l'auto-validation. C'est le libellé de la décision
(« strictement supérieur à 80 % »), appliqué littéralement.

Pourquoi un module séparé
-------------------------

La règle est une fonction PURE du score et des anomalies : elle ne touche
ni la base, ni le réseau. C'est ce qui permet de la tester sans PostgreSQL
— et c'est nécessaire, car une règle métier qu'on ne peut prouver que sur
un serveur monté est une règle qu'on ne prouve presque jamais.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from seamtech_search.fiches.anomalies import CODES_BLOQUANTS
from seamtech_search.fiches.modeles import Anomalie

# ---------------------------------------------------------------------------
# Le seuil. UN SEUL CHIFFRE À MODIFIER.
#
# Exprimé sur l'échelle de ``ChampExtrait.confiance`` (0,0 → 1,0), celle de
# ``FicheExtraite.score_qualite()`` (moyenne des confiances, arrondie à 3
# décimales) et de la colonne ``fiche.score_qualite`` (NUMERIC(4,3)).
# Ne pas le confondre avec un pourcentage entier : 80 % s'écrit 0,80 ici.
# ---------------------------------------------------------------------------
SEUIL_AUTO_VALIDATION: float = 0.80

STATUT_AUTO_VALIDE = "valide"
STATUT_ATTENTE_HUMAINE = "a_valider"

# ---------------------------------------------------------------------------
# Ce qui compte comme « anomalie bloquante ».
#
# On ne invente RIEN : deux notions existaient déjà dans le code, et on les
# réutilise telles quelles.
#
# 1. ``anomalies.CODES_BLOQUANTS`` — ensemble nommé et documenté (« une
#    anomalie bloquante envoie la fiche en revue, elle n'est pas simplement
#    relue »). Il contient aujourd'hui un seul code :
#    ``champ_obligatoire_manquant`` (gravité « forte »).
#
# 2. La gravité « forte ». Elle sert DÉJÀ de signal bloquant ailleurs dans
#    le produit, sous une forme identique :
#      - ``extractions_voile.py`` : ``anomalie.gravite == "forte"`` ⇒
#        statut « implausible » ;
#      - ``liaison_voile.py`` : ``a.gravite == "forte"`` ⇒ idem.
#    S'en servir ici, c'est aligner l'auto-validation sur le jugement que le
#    produit porte déjà sur ces anomalies — pas introduire un troisième avis.
#
# Les codes concernés, tous déjà émis par le pipeline :
#   client_invraisemblable      (forte)   anomalies.py
#   champ_obligatoire_manquant  (forte)   anomalies.py  — aussi CODES_BLOQUANTS
#   cotes_incoherentes          (forte)   anomalies.py  — émise aussi en « moyenne »
#   surface_incoherente         (forte)   anomalies.py
#   orientations_pdf_mixtes     (forte)   extraction.py
#   orientation_pdf_non_verifiee(forte)   extraction.py
#   gabarit_inconnu             (forte)   extraction.py
#
# SONT DONC NON BLOQUANTES (« moyenne »), et une fiche qui ne porte QUE
# celles-ci peut s'auto-valider si son score dépasse le seuil :
#   bateau_hors_referentiel, reference_discordante, champ_manquant,
#   cote_hors_plage, cotes_incoherentes (en moyenne), type_voile_non_traduit
# ---------------------------------------------------------------------------
GRAVITES_BLOQUANTES: frozenset[str] = frozenset({"forte"})

# ⚠️ INCOHÉRENCE CONSTATÉE, traitée et signalée (non corrigée : hors périmètre).
#
# ``fiche_de_fabrication_absente`` (reprise.py) est émise avec
# ``gravite="haute"`` — un mot que le modèle d'anomalie ne documente pas
# (``Anomalie.gravite`` annonce « faible | moyenne | forte »). « haute » est
# manifestement le même intend que « forte », et c'est un échec d'extraction
# majeur : la traiter comme NON bloquante auto-validerait des fiches dont on
# n'a pas trouvé la fiche de fabrication. Elle est donc comptée comme
# bloquante, avec un test qui fixe ce choix.
#
# À UNIFIER À LA SOURCE (reprise.py) quand le commanditaire le décidera :
# deux mots pour la même gravité est un piège pour le prochain lecteur.
GRAVITES_BLOQUANTES_HORS_NOMENCLATURE: frozenset[str] = frozenset({"haute"})

_GRAVITES_BLOQUANTES_EFFECTIVES: frozenset[str] = GRAVITES_BLOQUANTES | GRAVITES_BLOQUANTES_HORS_NOMENCLATURE


def est_anomalie_bloquante(anomalie: Anomalie) -> bool:
    """Une anomalie bloquante interdit l'auto-validation, quel que soit le score."""
    return anomalie.code in CODES_BLOQUANTS or anomalie.gravite in _GRAVITES_BLOQUANTES_EFFECTIVES


def anomalies_bloquantes(anomalies: Iterable[Anomalie]) -> list[Anomalie]:
    """Les anomalies bloquantes, dans l'ordre où elles ont été constatées."""
    return [anomalie for anomalie in anomalies if est_anomalie_bloquante(anomalie)]


@dataclass(frozen=True)
class DecisionArrivee:
    """Le statut d'arrivée, et POURQUOI — le pourquoi va au journal."""

    statut: str
    raison: str

    @property
    def auto_validee(self) -> bool:
        return self.statut == STATUT_AUTO_VALIDE


def _formater_score(score: float | None) -> str:
    return "indisponible" if score is None else f"{score:.3f}"


def decider_statut_arrivee(
    score_qualite: float | None,
    anomalies: Sequence[Anomalie] = (),
) -> DecisionArrivee:
    """Applique la règle. Fonction pure : ni base, ni réseau, ni effet de bord.

    ``score_qualite`` est celui de :meth:`FicheExtraite.score_qualite` — la
    moyenne des confiances des champs lus, ou ``None`` quand aucun champ n'a
    de valeur. Un score absent n'est pas un score élevé : il ne peut pas
    déclencher d'auto-validation.
    """
    bloquantes = anomalies_bloquantes(anomalies)

    if score_qualite is None:
        return DecisionArrivee(
            STATUT_ATTENTE_HUMAINE,
            "score indisponible (aucun champ extrait) : le seuil d'auto-validation "
            f"({SEUIL_AUTO_VALIDATION:.2f}) n'est pas atteint",
        )

    if bloquantes:
        # L'anomalie PRIME sur le score : c'est le point explicite de la règle.
        # Une extraction très confiante sur une géométrie incohérente reste
        # une géométrie incohérente — le score mesure la lecture, pas le vrai.
        detail = ", ".join(f"{a.code} ({a.gravite})" for a in bloquantes[:5])
        return DecisionArrivee(
            STATUT_ATTENTE_HUMAINE,
            f"score {_formater_score(score_qualite)} au-dessus du seuil "
            f"{SEUIL_AUTO_VALIDATION:.2f}, mais {len(bloquantes)} anomalie(s) "
            f"bloquante(s) l'emportent : {detail}",
        )

    if score_qualite > SEUIL_AUTO_VALIDATION:
        return DecisionArrivee(
            STATUT_AUTO_VALIDE,
            f"AUTO-VALIDATION — score {_formater_score(score_qualite)} strictement "
            f"supérieur au seuil {SEUIL_AUTO_VALIDATION:.2f} et aucune anomalie "
            "bloquante ; décision automatique de la chaîne d'extraction, "
            "AUCUN approbateur humain n'est intervenu",
        )

    return DecisionArrivee(
        STATUT_ATTENTE_HUMAINE,
        f"score {_formater_score(score_qualite)} ≤ seuil "
        f"{SEUIL_AUTO_VALIDATION:.2f} (l'auto-validation exige strictement "
        "supérieur)",
    )
