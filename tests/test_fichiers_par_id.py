"""Contrat HTTP des pièces accessibles par identifiant de catalogue.

Les tests utilisent des fichiers locaux dans une racine temporaire : pas de
PostgreSQL, de MinIO, de chemin arbitraire fourni par le navigateur ou de secret
S3 dans la réponse.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.storage import S3StorageClient

TOKEN = "test-service-token"


def _client_avec_fichiers(
    tmp_path: Path,
    fichiers: list[Path],
    *,
    object_keys: dict[str, str] | None = None,
) -> tuple[TestClient, dict[str, int]]:
    root = tmp_path / "archive"
    root.mkdir(exist_ok=True)
    database = tmp_path / "index.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    ids: dict[str, int] = {}
    for path in fichiers:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"%PDF-1.7 test content")
        document = Document(
            path=path,
            name=path.name,
            parent_path=path.parent,
            extension=path.suffix.lower(),
            size=path.stat().st_size,
            modified_at=path.stat().st_mtime,
            is_dir=False,
            object_key=(object_keys or {}).get(path.name),
            object_bucket="private-bucket" if (object_keys or {}).get(path.name) else None,
        )
        index.upsert_document(document)
        with index.connect() as connexion:
            row = connexion.execute("SELECT id FROM documents WHERE path_key = ?", (document.path_key,)).fetchone()
            ids[path.name] = int(row["id"])
    index.close()
    storage = (
        {
            "s3_endpoint_url": "https://storage.private.invalid",
            "s3_bucket": "private-bucket",
            "s3_access_key": "test-access-secret",
            "s3_secret_key": "test-secret-key",
        }
        if object_keys
        else {}
    )
    config = AppConfig(
        root_paths=[root], database_path=database, auth_token=TOKEN, min_free_bytes=0, **storage
    )
    return TestClient(create_app(config)), ids


def test_piece_preview_range_head_download_et_nom_utf8(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    pdf = root / "CLIENT" / "étude façade.pdf"
    content = b"%PDF-1.7\n0123456789\n%%EOF"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(content)
    client, ids = _client_avec_fichiers(tmp_path, [pdf])
    piece_id = ids[pdf.name]
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    catalogue = client.get("/pieces", headers=headers)
    assert catalogue.status_code == 200
    ligne = catalogue.json()["pieces"][0]
    assert ligne["id"] == piece_id
    assert not {"path", "path_key", "object_key", "chemin"} & ligne.keys()

    preview = client.get(f"/pieces/{piece_id}/apercu", headers=headers)
    assert preview.status_code == 200
    assert preview.content == content
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.headers["content-disposition"].startswith("inline;")
    assert "filename*=UTF-8''%C3%A9tude%20fa%C3%A7ade.pdf" in preview.headers["content-disposition"]
    assert preview.status_code != 501

    range_response = client.get(
        f"/pieces/{piece_id}/apercu",
        headers={**headers, "Range": "bytes=5-9"},
    )
    assert range_response.status_code == 206
    assert range_response.content == content[5:10]
    assert range_response.headers["content-range"] == f"bytes 5-9/{len(content)}"

    head = client.head(f"/pieces/{piece_id}/apercu", headers=headers)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))

    download = client.get(f"/pieces/{piece_id}/telecharger", headers=headers)
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "127.0.0.1" not in download.text


def test_pieces_refusent_les_ids_inconnus_les_chemins_interdits_et_les_formats_non_previsualisables(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    pdf_interdit = tmp_path / "hors-racine" / "secret.pdf"
    fichier_cao = root / "plans" / "coque.dxf"
    client, ids = _client_avec_fichiers(tmp_path, [pdf_interdit, fichier_cao])
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    inconnu = client.get("/pieces/999999/apercu", headers=headers)
    assert inconnu.status_code == 404
    assert inconnu.status_code != 501

    interdit = client.get(f"/pieces/{ids[pdf_interdit.name]}/apercu", headers=headers)
    assert interdit.status_code == 403

    non_previsualisable = client.get(f"/pieces/{ids[fichier_cao.name]}/apercu", headers=headers)
    assert non_previsualisable.status_code == 415
    assert "Téléchargez" in non_previsualisable.json()["detail"]

    sans_session = client.get(f"/pieces/{ids[fichier_cao.name]}/telecharger")
    assert sans_session.status_code == 401


def test_piece_stockee_s3_est_servie_par_id_sans_redirection_ni_secret(tmp_path: Path, monkeypatch) -> None:
    content = b"%PDF-1.7\nS3 bytes served through the authenticated API\n%%EOF"
    object_key = "imports-private/secret/fiche.pdf"
    remote_pdf = tmp_path / "archive" / "CLIENT" / "fiche.pdf"
    fake_s3 = _FauxS3(content)
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda _self: fake_s3)
    client, ids = _client_avec_fichiers(
        tmp_path,
        [remote_pdf],
        object_keys={remote_pdf.name: object_key},
    )
    piece_id = ids[remote_pdf.name]
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    catalogue = client.get("/pieces", headers=headers)
    assert catalogue.status_code == 200
    assert catalogue.json()["pieces"][0]["id"] == piece_id
    assert not {"path", "path_key", "object_key", "chemin"} & catalogue.json()["pieces"][0].keys()
    assert "storage.private.invalid" not in catalogue.text
    assert "test-access-secret" not in catalogue.text
    assert "test-secret-key" not in catalogue.text
    assert object_key not in catalogue.text

    preview = client.get(f"/pieces/{piece_id}/apercu", headers=headers)
    assert preview.status_code == 200
    assert preview.content == content
    assert preview.headers["content-disposition"].startswith("inline;")
    assert "location" not in preview.headers
    assert "storage.private.invalid" not in preview.text
    assert object_key not in preview.text

    range_response = client.get(
        f"/pieces/{piece_id}/apercu",
        headers={**headers, "Range": "bytes=5-9"},
    )
    assert range_response.status_code == 206
    assert range_response.content == content[5:10]
    assert range_response.headers["content-range"] == f"bytes 5-9/{len(content)}"

    head = client.head(f"/pieces/{piece_id}/apercu", headers=headers)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))
    assert "location" not in head.headers

    download = client.get(f"/pieces/{piece_id}/telecharger", headers=headers)
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "location" not in download.headers
    assert fake_s3.get_object_calls[0] == {
        "Bucket": "private-bucket",
        "Key": object_key,
    }
    assert fake_s3.get_object_calls[1] == {
        "Bucket": "private-bucket",
        "Key": object_key,
        "Range": "bytes=5-9",
    }
    assert fake_s3.head_object_calls == [{"Bucket": "private-bucket", "Key": object_key}]


class _FauxCorpsS3:
    def __init__(self, contenu: bytes):
        self.contenu = contenu

    def iter_chunks(self, chunk_size: int):
        for debut in range(0, len(self.contenu), chunk_size):
            yield self.contenu[debut : debut + chunk_size]


class _FauxS3:
    def __init__(self, contenu: bytes):
        self.contenu = contenu
        self.get_object_calls: list[dict[str, str]] = []
        self.head_object_calls: list[dict[str, str]] = []

    def get_object(self, **params: str):
        self.get_object_calls.append(params)
        range_header = params.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", range_header)
            assert match is not None
            debut, fin = int(match.group(1)), int(match.group(2))
            contenu = self.contenu[debut : fin + 1]
            return {
                "ContentRange": f"bytes {debut}-{fin}/{len(self.contenu)}",
                "ContentLength": len(contenu),
                "Body": _FauxCorpsS3(contenu),
            }
        return {"ContentLength": len(self.contenu), "Body": _FauxCorpsS3(self.contenu)}

    def head_object(self, **params: str):
        self.head_object_calls.append(params)
        return {"ContentLength": len(self.contenu), "ETag": '"fake-etag"'}
