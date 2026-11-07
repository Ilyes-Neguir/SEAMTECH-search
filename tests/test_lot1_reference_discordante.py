"""Lot 1.3 — la référence, comparée à trois (dossier, nom de fichier, document).

Contrat du plan, appliqué à la lettre :

* les trois sources sont comparées ;
* un écart produit l'anomalie ``reference_discordante`` qui **montre les trois
  valeurs** et part à l'examen ;
* cette anomalie est **NON BLOQUANTE** — le dossier entre en base et la fiche
  part en ``a_valider`` (RG3). Ce n'est PAS la mécanique du champ manquant
  (1.4), qui envoie en revue.

Une règle = un cas qui passe et un cas qui échoue. Les valeurs de référence
sont synthétiques ; le PDF utilisé au dépôt est celui d'exemple du dépôt.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from seamtech_search.fiches.anomalies import (
    ReferencesConstatees,
    evaluer_anomalies,
)
from seamtech_search.fiches.depot import deposer_dossier
from seamtech_search.fiches.modeles import Cotes, FicheExtraite
from tests.test_depot_transactionnel import (  # noqa: F401 - montures partagées
    ARCHIVE_7792,
    base_depot,
    dossier_ok,
)

RACINE = Path(__file__).resolve().parent.parent


def _fiche(**remplacements: object) -> FicheExtraite:
    """Fiche minimale, porteuse d'un jeu de cotes « finie » plausible."""
    donnees: dict[str, object] = {
        "code": "LOT1-REF-1",
        "client_nom": "Voilerie Fictive",
        "bateau_nom": "Voilier Fictif 42",
        "type_voile_libelle": "Grand-voile",
        "cotes": [Cotes(jeu="finie", slu_m=12.5, sle_m=4.2, sf_m=3.9, spa_m2=30.0)],
    }
    donnees.update(remplacements)
    return FicheExtraite(**donnees)  # type: ignore[arg-type]


def _codes(references: ReferencesConstatees, **kwargs: object) -> set[str]:
    return {a.code for a in evaluer_anomalies(_fiche(), references=references, **kwargs)}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Règle — trois écritures de la même référence
# ---------------------------------------------------------------------------


def test_trois_references_identiques_ne_levent_rien() -> None:
    """Cas qui PASSE : les trois sources disent la même chose."""
    refs = ReferencesConstatees(document="ZZ 075A", fichier="ZZ 075A", dossier="ZZ 075A")
    assert _codes(refs) == set()


def test_trois_references_differentes_sont_signalees() -> None:
    """Cas qui ÉCHOUE : trois écritures qui ne disent pas la même chose."""
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier="ST 430")
    assert "reference_discordante" in _codes(refs)


def test_deux_references_egales_ne_levent_rien() -> None:
    """Cas qui PASSE avec deux sources seulement."""
    refs = ReferencesConstatees(document="7792-SO", fichier="7792-SO", dossier=None)
    assert "reference_discordante" not in _codes(refs)


def test_deux_references_differentes_sont_signalees() -> None:
    """Cas qui ÉCHOUE avec deux sources seulement."""
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier=None)
    assert "reference_discordante" in _codes(refs)


def test_une_seule_reference_connue_ne_leve_rien() -> None:
    """Une seule source lue : il n'y a rien à comparer."""
    refs = ReferencesConstatees(document="7792-SO", fichier=None, dossier=None)
    assert "reference_discordante" not in _codes(refs)


def test_aucune_reference_ne_leve_rien() -> None:
    assert "reference_discordante" not in _codes(ReferencesConstatees())


def test_source_muette_nest_pas_un_desaccord() -> None:
    """Une source sans référence ne contredit pas les deux autres."""
    refs = ReferencesConstatees(document="7792-SO", fichier="7792-SO", dossier=None)
    assert _codes(refs) == set()


def test_casse_et_espaces_ne_sont_pas_un_desaccord() -> None:
    """« zz 075a » et « ZZ 075A » sont la même référence."""
    refs = ReferencesConstatees(document="ZZ 075A", fichier="zz  075a", dossier="ZZ 075A")
    assert "reference_discordante" not in _codes(refs)


def test_sans_references_transmises_le_controle_ne_sapplique_pas() -> None:
    """Il ne devine pas ce qu'il n'a pas reçu."""
    assert "reference_discordante" not in {a.code for a in evaluer_anomalies(_fiche())}


