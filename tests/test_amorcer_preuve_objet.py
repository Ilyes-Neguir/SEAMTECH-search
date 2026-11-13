"""L'amorçage doit échouer POUR UNE RAISON NOMMÉE, et ne rien inventer.

Pourquoi tester un script qu'on n'exécute pas ici : Docker n'existe sur aucune
machine de développement, et les journaux CI de ce dépôt ne sont pas lisibles
hors de GitHub. Sans ces tests, l'amorçage serait une boîte noire au pire
endroit — c'est lui qui décide si la preuve porte sur quelque chose.

Chaque test fait donc échouer UNE étape et vérifie que le verdict devient
faux en la NOMMANT.
"""
from __future__ import annotations

from typing import Any

from scripts.amorcer_preuve_objet import Amorcage

BASE = "postgresql://seamtech:secret@postgres:5432/seamtech_search"
COMPTE = "approbateur-preuve"
MOT_DE_PASSE = "motdepasse-preuve-ci"


class Monde:
    """Un faux déploiement : chaque commande reçoit une réponse scriptée."""

    def __init__(self) -> None:
        self.appels: list[list[str]] = []
        self.init_code = 0
        self.depot_code = 0
        self.promotion_code = 0
        self.compte_code = 0
        self.code_fiche = "ST-101"
        self.nb_objets = "1"
        self.nb_pieces = "2"

    def executeur(self, commande: Any) -> tuple[int, str, str]:
        self.appels.append(list(commande))
        jointe = " ".join(commande)
        if "fiches.cli" in jointe and "init" in jointe:
            return self.init_code, "gabarits enregistrés", ""
        if "fiches.cli" in jointe and "deposer" in jointe:
            return self.depot_code, "lot terminé", ""
        if "fiches.cli" in jointe and "promouvoir" in jointe:
            return self.promotion_code, "promotion : 1/1", ""
        if "SELECT code FROM fiche" in jointe:
            return 0, self.code_fiche + "\n", ""
        if "count(*) FROM documents" in jointe:
            return 0, self.nb_objets + "\n", ""
        if "count(*) FROM fiche_piece_jointe" in jointe:
            return 0, self.nb_pieces + "\n", ""
        # Le compte nominatif : « python -c », et NON le « -c » de psql.
        if "python" in commande and "-c" in commande and "fiches.cli" not in jointe:
            return self.compte_code, "compte nominatif prêt", ""
        return 0, "", ""

    def amorcer(self, **kwargs: Any) -> Amorcage:
        params: dict[str, Any] = dict(
            executeur=self.executeur, base_source=BASE, compte=COMPTE, mot_de_passe=MOT_DE_PASSE,
        )
        params.update(kwargs)
        return Amorcage(**params)


def _monde_sain() -> Monde:
    return Monde()


# --- le chemin heureux ------------------------------------------------------


def test_le_decor_est_prete_et_rend_le_code_de_fiche() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    assert rapport["code_fiche"] == "ST-101"
    assert [e["etape"] for e in rapport["etapes"]] == [
        "init", "dépôt", "promotion", "compte nominatif", "code de fiche", "clés objet",
    ]
    assert all(e["reussie"] for e in rapport["etapes"])


def test_le_depot_passe_par_les_commandes_du_produit() -> None:
    """Fabriquer les lignes en SQL prouverait un scénario qui n'existe pas."""
    monde = _monde_sain()
    monde.amorcer().courir()

    jointes = [" ".join(a) for a in monde.appels]
    for commande in ("init", "deposer", "promouvoir"):
        assert any("fiches.cli" in c and commande in c for c in jointes), commande
    # Et aucune écriture directe en base hors lecture du résultat.
    ecritures = [c for c in jointes if "psql" in c and ("INSERT" in c or "UPDATE" in c)]
    assert not ecritures, "l'amorçage n'écrit jamais en base lui-même"


def test_le_depot_declare_la_racine_servie() -> None:
    """Hors racine, l'application refuse le dossier — à juste titre."""
    monde = _monde_sain()
    monde.amorcer(racine_servie="/app/autre_racine").courir()

    depot = next(" ".join(a) for a in monde.appels if "deposer" in " ".join(a))
    assert "SEAMTECH_ROOT_PATHS=/app/autre_racine" in depot, depot


# --- chaque étape nomme son échec -------------------------------------------


def test_init_en_echec_est_nomme() -> None:
    monde = _monde_sain()
    monde.init_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "init" in rapport["raison"]


def test_depot_en_echec_rappelle_la_racine_servie() -> None:
    """« Dossier hors des racines » est la cause la plus fréquente : la nommer évite un cycle CI."""
    monde = _monde_sain()
    monde.depot_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "dépôt" in rapport["raison"]
    assert "racine servie" in rapport["raison"]


def test_promotion_en_echec_est_nomme() -> None:
    monde = _monde_sain()
    monde.promotion_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "promotion" in rapport["raison"]


def test_sans_compte_nominatif_l_etape_est_nommee() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer(compte="", mot_de_passe="").courir()

    assert rapport["ok"] is False
    assert "compte nominatif" in rapport["raison"]
    assert "Lot 0b" in rapport["raison"]


def test_aucune_fiche_apres_le_depot() -> None:
    monde = _monde_sain()
    monde.code_fiche = ""
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "code de fiche" in rapport["raison"]
    assert "aucune pièce à perdre" in rapport["raison"]


def test_aucune_cle_objet_apres_la_promotion() -> None:
    """GARDE-FOU : sans clé objet, la preuve de perte porterait sur du vent."""
    monde = _monde_sain()
    monde.nb_objets = "0"
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "clés objet" in rapport["raison"]
    assert "rien à perdre" in rapport["raison"]


def test_sans_base_source_rien_ne_demarre() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer(base_source="").courir()

    assert rapport["ok"] is False
    assert "--base-source" in rapport["raison"]
    assert rapport["etapes"] == []


# --- confidentialité --------------------------------------------------------


def test_le_mot_de_passe_n_apparait_dans_aucune_etape() -> None:
    """Un rapport d'amorçage finit en annotation CI : il ne doit rien divulguer."""
    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    for etape in rapport["etapes"]:
        assert MOT_DE_PASSE not in etape["detail"], etape


def test_le_rapport_est_serialisable() -> None:
    import json

    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert json.loads(json.dumps(rapport)) == rapport
