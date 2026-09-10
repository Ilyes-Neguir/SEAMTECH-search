import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import import_folder
from seamtech_search.indexer import SearchIndex
from seamtech_search.storage import S3StorageClient, StorageError, upload_artifacts_to_storage


def test_s3_storage_client_configuration() -> None:
    config = AppConfig(
        root_paths=[Path(".")],
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="test-bucket",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_region="us-east-1",
        min_free_bytes=0,
    )
    client = S3StorageClient(config=config)
    assert client.is_configured() is True
    assert client.bucket_name == "test-bucket"
    assert client.endpoint_url == "http://localhost:9000"


def test_s3_storage_upload_and_presigned_url(tmp_path: Path) -> None:
    test_file = tmp_path / "plan.pdf"
    test_file.write_bytes(b"%PDF-1.4 test content")

    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )

    mock_boto = MagicMock()
    mock_boto.generate_presigned_url.return_value = "https://minio.local/seamtech-documents/plan.pdf?token=abc"

    with patch.object(client, "_get_client", return_value=mock_boto):
        with patch.object(client, "ensure_bucket_exists"):
            key = client.upload_file(test_file, remote_key="jobs/plan.pdf")
            assert key == "jobs/plan.pdf"
            mock_boto.upload_file.assert_called_once()

            url = client.get_presigned_url(key)
            assert "token=abc" in url


def test_s3_storage_upload_bytes() -> None:
    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )

    mock_boto = MagicMock()
    with patch.object(client, "_get_client", return_value=mock_boto):
        with patch.object(client, "ensure_bucket_exists"):
            key = client.upload_bytes(b"sample data", remote_key="data.txt")
            assert key == "data.txt"
            mock_boto.put_object.assert_called_once()


def test_s3_storage_nonexistent_file_raises(tmp_path: Path) -> None:
    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )
    with pytest.raises(StorageError):
        client.upload_file(tmp_path / "does_not_exist.pdf")


def test_upload_artifacts_to_storage_helper(tmp_path: Path) -> None:
    file1 = tmp_path / "doc.pdf"
    file1.write_bytes(b"test pdf")
    file2 = tmp_path / "table.xlsx"
    file2.write_bytes(b"test xlsx")

    config = AppConfig(
        root_paths=[tmp_path],
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="seamtech-bucket",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        min_free_bytes=0,
    )

    with patch("seamtech_search.storage.S3StorageClient.upload_file") as mock_upload:
        status = upload_artifacts_to_storage(
            folder_name="REF-001",
            files_to_upload=[file1, file2],
            config=config,
        )
        assert status == "uploaded"
        assert mock_upload.call_count == 2


def test_import_with_s3_storage_uploads_all_files(tmp_path: Path) -> None:
    source_folder = tmp_path / "reference_order"
    source_folder.mkdir()

    fixture_pdf = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    (source_folder / "fiche-technique.pdf").write_bytes(fixture_pdf.read_bytes())
    (source_folder / "plan_de_coupe.dwg").write_bytes(b"CAD_DWG_BINARY_MOCK")
    (source_folder / "notes.txt").write_text("Assembly instructions")

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="seamtech-documents",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        min_free_bytes=0,
    )
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    with patch("seamtech_search.storage.S3StorageClient.upload_file") as mock_upload:
        result = import_folder(source_folder, config, index)
        assert result.status == "completed"
        assert result.upload_status == "uploaded"
        # 3 source files (pdf, dwg, txt) + 2 reports (pdf, docx) = 5 uploaded files
        assert mock_upload.call_count == 5


def test_multi_technical_pdf_import_analyzes_all_sheets(tmp_path: Path) -> None:
    source_folder = tmp_path / "multi_spec_order"
    source_folder.mkdir()

    fixture_pdf = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    (source_folder / "fiche_principale.pdf").write_bytes(fixture_pdf.read_bytes())
    (source_folder / "fiche_secondaire.pdf").write_bytes(fixture_pdf.read_bytes())

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source_folder, config, index)
    assert result.status == "completed"
    assert result.analyzed_files == 2
    assert result.data is not None
    assert len(result.data.get("additional_sheets", [])) == 1
    assert result.data["additional_sheets"][0]["filename"] == "fiche_secondaire.pdf"
    # Verify report generated successfully
    assert result.report_path and Path(result.report_path).exists()
    assert result.report_docx_path and Path(result.report_docx_path).exists()


@pytest.mark.s3
def test_live_minio_s3_integration(tmp_path: Path) -> None:
    endpoint = os.environ.get("SEAMTECH_TEST_S3_URL")
    if not endpoint:
        pytest.skip("SEAMTECH_TEST_S3_URL not set (live MinIO integration skipped)")

    access_key = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "minioadmin")
    secret_key = os.environ.get("SEAMTECH_S3_SECRET_KEY", "minioadmin")

    client = S3StorageClient(
        endpoint_url=endpoint,
        bucket_name="seamtech-integration-test",
        access_key_id=access_key,
        secret_access_key=secret_key,
    )

    test_doc = tmp_path / "live_spec.pdf"
    test_doc.write_bytes(b"%PDF-1.4 Live MinIO Integration Test Document")

    key = client.upload_file(test_doc, remote_key="integration/live_spec.pdf")
    assert key == "integration/live_spec.pdf"

    download_target = tmp_path / "downloaded.pdf"
    client.download_file(key, download_target)
    assert download_target.exists()
    assert download_target.read_bytes() == test_doc.read_bytes()

    presigned = client.get_presigned_url(key)
    assert "seamtech-integration-test" in presigned
    assert "X-Amz-Signature" in presigned or "Signature" in presigned
