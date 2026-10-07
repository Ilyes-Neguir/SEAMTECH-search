"""Aides partagées par les tests de rebuild d'index (E-40)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ecrire_config(tmp_path: Path, racine_documents: Path, database_url: str | None = None) -> Path:
    """Écrit un fichier de configuration réel (celui que lit ``--config``)."""
    chemin = tmp_path / "config.json"
    donnees: dict[str, Any] = {
        "root_paths": [str(racine_documents)],
        "database_path": str(tmp_path / "search.db"),
        "rate_limit_per_minute": 1000,
    }
    if database_url:
        donnees["database_url"] = database_url
    chemin.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    return chemin


def peupler_dossiers(racine: Path) -> None:
    """Deux fichiers texte : l'indexation les extrait vraiment (contenu réel)."""
    (racine / "dossier-client").mkdir(parents=True, exist_ok=True)
    (racine / "dossier-client" / "fiche-A.txt").write_text(
        "Fiche A — tissu monofilm, grand voile, cote 6.60 m", encoding="utf-8"
    )
    (racine / "dossier-client" / "note-B.txt").write_text("Note B — génois renforcé, guindant 50 mm", encoding="utf-8")


def indexer(index: Any, config: Any, *, rebuild: bool = False) -> dict[str, Any]:
    from seamtech_search.cli import _run_index

    return _run_index(index, config, rebuild=rebuild)


def identifiants_documents(index: Any) -> dict[str, int]:
    with index.connect() as connexion:
        curseur = connexion.cursor()
        curseur.execute("SELECT path_key, id FROM documents ORDER BY path_key")
        return {str(ligne[0]): int(ligne[1]) for ligne in curseur.fetchall()}


def id_document(index: Any, suffixe: str) -> int:
    """Identifiant du document dont le chemin se termine par ``suffixe``."""
    for cle, identifiant in identifiants_documents(index).items():
        if cle.endswith(suffixe):
            return identifiant
    raise AssertionError(f"document introuvable : {suffixe} parmi {sorted(identifiants_documents(index))}")


def lien_piece_resolu(index: Any, id_fiche: int) -> list[tuple[str, str]]:
    """La jointure EXACTE du chemin de téléchargement, pour une fiche."""
    marque = "%s" if index.is_postgres else "?"
    with index.connect() as connexion:
        curseur = connexion.cursor()
        curseur.execute(
            "SELECT p.chemin, d.path FROM fiche_piece_jointe p "
            f"JOIN documents d ON d.id = p.id_document WHERE p.id_fiche = {marque}",
            (id_fiche,),
        )
        return [(str(ligne[0]), str(ligne[1])) for ligne in curseur.fetchall()]
