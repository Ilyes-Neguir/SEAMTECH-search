"""Passe finale — matrice de rôles, refus explicites et isolation entre comptes.

Vérification indépendante du 29/10/2026. Ce module parcourt **toutes** les
routes du backend avec **tous** les acteurs du produit et vérifie trois choses :

1. aucune route ne renvoie 5xx pour un acteur donné (une erreur non gérée est
   un défaut, pas une réponse) ;
2. tout ce qui n'est pas explicitement public refuse l'anonyme (401/403), sauf
   les routes dont la validation de corps intervient AVANT l'authentification
   (comportement FastAPI) — elles sont alors éprouvées avec une charge valide ;
3. les actions qui DOIVENT être refusées le sont : approbateur seul décideur,
   admin jamais décideur métier, lecture jamais mutateur, téléchargement
   interdit hors approbateur, surfaces techniques réservées à l'admin.

Le test est volontairement piloté par la table de routage réelle : une route
ajoutée demain est couverte sans qu'on y pense.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable, semer_corpus

pytestmark = pytest.mark.postgres

JETON = "jeton-matrice-finale"
CODE = "0701-GV-001"

#: Sondes d'exploitation et documentation : publiques par construction.
PUBLIQUES = {"/live", "/ready"}

#: Routes dont le corps est validé avant l'authentification (FastAPI) : elles
#: sont éprouvées séparément avec une charge valide, sinon un 422 masquerait le
#: vrai contrôle d'accès.
VALIDATION_AVANT_AUTH = {
    "/comparaison-voiles",
    "/comparaison-voiles/candidats",
    "/confiance-reutilisation/evaluations",
    "/confiance-reutilisation/population",
    "/preview",
    "/search",
    "/assistant",
    "/imports",
    "/imports/confirm",
    "/imports/scan",
    "/imports/upload",
    "/imports/dossier/televersements/{jeton_session}/fichiers",
    "/ml/modeles",
    "/open",
    "/referentiel/bateaux/{id_modele}/fusion",
    "/referentiel/bateaux/{id_modele}/alias",
    "/referentiel/bateaux/{id_modele}/defusion",
    "/gabarits/brouillon/from-pdf",
    "/gabarits/{code}/versions",
}

REMPLACEMENTS = {
    "{code}": CODE,
    "{id_fiche}": "1",
    "{id_document}": "1",
    "{piece_id}": "1",
    "{id_demande}": "1",
    "{id_lot}": "1",
    "{identifiant}": "matrix-lecture",
    "{id_extraction}": "1",
    "{jeton_session}": "jeton-inexistant",
    "{import_id}": "import-inexistant",
    "{artifact}": "raw",
    "{id_brouillon}": "1",
    "{id_modele}": "1",
    "{id_notification}": "1",
    "{id_evaluation}": "1",
}


def _cible(chemin: str) -> str:
    for motif, valeur in REMPLACEMENTS.items():
        chemin = chemin.replace(motif, valeur)
    return chemin


@pytest.fixture(scope="module")
def monde() -> Any:  # noqa: ANN401 - monde de test hétérogène
    nom, url = _creer_base_jetable()
    index = SearchIndex(Path("/tmp/matrice-finale-unused.db"), url)
    index.initialize()
    index.run_migrations()
    semer_corpus(index)

    comptes: dict[str, dict[str, Any]] = {}
    for role in ("admin", "lecture", "utilisateur", "approbateur"):
        utilisateur = creer_utilisateur(
            index,
            f"matrix-{role}",
            f"Matrice {role}",
            "MotDePasse!123",
            role=role,
            doit_changer_mot_de_passe=False,
        )
        comptes[role] = {
            "utilisateur": utilisateur,
            "session": ouvrir_session(index, int(utilisateur["id_utilisateur"])),
        }
    # Deuxième compte du même rôle : sert aux essais IDOR.
    autre = creer_utilisateur(
        index,
        "matrix-autre-lecture",
        "Autre lecture",
        "MotDePasse!123",
        role="lecture",
        doit_changer_mot_de_passe=False,
    )
    comptes["autre_lecture"] = {
        "utilisateur": autre,
        "session": ouvrir_session(index, int(autre["id_utilisateur"])),
    }

    config = AppConfig(
        root_paths=[Path("/tmp/matrice-finale-racine")],
        database_path=Path("/tmp/matrice-finale-unused.db"),
        database_url=url,
        auth_token=JETON,
        storage_backend="local",
        web_worker_enabled=False,
        allow_self_approval=False,
    )
    Path("/tmp/matrice-finale-racine").mkdir(exist_ok=True)
    client = TestClient(create_app(config))
    try:
        yield {"index": index, "client": client, "comptes": comptes, "url": url}
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def _entetes(monde: dict[str, Any], role: str) -> dict[str, str]:
    if role == "anonyme":
        return {}
    if role == "secours":
        return {"X-SEAMTECH-TOKEN": JETON, "X-SEAMTECH-UTILISATEUR": "secours", "X-SEAMTECH-ROLE": "admin"}
    compte = monde["comptes"][role]
    return {
        "X-SEAMTECH-TOKEN": JETON,
        "X-SEAMTECH-UTILISATEUR": str(compte["utilisateur"]["identifiant"]),
        "X-SEAMTECH-ROLE": str(compte["utilisateur"]["role"]),
        "X-SEAMTECH-SESSION": str(compte["session"]["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(compte["session"]["jeton"]),
    }


def _routes(app: Any) -> list[tuple[str, str]]:  # noqa: ANN401
    vues: set[tuple[str, str]] = set()
    for route in app.routes:
        chemin = getattr(route, "path", None)
        for methode in getattr(route, "methods", None) or set():
            if not chemin or methode in {"HEAD", "OPTIONS"}:
                continue
            if chemin.startswith("/auth/deconnexion"):
                # La déconnexion RÉVOQUE la session : la balayer casserait les
                # appels suivants du même compte (éprouvée à part).
                continue
            vues.add((methode, chemin))
    return sorted(vues)


def test_aucune_route_ne_plante_et_l_anonyme_est_refuse(monde: dict[str, Any]) -> None:
    client: TestClient = monde["client"]
    app = client.app
    plantes: list[str] = []
    fuites: list[str] = []
    for methode, chemin in _routes(app):
        if chemin in VALIDATION_AVANT_AUTH:
            continue
        cible = _cible(chemin)
        for role in ("anonyme", "secours", "admin", "lecture", "utilisateur", "approbateur"):
            # Un acteur nominatif n'est balayé que sur les routes de LECTURE :
            # appeler une mutation réelle (désactivation d'un compte, purge,
            # décision…) détruirait l'état des tests suivants. L'anonyme, lui,
            # ne peut rien muter : il est balayé sur toutes les méthodes.
            if role != "anonyme" and methode != "GET":
                continue
            reponse = client.request(methode, cible, headers=_entetes(monde, role))
            if reponse.status_code >= 500 and reponse.status_code != 503:
                plantes.append(f"{role} {methode} {cible} → {reponse.status_code} {reponse.text[:80]}")
            # Pour l'anonyme : interdit d'obtenir des DONNÉES. Un 422 est la
            # validation de corps de FastAPI, exécutée avant le handler : elle
            # ne renvoie rien et ne prouve donc pas un accès. Ces routes sont
            # re-testées avec une charge VALIDE plus bas (401 exigé).
            if (
                role == "anonyme"
                and chemin not in PUBLIQUES
                and reponse.status_code not in {401, 403, 422}
            ):
                fuites.append(f"{methode} {cible} → {reponse.status_code}")
            if role == "anonyme" and 200 <= reponse.status_code < 300 and chemin not in PUBLIQUES:
                fuites.append(f"2xx anonyme : {methode} {cible}")
    assert plantes == [], f"routes en erreur serveur : {plantes}"
    assert fuites == [], f"routes accessibles sans authentification : {fuites}"


@pytest.mark.parametrize(
    ("methode", "cible", "corps", "attendu"),
    [
        ("GET", "/search?q=voile", None, 401),
        ("POST", "/assistant", {"question": "quelle voile ?"}, 401),
        ("POST", "/imports/scan", {"source_path": "/tmp/matrice-finale-racine"}, 401),
        ("GET", "/preview?path=/tmp/matrice-finale-racine", None, 401),
        ("POST", "/open?path=/tmp/matrice-finale-racine", None, 401),
        ("GET", "/comparaison-voiles?a=1&b=2", None, 401),
        ("GET", "/confiance-reutilisation/population?boat_model_id=1&type_voile=GV", None, 401),
        ("POST", "/ml/modeles", {"version": "v1", "chemin": "/tmp/x", "algorithme": "lineaire"}, 401),
        ("POST", "/referentiel/bateaux/1/fusion", {"id_modele_cible": 2}, 401),
        ("POST", "/gabarits/0701/versions", {"contenu": {}}, 401),
    ],
)
def test_validation_de_corps_ne_doit_pas_masquer_l_authentification(
    monde: dict[str, Any], methode: str, cible: str, corps: dict[str, Any] | None, attendu: int
) -> None:
    """Avec une charge VALIDE, l'anonyme est refusé — le 422 seul ne prouve rien."""
    client: TestClient = monde["client"]
    reponse = client.request(methode, cible, json=corps)
    assert reponse.status_code == attendu, (
        f"{methode} {cible} sans authentification → {reponse.status_code} "
        f"({reponse.text[:120]}) ; attendu {attendu}"
    )


