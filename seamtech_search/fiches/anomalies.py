"""Contrôles de cohérence RG16 — indépendants de la confiance (Lot B).

RG16 : une valeur peut être lue avec une confiance maximale et rester
TECHNIQUEMENT INCOHÉRENTE (surface absurde, cotes qui ne s'ordonnent pas,
valeur hors des plages physiques du domaine). Ces contrôles s'exécutent
toujours, même à confiance 1.0 — c'est le filet indépendant du gabarit.

Chaque anomalie produite arrive en base dans ``fiche_anomalie`` et bloque la
voie « passage direct » (routage §10.3).
"""

from __future__ import annotations

import logging

from seamtech_search.fiches.modeles import Anomalie, Cotes, FicheExtraite

LOGGER = logging.getLogger("seamtech_search.fiches.anomalies")

# Plages physiques plausibles (mètres, m², cm, kg) — points de départ du
# §10.3, à caler sur les fiches réelles ; une cote hors plage est suspecte,
# pas forcément fausse (gravite « moyenne », à confirmer par l'opérateur).
PLAGES_METRES = {"slu_m": (0.5, 30.0), "sle_m": (0.5, 30.0), "sf_m": (0.2, 12.0), "shw_m": (0.2, 12.0)}
PLAGES_AUTRES = {"spa_m2": (0.5, 300.0), "tetiere_cm": (0.5, 60.0), "poids_kg": (0.02, 30.0)}

# Cohérence surface (spi asymétrique et symétrique) : SPA ≈ 0,5 × SLU × SLE ×
# facteur de forme ; on tolère un facteur entre 0,55 et 1,30. Au-delà, la
# surface ne peut pas provenir de ces cotes — anomalie « forte ».
FACTEUR_SURFACE_MIN = 0.55
FACTEUR_SURFACE_MAX = 1.30

# Types de voile où SLU > SLE > SF est attendu (portants) ; les interfaces
# (génois, grand-voile) ordonnent plutôt SLU > SHW > SF — SLU > SF reste vrai
# pour tous, c'est le contrôle minimal commun.
FAMILIES_PORTANT = {"spi", "spinnaker"}


def evaluer_anomalies(fiche: FicheExtraite) -> list[Anomalie]:
    """RG16 : contrôles de cohérence, exécutés à TOUTE confiance."""
    anomalies: list[Anomalie] = []
    finie = next((c for c in fiche.cotes if c.jeu == "finie"), None)
    if finie is None:
        anomalies.append(
            Anomalie(code="champ_manquant", gravite="moyenne", message="Aucun jeu de cotes « finie » lu sur la fiche.")
        )
        return anomalies

    anomalies.extend(_controles_plages(finie))
    anomalies.extend(_controle_ordre_cotes(fiche, finie))
    anomalies.extend(_controle_surface(fiche, finie))
    for anomalie in anomalies:
        LOGGER.debug("Anomalie RG16 (%s) sur fiche %s : %s", anomalie.code, fiche.code or "?", anomalie.message)
    return anomalies


def _controles_plages(cotes: Cotes) -> list[Anomalie]:
    """Chaque cote connue doit tenir dans sa plage physique (m / m² / cm / kg)."""
    anomalies: list[Anomalie] = []
    for champ, (mini, maxi) in {**PLAGES_METRES, **PLAGES_AUTRES}.items():
        valeur = getattr(cotes, champ)
        if valeur is None:
            continue
        if valeur < mini or valeur > maxi:
            anomalies.append(
                Anomalie(
                    code="cote_hors_plage",
                    gravite="moyenne",
                    message=f"{champ} = {valeur} hors de la plage plausible [{mini}, {maxi}].",
                )
            )
    return anomalies


def _controle_ordre_cotes(fiche: FicheExtraite, cotes: Cotes) -> list[Anomalie]:
    """Contrôle minimal commun : guindant > bordure quand les deux sont lus."""
    anomalies: list[Anomalie] = []
    if cotes.slu_m is not None and cotes.sf_m is not None and cotes.slu_m <= cotes.sf_m:
        anomalies.append(
            Anomalie(
                code="cotes_incoherentes",
                gravite="forte",
                message=f"Guindant (SLU {cotes.slu_m} m) ≤ bordure (SF {cotes.sf_m} m) : impossible.",
            )
        )
    famille = (fiche.type_voile_libelle or "").lower()
    if any(portant in famille for portant in FAMILIES_PORTANT):
        if cotes.slu_m is not None and cotes.sle_m is not None and cotes.slu_m <= cotes.sle_m:
            anomalies.append(
                Anomalie(
                    code="cotes_incoherentes",
                    gravite="moyenne",
                    message=f"Portant : SLU ({cotes.slu_m} m) attendu > SLE ({cotes.sle_m} m).",
                )
            )
    return anomalies


def _controle_surface(fiche: FicheExtraite, cotes: Cotes) -> list[Anomalie]:
    """Surface cohérente avec les cotes (triangle SLU × SLE / 2, facteur toléré).

    Appliqué aux portants (le facteur de forme d'un spi se tient entre 0,55 et
    1,30) ; pour les interfaces la géométrie diffère (luff + foot) : contrôle
    de plage seulement, pas de ratio — documenté, pas oublié.
    """
    famille = (fiche.type_voile_libelle or "").lower()
    if not any(portant in famille for portant in FAMILIES_PORTANT):
        return []
    if cotes.spa_m2 is None or cotes.slu_m is None or cotes.sle_m is None:
        return []
    reference = 0.5 * cotes.slu_m * cotes.sle_m
    if reference <= 0:
        return []
    facteur = cotes.spa_m2 / reference
    if facteur < FACTEUR_SURFACE_MIN or facteur > FACTEUR_SURFACE_MAX:
        return [
            Anomalie(
                code="surface_incoherente",
                gravite="forte",
                message=(
                    f"Surface {cotes.spa_m2} m² incompatible avec SLU {cotes.slu_m} m × SLE {cotes.sle_m} m "
                    f"(facteur {facteur:.2f}, attendu entre {FACTEUR_SURFACE_MIN} et {FACTEUR_SURFACE_MAX})."
                ),
            )
        ]
    return []
