"""End-to-end coverage for the two-phase import workflow.

Builds real PDFs with reportlab so pdfplumber extraction, anchor
classification, unit normalization, reports, correction and upload retry are
all exercised against actual files — not mocks.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search import anchors as shared_anchors
from seamtech_search import crawler, import_pipeline
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.extractors import ExtractionResult
from seamtech_search.import_pipeline import (
    extract_structured_pdf,
    import_folder,
    scan_folder,
)
from seamtech_search.indexer import SearchIndex

TECHNICAL_LINES = [
    "FICHE DE FABRICATION",
    "Reference: SO-1234",
    "Material: Dacron",
    "Dimensions: 1200 x 800 mm",
    "Quantity: 5",
    "Description: Main sail",
]

TECHNICAL_LINES_2 = [
    "Fiche de fabrication",
    "Référence: GV-999",
    "Matière: Laminate",
    "Longueur: 2 x 1.5 m",
    "Quantité: 3",
    "Description: Genoa",
]

PLAN_LINES = ["Yard layout drawing", "Scale 1:50", "North elevation sketch"]


def make_pdf(path: Path, lines: list[str]) -> Path:
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path))
    y = 750
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 20
    pdf.save()
    return path


def make_pdf_bytes(lines: list[str]) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 750
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 20
    pdf.save()
    return buffer.getvalue()


def make_config(tmp_path: Path, root: Path) -> AppConfig:
    return AppConfig(root_paths=[root], database_path=tmp_path / "data" / "search.db")


def make_source(tmp_path: Path, name: str = "REF-001") -> Path:
    source = tmp_path / "roots" / name
    make_pdf(source / "a-technical.pdf", TECHNICAL_LINES)
    make_pdf(source / "b-technical.pdf", TECHNICAL_LINES_2)
    make_pdf(source / "c-plan.pdf", PLAN_LINES)
    (source / "notes.txt").write_text("some notes", encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# Shared anchors & classification
# ---------------------------------------------------------------------------


def test_anchors_are_shared_between_crawler_and_pipeline() -> None:
    assert crawler.TECHNICAL_PDF_ANCHORS == shared_anchors.TECHNICAL_ANCHORS
    assert import_pipeline.TECHNICAL_ANCHORS == shared_anchors.TECHNICAL_ANCHORS
    for anchor in ("reference", "référence", "longueur", "matériau"):
        assert anchor in shared_anchors.TECHNICAL_ANCHORS


def test_new_anchors_classify_technical_pdf() -> None:
    # Stricter classifier: 2 weak anchors alone is not enough, need 3 weak or strong+weak
    weak_two = "Reference: X-1\nLongueur: 1200 mm\nSome other words"
    assert shared_anchors.classify_pdf_text(weak_two) == "plan_pdf"

    weak_three = "Reference: X-1\nLongueur: 1200 mm\nLargeur: 800 mm"
    assert shared_anchors.classify_pdf_text(weak_three) == "technical_pdf"

    strong_plus_one = "Fiche de fabrication\nReference: X-1"
    assert shared_anchors.classify_pdf_text(strong_plus_one) == "technical_pdf"
    assert crawler.classify_pdf_text(strong_plus_one) == "technical_pdf" if hasattr(
        crawler, "classify_pdf_text"
    ) else True
    assert import_pipeline.classify_pdf_text(strong_plus_one) == "technical_pdf"

    # Original 3-anchor case still technical
    assert shared_anchors.classify_pdf_text(weak_three) == "technical_pdf"


def test_single_anchor_is_still_a_plan() -> None:
    assert shared_anchors.classify_pdf_text("Reference: lonely mention") == "plan_pdf"


# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------


def _extract_with_text(monkeypatch, text: str) -> None:
    monkeypatch.setattr(
        "seamtech_search.import_pipeline.extract_file",
        lambda *args, **kwargs: ExtractionResult(text, "extracted"),
    )


def test_dimensions_normalized_to_mm(monkeypatch, tmp_path: Path) -> None:
    _extract_with_text(monkeypatch, "Reference: R\nDimensions: 2 x 1.5 m")
    result = extract_structured_pdf(tmp_path / "drawing.pdf", AppConfig(root_paths=[tmp_path]))
    assert result.dimensions.length == 2
    assert result.dimensions.unit == "m"
    assert result.dimensions.length_mm == 2000
    assert result.dimensions.width_mm == 1500
    assert result.dimensions.unit_normalized == "mm"


def test_dimensions_cm_and_labeled_fallback(monkeypatch, tmp_path: Path) -> None:
    _extract_with_text(monkeypatch, "Reference: R\nLongueur: 120 cm\nLargeur: 80 cm")
    result = extract_structured_pdf(tmp_path / "drawing.pdf", AppConfig(root_paths=[tmp_path]))
    assert result.dimensions.length == 120
    assert result.dimensions.length_mm == 1200
    assert result.dimensions.width_mm == 800


# ---------------------------------------------------------------------------
# Scan + confirm flow with real PDFs
# ---------------------------------------------------------------------------


def test_scan_returns_multiple_candidates(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)

    result = scan_folder(source, config)

    assert result["files_detected"] == 4
    # Returns ALL PDFs with anchor score as ranking hint (4.8), not just technical
    assert len(result["candidates"]) == 3
    technical = [c for c in result["candidates"] if c.get("is_technical")]
    assert len(technical) == 2
    names = {c["name"] for c in technical}
    assert names == {"a-technical.pdf", "b-technical.pdf"}
    assert all(c["anchor_count"] >= 2 for c in technical)
    assert any("fiche de fabrication" in c["anchors_matched"] for c in technical)
    plan = [c for c in result["candidates"] if not c.get("is_technical")]
    assert len(plan) == 1
    assert plan[0]["name"] == "c-plan.pdf"
    assert any("select" in warning for warning in result["warnings"])


def test_import_oneshot_selects_first_and_lists_candidates(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source, config, index)

    assert result.technical_pdf and result.technical_pdf.endswith("a-technical.pdf")
    assert len(result.candidates) == 2
    assert any("first PDF was selected" in warning for warning in result.warnings)
    assert result.data and result.data["reference"] == "SO-1234"
    assert result.data["dimensions"]["length_mm"] == 1200
    assert result.report_path and Path(result.report_path).exists()
    assert result.report_docx_path and result.report_docx_path.endswith(".docx")
    assert Path(result.report_docx_path).exists()
    assert result.upload_status in ("not_configured", "pending", "not_applicable")


def test_import_confirm_uses_selected_pdf(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source, config, index, selected_pdf=source / "b-technical.pdf")

    assert result.technical_pdf and result.technical_pdf.endswith("b-technical.pdf")
    assert result.data and result.data["reference"] == "GV-999"
    assert result.data["dimensions"]["length_mm"] == 2000  # 2 m normalized
    assert result.status in {"completed", "needs_review"}


def test_import_rejects_selected_pdf_outside_folder(tmp_path: Path) -> None:
    import pytest

    root = tmp_path / "roots"
    source = make_source(tmp_path)
    outside = make_pdf(tmp_path / "elsewhere.pdf", TECHNICAL_LINES)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    with pytest.raises(ValueError, match="outside the scanned folder"):
        import_folder(source, config, index, selected_pdf=outside)


def test_docx_report_is_readable(tmp_path: Path) -> None:
    from docx import Document as DocxDocument

    root = tmp_path / "roots"
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source, config, index, selected_pdf=source / "a-technical.pdf")

    assert result.report_docx_path
    doc = DocxDocument(result.report_docx_path)
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "SEAMTECH Technical Report" in text
    assert any("SO-1234" in cell.text for table in doc.tables for row in table.rows for cell in row.cells)


# ---------------------------------------------------------------------------
# API: scan / confirm / correct / retry
# ---------------------------------------------------------------------------


def _client(tmp_path: Path, root: Path) -> TestClient:
    config = make_config(tmp_path, root)
    return TestClient(create_app(config))


def test_api_scan_and_confirm(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    client = _client(tmp_path, root)

    scan = client.post("/imports/scan", json={"source_path": str(source)})
    assert scan.status_code == 200
    # Now returns ALL PDFs with ranking hint
    assert len(scan.json()["candidates"]) == 3
    technical = [c for c in scan.json()["candidates"] if c.get("is_technical")]
    assert len(technical) == 2

    chosen = technical[1]["path"]
    confirm = client.post("/imports/confirm", json={"source_path": str(source), "technical_pdf": chosen})
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["technical_pdf"] == chosen
    assert body["report_docx_path"]

    fetched = client.get(f"/imports/{body['import_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["technical_pdf"] == chosen


def test_api_correction_regenerates_reports(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post("/imports?wait=true", json={"source_path": str(source)}).json()
    import_id = created["import_id"]

    response = client.patch(
        f"/imports/{import_id}",
        json={"reference": "CORRECTED-1", "dimensions": {"length": 99, "unit": "cm"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["reference"] == "CORRECTED-1"
    assert body["data"]["dimensions"]["length"] == 99
    assert body["data"]["dimensions"]["length_mm"] == 990
    assert Path(body["report_path"]).exists()
    assert Path(body["report_docx_path"]).exists()

    fetched = client.get(f"/imports/{import_id}").json()
    assert fetched["data"]["reference"] == "CORRECTED-1"


def test_api_correction_rejects_unknown_fields(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post("/imports?wait=true", json={"source_path": str(source)}).json()

    response = client.patch(f"/imports/{created['import_id']}", json={"quantity": "many"})
    assert response.status_code == 422


def test_api_correction_missing_import_returns_404(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    root.mkdir(parents=True)
    client = _client(tmp_path, root)
    assert client.patch("/imports/does-not-exist", json={"reference": "X"}).status_code == 404


def test_api_retry_upload_without_credentials(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post("/imports?wait=true", json={"source_path": str(source)}).json()

    response = client.post(f"/imports/{created['import_id']}/retry-upload")
    assert response.status_code == 200
    assert response.json()["upload_status"] in ("not_configured", "pending", "not_applicable", "uploaded")


def test_correct_import_rejects_unknown_fields_directly(tmp_path: Path) -> None:
    import pytest

    from seamtech_search.import_pipeline import correct_import, get_import

    root = tmp_path / "roots"
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    result = import_folder(source, config, index)

    with pytest.raises(ValueError, match="Unknown correction fields"):
        correct_import(index, config, result.import_id, {"nope": "x"})
    assert get_import(index, result.import_id) is not None


def _touch(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Browser upload staging
# ---------------------------------------------------------------------------


def test_api_upload_stages_files_and_scans(tmp_path: Path) -> None:
    root = tmp_path / "roots"
    root.mkdir(parents=True)
    client = _client(tmp_path, root)

    response = client.post(
        "/imports/upload",
        files=[("files", ("REF-DND/sheet.pdf", make_pdf_bytes(TECHNICAL_LINES), "application/pdf"))],
        data={"folder": "dnd-drop"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["staged_path"]
    assert len(body["candidates"]) == 1
    assert Path(body["staged_path"]).exists()

    # The staged folder is a valid confirm source.
    confirm = client.post(
        "/imports/confirm",
        json={"source_path": body["staged_path"], "technical_pdf": body["candidates"][0]["path"]},
    )
    assert confirm.status_code == 200
    assert confirm.json()["data"]["reference"] == "SO-1234"


def test_api_upload_rejects_path_escape(tmp_path: Path) -> None:
    from seamtech_search.api import _safe_relative_path

    assert ".." not in str(_safe_relative_path("../../etc/passwd"))
    assert str(_safe_relative_path("a//b.pdf")) == str(Path("a/b.pdf"))


@pytest.mark.parametrize('forme', ['absolute', 'relative', 'symlink'])
def test_import_refuse_excel_choisi_hors_dossier_avant_lecture(tmp_path: Path, monkeypatch, forme):
    import seamtech_search.import_pipeline as pipeline

    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    hors = tmp_path / 'externe.xlsx'
    hors.write_bytes(b'donnees synthetiques')
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    lectures = []
    monkeypatch.setattr(pipeline, 'extract_excel_summary', lambda path: lectures.append(path))
    if forme == 'absolute':
        choisi = hors
    elif forme == 'relative':
        choisi = Path('../../externe.xlsx')
    else:
        choisi = source / 'lien.xlsx'
        choisi.symlink_to(hors)
    with pytest.raises(ValueError, match='outside the scanned folder'):
        pipeline.import_folder(source, config, index, selected_excel=choisi)
    assert lectures == []


@pytest.mark.parametrize('mode', ['scan', 'import'])
@pytest.mark.parametrize('extension', ['.pdf', '.xlsx', '.txt'])
def test_import_refuse_fichier_lie_vers_exterieur_avant_lecture(tmp_path: Path, monkeypatch, mode, extension):
    import seamtech_search.import_pipeline as pipeline

    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    hors = tmp_path / ('externe' + extension)
    hors.write_bytes(b'donnees synthetiques')
    (source / ('lien' + extension)).symlink_to(hors)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    lectures = []
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, cfg: lectures.append(path))
    monkeypatch.setattr(pipeline, 'extract_excel_summary', lambda path: lectures.append(path))
    with pytest.raises(ValueError, match='outside the scanned folder'):
        if mode == 'scan':
            pipeline.scan_folder(source, config)
        else:
            pipeline.import_folder(source, config, index)
    assert lectures == []


@pytest.mark.parametrize('cible', ['interne', 'repertoire', 'absente'])
def test_scan_refuse_liens_symboliques_meme_non_externes(tmp_path: Path, cible):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    cible_path = source / 'cible'
    if cible == 'interne':
        cible_path.write_text('texte synthetique')
    elif cible == 'repertoire':
        cible_path.mkdir()
    (source / 'lien').symlink_to(cible_path, target_is_directory=cible == 'repertoire')
    config = make_config(tmp_path, root)
    with pytest.raises(ValueError, match='Symbolic link'):
        scan_folder(source, config)


def test_import_refuse_excel_choisi_absent(tmp_path: Path):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    with pytest.raises(ValueError, match='Selected Excel file does not exist'):
        import_folder(source, config, index, selected_excel='absent.xlsx')


def test_scan_ne_lit_jamais_lien_substitue_apres_inventaire(tmp_path: Path, monkeypatch):
    import seamtech_search.import_pipeline as pipeline

    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    entree = source / 'entree.pdf'
    entree.write_bytes(b'original synthetique')
    hors = tmp_path / 'externe.pdf'
    hors.write_bytes(b'externe synthetique')
    config = make_config(tmp_path, root)
    lecture = []
    inventaire = pipeline._inventaire_scan_confine

    def substituer(fd, repertoire):
        fichiers = inventaire(fd, repertoire)
        entree.unlink()
        entree.symlink_to(hors)
        return fichiers

    def extraire(chemin, _config):
        lecture.append(chemin.read_bytes())
        return pipeline.ExtractedData(raw_text='', extraction_status='failed')

    monkeypatch.setattr(pipeline, '_inventaire_scan_confine', substituer)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', extraire)
    try:
        scan_folder(source, config)
    except (ValueError, OSError):
        pass
    assert b'externe synthetique' not in lecture


def test_api_refuse_excel_externe_sans_le_lire(tmp_path: Path, monkeypatch):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    externe = tmp_path / 'externe.xlsx'
    externe.write_bytes(b'donnees synthetiques')
    client = _client(tmp_path, root)
    lectures = []
    monkeypatch.setattr(import_pipeline, 'extract_excel_summary', lambda path: lectures.append(path))
    reponse = client.post('/imports?wait=true', json={'source_path': str(source), 'excel_file': str(externe)})
    assert reponse.status_code == 400
    assert lectures == []


def test_api_scan_refuse_lien_externe_sans_le_lire(tmp_path: Path, monkeypatch):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'donnees synthetiques')
    (source / 'lien.pdf').symlink_to(externe)
    client = _client(tmp_path, root)
    lectures = []
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', lambda *args: lectures.append(args))
    reponse = client.post('/imports/scan', json={'source_path': str(source)})
    assert reponse.status_code == 400
    assert lectures == []


@pytest.mark.parametrize('extension', ['.pdf', '.xlsx'])
@pytest.mark.parametrize('cible', ['fichier', 'repertoire'])
def test_scan_refuse_substitution_concurrente_sans_lire_exterieur(tmp_path, monkeypatch, extension, cible):
    import seamtech_search.import_pipeline as pipeline

    root = tmp_path / 'roots'
    source = root / 'source'
    repertoire = source / 'sous'
    repertoire.mkdir(parents=True)
    entree = repertoire / ('document' + extension)
    entree.write_bytes(b'original synthetique')
    hors = tmp_path / 'exterieur'
    hors.mkdir()
    (hors / entree.name).write_bytes(b'exterieur synthetique')
    inventaire = pipeline._inventaire_scan_confine
    lectures = []

    def substituer(fd, repertoire_source):
        fichiers = inventaire(fd, repertoire_source)
        if cible == 'fichier':
            entree.unlink()
            entree.symlink_to(hors / entree.name)
        else:
            repertoire.rename(source / 'ancien')
            repertoire.symlink_to(hors, target_is_directory=True)
        return fichiers

    def extraire_pdf(chemin, _config):
        lectures.append(chemin.read_bytes())
        return pipeline.ExtractedData(raw_text='', extraction_status='failed')

    def extraire_excel(chemin):
        lectures.append(chemin.read_bytes())
        return pipeline.ExcelSummary(filename=chemin.name, path=str(chemin))

    monkeypatch.setattr(pipeline, '_inventaire_scan_confine', substituer)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', extraire_pdf)
    monkeypatch.setattr(pipeline, 'extract_excel_summary', extraire_excel)
    try:
        pipeline.scan_folder(source, make_config(tmp_path, root))
    except (ValueError, OSError):
        pass
    assert b'exterieur synthetique' not in lectures


@pytest.mark.parametrize('extension', ['.pdf', '.xlsx'])
def test_scan_refuse_copie_au_dela_de_la_limite(tmp_path, monkeypatch, extension):
    root = tmp_path / 'roots'
    root.mkdir()
    (root / ('document' + extension)).write_bytes(b'x' * 11)
    config = make_config(tmp_path, root).model_copy(update={'max_file_size_bytes': 10})
    lectures = []
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', lambda *args: lectures.append('pdf'))
    monkeypatch.setattr(import_pipeline, 'extract_excel_summary', lambda *args: lectures.append('excel'))
    with pytest.raises(ValueError, match='size limit'):
        scan_folder(root, config)
    assert not lectures


def test_import_ne_lit_jamais_lien_substitue_apres_inventaire(tmp_path, monkeypatch):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    entree = source / 'entree.pdf'
    entree.write_bytes(b'original synthetique')
    hors = tmp_path / 'externe.pdf'
    hors.write_bytes(b'externe synthetique')
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    inventaire = import_pipeline._inventaire_scan_confine
    lectures = []
    substitutions = []

    def substituer(fd, repertoire):
        fichiers = inventaire(fd, repertoire)
        entree.unlink()
        entree.symlink_to(hors)
        substitutions.append(True)
        return fichiers

    def extraire(chemin, _config):
        lectures.append(chemin.read_bytes())
        return import_pipeline.ExtractedData(raw_text='', extraction_status='failed')

    monkeypatch.setattr(import_pipeline, '_inventaire_scan_confine', substituer)
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', extraire)
    try:
        import_folder(source, config, index)
    except (ValueError, OSError):
        pass
    assert substitutions and b'externe synthetique' not in lectures


@pytest.mark.parametrize('mode', ['scan', 'upload'])
def test_api_scan_erreur_os_expurgee_et_nettoie_le_depot(tmp_path, monkeypatch, mode):
    import seamtech_search.api as api

    root = tmp_path / 'roots'
    root.mkdir()
    client = _client(tmp_path, root)
    secret = 'CHEMIN-SYNTHETIQUE-SECRET'

    def echec(*args):
        raise OSError(secret)

    monkeypatch.setattr(api, 'scan_folder', echec)
    if mode == 'scan':
        reponse = client.post('/imports/scan', json={'source_path': str(root)})
    else:
        reponse = client.post('/imports/upload', files=[('files', ('doc.pdf', b'fake', 'application/pdf'))])
    assert reponse.status_code == 409
    assert secret not in reponse.text
    if mode == 'upload':
        assert not list(import_pipeline.staging_root(make_config(tmp_path, root)).glob('*'))


@pytest.mark.parametrize('mode', ['scan', 'upload'])
def test_api_scan_permission_os_n_expose_pas_chemin(tmp_path, monkeypatch, mode):
    import seamtech_search.api as api

    root = tmp_path / 'roots'
    root.mkdir()
    client = _client(tmp_path, root)
    secret = 'CHEMIN-SYNTHETIQUE-SECRET'

    def echec(*args):
        raise PermissionError(secret)

    monkeypatch.setattr(api, 'scan_folder', echec)
    if mode == 'scan':
        reponse = client.post('/imports/scan', json={'source_path': str(root)})
        assert reponse.status_code == 403
    else:
        reponse = client.post('/imports/upload', files=[('files', ('doc.pdf', b'fake', 'application/pdf'))])
        assert reponse.status_code == 400
        assert not list(import_pipeline.staging_root(make_config(tmp_path, root)).glob('*'))
    assert secret not in reponse.text


def test_scan_refuse_modification_pendant_copie_et_supprime_temporaire(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    root = tmp_path / 'roots'
    root.mkdir()
    (root / 'document.pdf').write_bytes(b'donnees synthetiques')
    temporaire = tmp_path / 'temp'
    temporaire.mkdir()
    monkeypatch.setattr(import_pipeline.tempfile, 'tempdir', str(temporaire))
    vrai_fstat = os.fstat
    comptage = 0

    def fstat_modifie(fd):
        nonlocal comptage
        resultat = vrai_fstat(fd)
        comptage += 1
        if comptage == 2:
            return SimpleNamespace(st_size=resultat.st_size, st_mtime_ns=resultat.st_mtime_ns + 1, st_ctime_ns=resultat.st_ctime_ns, st_nlink=resultat.st_nlink)
        return resultat

    monkeypatch.setattr(import_pipeline.os, 'fstat', fstat_modifie)
    with pytest.raises(ValueError, match='changed during scanning'):
        scan_folder(root, make_config(tmp_path, root))
    assert not list(temporaire.iterdir())


def test_scan_refuse_lien_physique_vers_exterieur(tmp_path, monkeypatch):
    import os

    root = tmp_path / 'roots'
    root.mkdir()
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'EXTERIEUR SYNTHETIQUE')
    os.link(externe, root / 'document.pdf')
    lectures = []
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', lambda path, config: lectures.append(path.read_bytes()))
    with pytest.raises(ValueError, match='hard link'):
        scan_folder(root, make_config(tmp_path, root))
    assert not lectures


def test_scan_inventorie_sans_parcourir_chemins_non_epingles(tmp_path, monkeypatch):
    root = tmp_path / 'roots'
    repertoire = root / 'source'
    repertoire.mkdir(parents=True)
    (repertoire / 'notes.txt').write_text('synthetique')

    def inventaire_par_chemin_interdit(*args):
        raise AssertionError('inventaire par chemin non épinglé')

    monkeypatch.setattr(import_pipeline, '_walk_files', inventaire_par_chemin_interdit)
    resultat = scan_folder(repertoire, make_config(tmp_path, root))
    assert resultat['files_detected'] == 1


def test_scan_ne_parcourt_pas_repertoire_substitue_pendant_inventaire(tmp_path, monkeypatch):
    import os

    root = tmp_path / 'roots'
    source = root / 'source'
    interieur = source / 'sous'
    interieur.mkdir(parents=True)
    (interieur / 'document.pdf').write_bytes(b'interieur synthetique')
    exterieur = tmp_path / 'exterieur'
    exterieur.mkdir()
    (exterieur / 'document.pdf').write_bytes(b'exterieur synthetique')
    ouverture = os.open
    effectue = []

    def substituer_avant_open(path, flags, *args, **kwargs):
        if path == 'sous' and not effectue:
            effectue.append(True)
            interieur.rename(source / 'ancien')
            interieur.symlink_to(exterieur, target_is_directory=True)
        return ouverture(path, flags, *args, **kwargs)

    monkeypatch.setattr(import_pipeline.os, 'open', substituer_avant_open)
    # La vérification de capacité ne doit pas être faussée par le remplacement
    # du callable os.open pour injecter la course.
    monkeypatch.setattr(import_pipeline.os, 'supports_dir_fd', os.supports_dir_fd | {substituer_avant_open})
    lectures = []
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', lambda p, c: lectures.append(p.read_bytes()))
    with pytest.raises(OSError):
        scan_folder(source, make_config(tmp_path, root))
    assert effectue and not lectures


def test_import_ne_televerse_pas_source_substituee_apres_extraction(tmp_path, monkeypatch):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    document = source / 'document.pdf'
    document.write_bytes(b'original synthetique')
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'exterieur synthetique')
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', lambda p, c: import_pipeline.ExtractedData(
        raw_text='FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST\nQuantity: 1', extraction_status='partial'
    ))
    envois = []

    def televerser(**kwargs):
        envois.extend(Path(p).read_bytes() for p in kwargs['files_to_upload'] if p)
        return import_pipeline.UploadBatch(status='not_configured')

    def progres(phase, _pourcentage):
        if phase == 'uploading':
            document.unlink()
            document.symlink_to(externe)

    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', televerser)
    try:
        import_folder(source, config, index, progress_callback=progres)
    except (OSError, ValueError):
        pass
    assert b'original synthetique' in envois
    assert b'exterieur synthetique' not in envois


def test_scan_racine_deplacee_reste_ancree_sans_lire_exterieur(tmp_path, monkeypatch):
    root = tmp_path / 'roots'
    source = root / 'source'
    source.mkdir(parents=True)
    (source / 'document.pdf').write_bytes(b'interieur synthetique')
    exterieur = tmp_path / 'exterieur'
    exterieur.mkdir()
    (exterieur / 'document.pdf').write_bytes(b'exterieur synthetique')
    inventaire = import_pipeline._inventaire_scan_confine
    lectures = []

    def remplacer_racine(fd, chemin):
        source.rename(root / 'ancien')
        source.symlink_to(exterieur, target_is_directory=True)
        return inventaire(fd, chemin)

    def extraire(chemin, config):
        lectures.append(chemin.read_bytes())
        return import_pipeline.ExtractedData(raw_text='', extraction_status='failed')

    monkeypatch.setattr(import_pipeline, '_inventaire_scan_confine', remplacer_racine)
    monkeypatch.setattr(import_pipeline, 'extract_structured_pdf', extraire)
    scan_folder(source, make_config(tmp_path, root))
    assert lectures == [b'interieur synthetique']


def test_api_reprise_refuse_generation_interrompue_et_correction_recupere(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    identifiant = created['import_id']
    original = import_pipeline.generate_report

    def generation_interrompue(_data, chemin, **_kwargs):
        chemin.write_bytes(b'rapport-tronque')
        raise OSError('panne synthetique')

    monkeypatch.setattr(import_pipeline, 'generate_report', generation_interrompue)
    with pytest.raises(OSError):
        client.patch(f'/imports/{identifiant}', json={'reference': 'CORRECTION-SYNTHETIQUE'})
    from unittest.mock import patch

    from seamtech_search.redis_store import RedisStore

    with patch.object(RedisStore, 'is_configured', return_value=True), \
         patch.object(RedisStore, 'get_job', return_value={
             'id': identifiant, 'status': 'running', 'result': {'all_verified': True}
         }):
        etat = client.get(f'/imports/{identifiant}').json()
    assert etat['all_verified'] is False and etat['report_generation_pending'] is True
    assert etat['result']['all_verified'] is False
    response = client.post(f'/imports/{identifiant}/retry-upload')
    assert response.status_code == 409
    assert 'inachevée' in response.json()['detail']
    assert client.get(f'/imports/{identifiant}/artifacts/report_pdf').status_code == 409
    assert client.get(f'/imports/{identifiant}/artifacts/report_docx').status_code == 409
    monkeypatch.setattr(import_pipeline, 'generate_report', original)
    corrige = client.patch(f'/imports/{identifiant}', json={'reference': 'CORRECTION-SYNTHETIQUE'})
    assert corrige.status_code == 200
    assert corrige.json()['report_generation_pending'] is False
    assert client.post(f'/imports/{identifiant}/retry-upload').status_code == 200


def test_artifact_refuse_substitution_du_fichier_entre_controle_et_ouverture(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search import api

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    original = Path(created['technical_pdf'])
    secret = tmp_path / 'hors-racine' / 'confidentiel'
    secret.parent.mkdir()
    secret.write_bytes(b'SECRET-SYNTHETIQUE-HORS-RACINE')
    octets_initiaux = original.read_bytes()
    ouverture_originale = api._ouvrir_artefact_confine
    def substituer(*args, **kwargs):
        fd = ouverture_originale(*args, **kwargs)
        original.unlink()
        original.symlink_to(secret)
        return fd
    monkeypatch.setattr(api, '_ouvrir_artefact_confine', substituer)
    response = client.get(f"/imports/{created['import_id']}/artifacts/source_pdf")
    assert response.status_code == 200
    assert response.content == octets_initiaux
    assert b'SECRET-SYNTHETIQUE-HORS-RACINE' not in response.content


def test_artifact_local_conserve_plages_et_ferme_descripteur(tmp_path: Path, monkeypatch) -> None:
    import os

    from seamtech_search import api

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    original = Path(created['technical_pdf']).read_bytes()
    ouvrir = api._ouvrir_artefact_confine
    descripteurs = []
    def noter(*args, **kwargs):
        fd = ouvrir(*args, **kwargs)
        descripteurs.append(fd)
        return fd
    monkeypatch.setattr(api, '_ouvrir_artefact_confine', noter)
    response = client.get(f"/imports/{created['import_id']}/artifacts/source_pdf", headers={'Range': 'bytes=0-9'})
    assert response.status_code == 206 and response.content == original[:10]
    assert response.headers['content-range'] == f'bytes 0-9/{len(original)}'
    assert len(descripteurs) == 1
    with pytest.raises(OSError):
        os.fstat(descripteurs[0])


@pytest.mark.parametrize('type_lien', ['symbolique', 'physique'])
def test_artifact_refuse_lien_local_vers_fichier_hors_racine(tmp_path: Path, type_lien: str) -> None:
    import os

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    original = Path(created['technical_pdf'])
    secret = tmp_path / 'hors-racine' / 'confidentiel'
    secret.parent.mkdir()
    secret.write_bytes(b'SECRET-SYNTHETIQUE-HORS-RACINE')
    original.unlink()
    if type_lien == 'symbolique':
        original.symlink_to(secret)
    else:
        os.link(secret, original)
    response = client.get(f"/imports/{created['import_id']}/artifacts/source_pdf")
    assert response.status_code == 404
    assert b'SECRET-SYNTHETIQUE-HORS-RACINE' not in response.content


def test_open_local_refuse_substitution_apres_controle(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search import api

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    fichier = source / 'notes.txt'
    originaux = fichier.read_bytes()
    secret = tmp_path / 'hors-racine' / 'confidentiel'
    secret.parent.mkdir()
    secret.write_bytes(b'SECRET-SYNTHETIQUE-HORS-RACINE')
    client = _client(tmp_path, root)
    ouvrir = api._ouvrir_artefact_confine
    appels = []
    def substituer(*args, **kwargs):
        fd = ouvrir(*args, **kwargs)
        appels.append(fd)
        fichier.unlink()
        fichier.symlink_to(secret)
        return fd
    monkeypatch.setattr(api, '_ouvrir_artefact_confine', substituer)
    reponse = client.post('/open', params={'path': str(fichier)})
    assert appels and reponse.status_code == 200 and reponse.content == originaux


def test_source_en_quarantaine_reste_telechargeable(tmp_path: Path) -> None:
    import shutil

    from seamtech_search.import_pipeline import get_import, quarantine_root, update_import
    from seamtech_search.worker import relocaliser_references

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    client = _client(tmp_path, root)
    created = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    identifiant = created['import_id']
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    payload = get_import(index, identifiant)
    original = Path(payload['technical_pdf']).read_bytes()
    destination = quarantine_root(config) / 'depot-synthetique'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    relocaliser_references(payload, source, destination)
    update_import(index, identifiant, payload['status'], payload)
    index.close()
    reponse = client.get(f'/imports/{identifiant}/artifacts/source_pdf')
    assert reponse.status_code == 200 and reponse.content == original


def test_import_echoue_ne_publie_pas_cle_objet_non_verifiee(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    def echec(**kwargs):
        return UploadBatch('failed', [UploadedArtifact(str(f), Path(f).name,
                           key='cle-ambigue', bucket='bucket-synthetique', status='failed', verified=False)
                           for f in kwargs['files_to_upload'] if f])
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', echec)
    result = import_folder(source, config, index, import_id='test-echec')
    assert all(f.object_key is None and f.object_bucket is None for f in result.files)
    with index.connect() as connexion:
        lignes = connexion.execute('SELECT object_key, object_bucket FROM documents WHERE is_dir = 0').fetchall()
    assert lignes and all(row['object_key'] is None and row['object_bucket'] is None for row in lignes)
    index.close()


@pytest.mark.xfail(strict=True, reason='Reprise après upload partiel refuse le document déjà préservé')
def test_reprise_import_apres_envoi_partiel_conserve_documents_deja_proteges(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    root = tmp_path / 'roots'
    source = make_source(tmp_path)
    config = make_config(tmp_path, root)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    compteur = 0
    def envoyer(**kwargs):
        nonlocal compteur
        compteur += 1
        arts = []
        for nombre, f in enumerate(p for p in kwargs['files_to_upload'] if p):
            ok = compteur > 1 or nombre == 0
            arts.append(UploadedArtifact(str(f), Path(f).name, key=('cle-' + str(nombre)) if ok else None,
                                         bucket='bucket-synthetique', status='uploaded' if ok else 'failed',
                                         verified=ok, verification='relecture_sha256' if ok else 'non_verifie',
                                         sha256=hashlib.sha256(Path(f).read_bytes()).hexdigest() if ok else None))
        return UploadBatch('partial' if compteur == 1 else 'uploaded', arts)
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', envoyer)
    premier = import_folder(source, config, index, import_id='test-reprise')
    assert not premier.all_verified
    second = import_folder(source, config, index, import_id='test-reprise')
    assert not second.all_verified and second.transport_verifie
    index.close()


def test_reimport_partiel_refuse_avant_regeneration_et_nouvel_envoi(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    appels = []
    def envoyer(**kwargs):
        appels.append(True)
        arts = []
        for n, f in enumerate(p for p in kwargs['files_to_upload'] if p):
            ok = n == 0
            arts.append(UploadedArtifact(str(f), Path(f).name, key=f'cle-{n}' if ok else None,
                                         bucket='bucket-synthetique', status='uploaded' if ok else 'failed',
                                         verified=ok, verification='relecture_sha256' if ok else 'non_verifie',
                                         sha256=hashlib.sha256(Path(f).read_bytes()).hexdigest() if ok else None))
        return UploadBatch('partial', arts)
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', envoyer)
    premier = import_folder(source, config, index, import_id='test-reprise-garde')
    assert not premier.all_verified
    assert any(a['verified'] and a.get('sha256') for a in premier.artifacts)
    assert len(appels) == 1
    rapport = Path(premier.report_path)
    avant = rapport.read_bytes()
    monkeypatch.setattr(import_pipeline, 'generate_report', lambda *_args, **_kw: pytest.fail('rapport écrasé'))
    with pytest.raises(RuntimeError, match='reprise'):
        import_folder(source, config, index, import_id='test-reprise-garde')
    assert len(appels) == 1
    assert rapport.read_bytes() == avant
    index.close()


@pytest.mark.parametrize('identifiant', ['', '../sortie', '/tmp/ailleurs', '.', 'id/../../sortie', 'x\\y', 'x' * 129])
def test_import_identifiant_invalide_refuse_avant_generation(tmp_path: Path, identifiant: str, monkeypatch) -> None:
    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    monkeypatch.setattr(import_pipeline, 'generate_report', lambda *_args, **_kw: pytest.fail('génération prématurée'))
    with pytest.raises(ValueError, match="Identifiant d'import invalide"):
        import_folder(source, config, index, import_id=identifiant)
    index.close()


def test_import_initial_ne_suit_pas_lien_rapport_vers_source(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    cible = source / 'piece.txt'
    cible.write_bytes(b'original-synthetique')
    dossier = config.database_path.parent / 'reports' / 'test-lien'
    dossier.mkdir(parents=True)
    (dossier / 'technical-report.pdf').symlink_to(cible)
    import_folder(source, config, index, import_id='test-lien')
    assert cible.read_bytes() == b'original-synthetique'
    assert not (dossier / 'technical-report.pdf').is_symlink()
    index.close()


def test_import_initial_refuse_racine_rapport_symbolique(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    dossier_rapports = config.database_path.parent / 'reports'
    dossier_rapports.symlink_to(source, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        import_folder(source, config, index, import_id='test-lien-racine')
    assert not (source / 'test-lien-racine').exists()
    index.close()


def test_import_refuse_rapport_dans_arbre_source(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=source / 'db.sqlite')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    rapport_source = source / 'reports' / 'id-conflict' / 'technical-report.pdf'
    rapport_source.parent.mkdir(parents=True)
    rapport_source.write_bytes(b'original-synthetique')
    with pytest.raises(ValueError, match='source'):
        import_folder(source, config, index, import_id='id-conflict')
    assert rapport_source.read_bytes() == b'original-synthetique'
    index.close()


def test_import_envoie_copie_privee_des_rapports_malgre_substitution_publique(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    rapports_vus = []
    def upload(**kwargs):
        arts = []
        for fichier in kwargs['files_to_upload']:
            fichier = Path(fichier)
            if fichier.name in ('technical-report.pdf', 'technical-report.docx'):
                contenu_initial = fichier.read_bytes()
                public = config.database_path.parent / 'reports' / 'test-copie' / fichier.name
                assert public != fichier
                public.write_bytes(b'substitution-synthetique')
                assert fichier.read_bytes() == contenu_initial
                rapports_vus.append(fichier.name)
            arts.append(UploadedArtifact(str(fichier), fichier.name, key='cle-' + fichier.name,
                                         bucket='bucket-synthetique', status='uploaded', verified=True))
        return UploadBatch('uploaded', arts)
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', upload)
    result = import_folder(source, config, index, import_id='test-copie')
    assert sorted(rapports_vus) == ['technical-report.docx', 'technical-report.pdf']
    assert {a['path'] for a in result.artifacts}.issuperset({result.report_path, result.report_docx_path})
    assert not any('.correction-' in a['path'] for a in result.artifacts)
    index.close()


def test_reprise_envoi_relie_les_objets_verifies_au_catalogue(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.import_pipeline import retry_upload
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    tentative = 0
    def upload(**kwargs):
        nonlocal tentative
        tentative += 1
        reussi = tentative > 1
        return UploadBatch('uploaded' if reussi else 'failed', [
            UploadedArtifact(str(f), Path(f).name, key=('cle-' + str(i)) if reussi else None, bucket='bucket-synthetique',
                             status='uploaded' if reussi else 'failed', verified=reussi,
                             verification='relecture_sha256' if reussi else 'non_verifie', sha256=hashlib.sha256(Path(f).read_bytes()).hexdigest() if reussi else None)
            for i, f in enumerate(kwargs['files_to_upload']) if f
        ])
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', upload)
    initial = import_folder(source, config, index, import_id='test-reprise-liaison')
    assert not initial.all_verified
    repris = retry_upload(index, config, 'test-reprise-liaison')
    assert not repris['all_verified']
    assert repris['preservation']['transport_verifie']
    assert repris['preservation']['identite_locale_observee']
    with index.connect() as connexion:
        lignes = connexion.execute('SELECT path, object_key, object_bucket FROM documents WHERE is_dir = 0').fetchall()
    attendus = {f['path']: (f['object_key'], f['object_bucket']) for f in repris['files']}
    assert lignes and all((ligne['object_key'], ligne['object_bucket']) == attendus[ligne['path']] for ligne in lignes)
    assert all(f['catalogue_lie'] for f in repris['files'])
    assert repris['liaison_catalogue']['conflits'] == 0
    index.close()


def test_reprise_ne_valide_pas_une_source_modifiee_apres_preuve(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.import_pipeline import retry_upload
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / 'roots')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    n_appels = 0
    def upload(**kwargs):
        nonlocal n_appels
        n_appels += 1
        arts = []
        for i, path in enumerate(kwargs['files_to_upload']):
            ok = n_appels > 1 or i == 0
            arts.append(UploadedArtifact(str(path), Path(path).name, key=('cle-' + str(i)) if ok else None,
                                         bucket='bucket-synthetique', status='uploaded' if ok else 'failed', verified=ok,
                                         verification='relecture_sha256' if ok else 'non_verifie',
                                         sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest() if ok else None))
        return UploadBatch('partial' if n_appels == 1 else 'uploaded', arts)
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', upload)
    initial = import_folder(source, config, index, import_id='test-source-modifiee')
    deja_preserve = next(Path(a['path']) for a in initial.artifacts if a['verified'])
    deja_preserve.write_bytes(b'octets nouveaux et differents')
    reprise = retry_upload(index, config, 'test-source-modifiee')
    assert not reprise['all_verified']
    index.close()


def test_envoi_integralement_prouve_reste_uploaded(tmp_path: Path, monkeypatch) -> None:
    """Un envoi dont CHAQUE artefact est prouvé reste « uploaded ».

    Régression verrouillée (CI « recette-corpus-reel » / « recette-locale ») :
    une version rabaissait systématiquement « uploaded » en « partial » dès
    qu'une destination était configurée, interdisant de qualifier la moindre
    réussite. Le rabaissement doit dépendre des PREUVES, pas de la destination.
    """
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / "roots")
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    def upload(**kwargs):
        arts = []
        for i, fichier in enumerate(kwargs["files_to_upload"]):
            if not fichier:
                continue
            chemin = Path(fichier)
            arts.append(
                UploadedArtifact(
                    str(chemin), chemin.name, key=f"cle-{i}", bucket="bucket-synthetique",
                    status="uploaded", verified=True, verification="relecture_sha256",
                    sha256=hashlib.sha256(chemin.read_bytes()).hexdigest(),
                )
            )
        return UploadBatch("uploaded", arts)

    monkeypatch.setattr(import_pipeline, "upload_artifacts_to_storage", upload)
    result = import_folder(source, config, index, import_id="test-upload-prouve")
    assert result.upload_status == "uploaded", result.upload_status
    # Le succès GLOBAL reste suspendu dès qu'une destination objet existe
    # (pas de snapshot durable garanti) : l'import reste « à revoir », ce qui
    # n'enlève rien à l'envoi, prouvé et complet.
    assert result.status == "needs_review", (result.status, result.warnings)
    index.close()


def test_envoi_avec_cle_non_prouvee_est_partial_et_a_revoir(tmp_path: Path, monkeypatch) -> None:
    """Une clé NON PROUVÉE au milieu d'un envoi réussi → « partial » + à revoir.

    Inverse du test précédent : « uploaded » n'est annoncé que sur preuve. Une
    métadonnée déclarative (fournie par l'application) ne prouve pas les octets
    réellement stockés ; l'artefact est reclassé en échec et l'import reste à
    revoir au lieu d'annoncer un succès de préservation.
    """
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = make_source(tmp_path)
    config = make_config(tmp_path, tmp_path / "roots")
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    def upload(**kwargs):
        arts = []
        for i, fichier in enumerate(kwargs["files_to_upload"]):
            if not fichier:
                continue
            chemin = Path(fichier)
            prouve = i != 1  # le second artefact déclare une clé sans preuve
            arts.append(
                UploadedArtifact(
                    str(chemin), chemin.name, key=f"cle-{i}", bucket="bucket-synthetique",
                    status="uploaded", verified=True,
                    verification="relecture_sha256" if prouve else "metadata_seule",
                    sha256=hashlib.sha256(chemin.read_bytes()).hexdigest(),
                )
            )
        return UploadBatch("uploaded", arts)

    monkeypatch.setattr(import_pipeline, "upload_artifacts_to_storage", upload)
    result = import_folder(source, config, index, import_id="test-upload-non-prouve")
    assert result.upload_status == "partial", result.upload_status
    assert result.status == "needs_review", result.status
    index.close()
