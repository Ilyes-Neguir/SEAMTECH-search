"""Lot 3 — calibrer les seuils de validation automatique sur des fiches réelles.

État des lieux, et c'est un blocage : ``config/seuils_confiance.json`` porte
``calibre: false`` et ``fiches_reelles_utilisees: 0``. Tant que cette preuve
manque, la validation groupée est INTERDITE — et elle a raison de l'être : sans
calibration, entériner des fiches par lot reviendrait à valider une erreur
systématique du gabarit à l'échelle de l'archive.

Ce module fabrique la preuve. Deux idées le tiennent :

1. **On ne calibre pas une confiance avec des confiances.** Savoir que le
   moteur s'accorde 0,92 n'apprend rien sur son exactitude. Il faut la VÉRITÉ :
   parmi les champs qu'un humain a arbitrés, lesquels étaient justes ? On
   compare donc la confiance ANNONCÉE au sort RÉEL du champ.

2. **La preuve précède la décision.** Tant que l'échantillon est trop petit, le
   module refuse de proposer un seuil. Un seuil inventé est pire qu'un seuil
   absent, parce qu'il a l'air d'une mesure.

La définition de « juste » est assumée et écrite : un champ corrigé par un
humain était faux ; un champ non corrigé sur une fiche validée n'est pas prouvé
exact, il est ACCEPTÉ. C'est la meilleure preuve disponible sans relecture
intégrale, et le module le dit au lieu de le masquer.

Lecture seule sur la base ; l'écriture ne touche que le fichier de seuils, et
jamais sans qu'on la demande.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .persistance import famille_du_champ

LOGGER = logging.getLogger("seamtech_search.fiches.calibration")

#: Effectif minimal pour énoncer une exactitude : le même 5 que partout ailleurs
#: dans le projet. En dessous, on affiche l'effectif, pas un pourcentage.
EFFECTIF_MIN_PALIER = 5

#: Fiches réelles minimales pour déclarer les seuils calibrés (plan : 20-30
#: fiches de vérité terrain). En dessous, le fichier reste ``calibre: false``.
MIN_FICHES_CALIBRATION = 20

#: Paliers de confiance examinés par défaut, du plus laxiste au plus strict.
BORNES_PALIERS: tuple[float, ...] = (0.80, 0.85, 0.90, 0.95, 0.98)

_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Observation:
    """Un champ arbitré : la confiance que le moteur s'accordait, et son sort réel."""

    confiance: float
    correct: bool


def paliers_exactitude(
    observations: Sequence[Observation],
    bornes: Sequence[float] = BORNES_PALIERS,
) -> list[dict[str, Any]]:
    """Exactitude MESURÉE à chaque palier de confiance.

    Un palier sans assez de monde ne donne PAS d'exactitude : « 3 champs sur 3
    justes » n'est pas 100 %, c'est 3 champs. La différence n'est pas
    académique, c'est exactement ce qui sépare une mesure d'une impression.
    """
    paliers: list[dict[str, Any]] = []
    for borne in bornes:
        au_dessus = [observation for observation in observations if observation.confiance >= borne - _TOLERANCE]
        effectif = len(au_dessus)
        justes = sum(1 for observation in au_dessus if observation.correct)
        suffisant = effectif >= EFFECTIF_MIN_PALIER
        paliers.append(
            {
                "seuil": borne,
                "nb": effectif,
                "nb_corrects": justes,
                "exactitude": round(justes / effectif, 4) if suffisant and effectif else None,
                "effectif_suffisant": suffisant,
            }
        )
    return paliers


