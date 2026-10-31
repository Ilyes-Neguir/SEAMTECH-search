"""Courses réelles entre captures différentes ou objets pour un même chemin."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document


def _document(chemin: Path, contenu: bytes, *, objet: str | None = None) -> Document:
    return Document(
        path=chemin, name=chemin.name, parent_path=chemin.parent, extension='.txt',
        size=len(contenu), modified_at=1, is_dir=False, text=contenu.decode(),
        source_sha256=hashlib.sha256(contenu).hexdigest(), lexical_path_key=True,
        object_key=objet, object_bucket='bucket-synthetique' if objet else None,
    )


def _course(index_a: SearchIndex, index_b: SearchIndex, chemin: Path, cas: str) -> None:
    anciens = b'capture-ancienne'
    entree_a = _document(chemin, b'capture-a', objet='objet-a' if cas == 'meme-octets-objets-differents' else None)
    entree_b = _document(
        chemin, b'capture-a' if cas == 'meme-octets-objets-differents' else b'capture-b',
        objet='objet-b' if cas == 'meme-octets-objets-differents' else None,
    )
    if cas == 'ancienne-capture':
        assert index_a.upsert_document(_document(chemin, anciens))

    barriere = Barrier(2)

    def publier(index: SearchIndex, document: Document) -> tuple[int, set[str], Document]:
        proteges: set[str] = set()
        barriere.wait(timeout=10)
        change = index.upsert_documents([document], proteges=proteges)
        return change, proteges, document

    with ThreadPoolExecutor(max_workers=2) as pool:
        futurs = [pool.submit(publier, index_a, entree_a), pool.submit(publier, index_b, entree_b)]
        resultats = [f.result(timeout=40) for f in futurs]

    if cas == 'ancienne-capture':
        assert [r[0] for r in resultats] == [0, 0]
        attendu = hashlib.sha256(anciens).hexdigest()
        gagnant = None
    else:
        assert sorted(r[0] for r in resultats) == [0, 1]
        gagnant = next(r[2] for r in resultats if r[0])
        attendu = gagnant.source_sha256
    for change, proteges, document in resultats:
        assert proteges == (set() if change else {document.path_key})

    with index_a.connect() as connexion:
        if index_a.is_postgres:
            with connexion.cursor() as curseur:
                curseur.execute('SELECT source_sha256, object_key FROM documents WHERE path_key = %s', (str(chemin),))
                ligne = curseur.fetchone()
        else:
            ligne = connexion.execute('SELECT source_sha256, object_key FROM documents WHERE path_key = ?',
                                     (str(chemin),)).fetchone()
    assert ligne[0] == attendu
    if gagnant is not None:
        assert ligne[1] == gagnant.object_key
    else:
        assert ligne[1] is None


@pytest.mark.parametrize('cas', ['chemin-neuf', 'ancienne-capture', 'meme-octets-objets-differents'])
def test_course_deux_captures_sqlite(tmp_path: Path, cas: str) -> None:
    premier = SearchIndex(tmp_path / 'catalogue.sqlite')
    premier.initialize()
    premier.run_migrations()
    second = SearchIndex(tmp_path / 'catalogue.sqlite')
    try:
        for i in range(5):
            _course(premier, second, tmp_path / f'{cas}-{i}.txt', cas)
    finally:
        premier.close()
        second.close()


@pytest.mark.postgres
@pytest.mark.parametrize('cas', ['chemin-neuf', 'ancienne-capture', 'meme-octets-objets-differents'])
def test_course_deux_captures_postgres(base_recherche: dict[str, Any], tmp_path: Path, cas: str) -> None:
    premier = base_recherche['index']
    second = SearchIndex(tmp_path / 'inutile.db', base_recherche['url'])
    try:
        for i in range(5):
            _course(premier, second, tmp_path / f'{cas}-{i}.txt', cas)
    finally:
        second.close()
