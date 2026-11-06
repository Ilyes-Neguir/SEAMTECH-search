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
import re
from functools import lru_cache
from typing import NamedTuple

from seamtech_search.fiches.gabarits import GabaritDef
from seamtech_search.fiches.modeles import Anomalie, Cotes, FicheExtraite
from seamtech_search.lexique import charger_lexique
from seamtech_search.qualite.cartographie import cle_normalisation

LOGGER = logging.getLogger("seamtech_search.fiches.anomalies")

# Lot 1.4 — codes d'anomalies BLOQUANTES.
#
# Une anomalie bloquante envoie la fiche en REVUE (voie « reprise_complete » du
# §10.3) : elle n'est pas simplement relue, elle retourne à l'atelier.
#
# À ne PAS confondre avec « reference_discordante » (Lot 1.3), qui est NON
# bloquante : le dossier entre en base, l'anomalie l'accompagne et la fiche
# part en « a_valider ». Ce sont deux mécaniques distinctes, volontairement.
CODES_BLOQUANTS: frozenset[str] = frozenset({"champ_obligatoire_manquant"})

# Lot 1.5 — codes émis par les contrôles RG16 de ce module.
#
# Ils servent à REJOUER les contrôles sans écraser une anomalie d'une autre
# provenance (reprise, orientation PDF…) : c'est ce qui rend l'application
# obligatoire IDEMPOTENTE. À tenir à jour avec les ``code=`` de ce module.
CODES_RG16: frozenset[str] = frozenset({
    "bateau_hors_referentiel",
    "champ_manquant",
    "champ_obligatoire_manquant",
    "client_invraisemblable",
    "cote_hors_plage",
    "cotes_incoherentes",
    "reference_discordante",
    "surface_incoherente",
})

# Plages physiques plausibles (mètres, m², cm, kg) — points de départ du
# §10.3, à caler sur les fiches réelles ; une cote hors plage est suspecte,
# pas forcément fausse (gravite « moyenne », à confirmer par l'opérateur).
PLAGES_METRES = {"slu_m": (0.5, 30.0), "sle_m": (0.5, 30.0), "sf_m": (0.2, 12.0), "shw_m": (0.2, 12.0)}
#: ``poids_kg`` : plafond porté à 120 kg après MESURE sur une fiche réelle —
#: `/VOILES 2025/PROTO ST430/ST 430_ffab.pdf` imprime « 46,2 m² … 42,4 kg »
#: (guindant 18,65 m). L'ancien plafond de 30 kg levait une fausse anomalie
#: « cote hors plage » sur un document parfaitement lu.
PLAGES_AUTRES = {"spa_m2": (0.5, 300.0), "tetiere_cm": (0.5, 60.0), "poids_kg": (0.02, 120.0)}

# Cohérence surface (portants) — TOLÉRANCE NOMMÉE (constat A de revue, voir
# docs/CONTROLES_RG16.md) : facteur = SPA / (½·SLU·SLE) accepté dans
# [0,55 ; 1,30], soit [−45 % ; +30 %] autour du triangle quelconque.
# Justification : le tableur d'origine calcule la surface en triangle
# ÉQUILATÉRAL — SPA = (√3/4)·SLU·SLE = 0,433·SLU·SLE, facteur 0,866 — et la
# fiche de référence 7792-SO imprime SPA = 15,71 pour SLU 6,60 / SLE 5,50
# (facteur réel 0,8655, écart −13,4 %). La bande couvre donc LES DEUX formules
# (équilatéral 0,866 et quelconque 1,0) avec marge pour les ronds de chute.
# Une tolérance ±5/10 % signalerait à tort la fiche de référence — test figé
# dans tests/test_anomalies_coherence.py::TestToleranceSurfaceFigee.
FACTEUR_SURFACE_MIN = 0.55
FACTEUR_SURFACE_MAX = 1.30

# Types de voile où SLU > SLE > SF est attendu (portants) ; les interfaces
# (génois, grand-voile) ordonnent plutôt SLU > SHW > SF — SLU > SF reste vrai
# pour tous, c'est le contrôle minimal commun.
FAMILIES_PORTANT = {"spi", "spinnaker"}

