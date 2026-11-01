"""P2 — sécurité offensive : attaques menées contre le produit, pas contre un double.

Ce fichier ne « vérifie la sécurité » en général — impossible. Il mène les
attaques qui ont un SENS pour ce produit, et chacune se termine par une
assertion que l'attaquant voudrait voir fausse :

* **traversée de chemin** au téléversement : un nom de fichier ``../../../``
  doit rester confiné au staging ;
* **injection d'en-tête** : un nom portant CR/LF ne doit pas pouvoir forger
  une ligne d'en-tête HTTP (``Content-Disposition``) ;
* **escalade de rôle par en-tête** : ``X-SEAMTECH-ROLE: admin`` sans session
  valide ne doit rien ouvrir ;
* **lien symbolique** : un lien dans un dossier déposé ne doit pas servir à
  lire — ni à effacer — un fichier situé hors du dossier ;
* **traversée sur la route d'artefacts** : un ``artifact`` contenant ``../`` ne
  doit rien servir hors des racines autorisées.

Chaque test nomme l'attaque, l'observe, et échoue si elle réussit. Aucun test
ne se contente de vérifier « il n'y a pas eu d'exception » : on vérifie l'état
du système de fichiers et/ou la réponse exacte.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import staging_root

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


def _client(tmp_path: Path, racine: Path | None = None) -> TestClient:
    config = AppConfig(root_paths=[racine or tmp_path / "racine"], database_path=tmp_path / "data" / "search.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (racine or tmp_path / "racine").mkdir(parents=True, exist_ok=True)
    return TestClient(create_app(config))


def _pdf(octets: bytes | None = None) -> bytes:
    return octets or b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def test_traversee_de_chemin_dans_le_nom_de_fichier_reste_confinee(tmp_path: Path) -> None:
    """``../../../../tmp/pwned.pdf`` ne doit pas écrire hors du staging."""
    racine = tmp_path / "racine"
    racine.mkdir()
    client = _client(tmp_path, racine)
    cible = Path("/tmp/pwned-seamtech.pdf")
    if cible.exists():  # pragma: no cover - nettoyage d'un essai précédent
        cible.unlink()

    reponse = client.post(
        "/imports/upload",
        files=[("files", ("../../../../tmp/pwned-seamtech.pdf", _pdf(), "application/pdf"))],
        data={"folder": "depot-offensif"},
    )

    # Deux issues possibles, toutes deux acceptables : refus explicite (4xx) ou
    # acceptation AVEC confinement. Ce qui est interdit, c'est l'écriture hors
    # staging — et a fortiori un succès silencieux.
    if reponse.status_code == 200:
        staging = staging_root(AppConfig(root_paths=[racine], database_path=tmp_path / "data" / "search.db"))
        chemins = [str(p) for p in staging.rglob("*")] if staging.exists() else []
        assert all(str(staging.resolve()) in str(Path(c).resolve()) for c in chemins if c), (
            f"fichier écrit hors du staging : {chemins}"
        )
    assert not cible.exists(), "traversée réussie : le fichier a été écrit hors du staging"


def test_nom_de_fichier_avec_retour_chariot_n_injecte_pas_d_entete(tmp_path: Path) -> None:
    """Un nom portant CR/LF ne doit pas forger une ligne d'en-tête HTTP."""
    from seamtech_search.fiches.routes import _content_disposition, _nom_fichier_sure

    sale = "fiche.pdf\r\nX-Injected: oui\r\n\r\n<script>alert(1)</script>"
    nom = _nom_fichier_sure(sale)
    assert "\r" not in nom and "\n" not in nom, f"nom non assaini : {nom!r}"
    disposition = _content_disposition(sale, inline=True)
    assert "\r" not in disposition and "\n" not in disposition, (
        f"injection d'en-tête possible : {disposition!r}"
    )
    assert "X-Injected" not in disposition


