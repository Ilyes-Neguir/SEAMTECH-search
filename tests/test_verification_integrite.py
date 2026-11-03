"""Sémantique de la vérification d'intégrité du stockage objet.

Question à laquelle ce fichier répond, sans ambiguïté :

    La métadonnée ``seamtech-sha256`` (que NOUS fournissons à l'envoi) est-elle
    une vérification indépendante des octets stockés ?
    **Non.** Elle est déclarative. Un envoi tronqué conserve la métadonnée
    intacte : la comparer revient à se relire soi-même.

Ce qui prouve quelque chose, et qui est testé ici :

* ``relecture_sha256`` — l'objet est relu et l'empreinte recalculée sur les
  octets REÇUS ;
* ``checksum_serveur`` — le fournisseur a calculé ``ChecksumSHA256`` sur
  l'objet stocké ET déclare ``ChecksumType: FULL_OBJECT`` (un checksum
  multipart n'est PAS l'empreinte du fichier entier).

Et la conséquence opérationnelle : **la purge de la copie locale n'est permise
que par une intégrité prouvée** (``UploadBatch.all_verified``). Ni la
métadonnée, ni la taille seule ne l'autorisent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.storage import (
    S3StorageClient,
    UploadBatch,
    upload_artifacts_to_storage,
)
from tests.s3_en_memoire import S3EnMemoire

_GET_CLIENT_ORIGINAL = S3StorageClient._get_client


def _config(tmp_path: Path, **extra: object) -> AppConfig:
    parametres: dict[str, object] = {
        "root_paths": [tmp_path],
        "min_free_bytes": 0,
        "s3_endpoint_url": "https://s3.invalide",
        "s3_bucket": "seamtech-documents",
        "s3_access_key": "cle-fictive",
        "s3_secret_key": "secret-fictif",
    }
    parametres.update(extra)
    return AppConfig(**parametres)  # type: ignore[arg-type]


def _client(magasin: S3EnMemoire) -> S3StorageClient:
    """Client S3 réel branché sur un magasin en mémoire (aucun socket)."""
    return S3StorageClient(
        endpoint_url="https://s3.invalide",
        bucket_name="seamtech-documents",
        access_key_id="cle-fictive",
        secret_access_key="secret-fictif",
    )


def _fichier(tmp_path: Path, nom: str = "plan.pdf", contenu: bytes = b"%PDF-1.4 plan de coupe") -> Path:
    chemin = tmp_path / nom
    chemin.write_bytes(contenu)
    return chemin


def _brancher(magasin: S3EnMemoire, monkeypatch: pytest.MonkeyPatch) -> None:
    """Branche un double pour un seul test ; pytest rétablit les méthodes."""
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda self: magasin)
    monkeypatch.setattr(S3StorageClient, "ensure_bucket_exists", lambda self: True)
    monkeypatch.setattr(S3StorageClient, "first_free_key", lambda self, cle: cle)


# ---------------------------------------------------------------------------
# 1. La relecture prouve les octets ; la métadonnée ne prouve rien
# ---------------------------------------------------------------------------


def test_relecture_recalcule_l_empreinte_sur_les_octets_stockes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    resultat = client.verifier_integrite(cle, fichier, relire=True)
    assert resultat.integrite_prouvee is True
    assert resultat.methode == "relecture_sha256"
    # Le fournisseur a bien été prié de valider l'empreinte à l'ingestion.
    assert magasin.envois[0]["extra"].get("ChecksumAlgorithm") == "SHA256"


def test_objet_tronque_avec_metadonnee_intacte_est_detecte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LE test qui distingue vérification et déclaration.

    L'objet stocké est tronqué, mais la métadonnée ``seamtech-sha256`` envoyée
    par l'application est toujours là et correspond au fichier local. Une
    vérification qui se contenterait de comparer cette métadonnée déclarerait
    l'objet conforme — et supprimerait la copie locale d'un fichier corrompu.
    La relecture, elle, recalcule l'empreinte et refuse.
    """
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    magasin.corrompre(cle, tronquer_a=5)
    # La métadonnée déclarée est toujours celle du fichier local intact :
    assert magasin.objets[cle]["entete"]["Metadata"]["seamtech-sha256"]

    resultat = client.verifier_integrite(cle, fichier, relire=True)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "echec"
    # Détecté d'abord par la taille, puis par l'empreinte si la taille concorde.
    assert "taille differente" in resultat.detail or "sha256 different" in resultat.detail