# ---------------------------------------------------------------------------
# Lot 1.2 — vraisemblance des champs
#
# Deux règles, et une seule exigence pour les deux : chaque refus est NOMMÉ.
# Un contrôle qui écarte une valeur sans dire laquelle, ni pourquoi, est pire
# qu'une absence de contrôle — l'opérateur ne peut pas le contester.
# ---------------------------------------------------------------------------

#: Unités connues, de la plus longue à la plus courte : « mm » AVANT « m »,
#: sinon « 12 mm » serait lu comme un nombre suivi de l'unité « m ».
_UNITES_MESURE: tuple[str, ...] = ("mm", "cm", "m2", "kg", "m", "g")
#: Un nombre nu, après normalisation — la ponctuation y est devenue une espace
#: (« 12.50 » → « 12 50 »).
_RE_NOMBRE_NORMALISE = re.compile(r"^\d+(?:\s\d+)*$")


def _est_valeur_mesuree(cle_normalisee: str) -> bool:
    """Une MESURE (« 12.50 m », « 380 g ») n'est pas un nom de client.

    La clé d'entrée est déjà normalisée : ponctuation et exposants y sont des
    espaces (« 12,50 m² » → « 12 50 m 2 »). On compacte donc avant de
    reconnaître l'unité, sinon la virgule décimale masquerait la mesure.
    """
    compact = cle_normalisee.replace(" ", "")
    if not compact:
        return False
    return any(
        len(compact) > len(unite)
        and compact.endswith(unite)
        and compact[: -len(unite)].isdigit()
        for unite in _UNITES_MESURE
    )


@lru_cache(maxsize=1)
def _vocabulaire_technique() -> frozenset[str]:
    """Termes techniques du domaine — un nom de client ne peut pas en être un.

    Le lexique engagé (`config/lexique_fiches.json`) est la source : il
    contient déjà les types de voile, les cotes, les matériaux et les
    libellés d'atelier. On ne le redéveloppe pas, on le lit.

    Si le lexique est absent ou invalide, le vocabulaire est vide et le
    contrôle (a) ne s'applique pas : c'est un avertissement journalisé, pas un
    silence — le contrôle ne prétend jamais couvrir ce qu'il n'a pas lu.
    """
    try:
        return frozenset(charger_lexique().vocabulaire_normalise)
    except Exception as erreur:  # lexique absent ou illisible : dit, jamais tu
        LOGGER.warning(
            "Lot 1.2 : lexique technique indisponible (%s) — le contrôle "
            "« valeur technique comme client » ne s'applique pas.",
            type(erreur).__name__,
        )
        return frozenset()


def _terme_technique(cle_normalisee: str) -> str | None:
    """Le terme technique reconnu, ou ``None`` si la valeur est plausible.

    Seul un nom qui EST un terme technique est refusé — pas un nom qui en
    CONTIENT un : « Voilerie du Guindant » est un client, « Guindant » n'en
    est pas un. Un élargissement aux sous-chaînes produirait des refus en
    série sur des noms parfaitement réels.
    """
    if not cle_normalisee:
        return None
    if cle_normalisee in _vocabulaire_technique():
        return cle_normalisee
    if _est_valeur_mesuree(cle_normalisee) or _RE_NOMBRE_NORMALISE.match(cle_normalisee):
        return cle_normalisee
    return None


def _controle_vraisemblance(
    fiche: FicheExtraite,
    referentiel_bateaux: set[str] | None,
) -> list[Anomalie]:
    """Lot 1.2 : une valeur technique n'est pas un client ; un bateau hors
    référentiel est signalé, pas enregistré."""
    anomalies: list[Anomalie] = []

    # (a) Une valeur technique ne peut pas devenir un nom de client.
    client = (fiche.client_nom or "").strip()
    if client:
        terme = _terme_technique(cle_normalisation(client))
        if terme is not None:
            anomalies.append(
                Anomalie(
                    code="client_invraisemblable",
                    gravite="forte",
                    message=(
                        f"Nom de client « {client} » refusé : c'est la valeur technique "
                        f"« {terme} », pas un nom. Le champ est laissé tel quel et "
                        f"signalé — jamais enregistré comme client."
                    ),
                )
            )

    # (b) Un bateau absent du référentiel est signalé, pas enregistré.
    #
    # « Pas enregistré » : le référentiel n'est pas complété en silence. La
    # fiche entre, l'anomalie est portée à l'approbateur ; c'est un humain qui
    # décide d'ajouter le modèle. Sans référentiel transmis, le contrôle ne
    # s'applique pas — il ne devine pas ce qu'il n'a pas.
    bateau = (fiche.bateau_nom or "").strip()
    if bateau and referentiel_bateaux is not None:
        cle_bateau = cle_normalisation(bateau)
        if cle_bateau and cle_bateau not in referentiel_bateaux:
            anomalies.append(
                Anomalie(
                    code="bateau_hors_referentiel",
                    gravite="moyenne",
                    message=(
                        f"Bateau « {bateau} » absent du référentiel : signalé, pas "
                        f"enregistré. Le référentiel n'est pas complété en silence — "
                        f"un humain tranche."
                    ),
                )
            )
    return anomalies


