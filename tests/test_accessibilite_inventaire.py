"""Section J — chaque écran de l'inventaire doit être audité, un par un.

Le garde-fou de CI se contentait d'un plancher : « au moins 8 écrans scannés ».
Un plancher ne voit pas ce qui manque. Une campagne peut rendre 82 constats
en ne couvrant que 14 écrans, rejoués sous six rôles — et c'est exactement ce
qui s'est produit : les deux écrans à paramètre, ``/fiches/[code]`` et
``/dossier/[code]``, n'apparaissaient dans aucun constat.

Ce module vérifie l'inventaire, pas un décompte : il extrait les écrans
réellement présents dans ``frontend/app`` et exige que le plan d'audit en
réponde, un par un.

Pourquoi lire les SOURCES plutôt que ``frontend/accessibilite/rapport-axe.json``
: ce rapport est un instantané publié par la CI, et il est périmé dès le
commit suivant — il annonçait 73 écrans quand la campagne en scannait 82. Un
test assis sur un instantané mesure l'âge du fichier, pas l'application. Les
sources, elles, ne périment pas : si un écran est ajouté et oublié ici, ce
test échoue dès le commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
APP = RACINE / "frontend" / "app"
SPEC = RACINE / "frontend" / "e2e" / "accessibilite.spec.ts"

# Les routes dynamiques ([code], [id]…) ne sont pas atteignables sans
# paramètre : une liste plate d'URL les omet toujours.
_PARAMETRE = re.compile(r"\[[^\]]+\]")


def _ecrans_next() -> dict[str, bool]:
    """Chaque écran de l'application, et s'il porte un paramètre d'URL.

    La clé est le chemin tel que Next.js le sert : ``/fiches/[code]``. La
    valeur dit si un audit doit injecter un identifiant réel.
    """
    if not APP.is_dir():
        return {}
    ecrans: dict[str, bool] = {}
    for page in sorted(APP.rglob("page.tsx")):
        if "node_modules" in page.parts:
            continue
        relatif = page.parent.relative_to(APP)
        chemin = "/" if str(relatif) == "." else f"/{relatif.as_posix()}"
        ecrans[chemin] = bool(_PARAMETRE.search(chemin))
    return ecrans


def _texte_spec() -> str:
    return SPEC.read_text(encoding="utf-8") if SPEC.exists() else ""


def test_les_sources_sont_disponibles() -> None:
    """Sans sources, ce module ne prouve rien et doit le dire plutôt que passer."""
    if not APP.is_dir() or not SPEC.exists():
        pytest.skip("frontend/app ou le spec d'accessibilité est absent du dépôt")
    assert _ecrans_next(), "aucun écran Next.js trouvé"


def test_chaque_ecran_de_l_inventaire_est_dans_le_plan_d_audit() -> None:
    """Section J — aucun écran de l'inventaire ne doit rester non audité.

    Un écran à paramètre est couvert si le plan injecte un identifiant réel
    (gabarit ``${…}``) et pas seulement s'il cite le préfixe : citer
    ``/fiches`` n'a jamais audité ``/fiches/CODE``, et c'est précisément
    l'angle mort qui a laissé deux écrans sans aucun constat.
    """
    ecrans = _ecrans_next()
    if not ecrans:
        pytest.skip("frontend/app absent du dépôt")
    texte = _texte_spec()
    if not texte:
        pytest.skip("spec d'accessibilité absent du dépôt")

    manquants: list[str] = []
    for chemin, parametre in sorted(ecrans.items()):
        if parametre:
            prefixe = chemin[: chemin.index("[")].rstrip("/")
            # Un gabarit « /fiches/${code} » prouve qu'un identifiant réel est
            # injecté ; un littéral nu ne le prouve pas.
            gabarit = re.search(
                re.escape(prefixe) + r"/\$\{",
                texte,
            )
            if not gabarit:
                manquants.append(f"{chemin} (audit de détail absent)")
            continue
        if not re.search(rf'["\'`]{re.escape(chemin)}["\'`]', texte):
            manquants.append(chemin)

    assert not manquants, (
        f"{len(manquants)} écran(s) de l'inventaire jamais audités : "
        f"{', '.join(manquants)}. Un écran ajouté sans l'ajouter au plan "
        "d'accessibilité doit faire échouer ce test, pas passer inaperçu."
    )


def _balises_reelles(contenu: str, balise: str) -> int:
    """Occurrences RÉELLES d'une balise, commentaires exclus.

    Sans cette précaution le test ci-dessous compterait deux ``<main>`` dans
    ``app/layout.tsx`` : le second est dans un commentaire qui explique
    justement pourquoi il en faut un. Un test qui lit les commentaires comme
    du code rend un verdict faux.
    """
    sans_commentaires = re.sub(r"/\*.*?\*/", " ", contenu, flags=re.DOTALL)
    sans_commentaires = re.sub(r"//[^\n]*", " ", sans_commentaires)
    sans_commentaires = re.sub(r"\{/\*.*?\*/\}", " ", sans_commentaires, flags=re.DOTALL)
    return len(re.findall(rf"<{balise}[\s>]", sans_commentaires))


def test_un_seul_repere_main_dans_la_mise_en_page() -> None:
    """Section J — un seul `<main>` par écran, garanti par construction.

    Le repère est déclaré une fois dans ``app/layout.tsx`` et enveloppe
    ``children`` : chaque écran en hérite exactement un. La règle qui tient
    ce contrat est donc simple — personne d'autre n'en déclare. Deux ``<main>``
    rendus, et un lecteur d'écran annonce deux contenus principaux : c'est la
    règle axe « landmark-one-main ».
    """
    if not APP.is_dir():
        pytest.skip("frontend/app absent du dépôt")
    layout = APP / "layout.tsx"
    if not layout.exists():
        pytest.skip("app/layout.tsx absent du dépôt")

    assert _balises_reelles(layout.read_text(encoding="utf-8"), "main") == 1, (
        "app/layout.tsx doit déclarer exactement un <main> : zéro laisse les "
        "écrans sans repère de contenu, deux les rendent ambigus."
    )

    autres: dict[str, int] = {}
    for chemin in sorted(APP.rglob("*.tsx")):
        if "node_modules" in chemin.parts or chemin == layout:
            continue
        nombre = _balises_reelles(chemin.read_text(encoding="utf-8"), "main")
        if nombre:
            autres[str(chemin.relative_to(RACINE))] = nombre
    # Un <main> dans un composant n'est pas un défaut en soi — mais il l'est
    # dès qu'il est rendu à l'intérieur de celui du layout. C'est une
    # duplication de repère, et elle est signalée plutôt que tolérée.
    assert not autres, (
        "un second <main> déclaré hors du layout serait rendu À L'INTÉRIEUR du "
        f"premier : {', '.join(f'{c} ({n})' for c, n in autres.items())}"
    )


def test_le_plan_d_audit_couvre_au_moins_l_inventaire() -> None:
    """Le plan peut auditer plus que l'inventaire (états, rôles) — jamais moins."""
    ecrans = _ecrans_next()
    if not ecrans:
        pytest.skip("frontend/app absent du dépôt")
    texte = _texte_spec()
    if not texte:
        pytest.skip("spec d'accessibilité absent du dépôt")

    listes = re.findall(r"const ECRANS[^=]*=\s*\[(.*?)\]", texte, re.DOTALL)
    assert listes, "la liste ECRANS du plan d'audit est introuvable"
    plats = {litteral for bloc in listes for litteral in re.findall(r'"([^"]+)"', bloc)}
    # Un écran sans paramètre peut être audité hors de la liste plate :
    # « /login » l'est par le test d'état, à juste titre — on ne peut pas s'y
    # rendre avec une session ouverte. Ce qu'on exige, c'est que l'écran soit
    # atteint QUELQUE PART (un `goto`), pas qu'il figure dans cette liste.
    atteints = set(re.findall(r"goto\(\s*[`\"']([^`\"'$]+)", texte)) | plats

    attendus = {c for c, p in ecrans.items() if not p}
    # Les écrans à paramètre sont portés par le test de détail, pas par la
    # liste plate ; on ne les exige donc pas ici.
    absents = sorted(attendus - atteints)
    assert not absents, f"écran(s) jamais atteints par le plan d'audit : {', '.join(absents)}"