def test_objet_altere_de_meme_taille_est_detecte_par_l_empreinte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corruption « invisible » : mêmes octets en nombre, contenu différent."""
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    donnees = magasin.objets[cle]["donnees"]
    magasin.corrompre(cle, octets=b"X" * len(donnees))

    resultat = client.verifier_integrite(cle, fichier, relire=True)
    assert resultat.integrite_prouvee is False
    assert "relecture: sha256 different" in resultat.detail


def test_objet_absent_est_un_echec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    resultat = _client(magasin).verifier_integrite("IMP-1/absent.pdf", fichier, relire=True)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "echec"


# ---------------------------------------------------------------------------
# 2. Quand la relecture est désactivée : métadonnée ≠ preuve
# ---------------------------------------------------------------------------


def test_metadonnee_seule_ne_prouve_pas_et_ne_permet_pas_la_purge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    resultat = client.verifier_integrite(cle, fichier, relire=False)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "metadata_seule"
    assert "NON prouve" in resultat.detail

    # Et un objet corrompu de MÊME LONGUEUR passe par cette porte : c'est
    # exactement pourquoi elle ne vaut pas vérification.
    longueur = len(magasin.objets[cle]["donnees"])
    magasin.corrompre(cle, octets=b"Z" * longueur)
    trompeur = client.verifier_integrite(cle, fichier, relire=False)
    assert trompeur.methode == "metadata_seule", (
        "la métadonnée déclarée ne peut pas voir la corruption du corps : "
        "c'est un aveuglement, pas une preuve"
    )


def test_taille_seule_ne_prouve_pas_et_ne_permet_pas_la_purge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")
    magasin.objets[cle]["entete"]["Metadata"] = {}  # objet déposé hors application

    resultat = client.verifier_integrite(cle, fichier, relire=False)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "taille_seule"


def test_checksum_serveur_full_object_n_est_accepte_que_pour_l_objet_entier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un checksum multipart n'est PAS l'empreinte du fichier : il ne prouve rien."""
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    magasin.ajouter_checksum_serveur(cle, type_checksum="COMPOSITE")
    composite = client.verifier_integrite(cle, fichier, relire=False)
    assert composite.integrite_prouvee is False, "checksum multipart accepté à tort"
    assert composite.sha256 is None
    assert composite.methode == "metadata_seule"

    magasin.ajouter_checksum_serveur(cle, type_checksum="FULL_OBJECT")
    entier = client.verifier_integrite(cle, fichier, relire=False)
    assert entier.integrite_prouvee is True
    assert entier.methode == "checksum_serveur"
    import hashlib
    assert entier.sha256 == hashlib.sha256(fichier.read_bytes()).hexdigest()

    # Corrompu + checksum recalculé par le fournisseur ⇒ divergence détectée.
    magasin.corrompre(cle, octets=b"Z" * 10)
    magasin.ajouter_checksum_serveur(cle, type_checksum="FULL_OBJECT")
    divergente = client.verifier_integrite(cle, fichier, relire=False)
    assert divergente.integrite_prouvee is False
    assert divergente.methode == "echec"


# ---------------------------------------------------------------------------
# 3. Conséquence opérationnelle : all_verified (donc la purge) exige une preuve
# ---------------------------------------------------------------------------


