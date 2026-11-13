"""La preuve « objet perdu » doit échouer POUR UNE RAISON NOMMÉE, jamais en silence.

Pourquoi tester un script qu'on n'exécute pas ici : l'orchestration Docker
n'existe sur aucune machine de développement. Sans ces tests, rien ne
distinguerait une preuve qui fonctionne d'une preuve qui rend « PROUVÉ » parce
qu'elle n'a rien vérifié — le pire défaut possible pour un instrument de
preuve.

Chaque test fait donc échouer UNE étape précise et vérifie que le verdict
devient « NON PROUVÉ » en la NOMMANT. Un script de preuve qui passerait au
vert sur un monde cassé serait une fausse assurance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.preuve_objet_perdu import PreuveObjetPerdu

OCTETS = b"contenu-du-document-de-l-atelier"
SHA = hashlib.sha256(OCTETS).hexdigest()

FICHE = "ST-101"
CLE = "pieces/ST-101/plan.pdf"
JETON = "jeton-de-test"


class Monde:
    """Un faux déploiement : chaque commande reçoit une réponse scriptée."""

    def __init__(self, *, services=("web", "minio", "postgres")) -> None:
        self.services = list(services)
        self.appels: list[list[str]] = []
        self.reponses: dict[str, tuple[int, str, str]] = {}
        self.objet_present = True

    # -- scriptage ---------------------------------------------------------

    def quand(self, fragment: str, code: int = 0, sortie: str = "", erreur: str = "") -> "Monde":
        self.reponses[fragment] = (code, sortie, erreur)
        return self

    def _repondre(self, commande: list[str]) -> tuple[int, str, str]:
        self.appels.append(commande)
        jointe = " ".join(commande)
        # Le faux stockage obéit aux commandes qu'il reçoit : sans cela, la
        # simulation resservirait l'objet après « mc rm » et la preuve
        # croirait à un cache — l'inverse de ce qu'on veut éprouver.
        if "mc" in jointe and "rm" in jointe and CLE in jointe:
            self.objet_present = False
        if "mc" in jointe and "cp" in jointe and CLE in jointe:
            self.objet_present = True
        if "ps --status running --services" in jointe:
            return 0, "\n".join(self.services), ""
        if "curl" in jointe and jointe.endswith("/pieces"):
            # Listing : l'API n'expose jamais object_key, seulement un id.
            return 0, json.dumps({"pieces": [{"id": 42, "kind": "pdf"}]}), ""
        if "curl" in jointe and "/telecharger" in jointe:
            # L'application sert la pièce seulement si l'objet existe.
            if not self.objet_present:
                return 0, "404", ""
            # La cible est l'argument qui suit « -o », pas un chemin commençant
            # par « /tmp/ » : sous Windows, tempfile crée le fichier ailleurs,
            # et un préfixe écrit en dur rendait le fichier vide — donc
            # l'empreinte du vide, et un échec incompréhensible. Mesuré au run
            # 43455974024 : « attendu e3b0c442… » est le SHA-256 de RIEN.
            cible: str | None = None
            for indice, arg in enumerate(commande):
                if arg == "-o" and indice + 1 < len(commande):
                    cible = commande[indice + 1]
            if cible:
                Path(cible).write_bytes(OCTETS)
            return 0, "200", ""
        for fragment, reponse in self.reponses.items():
            if fragment in jointe:
                return reponse
        return 0, "", ""

    def executeur(self, commande: list[str]) -> tuple[int, str, str]:
        return self._repondre(commande)


def _preuve(monde: Monde, **kwargs: Any) -> PreuveObjetPerdu:
    params: dict[str, Any] = dict(
        executeur=monde.executeur,
        fiche=FICHE,
        cle_objet=CLE,
        jeton=JETON,
        base_cible="postgresql://seamtech@postgres:5432/scratch",
        base_api="http://127.0.0.1:8000",
        docker_disponible=True,
    )
    params.update(kwargs)
    return PreuveObjetPerdu(**params)


def _monde_sain() -> Monde:
    """Un déploiement où tout se passe bien — la preuve doit aboutir."""
    return (
        Monde()
        .quand("mc rm", 0)
        .quand("sauvegarder", 0, "ok")
        .quand("verifier", 0, json.dumps({"nb_documents_verifies": 3, "nb_documents_alteres": 0,
                                         "nb_documents_absents": 0}))
        .quand("restaurer", 0, "ok")
        .quand("find", 0, "/app/data/restauration-preuve-objet/plan.pdf\n")
        .quand("sha256sum", 0, f"{SHA}  /app/data/restauration-preuve-objet/plan.pdf\n")
        .quand("mc cp", 0)
    )


# --- le chemin heureux ------------------------------------------------------


def test_la_chaine_complete_est_prouvee() -> None:
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    noms = [e["etape"] for e in rapport["etapes"]]
    # Les huit jalons, dans l'ordre : servi, supprimé, disparu, sauvegardé,
    # vérifié, restauré, réinjecté, resservi.
    assert noms == [
        "services",
        "objet servi avant",
        "objet supprimé",
        "objet disparu",
        "sauvegarde complète",
        "bundle vérifié",
        "restauration",
        "octets restaurés",
        "réinjection",
        "objet resservi",
    ]
    assert all(e["reussie"] for e in rapport["etapes"])


def test_la_preuve_supprime_vraiment_l_objet() -> None:
    """Sans suppression, « resservir » ne prouverait rien."""
    monde = _monde_sain()
    preuve = _preuve(monde)
    rapport = preuve.courir()

    assert rapport["verdict"] == "PROUVÉ"
    rm = [c for c in monde.appels if "mc" in c and "rm" in c]
    assert rm, "la preuve doit réellement supprimer l'objet du stockage"


# --- les prérequis ----------------------------------------------------------


@pytest.mark.parametrize(
    ("manque", "attendu"),
    [
        ({"fiche": ""}, "--fiche"),
        ({"cle_objet": ""}, "--cle-objet"),
        ({"jeton": ""}, "--jeton"),
        ({"base_cible": ""}, "--base-cible"),
        ({"base_api": ""}, "--base-api"),
    ],
)
def test_un_prerequis_manquant_est_nomme(manque: dict[str, Any], attendu: str) -> None:
    monde = _monde_sain()
    rapport = _preuve(monde, **manque).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert any(attendu in m for m in rapport["manques"]), rapport["manques"]


def test_les_cinq_prerequis_sont_tous_nommes() -> None:
    """Un échec obscur vaudrait un mensonge : la liste doit être complète."""
    monde = _monde_sain()
    rapport = _preuve(
        monde, fiche="", cle_objet="", jeton="", base_cible="", base_api=""
    ).courir()

    assert len(rapport["manques"]) == 5
    assert rapport["etapes"] == [], "sans prérequis, aucune étape ne doit être lancée"


# --- chaque échec est nommé -------------------------------------------------


def test_services_absents() -> None:
    monde = Monde(services=["postgres"])
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "web" in rapport["raison"]


def test_piece_non_servie_avant_la_preuve() -> None:
    """Si la pièce n'est pas servie au départ, la preuve n'aurait rien prouvé."""
    monde = _monde_sain()
    monde.objet_present = False
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet servi avant" in rapport["raison"]


def test_piece_toujours_servie_apres_suppression() -> None:
    """Un cache qui resservirait l'objet rendrait la preuve fausse."""
    monde = _monde_sain()

    def executeur(commande: list[str]) -> tuple[int, str, str]:
        if "mc" in commande and "rm" in commande:
            return 0, "", ""  # suppression « réussie » mais sans effet
        return monde.executeur(commande)

    rapport = PreuveObjetPerdu(
        executeur=executeur, fiche=FICHE, cle_objet=CLE, jeton=JETON,
        base_cible="postgresql://scratch", base_api="http://127.0.0.1:8000",
        docker_disponible=True,
    ).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet disparu" in rapport["raison"]
    assert "cache" in rapport["raison"]


