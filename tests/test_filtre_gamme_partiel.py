"""Le filtre ``?gamme=…`` doit retrouver une gamme sans la recopier en entier.

Constat mesuré sur le corpus réel (2026-11-10) : sept fiches portant la même
gamme donnaient sept valeurs DISTINCTES, parce que la colonne reçoit le texte
libre du client — « enrouleur Kelt 9 mètres », « enrouleur First 27 »… Le
filtre étant une égalité stricte, il n'en retrouvait qu'une, et la facette
affichait une entrée par fiche.

Le texte libre reste libre : ce n'est pas à ce filtre de décider ce qu'est une
gamme (c'est un arbitrage métier, rendu le 2026-11-09). Mais un utilisateur qui
tape un MOT de la gamme doit retrouver ses fiches — sinon le filtre ne sert à
rien. C'est l'objet de ces épreuves.

Elles n'imposent pas de normaliser la gamme : elles imposent que le filtre
tolère une valeur partielle, ce qui est une exigence d'usage, pas de classement.
"""

from __future__ import annotations

from typing import Any

import pytest

from seamtech_search.recherche import rechercher_fiches

pytestmark = pytest.mark.postgres

# Une gamme réelle de l'atelier : deux mots, le second étant le classement.
GAMME_COMPLETE = "Medium Régate"


def _semer(base: dict[str, Any], code: str, gamme: str, statut: str = "valide") -> None:
    index = base["index"]
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO fiche (code, titre, gamme, statut) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (code) DO UPDATE SET gamme = EXCLUDED.gamme, statut = EXCLUDED.statut",
                (code, "Voile de test " + code, gamme, statut),
            )
            curseur.execute("SELECT id_fiche FROM fiche WHERE code = %s", (code,))
            curseur.execute("SELECT rafraichir_texte_recherche_fiche(id_fiche) FROM fiche WHERE code = %s", (code,))
        connexion.commit()


def _codes(base: dict[str, Any], **filtres) -> set[str]:
    reponse = rechercher_fiches(base["index"], requete="", filtres=filtres, limit=100)
    return {r["code"] for r in reponse["resultats"]}


@pytest.fixture()
def base_gamme(base_recherche: dict[str, Any]) -> dict[str, Any]:
    """Deux fiches de gamme voisines, pour que « partiel » ait un sens."""
    _semer(base_recherche, "GM-COMPLETE", GAMME_COMPLETE)
    _semer(base_recherche, "GM-AUTRE", "Medium Croisière")
    return base_recherche


def test_un_mot_de_la_gamme_retrouve_la_fiche(base_gamme: dict[str, Any]) -> None:
    """Taper « Medium » doit retrouver les deux fiches Medium.

    C'est le défaut constaté : l'égalité stricte rendait le filtre inutilisable
    dès que la gamme porte plus d'un mot.
    """
    assert _codes(base_gamme, gamme="Medium") == {"GM-COMPLETE", "GM-AUTRE"}


def test_le_second_mot_retrouve_aussi(base_gamme: dict[str, Any]) -> None:
    """Taper le classement (« Régate ») doit retrouver la fiche correspondante."""
    assert "GM-COMPLETE" in _codes(base_gamme, gamme="Régate")


def test_la_valeur_complete_fonctionne_toujours(base_gamme: dict[str, Any]) -> None:
    """Contre-épreuve : la valeur exacte reste exacte, elle ne devient pas floue."""
    assert _codes(base_gamme, gamme=GAMME_COMPLETE) == {"GM-COMPLETE"}


def test_un_mot_absent_ne_rend_rien(base_gamme: dict[str, Any]) -> None:
    """Tolérant ne veut pas dire inventif : un mot absent ne rend rien."""
    assert _codes(base_gamme, gamme="Triradiale-inexistante") == set()
