"""Rétention vs travail actif — investigation complémentaire (audit 2026-10-08).

Constat de revue de code : ``run_retention_cleanup`` élaguait les copies locales
par ÂGE SEUL (``st_mtime``), sans jamais consulter l'état des imports. Or :

* un import ``pending``/``running`` a sa source locale pour seule entrée — la
  supprimer condamne le travail, silencieusement ;
* un import dont la copie durable n'est PAS vérifiée (``not_configured``,
  ``partial``, ``upload_incomplete``…) a sa copie locale pour SEULE copie — la
  supprimer détruit le document ;
* les rapports font partie de l'inventaire de reprise (constat A07) : les
  élaguer fait échouer la reprise sur un fichier manquant.

Correctif éprouvé ici : l'élagage consulte le registre des jobs, protège les
chemins nécessaires, et — s'il ne peut PAS établir cette liste — n'élague RIEN
du tout (échec fermé : on ne supprime jamais ce qu'on n'a pas pu examiner).

Les doubles utilisés sont étiquetés : l'index est un SQLite jetable (couche
métier non requise ici), et l'échec de lecture est simulé par un index dont la
connexion refuse la requête.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, update_job
from seamtech_search.retention import (
    chemins_proteges,
    prune_reports,
    prune_staged_uploads,
    run_retention_cleanup,
)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "data" / "search.db",
        min_free_bytes=0,
        reports_retention_days=1,
        staged_retention_days=1,
        audit_retention_days=1,
    )


def _index(config: AppConfig, tmp_path: Path) -> SearchIndex:
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


def _vieillir(chemin: Path, jours: int = 3) -> None:
    """Rend un chemin plus vieux que la rétention — le geste de l'horloge."""
    ancien = time.time() - jours * 86400
    os.utime(chemin, (ancien, ancien))
    if chemin.is_dir():
        for enfant in chemin.rglob("*"):
            os.utime(enfant, (ancien, ancien))


def _dossier_stage(config: AppConfig, nom: str, contenu: bytes = b"%PDF-1.4") -> Path:
    from seamtech_search.import_pipeline import staging_root

    racine = staging_root(config)
    dossier = racine / nom
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "fiche.pdf").write_bytes(contenu)
    return dossier


def test_retention_conserve_l_entree_d_un_import_en_attente(tmp_path: Path) -> None:
    """Un import ``pending`` de 3 jours garde sa source : ce n'est pas un détritus."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    dossier = _dossier_stage(config, "uuid_en_attente_affaire")
    _vieillir(dossier)
    create_job(index, "job-en-attente", str(dossier), status="pending", stage="queued")

    resume = run_retention_cleanup(config, index)
    assert dossier.exists(), "la source d'un travail en attente a été supprimée"
    assert (dossier / "fiche.pdf").exists()
    assert resume["pruned_staged_uploads"] == 0, resume
    assert resume["jobs_proteges"] >= 1, resume
    assert resume["protection_indisponible"] is None, resume
    index.close()


def test_retention_conserve_la_seule_copie_d_un_import_non_preserve(tmp_path: Path) -> None:
    """Sans copie durable vérifiée, la copie locale EST le document : intouchable."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    dossier = _dossier_stage(config, "uuid_not_configured_affaire")
    _vieillir(dossier)
    create_job(index, "job-not-configured", str(dossier), status="needs_review", stage="done")
    update_job(
        index,
        "job-not-configured",
        result={
            "status": "needs_review",
            "source_path": str(dossier),
            "upload_status": "not_configured",
            "all_verified": False,
            "files": [{"path": str(dossier / "fiche.pdf"), "upload_status": "not_configured"}],
        },
    )

    resume = run_retention_cleanup(config, index)
    assert dossier.exists(), "la seule copie d'un import non préservé a été supprimée"
    assert resume["pruned_staged_uploads"] == 0, resume
    index.close()


