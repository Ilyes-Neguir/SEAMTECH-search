"""Matrice systématique routes × rôles nominatifs (PostgreSQL réel).

Chaque route/méthode enregistrée est appelée avec six identités — jeton de
service seul, compte de secours, et un compte nominatif par rôle canonique
(admin, lecture, utilisateur, approbateur) muni d'une vraie session. Les
corps sont les plus petits corps valides dérivés du schéma OpenAPI, sur des
identifiants inexistants : on observe LA PORTE (403/401 ou passage), jamais
un effet métier.

Invariants figés (contrat du Lot 1, vérifiés sur TOUTES les routes) :

* aucun 500, quelle que soit l'identité (503 toléré : dépendance absente) ;
* ``lecture`` n'obtient un 2xx hors GET que sur les lectures déguisées en
  POST explicitement listées (assistant, doublons en lecture/dry-run) ;
* ``admin`` nominatif est refusé (403) sur les décisions métier réservées à
  l'approbateur : il administre, il ne décide jamais.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comptes.comptes import IDENTIFIANT_SECOURS, creer_utilisateur
from seamtech_search.config import AppConfig

JETON_SERVICE = "jeton-service-matrice-roles"

# POST qui sont des lectures ou de l'état PERSONNEL (aucune écriture
# métier) : 2xx admis en lecture.
_LECTURES_POST_AUTORISEES = {
    ("POST", "/assistant"),  # question/réponse documentaire, lecture seule
    ("POST", "/fiches/doublons/scan"),  # dry-run par défaut ; appliquer=true exige l'approbateur
    ("POST", "/validation/doublons"),  # résolution de liens pour une liste de codes
    ("POST", "/notifications/tout-lire"),  # sa propre boîte : état personnel, comme la déconnexion
}

# Décisions métier : l'admin nominatif doit être refusé, seul l'approbateur passe.
_DECISIONS_APPROBATEUR = {
    ("POST", "/fiches/{code}/corriger"),
    ("POST", "/fiches/{code}/valider"),
    ("POST", "/fiches/{code}/rejeter"),
    ("POST", "/fiches/{code}/rouvrir"),
    ("POST", "/fiches/{code}/reconcilier-corrections"),
    ("POST", "/validation/lot"),
    ("PATCH", "/imports/{import_id}"),
    ("GET", "/pieces/{piece_id}/telecharger"),
}

# Ces routes agissent sur les sessions de la sonde elle-même : exclues de
# l'énumération (couvertes par les tests de comptes/sessions dédiés).
_EXCLUSIONS = {
    ("POST", "/auth/connexion"),
    ("POST", "/auth/deconnexion"),
    ("POST", "/auth/deconnexion-tout"),
}


def _minimal(schema: Any, defs: dict[str, Any], profondeur: int = 0) -> Any:  # noqa: ANN401
    """Plus petite valeur conforme à un fragment de schéma OpenAPI."""
    if not isinstance(schema, dict) or profondeur > 4:
        return "x"
    if "$ref" in schema:
        return _minimal(defs.get(schema["$ref"].split("/")[-1], {}), defs, profondeur + 1)
    for cle in ("allOf", "anyOf", "oneOf"):
        if schema.get(cle):
            return _minimal(schema[cle][0], defs, profondeur + 1)
    type_ = schema.get("type")
    if type_ == "object" or "properties" in schema:
        proprietes = schema.get("properties", {})
        return {
            nom: _minimal(proprietes.get(nom, {}), defs, profondeur + 1)
            for nom in schema.get("required", [])
        }
    if type_ == "array":
        return [_minimal(schema.get("items", {}), defs, profondeur + 1)]
    if type_ == "integer":
        return 999999
    if type_ == "number":
        return 1.5
    if type_ == "boolean":
        return True
    if "enum" in schema:
        return schema["enum"][0]
    return "INCONNU"


def _est_champ_fichier(schema: Any, defs: dict[str, Any], profondeur: int = 0) -> bool:  # noqa: ANN401
    if not isinstance(schema, dict) or profondeur > 4:
        return False
    if schema.get("format") == "binary":
        return True
    if "$ref" in schema:
        return _est_champ_fichier(defs.get(schema["$ref"].split("/")[-1], {}), defs, profondeur + 1)
    if schema.get("type") == "array":
        return _est_champ_fichier(schema.get("items", {}), defs, profondeur + 1)
    return any(
        _est_champ_fichier(schema.get(cle, [{}])[0], defs, profondeur + 1)
        for cle in ("allOf", "anyOf", "oneOf")
        if schema.get(cle)
    )


def _chemin_concret(chemin: str) -> str:
    def remplacer(match: re.Match[str]) -> str:
        nom = match.group(1).split(":", 1)[0]
        if "jeton" in nom:
            return "A" * 32
        if "id" in nom:
            return "999999"
        if nom == "artifact":
            return "report_pdf"
        return "INCONNU"

    return re.sub(r"\{([^}]+)\}", remplacer, chemin)


@pytest.fixture()
def monde_matrice(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]
    identites: dict[str, dict[str, str]] = {"service": {"X-SEAMTECH-TOKEN": JETON_SERVICE}}
    identites["secours"] = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": IDENTIFIANT_SECOURS,
        "X-SEAMTECH-ROLE": "admin",
    }
    mots_de_passe: dict[str, str] = {}
    for role in ("admin", "lecture", "utilisateur", "approbateur"):
        mot_de_passe = f"MotDePasse-matrice-{role}-2026"
        creer_utilisateur(
            index,
            f"matrice-{role}",
            f"Matrice {role}",
            mot_de_passe,
            role=role,
            doit_changer_mot_de_passe=False,
        )
        mots_de_passe[role] = mot_de_passe

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused-matrice.db",
        database_url=base_recherche["url"],
        auth_token=JETON_SERVICE,
        storage_backend="local",
        web_worker_enabled=False,
        redis_url=os.environ.get("SEAMTECH_TEST_REDIS_URL", ""),
        rate_limit_per_minute=100_000,  # la matrice émet ~650 requêtes
    )
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)
    # Connexion HTTP réelle : c'est elle qui inscrit la session au registre
    # Redis quand il est configuré, exactement comme en production.
    for role, mot_de_passe in mots_de_passe.items():
        reponse = client.post(
            "/auth/connexion",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
            json={"identifiant": f"matrice-{role}", "mot_de_passe": mot_de_passe},
        )
        assert reponse.status_code == 200, (role, reponse.text)
        session = reponse.json()
        identites[role] = {
            "X-SEAMTECH-TOKEN": JETON_SERVICE,
            "X-SEAMTECH-UTILISATEUR": f"matrice-{role}",
            "X-SEAMTECH-ROLE": role,
            "X-SEAMTECH-SESSION": str(session["id_session"]),
            "X-SEAMTECH-SESSION-JETON": str(session["jeton_session"]),
        }
    return {"app": app, "client": client, "identites": identites}


@pytest.mark.postgres
def test_matrice_routes_par_role(monde_matrice: dict[str, Any]) -> None:
    app = monde_matrice["app"]
    client: TestClient = monde_matrice["client"]
    identites: dict[str, dict[str, str]] = monde_matrice["identites"]
    spec = app.openapi()
    defs = spec.get("components", {}).get("schemas", {})

    anomalies: list[str] = []
    routes_vues = 0
    for route in app.routes:
        for methode in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            if (methode, route.path) in _EXCLUSIONS:
                continue
            routes_vues += 1
            operation = spec.get("paths", {}).get(route.path, {}).get(methode.lower(), {})
            parametres: dict[str, Any] = {}
            for parametre in operation.get("parameters", []):
                if parametre.get("in") == "query" and parametre.get("required"):
                    valeur = _minimal(parametre.get("schema", {}), defs)
                    parametres[parametre["name"]] = valeur if not isinstance(valeur, (dict, list)) else "INCONNU"
            kwargs: dict[str, Any] = {"params": parametres}
            contenus = operation.get("requestBody", {}).get("content", {})
            if "application/json" in contenus:
                kwargs["json"] = _minimal(contenus["application/json"].get("schema", {}), defs)
            elif contenus:
                cle = next(iter(contenus))
                schema_corps = contenus[cle].get("schema", {})
                donnees = _minimal(schema_corps, defs)
                if isinstance(donnees, dict):
                    proprietes = schema_corps.get("properties", {})
                    if "$ref" in schema_corps:
                        proprietes = defs.get(schema_corps["$ref"].split("/")[-1], {}).get("properties", {})
                    kwargs["data"] = {
                        nom: (json.dumps(valeur) if isinstance(valeur, (dict, list)) else str(valeur))
                        for nom, valeur in donnees.items()
                        if not _est_champ_fichier(proprietes.get(nom, {}), defs)
                    }
                    kwargs["files"] = {
                        nom: ("sonde.pdf", b"%PDF-1.4 sonde matrice", "application/pdf")
                        for nom in ("fichier", "fichiers", "files")
                    }

            statuts: dict[str, int] = {}
            for nom_identite, entetes in identites.items():
                reponse = client.request(methode, _chemin_concret(route.path), headers=entetes, **kwargs)
                statuts[nom_identite] = reponse.status_code

            for nom_identite, statut in statuts.items():
                if statut == 500:
                    anomalies.append(f"500 pour {nom_identite} sur {methode} {route.path}")
            statut_lecture = statuts["lecture"]
            if (
                methode != "GET"
                and 200 <= statut_lecture < 300
                and (methode, route.path) not in _LECTURES_POST_AUTORISEES
            ):
                anomalies.append(f"lecture obtient {statut_lecture} sur {methode} {route.path}")
            if (methode, route.path) in _DECISIONS_APPROBATEUR and statuts["admin"] != 403:
                anomalies.append(
                    f"admin nominatif non refusé ({statuts['admin']}) sur décision {methode} {route.path}"
                )

    assert routes_vues >= 100, f"inventaire anormalement petit : {routes_vues}"
    assert not anomalies, "\n".join(anomalies)


@pytest.mark.postgres
def test_annuler_un_lot_inconnu_repond_404_pas_500(monde_matrice: dict[str, Any]) -> None:
    client: TestClient = monde_matrice["client"]
    reponse = client.post("/lots/999999/annuler", headers=monde_matrice["identites"]["service"])
    assert reponse.status_code == 404, reponse.text


@pytest.mark.postgres
def test_brouillon_depuis_pdf_illisible_repond_422_sans_fuite(monde_matrice: dict[str, Any]) -> None:
    client: TestClient = monde_matrice["client"]
    reponse = client.post(
        "/gabarits/brouillon/from-pdf",
        headers=monde_matrice["identites"]["admin"],
        files={"fichier": ("variante.pdf", b"%PDF-1.4 contenu volontairement illisible", "application/pdf")},
    )
    assert reponse.status_code == 422, reponse.text
    assert "/tmp" not in reponse.text and "brouillon-" not in reponse.text