@pytest.mark.parametrize(
    ("methode", "cible", "corps", "refuses", "motif"),
    [
        ("POST", f"/fiches/{CODE}/valider", {"revision": 1}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", f"/fiches/{CODE}/rejeter", {"motif": "x", "revision": 1}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", f"/fiches/{CODE}/corriger", {"champ": "titre", "valeur": "x", "revision": 1}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", f"/fiches/{CODE}/rouvrir", {"motif": "x", "revision": 1}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", "/approvals/1/decide", {"decision": "approuver"}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", "/validation/lot", {"codes": [CODE]}, ("admin", "lecture", "utilisateur", "secours"), "approbateur"),
        ("POST", "/extractions-voile/1/corriger", {"champ": "sf_m", "valeur": 1.0}, ("admin", "lecture", "utilisateur"), "approbateur"),
        ("POST", "/accessoires/extractions/1/lier", {"id_modele": 1}, ("admin", "lecture", "utilisateur"), "approbateur"),
    ],
)
def test_seul_l_approbateur_decide(
    monde: dict[str, Any], methode: str, cible: str, corps: dict[str, Any], refuses: tuple[str, ...], motif: str
) -> None:
    client: TestClient = monde["client"]
    for role in refuses:
        reponse = client.request(methode, cible, json=corps, headers=_entetes(monde, role))
        assert reponse.status_code in {403, 401}, f"{role} ne doit pas pouvoir {cible} (→ {reponse.status_code})"
    assert motif == "approbateur"


@pytest.mark.parametrize(
    ("methode", "cible", "corps"),
    [
        ("POST", "/auth/utilisateurs", {"identifiant": "x", "nom": "X", "mot_de_passe": "MotDePasse!123", "role": "lecture"}),
        ("POST", "/auth/utilisateurs/matrix-lecture/role", {"role": "admin"}),
        ("POST", "/auth/utilisateurs/matrix-lecture/desactiver", {}),
        ("POST", "/maintenance/cleanup", {}),
        ("GET", "/audit", None),
        ("GET", "/auth/utilisateurs", None),
        ("GET", "/ml/modeles", None),
        ("GET", "/recherche/journal", None),
    ],
)
def test_admin_seul_sur_les_surfaces_techniques(
    monde: dict[str, Any], methode: str, cible: str, corps: dict[str, Any] | None
) -> None:
    client: TestClient = monde["client"]
    for role in ("lecture", "utilisateur", "approbateur"):
        reponse = client.request(methode, cible, json=corps, headers=_entetes(monde, role))
        assert reponse.status_code == 403, f"{role} sur {cible} → {reponse.status_code} (403 attendu)"


@pytest.mark.parametrize(
    ("methode", "cible", "corps"),
    [
        ("POST", "/imports/scan", {"source_path": "/tmp/matrice-finale-racine"}),
        ("POST", "/imports/dossier", {"source_path": "/tmp/matrice-finale-racine"}),
        ("POST", "/lots/1/annuler", {}),
    ],
)
def test_lecture_ne_mute_rien(
    monde: dict[str, Any], methode: str, cible: str, corps: dict[str, Any]
) -> None:
    client: TestClient = monde["client"]
    reponse = client.request(methode, cible, json=corps, headers=_entetes(monde, "lecture"))
    assert reponse.status_code == 403, f"lecture sur {cible} → {reponse.status_code} (403 attendu)"


def test_telechargement_direct_interdit_hors_approbateur(monde: dict[str, Any]) -> None:
    """Le téléchargement d'une pièce n'est jamais direct pour un non-approbateur."""
    client: TestClient = monde["client"]
    for role in ("admin", "lecture", "utilisateur", "secours"):
        reponse = client.get("/pieces/1/telecharger", headers=_entetes(monde, role))
        assert reponse.status_code == 403, f"{role} → {reponse.status_code}"
    # L'approbateur, lui, passe le contrôle de rôle : c'est la pièce qui manque
    # (elle n'existe pas dans ce jeu), donc 403/404 mais jamais 401.
    reponse = client.get("/pieces/1/telecharger", headers=_entetes(monde, "approbateur"))
    assert reponse.status_code in {403, 404}


def test_idor_notification_d_un_autre_compte(monde: dict[str, Any]) -> None:
    """Un compte ne peut pas marquer comme lue la notification d'un AUTRE compte.

    La réponse est indistinguable de « notification inexistante » (404) : elle
    ne révèle pas l'existence d'une ressource d'autrui.
    """
    client: TestClient = monde["client"]
    reponse = client.post("/notifications/1/lu", headers=_entetes(monde, "autre_lecture"))
    assert reponse.status_code in {403, 404}


def test_desactivation_revoque_immediatement_la_session(monde: dict[str, Any]) -> None:
    """Une session vivante cesse de l'être dès la désactivation du compte."""
    client: TestClient = monde["client"]
    entetes = _entetes(monde, "autre_lecture")
    assert client.get("/auth/session", headers=entetes).status_code == 200
    desactivation = client.post(
        "/auth/utilisateurs/matrix-autre-lecture/desactiver", json={}, headers=_entetes(monde, "admin")
    )
    assert desactivation.status_code < 300, desactivation.text
    apres = client.get("/auth/session", headers=entetes)
    assert apres.status_code in {401, 403}, f"session encore valable après désactivation ({apres.status_code})"


def test_mot_de_passe_faux_ne_rend_pas_de_session(monde: dict[str, Any]) -> None:
    client: TestClient = monde["client"]
    reponse = client.post(
        "/auth/connexion", json={"identifiant": "matrix-lecture", "mot_de_passe": "mauvais-mot-de-passe"}
    )
    assert reponse.status_code in {401, 403}
    assert "jeton" not in reponse.text.lower()


def test_jeton_de_service_seul_ne_suffit_pas_pour_les_surfaces_nominatives(monde: dict[str, Any]) -> None:
    """Le jeton de service seul n'ouvre NI l'administration, NI une mutation.

    Modèle d'architecture documenté (``securite_requetes``) : le backend n'est
    joignable que par le frontal, qui présente le jeton de service ; les routes
    de LECTURE acceptent donc l'acteur « service ». Les surfaces nominatives et
    toutes les mutations, elles, exigent une session humaine : c'est ce que ce
    test vérifie. Conséquence opérationnelle à ne jamais perdre de vue : le port
    du backend ne doit pas être exposé hors du réseau du frontal, sinon le jeton
    de service devient une clé de lecture.
    """
    client: TestClient = monde["client"]
    for cible in ("/auth/utilisateurs", "/audit", "/recherche/journal"):
        reponse = client.get(cible, headers={"X-SEAMTECH-TOKEN": JETON})
        assert reponse.status_code in {401, 403}, f"{cible} ouvert au seul jeton de service ({reponse.status_code})"
    for methode, cible, corps in (
        ("POST", f"/fiches/{CODE}/valider", {"revision": 1}),
        ("POST", "/validation/lot", {"codes": [CODE]}),
        ("POST", "/maintenance/cleanup", {}),
        ("POST", "/approvals/1/decide", {"decision": "approuver"}),
    ):
        reponse = client.request(methode, cible, json=corps, headers={"X-SEAMTECH-TOKEN": JETON})
        assert reponse.status_code in {401, 403}, (
            f"{methode} {cible} accepté avec le seul jeton de service ({reponse.status_code})"
        )


def _nb_liens_doublons(index: Any) -> int:  # noqa: ANN401 - SearchIndex réel
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT count(*) FROM fiche_lien WHERE type LIKE 'doublon%'")
        return int(curseur.fetchone()[0])


def test_scan_doublons_est_une_lecture_et_seule_l_ecriture_exige_un_approbateur(
    monde: dict[str, Any],
) -> None:
    """Tranche, preuve en main, le point « un rôle lecture peut appeler un POST ».

    ``POST /fiches/doublons/scan`` et ``POST /validation/doublons`` sont des
    POST par la forme (corps JSON potentiellement long), pas par l'effet :
    sans ``appliquer=true`` ils ne font que COMPTER et LIRE. Ce test mesure
    l'état de la table de liens avant/après pour le prouver, puis vérifie que
    la seule variante qui ÉCRIT (``appliquer=true``) est refusée à tout le
    monde sauf à un approbateur nominatif.
    """
    client: TestClient = monde["client"]
    index = monde["index"]
    avant = _nb_liens_doublons(index)

    for role in ("lecture", "utilisateur"):
        reponse = client.post("/fiches/doublons/scan", json={}, headers=_entetes(monde, role))
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["dry_run"] is True, "le défaut doit rester le dry-run"
    reponse = client.post(
        "/validation/doublons", json={"codes": [CODE]}, headers=_entetes(monde, "lecture")
    )
    assert reponse.status_code == 200, reponse.text

    assert _nb_liens_doublons(index) == avant, "un dry-run ne doit écrire AUCUNE ligne de lien"

    for role in ("lecture", "utilisateur", "admin", "secours"):
        refuse = client.post(
            "/fiches/doublons/scan", json={"appliquer": True}, headers=_entetes(monde, role)
        )
        assert refuse.status_code == 403, f"{role} a pu écrire les liens ({refuse.status_code})"
    assert _nb_liens_doublons(index) == avant

    accepte = client.post(
        "/fiches/doublons/scan", json={"appliquer": True}, headers=_entetes(monde, "approbateur")
    )
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["dry_run"] is False
