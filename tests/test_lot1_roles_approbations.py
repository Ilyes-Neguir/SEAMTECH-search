"""Lot 1 — quatre rôles canoniques et approbations instantanées.

Ces tests sont le contrat du Lot 1 du plan directeur v2 :

* ``admin`` administre les comptes et les surfaces techniques, mais ne
  corrige, ne valide, ne télécharge et ne décide jamais métier ;
* ``lecture`` lit et prévisualise, sans mutation ni demande ;
* ``utilisateur`` demande un téléchargement, une modification ou une
  suppression logique ;
* ``approbateur`` est le seul décideur et le seul correcteur direct ;
* l'auto-approbation est fermée par défaut ;
* un changement de rôle révoque immédiatement les sessions de la cible ;
* une autorisation de téléchargement approuvée reste utilisable pendant sa
  fenêtre bornée (les requêtes Range/reprise ne la « consomment » pas).

Les parcours HTTP tournent sur PostgreSQL réel et des sessions réelles. Les
trois premiers tests sont purs : ils verrouillent le vocabulaire, le défaut
fermé et le contrat SQL de la migration 027 sans service externe.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comptes.comptes import (
    IDENTIFIANT_SECOURS,
    ROLES,
    changer_role_utilisateur,
    creer_utilisateur,
    ouvrir_session,
    session_valide,
)
from seamtech_search.config import AppConfig
from seamtech_search.schema_metier import (
    SQL_027_ROLES_APPROBATIONS,
    TABLES_METIER,
    VERSION_SCHEMA_METIER,
)

JETON_SERVICE = "jeton-service-lot1"
CODE_MODIFICATION = "1001-GV-006"
CODE_SUPPRESSION = "1002-GEN-005"
CHAMP_COTE = "cotes.finie.slu_m"


# ---------------------------------------------------------------------------
# Contrats purs
# ---------------------------------------------------------------------------


def test_vocabulaire_des_roles_et_defauts_fermes(tmp_path: Path) -> None:
    from seamtech_search.comptes.comptes import ROLE_DEFAUT, ROLE_SECOURS

    assert ROLES == ("admin", "lecture", "utilisateur", "approbateur")
    assert ROLE_DEFAUT == "lecture"
    assert ROLE_SECOURS == "admin"
    assert callable(changer_role_utilisateur)

    config = AppConfig(root_paths=[tmp_path])
    assert config.allow_self_approval is False


def test_migration_027_version_tables_et_conversion_des_anciens_roles() -> None:
    assert VERSION_SCHEMA_METIER == "027_roles_approbations"
    assert "approval_requests" in TABLES_METIER
    assert "in_app_notifications" in TABLES_METIER
    assert len(TABLES_METIER) == 40

    sql = SQL_027_ROLES_APPROBATIONS
    # Conversion préservant les capacités historiques : un ancien opérateur
    # corrigeait/validait ; le convertir en simple utilisateur supprimerait
    # silencieusement le dernier approbateur d'une installation.
    assert "WHEN 'administrateur' THEN 'admin'" in sql
    assert "WHEN 'operateur' THEN 'approbateur'" in sql
    assert "ELSE 'lecture'" in sql
    assert "role IN ('admin', 'lecture', 'utilisateur', 'approbateur')" in sql
    assert "ALTER COLUMN role SET DEFAULT 'lecture'" in sql
    assert "CREATE TABLE IF NOT EXISTS approval_requests" in sql
    assert "CREATE TABLE IF NOT EXISTS in_app_notifications" in sql
    # Les sessions ouvertes avant la conversion ne doivent pas survivre avec
    # une ancienne sémantique de rôle.
    assert "UPDATE session_ui SET revoque_le = now()" in sql


def test_auto_approbation_reste_un_interrupteur_explicite(tmp_path: Path) -> None:
    assert AppConfig(root_paths=[tmp_path], allow_self_approval=True).allow_self_approval is True
    assert AppConfig(root_paths=[tmp_path], allow_self_approval=False).allow_self_approval is False


# ---------------------------------------------------------------------------
# Fixture PostgreSQL réelle
# ---------------------------------------------------------------------------


@pytest.fixture()
def monde_lot1(base_recherche: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    index = base_recherche["index"]
    archive = tmp_path / "archive"
    archive.mkdir()
    chemin_pdf = archive / "fiche-lot1.pdf"
    contenu_pdf = b"%PDF-1.4\n% SEAMTECH Lot 1 synthetic approval fixture\n" + (b"0" * 2048)
    chemin_pdf.write_bytes(contenu_pdf)

    comptes: dict[str, dict[str, Any]] = {}
    for cle, identifiant, role in (
        ("admin", "lot1-admin", "admin"),
        ("lecture", "lot1-lecture", "lecture"),
        ("utilisateur", "lot1-utilisateur", "utilisateur"),
        ("approbateur", "lot1-approbateur", "approbateur"),
        ("approbateur2", "lot1-approbateur-2", "approbateur"),
    ):
        compte = creer_utilisateur(
            index,
            identifiant,
            f"Compte {identifiant}",
            f"MotDePasse-{identifiant}-2026",
            role=role,
            doit_changer_mot_de_passe=False,
        )
        session = ouvrir_session(index, int(compte["id_utilisateur"]))
        comptes[cle] = {**compte, **session}

    id_fiche = int(base_recherche["fiches"][CODE_MODIFICATION])
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m) VALUES (%s, 'finie', 6.6)",
                (id_fiche,),
            )
            curseur.execute(
                "INSERT INTO fiche_champ_extrait "
                "(id_fiche, champ, rang, table_cible, colonne_cible, valeur_brute, "
                "valeur_normalisee, methode, confiance) "
                "VALUES (%s, %s, NULL, 'fiche_cotes', 'slu_m', '6.6', '6.6', 'test-lot1', 0.9)",
                (id_fiche, CHAMP_COTE),
            )
            curseur.execute(
                "INSERT INTO documents "
                "(path_key, path, name, parent_path, extension, size, modified_at, is_dir, "
                "content, search_vector, id_fiche, role, category, extraction_status) "
                "VALUES (%s, %s, %s, %s, '.pdf', %s, %s, FALSE, '', ''::tsvector, %s, "
                "'fiche_pdf', 'analyzed', 'metadata') RETURNING id",
                (
                    os.path.normcase(str(chemin_pdf.resolve())),
                    str(chemin_pdf.resolve()),
                    chemin_pdf.name,
                    str(archive.resolve()),
                    len(contenu_pdf),
                    time.time(),
                    id_fiche,
                ),
            )
            id_piece = int(curseur.fetchone()[0])
            curseur.execute(
                "INSERT INTO fiche_piece_jointe "
                "(id_fiche, id_document, chemin, role, empreinte_sha256, taille_octets) "
                "VALUES (%s, %s, %s, 'fiche_pdf', %s, %s)",
                (
                    id_fiche,
                    id_piece,
                    str(chemin_pdf.resolve()),
                    hashlib.sha256(contenu_pdf).hexdigest(),
                    len(contenu_pdf),
                ),
            )
            curseur.execute("SELECT revision FROM fiche WHERE code = %s", (CODE_MODIFICATION,))
            revision = int(curseur.fetchone()[0])
            curseur.execute("SELECT revision FROM fiche WHERE code = %s", (CODE_SUPPRESSION,))
            revision_suppression = int(curseur.fetchone()[0])

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[archive],
        database_path=tmp_path / "unused-lot1.db",
        database_url=base_recherche["url"],
        auth_token=JETON_SERVICE,
        storage_backend="local",
        web_worker_enabled=False,
    )
    return {
        **base_recherche,
        "client": TestClient(create_app(config)),
        "comptes": comptes,
        "id_piece": id_piece,
        "revision": revision,
        "revision_suppression": revision_suppression,
        "contenu_pdf": contenu_pdf,
    }


def _entetes(compte: dict[str, Any]) -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": str(compte["identifiant"]),
        "X-SEAMTECH-ROLE": str(compte["role"]),
        "X-SEAMTECH-SESSION": str(compte["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(compte["jeton"]),
    }


def _entetes_secours() -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-UTILISATEUR": IDENTIFIANT_SECOURS,
        "X-SEAMTECH-ROLE": "admin",
    }


def _valeur_unique(index: Any, sql: str, parametres: tuple[Any, ...]) -> Any:  # noqa: ANN401
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(sql, parametres)
            ligne = curseur.fetchone()
    assert ligne is not None
    return ligne[0]


# ---------------------------------------------------------------------------
# Rôles et sessions
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_changement_de_role_revoque_les_sessions_et_route_admin(monde_lot1: dict[str, Any]) -> None:
    index = monde_lot1["index"]
    client: TestClient = monde_lot1["client"]
    cible = monde_lot1["comptes"]["utilisateur"]
    assert session_valide(index, int(cible["id_session"]), str(cible["jeton"])) is True

    resultat = changer_role_utilisateur(index, str(cible["identifiant"]), "lecture")
    assert resultat["ancien_role"] == "utilisateur"
    assert resultat["nouveau_role"] == "lecture"
    assert resultat["sessions_revoquees"] >= 1
    assert session_valide(index, int(cible["id_session"]), str(cible["jeton"])) is False

    # Même rôle : aucun effet de bord, aucune révocation artificielle.
    resultat = changer_role_utilisateur(index, str(cible["identifiant"]), "lecture")
    assert resultat["sessions_revoquees"] == 0

    # La route HTTP est réservée à admin : un approbateur ne s'administre pas.
    refuse = client.post(
        f"/auth/utilisateurs/{cible['identifiant']}/role",
        headers=_entetes(monde_lot1["comptes"]["approbateur"]),
        json={"role": "utilisateur"},
    )
    assert refuse.status_code == 403

    accepte = client.post(
        f"/auth/utilisateurs/{cible['identifiant']}/role",
        headers=_entetes(monde_lot1["comptes"]["admin"]),
        json={"role": "utilisateur"},
    )
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["nouveau_role"] == "utilisateur"


@pytest.mark.postgres
def test_schema_027_defaut_lecture_et_rejet_des_roles_herites(monde_lot1: dict[str, Any]) -> None:
    index = monde_lot1["index"]
    role = _valeur_unique(
        index,
        "INSERT INTO utilisateur (identifiant, nom) VALUES ('lot1-sans-role', 'Sans rôle') RETURNING role",
        (),
    )
    assert role == "lecture"

    tables = {
        str(_valeur_unique(index, "SELECT to_regclass(%s)::text", (nom,)))
        for nom in ("approval_requests", "in_app_notifications")
    }
    assert tables == {"approval_requests", "in_app_notifications"}

    with pytest.raises(Exception):
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute(
                    "INSERT INTO utilisateur (identifiant, nom, role) VALUES ('lot1-legacy', 'Legacy', 'operateur')"
                )


# ---------------------------------------------------------------------------
# Parcours d'approbation
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_modification_utilisateur_executee_atomiquement_par_approbateur(
    monde_lot1: dict[str, Any],
) -> None:
    client: TestClient = monde_lot1["client"]
    comptes = monde_lot1["comptes"]
    corps = {
        "type_demande": "modification",
        "code_fiche": CODE_MODIFICATION,
        "motif": "La cote relue sur le document vaut 7,7 m.",
        "modification": {
            "champ": CHAMP_COTE,
            "valeur": "7.7",
            "revision": monde_lot1["revision"],
        },
    }

    creee = client.post("/approvals", headers=_entetes(comptes["utilisateur"]), json=corps)
    assert creee.status_code == 201, creee.text
    demande = creee.json()
    assert demande["statut"] == "en_attente"
    assert demande["type_demande"] == "modification"
    id_demande = int(demande["id_demande"])

    doublon = client.post("/approvals", headers=_entetes(comptes["utilisateur"]), json=corps)
    assert doublon.status_code == 409

    # Ni lecture, ni admin ne peuvent créer une demande métier.
    for cle in ("lecture", "admin"):
        refusee = client.post("/approvals", headers=_entetes(comptes[cle]), json=corps)
        assert refusee.status_code == 403

    file = client.get(
        "/approvals?vue=a_decider",
        headers=_entetes(comptes["approbateur"]),
    )
    assert file.status_code == 200, file.text
    assert id_demande in {int(item["id_demande"]) for item in file.json()["demandes"]}

    for cle in ("admin", "utilisateur", "lecture"):
        decision = client.post(
            f"/approvals/{id_demande}/decide",
            headers=_entetes(comptes[cle]),
            json={"decision": "approuver", "commentaire": "tentative interdite"},
        )
        assert decision.status_code == 403

    executee = client.post(
        f"/approvals/{id_demande}/decide",
        headers=_entetes(comptes["approbateur"]),
        json={"decision": "approuver", "commentaire": "Valeur confirmée sur le PDF."},
    )
    assert executee.status_code == 200, executee.text
    assert executee.json()["statut"] == "executee"

    valeur = _valeur_unique(
        monde_lot1["index"],
        "SELECT slu_m FROM fiche_cotes WHERE id_fiche = %s AND jeu = 'finie'",
        (int(monde_lot1["fiches"][CODE_MODIFICATION]),),
    )
    assert float(valeur) == pytest.approx(7.7)
    corrige_par = _valeur_unique(
        monde_lot1["index"],
        "SELECT corrige_par FROM fiche_champ_extrait WHERE id_fiche = %s AND champ = %s",
        (int(monde_lot1["fiches"][CODE_MODIFICATION]), CHAMP_COTE),
    )
    assert int(corrige_par) == int(comptes["approbateur"]["id_utilisateur"])

    notifications = client.get("/notifications", headers=_entetes(comptes["utilisateur"]))
    assert notifications.status_code == 200, notifications.text
    assert notifications.json()["non_lues"] >= 1
    assert any(
        int(item.get("id_demande") or 0) == id_demande
        for item in notifications.json()["notifications"]
    )


@pytest.mark.postgres
def test_telechargement_approuve_range_et_refus_des_autres_roles(
    monde_lot1: dict[str, Any],
) -> None:
    client: TestClient = monde_lot1["client"]
    comptes = monde_lot1["comptes"]
    id_piece = int(monde_lot1["id_piece"])

    apercu = client.get(f"/pieces/{id_piece}/apercu", headers=_entetes(comptes["lecture"]))
    assert apercu.status_code == 200

    for cle in ("lecture", "utilisateur", "admin"):
        refuse = client.get(f"/pieces/{id_piece}/telecharger", headers=_entetes(comptes[cle]))
        assert refuse.status_code == 403

    creee = client.post(
        "/approvals",
        headers=_entetes(comptes["utilisateur"]),
        json={
            "type_demande": "telechargement",
            "code_fiche": CODE_MODIFICATION,
            "id_piece": id_piece,
            "motif": "Transmettre le PDF à l'atelier de découpe.",
        },
    )
    assert creee.status_code == 201, creee.text
    id_demande = int(creee.json()["id_demande"])

    approuvee = client.post(
        f"/approvals/{id_demande}/decide",
        headers=_entetes(comptes["approbateur"]),
        json={"decision": "approuver", "commentaire": "Besoin atelier confirmé."},
    )
    assert approuvee.status_code == 200, approuvee.text
    assert approuvee.json()["statut"] == "approuvee"
    assert approuvee.json()["expire_le"]

    complet = client.get(
        f"/pieces/{id_piece}/telecharger", headers=_entetes(comptes["utilisateur"])
    )
    assert complet.status_code == 200
    assert complet.content == monde_lot1["contenu_pdf"]

    partiel = client.get(
        f"/pieces/{id_piece}/telecharger",
        headers={**_entetes(comptes["utilisateur"]), "Range": "bytes=0-3"},
    )
    assert partiel.status_code == 206
    assert partiel.content == b"%PDF"

    tete = client.head(f"/pieces/{id_piece}/telecharger", headers=_entetes(comptes["utilisateur"]))
    assert tete.status_code == 200

    statut, executee_le = _valeur_unique(
        monde_lot1["index"],
        "SELECT statut || '|' || (executee_le IS NOT NULL)::text FROM approval_requests WHERE id_demande = %s",
        (id_demande,),
    ).split("|")
    assert statut == "approuvee"  # Range/reprise ne consomment pas l'autorisation.
    assert executee_le == "true"

    direct = client.get(f"/pieces/{id_piece}/telecharger", headers=_entetes(comptes["approbateur"]))
    assert direct.status_code == 200


@pytest.mark.postgres
def test_auto_approbation_annulation_refus_et_notifications_anti_idor(
    monde_lot1: dict[str, Any],
) -> None:
    client: TestClient = monde_lot1["client"]
    comptes = monde_lot1["comptes"]
    corps = {
        "type_demande": "telechargement",
        "code_fiche": CODE_MODIFICATION,
        "id_piece": int(monde_lot1["id_piece"]),
        "motif": "Demande créée par un approbateur.",
    }
    creee = client.post("/approvals", headers=_entetes(comptes["approbateur2"]), json=corps)
    assert creee.status_code == 201, creee.text
    id_demande = int(creee.json()["id_demande"])

    auto = client.post(
        f"/approvals/{id_demande}/decide",
        headers=_entetes(comptes["approbateur2"]),
        json={"decision": "approuver", "commentaire": "Je m'approuve."},
    )
    assert auto.status_code == 403

    annulation_autrui = client.post(
        f"/approvals/{id_demande}/annuler",
        headers=_entetes(comptes["approbateur"]),
        json={},
    )
    assert annulation_autrui.status_code == 403

    annulee = client.post(
        f"/approvals/{id_demande}/annuler",
        headers=_entetes(comptes["approbateur2"]),
        json={},
    )
    assert annulee.status_code == 200, annulee.text
    assert annulee.json()["statut"] == "annulee"

    # Une demande annulée ne bloque pas une nouvelle demande.
    creee2 = client.post("/approvals", headers=_entetes(comptes["utilisateur"]), json=corps)
    assert creee2.status_code == 201, creee2.text
    id_demande2 = int(creee2.json()["id_demande"])

    refus_sans_motif = client.post(
        f"/approvals/{id_demande2}/decide",
        headers=_entetes(comptes["approbateur"]),
        json={"decision": "refuser", "commentaire": ""},
    )
    assert refus_sans_motif.status_code == 422

    refusee = client.post(
        f"/approvals/{id_demande2}/decide",
        headers=_entetes(comptes["approbateur"]),
        json={"decision": "refuser", "commentaire": "Utiliser l'aperçu interne."},
    )
    assert refusee.status_code == 200, refusee.text
    assert refusee.json()["statut"] == "refusee"

    notifications = client.get("/notifications", headers=_entetes(comptes["utilisateur"]))
    assert notifications.status_code == 200
    notification = next(
        item
        for item in notifications.json()["notifications"]
        if int(item.get("id_demande") or 0) == id_demande2
    )
    id_notification = int(notification["id_notification"])

    lecture_autrui = client.post(
        f"/notifications/{id_notification}/lu",
        headers=_entetes(comptes["approbateur"]),
        json={},
    )
    assert lecture_autrui.status_code == 404

    lue = client.post(
        f"/notifications/{id_notification}/lu",
        headers=_entetes(comptes["utilisateur"]),
        json={},
    )
    assert lue.status_code == 200, lue.text
    assert lue.json()["lue"] is True


@pytest.mark.postgres
def test_suppression_logique_approuvee_puis_reouverture_approbateur(
    monde_lot1: dict[str, Any],
) -> None:
    client: TestClient = monde_lot1["client"]
    comptes = monde_lot1["comptes"]

    creee = client.post(
        "/approvals",
        headers=_entetes(comptes["utilisateur"]),
        json={
            "type_demande": "suppression",
            "code_fiche": CODE_SUPPRESSION,
            "motif": "Fiche créée en double pendant l'essai.",
            "revision": monde_lot1["revision_suppression"],
        },
    )
    assert creee.status_code == 201, creee.text
    id_demande = int(creee.json()["id_demande"])

    executee = client.post(
        f"/approvals/{id_demande}/decide",
        headers=_entetes(comptes["approbateur"]),
        json={"decision": "approuver", "commentaire": "Doublon confirmé."},
    )
    assert executee.status_code == 200, executee.text
    assert executee.json()["statut"] == "executee"

    # Suppression LOGIQUE : ligne et traçabilité conservées, archive intacte.
    statut = _valeur_unique(
        monde_lot1["index"],
        "SELECT statut FROM fiche WHERE code = %s",
        (CODE_SUPPRESSION,),
    )
    assert statut == "supprimee"
    action = _valeur_unique(
        monde_lot1["index"],
        "SELECT action FROM fiche_validation WHERE id_fiche = %s ORDER BY id_validation DESC LIMIT 1",
        (int(monde_lot1["fiches"][CODE_SUPPRESSION]),),
    )
    assert action == "supprimer"

    revision = int(
        _valeur_unique(
            monde_lot1["index"],
            "SELECT revision FROM fiche WHERE code = %s",
            (CODE_SUPPRESSION,),
        )
    )
    rouver = client.post(
        f"/fiches/{CODE_SUPPRESSION}/rouvrir",
        headers=_entetes(comptes["approbateur"]),
        json={"commentaire": "Restauration après vérification.", "revision": revision},
    )
    assert rouver.status_code == 200, rouver.text
    assert rouver.json()["statut"] == "a_valider"


@pytest.mark.postgres
def test_correction_et_validation_directes_reservees_a_lapprobateur(
    monde_lot1: dict[str, Any],
) -> None:
    client: TestClient = monde_lot1["client"]
    comptes = monde_lot1["comptes"]
    corps_correction = {
        "champ": CHAMP_COTE,
        "valeur": "6.9",
        "revision": monde_lot1["revision"],
    }

    for cle in ("admin", "lecture", "utilisateur"):
        refusee = client.post(
            f"/fiches/{CODE_MODIFICATION}/corriger",
            headers=_entetes(comptes[cle]),
            json=corps_correction,
        )
        assert refusee.status_code == 403

    secours = client.post(
        f"/fiches/{CODE_MODIFICATION}/corriger",
        headers=_entetes_secours(),
        json=corps_correction,
    )
    assert secours.status_code == 403

    service_seul = client.post(
        f"/fiches/{CODE_MODIFICATION}/corriger",
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
        json={**corps_correction, "utilisateur": "lot1-approbateur"},
    )
    assert service_seul.status_code in {401, 403}

    valeur = _valeur_unique(
        monde_lot1["index"],
        "SELECT slu_m FROM fiche_cotes WHERE id_fiche = %s AND jeu = 'finie'",
        (int(monde_lot1["fiches"][CODE_MODIFICATION]),),
    )
    assert float(valeur) == pytest.approx(6.6)

    corrigee = client.post(
        f"/fiches/{CODE_MODIFICATION}/corriger",
        headers=_entetes(comptes["approbateur"]),
        json=corps_correction,
    )
    assert corrigee.status_code == 200, corrigee.text
    revision = int(corrigee.json()["revision"])

    validation_admin = client.post(
        f"/fiches/{CODE_MODIFICATION}/valider",
        headers=_entetes(comptes["admin"]),
        json={"revision": revision, "commentaire": "Interdit à admin."},
    )
    assert validation_admin.status_code == 403

    validee = client.post(
        f"/fiches/{CODE_MODIFICATION}/valider",
        headers=_entetes(comptes["approbateur"]),
        json={"revision": revision, "commentaire": "Relue et approuvée."},
    )
    assert validee.status_code == 200, validee.text
    assert validee.json()["statut"] == "valide"
