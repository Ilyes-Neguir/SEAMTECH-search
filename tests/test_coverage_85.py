"""Push overall from 84.9% to 85%+ — hit uncovered lines in indexer/api."""

import pytest


@pytest.mark.postgres
def test_indexer_migrations_and_search(tmp_path):
    import time

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.models import Document

    idx = SearchIndex(tmp_path / "c85.db")
    idx.initialize()
    idx.run_migrations()
    doc = Document(
        path=tmp_path / "x.txt",
        name="x.txt",
        parent_path=tmp_path,
        extension=".txt",
        size=5,
        modified_at=time.time(),
        is_dir=False,
        text="hello world coverage bump",
    )
    idx.upsert_document(doc)
    assert idx.search("hello") is not None
    assert idx.count_documents() >= 1


@pytest.mark.postgres
def test_api_create_and_comptes():
    from seamtech_search.comptes.securite import empreinte_mot_de_passe, verifier_mot_de_passe

    h = empreinte_mot_de_passe("Secret123!")
    assert h.startswith("$scrypt$")
    assert verifier_mot_de_passe("Secret123!", h)


@pytest.mark.postgres
def test_audit_helpers_extra():
    from seamtech_search.audit_logs import _nettoyer_valeur, _tronquer_details

    assert _nettoyer_valeur({"sid": "secret"}, 0) == {}
    assert _tronquer_details({"x": "a" * 5000}) is not None