def seuil_recommande(
    observations: Sequence[Observation],
    *,
    effectif_min: int = EFFECTIF_MIN_PALIER,
    exactitude_visee: float = 1.0,
) -> float | None:
    """Le plus bas seuil OBSERVÉ que les preuves soutiennent.

    Deux conditions, cumulatives : assez de champs au-dessus pour que
    l'exactitude veuille dire quelque chose, et une exactitude mesurée au moins
    égale à la cible. ``None`` quand aucune confiance observée ne les réunit —
    le refus est la réponse correcte, pas un échec.

    Le seuil est toujours une confiance RÉELLEMENT OBSERVÉE : sortir 0,93 d'un
    chapeau parce qu'il « tombe bien » serait inventer une mesure.
    """
    candidats = sorted({round(float(observation.confiance), 6) for observation in observations})
    for seuil in candidats:
        au_dessus = [
            observation for observation in observations if observation.confiance >= seuil - _TOLERANCE
        ]
        if len(au_dessus) < effectif_min:
            continue
        justes = sum(1 for observation in au_dessus if observation.correct)
        if justes / len(au_dessus) >= exactitude_visee - _TOLERANCE:
            return seuil
    return None


def proposition_famille(
    famille: str,
    seuil_actuel: float,
    observations: Sequence[Observation],
    *,
    effectif_min: int = EFFECTIF_MIN_PALIER,
) -> dict[str, Any]:
    """Proposition de seuil pour une famille, AVEC ses preuves et ses raisons.

    Une proposition applicable porte l'effectif, l'exactitude et les paliers
    complets : un seuil ne voyage jamais seul, sinon il redevient une opinion.
    Une proposition refusée dit pourquoi, et n'avance aucun chiffre.
    """
    seuil = seuil_recommande(observations, effectif_min=effectif_min)
    paliers = paliers_exactitude(observations)
    if seuil is None:
        return {
            "famille": famille,
            "seuil_actuel": seuil_actuel,
            "seuil_propose": None,
            "applicable": False,
            "effectif": len(observations),
            "exactitude": None,
            "paliers": paliers,
            "raison": (
                f"{len(observations)} observation(s) arbitrée(s) : aucune confiance observée ne réunit "
                f"{effectif_min} champs justes au-dessus d'elle. Aucun seuil n'est proposé — un seuil "
                "inventé est pire qu'un seuil absent, parce qu'il a l'air d'une mesure."
            ),
            "lecture": (
                f"{famille} : {len(observations)} champ(s) arbitré(s), insuffisant pour proposer un seuil "
                f"(il faut {effectif_min} champs justes au-dessus du seuil). Le seuil actuel de "
                f"{seuil_actuel:.2f} reste en place."
            ),
        }
    au_dessus = [
        observation for observation in observations if observation.confiance >= seuil - _TOLERANCE
    ]
    justes = sum(1 for observation in au_dessus if observation.correct)
    exactitude = justes / len(au_dessus)
    return {
        "famille": famille,
        "seuil_actuel": seuil_actuel,
        "seuil_propose": seuil,
        "applicable": True,
        "effectif": len(au_dessus),
        "exactitude": round(exactitude, 4),
        "paliers": paliers,
        "raison": None,
        "lecture": (
            f"{famille} : {len(au_dessus)} champs arbitrés à partir de {seuil:.2f} de confiance, dont "
            f"{justes} justes ({exactitude:.0%}). Seuil proposé {seuil:.2f} au lieu de {seuil_actuel:.2f}."
        ),
    }


# ---------------------------------------------------------------------------
# Lecture de la vérité terrain (PostgreSQL) et écriture du fichier de seuils
# ---------------------------------------------------------------------------


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise CalibrationRefusee(
            "La calibration lit la vérité terrain dans fiche_champ_extrait : "
            "PostgreSQL uniquement (décision de couche §17.1)."
        )


class CalibrationRefusee(ValueError):
    """Calibration impossible : pas de base métier, ou pas assez de preuves."""