def _lot(tmp_path: Path, magasin: S3EnMemoire, monkeypatch: pytest.MonkeyPatch, **config_extra: object) -> UploadBatch:
    fichier = _fichier(tmp_path)
    _brancher(magasin, monkeypatch)
    return upload_artifacts_to_storage(
        "dossier",
        [fichier],
        _config(tmp_path, **config_extra),
        import_id="IMP-1",
        source_root=tmp_path,
    )


def test_all_verified_exige_une_preuve_sur_les_octets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    lot = _lot(tmp_path, magasin, monkeypatch)
    assert lot.status == "uploaded"
    assert lot.all_verified is True
    assert lot.artifacts[0].verification == "relecture_sha256"
    assert lot.artifacts[0].to_dict()["verification"] == "relecture_sha256"


def test_objet_corrompu_interdit_la_purge_locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaîne complète : envoi → objet corrompu → ``all_verified`` faux.

    C'est cette valeur que le worker consulte avant de supprimer la copie
    locale ; elle doit rester fausse dès que l'intégrité n'est pas établie.
    """
    magasin = S3EnMemoire()

    def _envoi_puis_corruption(
        self, local_path, remote_key=None, content_type=None, *, avoid_overwrite=True, relative_path=None
    ):  # noqa: ANN001, ANN202
        cle = str(remote_key)
        magasin._ecrire(cle, b"abime", {"Metadata": {"seamtech-sha256": "peu importe"}})
        return cle

    with patch.object(S3StorageClient, "upload_file", _envoi_puis_corruption):
        _brancher(magasin, monkeypatch)
        lot = upload_artifacts_to_storage(
            "dossier", [_fichier(tmp_path)], _config(tmp_path), import_id="IMP-1", source_root=tmp_path
        )
    assert lot.status != "uploaded"
    assert lot.all_verified is False
    assert not lot.artifacts[0].verified
    assert lot.artifacts[0].error


def test_relecture_desactivee_par_configuration_conserve_la_copie_locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin = S3EnMemoire()
    lot = _lot(tmp_path, magasin, monkeypatch, storage_verify_reread=False)
    assert lot.artifacts[0].verified is False
    assert lot.artifacts[0].verification == "metadata_seule"
    assert lot.all_verified is False, (
        "sans relecture, rien ne prouve les octets stockés : la purge locale doit être refusée"
    )
    assert "copie locale conservée" in (lot.artifacts[0].error or "")


def test_double_s3_retabli_apres_les_tests_precedents() -> None:
    """Une vraie suite réseau suivante ne doit pas parler à un double persistant."""
    assert S3StorageClient._get_client is _GET_CLIENT_ORIGINAL


@pytest.mark.parametrize('lecture_echoue', [False, True])
def test_relecture_ferme_flux_meme_en_cas_d_echec(tmp_path: Path, monkeypatch, lecture_echoue: bool) -> None:
    import io

    fichier = _fichier(tmp_path)
    contenu = fichier.read_bytes()
    class Flux(io.BytesIO):
        def read(self, taille=-1):
            if lecture_echoue:
                raise OSError('erreur synthétique')
            return super().read(taille)
    flux = Flux(contenu)
    client = _client(S3EnMemoire())
    monkeypatch.setattr(client, 'head_object', lambda cle: {'ContentLength': len(contenu)})
    monkeypatch.setattr(client, 'get_object', lambda cle: {'Body': flux})
    resultat = client.verifier_integrite('cle-synthetique', fichier)
    assert resultat.integrite_prouvee is not lecture_echoue
    assert flux.closed


def test_relecture_refuse_corps_qui_depasse_taille_annoncee(tmp_path: Path, monkeypatch) -> None:
    import io

    fichier = _fichier(tmp_path, contenu=b'court')
    class FluxInfini(io.BytesIO):
        appels = 0
        def read(self, taille=-1):
            self.appels += 1
            if self.appels > 3:
                raise AssertionError('lecture non bornée')
            return b'X' * max(1, taille)
    flux = FluxInfini()
    client = _client(S3EnMemoire())
    monkeypatch.setattr(client, 'head_object', lambda cle: {'ContentLength': 5})
    monkeypatch.setattr(client, 'get_object', lambda cle: {'Body': flux})
    resultat = client.verifier_integrite('cle-synthetique', fichier)
    assert not resultat.integrite_prouvee
    assert 'taille' in resultat.detail
    assert flux.appels <= 2 and flux.closed


def test_checksum_serveur_doit_etre_explicitement_demande(tmp_path: Path, monkeypatch) -> None:
    import base64
    import hashlib

    fichier = _fichier(tmp_path, contenu=b'checksum-synthetique')
    digest = base64.b64encode(hashlib.sha256(fichier.read_bytes()).digest()).decode('ascii')
    class Fournisseur:
        def __init__(self):
            self.parametres = []
        def head_object(self, **kwargs):
            self.parametres.append(kwargs)
            resultat = {'ContentLength': fichier.stat().st_size}
            if kwargs.get('ChecksumMode') == 'ENABLED':
                resultat.update(ChecksumSHA256=digest, ChecksumType='FULL_OBJECT')
            return resultat
    fournisseur = Fournisseur()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self: fournisseur)
    client = _client(S3EnMemoire())
    resultat = client.verifier_integrite('cle-synthetique', fichier, relire=False)
    assert fournisseur.parametres[0].get('ChecksumMode') == 'ENABLED'
    assert resultat.integrite_prouvee and resultat.methode == 'checksum_serveur'


def test_preuve_expose_empreinte_des_octets_reellement_relus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key='IMP-test/plan.pdf')
    resultat = client.verifier_integrite(cle, fichier)
    assert resultat.sha256 == hashlib.sha256(fichier.read_bytes()).hexdigest()
    magasin.corrompre(cle, tronquer_a=3)
    assert client.verifier_integrite(cle, fichier).sha256 is None


def test_empreinte_prouvee_est_persistee_dans_artefact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    fichier = _fichier(tmp_path)
    lot = upload_artifacts_to_storage('synthetique', [fichier], _config(tmp_path), import_id='test')
    assert lot.all_verified
    assert lot.artifacts[0].to_dict()['sha256'] == hashlib.sha256(fichier.read_bytes()).hexdigest()


def test_manifeste_conserve_empreinte_reellement_prouvee() -> None:
    from seamtech_search.import_pipeline import construire_manifeste

    empreinte = 'a' * 64
    payload = {
        'files': [{'path': '/source/test.txt', 'upload_status': 'uploaded', 'object_key': 'cle',
                   'object_bucket': 'bucket'}],
        'artifacts': [{'path': '/source/test.txt', 'status': 'uploaded', 'verified': True,
                       'key': 'cle', 'bucket': 'bucket', 'sha256': empreinte}],
    }
    assert construire_manifeste(payload)[0]['sha256'] == empreinte


def test_manifeste_omet_empreinte_si_preuve_n_est_plus_coherente() -> None:
    from seamtech_search.import_pipeline import construire_manifeste

    payload = {
        'files': [{'path': '/source/test.txt', 'upload_status': 'uploaded', 'object_key': 'autre-cle',
                   'object_bucket': 'bucket'}],
        'artifacts': [{'path': '/source/test.txt', 'status': 'uploaded', 'verified': True,
                       'key': 'cle', 'bucket': 'bucket', 'sha256': 'a' * 64}],
    }
    entree = construire_manifeste(payload)[0]
    assert not entree['verified']
    assert entree['sha256'] is None


def test_reprise_source_modifiee_avec_vraie_preuve_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from seamtech_search.import_pipeline import import_folder, retry_upload
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import StoragePreflightError

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'octets-synthetiques-avant')
    (source / 'b.txt').write_bytes(b'autre-fichier-synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = S3EnMemoire()
    _brancher(magasin, monkeypatch)
    envoyer_original = S3StorageClient.upload_file
    appels = 0
    def envoyer_avec_panne(self, *args, **kwargs):
        nonlocal appels
        appels += 1
        if appels == 2:
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer_original(self, *args, **kwargs)
    monkeypatch.setattr(S3StorageClient, 'upload_file', envoyer_avec_panne)
    initial = import_folder(source, cfg, index, import_id='preuve-sha')
    conserve = next(a for a in initial.artifacts if a['verified'])
    assert conserve['sha256']
    Path(conserve['path']).write_bytes(b'octets-synthetiques-apres')
    repris = retry_upload(index, cfg, 'preuve-sha')
    assert not repris['all_verified']
    index.close()


def test_reprise_ne_valide_pas_un_fichier_ajoute_apres_inventaire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from seamtech_search.import_pipeline import import_folder, retry_upload
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original-synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    _brancher(S3EnMemoire(), monkeypatch)
    initial = import_folder(source, cfg, index, import_id='inventaire-ajout')
    assert not initial.all_verified and initial.transport_verifie
    (source / 'nouveau.txt').write_bytes(b'nouveau-synthetique')
    assert not retry_upload(index, cfg, 'inventaire-ajout')['all_verified']
    index.close()


def test_reprise_ancienne_preuve_sans_sha_ne_valide_pas_la_source(tmp_path: Path) -> None:
    from seamtech_search import import_pipeline as pipeline

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'ancien.txt'
    fichier.write_bytes(b'ancien-contenu-synthetique')
    config = _config(tmp_path, database_path=tmp_path / 'index.db')
    record = {
        'source_path': str(source), 'status': 'needs_review',
        'files': [{'path': str(fichier), 'upload_status': 'uploaded', 'object_key': 'cle', 'object_bucket': 'bucket'}],
        'artifacts': [{'path': str(fichier), 'status': 'uploaded', 'key': 'cle', 'bucket': 'bucket', 'verified': True}],
    }
    with patch.object(pipeline, 'get_import', return_value=record), patch.object(pipeline, 'update_import'):
        resultat = pipeline.retry_upload(None, config, 'ancien')
    assert not resultat['all_verified']
    assert resultat['artifacts'][0]['verified']  # preuve historique conservée


def test_reprise_mutation_apres_observation_avant_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from seamtech_search import import_pipeline as pipeline
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'a.txt'
    fichier.write_bytes(b'avant-synthetique')
    config = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    _brancher(S3EnMemoire(), monkeypatch)
    initial = pipeline.import_folder(source, config, index, import_id='mutation-interstice')
    assert not initial.all_verified and initial.transport_verifie
    save = pipeline.update_import
    def modifier_puis_sauver(*args, **kwargs):
        fichier.write_bytes(b'apres-synthetique')
        return save(*args, **kwargs)
    monkeypatch.setattr(pipeline, 'update_import', modifier_puis_sauver)
    resultat = pipeline.retry_upload(index, config, 'mutation-interstice')
    assert not resultat['all_verified']
    index.close()


@pytest.mark.skipif(not hasattr(__import__('os'), 'mkfifo'), reason='FIFO POSIX requis')
def test_reprise_refuse_inventaire_avec_fifo_ajoute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from seamtech_search import import_pipeline as pipeline
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original-synthetique')
    config = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    _brancher(S3EnMemoire(), monkeypatch)
    initial = pipeline.import_folder(source, config, index, import_id='fifo-ajoute')
    assert not initial.all_verified and initial.transport_verifie
    os.mkfifo(source / 'ajout-special')
    assert not pipeline.retry_upload(index, config, 'fifo-ajoute')['all_verified']
    index.close()


@pytest.mark.postgres
def test_reprise_identite_source_postgres_vrai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from seamtech_search.import_pipeline import import_folder, retry_upload
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    if not os.environ.get('SEAMTECH_TEST_DATABASE_URL'):
        pytest.skip('PostgreSQL privé requis')
    nom, url = _creer_base_jetable()
    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'a.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db', database_url=url)
    index = SearchIndex(cfg.database_path, url)
    try:
        index.initialize(rebuild=True)
        _brancher(S3EnMemoire(), monkeypatch)
        initial = import_folder(source, cfg, index, import_id='identite-pg')
        assert not initial.all_verified and initial.transport_verifie
        stable = retry_upload(index, cfg, 'identite-pg')
        assert not stable['all_verified'] and stable['preservation']['transport_verifie']
        assert stable['preservation']['identite_locale_observee']
        fichier.write_bytes(b'modification-synthetique')
        resultat = retry_upload(index, cfg, 'identite-pg')
        assert not resultat['all_verified']
        assert resultat['preservation']['identite_locale_observee'] is False
        assert resultat['artifacts'][0]['sha256']
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_reprise_sans_snapshot_ni_liaison_catalogue_ne_declare_pas_succes_total(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.import_pipeline import import_folder, retry_upload
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'f.txt').write_bytes(b'synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    _brancher(S3EnMemoire(), monkeypatch)
    initial = import_folder(source, cfg, index, import_id='pas-de-snapshot')
    assert not initial.all_verified and initial.transport_verifie
    repris = retry_upload(index, cfg, 'pas-de-snapshot')
    assert not repris['all_verified']
    assert not repris['preservation']['integrite_prouvee']
    assert repris['preservation']['identite_locale_observee']
    assert repris['preservation']['transport_verifie']
    assert repris['upload_status'] == 'partial'
    assert repris['artifacts'][0]['verified']
    index.close()


def test_import_initial_sans_snapshot_ne_declare_pas_succes_global(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search.import_pipeline import import_folder
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'fichier-synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    _brancher(S3EnMemoire(), monkeypatch)
    resultat = import_folder(source, cfg, index, import_id='initial-sans-snapshot')
    assert not resultat.all_verified
    assert resultat.transport_verifie
    # « uploaded » = transport prouvé (tous les artefacts relus et comparés).
    # Le succès GLOBAL n'est toujours pas déclaré : `all_verified` reste faux
    # (pas de snapshot durable ni de liaison de catalogue garanties), ce qui
    # est précisément l'objet de ce test — le libellé d'envoi a changé, pas
    # l'exigence.
    assert resultat.upload_status == 'uploaded'
    assert all(art['verified'] for art in resultat.artifacts)
    index.close()


def test_import_transport_ne_valide_pas_un_lot_qui_omet_un_original(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search import import_pipeline as pipeline
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import UploadedArtifact

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'a-synthetique')
    (source / 'b.txt').write_bytes(b'b-synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    def envoyer_sous_ensemble(**kwargs):
        chemin = kwargs['files_to_upload'][0]
        return UploadBatch('uploaded', [UploadedArtifact(str(chemin), chemin.name, key='cle', bucket='bucket',
                                                         status='uploaded', verified=True, verification='relecture_sha256',
                                                         sha256=hashlib.sha256(chemin.read_bytes()).hexdigest())])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', envoyer_sous_ensemble)
    resultat = pipeline.import_folder(source, cfg, index, import_id='sous-ensemble')
    assert not resultat.transport_verifie
    assert not resultat.all_verified
    assert resultat.upload_status == 'partial'
    index.close()


def test_transport_ne_compte_pas_pour_preuve_un_drapeau_sans_sha(tmp_path: Path, monkeypatch) -> None:
    from seamtech_search import import_pipeline as pipeline
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import UploadedArtifact

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'synthetique')
    cfg = _config(tmp_path, database_path=tmp_path / 'index.db')
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    def upload_sans_sha(**kwargs):
        return UploadBatch('uploaded', [UploadedArtifact(str(f), f.name, key='cle', bucket='bucket',
                            status='uploaded', verified=True) for f in kwargs['files_to_upload']])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', upload_sans_sha)
    resultat = pipeline.import_folder(source, cfg, index, import_id='sans-sha')
    assert not resultat.transport_verifie
    assert not resultat.all_verified
    index.close()
