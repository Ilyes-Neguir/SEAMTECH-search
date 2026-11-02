"""Garde-fou du fichier d'écarts Windows — un écart doit être VÉRIFIABLE.

Pourquoi ce fichier existe : ``ci/ecarts-windows.txt`` est le moyen le plus
commode de transformer une ignorance en périmètre. On y écrit un motif, une
raison vraisemblable, et le revoilà vert. Ce test rend cette facilité
coûteuse :

* chaque motif doit correspondre à un fichier de test qui EXISTE et, quand un
  nom de test est donné, à un test qui existe dans ce fichier — un motif
  erroné ne protège rien et ne nomme rien ;
* chaque ligne doit porter une RAISON lisible (pas un mot-clé) : c'est la
  raison qu'on relit dans six mois, pas le motif ;
* aucun doublon, aucun motif vide.

Ce qu'il ne fait pas, et qui reste une vigilance humaine : il ne vérifie pas
que la raison est VRAIE. La méthode employée pour l'établir est écrite en tête
du fichier d'écarts — exécuter la suite POSIX avec les primitives déclarées
absentes, et constater lesquels échouent avec l'OSError nommé de
``seamtech_search/plateforme.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
FICHIER_ECARTS = RACINE / "ci" / "ecarts-windows.txt"

# Le nom de test accepte tout sauf le séparateur « | » : un test paramétré
# s'écrit ``test_plages[bytes=-0]``, et le signe « = » de ses paramètres doit
# pouvoir figurer dans un écart sans casser la lecture du fichier.
_MOTIF_LIGNE = re.compile(r"^(tests/[A-Za-z0-9_/\.]+\.py)(?:::([^|]*))?\|(.+)$")
_MOTIF_CAUSE = re.compile(r"^CAUSE\|(.+)\|(.+)$")


def _lignes_ecarts() -> list[tuple[int, str]]:
    """Lignes d'écarts numérotées, commentaires et lignes vides exclus."""
    if not FICHIER_ECARTS.is_file():
        pytest.skip("fichier d'écarts Windows absent")
    resultat: list[tuple[int, str]] = []
    for numero, ligne in enumerate(FICHIER_ECARTS.read_text(encoding="utf-8").splitlines(), start=1):
        if ligne.strip() and not ligne.strip().startswith("#"):
            resultat.append((numero, ligne.strip()))
    return resultat


def test_chaque_ligne_a_un_motif_un_fichier_et_une_raison() -> None:
    for numero, ligne in _lignes_ecarts():
        if ligne.startswith("CAUSE|"):
            assert _MOTIF_CAUSE.match(ligne), (
                f"ci/ecarts-windows.txt:{numero} — forme attendue "
                f"« CAUSE|<message du produit>|<raison> », lu : {ligne!r}"
            )
            continue
        assert _MOTIF_LIGNE.match(ligne), (
            f"ci/ecarts-windows.txt:{numero} — forme attendue "
            f"« tests/fichier.py::[test]|raison lisible », lu : {ligne!r}"
        )


def test_chaque_raison_est_une_phrase_pas_un_mot_cle() -> None:
    """Une raison d'une dizaine de caractères ne dit rien à qui la relit."""
    for numero, ligne in _lignes_ecarts():
        if ligne.startswith("CAUSE|"):
            concordance_cause = _MOTIF_CAUSE.match(ligne)
            if not concordance_cause:
                continue
            raison, message = concordance_cause.group(2).strip(), concordance_cause.group(1).strip()
            # Un écart par cause trop court engloutirait n'importe quel échec :
            # « erreur » ferait passer tout le rouge. Le message doit nommer une
            # capacité, pas un incident.
            assert len(message) >= 25, (
                f"ci/ecarts-windows.txt:{numero} — cause trop générique pour être "
                f"opposable ({len(message)} caractères) : {message!r}"
            )
        else:
            concordance = _MOTIF_LIGNE.match(ligne)
            if not concordance:
                continue  # déjà signalé par le test de forme
            raison = concordance.group(3).strip()
        assert len(raison) >= 40, (
            f"ci/ecarts-windows.txt:{numero} — raison trop courte pour être "
            f"opposable ({len(raison)} caractères) : {raison!r}"
        )


def test_chaque_motif_correspond_a_un_fichier_de_test_qui_existe() -> None:
    for numero, ligne in _lignes_ecarts():
        concordance = _MOTIF_LIGNE.match(ligne)
        if not concordance:
            continue
        chemin = RACINE / concordance.group(1)
        assert chemin.is_file(), f"ci/ecarts-windows.txt:{numero} — {concordance.group(1)} n'existe pas"


def test_chaque_test_nomme_existe_dans_son_fichier() -> None:
    """Un motif qui ne nomme aucun test existant est un écart qui ne protège rien."""
    for numero, ligne in _lignes_ecarts():
        concordance = _MOTIF_LIGNE.match(ligne)
        if not concordance:
            continue
        nom = concordance.group(2)
        if not nom:
            continue  # écart à l'échelle du fichier : volontaire, et dit comme tel
        fonction = nom.split("[", 1)[0].split("::")[-1]
        if not fonction:
            continue
        contenu = (RACINE / concordance.group(1)).read_text(encoding="utf-8")
        assert re.search(rf"^\s*(async\s+)?def\s+{re.escape(fonction)}\s*\(", contenu, re.M), (
            f"ci/ecarts-windows.txt:{numero} — {fonction} est introuvable dans "
            f"{concordance.group(1)} : le motif nomme un test qui n'existe pas"
        )


def test_aucun_motif_en_double() -> None:
    """Un motif répété est un écart qu'on a oublié d'avoir déjà écrit."""
    par_test = [ligne.split("|", 1)[0] for _, ligne in _lignes_ecarts() if not ligne.startswith("CAUSE|")]
    par_cause = [ligne.split("|")[1] for _, ligne in _lignes_ecarts() if ligne.startswith("CAUSE|")]
    for nom, liste in (("motifs de test", par_test), ("causes", par_cause)):
        doublons = {valeur for valeur in liste if liste.count(valeur) > 1}
        assert not doublons, f"{nom} répétés dans ci/ecarts-windows.txt : {sorted(doublons)}"


def test_le_fichier_rappelle_comment_les_ecarts_ont_ete_etablis() -> None:
    """La méthode doit être écrite : sinon la liste devient une opinion."""
    if not FICHIER_ECARTS.is_file():
        pytest.skip("fichier d'écarts Windows absent")
    texte = FICHIER_ECARTS.read_text(encoding="utf-8")
    assert "primitives" in texte.lower(), (
        "l'en-tête doit rappeler comment chaque écart a été établi "
        "(suite POSIX exécutée avec les primitives déclarées absentes)"
    )
