"""Lot 1.4 — complétude par gabarit : un champ manquant est BLOQUANT.

Contrat du plan, appliqué à la lettre :

* les champs attendus sont **déclarés par le gabarit** ;
* un champ déclaré obligatoire et non lu est une anomalie **bloquante** ;
* elle **envoie en revue** (voie « reprise_complete » du §10.3) ;
* elle n'est **jamais complétée à deviner**.

Ce fichier prouve aussi, noir sur blanc, que 1.4 et 1.3 ne sont PAS la même
mécanique : la discordance de référence laisse la fiche en relecture ciblée,
là où le champ obligatoire manquant l'envoie en revue.

Une règle = un cas qui passe et un cas qui échoue.
"""

from __future__ import annotations

from seamtech_search.fiches.anomalies import CODES_BLOQUANTS, evaluer_anomalies
from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
from seamtech_search.fiches.modeles import Anomalie, ChampExtrait, Cotes, FicheExtraite
from seamtech_search.fiches.persistance import routage

CIBLE_OBLIGATOIRE = "fiche.code"


def _fiche(champs_lus: tuple[str, ...] = (), **remplacements: object) -> FicheExtraite:
    """Fiche minimale ; ``champs_lus`` déclare les cibles déjà lues."""
    donnees: dict[str, object] = {
        "code": "LOT1-CPL-1",
        "client_nom": "Voilerie Fictive",
        "bateau_nom": "Voilier Fictif 42",
        "type_voile_libelle": "Grand-voile",
        "cotes": [Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    }
    donnees.update(remplacements)
    fiche = FicheExtraite(**donnees)  # type: ignore[arg-type]
    fiche.gabarit_code = "GABARIT-FICTIF"
    for cible in champs_lus:
        fiche.champs.append(
            ChampExtrait(
                champ=cible,
                valeur_brute="valeur lue",
                valeur_normalisee="valeur lue",
                methode="document",
                confiance=1.0,
                page=1,
                zone=None,
                table_cible="fiche",
                colonne_cible=cible.split(".")[-1],
            )
        )
    return fiche


def _gabarit(*cibles_obligatoires: str) -> GabaritDef:
    """Un gabarit qui DÉCLARE ses champs obligatoires (et un champ libre)."""
    return GabaritDef(
        code="GABARIT-FICTIF",
        version=1,
        description="gabarit de test — aucun libellé d'atelier réel",
        ancres_detection=["ancre fictive"],
        champs=[
            RegleChamp(cible="fiche.notes", ancres=["notes"]),
            *(RegleChamp(cible=cible, ancres=[cible], obligatoire=True) for cible in cibles_obligatoires),
        ],
    )


def _codes(fiche: FicheExtraite, gabarit: GabaritDef | None) -> set[str]:
    return {a.code for a in evaluer_anomalies(fiche, gabarit=gabarit)}


# ---------------------------------------------------------------------------
# Règle — un champ déclaré obligatoire et non lu
# ---------------------------------------------------------------------------


def test_champ_obligatoire_lu_ne_leve_rien() -> None:
    """Cas qui PASSE : le champ déclaré obligatoire a été lu."""
    gabarit = _gabarit(CIBLE_OBLIGATOIRE)
    assert _codes(_fiche((CIBLE_OBLIGATOIRE,)), gabarit) == set()


def test_champ_obligatoire_manquant_est_signale() -> None:
    """Cas qui ÉCHOUE : déclaré obligatoire, non lu → anomalie."""
    gabarit = _gabarit(CIBLE_OBLIGATOIRE)
    assert "champ_obligatoire_manquant" in _codes(_fiche(), gabarit)


def test_regle_non_obligatoire_manquante_ne_leve_rien() -> None:
    """Un champ simplement déclaré n'est pas un attendu : son absence ne bloque pas."""
    gabarit = _gabarit(CIBLE_OBLIGATOIRE)
    assert "champ_obligatoire_manquant" not in _codes(_fiche((CIBLE_OBLIGATOIRE,)), gabarit)


def test_sans_gabarit_la_completude_ne_sapplique_pas() -> None:
    """Il ne devine pas ce qu'il n'a pas reçu."""
    assert "champ_obligatoire_manquant" not in _codes(_fiche(), None)


def test_gabarit_sans_aucune_regle_obligatoire_ne_leve_rien() -> None:
    assert "champ_obligatoire_manquant" not in _codes(_fiche(), _gabarit())


def test_la_completude_est_vue_meme_sans_cotes() -> None:
    """Un attendu reste un attendu : une fiche sans cotes n'y échappe pas."""
    fiche = _fiche(cotes=[])
    assert "champ_obligatoire_manquant" in _codes(fiche, _gabarit(CIBLE_OBLIGATOIRE))


# ---------------------------------------------------------------------------
# Qualité du signal : chaque manque est nommé, et jamais complété à deviner
# ---------------------------------------------------------------------------


def test_le_champ_manquant_est_nomme() -> None:
    """Exigence du plan : le refus est NOMMÉ."""
    fiche = _fiche()
    refus = [a for a in evaluer_anomalies(fiche, gabarit=_gabarit(CIBLE_OBLIGATOIRE)) if a.code == "champ_obligatoire_manquant"]
    assert len(refus) == 1
    assert CIBLE_OBLIGATOIRE in refus[0].message
    assert "GABARIT-FICTIF" in refus[0].message
    assert refus[0].gravite == "forte"


def test_deux_champs_manquants_sont_chacun_nommes() -> None:
    """Deux manques, deux anomalies : l'un ne masque pas l'autre."""
    gabarit = _gabarit("fiche.code", "cotes.finie.slu_m")
    refus = [
        a
        for a in evaluer_anomalies(_fiche(), gabarit=gabarit)
        if a.code == "champ_obligatoire_manquant"
    ]
    assert len(refus) == 2
    messages = " ".join(a.message for a in refus)
    assert "fiche.code" in messages and "cotes.finie.slu_m" in messages


def test_le_message_dit_que_rien_nest_devine() -> None:
    """« jamais complété à deviner » est dit explicitement."""
    refus = [
        a
        for a in evaluer_anomalies(_fiche(), gabarit=_gabarit(CIBLE_OBLIGATOIRE))
        if a.code == "champ_obligatoire_manquant"
    ]
    assert "deviner" in refus[0].message


def test_le_champ_manquant_n_est_pas_invente() -> None:
    """La fiche n'est pas complétée : le champ reste absent après contrôle."""
    fiche = _fiche()
    evaluer_anomalies(fiche, gabarit=_gabarit(CIBLE_OBLIGATOIRE))
    assert CIBLE_OBLIGATOIRE not in {c.champ for c in fiche.tous_les_champs()}


# ---------------------------------------------------------------------------
# La mécanique : BLOQUANTE → revue. Et 1.3, elle, ne l'est pas.
# ---------------------------------------------------------------------------


def test_anomalie_bloquante_envoie_en_revue() -> None:
    """1.4 est bloquante : la fiche part en revue, pas en simple relecture."""
    fiche = _fiche()
    fiche.anomalies = evaluer_anomalies(fiche, gabarit=_gabarit(CIBLE_OBLIGATOIRE))
    assert routage(fiche)["voie"] == "reprise_complete"
    assert "revue" in routage(fiche)["motif"]


def test_la_discordance_de_reference_n_envoie_pas_en_revue() -> None:
    """1.3 n'est PAS 1.4 : même écrite, la fiche reste en relecture ciblée."""
    fiche = _fiche()
    fiche.anomalies = [
        Anomalie(code="reference_discordante", gravite="moyenne", message="dossier « A », document « B »")
    ]
    assert routage(fiche)["voie"] == "relecture_ciblee"


def test_les_deux_mecaniques_sont_distinctes() -> None:
    """La preuve d'ensemble : un code n'appartient qu'à une seule mécanique."""
    assert "champ_obligatoire_manquant" in CODES_BLOQUANTS
    assert "reference_discordante" not in CODES_BLOQUANTS