def observations_depuis_base(index: Any) -> dict[str, list[Observation]]:  # noqa: ANN401
    """Observations par famille, lues sur les champs qu'un HUMAIN a arbitrés.

    Un champ est arbitré quand la fiche qui le porte est validée : ``corrige``
    dit alors s'il a fallu repasser derrière le moteur. Un champ non corrigé
    n'est pas prouvé exact, il est accepté — la nuance est dans le rapport,
    pas dans le code, parce qu'elle n'appartient qu'au jugement humain.

    Les fiches encore « à valider » sont écartées : on ne tire aucune leçon
    d'un champ que personne n'a regardé.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                SELECT e.champ,
                       e.confiance,
                       e.corrige
                FROM fiche_champ_extrait e
                JOIN fiche f ON f.id_fiche = e.id_fiche
                WHERE f.statut = 'valide'
                  AND e.confiance IS NOT NULL
                """
            )
            lignes = curseur.fetchall()
    observations: dict[str, list[Observation]] = {}
    for champ, confiance, corrige in lignes:
        famille = famille_du_champ(str(champ))
        observations.setdefault(famille, []).append(
            Observation(confiance=float(confiance), correct=not bool(corrige))
        )
    return observations


#: Ce que vaut un verdict PRONONCÉ, côté preuve.
#:
#: « probable » est délibérément ABSENT de cette table. Ce n'est pas un oubli
#: et ce n'est pas prudent par excès : un doute n'est une preuve dans aucun
#: sens. Le compter parmi les justes gonflerait l'exactitude mesurée ; le
#: compter parmi les faux la plomberait. Le verdict est donc consigné, il est
#: traçable, il apparaît dans le bilan — et il ne nourrit PAS le calcul du
#: seuil. C'est le prix de l'honnêteté, il est assumé et il est écrit ici pour
#: que personne n'ait à le deviner.
VERDICT_PREUVE: dict[str, bool] = {"certain": True, "rejet": False}

#: Lecture du DERNIER verdict par (fiche, champ) : revenir sur un jugement est
#: un droit, l'historique des revirements est une donnée et non un déchet.
_SQL_VERDICTS_COURANTS = """
SELECT DISTINCT ON (v.id_fiche, v.champ)
       v.champ, v.verdict, v.confiance_moteur
FROM verdict_revue v
WHERE v.confiance_moteur IS NOT NULL
ORDER BY v.id_fiche, v.champ, v.decide_le DESC, v.id_verdict DESC
"""


