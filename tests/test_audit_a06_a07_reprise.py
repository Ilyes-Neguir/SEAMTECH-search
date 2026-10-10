"""Constats A06 (reprise de file / sélections) et A07 (inventaire de reprise).

A06 — un job accepté dont l'entrée Redis disparaît restait orphelin POUR TOUJOURS
==============================================================================

Défaut reproduit par la revue : ``reconcilier_file`` ne regardait que les jobs
``running``. Un job ``pending`` (accepté, en attente de worker) dont l'entrée de
file avait disparu — Redis redémarré sans persistance, entrée évincée — n'était
jamais ré-enfilé ni marqué en échec : il restait « en attente » indéfiniment, et
rien ne le signalait. Second volet du même défaut : la boucle lisait ``limite``
lignes triées par ``updated_at DESC``, donc au-delà de la première page les jobs
les plus anciens (précisément les orphelins) n'étaient JAMAIS examinés.

Troisième volet : les sélections manuelles (``selected_pdf``/``selected_excel``)
ne vivaient que dans la charge Redis ; après perte, la reprise repartait sur « le
premier PDF trouvé », produisant un import DIFFÉRENT sans aucun signal.

A07 — un renvoi partiel rapportait un succès total
=================================================

Défaut reproduit par la revue : ``retry_upload`` remplaçait ``artifacts`` par les
seuls fichiers renvoyés et recalculait ``all_verified`` sur ce sous-ensemble. Un
renvoi des rapports seuls — originaux toujours en échec — donnait
``upload_status=uploaded`` et ``all_verified=true`` : l'exploitant lisait « tout
est préservé » alors que les originaux n'étaient nulle part.

Les tests ci-dessous éprouvent le comportement CORRIGÉ. Le magasin d'objets est
le double EN MÉMOIRE du dépôt (``tests/s3_en_memoire.py``, étiqueté comme tel) :
il conserve les octets et recalcule l'empreinte, ce qui est nécessaire pour que
« vérifié » veuille dire quelque chose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex


def _config(tmp_path: Path, **extra: Any) -> AppConfig:
    base: dict[str, Any] = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "search.db",
        "min_free_bytes": 0,
        **extra,
    }
    return AppConfig(**base)


def _index(config: AppConfig) -> SearchIndex:
    index = SearchIndex(config.database_path, config.database_url)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


# --------------------------------------------------------------------------- #
# A06 — réconciliation de la file
# --------------------------------------------------------------------------- #


def test_a06_job_pending_orphelin_est_reconcilie_et_relance(tmp_path: Path) -> None:
    """Un ``pending`` sans entrée de file n'est plus « en attente pour toujours »."""
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    dossier = tmp_path / "affaire"
    dossier.mkdir()
    (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4")
    create_job(index, "job-pending-orphelin", str(dossier), status="pending", stage="queued")

    class _FileQuiARepondu:
        """Double de file : ``job_est_dans_file`` dit NON (entrée perdue), le
        ré-enfilage réussit. Étiqueté double — la file réelle est Redis (CI)."""

        def __init__(self) -> None:
            self.enfilees: list[dict[str, Any]] = []

        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return []

        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            return False

        def enqueue_task(self, queue: str, charge: dict[str, Any]) -> bool:
            self.enfilees.append(charge)
            return True

    file_ = _FileQuiARepondu()
    resume = reconcilier_file(index, file_)  # type: ignore[arg-type]

    assert "job-pending-orphelin" in resume["relanced_from_db"], resume
    assert resume["failed_from_db"] == [], resume
    assert file_.enfilees, "le job orphelin doit être RÉ-ENFILÉ, pas seulement signalé"
    assert file_.enfilees[0]["job_id"] == "job-pending-orphelin"
    assert file_.enfilees[0]["source_path"] == str(dossier)
    job = get_job(index, "job-pending-orphelin")
    assert job is not None and job["status"] == "pending", job
    assert "orph" in str(job.get("failure_reason") or "").lower() or job.get("attempts") is not None
    index.close()


def test_a06_redis_indisponible_ne_touche_a_rien_et_le_dit(tmp_path: Path) -> None:
    """Redis injoignable : aucun job arbitré à l'aveugle, et la liste est publiée."""
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-running-illisible", str(tmp_path / "x"), status="running", stage="extracting")

    class _FileMorte:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            raise RuntimeError("Redis injoignable")

        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            raise RuntimeError("Redis injoignable")

    resume = reconcilier_file(index, _FileMorte())  # type: ignore[arg-type]
    assert resume["redis_indisponible"] == ["job-running-illisible"], resume
    assert resume["failed_from_db"] == [] and resume["relanced_from_db"] == []
    job = get_job(index, "job-running-illisible")
    assert job is not None and job["status"] == "running", "un job peut être vivant ailleurs"


def test_a06_plus_de_la_premiere_page_de_jobs_est_examinee(tmp_path: Path) -> None:
    """Au-delà de la limite historique, les orphelins anciens sont ENFIN vus.

    La régression tenait à deux choses : seul ``running`` était regardé, ET la
    lecture s'arrêtait à la première page triée par ``updated_at DESC`` — donc
    aux jobs RÉCENTS. Ici : un orphelin ancien au milieu de 250 jobs récents.
    """
    from seamtech_search.jobs import create_job, get_job, jobs_actifs
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    dossier = tmp_path / "vieille-affaire"
    dossier.mkdir()
    (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4")
    create_job(index, "job-tres-ancien", str(dossier), status="pending", stage="queued")
    # 250 jobs récents > la limite de page (200) : l'ancien sort en DERNIER d'un
    # tri updated_at DESC, donc hors de la première page.
    for numero in range(250):
        create_job(index, f"job-recent-{numero:03d}", str(tmp_path), status="completed")

    assert len(jobs_actifs(index, limite=200)) == 1, "un seul job ACTIF ici : l'ancien"

    class _FileSelective:
        def __init__(self) -> None:
            self.demandes: list[str] = []

        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return []

        def job_est_dans_file(self, job_id: str, *args: object, **kwargs: object) -> bool:
            return job_id not in {"job-tres-ancien"}

        def enqueue_task(self, queue: str, charge: dict[str, Any]) -> bool:
            self.demandes.append(str(charge.get("job_id")))
            return True

    file_ = _FileSelective()
    resume = reconcilier_file(index, file_)  # type: ignore[arg-type]
    assert resume["relanced_from_db"] == ["job-tres-ancien"], resume
    assert file_.demandes == ["job-tres-ancien"]
    job = get_job(index, "job-tres-ancien")
    assert job is not None and job["status"] == "pending"
    index.close()


def test_a06_selection_manquante_refuse_la_relance_au_lieu_de_substituer(tmp_path: Path) -> None:
    """Une sélection persistée introuvable = échec EXPLICITE, jamais un autre document."""
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    dossier = tmp_path / "dossier"
    dossier.mkdir()
    (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4")
    autre = dossier / "autre.pdf"
    autre.write_bytes(b"%PDF-1.4 autre")
    disparu = dossier / "choisi-par-operateur.pdf"  # jamais écrit : disparu
    create_job(
        index,
        "job-selection-perdue",
        str(dossier),
        status="pending",
        stage="queued",
        selected_pdf=str(disparu),
    )

    class _FileVide:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return []

        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            return False

        def enqueue_task(self, queue: str, charge: dict[str, Any]) -> bool:  # pragma: no cover
            raise AssertionError("aucun ré-enfilage ne doit avoir lieu avec une sélection perdue")

    resume = reconcilier_file(index, _FileVide())  # type: ignore[arg-type]
    assert resume["relanced_from_db"] == [], "substitution interdite"
    assert resume["failed_from_db"] == ["job-selection-perdue"], resume
    job = get_job(index, "job-selection-perdue")
    assert job is not None
    assert "sélection" in str(job.get("failure_reason") or ""), job
    # Le fichier NON désigné n'a pas été promu en remplacement en silence.
    assert "autre.pdf" not in str(job.get("source_path") or "")
    index.close()


def test_a06_selection_conservee_est_relancee_telle_quelle(tmp_path: Path) -> None:
    """Une sélection persistée ET présent → la reprise ré-importe LE document choisi."""
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    dossier = tmp_path / "dossier"
    dossier.mkdir()
    choisi = dossier / "choisi.pdf"
    choisi.write_bytes(b"%PDF-1.4 choisi")
    create_job(
        index,
        "job-selection-presente",
        str(dossier),
        status="pending",
        stage="queued",
        selected_pdf=str(choisi),
    )

    class _FileQuiARepondu:
        def __init__(self) -> None:
            self.enfilees: list[dict[str, Any]] = []

        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return []

        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            return False

        def enqueue_task(self, queue: str, charge: dict[str, Any]) -> bool:
            self.enfilees.append(charge)
            return True

    file_ = _FileQuiARepondu()
    resume = reconcilier_file(index, file_)  # type: ignore[arg-type]
    assert resume["relanced_from_db"] == ["job-selection-presente"], resume
    assert file_.enfilees[0]["selected_pdf"] == str(choisi)
    job = get_job(index, "job-selection-presente")
    assert job is not None and job["status"] == "pending"
    index.close()


def test_a06_jobs_non_preserves_lit_le_resultat_quel_que_soit_son_encodage(tmp_path: Path) -> None:
    """SQLite (mode poste) : la protection doit lire le résultat sans se tromper.

    ``jobs_non_preserves`` décide de ce que la rétention a le DROIT de purger.
    Sur SQLite (mode poste), ``result`` peut revenir en TEXTE JSON ; une erreur
    de lecture ne doit pas se transformer en « préservé » — ni en « tout est
    protégé », ce qui reviendrait à ne plus jamais rien élaguer. Trois cas :
    résultat prouvé (exclu), résultat illisible (protégé, par prudence),
    résultat absent (protégé).
    """
    from seamtech_search.jobs import create_job, jobs_non_preserves

    config = _config(tmp_path)
    index = _index(config)
    for job_id in ("job-preuve", "job-illisible", "job-sans-resultat", "job-attente"):
        create_job(index, job_id, str(tmp_path / job_id), durability="durable")

    with index.connect() as connexion:
        connexion.execute(
            "UPDATE import_jobs SET status = 'completed', result = ? WHERE id = ?",
            ('{"all_verified": true, "upload_status": "uploaded"}', "job-preuve"),
        )
        connexion.execute(
            "UPDATE import_jobs SET status = 'completed', result = ? WHERE id = ?",
            ("{pas du json", "job-illisible"),
        )
        connexion.commit()

    trouves = {job["id"] for job in jobs_non_preserves(index, limite=50)}
    assert "job-preuve" not in trouves, "une préservation PROUVÉE n'a rien à protéger"
    assert "job-illisible" in trouves, "un résultat illisible doit être protégé, jamais élagué"
    assert "job-sans-resultat" in trouves and "job-attente" in trouves
    index.close()


# --------------------------------------------------------------------------- #
# A07 — inventaire de reprise et preuve de préservation
# --------------------------------------------------------------------------- #


def _importer_avec_stockage(tmp_path: Path, *, config: AppConfig) -> tuple[Any, "Any"]:
    """Import RÉEL des fichiers d'un dossier, avec le magasin en mémoire.

    On passe par ``import_folder`` (le vrai moteur) pour que ``payload["files"]``
    et les clés d'objet soient ceux que le produit écrit — un payload fabriqué à
    la main ne prouverait rien du comportement de reprise.
    """
    from seamtech_search.import_pipeline import import_folder
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    dossier = tmp_path / "dossier-source"
    dossier.mkdir()
    (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche technique 6,60 m")
    (dossier / "notes.txt").write_text("note de dossier")

    magasin = S3EnMemoire()
    index = _index(config)
    with (
        patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
    ):
        resultat = import_folder(source=dossier, config=config, index=index, import_id="IMP-A07")
    return index, (resultat, magasin, config)


def _config_s3(tmp_path: Path) -> AppConfig:
    return _config(
        tmp_path,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="seamtech-documents",
        s3_access_key="cle-fictive",
        s3_secret_key="secret-fictif",
    )


def test_a07_renvoi_apres_perte_d_un_original_n_est_pas_un_succes_total(tmp_path: Path) -> None:
    """Le constat A07, reproduit tel quel : une PIÈCE perdue, rapports renvoyés.

    Ancien code : la pièce (``notes.txt``) avait échoué ET son fichier local
    avait disparu — deux conditions qui la faisaient SORTIR de la liste à
    renvoyer sans un mot. Le lot ne contenait plus que les rapports, envoyés avec
    succès, et ``all_verified`` était recalculé SUR CE SOUS-ENSEMBLE :
    ``uploaded`` + ``true``. L'exploitant lisait « tout est préservé » alors que
    la pièce du dossier n'existait plus nulle part — et la purge locale était
    autorisée sur cette base.

    Comportement corrigé : l'inventaire COMPLET fait foi. La pièce manquante est
    un échec NOMMÉ, le statut global reste ``partial``, et la purge est refusée.
    """
    from seamtech_search.import_pipeline import get_import, retry_upload, update_import
    from seamtech_search.storage import S3StorageClient, upload_artifacts_to_storage

    config = _config_s3(tmp_path)
    index, (_resultat, magasin_import, _cfg) = _importer_avec_stockage(tmp_path, config=config)
    payload = get_import(index, "IMP-A07")
    assert payload is not None
    assert len(payload["files"]) >= 2, "le dossier de test doit porter au moins deux fichiers"
    assert payload["all_verified"] is True, "le préalable : l'import initial a tout envoyé et vérifié"

    # Les rapports sont DÉCLARÉS dans le payload exactement comme le produit le
    # fait (``report_path`` / ``report_docx_path``). Le PDF de test étant
    # minimal, la génération n'en produit pas : on les matérialise ici et on les
    # déclare — le scénario A07 éprouve l'INVENTAIRE de reprise (rapports +
    # originaux), pas la génération du rapport (couverte par
    # tests/test_import_workflow.py).
    rapports = [
        Path(payload["source_path"]) / "rapport-pdf.pdf",
        Path(payload["source_path"]) / "rapport.docx",
    ]
    rapports[0].write_bytes(b"%PDF-1.4 rapport")
    rapports[1].write_bytes(b"docx rapport")
    payload["report_path"] = str(rapports[0])
    payload["report_docx_path"] = str(rapports[1])
    with (
        patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin_import),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
    ):
        lot = upload_artifacts_to_storage(
            "dossier", rapports, config, import_id="IMP-A07", source_root=Path(payload["source_path"])
        )
    assert lot.all_verified, "le préalable du test : les rapports sont prouvés"

    # LA PIÈCE échoue ET son fichier local disparaît : les deux conditions du
    # défaut (l'ancien code la faisait alors sortir de son compte rendu).
    piece = next(Path(entree["path"]) for entree in payload["files"] if Path(entree["path"]).suffix == ".txt")
    for entree in payload["files"]:
        if str(entree["path"]) == str(piece):
            entree["upload_status"] = "failed"
            entree["object_key"] = None
            entree["verified"] = False
    piece.unlink()
    payload["upload_status"] = "partial"
    payload["all_verified"] = False
    update_import(index, "IMP-A07", "needs_review", payload)

    with (
        patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin_import),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
    ):
        apres = retry_upload(index, config, "IMP-A07")

    # 1. JAMAIS de succès total : la pièce n'est pas préservée.
    assert apres["all_verified"] is False, apres["preservation"]
    assert apres["upload_status"] == "partial", apres["preservation"]
    assert apres["cleanup"]["purge"] is False, apres["cleanup"]

    # 2. La pièce perdue est NOMMÉE — elle ne sort pas de l'inventaire.
    assert str(piece) in apres["inventaire"]["manquants_localement"], apres["inventaire"]
    assert str(piece) in apres["preservation"]["artefacts_non_verifies"], apres["preservation"]
    entree_piece = next(e for e in apres["artifacts"] if e["path"] == str(piece))
    assert entree_piece["status"] == "failed", entree_piece
    assert "absent localement" in entree_piece["error"], entree_piece
    # …et l'entrée de fichier publiée au client dit la même chose.
    fichier_publie = next(e for e in apres["files"] if e["path"] == str(piece))
    assert fichier_publie["upload_status"] == "failed", fichier_publie

    # 3. L'inventaire couvre l'ENSEMBLE (fichiers du dossier + rapports déclarés).
    assert apres["inventaire"]["total"] == len(payload["files"]) + len(rapports), apres["inventaire"]

    # 4. Les preuves acquises ne sont pas réécrites par ce renvoi.
    for chemin in [Path(entree["path"]) for entree in payload["files"] if str(entree["path"]) != str(piece)]:
        entree = next(e for e in apres["artifacts"] if e["path"] == str(chemin))
        assert entree["status"] == "uploaded" and entree["verified"] is True, entree
        assert entree["key"] in magasin_import.objets, entree

    # 5. Ce que l'opérateur relit en base dit exactement la même chose.
    relu = get_import(index, "IMP-A07")
    assert relu is not None
    assert relu["all_verified"] is False and relu["upload_status"] == "partial", relu["preservation"]
    index.close()


def test_a07_rapport_uploaded_sans_cle_est_ramene_a_pending(tmp_path: Path) -> None:
    """« uploaded » sans clé d'objet n'est PAS une préservation (défaut historique)."""
    from seamtech_search.import_pipeline import construire_manifeste

    manifeste = construire_manifeste(
        {
            "files": [
                {
                    "path": "/d/f.pdf",
                    "name": "f.pdf",
                    "upload_status": "uploaded",  # drapeau global recopié…
                    "object_key": None,  # …mais AUCUNE preuve
                }
            ],
            "report_path": "/d/rapport.pdf",
            "artifacts": [],
        }
    )
    entree = next(e for e in manifeste if e["path"] == "/d/f.pdf")
    assert entree["status"] == "pending", entree
    assert entree["verified"] is False
    assert "clé d'objet absente" in entree["error"]


def test_a07_inventaire_vide_ou_sans_destination(tmp_path: Path) -> None:
    """Agrégat : vide → ``not_applicable`` ; sans destination → ``not_configured``."""
    from seamtech_search.import_pipeline import agreger_preservation

    assert agreger_preservation([], destination_configuree=True) == ("not_applicable", False)
    manifeste = [
        {"path": "/a", "status": "uploaded", "verified": True, "key": "k"},
    ]
    assert agreger_preservation(manifeste, destination_configuree=False) == ("not_configured", False)
    assert agreger_preservation(manifeste, destination_configuree=True) == ("uploaded", True)
    incomplet = manifeste + [{"path": "/b", "status": "failed", "verified": False, "key": None}]
    assert agreger_preservation(incomplet, destination_configuree=True) == ("partial", False)


def test_a07_renvoi_complet_autorise_la_purge_seulement_avec_accord(tmp_path: Path) -> None:
    """Inventaire entier vérifié : purge possible — mais seulement avec l'accord explicite."""
    from seamtech_search.import_pipeline import retry_upload
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    for accord in (False, True):
        base = tmp_path / f"accord-{accord}"
        base.mkdir()
        config = AppConfig(
            root_paths=[base],
            database_path=base / "search.db",
            min_free_bytes=0,
            s3_endpoint_url="https://s3.invalide",
            s3_bucket="seamtech-documents",
            s3_access_key="cle-fictive",
            s3_secret_key="secret-fictif",
            delete_local_after_upload=accord,
        )
        index, (_resultat, _magasin, _cfg) = _importer_avec_stockage(base, config=config)
        magasin = S3EnMemoire()
        with (
            patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
        ):
            apres = retry_upload(index, config, "IMP-A07")
        assert apres["all_verified"] is True, apres["preservation"]
        assert apres["inventaire"]["verifies"] == apres["inventaire"]["total"]
        assert apres["cleanup"]["purge"] is accord, apres["cleanup"]
        if not accord:
            assert "SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD" in apres["cleanup"]["raison"]
        index.close()


def test_a07_fichier_local_absent_est_un_echec_explicite(tmp_path: Path) -> None:
    """Un original disparu du disque : échec NOMMÉ, jamais « préservé » par omission."""
    from seamtech_search.import_pipeline import get_import, retry_upload, update_import
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    config = _config_s3(tmp_path)
    index, (resultat, _magasin, _cfg) = _importer_avec_stockage(tmp_path, config=config)
    payload = get_import(index, "IMP-A07")
    assert payload is not None

    # Aucune preuve antérieure + un fichier source disparu du disque.
    payload["artifacts"] = []
    for entree in payload["files"]:
        entree["upload_status"] = "failed"
        entree["object_key"] = None
    disparu = Path(payload["files"][0]["path"])
    disparu.unlink()
    update_import(index, "IMP-A07", "needs_review", payload)

    magasin = S3EnMemoire()
    with (
        patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
    ):
        apres = retry_upload(index, config, "IMP-A07")

    assert str(disparu) in apres["inventaire"]["manquants_localement"], apres["inventaire"]
    entree = next(e for e in apres["artifacts"] if e["path"] == str(disparu))
    assert entree["status"] == "failed", entree
    assert "absent localement" in entree["error"]
    assert apres["all_verified"] is False
    assert apres["cleanup"]["purge"] is False
    index.close()


def test_a07_preuves_anterieures_ne_sont_jamais_reecrites(tmp_path: Path) -> None:
    """Un renvoi partiel ne réécrit pas le passé : les preuves acquises restent."""
    from seamtech_search.import_pipeline import construire_manifeste

    manifeste = construire_manifeste(
        {
            "files": [
                {
                    "path": "/d/prouve.pdf",
                    "name": "prouve.pdf",
                    "upload_status": "uploaded",
                    "object_key": "objets/prouve.pdf",
                },
                {"path": "/d/neuf.pdf", "name": "neuf.pdf", "upload_status": "pending", "object_key": None},
            ],
            "artifacts": [
                {
                    "path": "/d/prouve.pdf",
                    "key": "objets/prouve.pdf",
                    "bucket": "seamtech-documents",
                    "status": "uploaded",
                    "verified": True,
                    "verification": "empreinte_sha256",
                    "uploaded_at": 111.0,
                }
            ],
        }
    )
    prouve = next(e for e in manifeste if e["path"] == "/d/prouve.pdf")
    assert prouve["status"] == "uploaded" and prouve["verified"] is True
    assert prouve["verification"] == "empreinte_sha256", prouve
    assert prouve["uploaded_at"] == 111.0, "l'horodatage de la preuve doit survivre"
    neuf = next(e for e in manifeste if e["path"] == "/d/neuf.pdf")
    assert neuf["status"] == "pending" and neuf["verified"] is False


def test_r3_quarantaine_reprise_propage_nouveaux_chemins_et_selections(tmp_path: Path) -> None:
    """R3 : Après mise en quarantaine, la charge de retry et les sélections sont cohérentes et réessayables."""
    import json
    from unittest.mock import patch

    import redislite

    from seamtech_search import worker
    from seamtech_search.config import AppConfig
    from seamtech_search.import_pipeline import staging_root
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.redis_store import RedisStore
    from seamtech_search.storage import S3StorageClient, StorageError, UploadBatch, UploadedArtifact

    r_inst = redislite.Redis(str(tmp_path / "r3_test.rdb"))
    store = RedisStore(redis_url=f"unix://{r_inst.socket_file}")
    client = store._get_client()
    client.flushdb()

    root = tmp_path / "env"
    root.mkdir()
    cfg = AppConfig(root_paths=[root], database_path=root / "data/index.db", min_free_bytes=0,
                    s3_endpoint_url="http://127.0.0.1:1", s3_access_key="audit", s3_secret_key="audit")
    idx = SearchIndex(cfg.database_path)
    idx.initialize()
    idx.run_migrations()

    source = staging_root(cfg) / "upload"
    source.mkdir(parents=True)
    pdf = source / "selected.pdf"
    pdf.write_bytes(Path("sample_data/CLIENT-123/fiche-technique.pdf").read_bytes())

    create_job(idx, "job-r3", str(source), selected_pdf=str(pdf))
    store.enqueue_task("imports", {"job_id": "job-r3", "source_path": str(source), "selected_pdf": str(pdf)})

    # Tentative 1 avec panne S3 -> quarantaine
    with patch.object(S3StorageClient, "_get_client", side_effect=StorageError("panne S3 injectée")):
        worker.worker_loop(cfg, idx, store, worker_id="worker-A", run_once=True)

    job = get_job(idx, "job-r3")
    assert job is not None
    pending = client.zrange("seamtech:retry:imports", 0, -1)
    assert len(pending) == 1
    retry_payload = json.loads(pending[0])

    # Invariants R3 :
    # 1. Le chemin source en base existe et est en quarantaine
    assert Path(job["source_path"]).exists()
    assert "quarantine" in job["source_path"]
    # 2. La charge de retry Redis porte le NOUVEAU chemin en quarantaine, pas l'ancien périmé
    assert Path(retry_payload["source_path"]).exists()
    assert retry_payload["source_path"] == job["source_path"]
    # 3. La sélection PDF en base et dans le payload pointe vers le fichier relocalisé
    assert Path(job["selected_pdf"]).exists()
    assert Path(retry_payload["selected_pdf"]).exists()

    # 4. Reconstruction depuis la base
    reconstruction = worker._charge_depuis_job(job)
    assert reconstruction is not None
    assert Path(reconstruction["source_path"]).exists()
    assert Path(reconstruction["selected_pdf"]).exists()

    # 5. Seconde tentative effective réussie
    def fake_upload(folder_name, files_to_upload, config, import_id=None, source_root=None):
        arts = [
            UploadedArtifact(path=str(f), name=Path(f).name, key=f"key_{Path(f).name}", bucket="b", status="uploaded", verified=True)
            for f in files_to_upload if f is not None
        ]
        return UploadBatch(status="uploaded", artifacts=arts)

    client.zrem("seamtech:retry:imports", pending[0])
    store.enqueue_task("imports", retry_payload)
    with patch("seamtech_search.import_pipeline.upload_artifacts_to_storage", fake_upload):
        worker.worker_loop(cfg, idx, store, worker_id="worker-A", run_once=True)

    job_final = get_job(idx, "job-r3")
    assert job_final is not None
    assert job_final["status"] == "completed"
    idx.close()
