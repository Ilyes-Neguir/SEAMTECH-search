"""Drapeau runtime assistant/ML : présent par défaut, masquable sans les supprimer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from seamtech_search import api as api_module
from seamtech_search.config import AppConfig


def _app(tmp_path: Path):
    return api_module.create_app(
        AppConfig(root_paths=[tmp_path], database_path=tmp_path / "search.db", min_free_bytes=0)
    )


def test_assistant_et_routes_ml_visibles_par_defaut(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("SEAMTECH_OPTIONAL_FEATURES_ENABLED", raising=False)
    chemins = {route.path for route in _app(tmp_path).routes}
    assert "/assistant" in chemins
    assert "/ml/modeles" in chemins


def test_drapeau_desactive_masque_assistant_et_ml_sans_charger_le_modele(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("SEAMTECH_OPTIONAL_FEATURES_ENABLED", "false")

    def chargement_interdit(_config: AppConfig) -> None:
        raise AssertionError("le modèle optionnel ne doit pas être chargé lorsque le drapeau est désactivé")

    monkeypatch.setattr(api_module, "_charger_encodeur_ml", chargement_interdit)
    chemins = {route.path for route in _app(tmp_path).routes}
    assert "/assistant" not in chemins
    assert "/ml/modeles" not in chemins
    # Les autres fonctions de recherche restent disponibles.
    assert "/recherche" in chemins