def test_retention_conserve_le_rapport_d_un_import_non_preserve(tmp_path: Path) -> None:
    """Les rapports font partie de l'inventaire de reprise : ils survivent aussi."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    dossier = _dossier_stage(config, "uuid_rapport_affaire")
    rapport = config.database_path.parent / "reports" / "job-rapport" / "rapport.pdf"
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_bytes(b"%PDF-1.4 rapport")
    _vieillir(rapport.parent)
    create_job(index, "job-rapport", str(dossier), status="needs_review", stage="done")
    update_job(
        index,
        "job-rapport",
        result={
            "source_path": str(dossier),
            "upload_status": "partial",
            "all_verified": False,
            "report_path": str(rapport),
            "files": [],
        },
    )

    resume = run_retention_cleanup(config, index)
    assert rapport.exists(), "le rapport nécessaire à la reprise a été élagué"
    assert resume["pruned_reports"] == 0, resume
    index.close()


def test_retention_elague_ce_qui_n_est_plus_reference(tmp_path: Path) -> None:
    """La protection n'empêche pas le rangement : l'inutile part, et c'est dit."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    orphelin = _dossier_stage(config, "uuid_abandonne")
    _vieillir(orphelin)
    vieux_rapport = config.database_path.parent / "reports" / "vieux" / "r.pdf"
    vieux_rapport.parent.mkdir(parents=True, exist_ok=True)
    vieux_rapport.write_bytes(b"x")
    _vieillir(vieux_rapport.parent)

    resume = run_retention_cleanup(config, index)
    assert not orphelin.exists(), "un dossier non référencé et hors délai doit être élagué"
    assert not vieux_rapport.exists()
    assert resume["pruned_staged_uploads"] == 1, resume
    assert resume["pruned_reports"] == 1, resume
    index.close()


def test_retention_echec_ferme_ne_supprime_rien(tmp_path: Path) -> None:
    """Liste de protection indisponible ⇒ AUCUN élagage (et l'échec est publié)."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    dossier = _dossier_stage(config, "uuid_inconnu")
    _vieillir(dossier)

    class _IndexIllisible:
        """Double étiqueté : index dont la connexion refuse la lecture des jobs."""

        class _Connexion:
            def cursor(self) -> Any:  # pragma: no cover - jamais atteint
                raise RuntimeError("base injoignable")

            def execute(self, *args: object, **kwargs: object) -> Any:
                raise RuntimeError("base injoignable")

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def connect(self) -> Any:
            return self._Connexion()

    resume = run_retention_cleanup(config, _IndexIllisible())  # type: ignore[arg-type]
    assert dossier.exists(), "échec fermé violé : un dossier a été supprimé sans arbitrage"
    assert resume["pruned_staged_uploads"] == 0, resume
    assert resume["pruned_reports"] == 0, resume
    assert resume["protection_indisponible"], resume
    index.close()


def test_chemins_proteges_couvre_les_chemins_persistes(tmp_path: Path) -> None:
    """L'inventaire protégé couvre source, fichiers, PDF technique et rapports."""
    config = _config(tmp_path)
    index = _index(config, tmp_path)
    dossier = _dossier_stage(config, "uuid_complet")
    rapport = config.database_path.parent / "reports" / "job-complet" / "r.pdf"
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_bytes(b"x")
    create_job(index, "job-complet", str(dossier), status="running", stage="extracting")
    update_job(
        index,
        "job-complet",
        result={
            "source_path": str(dossier),
            "all_verified": False,
            "technical_pdf": str(dossier / "fiche.pdf"),
            "report_path": str(rapport),
            "files": [{"path": str(dossier / "fiche.pdf")}],
        },
    )

    proteges, nb_jobs, echec = chemins_proteges(index)
    assert echec is None
    assert nb_jobs >= 1
    attendus = {dossier, dossier / "fiche.pdf", rapport}
    manquants = [chemin for chemin in attendus if chemin.resolve() not in {p.resolve() for p in proteges}]
    assert not manquants, f"chemins non protégés : {manquants}"
    index.close()


def test_prune_staged_uploads_sans_liste_est_le_comportement_historique(tmp_path: Path) -> None:
    """Sans registre de jobs, l'élagage par âge reste possible (appel explicite).

    Cette fonction est publique et les appelants sans base (scripts, outils)
    gardent le comportement d'origine — mais tout appelant qui CONNAÎT les jobs
    doit fournir ``proteges`` (c'est ce que fait ``run_retention_cleanup``).
    """
    config = _config(tmp_path)
    dossier = _dossier_stage(config, "uuid_sans_registre")
    _vieillir(dossier)
    from seamtech_search.import_pipeline import staging_root

    assert prune_staged_uploads(staging_root(config), 1) == 1
    assert not dossier.exists()


def test_prune_staged_uploads_ne_supprime_pas_un_parent_d_un_chemin_protege(tmp_path: Path) -> None:
    """Protéger un fichier protège aussi son dossier : pas de demi-suppression."""
    config = _config(tmp_path)
    from seamtech_search.import_pipeline import staging_root

    dossier = _dossier_stage(config, "uuid_parent")
    fichier = dossier / "fiche.pdf"
    _vieillir(dossier)
    assert prune_staged_uploads(staging_root(config), 1, proteges={fichier}) == 0
    assert fichier.exists()
    assert prune_reports(config.database_path.parent, 1, proteges={fichier}) == 0
