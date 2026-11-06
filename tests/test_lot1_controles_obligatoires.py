"""Lot 1.5 — les contrôles RG16 existants deviennent OBLIGATOIRES sur toute fiche.

Contrat du plan, appliqué à la lettre : les contrôles « pas seulement sur
certaines voies ». Le verrou est posé À L'ÉCRITURE, parce que c'est le seul
endroit par où passe nécessairement une fiche, quelle que soit la voie qui
l'a produite — gabarit, reprise, import.

Une règle = un cas qui passe et un cas qui échoue.
"""

from __future__ import annotations

import pytest

from seamtech_search.fiches import persistance
from seamtech_search.fiches.anomalies import CODES_RG16, appliquer_controles_rg16
from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
from seamtech_search.fiches.modeles import Anomalie, Cotes, FicheExtraite

GABARIT = GabaritDef(
    code="GABARIT-FICTIF",
    version=1,
    description="gabarit de test",
    ancres_detection=["ancre fictive"],
    champs=[RegleChamp(cible="fiche.code", ancres=["code fiche"], obligatoire=True)],
)


def _fiche(cotes: list[Cotes] | None = None, **remplacements: object) -> FicheExtraite:
    """Fiche minimale ; ``cotes`` par défaut : un jeu « finie » cohérent."""
    donnees: dict[str, object] = {
        "code": "LOT1-RG16-1",
        "client_nom": "Voilerie Fictive",
        "bateau_nom": "Voilier Fictif 42",
        "type_voile_libelle": "Grand-voile",
        "cotes": [Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)]
        if cotes is None
        else cotes,
    }
    donnees.update(remplacements)
    fiche = FicheExtraite(**donnees)  # type: ignore[arg-type]
    fiche.gabarit_code = GABARIT.code
    return fiche


def _fiche_boiteuse() -> FicheExtraite:
    """Une fiche techniquement incohérente : SLU plus courte que la bordure."""
    return _fiche(cotes=[Cotes(jeu="finie", slu_m=2.0, sle_m=4.0, sf_m=3.9)])


# ---------------------------------------------------------------------------
# Règle — toute fiche reçoit les contrôles, même sans être passée par l'extracteur
# ---------------------------------------------------------------------------


def test_fiche_saine_ne_recoit_rien() -> None:
    """Cas qui PASSE : une fiche cohérente n'est pas signalée à tort."""
    fiche = _fiche()
    assert appliquer_controles_rg16(fiche) == []


def test_fiche_boiteuse_est_controlee_meme_sans_controle_prealable() -> None:
    """Cas qui ÉCHOUE : une fiche jamais contrôlée l'est quand même à l'écriture.

    C'est tout l'objet du Lot 1.5 : une voie qui n'a PAS appelé les contrôles
    ne peut pas faire entrer une fiche incohérente sans la signaler.
    """
    fiche = _fiche_boiteuse()
    assert fiche.anomalies == []  # aucune voie ne l'a contrôlée
    assert "cotes_incoherentes" in {a.code for a in appliquer_controles_rg16(fiche)}


# ---------------------------------------------------------------------------
# Rejouable : on remplace, on ne cumule pas
# ---------------------------------------------------------------------------


def test_rejouer_ne_duplique_pas() -> None:
    """Appliquer deux fois rend deux fois la même chose, pas le double."""
    fiche = _fiche_boiteuse()
    premier = list(appliquer_controles_rg16(fiche))
    second = list(appliquer_controles_rg16(fiche))
    assert [a.code for a in premier] == [a.code for a in second]
    assert len(fiche.anomalies) == len(premier)


def test_une_anomalie_rg16_ancienne_est_remplacee_pas_cumulee() -> None:
    """Une fiche redevenue saine ne traîne pas l'anomalie d'une extraction passée."""
    fiche = _fiche()
    fiche.anomalies = [Anomalie(code="cotes_incoherentes", gravite="moyenne", message="ancienne lecture")]
    assert appliquer_controles_rg16(fiche) == []