def observations_depuis_verdicts(index: Any) -> dict[str, list[Observation]]:  # noqa: ANN401
    """Observations par famille, lues sur les verdicts humains EXPLICITES.

    Différence de nature avec :func:`observations_depuis_base` : celle-ci lit
    un SIGNE INDIRECT (« l'approbateur a-t-il corrigé ? »), où un champ non
    corrigé est compté juste alors qu'il est seulement accepté. Ici le verdict
    est celui qu'un humain a PRONONCÉ, avec son auteur et sa date.

    Un verdict « probable » est lu, puis écarté du calcul — voir
    :data:`VERDICT_PREUVE`. Il reste visible dans le bilan.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(_SQL_VERDICTS_COURANTS)
            lignes = curseur.fetchall()
    observations: dict[str, list[Observation]] = {}
    for champ, verdict, confiance in lignes:
        correct = VERDICT_PREUVE.get(str(verdict))
        if correct is None:
            continue
        famille = famille_du_champ(str(champ))
        observations.setdefault(famille, []).append(
            Observation(confiance=float(confiance), correct=correct)
        )
    return observations


def nombre_fiches_verdictees(index: Any) -> int:  # noqa: ANN401
    """Fiches distinctes portant au moins un verdict EXPLOITABLE.

    Une fiche dont les seuls verdicts sont « probable » ne compte pas : elle
    n'apporte aucune preuve, seulement des doutes consignés.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                SELECT COUNT(DISTINCT id_fiche)
                FROM (
                    SELECT DISTINCT ON (id_fiche, champ) id_fiche, verdict
                    FROM verdict_revue
                    ORDER BY id_fiche, champ, decide_le DESC, id_verdict DESC
                ) courants
                WHERE verdict IN ('certain', 'rejet')
                """
            )
            ligne = curseur.fetchone()
    return int(ligne[0]) if ligne else 0


def bilan_verdicts(index: Any) -> dict[str, int]:  # noqa: ANN401
    """Répartition des verdicts COURANTS, doutes compris.

    Le bilan montre les « probable » que le calcul du seuil écarte : les
    cacher reviendrait à faire croire que le doute n'a pas été exprimé.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                SELECT verdict, COUNT(*)
                FROM (
                    SELECT DISTINCT ON (id_fiche, champ) id_fiche, verdict
                    FROM verdict_revue
                    ORDER BY id_fiche, champ, decide_le DESC, id_verdict DESC
                ) courants
                GROUP BY verdict
                """
            )
            lignes = curseur.fetchall()
    return {str(verdict): int(nombre) for verdict, nombre in lignes}


def nombre_fiches_arbitrees(index: Any) -> int:  # noqa: ANN401
    """Fiches validées portant au moins un champ arbitré : l'assiette de la preuve."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                """
                SELECT COUNT(DISTINCT f.id_fiche)
                FROM fiche f
                JOIN fiche_champ_extrait e ON e.id_fiche = f.id_fiche
                WHERE f.statut = 'valide' AND e.confiance IS NOT NULL
                """
            )
            ligne = curseur.fetchone()
    return int(ligne[0]) if ligne else 0


def appliquer_calibration(
    chemin: Path | str,
    propositions: Sequence[dict[str, Any]],
    *,
    nb_fiches: int,
    date_calibration: datetime | None = None,
) -> dict[str, Any]:
    """Écrit les seuils calibrés. Refuse si la preuve est trop mince.

    Le fichier garde sa documentation et l'historique de ce qui a été mesuré :
    personne ne doit pouvoir lire « calibre : true » sans voir sur combien de
    fiches, ni quand.
    """
    if nb_fiches < MIN_FICHES_CALIBRATION:
        raise CalibrationRefusee(
            f"Calibration refusée : {nb_fiches} fiche(s) réelle(s) arbitrée(s), "
            f"il en faut {MIN_FICHES_CALIBRATION}. Une calibration sur un échantillon "
            "trop petit donnerait un seuil qui a l'air d'une mesure sans en être une."
        )
    chemin_resolu = Path(chemin)
    donnees = json.loads(chemin_resolu.read_text(encoding="utf-8")) if chemin_resolu.is_file() else {}
    appliquees: list[dict[str, Any]] = []
    for proposition in propositions:
        if not proposition.get("applicable"):
            continue
        famille = str(proposition["famille"])
        seuil = float(proposition["seuil_propose"])  # type: ignore[arg-type]
        familles = donnees.setdefault("familles", {}).setdefault(famille, {})
        familles["seuil_passage_direct"] = seuil
        familles["calibre_le"] = (date_calibration or datetime.now(timezone.utc)).isoformat()
        familles["preuve"] = {
            "effectif": proposition["effectif"],
            "exactitude_mesuree": proposition["exactitude"],
            "seuil_precedent": proposition["seuil_actuel"],
            "definition_correct": "non corrigé sur une fiche validée = accepté, non prouvé exact",
        }
        appliquees.append({"famille": famille, "seuil": seuil, "effectif": proposition["effectif"]})
    donnees["calibre"] = True
    donnees["calibre_le"] = (date_calibration or datetime.now(timezone.utc)).isoformat()
    donnees["fiches_reelles_utilisees"] = int(nb_fiches)
    donnees["calibration"] = {
        "methode": "exactitude mesurée sur les champs arbitrés par un humain (fiches validées)",
        "seuil_min_fiches": MIN_FICHES_CALIBRATION,
        "effectif_min_par_palier": EFFECTIF_MIN_PALIER,
        "familles_calibre": sorted(entree["famille"] for entree in appliquees),
    }
    chemin_resolu.parent.mkdir(parents=True, exist_ok=True)
    chemin_resolu.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Seuils calibrés sur %s fiche(s) : %s", nb_fiches, appliquees)
    return {
        "calibre": True,
        "nb_fiches": int(nb_fiches),
        "familles_appliquees": appliquees,
        "chemin": str(chemin_resolu),
    }
