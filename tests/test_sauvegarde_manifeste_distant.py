"""Le manifeste distant doit permettre de retrouver le dump effectivement envoyé."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from seamtech_search import sauvegarde


class StockageMemoire:
    def __init__(self, corrompre_manifeste=False):
        self.objets = {}
        self.corrompre_manifeste = corrompre_manifeste

    def upload_file(self, chemin, cle, avoid_overwrite=False):
        contenu = Path(chemin).read_bytes()
        self.objets[cle] = b'corrompu' if self.corrompre_manifeste and cle.endswith('.json') else contenu
        return cle

    def download_file(self, cle, chemin):
        Path(chemin).write_bytes(self.objets[cle])


@pytest.fixture
def sauver_sans_postgres(monkeypatch):
    class ConnexionSnapshot:
        def set_session(self, **kwargs):
            assert kwargs == {'isolation_level': 'REPEATABLE READ', 'readonly': True}

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, sql):
            assert sql == 'SELECT pg_export_snapshot()'

        def fetchone(self):
            return ('snapshot-synthetique',)

        def close(self):
            pass

    monkeypatch.setattr(sauvegarde, '_connecter', lambda url: ConnexionSnapshot())
    monkeypatch.setattr(sauvegarde, '_trouver_binaire', lambda nom: nom)
    monkeypatch.setattr(sauvegarde, '_etat_base', lambda url, **kwargs: {
        'versions_schema': [sauvegarde.VERSION_SCHEMA_METIER, sauvegarde.VERSION_SCHEMA_APPLICATION], 'tables': {},
        'schema_identite_octets_valide': True, 'fiches_par_statut': {}, 'documents': 0,
    })
    def dump(args, **kwargs):
        Path(args[2].split('=', 1)[1]).write_bytes(b'dump synthetique')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(sauvegarde.subprocess, 'run', dump)
    monkeypatch.setattr(sauvegarde, '_commit_courant', lambda: 'synthetique')
    purges = []
    monkeypatch.setattr(sauvegarde, 'appliquer_retention', lambda *a, **kw: purges.append(True) or [])
    return purges


def test_manifeste_distant_contient_cle_reelle_dump(tmp_path, sauver_sans_postgres):
    client = StockageMemoire()
    resultat = sauvegarde.sauver('postgresql://synthetique', [], tmp_path, client_s3=client)
    manifeste = json.loads(client.objets[resultat['manifeste_cle_s3']])
    assert manifeste['version_schema_application'] == sauvegarde.VERSION_SCHEMA_APPLICATION
    assert manifeste['dump']['cle_s3'] == resultat['dump']['cle_s3']
    assert client.objets[manifeste['dump']['cle_s3']] == b'dump synthetique'
    # La copie partielle ne remplace pas une sauvegarde globale prouvée.
    assert sauver_sans_postgres == []


def test_manifeste_distant_corrompu_interdit_purge(tmp_path, sauver_sans_postgres):
    client = StockageMemoire(corrompre_manifeste=True)
    with pytest.raises(sauvegarde.SauvegardeError, match='manifeste'):
        sauvegarde.sauver('postgresql://synthetique', [], tmp_path, client_s3=client)
    assert sauver_sans_postgres == []


def test_objet_reference_sans_client_ne_peut_pas_etre_complet(tmp_path, sauver_sans_postgres, monkeypatch):
    monkeypatch.setattr(sauvegarde, 'inventorier_references_documents', lambda url, **kwargs: {
        'nb_documents': 1, 'nb_locaux_manquants': 0, 'nb_avec_objet': 1,
        'documents': [{'id': 1, 'path': '', 'object_key': 'documents/exemple.pdf'}],
    })
    resultat = sauvegarde.sauver(
        'postgresql://synthetique', [], tmp_path, inclure_references_base=True,
    )
    assert resultat['complet'] is False
    assert resultat['complet_perimetre_demande'] is False
    assert resultat['objets_s3']['nb_manquants'] == 1


def test_copie_locale_refuse_nom_sortant_du_bundle(tmp_path):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'synthetique')
    bundle = tmp_path / 'bundle'
    refs = {'documents': [{'id': 1, 'path': str(source), 'name': '../../../hors.pdf'}]}
    with pytest.raises(sauvegarde.SauvegardeError):
        sauvegarde.copier_fichiers_locaux(refs, bundle)
    assert not (tmp_path / 'hors.pdf').exists()


def test_sauvegarde_incomplete_ne_purge_pas_les_anciennes(tmp_path, sauver_sans_postgres, monkeypatch):
    monkeypatch.setattr(sauvegarde, 'inventorier_references_documents', lambda url, **kwargs: {
        'nb_documents': 1, 'nb_locaux_manquants': 0, 'nb_avec_objet': 1,
        'documents': [{'id': 1, 'path': '', 'object_key': 'documents/exemple.pdf'}],
    })
    resultat = sauvegarde.sauver(
        'postgresql://synthetique', [], tmp_path, client_s3=StockageMemoire(),
        inclure_references_base=True,
    )
    assert resultat['complet'] is False
    assert sauver_sans_postgres == []


def test_dump_distant_refuse_nom_hors_dossier_temporaire(tmp_path, monkeypatch):
    monkeypatch.setenv('SEAMTECH_SAUVEGARDE_TMP', str(tmp_path / 'telechargements'))
    client = StockageMemoire()
    client.objets['dump'] = b'dump synthetique'
    manifeste = {'dump': {'fichier': '../../hors.dump', 'cle_s3': 'dump', 'sha256': 'incorrect'}}
    with pytest.raises(sauvegarde.SauvegardeError, match='nom'):
        sauvegarde.restaurer('postgresql://synthetique/base', manifeste, client_s3=client)
    assert not (tmp_path.parent / 'hors.dump').exists()


def test_dump_distant_ne_remplace_pas_fichier_existant_et_nettoie(tmp_path, monkeypatch):
    monkeypatch.setenv('SEAMTECH_SAUVEGARDE_TMP', str(tmp_path))
    existant = tmp_path / 'sauvegarde.dump'
    existant.write_bytes(b'original')
    client = StockageMemoire()
    client.objets['dump'] = b'dump synthetique'
    manifeste = {'dump': {'fichier': existant.name, 'cle_s3': 'dump', 'sha256': 'incorrect'}}
    with pytest.raises(sauvegarde.SauvegardeError, match='empreinte'):
        sauvegarde.restaurer('postgresql://synthetique/base', manifeste, client_s3=client)
    assert existant.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [existant]


def test_deux_sauvegardes_gardent_copies_independantes(tmp_path, sauver_sans_postgres, monkeypatch):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'premiere version')
    monkeypatch.setattr(sauvegarde, 'inventorier_references_documents', lambda url, **kwargs: {
        'nb_documents': 1, 'nb_locaux_manquants': 0, 'nb_avec_objet': 0,
        'documents': [{'id': 1, 'path': str(source), 'name': 'source.pdf', 'object_key': ''}],
    })
    bundle = tmp_path / 'backups'
    premier = sauvegarde.sauver('postgresql://synthetique', [], bundle, inclure_references_base=True)
    source.write_bytes(b'deuxieme version')
    second = sauvegarde.sauver('postgresql://synthetique', [], bundle, inclure_references_base=True)
    assert premier['dump']['fichier'] != second['dump']['fichier']
    ancienne_copie = bundle / premier['fichiers_locaux']['copies'][0]['path_relatif']
    nouvelle_copie = bundle / second['fichiers_locaux']['copies'][0]['path_relatif']
    assert ancienne_copie.read_bytes() == b'premiere version'
    assert nouvelle_copie.read_bytes() == b'deuxieme version'


def test_copie_referencee_envoyee_hors_site_et_verifiee(tmp_path, sauver_sans_postgres, monkeypatch):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'piece synthetique')
    monkeypatch.setattr(sauvegarde, 'inventorier_references_documents', lambda url, **kwargs: {
        'nb_documents': 1, 'nb_locaux_manquants': 0, 'nb_avec_objet': 0,
        'documents': [{'id': 1, 'path': str(source), 'name': source.name, 'object_key': ''}],
    })
    client = StockageMemoire()
    resultat = sauvegarde.sauver('postgresql://synthetique', [], tmp_path / 'backup',
                                 client_s3=client, inclure_references_base=True)
    distant = json.loads(client.objets[resultat['manifeste_cle_s3']])
    copie = distant['fichiers_locaux']['copies'][0]
    assert client.objets[copie['cle_s3_sauvegarde']] == b'piece synthetique'
    assert copie['sha256'] == sauvegarde.hashlib.sha256(b'piece synthetique').hexdigest()


def test_copie_hors_site_corrompue_interdit_publication_et_retention(tmp_path, sauver_sans_postgres, monkeypatch):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'piece synthetique')
    monkeypatch.setattr(sauvegarde, 'inventorier_references_documents', lambda url, **kwargs: {
        'nb_documents': 1, 'nb_locaux_manquants': 0, 'nb_avec_objet': 0,
        'documents': [{'id': 1, 'path': str(source), 'name': source.name, 'object_key': ''}],
    })
    class ClientCorromptCopie(StockageMemoire):
        def upload_file(self, chemin, cle, avoid_overwrite=False):
            resultat = super().upload_file(chemin, cle, avoid_overwrite)
            if '/fichiers/' in cle:
                self.objets[resultat] = b'corrompu'
            return resultat
    client = ClientCorromptCopie()
    with pytest.raises(sauvegarde.SauvegardeError, match='copie'):
        sauvegarde.sauver('postgresql://synthetique', [], tmp_path / 'backup',
                          client_s3=client, inclure_references_base=True)
    assert not any(cle.endswith('.manifest.json') for cle in client.objets)
    assert sauver_sans_postgres == []


def test_manifeste_et_cli_ne_pretendent_pas_a_une_sauvegarde_globale(tmp_path, sauver_sans_postgres):
    client = StockageMemoire()
    resultat = sauvegarde.sauver('postgresql://synthetique', [], tmp_path, client_s3=client)
    distant = json.loads(client.objets[resultat['manifeste_cle_s3']])
    for manifeste in (resultat, distant):
        assert manifeste['protection_globale_prouvee'] is False
        assert manifeste['references_incluses'] is False
        assert manifeste['retention_automatique_autorisee'] is False
        assert manifeste['coherence_references_snapshot'] is False
        assert 'indépendant' in manifeste['coherence']
        assert 'indépendant' in manifeste['rpo_rto']['coherence']


def test_cli_annonce_limites_sauvegarde(tmp_path, sauver_sans_postgres, capsys):
    rc = sauvegarde.main([
        'sauver', '--base-url', 'postgresql://synthetique', '--archive', str(tmp_path),
        '--dossier', str(tmp_path / 'sauvegardes'), '--sans-s3',
    ])
    sortie = json.loads(capsys.readouterr().out)
    assert rc == 2  # impossible de déclarer la protection globale réussie
    assert sortie['complet_perimetre_demande'] is True
    assert sortie['protection_globale_prouvee'] is False
    assert sortie['references_incluses'] is False
    assert sortie['retention_automatique_autorisee'] is False
    assert sortie['coherence_references_snapshot'] is False


def test_sauvegarde_non_qualifiee_interdit_retention_des_anciennes(tmp_path, sauver_sans_postgres):
    resultat = sauvegarde.sauver('postgresql://synthetique', [], tmp_path, client_s3=StockageMemoire())
    assert resultat['complet'] is False
    assert resultat['complet_perimetre_demande'] is True
    assert resultat['protection_globale_prouvee'] is False
    assert resultat['retention_purgees'] == []
    assert sauver_sans_postgres == []


def test_copie_partielle_ne_declenche_pas_le_statut_complet_legacy(tmp_path, sauver_sans_postgres):
    client = StockageMemoire()
    resultat = sauvegarde.sauver('postgresql://synthetique', [], tmp_path, client_s3=client)
    distant = json.loads(client.objets[resultat['manifeste_cle_s3']])
    for manifeste in (resultat, distant):
        assert manifeste['complet'] is False
        assert manifeste['complet_perimetre_demande'] is True
        assert manifeste['protection_globale_prouvee'] is False


@pytest.mark.parametrize('url', [
    'postgresql://compte:synthetique-secret@localhost/base',
    'postgresql://compte@localhost/base?password=synthetique-secret',
])
def test_dump_ne_met_pas_le_mot_de_passe_dans_les_arguments(tmp_path, sauver_sans_postgres, monkeypatch, url):
    import os

    appels = []

    def dump(args, **kwargs):
        appels.append((args, kwargs))
        Path(args[2].split('=', 1)[1]).write_bytes(b'dump synthetique')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(sauvegarde.subprocess, 'run', dump)
    sauvegarde.sauver(url, [], tmp_path)
    args, kwargs = appels[0]
    assert all('synthetique-secret' not in str(partie) for partie in args)
    assert kwargs['env']['PGPASSWORD'] == 'synthetique-secret'
    assert kwargs['env'].get('PATH') == os.environ.get('PATH')


def test_erreur_pg_dump_ne_fuite_pas_le_mot_de_passe(tmp_path, sauver_sans_postgres, monkeypatch):
    def dump(_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr='echec mot de passe synthetique-secret')

    monkeypatch.setattr(sauvegarde.subprocess, 'run', dump)
    with pytest.raises(sauvegarde.SauvegardeError) as erreur:
        sauvegarde.sauver('postgresql://compte:synthetique-secret@localhost/base', [], tmp_path)
    assert 'synthetique-secret' not in str(erreur.value)


def test_restore_cache_le_mot_de_passe_et_ne_fuite_pas_stderr(tmp_path, monkeypatch):
    import hashlib

    dump = tmp_path / 'dump.dump'
    dump.write_bytes(b'dump synthetique')
    manifeste = {'dump': {'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(), 'octets': dump.stat().st_size}}
    appels = []

    class ConnexionAdmin:
        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, *_args):
            pass

        def fetchone(self):
            return None

        def close(self):
            pass

    def echec(args, **kwargs):
        if '--file=-' in args:
            return SimpleNamespace(returncode=0)
        appels.append((args, kwargs))
        return SimpleNamespace(returncode=1, stderr='panne synthetique-secret')

    monkeypatch.setattr(sauvegarde, '_connecter', lambda *a, **kw: ConnexionAdmin())
    monkeypatch.setattr(sauvegarde, '_trouver_binaire', lambda nom: nom)
    monkeypatch.setattr(sauvegarde.subprocess, 'run', echec)
    with pytest.raises(sauvegarde.SauvegardeError) as erreur:
        sauvegarde.restaurer('postgresql://compte:synthetique-secret@localhost/cible', manifeste, dump)
    args, kwargs = appels[0]
    assert all('synthetique-secret' not in str(partie) for partie in args)
    assert kwargs['env']['PGPASSWORD'] == 'synthetique-secret'
    assert 'synthetique-secret' not in str(erreur.value)


def test_cli_base_depuis_environnement_sans_argument_secret(tmp_path, sauver_sans_postgres, monkeypatch, capsys):
    monkeypatch.setenv('SEAMTECH_DATABASE_URL', 'postgresql://utilisateur:synthetique-secret@localhost/base')
    code = sauvegarde.main([
        'sauver', '--archive', str(tmp_path), '--dossier', str(tmp_path / 'sauvegardes'), '--sans-s3',
    ])
    capture = capsys.readouterr()
    assert code == 2
    assert 'synthetique-secret' not in capture.out + capture.err


def test_cli_restauration_cible_par_environnement(tmp_path, monkeypatch, capsys):
    url = 'postgresql://utilisateur:synthetique-secret@localhost/cible'
    monkeypatch.setenv('SEAMTECH_RESTORE_DATABASE_URL', url)
    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: None)
    monkeypatch.setattr(sauvegarde, 'charger_manifeste', lambda *a: {'dump': {'fichier': 'synthetique.dump'}})
    appels = []

    def restaurer(base_cible, *args, **kwargs):
        appels.append(base_cible)
        return {'base': 'cible', 'duree_s': 1.0}

    monkeypatch.setattr(sauvegarde, 'restaurer', restaurer)
    code = sauvegarde.main(['restaurer', '--manifeste', str(tmp_path / 'manifest.json')])
    capture = capsys.readouterr()
    assert code == 0
    assert appels == [url]
    assert 'synthetique-secret' not in capture.out + capture.err


def test_cli_verification_source_par_environnement(tmp_path, monkeypatch, capsys):
    url = 'postgresql://utilisateur:synthetique-secret@localhost/cible'
    monkeypatch.setenv('SEAMTECH_DATABASE_URL', url)
    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: None)
    monkeypatch.setattr(sauvegarde, 'charger_manifeste', lambda *a: {})
    appels = []

    def verifier(base, manifeste, archives):
        appels.append(base)
        return {'ok': True, 'ecarts': []}

    monkeypatch.setattr(sauvegarde, 'verifier', verifier)
    code = sauvegarde.main(['verifier', '--manifeste', str(tmp_path / 'manifest.json')])
    capture = capsys.readouterr()
    assert code == 0
    assert appels == [url]
    assert 'synthetique-secret' not in capture.out + capture.err


def test_sauvegarde_refuse_schema_application_non_migre(tmp_path, sauver_sans_postgres, monkeypatch):
    monkeypatch.setattr(sauvegarde, '_etat_base', lambda url, **kwargs: {
        'versions_schema': [sauvegarde.VERSION_SCHEMA_METIER], 'tables': {},
        'fiches_par_statut': {}, 'documents': 0,
    })
    with pytest.raises(sauvegarde.SauvegardeError, match='032_identite_octets_documents'):
        sauvegarde.sauver('postgresql://synthetique', [], tmp_path)
    assert not list(tmp_path.glob('*.dump'))


def test_verification_signale_version_application_manquante(monkeypatch):
    monkeypatch.setattr(sauvegarde, '_etat_base', lambda url: {
        'versions_schema': [sauvegarde.VERSION_SCHEMA_METIER], 'tables': {},
        'fiches_par_statut': {}, 'documents': 0,
    })
    manifeste = {'tables': {}, 'version_schema_metier': sauvegarde.VERSION_SCHEMA_METIER,
                 'version_schema_application': '032_identite_octets_documents',
                 'fiches_par_statut': {}, 'documents': 0, 'archive': {'nb_fichiers': 0}}
    resultat = sauvegarde.verifier('postgresql://synthetique', manifeste)
    assert resultat['ok'] is False and any('032_identite_octets_documents' in e for e in resultat['ecarts'])


def test_verification_signale_declencheur_application_desactive(monkeypatch):
    monkeypatch.setattr(sauvegarde, '_etat_base', lambda url: {
        'versions_schema': [sauvegarde.VERSION_SCHEMA_METIER, sauvegarde.VERSION_SCHEMA_APPLICATION],
        'schema_identite_octets_valide': False, 'tables': {}, 'fiches_par_statut': {}, 'documents': 0,
    })
    manifeste = {'tables': {}, 'version_schema_metier': sauvegarde.VERSION_SCHEMA_METIER,
                 'version_schema_application': sauvegarde.VERSION_SCHEMA_APPLICATION,
                 'fiches_par_statut': {}, 'documents': 0, 'archive': {'nb_fichiers': 0}}
    resultat = sauvegarde.verifier('postgresql://synthetique', manifeste)
    assert resultat['ok'] is False and any('identité' in ecart for ecart in resultat['ecarts'])
