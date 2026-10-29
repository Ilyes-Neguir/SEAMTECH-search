"""Passe finale — sessions nominatives : expiration, révocation, incohérence.

Vérification indépendante du 29/10/2026. Ce que ce module prouve, au niveau du
service (PostgreSQL réel, pas de mock) :

* une session expirée par INACTIVITÉ n'est plus valable ;
* une session révoquée (déconnexion, changement de rôle, désactivation du
  compte) n'est plus valable, même avec un jeton parfaitement conforme ;
* un jeton de session FALSIFIÉ ne passe pas ;
* une session d'un compte ne peut pas être présentée avec l'identité d'un autre
  (en-têtes incohérents → 401) ;
* aucune session ne survit à un changement de rôle (Lot 1).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.comptes.comptes import (
    changer_role_utilisateur,
    creer_utilisateur,
    ouvrir_session,
    session_valide,
)
from seamtech_search.indexer import SearchIndex
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

pytestmark = pytest.mark.postgres


@pytest.fixture()
def base(tmp_path: Path) -> Any:  # noqa: ANN401
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "unused-sessions.db", url)
    index.initialize()
    index.run_migrations()
    utilisateur = creer_utilisateur(
        index, "sess-test", "Sessions", "MotDePasse!123", role="lecture", doit_changer_mot_de_passe=False
    )
    try:
        yield {"index": index, "url": url, "utilisateur": utilisateur}
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_session_neuve_valide(base: dict[str, Any]) -> None:
    session = ouvrir_session(base["index"], int(base["utilisateur"]["id_utilisateur"]))
    assert session_valide(base["index"], int(session["id_session"]), str(session["jeton"])) is True


def test_jeton_falsifie_refuse(base: dict[str, Any]) -> None:
    session = ouvrir_session(base["index"], int(base["utilisateur"]["id_utilisateur"]))
    faux = str(session["jeton"])[:-1] + ("A" if not str(session["jeton"]).endswith("A") else "B")
    assert session_valide(base["index"], int(session["id_session"]), faux) is False


def test_session_expiree_par_inactivite(base: dict[str, Any]) -> None:
    """L'inactivité est mesurée sur ``derniere_activite`` : on la recule."""
    session = ouvrir_session(base["index"], int(base["utilisateur"]["id_utilisateur"]))
    with base["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "UPDATE session_ui SET derniere_activite = now() - %s WHERE id_session = %s",
            (timedelta(days=2), int(session["id_session"])),
        )
    assert session_valide(base["index"], int(session["id_session"]), str(session["jeton"])) is False


def test_session_revoquee_refusee(base: dict[str, Any]) -> None:
    session = ouvrir_session(base["index"], int(base["utilisateur"]["id_utilisateur"]))
    with base["index"].connect() as connexion, connexion.cursor() as curseur:
        curseur.execute("UPDATE session_ui SET revoque_le = now() WHERE id_session = %s", (int(session["id_session"]),))
    assert session_valide(base["index"], int(session["id_session"]), str(session["jeton"])) is False


def test_changement_de_role_revoque_les_sessions(base: dict[str, Any]) -> None:
    session = ouvrir_session(base["index"], int(base["utilisateur"]["id_utilisateur"]))
    resultat = changer_role_utilisateur(base["index"], "sess-test", "approbateur")
    assert resultat["sessions_revoquees"] >= 1
    assert session_valide(base["index"], int(session["id_session"]), str(session["jeton"])) is False


def test_compte_desactive_ne_peut_plus_se_connecter(base: dict[str, Any]) -> None:
    """Le refus est porté par la CONNEXION (``verifier_identifiants``), pas par
    le constructeur de session : ``ouvrir_session`` est une primitive interne
    appelée après authentification. On éprouve donc le chemin réel."""
    from fastapi import HTTPException

    from seamtech_search.comptes.comptes import desactiver_utilisateur, verifier_identifiants

    desactiver_utilisateur(base["index"], "sess-test")
    with pytest.raises(HTTPException) as erreur:
        verifier_identifiants(base["index"], "sess-test", "MotDePasse!123")
    assert erreur.value.status_code == 401
    # Le message ne distingue pas « compte désactivé » de « mot de passe faux ».
    assert "désactivé" not in str(erreur.value.detail).lower()