# ---------------------------------------------------------------------------
# Lot 1.3 — la référence, comparée à trois
# ---------------------------------------------------------------------------


class ReferencesConstatees(NamedTuple):
    """Les trois références d'un même dossier, telles qu'elles ont été LUES.

    Un dossier d'atelier porte sa référence à trois endroits : le NOM DU
    DOSSIER, le NOM DU FICHIER, et le DOCUMENT lui-même. Elles doivent dire la
    même chose. Quand elles ne le disent pas, l'écart est MONTRÉ — les trois
    valeurs, côte à côte — et non résolu en douce par un repli silencieux sur
    la source la plus commode.

    ``None`` signifie « cette source ne porte pas de référence » : une source
    muette n'est pas un désaccord.
    """

    document: str | None = None
    fichier: str | None = None
    dossier: str | None = None

    def connues(self) -> dict[str, str]:
        """Les références lues, par provenance, dans l'ordre du plan."""
        return {
            provenance: valeur
            for provenance, valeur in (
                ("dossier", self.dossier),
                ("nom de fichier", self.fichier),
                ("document", self.document),
            )
            if valeur and valeur.strip()
        }


def _cle_reference(valeur: str) -> str:
    """Deux écritures d'une même référence, rapprochées : casse et espaces."""
    return re.sub(r"\s+", " ", valeur.strip().upper())


def _controle_reference_triple(references: ReferencesConstatees) -> list[Anomalie]:
    """Lot 1.3 : trois écritures de la même référence, montrées côte à côte.

    NON BLOQUANT — et c'est volontaire : ce n'est PAS la mécanique du champ
    manquant (1.4), qui envoie en revue. Ici le dossier ENTRE en base : la
    référence retenue reste celle lue dans le document, l'écart est porté à la
    connaissance de l'approbateur, et la fiche arrive en « a_valider » (RG3).
    Une discordance n'empêche donc jamais le dépôt ; elle l'accompagne.

    Avec une seule source lue, il n'y a rien à comparer : aucune anomalie.
    """
    connues = references.connues()
    if len(connues) < 2:
        return []
    if len({_cle_reference(valeur) for valeur in connues.values()}) <= 1:
        return []
    detail = ", ".join(f"{provenance} « {valeur} »" for provenance, valeur in connues.items())
    return [
        Anomalie(
            code="reference_discordante",
            gravite="moyenne",
            message=(
                f"Référence discordante : {detail}. Ces sources ne disent pas "
                "la même chose — à examiner avant validation. Le dossier est "
                "enregistré : l'écart est signalé, il ne bloque pas le dépôt."
            ),
        )
    ]


def _controle_completude(fiche: FicheExtraite, gabarit: GabaritDef) -> list[Anomalie]:
    """Lot 1.4 : un champ déclaré obligatoire par le gabarit et non lu est BLOQUANT.

    Les champs attendus sont DÉCLARÉS PAR LE GABARIT (``RegleChamp.obligatoire``),
    pas devinés ici : ce module ne fait que lire la déclaration. Un champ
    manquant part en revue et n'est JAMAIS complété à deviner — une valeur
    inventée serait pire qu'une valeur absente, parce qu'elle serait invisible ;
    une valeur absente est au moins signalée.
    """
    lus = {champ.champ for champ in fiche.tous_les_champs()}
    anomalies: list[Anomalie] = []
    for regle in gabarit.champs:
        if not regle.obligatoire or regle.cible in lus:
            continue
        anomalies.append(
            Anomalie(
                code="champ_obligatoire_manquant",
                gravite="forte",
                message=(
                    f"Champ obligatoire « {regle.cible} » non lu — déclaré obligatoire par le "
                    f"gabarit « {gabarit.code} » v{gabarit.version}. La fiche part en revue : "
                    "le champ n'est PAS complété à deviner. Une valeur inventée serait "
                    "invisible ; une valeur absente est au moins signalée."
                ),
            )
        )
    return anomalies