@pytest.mark.postgres
def test_role_usurpe_par_entete_ne_donne_pas_l_approbation(tmp_path: Path) -> None:
    """Un compte « lecture » qui S'ANNONCE approbateur par en-tête reste lecture.

    L'attaque n'est pas « sans session » (déjà couvert : 401). Elle est : «
    j'ai une session valide, je pose ``X-SEAMTECH-ROLE: approbateur`` sur une
    décision d'approbation ». La route lit bien l'en-tête — c'est donc ici que
    l'usurpation serait possible si le rôle venait du navigateur au lieu de la
    session en base.

    Deux refus sont possibles, tous deux corrects : 401 (en-tête de rôle
    incompatible avec la session présentée) ou 403 (rôle réel insuffisant). Ce
    qui est interdit, c'est un 2xx : la décision serait prise par un compte qui
    n'a pas le rôle.
    """
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable
    from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session, verifier_identifiants
    from seamtech_search.indexer import SearchIndex

    racine = tmp_path / "racine"
    racine.mkdir()
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "unused-offensif.db", url)
    index.initialize()
    index.run_migrations()
    try:
        creer_utilisateur(index, "off-lecteur", "Lecteur Offensif", "Passe!12345", "lecture")
        utilisateur = verifier_identifiants(index, "off-lecteur", "Passe!12345")
        session = ouvrir_session(index, utilisateur["id_utilisateur"])
        config = AppConfig(
            root_paths=[racine],
            database_path=tmp_path / "data" / "search.db",
            database_url=url,
            auth_token="jeton-service-offensif",
        )
        client = TestClient(create_app(config))
        socle = {
            "X-SEAMTECH-TOKEN": "jeton-service-offensif",
            "X-SEAMTECH-SESSION": str(session["id_session"]),
            "X-SEAMTECH-SESSION-JETON": session["jeton"],
        }

        # Référence : sans usurpation, le refus nomme le rôle RÉEL (lecture).
        reference = client.post("/approvals/1/decide", json={"decision": "approuve"}, headers=socle)
        assert reference.status_code == 403, reference.text[:200]
        assert "lecture" in reference.text, (
            "le rôle refusé devrait être celui de la session, pas celui de l'en-tête"
        )

        # Attaque : en-tête de rôle approbateur.
        usurpe = client.post(
            "/approvals/1/decide",
            json={"decision": "approuve"},
            headers={**socle, "X-SEAMTECH-ROLE": "approbateur",
                     "X-SEAMTECH-UTILISATEUR": "off-lecteur"},
        )
        # Un 404 signifierait que la couche d'autorisation a été FRANCHIE et que
        # seul l'absence de demande a arrêté la requête : ce n'est pas un refus.
        assert usurpe.status_code in {401, 403}, (
            f"escalade de rôle réussie : HTTP {usurpe.status_code} — {usurpe.text[:200]}"
        )
        assert "rôle actuel : approbateur" not in usurpe.text, (
            "l'en-tête a été cru : le rôle annoncé apparaît comme rôle actuel"
        )

        # Contrôle : un en-tête CONFORME au rôle réel n'ouvre rien de plus.
        conforme = client.post(
            "/approvals/1/decide",
            json={"decision": "approuve"},
            headers={**socle, "X-SEAMTECH-ROLE": "lecture",
                     "X-SEAMTECH-UTILISATEUR": "off-lecteur"},
        )
        assert conforme.status_code == 403, conforme.text[:200]
    finally:
        index.close()
        _supprimer_base_jetable(nom)