# ---------------------------------------------------------------------------
# Qualité du signal : les trois valeurs, montrées — et l'écart dit non bloquant
# ---------------------------------------------------------------------------


def test_les_trois_valeurs_sont_dans_le_message() -> None:
    """Exigence du plan : l'anomalie MONTRE les trois valeurs."""
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier="ST 430")
    refus = [a for a in evaluer_anomalies(_fiche(), references=refs) if a.code == "reference_discordante"]
    assert len(refus) == 1
    for valeur in ("7792-SO", "ZZ 075A", "ST 430"):
        assert valeur in refus[0].message


def test_les_provenances_sont_nommees_dans_l_ordre() -> None:
    """« dossier », « nom de fichier » puis « document » — lisibles pour l'approbateur."""
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier="ST 430")
    refus = [a for a in evaluer_anomalies(_fiche(), references=refs) if a.code == "reference_discordante"]
    message = refus[0].message
    assert message.index("dossier") < message.index("nom de fichier") < message.index("document")


def test_lanomalie_dit_que_le_depot_nest_pas_bloque() -> None:
    """1.3 est NON BLOQUANT : le message le dit, l'approbateur ne peut s'y tromper."""
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier="ST 430")
    refus = [a for a in evaluer_anomalies(_fiche(), references=refs) if a.code == "reference_discordante"]
    assert "enregistré" in refus[0].message
    assert "ne bloque pas" in refus[0].message
    assert refus[0].gravite == "moyenne"


def test_la_discordance_est_vue_meme_sans_cotes() -> None:
    """Elle porte sur l'identité : une fiche sans cotes n'y échappe pas."""
    fiche = _fiche(cotes=[])
    refs = ReferencesConstatees(document="7792-SO", fichier="ZZ 075A", dossier="ST 430")
    codes = {a.code for a in evaluer_anomalies(fiche, references=refs)}
    assert "reference_discordante" in codes
    assert "champ_manquant" in codes  # le contrôle historique reste posé


# ---------------------------------------------------------------------------
# Au dépôt : le dossier entre quand même (PDF d'exemple, base jetable)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
class TestDepotAvecReferencesDiscordantes:
    """La preuve qui compte : un dossier discordant ENTRE en base."""

    def test_dossier_concordant_n_est_pas_signale(self, base_depot: dict, dossier_ok: Path) -> None:  # noqa: F811
        """Cas qui PASSE au dépôt : nom de fichier et document d'accord."""
        index = base_depot["index"]
        resultat = deposer_dossier(index, dossier_ok)
        assert resultat["statut"] == "traite"
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT code FROM fiche_anomalie")
                codes = {ligne[0] for ligne in cursor.fetchall()}
        assert "reference_discordante" not in codes

    def test_dossier_discordant_entre_et_est_signale(self, base_depot: dict, tmp_path: Path) -> None:  # noqa: F811
        """Cas qui ÉCHOUE au dépôt : trois références qui diffèrent.

        Dossier « ST 430 », fichier « ZZ 075a_ffab.pdf », document « 7792-SO » :
        les trois sources ne disent pas la même chose. Le dossier doit quand
        même être traité — c'est toute la différence avec le champ manquant.
        """
        index = base_depot["index"]
        dossier = tmp_path / "ST 430"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "ZZ 075a_ffab.pdf")

        resultat = deposer_dossier(index, dossier)

        # NON BLOQUANT : le dossier est traité, la référence retenue reste
        # celle lue dans le document.
        assert resultat["statut"] == "traite"
        assert resultat["fiche"] == "7792-SO"

        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT code, message FROM fiche_anomalie")
                lignes = cursor.fetchall()
                cursor.execute("SELECT statut FROM fiche")
                statut = cursor.fetchone()[0]

        codes = {code for code, _ in lignes}
        assert "reference_discordante" in codes
        # Les trois valeurs sont montrées, pas seulement l'écart.
        message = next(m for c, m in lignes if c == "reference_discordante")
        for valeur in ("ST 430", "ZZ 075A", "7792-SO"):
            assert valeur in message
        # Et la fiche n'est pas validée pour autant (RG3).
        assert statut == "a_valider"