def evaluer_anomalies(
    fiche: FicheExtraite,
    *,
    referentiel_bateaux: set[str] | None = None,
    references: ReferencesConstatees | None = None,
    gabarit: GabaritDef | None = None,
) -> list[Anomalie]:
    """RG16 : contrôles de cohérence, exécutés à TOUTE confiance.

    ``referentiel_bateaux`` (Lot 1.2) : clés normalisées des modèles connus
    (:func:`~seamtech_search.qualite.cartographie.cle_normalisation`). Sans lui,
    le contrôle « bateau hors référentiel » ne s'applique pas — il ne devine
    pas ce qu'il n'a pas reçu.

    ``references`` (Lot 1.3) : les trois références constatées du dossier
    (dossier, nom de fichier, document). Sans elles, la comparaison à trois ne
    s'applique pas.

    ``gabarit`` (Lot 1.4) : les champs attendus y sont DÉCLARÉS
    (``RegleChamp.obligatoire``). Sans gabarit, la complétude ne s'applique pas.

    La vraisemblance, la discordance de référence et la complétude passent
    AVANT le retour anticipé sur les cotes : elles portent sur l'identité et
    sur les champs attendus, et une fiche sans cotes ne doit pas y échapper.
    """
    anomalies: list[Anomalie] = []
    anomalies.extend(_controle_vraisemblance(fiche, referentiel_bateaux))
    if references is not None:
        anomalies.extend(_controle_reference_triple(references))
    if gabarit is not None:
        anomalies.extend(_controle_completude(fiche, gabarit))
    finie = next((c for c in fiche.cotes if c.jeu == "finie"), None)
    if finie is None:
        anomalies.append(
            Anomalie(code="champ_manquant", gravite="moyenne", message="Aucun jeu de cotes « finie » lu sur la fiche.")
        )
        return anomalies

    anomalies.extend(_controles_plages(finie))
    anomalies.extend(_controle_ordre_cotes(fiche, finie))
    anomalies.extend(_controle_surface(fiche, finie))
    LOGGER.debug("Contrôles RG16 : %d anomalie(s).", len(anomalies))
    return anomalies


def appliquer_controles_rg16(
    fiche: FicheExtraite,
    *,
    referentiel_bateaux: set[str] | None = None,
    references: ReferencesConstatees | None = None,
    gabarit: GabaritDef | None = None,
) -> list[Anomalie]:
    """Lot 1.5 : les contrôles RG16 sur TOUTE fiche, quelle que soit la voie.

    Ils deviennent OBLIGATOIRES, « pas seulement sur certaines voies » : c'est
    la fonction appelée à l'ÉCRITURE, donc aucune fiche n'y échappe — venue du
    gabarit, de la reprise, d'un import ou d'ailleurs. Un contrôle qui ne
    s'exécute que si quelqu'un pense à l'appeler n'est pas un contrôle.

    Rejouable sans effet de bord : les anomalies RG16 déjà posées sont
    REMPLACÉES, jamais cumulées ; celles d'une autre provenance — reprise,
    orientation PDF — sont conservées telles quelles, ce module ne les connaît
    pas et n'a pas à les juger.
    """
    etrangeres = [anomalie for anomalie in fiche.anomalies if anomalie.code not in CODES_RG16]
    evaluees = evaluer_anomalies(
        fiche,
        referentiel_bateaux=referentiel_bateaux,
        references=references,
        gabarit=gabarit,
    )
    fiche.anomalies = etrangeres + evaluees
    LOGGER.debug("Contrôles RG16 appliqués à l'écriture : %d anomalie(s).", len(evaluees))
    return fiche.anomalies


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