@pytest.mark.postgres
def test_creation_de_compte_par_entete_seul_est_refusee(tmp_path: Path) -> None:
    """Variante en écriture : créer un compte « admin » sans session valide."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from seamtech_search.comptes.comptes import lister_utilisateurs
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    racine = tmp_path / "racine"
    racine.mkdir()
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "unused-offensif2.db", url)
    index.initialize()
    index.run_migrations()
    try:
        config = AppConfig(
            root_paths=[racine],
            database_path=tmp_path / "data" / "search.db",
            database_url=url,
            auth_token="jeton-service-offensif",
        )
        client = TestClient(create_app(config))
        reponse = client.post(
            "/auth/utilisateurs",
            json={"identifiant": "intrus", "nom": "Intrus", "mot_de_passe": "Passe!12345", "role": "admin"},
            headers={"X-SEAMTECH-TOKEN": "jeton-service-offensif", "X-SEAMTECH-ROLE": "admin",
                     "X-SEAMTECH-UTILISATEUR": "intrus"},
        )
        assert reponse.status_code in {401, 403, 404, 422}, (
            f"compte créé sur en-tête seul : HTTP {reponse.status_code}"
        )
        assert not any(u.get("identifiant") == "intrus" for u in lister_utilisateurs(index)), (
            "un compte « intrus » a été créé sans acteur humain authentifié"
        )
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_lien_symbolique_dans_un_depot_ne_donne_pas_acces_hors_racine(tmp_path: Path) -> None:
    """Un lien déposé dans un dossier ne doit pas être suivi pour lire ailleurs."""
    racine = tmp_path / "racine"
    racine.mkdir()
    secret = tmp_path / "secret.txt"
    contenu = b"contenu-confidentiel-synthetique"
    secret.write_bytes(contenu)
    depot = racine / "DEPOT-LIEN"
    depot.mkdir()
    (depot / "fiche.pdf").write_bytes(_pdf())
    lien = depot / "lien-secret.txt"
    try:
        lien.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - plateforme sans liens
        pytest.skip("liens symboliques indisponibles sur cette plateforme")

    client = _client(tmp_path, racine)
    reponse = client.post("/imports/scan", json={"source_path": str(depot)})
    if reponse.status_code == 200:
        corps = reponse.text
        assert "contenu-confidentiel-synthetique" not in corps, (
            "le lien a été suivi : le contenu hors racine apparaît dans la réponse"
        )
    else:
        assert reponse.status_code in {400, 403, 404, 409}, reponse.status_code
    assert secret.read_bytes() == contenu, "le fichier hors racine a été modifié"


def test_route_artefacts_refuse_une_traversee(tmp_path: Path) -> None:
    """``/imports/{id}/artifacts/../../../etc/passwd`` ne doit rien servir."""
    racine = tmp_path / "racine"
    racine.mkdir()
    client = _client(tmp_path, racine)
    for artifact in ("../../../etc/passwd", "..%2F..%2F..%2Fetc%2Fpasswd", "/etc/passwd"):
        reponse = client.get(f"/imports/import-inexistant/artifacts/{artifact}")
        assert reponse.status_code in {401, 403, 404, 422}, (
            f"traversée servie ou erreur inattendue ({artifact}) : HTTP {reponse.status_code}"
        )
        assert "root:" not in reponse.text, f"contenu système servi : {artifact}"


def test_telechargement_refuse_un_chemin_absolu_hors_racines(tmp_path: Path) -> None:
    """``/preview`` ne doit pas servir un fichier désigné par un chemin absolu."""
    racine = tmp_path / "racine"
    racine.mkdir()
    secret = tmp_path / "secret-absolu.txt"
    secret.write_text("secret-synthetique-absolu", encoding="utf-8")
    client = _client(tmp_path, racine)
    reponse = client.get("/preview", params={"path": str(secret)})
    assert "secret-synthetique-absolu" not in reponse.text, "chemin absolu hors racines servi"


def test_health_exige_le_jeton_quand_l_instance_en_a_un(tmp_path: Path) -> None:
    """`/health` n'est ouvert que si l'instance n'a PAS de jeton (mode local).

    Le diagnostic complet (état de la base, migrations appliquées) est une
    lecture d'exploitation : il est protégé dès qu'un jeton de service est
    configuré. Sans jeton, l'instance est volontairement sans contrôle d'accès
    — c'est un choix d'installation, pas une fuite, et le test le DIT au lieu
    de le maquiller en succès.
    """
    racine = tmp_path / "racine"
    racine.mkdir()
    config = AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "data" / "search.db",
        auth_token="jeton-service-offensif",
    )
    client = TestClient(create_app(config))
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"X-SEAMTECH-TOKEN": "jeton-service-offensif"}).status_code == 200
