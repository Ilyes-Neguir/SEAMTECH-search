"""Courses réelles entre captures différentes ou objets pour un même chemin."""
from __future__ import annotations

import hashlib
import os
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


def _cle(chemin: Path) -> str:
    """Clé d'index d'un chemin : CELLE QUE L'INDEXEUR ÉCRIT.

    ``path_key`` est stocké en ``os.path.normcase`` du chemin absolu. Sur
    POSIX, ``normcase`` ne change rien et la confusion est sans effet ; sur
    Windows, il met TOUT en minuscules, si bien qu'une recherche par le chemin
    brut ne trouvait jamais la ligne — et le test échouait en
    ``TypeError: 'NoneType' object is not subscriptable``, un message qui ne
    dit rien de la vraie cause (vue au run 41454455187, cible diag-cible-1).

    Le produit n'est pas en cause : c'est la clé du TEST qui doit être celle du
    produit. Une comparaison de chemins qui ignore la casse de la plateforme
    serait, elle, un test plus faible.
    """
    return os.path.normcase(str(chemin.resolve()))


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
                curseur.execute('SELECT source_sha256, object_key FROM documents WHERE path_key = %s', (_cle(chemin),))
                ligne = curseur.fetchone()
        else:
            ligne = connexion.execute('SELECT source_sha256, object_key FROM documents WHERE path_key = ?',
                                     (_cle(chemin),)).fetchone()
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
