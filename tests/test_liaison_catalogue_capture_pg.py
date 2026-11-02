"""Liaison conditionnelle et rollback sur PostgreSQL jetable réel."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.models import Document
from seamtech_search.storage import S3StorageClient, StoragePreflightError
from tests.test_snapshot_import_pg import _config, _magasin

pytestmark = pytest.mark.postgres


def _imports_sans_objet(base_recherche, tmp_path, monkeypatch, identifiants=('liaison-A',)):
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'octets-synthetiques-originaux')
    cfg = _config(tmp_path, source, base_recherche['url'])
    index = base_recherche['index']
    magasin = _magasin(monkeypatch)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', lambda *_a, **_kw: (_ for _ in ()).throw(
            StoragePreflightError('panne avant ecriture synthetique')))
        imports = [pipeline.import_folder(source, cfg, index, import_id=i) for i in identifiants]
    assert all(not i.publication_pending and not i.all_verified for i in imports)
    assert not magasin.envois
    return source, piece, cfg, index, magasin, imports


def _document(index, piece):
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT id, content_hash, source_sha256, object_key, object_bucket '
                        'FROM documents WHERE path_key = %s', (_cle(piece),))
        return curseur.fetchone()

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



def test_reprise_pg_lie_preuve_identique_et_preserve_reference(base_recherche, tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    avant = _document(index, piece)
    assert avant[2] == imports[0].files[0].source_sha256 and avant[3] is None
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('CREATE TABLE test_ref_document (id_document BIGINT REFERENCES documents(id))')
        curseur.execute('INSERT INTO test_ref_document VALUES (%s)', (avant[0],))
    repris = pipeline.retry_upload(index, cfg, imports[0].import_id)
    apres = _document(index, piece)
    assert apres[:3] == avant[:3] and apres[3:] == (repris['files'][0]['object_key'], 'bucket-synthetique')
    assert repris['files'][0]['catalogue_lie'] is True and not repris['all_verified']
    assert magasin.objets[apres[3]]['donnees'] == piece.read_bytes()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT id_document FROM test_ref_document')
        assert curseur.fetchone()[0] == avant[0]


def test_reprise_pg_refuse_document_reindexe(base_recherche, tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, _magasin, imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    remplacement = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.txt',
                            size=10, modified_at=3, is_dir=False, text='version differente')
    with pytest.raises(RuntimeError):
        index.upsert_documents([remplacement], strict=True)
    # Reproduction d'un ancien écrivain SQL qui contourne la garde applicative.
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('UPDATE documents SET size = %s, content = %s WHERE path_key = %s',
                        (remplacement.size, remplacement.searchable_text, _cle(piece)))
    assert _document(index, piece)[2] is None
    repris = pipeline.retry_upload(index, cfg, imports[0].import_id)
    assert _document(index, piece)[3] is None
    assert repris['files'][0]['catalogue_lie'] is False and repris['status'] == 'needs_review'


def test_reprise_pg_annule_liaison_si_publication_echoue(base_recherche, tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    sauvegarder = pipeline.update_import
    def echouer(idx, identifiant, statut, payload):
        if not payload.get('publication_pending'):
            raise RuntimeError('panne-publication-synthetique')
        return sauvegarder(idx, identifiant, statut, payload)
    monkeypatch.setattr(pipeline, 'update_import', echouer)
    with pytest.raises(RuntimeError, match='panne-publication-synthetique'):
        pipeline.retry_upload(index, cfg, imports[0].import_id)
    intention = pipeline.get_import(index, imports[0].import_id)
    assert intention and intention['publication_pending'] and len(magasin.envois) == 1
    assert _document(index, piece)[3] is None


def test_deux_reprises_pg_necrasent_pas_la_liaison_concurrente(base_recherche, tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, imports = _imports_sans_objet(
        base_recherche, tmp_path, monkeypatch, ('liaison-A', 'liaison-B'))
    barriere = Barrier(2)
    lier = index.lier_objets_capture
    def simultanement(correspondances):
        barriere.wait(timeout=15)
        return lier(correspondances)
    monkeypatch.setattr(index, 'lier_objets_capture', simultanement)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futurs = [pool.submit(pipeline.retry_upload, index, cfg, i.import_id) for i in imports]
        resultats = [f.result(timeout=30) for f in futurs]
    fichier = _document(index, piece)
    assert len(magasin.envois) == 2
    assert sorted(r['files'][0]['catalogue_lie'] for r in resultats) == [False, True]
    gagnant = next(r for r in resultats if r['files'][0]['catalogue_lie'])
    perdant = next(r for r in resultats if not r['files'][0]['catalogue_lie'])
    assert fichier[3] == gagnant['files'][0]['object_key'] != perdant['files'][0]['object_key']
    assert perdant['status'] == 'needs_review' and not perdant['all_verified']


def test_migration_pg_ancienne_ligne_ne_devine_pas_empreinte(base_recherche):
    index = base_recherche['index']
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT id FROM documents ORDER BY id LIMIT 1')
        identifiant = curseur.fetchone()[0]
        curseur.execute('UPDATE documents SET content_hash = %s, object_key = %s WHERE id = %s',
                        ('f' * 64, 'objet-ancien', identifiant))
        # Base jetable exclusivement : simule un schéma antérieur sans la colonne.
        curseur.execute('ALTER TABLE documents DROP COLUMN source_sha256')
        curseur.execute("DELETE FROM schema_migrations WHERE version = '032_identite_octets_documents'")
    index.run_migrations()
    index.run_migrations()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT content_hash, source_sha256, object_key FROM documents WHERE id = %s', (identifiant,))
        assert curseur.fetchone() == ('f' * 64, None, 'objet-ancien')
        curseur.execute("SELECT count(*) FROM schema_migrations WHERE version = '032_identite_octets_documents'")
        assert curseur.fetchone()[0] == 1


def test_piece_capturee_pg_verifie_objet_et_session(base_recherche, tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session

    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.pdf'
    original = b'FICHE DE FABRICATION\nReference: SYNTHETIQUE'
    piece.write_bytes(original)
    index = base_recherche['index']
    cfg = _config(tmp_path, source, base_recherche['url']).model_copy(update={'auth_token': 'service-synthetique'})
    magasin = _magasin(monkeypatch)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHETIQUE', extraction_status='partial'))
    initial = pipeline.import_folder(source, cfg, index, import_id='piece-pg')
    identifiant = _document(index, piece)[0]
    compte = creer_utilisateur(index, 'lecteur-test', 'Nom synthetique', 'mot-de-passe-test',
                               role='approbateur', doit_changer_mot_de_passe=False)
    session = ouvrir_session(index, compte['id_utilisateur'])
    en_tetes = {'X-SEAMTECH-TOKEN': 'service-synthetique', 'X-SEAMTECH-UTILISATEUR': 'lecteur-test',
                 'X-SEAMTECH-ROLE': 'approbateur', 'X-SEAMTECH-SESSION': str(session['id_session']),
                 'X-SEAMTECH-SESSION-JETON': session['jeton']}
    piece.write_bytes(b'LOCAL-MODIFIE')
    with TestClient(create_app(cfg)) as navigateur:
        url = f'/pieces/{identifiant}/apercu'
        assert navigateur.get(url, headers={'X-SEAMTECH-TOKEN': 'service-synthetique'}).status_code == 401
        reponse = navigateur.get(url, headers={**en_tetes, 'Range': 'bytes=0-9'})
        assert reponse.status_code == 206 and reponse.content == original[:10]
        magasin.corrompre(initial.files[0].object_key, octets=b'OBJET-MODIFIE')
        assert navigateur.get(url, headers=en_tetes).status_code == 404
        assert navigateur.head(url, headers=en_tetes).status_code == 404
        piece.write_bytes(original)
        assert navigateur.get(url, headers=en_tetes).content == original


def test_mutation_par_code_ancien_pg_invalide_identite_avant_reprise(base_recherche, tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, _magasin, imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('UPDATE documents SET size = %s, content_hash = %s WHERE path_key = %s',
                        (999, 'texte-remplace', _cle(piece)))
    assert _document(index, piece)[2] is None
    repris = pipeline.retry_upload(index, cfg, imports[0].import_id)
    assert repris['files'][0]['catalogue_lie'] is False and _document(index, piece)[3] is None


def test_demarrage_pg_refuse_declencheur_absent_ou_desactive(base_recherche):
    index = base_recherche['index']
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('ALTER TABLE documents DISABLE TRIGGER documents_invalider_source_sha256')
    with pytest.raises(RuntimeError, match='identité|intégrité|déclencheur'):
        index.run_migrations()


def test_diagnostic_pg_signale_declencheur_desactive(base_recherche):
    index = base_recherche['index']
    assert index.health_details()['schema_application_a_jour'] is True
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('ALTER TABLE documents DISABLE TRIGGER documents_invalider_source_sha256')
    assert index.health_details()['schema_application_a_jour'] is False


def test_declencheur_invalide_meme_les_reecritures_identiques_pg(base_recherche, tmp_path: Path):
    from seamtech_search.models import Document

    index = base_recherche['index']
    piece = tmp_path / 'piece.pdf'
    piece.write_bytes(b'original-synthetique')
    document = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.pdf',
                        size=piece.stat().st_size, modified_at=piece.stat().st_mtime, is_dir=False,
                        source_sha256='a' * 64)
    index.upsert_document(document)
    colonnes = ('path_key', 'path', 'name', 'parent_path', 'extension', 'size', 'modified_at', 'is_dir',
                'content', 'search_vector', 'extractor_version', 'content_hash', 'extraction_status',
                'extraction_detail', 'category')
    for colonne in colonnes:
        with index.connect() as connexion, connexion.cursor() as curseur:
            curseur.execute('UPDATE documents SET source_sha256 = %s WHERE path_key = %s', ('a' * 64, _cle(piece)))
            curseur.execute(f'UPDATE documents SET {colonne} = {colonne} WHERE path_key = %s', (_cle(piece),))
        assert _document(index, piece)[2] is None, colonne
    index.upsert_document(document)
    index.upsert_document(document)
    assert _document(index, piece)[2] == 'a' * 64
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('UPDATE documents SET object_key = %s WHERE path_key = %s', ('cle', _cle(piece)))
    assert _document(index, piece)[2] == 'a' * 64


def test_scan_et_suppression_pg_preservent_document_capture_sans_objet(base_recherche, tmp_path: Path, monkeypatch):
    from seamtech_search.models import Document

    _source, piece, _cfg, index, _magasin, _imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    identite = _document(index, piece)[2]
    non_capture = Document(path=piece, name=piece.name, parent_path=piece.parent, extension=piece.suffix,
                           size=999, modified_at=2, is_dir=False, text='texte autre')
    assert index.upsert_documents([non_capture]) == 0
    assert _document(index, piece)[2] == identite
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT path_key FROM documents WHERE path_key <> %s', (str(piece),))
        autres = {ligne[0] for ligne in curseur.fetchall()}
    assert index.remove_missing(autres, scan_complete=True) == 0
    assert _document(index, piece)[2] == identite


def test_rebuild_pg_sans_fiche_preserve_capture_sans_objet(tmp_path: Path):
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / 'unused.db', url)
    try:
        index.initialize()
        piece = tmp_path / 'piece.txt'
        piece.write_bytes(b'source-synthetique')
        document = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.txt',
                            size=piece.stat().st_size, modified_at=piece.stat().st_mtime,
                            is_dir=False, source_sha256='a' * 64)
        index.upsert_document(document)
        index.initialize(rebuild=True)
        assert index.rebuild_mode == 'en_place'
        assert _document(index, piece)[2] == 'a' * 64
        index.run_migrations()
        assert index.health_details()['schema_application_a_jour'] is True
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_reimport_octets_differents_preserve_capture_sans_objet_pg(base_recherche, tmp_path: Path, monkeypatch):
    import hashlib

    source, piece, cfg, index, magasin, imports = _imports_sans_objet(base_recherche, tmp_path, monkeypatch)
    avant = _document(index, piece)
    assert avant[2] == imports[0].files[0].source_sha256 and avant[3] is None
    piece.write_bytes(b'nouvelle-version-synthetique')
    second = pipeline.import_folder(source, cfg, index, import_id='nouvelle-version-pg')

    assert _document(index, piece) == avant
    fichier = second.files[0]
    assert fichier.source_sha256 == hashlib.sha256(piece.read_bytes()).hexdigest()
    assert fichier.source_sha256 != avant[2]
    assert fichier.object_key and magasin.objets[fichier.object_key]['donnees'] == piece.read_bytes()
    assert fichier.catalogue_lie is False and not second.all_verified
    assert any('catalogue' in avertissement.lower() for avertissement in second.warnings)

    avant_reprise = len(magasin.envois)
    reprise = pipeline.retry_upload(index, cfg, imports[0].import_id)
    assert reprise['inventaire']['sources_modifiees'] == [str(piece)]
    assert len(magasin.envois) == avant_reprise and _document(index, piece) == avant
