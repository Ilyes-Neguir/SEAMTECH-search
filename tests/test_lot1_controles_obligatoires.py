"""Lot 1.5 — les contrôles RG16 existants deviennent OBLIGATOIRES sur toute fiche.

Contrat du plan, appliqué à la lettre : les contrôles « pas seulement sur
certaines voies ». Le verrou est posé À L'ÉCRITURE, parce que c'est le seul
endroit par où passe nécessairement une fiche, quelle que soit la voie qui
l'a produite — gabarit, reprise, import.

Une règle = un cas qui passe et un cas qui échoue.
"""

from __future__ import annotations

from collections.abc import Iterator

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
# Le verrou est À L'ÉCRITURE — prouvé par une écriture réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_ecriture() -> Iterator[dict[str, object]]:
    """Base PostgreSQL jetable et migrée, pour une écriture RÉELLE.

    Pourquoi une vraie base, et non plus un objet nu.

    Le verrou RG16 est appliqué dans ``_ecrire_fiche_dans``, APRÈS les
    projections Lot 3a — ordre non négociable depuis la correction du
    06/11 : la projection CANONISE (un guindant lu « 6600 mm » y devient
    6,6 m) et juge la plausibilité sur ces valeurs canonisées ; évaluer les
    contrôles avant elle déclarait « implausible » une fiche saine (mesuré :
    ``test_lot3a_extraction_voile``). Les projections touchant la base, la
    preuve a besoin d'une base.

    Elle n'en est pas plus faible, elle est plus forte : l'ancienne preuve
    constatait qu'une DOUBLURE était appelée ; celle-ci constate que
    l'anomalie est réellement ÉCRITE et relue.
    """
    from pathlib import Path

    from tests.conftest import (
        DATABASE_URL,
        _creer_base_jetable,
        _supprimer_base_jetable,
    )

    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-lot15-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    try:
        yield {"index": index, "url": url_base}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)


def _anomalies_en_base(index: object, code_fiche: str) -> set[str]:
    """Les codes d'anomalie réellement persistés pour une fiche."""
    with index.connect() as connexion:  # type: ignore[attr-defined]
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT a.code FROM fiche_anomalie a JOIN fiche f ON f.id_fiche = a.id_fiche"
                " WHERE f.code = %s",
                (code_fiche,),
            )
            return {ligne[0] for ligne in cursor.fetchall()}


@pytest.mark.postgres
def test_le_verrou_est_pose_a_l_ecriture(base_ecriture: dict[str, object]) -> None:
    """La preuve qui compte : l'écriture applique les RG16 et les PERSISTE.

    La fiche n'est passée par aucun extracteur : elle est construite à la
    main, techniquement incohérente (SLU plus courte que la bordure). Si le
    verrou n'était posé qu'à l'extraction, aucune anomalie n'apparaîtrait.
    """
    index = base_ecriture["index"]
    fiche = _fiche_boiteuse()

    persistance.ecrire_fiche_resultat(index, fiche)

    assert "cotes_incoherentes" in _anomalies_en_base(index, fiche.code)


@pytest.mark.postgres
def test_le_contexte_est_transmis_a_l_ecriture(base_ecriture: dict[str, object]) -> None:
    """Gabarit, références et référentiel voyagent jusqu'aux contrôles.

    Deux écritures de la même fiche, seul le référentiel diffère : le
    bateau est hors référentiel quand le référentiel le dit, et ne l'est
    pas quand il le contient. Le contexte est donc bien transmis, et son
    effet est mesurable en base — pas seulement dans un appel espionné.
    """
    index = base_ecriture["index"]

    hors_referentiel = _fiche(code="LOT1-CTX-HORS")
    persistance.ecrire_fiche_resultat(
        index, hors_referentiel, referentiel_bateaux={"un autre modele"}
    )
    assert "bateau_hors_referentiel" in _anomalies_en_base(index, "LOT1-CTX-HORS")

    dans_referentiel = _fiche(code="LOT1-CTX-DANS")
    persistance.ecrire_fiche_resultat(
        index, dans_referentiel, referentiel_bateaux={"voilier fictif 42"}
    )
    assert "bateau_hors_referentiel" not in _anomalies_en_base(index, "LOT1-CTX-DANS")


def test_l_ecriture_refuse_toujours_une_fiche_sans_code() -> None:
    """Le garde-fou historique passe AVANT les contrôles et reste intact."""
    fiche = _fiche()
    fiche.code = ""
    with pytest.raises(ValueError, match="Fiche sans code"):
        persistance.ecrire_fiche_resultat(object(), fiche)
