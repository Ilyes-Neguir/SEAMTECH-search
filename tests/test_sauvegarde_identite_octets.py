"""Cohérence conservatrice des copies avec l'identité issue du catalogue."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from seamtech_search.sauvegarde import SauvegardeError, copier_fichiers_locaux


@pytest.mark.parametrize('identite', ['connue', 'inconnue', 'differente'])
def test_copie_locale_preserve_ou_signale_identite(tmp_path: Path, identite: str) -> None:
    source = tmp_path / 'source.txt'
    contenu = b'octets synthetiques'
    source.write_bytes(contenu)
    avant = source.stat().st_mtime_ns
    empreinte = hashlib.sha256(contenu if identite != 'differente' else b'autre').hexdigest()
    doc = {'id': 1, 'path': str(source), 'name': source.name,
           'source_sha256': None if identite == 'inconnue' else empreinte}
    bundle = tmp_path / 'backup'
    resultat = copier_fichiers_locaux({'documents': [doc]}, bundle)

    if identite == 'differente':
        assert resultat['nb_copies'] == 0 and resultat['nb_manquants'] == 1
        assert resultat['manquants'][0]['erreur'] == 'identite_catalogue_differente'
        assert not (bundle / 'fichiers' / '1' / source.name).exists()
    else:
        assert resultat['nb_copies'] == 1 and resultat['nb_manquants'] == 0
        copie = resultat['copies'][0]
        assert copie['identite_catalogue'] == ('verifiee' if identite == 'connue' else 'inconnue')
        assert (bundle / copie['path_relatif']).read_bytes() == contenu
    assert source.read_bytes() == contenu and source.stat().st_mtime_ns == avant


@pytest.mark.parametrize('invalide', ['', 'A' * 64, '0' * 63, 'z' * 64, 5, False])
def test_identite_locale_invalide_refuse_avant_toute_copie(tmp_path: Path, invalide: object) -> None:
    source = tmp_path / 'source.txt'
    source.write_bytes(b'synthetique')
    valide = hashlib.sha256(source.read_bytes()).hexdigest()
    docs = [{'id': 1, 'path': str(source), 'name': source.name, 'source_sha256': valide},
            {'id': 2, 'path': str(source), 'name': source.name, 'source_sha256': invalide}]
    bundle = tmp_path / 'backup'
    with pytest.raises(SauvegardeError, match='empreinte.*catalogue'):
        copier_fichiers_locaux({'documents': docs}, bundle)
    assert not bundle.exists()
    assert source.read_bytes() == b'synthetique'
