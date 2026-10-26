"""Lot 6 — cohérence UI globale : navigation, liens croisés, matrice des rôles.

Le plan directeur ferme la boucle produit : chaque écran métier doit être
atteignable et relié aux autres sans impasse, la navigation ne doit jamais
promettre un écran que le rôle ne peut pas ouvrir, et aucun lien ne doit
pointer vers une route inexistante. Ces gardes sont des contrôles
**statiques déterministes** sur les sources du frontend (ils tournent dans
la suite standard, sans navigateur) ; le parcours réel compté en clics vit
dans `frontend/e2e/lot6-parcours-3clics.spec.ts` (recette navigateur live).

Contrats figés :
* chaque `href` de la navigation correspond à une page Next.js existante ;
* la visibilité des liens épouse la matrice des rôles du Lot 1 :
  Validation/Extractions réservés à l'approbateur, Gabarits/Comptes à
  l'admin (qui ne décide jamais), dépôt interdit en lecture, écrans voiles
  (Comparaison/Confiance/Demandes) réservés aux comptes nominatifs ;
* les écrans voiles se relient entre eux et vers la fiche métier :
  détail d'extraction → « Confiance… » (pendant du « Comparer… » du Lot 4b),
  sources de population et en-têtes de comparaison → `/fiches/{code}`.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
NAV = RACINE / "frontend" / "components" / "nav.tsx"
EXTRACTIONS_APP = RACINE / "frontend" / "components" / "extractions-app.tsx"
CONFIANCE_APP = RACINE / "frontend" / "components" / "confiance-app.tsx"
COMPARAISON_APP = RACINE / "frontend" / "components" / "comparaison-app.tsx"


def _lire(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8")


def _routes_pages() -> set[str]:
    """Routes publiques déduites de l'arborescence `frontend/app/**/page.tsx`."""
    routes: set[str] = set()
    for page in (RACINE / "frontend" / "app").rglob("page.tsx"):
        relatif = page.parent.relative_to(RACINE / "frontend" / "app")
        segments = [s for s in relatif.parts if not s.startswith("(")]
        # Un segment dynamique ([code]) couvre la route paramétrée.
        routes.add("/" + "/".join(segments) if segments else "/")
    return routes


def _liens_nav() -> list[str]:
    return re.findall(r'\{ href: "([^"]+)",', _lire(NAV)) or re.findall(
        r'href: "(/[a-z-]+)"', _lire(NAV)
    )


def test_chaque_lien_de_navigation_pointe_vers_une_page_existante() -> None:
    routes = _routes_pages()
    liens = _liens_nav()
    assert liens, "aucun lien lu dans nav.tsx — le format a changé, relire la garde"
    for lien in liens:
        assert lien in routes, f"lien de navigation orphelin : {lien} (routes : {sorted(routes)})"


def test_matrice_visibilite_navigation_par_role() -> None:
    source = _lire(NAV)
    attendues = {
        # Dépôt interdit au rôle lecture (Lot 1).
        '"/nouveau"': "peutDeposer(role)",
        # Décider/corriger : approbateur uniquement, jamais l'admin.
        '"/validation"': "nominatif && estApprobateur(role)",
        '"/extractions"': "nominatif && estApprobateur(role)",
        # Écrans voiles et demandes : identité nominative exigée.
        '"/comparaison"': "nominatif",
        '"/confiance"': "nominatif",
        '"/approvals"': "nominatif",
        # Administration technique.
        '"/gabarits"': "estAdmin(role)",
        '"/comptes"': "estAdmin(role)",
    }
    for href, garde in attendues.items():
        motif = re.compile(
            r"href: " + re.escape(href) + r".{0,220}?visible: ([^,}]+)", re.DOTALL
        )
        correspondance = motif.search(source)
        assert correspondance, f"lien {href} introuvable dans nav.tsx"
        assert correspondance.group(1).strip() == garde, (
            f"{href} : visibilité « {correspondance.group(1).strip()} », "
            f"matrice figée « {garde} »"
        )


def test_detail_extraction_relies_comparaison_et_confiance() -> None:
    source = _lire(EXTRACTIONS_APP)
    assert 'href={`/comparaison?a=${extraction.id_extraction}`}' in source
    assert 'data-testid="lien-comparer"' in source
    # Pendant Lot 6 : la confiance de réutilisation depuis le même détail.
    assert 'href={`/confiance?id=${extraction.id_extraction}`}' in source
    assert 'data-testid="lien-confiance"' in source


def test_confiance_relie_les_fiches_sources_vers_la_fiche_metier() -> None:
    source = _lire(CONFIANCE_APP)
    # Le lien n'existe que si une fiche métier est réellement rattachée
    # (code_fiche_metier résolu côté backend) : jamais de lien mort.
    assert "code_fiche_metier" in source
    assert "/fiches/${encodeURIComponent(s.code_fiche_metier)}" in source


def test_comparaison_relie_les_deux_voiles_vers_leurs_fiches() -> None:
    source = _lire(COMPARAISON_APP)
    assert "code_fiche_metier" in source, (
        "les en-têtes A/B de la comparaison doivent ouvrir la fiche métier "
        "quand elle existe (code_fiche_metier résolu côté backend)"
    )
    assert "/fiches/${encodeURIComponent(" in source
