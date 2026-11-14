"""Le livrable de recette ne doit pas se contredire lui-même.

Un rapport peut être faux de deux façons : annoncer ce qui n'a pas été
mesuré, ou se contredire entre son résumé et son détail. La seconde est la
plus dangereuse, parce que le lecteur qui voit « 52 prouvés » en tête
descend les tableaux en croyant les trouver — et tombe sur des lignes
marquées « NON PROUVÉ ». Il ne sait plus alors laquelle des deux affirmations
est la bonne, et il a raison de ne plus rien croire du tout.

Ce qui s'est produit, et que ce module interdit : un `str.replace` censé
passer les relèves de CI à la fonction de statut n'a RIEN remplacé — la
chaîne cherchée était une f-string multiligne, devenue une seule ligne après
formatage. Le remplacement a échoué en silence, sans erreur, et le rapport a
été publié avec un en-tête « 52 prouvés » au-dessus de tableaux où la moitié
des lignes disaient « NON PROUVÉ ».

Aucun test n'a bronché : rien ne comparait les deux. C'est précisément ce
qu'ajoute ce module.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
MATRICE = RACINE / "docs/recette/matrice-recette.json"
RELEVES = RACINE / "docs/recette/releves-ci.json"

# Les statuts qui peuvent conclure une ligne.
CONCLUSIFS = ("PROUVÉ (CI)", "PROUVÉ", "À TRANCHER", "PARTIEL", "NON PROUVÉ", "ÉCHEC")


@pytest.fixture(scope="module")
def rapport() -> str:
    """Le livrable tel qu'il est généré — pas tel qu'il est versionné."""
    if not MATRICE.exists() or not RELEVES.exists():
        pytest.skip("matrice ou relèves de CI absentes du dépôt")
    subprocess.run(  # noqa: S603
        [sys.executable, "scripts/rapport_recette.py"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        check=True,
    )
    # Le script écrit dans docs/ ; on relit ce qu'il a produit.
    produits = sorted(RACINE.glob("docs/RECETTE_EXHAUSTIVE_*.md"))
    assert produits, "le rapport n'a rien écrit"
    return produits[-1].read_text(encoding="utf-8")


def _lignes_items(rapport: str) -> list[tuple[str, str, str]]:
    """(item, verdict CI, statut final) pour chaque ligne de tableau d'item."""
    motif = re.compile(r"^\| \*\*([A-K]\d+)\*\* .*?\| ([^|]*?) \| \*\*([^|]+?)\*\* \|$", re.M)
    return [(m.group(1), m.group(2).strip(), m.group(3).strip()) for m in motif.finditer(rapport)]


def test_le_rapport_ne_se_contredit_pas(rapport: str) -> None:
    """Une ligne prouvée en CI ne peut pas conclure « NON PROUVÉ ».

    C'est la contradiction exacte qui a été publiée : cinquante-deux lignes
    annonçaient « PROUVÉ — 5ad5d8b » dans la colonne CI et concluaient
    « NON PROUVÉ ». Le lecteur ne peut pas arbitrer entre les deux, et un
    rapport qu'on ne peut pas croire ne sert à rien.
    """
    lignes = _lignes_items(rapport)
    assert lignes, "aucune ligne d'item analysée — la mise en forme a changé"

    incoherentes = [
        (item, ci, final) for item, ci, final in lignes if ci.startswith("PROUVÉ") and final.startswith("NON PROUVÉ")
    ]
    assert not incoherentes, (
        f"{len(incoherentes)} ligne(s) annoncent la CI prouvée et concluent non prouvé : "
        + "; ".join(f"{i} (CI: {c} / final: {f})" for i, c, f in incoherentes[:8])
    )


def test_le_resume_compte_ce_que_disent_les_lignes(rapport: str) -> None:
    """Le chiffre en tête du rapport doit être celui des lignes, pas un vœu."""
    lignes = _lignes_items(rapport)
    assert lignes, "aucune ligne d'item analysée"

    finaux = [final for _, _, final in lignes]
    prouves_lignes = sum(1 for f in finaux if f.startswith("PROUVÉ"))
    non_prouves_lignes = sum(1 for f in finaux if f.startswith(("NON PROUVÉ", "PARTIEL")))

    # « 2. **N sont prouvés** » — la seule phrase du résumé qui compte.
    annonce = re.search(r"\*\*(\d+) sont prouvés\*\*", rapport)
    assert annonce, "le résumé n'annonce pas le nombre d'items prouvés"
    assert int(annonce.group(1)) == prouves_lignes, (
        f"le résumé annonce {annonce.group(1)} prouvés, les lignes en comptent "
        f"{prouves_lignes}. Un écart entre les deux est exactement ce qui rend "
        "un rapport inutilisable."
    )

    trous = re.search(r"(\d+) trou\(x\)", rapport)
    assert trous, "le résumé n'annonce pas le nombre de trous"
    assert int(trous.group(1)) == non_prouves_lignes, (
        f"le résumé annonce {trous.group(1)} trou(s), les lignes en comptent {non_prouves_lignes}"
    )


def test_le_verdict_est_coherent_avec_les_trous(rapport: str) -> None:
    """« Prêt » ne se dit pas avec un trou, et réciproquement."""
    lignes = _lignes_items(rapport)
    trous = sum(1 for _, _, f in lignes if f.startswith(("NON PROUVÉ", "PARTIEL", "ÉCHEC")))

    if trous == 0:
        assert "prêt pour la recette manuelle du commanditaire" in rapport, (
            "aucun trou, mais le rapport ne dit pas « prêt »"
        )
    else:
        assert "PAS prêt" in rapport, f"{trous} trou(s) dans les lignes, mais le rapport ne dit pas « PAS prêt »"


def test_chaque_item_apparait_exactement_une_fois(rapport: str) -> None:
    """Un item compté deux fois gonfle le bilan ; oublié, il le vide."""
    import json

    if not MATRICE.exists():
        pytest.skip("matrice absente du dépôt")
    attendus = [ligne["item"] for ligne in json.loads(MATRICE.read_text(encoding="utf-8"))["lignes"]]
    lignes = _lignes_items(rapport)
    vus = [item for item, _, _ in lignes]

    assert sorted(vus) == sorted(attendus), (
        "les items du rapport ne sont pas ceux de la matrice. "
        f"Manquants : {sorted(set(attendus) - set(vus))} ; "
        f"en trop : {sorted(set(vus) - set(attendus))}"
    )
    assert len(vus) == len(set(vus)), "un item apparaît deux fois dans le rapport"


def test_chaque_ligne_conclut_par_un_statut_connu(rapport: str) -> None:
    """Un statut inventé est un statut qu'on ne peut pas interpréter."""
    lignes = _lignes_items(rapport)
    inconnus = sorted({f for _, _, f in lignes if f not in CONCLUSIFS})
    assert not inconnus, f"statut(s) non reconnu(s) dans le rapport : {inconnus}"