def test_sauvegarde_en_echec() -> None:
    monde = _monde_sain().quand("sauvegarder", 1, "", "pg_dump: échec")
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "sauvegarde complète" in rapport["raison"]
    assert "pg_dump" in rapport["raison"]


def test_bundle_sans_document() -> None:
    """Zéro document vérifié n'est pas une preuve : c'est une preuve vide."""
    monde = _monde_sain().quand(
        "verifier", 0, json.dumps({"nb_documents_verifies": 0, "nb_documents_alteres": 0,
                                   "nb_documents_absents": 0})
    )
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "bundle vérifié" in rapport["raison"]
    assert "vide" in rapport["raison"]


def test_octets_restaures_differents() -> None:
    """Le cœur du sujet : des octets différents, ce n'est pas une restauration."""
    faux = hashlib.sha256(b"autres-octets").hexdigest()
    monde = _monde_sain().quand("sha256sum", 0, f"{faux}  /app/data/plan.pdf\n")
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "octets restaurés" in rapport["raison"]
    assert SHA[:16] in rapport["raison"] and faux[:16] in rapport["raison"]


def test_objet_non_resservi_apres_reinjection() -> None:
    """La réinjection réussit, mais l'objet reste absent : échec à nommer."""
    monde = _monde_sain()

    def executeur(commande: list[str]) -> tuple[int, str, str]:
        jointe = " ".join(commande)
        reinjecte = any("mc" in " ".join(a) and "cp" in " ".join(a) for a in monde.appels)
        if "curl" in jointe and "/telecharger" in jointe and reinjecte:
            return 0, "404", ""
        return monde.executeur(commande)

    rapport = PreuveObjetPerdu(
        executeur=executeur, fiche=FICHE, cle_objet=CLE, jeton=JETON,
        base_cible="postgresql://scratch", base_api="http://127.0.0.1:8000",
        docker_disponible=True,
    ).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet resservi" in rapport["raison"]


# --- le contrat de sortie ---------------------------------------------------


def test_le_rapport_est_serialisable() -> None:
    """Le rapport part dans un journal : il doit être transformable en JSON."""
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert json.loads(json.dumps(rapport, ensure_ascii=False))["verdict"] == "PROUVÉ"


def test_les_etapes_portent_un_detail() -> None:
    """Une étape sans détail ne se répare pas : on sait quoi, pas pourquoi."""
    monde = _monde_sain().quand("mc rm", 1, "", "permission refusée")
    rapport = _preuve(monde).courir()

    echec = [e for e in rapport["etapes"] if not e["reussie"]]
    assert echec and echec[0]["detail"].strip(), echec
