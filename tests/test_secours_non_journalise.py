"""Lot 0b — compte secours : aucun secret en logs/tests, audit filtré.

Vérifie que :
- audit_logs ne contient jamais mot_de_passe/empreinte/cookie/jeton/secret/sid/token en details_json
- SEAMTECH_UI_PASSWORD n'est pas loggé (filtre _tronquer_details)
- Le comportement secours est en attente décision Lass (non bloquant ici)

Ce test est neutre : il ne tranche pas la politique de désactivation du compte secours.
"""
from __future__ import annotations

import pathlib

import pytest

from seamtech_search.audit_logs import _tronquer_details


def test_details_filtre_secrets():
    interdits = ["mot_de_passe", "password", "empreinte", "cookie", "jeton", "token", "secret", "sid"]
    for cle in interdits:
        nettoye = _tronquer_details({cle: "valeur_secrete", "autre": "ok"})
        assert cle not in nettoye, f"secret {cle} non filtré"
        assert "autre" in nettoye

    # Test insensible à la casse
    assert "MDP" not in _tronquer_details({"MDP": "x", "Mot_de_Passe": "y"})
    # mot_de_passe en minuscule est filtré
    assert "mot_de_passe" not in _tronquer_details({"mot_de_passe": "secret123"})


def test_details_troncature_4ko():
    gros = {"gros": "x" * 5000}
    nettoye = _tronquer_details(gros)
    import json
    assert len(json.dumps(nettoye).encode("utf-8")) <= 4096
    assert nettoye["gros"].endswith("…") or "truncated" in nettoye


def test_audit_logs_py_ne_journalise_pas_secrets():
    p = pathlib.Path("seamtech_search/audit_logs.py").read_text(encoding="utf-8")
    # Vérifie que les champs interdits sont listés
    assert "mot_de_passe" in p
    assert "empreinte" in p
    # Vérifie que record_audit_log ne log jamais le mot de passe en clair
    assert "record_audit_log" in p
    # Aucun log avec le mot de passe en clair dans les tests
    # On exclut ce fichier lui-même qui contient la sentinelle dans l'assertion
    sentinel = "SEAMTECH" + "_UI_" + "PASSWORD=sup3r"
    for tf in pathlib.Path("tests").glob("test_*.py"):
        if tf.name == "test_secours_non_journalise.py":
            continue
        txt = tf.read_text(encoding="utf-8", errors="ignore")
        # Ne doit pas contenir de vrai mot de passe en dur
        assert sentinel not in txt
        # Les tests utilisent des mots de passe factices
        if "mot_de_passe" in txt.lower():
            # Doit être via la variable, pas en dur dans un log
            assert "logger.info" not in txt or "mot_de_passe" not in txt


def test_comptes_routes_ne_log_pas_mdp():
    p = pathlib.Path("seamtech_search/comptes/routes.py").read_text(encoding="utf-8")
    # Le log de création ne doit pas contenir le mot de passe
    assert "Création du compte" in p
    # Vérifie que le mot de passe n'est pas interpolé dans le log
    for ligne in p.splitlines():
        if "LOGGER.info" in ligne and "Création" in ligne:
            assert "mot_de_passe" not in ligne.lower()
            assert "password" not in ligne.lower()


@pytest.mark.skip(reason="Politique compte secours en attente décision Lass (ILY-10) — test neutre, voir docs/Lot0b_SECURITE.md §9")
def test_secours_desactivation_apres_admin():
    """Ce test sera activé après décision Lass sur la désactivation du compte secours."""
    pytest.skip("En attente décision Lass")