def test_les_anomalies_etrangeres_sont_conservees() -> None:
    """Ce module ne connaît pas la reprise ni l'orientation PDF : il n'y touche pas."""
    fiche = _fiche_boiteuse()
    etrangere = Anomalie(code="orientation_pdf_non_verifiee", gravite="moyenne", message="non vérifiée")
    fiche.anomalies = [etrangere]
    rendues = appliquer_controles_rg16(fiche)
    assert etrangere in rendues
    assert "cotes_incoherentes" in {a.code for a in rendues}


# ---------------------------------------------------------------------------
# L'invariant qui fait tenir l'idempotence
# ---------------------------------------------------------------------------


def test_tout_code_emis_est_declaré_dans_codes_rg16() -> None:
    """Si ce test échoue, `appliquer_controles_rg16` n'est plus idempotent.

    Un code émis et absent de `CODES_RG16` serait conservé comme « étranger »
    au second passage, puis doublé : c'est exactement la panne que ce test
    interdit.
    """
    fiches = [
        _fiche(),
        _fiche_boiteuse(),
        _fiche(cotes=[]),
        _fiche(client_nom="Spinnaker"),
        _fiche(cotes=[Cotes(jeu="finie", slu_m=12.0, sle_m=4.0, sf_m=3.0, spa_m2=250.0)]),
    ]
    emis: set[str] = set()
    for fiche in fiches:
        emis |= {a.code for a in appliquer_controles_rg16(fiche, gabarit=GABARIT)}
    assert emis, "aucune anomalie émise : le jeu d'essai ne prouve plus rien"
    assert emis <= CODES_RG16, f"codes émis hors CODES_RG16 : {sorted(emis - CODES_RG16)}"


# ---------------------------------------------------------------------------
# Le verrou est À L'ÉCRITURE — et il précède tout accès à la base
# ---------------------------------------------------------------------------


class _ArretAvantBase(RuntimeError):
    """Levée par la doublure : arrête `ecrire_fiche_resultat` avant la base."""


def test_le_verrou_est_pose_a_l_ecriture(monkeypatch: pytest.MonkeyPatch) -> None:
    """La preuve qui compte : l'écriture applique les RG16, avant toute base.

    `index` est un objet nu, sans `connect()` : si les contrôles étaient
    appelés après l'ouverture de la connexion, ce test échouerait sur
    l'attribut manquant au lieu d'observer l'arrêt de la doublure.
    """
    appel: dict[str, object] = {}

    def doublure(fiche: FicheExtraite, **kwargs: object) -> list[Anomalie]:
        appel["fiche"] = fiche
        appel["kwargs"] = kwargs
        raise _ArretAvantBase

    monkeypatch.setattr(persistance, "appliquer_controles_rg16", doublure)
    fiche = _fiche()
    with pytest.raises(_ArretAvantBase):
        persistance.ecrire_fiche_resultat(object(), fiche)
    assert appel["fiche"] is fiche


def test_le_contexte_est_transmis_a_l_ecriture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gabarit, références et référentiel voyagent jusqu'aux contrôles."""
    appel: dict[str, object] = {}

    def doublure(fiche: FicheExtraite, **kwargs: object) -> list[Anomalie]:
        appel["kwargs"] = kwargs
        raise _ArretAvantBase

    monkeypatch.setattr(persistance, "appliquer_controles_rg16", doublure)
    fiche = _fiche()
    with pytest.raises(_ArretAvantBase):
        persistance.ecrire_fiche_resultat(
            object(),
            fiche,
            gabarit=GABARIT,
            referentiel_bateaux={"voilier fictif 42"},
        )
    kwargs = appel["kwargs"]
    assert kwargs["gabarit"] is GABARIT
    assert kwargs["referentiel_bateaux"] == {"voilier fictif 42"}


def test_l_ecriture_refuse_toujours_une_fiche_sans_code() -> None:
    """Le garde-fou historique passe AVANT les contrôles et reste intact."""
    fiche = _fiche()
    fiche.code = ""
    with pytest.raises(ValueError, match="Fiche sans code"):
        persistance.ecrire_fiche_resultat(object(), fiche)
