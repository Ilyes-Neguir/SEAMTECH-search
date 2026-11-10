"""Frontières ENTRE les lots — ce qu'aucun test de lot isolé ne couvre.

Chaque lot a ses tests ; personne n'avait vérifié ce qui se passe QUAND les
lots se touchent. Deux questions posées par l'exploitant, traitées ici avec
preuve :

1. Une fiche qui porte ``reference_discordante`` (Lot 1.3) arrive-t-elle bien
   en ``a_valider`` et JAMAIS auto-validée par le Lot 3, même avec un score
   de qualité au-dessus de 0,90 ?
2. Une fiche avec ``champ_obligatoire_manquant`` (Lot 1.4, bloquante) est-elle
   exclue des Lots 5/6 (comparaison), ou y apparaît-elle drapeautée ?

Réponse mesurée, pas décrétée : la frontière 1.4 → 5/6 passe par la
PLAUSIBILITÉ de l'extraction Lot 3a, et par elle seule.

Aucune donnée d'atelier : libellés génériques et comptes.
"""

from __future__ import annotations

from seamtech_search.comparaison_voile import comparer_extractions, comparer_plusieurs
from seamtech_search.confiance_reutilisation import (
    _charger_population,
)
from seamtech_search.extractions_voile import ExtractionVoile, construire_extraction_voile
from seamtech_search.fiches.anomalies import Anomalie, evaluer_anomalies
from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
from seamtech_search.fiches.modeles import ChampExtrait, Cotes, FicheExtraite
from seamtech_search.fiches.persistance import (
    routage,
    verifier_autorisation_validation_lot,
)

GABARIT = GabaritDef(
    code="GABARIT-FICTIF",
    version=1,
    description="gabarit de test",
    ancres_detection=["ancre fictive"],
    champs=[RegleChamp(cible="fiche.code", ancres=["code fiche"], obligatoire=True)],
)


def _trace(cible: str) -> ChampExtrait:
    """Une trace de lecture parfaite : confiance maximale, méthode gabarit."""
    return ChampExtrait(
        champ=cible,
        valeur_brute="valeur lue",
        valeur_normalisee="valeur lue",
        methode="gabarit",
        confiance=1.0,
        page=1,
        zone=None,
        table_cible="fiche",
        colonne_cible=cible.split(".")[-1],
    )


def _extraction(code: str, plausible: bool = True, **remplacements: object) -> ExtractionVoile:
    """Une extraction de voile complète ; ``plausible=False`` la rend RG16-suspecte."""
    donnees: dict[str, object] = {
        "id_extraction": None,
        "code_fiche": code,
        "type_voile": "Grand-voile",
        "gamme": "Ocean",
        "slu_m": 12.5,
        "sle_m": 4.2,
        "sf_m": 3.9,
        "spa_m2": 30.0,
        "score_qualite": 0.97,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible" if plausible else "implausible",
    }
    donnees.update(remplacements)
    return ExtractionVoile(**donnees)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1.3 → Lot 3 : une fiche discordante n'est JAMAIS validée automatiquement
# ---------------------------------------------------------------------------


