"""Cohérence visuelle des champs de formulaire — défaut E5 de la recette.

Le commanditaire a signalé un défaut de couleurs sur les listes déroulantes.
La cause est dans le code, et elle est mesurable : les écrans de recherche et
de comparaison peignaient leurs champs en ``bg-background`` — un fond opaque —
alors que tous les autres écrans (comptes, demandes, archive, correction)
utilisent ``bg-input`` — un fond translucide, assorti de ``text-foreground``.

Dans le thème clair, ``--background`` vaut ``#f6f1e6`` et ``--input``
``rgba(21, 39, 48, 0.22)`` ; dans le thème sombre ``#08111a`` contre
``rgba(237, 242, 238, 0.18)``. Deux champs voisins, deux couleurs : c'est le
défaut, visible à l'œil et invisible pour tout test qui ne regarde que le
comportement.

Ce module est le test de non-régression : il lit les sources et refuse qu'un
champ de formulaire s'écarte du fond de référence. Un test sur les sources
n'est pas un test d'intégration, mais le défaut qu'il attrape est dans les
sources, et il se joue partout — y compris là où aucun navigateur n'est
disponible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
FRONTEND = RACINE / "frontend"

# Les composants d'interface, et seulement eux : les fichiers e2e et les
# utilitaires ne peignent rien.
CIBLES = [
    FRONTEND / "components",
    FRONTEND / "app",
]

# Un champ de formulaire : <select …> ou <input …>, sur une ou plusieurs
# lignes, jusqu'à la fermeture de la balise ouvrante.
#
# ATTENTION — une regex du type ``<(select|input)\b[^>]*>`` NE MARCHE PAS ici,
# et elle ne marche pas en silence : ``onChange={(e) => changerTri(...)}``
# contient une FLÈCHE, donc un ``>``, donc la balise est tronquée avant
# ``className`` et le champ passe inaperçu. Vérifié : avec cette regex le
# défaut réintroduit à la main laissait le test vert. Le parcours ci-dessous
# suit les chaînes et les accolades, condition sans laquelle ce module
# n'attraperait rien du tout.
_BALISE = re.compile(r"<(select|input)\b")
_CLASSE = re.compile(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', re.DOTALL)


def _sans_commentaires(contenu: str) -> str:
    """Retire ``/* … */`` et ``// …`` — piège n°2 de ce parseur.

    Un commentaire JSX contenant une apostrophe (« n'est PAS ») fait croire au
    parcours qu'une chaîne s'ouvre ; elle ne se ferme alors qu'à l'apostrophe
    suivante du fichier, plusieurs milliers de caractères plus loin, et tout
    ce qui suit passe inaperçu. Constaté : le parcours ne rendait plus qu'UNE
    balise sur sept.
    """
    sans_blocs = re.sub(r"/\*.*?\*/", " ", contenu, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", sans_blocs)


def _balises_ouvrantes(contenu: str) -> list[str]:
    """Le texte de chaque balise ``<select …>`` / ``<input …>``, chaînes comprises.

    On s'arrête au ``>`` de fermeture, c'est-à-dire au premier ``>`` qui n'est
    ni dans une chaîne ni dans une accolade — donc pas dans une flèche
    ``=>`` ni dans une expression JSX.

    L'apostrophe n'est PAS traitée comme délimiteur : dans ce dépôt les
    attributs sont en guillemets doubles, et confondre l'apostrophe d'un
    commentaire avec une chaîne désynchronise tout le parcours.
    """
    texte = _sans_commentaires(contenu)
    longueur = len(texte)
    balises: list[str] = []
    position = 0
    while True:
        trouve = _BALISE.search(texte, position)
        if not trouve:
            return balises
        j = trouve.end()
        accolades = 0
        while j < longueur:
            caractere = texte[j]
            if caractere in '"`':
                j += 1
                while j < longueur and texte[j] != caractere:
                    if texte[j] == "\\":
                        j += 1
                    j += 1
                j += 1
                continue
            if caractere == "{":
                accolades += 1
            elif caractere == "}":
                accolades = max(0, accolades - 1)
            elif caractere == ">" and accolades == 0:
                break
            j += 1
        balises.append(texte[trouve.start() : j])
        position = j + 1


FOND_REFERENCE = "bg-input"
COULEUR_TEXTE_REFERENCE = "text-foreground"

# Une DEUXIÈME génération de champs coexiste dans le dépôt : bordure
# ``border-input``, fond plein, et surtout un anneau de focus
# (``ring-primary focus:ring-2``) que la première génération n'a pas.
#
# Ce n'est pas le même défaut. Les écrans de recherche et de comparaison
# étaient incohérents AVEC LEURS VOISINS : mêmes écrans, mêmes champs, deux
# couleurs — c'était le défaut signalé, il est corrigé. ``import-panel``,
# lui, est cohérent en lui-même et porte une meilleure accessibilité. Le
# fondre de force dans l'ancien style serait un choix de design pris à la
# place du commanditaire ; la divergence est donc DÉCLARÉE ici et remontée
# comme décision à trancher, pas masquée.
#
# Une exception non déclarée est un contournement. Celle-ci est déclarée,
# nommée, et le test la compte : si la liste s'allonge, il faudra s'en
# expliquer.
GENERATION_RECENTE = frozenset({"import-panel.tsx"})


def _fichiers_a_analyser() -> list[Path]:
    if not FRONTEND.exists():
        return []
    fichiers: list[Path] = []
    for cible in CIBLES:
        if cible.is_dir():
            fichiers.extend(sorted(cible.rglob("*.tsx")))
        elif cible.exists():
            fichiers.append(cible)
    return [f for f in fichiers if "node_modules" not in f.parts]


def _champs_incoherents(contenu: str) -> list[tuple[str, str]]:
    """Les champs dont le fond s'écarte de la référence.

    Renvoie ``(balise, classe)`` pour chaque champ fautif. Un champ sans
    ``className`` du tout n'est pas fautif ici : il hérite, et l'attraper est
    le travail de l'audit axe-core, pas de ce test.
    """
    fautifs: list[tuple[str, str]] = []
    for texte in _balises_ouvrantes(contenu):
        trouve = _CLASSE.search(texte)
        if not trouve:
            continue
        classe = trouve.group(1) or trouve.group(2) or ""
        if "bg-background" in classe.split():
            fautifs.append((texte.split("\n")[0][:60], classe))
    return fautifs


def test_les_sources_frontend_sont_presentes() -> None:
    """Une recette qui ne trouve pas les sources ne prouve rien — elle le dit."""
    fichiers = _fichiers_a_analyser()
    if not fichiers:
        pytest.skip("Sources frontend absentes de ce dépôt")
    assert fichiers, "aucun composant .tsx à analyser"


def test_aucun_champ_de_formulaire_ne_se_decore_du_fond_de_page() -> None:
    """Défaut E5 — les dropdowns de la recherche avaient la couleur de la page.

    Le fond d'un champ est ``bg-input`` ; ``bg-background`` est le fond de la
    PAGE. Peindre un champ avec le fond de la page le rend opaque au milieu de
    champs translucides : c'est exactement l'écart de couleur remonté, et il
    touchait les écrans de recherche et de comparaison.
    """
    fichiers = _fichiers_a_analyser()
    if not fichiers:
        pytest.skip("Sources frontend absentes de ce dépôt")

    fautifs: dict[str, list[tuple[str, str]]] = {}
    for chemin in fichiers:
        if chemin.name in GENERATION_RECENTE:
            continue
        contenu = chemin.read_text(encoding="utf-8")
        ecarts = _champs_incoherents(contenu)
        if ecarts:
            fautifs[str(chemin.relative_to(RACINE))] = ecarts

    assert not fautifs, (
        "champ(s) peints avec le fond de page au lieu du fond de champ "
        f"({FOND_REFERENCE}). Un champ opaque parmi des champs translucides "
        "est l'écart de couleur signalé : "
        + "; ".join(f"{f} → {len(e)} champ(s), ex. {e[0][0]!r} classe {e[0][1]!r}" for f, e in fautifs.items())
    )


def test_l_exception_de_generation_est_exacte_et_non_bidonnee() -> None:
    """Une exception déclarée se vérifie, sinon elle devient un permis.

    Deux sens : les fichiers déclarés doivent VRAIMENT porter l'autre style
    (une exception de complaisance masquerait un défaut réel), et aucun autre
    fichier ne doit le porter sans être déclaré (sinon l'exception s'étend en
    silence et le contrôle ci-dessus ne veut plus rien dire).
    """
    fichiers = _fichiers_a_analyser()
    if not fichiers:
        pytest.skip("Sources frontend absentes de ce dépôt")

    porteurs: set[str] = set()
    for chemin in fichiers:
        contenu = chemin.read_text(encoding="utf-8")
        for texte in _balises_ouvrantes(contenu):
            trouve = _CLASSE.search(texte)
            if not trouve:
                continue
            classe = (trouve.group(1) or trouve.group(2) or "").split()
            if "border-input" in classe or "focus:ring-2" in classe:
                porteurs.add(chemin.name)
                break

    assert porteurs == set(GENERATION_RECENTE), (
        "les fichiers porteurs de la seconde génération de champs ont changé. "
        f"Déclarés : {sorted(GENERATION_RECENTE)} — observés : {sorted(porteurs)}. "
        "Cette liste est une exception, elle se met à jour en connaissance de "
        "cause, jamais pour faire passer un test."
    )


def test_les_champs_de_recherche_portent_la_couleur_de_texte_de_reference() -> None:
    """Le défaut était double : fond opaque ET couleur de texte implicite.

    Corriger le fond en laissant le texte hériter aurait laissé une partie du
    défaut en place sur les thèmes où l'héritage ne tombe pas sur
    ``--foreground``. Les écrans corrigés portent donc les deux.
    """
    fichiers = _fichiers_a_analyser()
    if not fichiers:
        pytest.skip("Sources frontend absentes de ce dépôt")

    for nom in ("recherche-fiches-app.tsx", "comparaison-groupe.tsx"):
        chemin = FRONTEND / "components" / nom
        if not chemin.exists():
            pytest.skip(f"{nom} absent du dépôt")
        contenu = chemin.read_text(encoding="utf-8")
        for texte in _balises_ouvrantes(contenu):
            trouve = _CLASSE.search(texte)
            if not trouve:
                continue
            classe = trouve.group(1) or trouve.group(2) or ""
            classes = classe.split()
            if FOND_REFERENCE not in classes:
                continue
            assert COULEUR_TEXTE_REFERENCE in classes, (
                f"{nom} : un champ en {FOND_REFERENCE} sans "
                f"{COULEUR_TEXTE_REFERENCE} — la couleur du texte reste "
                f"implicite. Classe : {classe!r}"
            )
