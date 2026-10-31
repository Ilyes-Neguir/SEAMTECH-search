"""Restauration isolée depuis un stockage simulé ; aucune donnée réelle."""
import json
import os
import shutil
from pathlib import Path

import pytest

from seamtech_search import sauvegarde
from seamtech_search.indexer import SearchIndex
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable
from tests.test_sauvegarde_unites import S3EnMemoire


@pytest.mark.postgres
@pytest.mark.sauvegarde
def test_base_et_piece_restaures_depuis_stockage_simule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    if not os.environ.get('SEAMTECH_TEST_DATABASE_URL'):
        pytest.skip('PostgreSQL de test absent')
    nom, url = _creer_base_jetable()
    nom_cible = f'{nom}_cible'
    url_cible = sauvegarde._remplacer_nom_base(url, nom_cible)
    index = SearchIndex(tmp_path / 'unused.db', url)
    client = S3EnMemoire()
    try:
        index.initialize()
        index.run_migrations()
        source = tmp_path / 'original.pdf'
        source.write_bytes(b'piece synthetique locale')
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir) "
                    "VALUES (%s, %s, %s, %s, '.pdf', %s, 1, false)",
                    ('test-hors-site', str(source), source.name, str(source.parent), source.stat().st_size),
                )
        backup = tmp_path / 'backup'
        resultat = sauvegarde.sauver(url, [], backup, client_s3=client, inclure_references_base=True)
        assert resultat['complet'] is False
        assert resultat['complet_perimetre_demande'] is True
        assert resultat['fichiers_locaux']['nb_copies'] == 1
        shutil.rmtree(backup)
        source.unlink()
        monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: client)
        monkeypatch.setenv('SEAMTECH_SAUVEGARDE_TMP', str(tmp_path / 'tmp'))
        code = sauvegarde.main(['restaurer', '--cle-manifeste', resultat['manifeste_cle_s3'],
                                '--base-cible', url_cible, '--fichiers-cible', str(tmp_path / 'reconstruit')])
        assert code == 0
        sortie = json.loads(capsys.readouterr().out)
        assert sortie['restaure'] == nom_cible
        assert sortie['fichiers']['nb'] == 1
        assert Path(sortie['fichiers']['fichiers'][0]['path']).read_bytes() == b'piece synthetique locale'
        assert sauvegarde.verifier(url_cible, resultat)['ok']
    finally:
        index.close()
        _supprimer_base_jetable(nom_cible)
        _supprimer_base_jetable(nom)