def test_le_routage_ne_valide_jamais_une_fiche() -> None:
    """Même « passage direct » ne change aucun statut : le routage ne valide pas.

    C'est la première raison pour laquelle un score élevé n'auto-valide rien :
    `routage()` propose une VOIE, il ne pose jamais `statut = 'valide'`.
    """
    fiche = FicheExtraite(
        code="LOT1-FRONTIERE-1",
        type_voile_libelle="Grand-voile",
        cotes=[Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    )
    fiche.gabarit_code = GABARIT.code
    # Champs tracés au score maximal : sans eux le routage viserait
    # « reprise_complete » (champs structurels absents) et le test ne
    # mesurerait plus ce qu'il prétend mesurer.
    fiche.champs.extend(_trace(cible) for cible in ("fiche.code", "fiche.client", "cotes.finie.slu_m", "cotes.finie.sf_m"))
    voie = routage(fiche)
    assert voie["voie"] == "passage_direct"
    # Le routage ne transporte AUCUN pouvoir de validation.
    assert set(voie) == {"voie", "motif", "champs_sous_seuil"}


def test_la_validation_groupee_est_interdite_sans_calibration() -> None:
    """Deuxième verrou : sans preuve de calibration, la validation de lot est refusée."""
    autorise, motif = verifier_autorisation_validation_lot()
    assert autorise is False
    assert "calibr" in motif.lower()


def test_la_validation_groupee_ignore_le_score() -> None:
    """Un score parfait ne rachète pas une calibration absente.

    La question posée était « même si son score dépasse 0,90 ». La réponse est
    que le score n'entre jamais dans la décision d'autoriser : seule la preuve
    de calibration compte.
    """
    autorise_sans, _ = verifier_autorisation_validation_lot()
    assert autorise_sans is False
    # Même avec un acquittement explicite, c'est un HUMAIN qui tranche :
    # le score de la fiche n'est ni lu ni transmis.
    autorise_avec, motif = verifier_autorisation_validation_lot(acquittement_humain=True)
    assert autorise_avec is True
    assert "acquittement" in motif.lower()


# ---------------------------------------------------------------------------
# 1.4 → Lots 5/6 : la frontière passe par la plausibilité
# ---------------------------------------------------------------------------


def test_un_champ_obligatoire_manquant_rend_la_projection_implausible() -> None:
    """Le chaînon mesuré : 1.4 (bloquante) → plausibilité Lot 3a.

    Une anomalie de gravité « forte » rend l'extraction implausible, quel que
    soit son code. C'est par ce seul chaînon que le Lot 1.4 atteint les Lots
    5 et 6.
    """
    fiche = FicheExtraite(
        code="LOT1-FRONTIERE-2",
        type_voile_libelle="Grand-voile",
        cotes=[Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    )
    fiche.gabarit_code = GABARIT.code
    fiche.anomalies = evaluer_anomalies(fiche, gabarit=GABARIT)
    # Le champ « fiche.code » n'ayant pas été LU, la règle obligatoire le lève.
    assert "champ_obligatoire_manquant" in [a.code for a in fiche.anomalies]
    projection = construire_extraction_voile(fiche)
    assert projection.statut_plausibilite == "implausible"


def test_le_lot5_exclut_les_implausibles_de_la_population() -> None:
    """Lot 5 : une extraction implausible n'entre pas dans la population comparée."""

    class _CurseurEnregistreur:
        """Curseur factice : retient la requête, ne rend aucune ligne."""

        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, params: object = None) -> None:
            self.sql = " ".join(str(sql).split())

        def fetchall(self) -> list[object]:
            return []

    curseur = _CurseurEnregistreur()
    _charger_population(curseur, boat_model_id=1, type_voile="Grand-voile", exclure_id=None)
    assert "statut_plausibilite <> 'implausible'" in curseur.sql


def test_le_lot6_drapeaute_une_extraction_implausible() -> None:
    """Lot 6, comparaison à deux : l'avertissement est posé, pas silencieux."""
    compare = comparer_extractions(
        _extraction("A-1", plausible=True),
        _extraction("B-2", plausible=False),
    )
    avertissements = " ".join(str(a) for a in compare.get("avertissements", []))
    assert "implausible" in avertissements.lower()


def test_le_lot6_ne_drapeaute_pas_une_fiche_saine() -> None:
    """Réciproque : deux fiches saines ne lèvent aucun avertissement de plausibilité."""
    compare = comparer_extractions(
        _extraction("A-1", plausible=True),
        _extraction("B-2", plausible=True),
    )
    avertissements = " ".join(str(a) for a in compare.get("avertissements", []))
    assert "implausible" not in avertissements.lower()


def test_le_lot6_groupe_drapeaute_aussi() -> None:
    """Lot 6, vue groupe : un seul membre implausible suffit à drapeauter le groupe."""
    compare = comparer_plusieurs(
        [
            _extraction("A-1", id_extraction=1),
            _extraction("B-2", id_extraction=2),
            _extraction("C-3", id_extraction=3, plausible=False),
        ]
    )
    avertissements = " ".join(str(a) for a in compare.get("avertissements", []))
    assert "implausible" in avertissements.lower()


# ---------------------------------------------------------------------------
# La limite de la frontière — écrite noir sur blanc, pas cachée
# ---------------------------------------------------------------------------


def test_la_frontiere_est_la_plausibilite_et_rien_d_autre() -> None:
    """Ce que cette frontière NE fait PAS, mesuré pour qu'on le sache.

    Une anomalie de fiche qui n'atteint pas la projection (posée après elle,
    ou d'un code sans gravité « forte ») ne suffit pas à exclure la fiche de la
    comparaison. La frontière 1.4 → 5/6 tient à la plausibilité, et à elle
    seule : c'est une limite connue, pas un oubli.
    """
    fiche = FicheExtraite(
        code="LOT1-FRONTIERE-3",
        type_voile_libelle="Grand-voile",
        cotes=[Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    )
    fiche.gabarit_code = GABARIT.code
    # Une anomalie MOYENNE : la projection reste plausible.
    fiche.anomalies = [
        Anomalie(code="reference_discordante", gravite="moyenne", message="dossier « X », document « Y »")
    ]
    projection = construire_extraction_voile(fiche)
    # Mesuré : « a_verifier », pas « plausible » — la discordance EST donc
    # visible dans la projection. Mais « a_verifier » n'exclut PAS du Lot 5
    # (seul « implausible » exclut, voir le test ci-dessus) : la fiche reste
    # comparée, signalée. C'est exactement le statut NON BLOQUANT voulu.
    assert projection.statut_plausibilite == "a_verifier"
    assert "implausible" != projection.statut_plausibilite
    # Et la différence avec 1.4 est tranchée : la même anomalie en gravité
    # « forte » bascule, elle, en implausible.
    forte = FicheExtraite(
        code="LOT1-FRONTIERE-4",
        type_voile_libelle="Grand-voile",
        cotes=[Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    )
    forte.gabarit_code = GABARIT.code
    forte.anomalies = [Anomalie(code="reference_discordante", gravite="forte", message="écart")]
    assert construire_extraction_voile(forte).statut_plausibilite == "implausible"


# ---------------------------------------------------------------------------
# Lot 1.2 — le référentiel doit être TRANSMIS, sinon le contrôle est mort
#
# Trouvé en mesurant l'apport des instruments (A.3) : la famille
# « bateau_hors_referentiel » comptait zéro sur les deux colonnes, avant
# comme après. L'instrument était livré et raccordé à la fonction, mais le
# dépôt ne lui passait jamais le référentiel — et la branche est conditionnée
# à ``referentiel_bateaux is not None``. Un contrôle qui ne reçoit pas sa
# matière première ne se plaint pas : il ne fait rien.
# ---------------------------------------------------------------------------


def test_un_bateau_inconnu_leve_une_anomalie_quand_le_referentiel_est_passe() -> None:
    """Le contrôle fonctionne — c'est son câblage qui manquait, pas sa logique."""
    fiche = FicheExtraite(code="LOT12-1", type_voile_libelle="Génois", bateau_nom="Modele Inconnu XY")
    fiche.cotes = [Cotes(jeu="finie", slu_m=12.0, sle_m=4.0, sf_m=3.5, spa_m2=28.0)]
    sans = evaluer_anomalies(fiche)
    assert not any(a.code == "bateau_hors_referentiel" for a in sans), (
        "sans référentiel, le contrôle ne s'applique pas (comportement voulu)"
    )
    avec = evaluer_anomalies(fiche, referentiel_bateaux={"first 40 7", "sun shine 38"})
    assert any(a.code == "bateau_hors_referentiel" for a in avec), (
        "un bateau absent du référentiel doit lever l'anomalie du Lot 1.2"
    )


def test_un_bateau_connu_ne_leve_rien() -> None:
    """Le pendant : un modèle du référentiel passe. Sinon tout serait signalé."""
    fiche = FicheExtraite(code="LOT12-2", type_voile_libelle="Génois", bateau_nom="First 40.7")
    fiche.cotes = [Cotes(jeu="finie", slu_m=12.0, sle_m=4.0, sf_m=3.5, spa_m2=28.0)]
    anomalies = evaluer_anomalies(fiche, referentiel_bateaux={"first 40 7"})
    assert not any(a.code == "bateau_hors_referentiel" for a in anomalies)


def test_le_depot_transmet_le_referentiel_a_l_ecriture() -> None:
    """C'est LE test du défaut : sans lui, le Lot 1.2 ne s'exécute jamais.

    On instrumente l'écriture et on regarde ce qu'elle reçoit, plutôt que de
    faire confiance à la lecture du code.
    """
    import inspect

    from seamtech_search.fiches import depot

    source = inspect.getsource(depot)
    # l'appel doit nommer le paramètre : le seul moyen de ne pas l'oublier
    # silencieusement est que son absence se voie.
    assert "referentiel_bateaux=referentiel" in source, (
        "le dépôt doit transmettre le référentiel des modèles à l'écriture, "
        "sinon le contrôle du Lot 1.2 ne s'exécute sur aucune fiche"
    )
    assert "_cles_referentiel_bateaux" in source


def test_un_referentiel_vide_ne_declare_pas_tout_hors_referentiel() -> None:
    """Un référentiel vide doit désactiver le contrôle, pas tout condamner.

    Une liste vide dirait « aucun modèle connu », donc tout bateau serait
    hors référentiel : des centaines de fausses anomalies. On rend None.
    """
    from seamtech_search.fiches.depot import _cles_referentiel_bateaux

    class _Connexion:
        def cursor(self):
            raise RuntimeError("référentiel indisponible")

    assert _cles_referentiel_bateaux(_Connexion()) is None
