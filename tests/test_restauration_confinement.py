"""Restaurations : valider tout le bundle avant la moindre écriture."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from seamtech_search.sauvegarde import SauvegardeError, restaurer_fichiers_locaux, restaurer_objets_s3


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_corruption_tardive_ne_laisse_aucune_copie_locale(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    (bundle / "b").write_bytes(b"alteration")
    manifest = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"a")},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"b")},
    ]}}
    destination = tmp_path / "restore"
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifest, bundle, destination)
    assert not destination.exists() or not list(destination.rglob("*"))


@pytest.mark.parametrize("path_relatif", ["../secret", "/tmp/secret"])
def test_restaurer_refuse_chemin_hors_bundle(tmp_path: Path, path_relatif: str) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (tmp_path / "secret").write_bytes(b"secret")
    manifest = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": path_relatif, "sha256": _sha(b"secret")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifest, bundle, tmp_path / "out")


def test_restaurer_refuse_identifiant_malveillant(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    manifest = {"fichiers_locaux": {"copies": [
        {"id": "../escape", "path_relatif": "a", "sha256": _sha(b"a")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifest, bundle, tmp_path / "out")
    assert not (tmp_path / "escape" / "a").exists()


def test_lien_source_vers_exterieur_refuse(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    (bundle / "lien").symlink_to(secret)
    manifest = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "lien", "sha256": _sha(b"secret")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifest, bundle, tmp_path / "out")


def test_lien_destination_vers_exterieur_refuse(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    destination = tmp_path / "out"
    destination.mkdir()
    externe = tmp_path / "externe"
    externe.mkdir()
    (destination / "1").symlink_to(externe, target_is_directory=True)
    manifest = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"a")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifest, bundle, destination)
    assert not (externe / "a").exists()


def test_objets_corrompus_ne_provoquent_aucun_upload(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    (bundle / "b").write_bytes(b"alteration")
    manifest = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"a"), "object_key": "a"},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"b"), "object_key": "b"},
    ]}}

    class Client:
        appels = 0

        def upload_file(self, *args: object, **kwargs: object) -> str:
            self.appels += 1
            return "a"

    client = Client()
    with pytest.raises(SauvegardeError):
        restaurer_objets_s3(manifest, bundle, client)
    assert client.appels == 0


def test_cli_bundle_corrompu_ne_cree_pas_la_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from seamtech_search import sauvegarde

    (tmp_path / 'piece').write_bytes(b'alteration')
    manifest = {
        'dump': {'fichier': 'dump', 'sha256': 'non utilise'},
        'fichiers_locaux': {'copies': [{'id': 1, 'path_relatif': 'piece', 'sha256': _sha(b'original')}]},
    }
    chemin = tmp_path / 'manifest.json'
    chemin.write_text(json.dumps(manifest))
    appels = []
    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: None)
    monkeypatch.setattr(sauvegarde, 'restaurer', lambda *a, **k: appels.append(True))
    with pytest.raises(SauvegardeError):
        sauvegarde.main(['restaurer', '--manifeste', str(chemin), '--base-cible', 'postgresql://test/base',
                         '--fichiers-cible', str(tmp_path / 'out')])
    assert appels == []


def test_cli_objet_corrompu_ne_cree_pas_la_base_ni_fichiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from seamtech_search import sauvegarde

    (tmp_path / 'piece').write_bytes(b'valide')
    (tmp_path / 'objet').write_bytes(b'alteration')
    manifest = {
        'dump': {'fichier': 'dump', 'sha256': 'non utilise'},
        'fichiers_locaux': {'copies': [{'id': 1, 'path_relatif': 'piece', 'sha256': _sha(b'valide')}]},
        'objets_s3': {'copies': [{'id': 1, 'path_relatif': 'objet', 'sha256': _sha(b'original'), 'object_key': 'objet'}]},
    }
    chemin = tmp_path / 'manifest.json'
    chemin.write_text(json.dumps(manifest))
    appels = []
    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: None)
    monkeypatch.setattr(sauvegarde, 'restaurer', lambda *a, **k: appels.append(True))
    with pytest.raises(SauvegardeError):
        sauvegarde.main(['restaurer', '--manifeste', str(chemin), '--base-cible', 'postgresql://test/base',
                         '--fichiers-cible', str(tmp_path / 'out'), '--restaurer-objets'])
    assert appels == []
    assert not (tmp_path / 'out').exists()


def test_cli_restaure_fichier_depuis_manifeste_distant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from seamtech_search import sauvegarde

    manifest = {
        'dump': {'fichier': 'dump', 'cle_s3': 'dump', 'sha256': 'sans-objet'},
        'fichiers_locaux': {'copies': [{'id': 1, 'path_relatif': 'fichiers/1/piece.pdf',
                                        'sha256': _sha(b'piece'), 'cle_s3_sauvegarde': 'copie'}]},
    }
    class Client:
        objets = {'manifest': json.dumps(manifest).encode(), 'copie': b'piece'}
        def download_file(self, key: str, cible: Path) -> None:
            cible.write_bytes(self.objets[key])

    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: Client())
    monkeypatch.setattr(sauvegarde, 'restaurer', lambda *a, **k: {'base': 'base', 'duree_s': 0.0})
    assert sauvegarde.main(['restaurer', '--cle-manifeste', 'manifest', '--base-cible', 'postgresql://test/base',
                            '--fichiers-cible', str(tmp_path / 'out')]) == 0
    assert (tmp_path / 'out' / '1' / 'piece.pdf').read_bytes() == b'piece'


@pytest.mark.parametrize('chemin', ['../hors', '/tmp/hors', 'fichiers/../../hors'])
def test_telechargement_bundle_refuse_traversee_avant_ecriture(tmp_path: Path, chemin: str) -> None:
    from seamtech_search import sauvegarde

    class Client:
        def download_file(self, key: str, cible: Path) -> None:
            raise AssertionError('aucun téléchargement attendu')
    manifest = {'fichiers_locaux': {'copies': [{'path_relatif': chemin, 'cle_s3_sauvegarde': 'objet'}]}}
    with pytest.raises(SauvegardeError):
        sauvegarde._telecharger_bundle_distant(manifest, Client(), tmp_path, ('fichiers_locaux',))


def test_cli_piece_distante_alteree_ne_cree_pas_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from seamtech_search import sauvegarde

    manifest = {'dump': {'fichier': 'dump', 'cle_s3': 'dump', 'sha256': 'sans-objet'},
                'fichiers_locaux': {'copies': [{'id': 1, 'path_relatif': 'fichier',
                                               'sha256': _sha(b'correct'), 'cle_s3_sauvegarde': 'copie'}]}}
    class Client:
        def download_file(self, key: str, cible: Path) -> None:
            cible.write_bytes(json.dumps(manifest).encode() if key == 'manifest' else b'altere')

    appels = []
    monkeypatch.setattr(sauvegarde, '_client_s3_depuis_env', lambda: Client())
    monkeypatch.setattr(sauvegarde, 'restaurer', lambda *a, **k: appels.append(True))
    with pytest.raises(SauvegardeError):
        sauvegarde.main(['restaurer', '--cle-manifeste', 'manifest', '--base-cible', 'postgresql://test/base',
                         '--fichiers-cible', str(tmp_path / 'out')])
    assert appels == []
    assert not (tmp_path / 'out').exists()


def test_restaurer_fichier_ne_copie_pas_octets_substitues(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = bundle / "piece"
    source.write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original")},
    ]}}
    copier = sauvegarde._ecrire_copie_confinee
    def substituer(source_path, cible, empreinte):
        source.write_bytes(b"substitue")
        return copier(source_path, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", substituer)
    destination = tmp_path / "restauration"
    sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, destination)
    assert (destination / "1" / "piece").read_bytes() == b"original"


def test_restaurer_objet_ne_transfere_pas_octets_substitues(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = bundle / "piece"
    source.write_bytes(b"original")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class Client:
        contenu = None
        def creer_fichier_exclusif(self, chemin, cle, **_kwargs):
            source.write_bytes(b"substitue")
            self.contenu = Path(chemin).read_bytes()
            return cle
        def download_file(self, _cle, chemin):
            Path(chemin).write_bytes(self.contenu)
    client = Client()
    restaurer_objets_s3(manifeste, bundle, client)
    assert client.contenu == b"original"


def test_restaurer_ne_remplace_pas_fichier_preexistant(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    cible = tmp_path / "restauration" / "1" / "piece"
    cible.parent.mkdir(parents=True)
    cible.write_bytes(b"temoin-preexistant")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde")},
    ]}}
    try:
        restaurer_fichiers_locaux(manifeste, bundle, cible.parent.parent)
    except SauvegardeError:
        pass
    assert cible.read_bytes() == b"temoin-preexistant"


def test_fichier_cree_apres_precontrole_n_est_pas_ecrase(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    cible = tmp_path / "out" / "1" / "piece"
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde")},
    ]}}
    ecrire = sauvegarde._ecrire_copie_confinee
    def concurrent(source, destination, empreinte):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"temoin")
        return ecrire(source, destination, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", concurrent)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert cible.read_bytes() == b"temoin"


def test_lien_cree_apres_precontrole_ne_redirige_pas_ecriture(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    externe = tmp_path / "externe"
    externe.mkdir()
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde")},
    ]}}
    ecrire = sauvegarde._ecrire_copie_confinee
    def concurrent(source, destination, empreinte):
        destination.parent.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.symlink_to(externe, target_is_directory=True)
        return ecrire(source, destination, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", concurrent)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not list(externe.iterdir())


def test_echec_staging_tardif_avant_toute_ecriture(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for nom in ("a", "b"):
        (bundle / nom).write_bytes(nom.encode())
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"a")},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"b")},
    ]}}
    original = sauvegarde._instantane_bundle_prive
    @contextmanager
    def alterer(racine, source):
        with original(racine, source) as copie:
            if source.name == "b":
                copie.write_bytes(b"alteration")
            yield copie
    monkeypatch.setattr(sauvegarde, "_instantane_bundle_prive", alterer)
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    with pytest.raises(SauvegardeError, match="empreinte"):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob("seamtech-restore-copy-*"))


def test_cli_fichiers_figes_avant_creation_base(tmp_path, monkeypatch):
    import json

    from seamtech_search import sauvegarde

    source = tmp_path / "piece"
    source.write_bytes(b"original")
    manifeste = {
        "dump": {"fichier": "dump", "sha256": "sans-objet"},
        "fichiers_locaux": {"copies": [{"id": 1, "path_relatif": "piece", "sha256": _sha(b"original")}]},
    }
    chemin = tmp_path / "manifest.json"
    chemin.write_text(json.dumps(manifeste))
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    def restaurer_base(*_a, **_k):
        source.write_bytes(b"substitue")
        return {"base": "base", "duree_s": 0.0}
    monkeypatch.setattr(sauvegarde, "restaurer", restaurer_base)
    assert sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", "postgresql://test/base",
                            "--fichiers-cible", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "1" / "piece").read_bytes() == b"original"


def test_erreur_copie_supprime_fichier_partiel_sans_effacer_tiers(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original")},
    ]}}
    copier = sauvegarde.shutil.copyfileobj
    compteur = 0
    def panne_sur_publication(source, destination, *args, **kwargs):
        nonlocal compteur
        compteur += 1
        if compteur == 2:
            destination.write(b"partiel")
            raise OSError("panne-synthetique")
        return copier(source, destination, *args, **kwargs)
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", panne_sur_publication)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out" / "1" / "piece").exists()


@pytest.mark.xfail(strict=True, reason="La publication multi-fichiers n'est pas transactionnelle")
def test_erreur_seconde_publication_ne_laisse_pas_premiere(tmp_path, monkeypatch):
    import os
    if os.open not in os.supports_dir_fd:
        pytest.skip("lecture confinée du bundle refusée sur cette plateforme : "
                    "le produit n'ouvre rien avant de refuser, donc ce scénario "
                    "n'a pas lieu d'être mesuré — démontré, sans Windows, par "
                    "tests/test_plateforme_sans_dir_fd.py")
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for nom in ("a", "b"):
        (bundle / nom).write_bytes(nom.encode())
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"a")},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"b")},
    ]}}
    ecrire = sauvegarde._ecrire_copie_confinee
    def echec_seconde(source, cible, empreinte):
        if cible.name == "b":
            raise SauvegardeError("panne-synthetique")
        return ecrire(source, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", echec_seconde)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out" / "1" / "a").exists()


def test_objet_existant_n_est_pas_ecrase(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde"), "object_key": "cle"},
    ]}}
    class Client:
        contenu = b"temoin"
        def upload_file(self, chemin, cle, **_kwargs):
            self.contenu = Path(chemin).read_bytes()
            return cle
    client = Client()
    try:
        restaurer_objets_s3(manifeste, bundle, client)
    except SauvegardeError:
        pass
    assert client.contenu == b"temoin"


def test_deux_restaurations_concurrentes_necrasent_pas(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original")},
    ]}}
    barriere = Barrier(2, timeout=5)
    ecrire = sauvegarde._ecrire_copie_confinee
    def ecrire_ensemble(source, cible, empreinte):
        barriere.wait()
        return ecrire(source, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", ecrire_ensemble)
    def executer():
        try:
            sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
            return "ok"
        except SauvegardeError:
            return "refus"
    with ThreadPoolExecutor(max_workers=2) as pool:
        resultats = list(pool.map(lambda _: executer(), range(2)))
    assert sorted(resultats) == ["ok", "refus"]
    assert (tmp_path / "out" / "1" / "piece").read_bytes() == b"original"


def test_objet_altere_par_transport_ne_peut_pas_etre_signale_restaure(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class Client:
        contenu = b""
        def creer_fichier_exclusif(self, _chemin, cle, **_kwargs):
            self.contenu = b"tronque"
            return cle
        def download_file(self, _cle, chemin):
            Path(chemin).write_bytes(self.contenu)
    with pytest.raises(SauvegardeError, match="empreinte"):
        restaurer_objets_s3(manifeste, bundle, Client())
    assert not list(tmp_path.glob("seamtech-restore-readback-*"))
    assert not list(tmp_path.glob("seamtech-restore-copy-*"))


def test_relecture_objet_echoue_sans_divulgation_et_nettoie(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class Client:
        def creer_fichier_exclusif(self, _chemin, cle, **_kwargs):
            return cle
        def download_file(self, _cle, _chemin):
            raise OSError("jeton-synthetique-sensible")
    with pytest.raises(SauvegardeError) as erreur:
        sauvegarde.restaurer_objets_s3(manifeste, bundle, Client())
    assert "jeton-synthetique-sensible" not in str(erreur.value)
    assert not list(tmp_path.glob("seamtech-restore-readback-*"))
    assert not list(tmp_path.glob("seamtech-restore-copy-*"))


def test_objet_deplace_vers_autre_cle_ne_peut_pas_etre_signale_restaure(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class Client:
        def creer_fichier_exclusif(self, _chemin, _cle, **_kwargs):
            return "cle-differente"
        def download_file(self, *_args):
            pytest.fail("ne doit pas lire une clé déplacée")
    with pytest.raises(SauvegardeError, match="clé"):
        restaurer_objets_s3(manifeste, bundle, Client())


def test_objet_s3_prefixe_divergent_refuse_avant_ecriture(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class Client:
        appels = 0
        def _apply_prefix(self, cle):
            return "autre-prefixe/" + cle
        def upload_file(self, _chemin, _cle, **_kwargs):
            self.appels += 1
            return "autre-prefixe/cle"
    client = Client()
    with pytest.raises(SauvegardeError):
        restaurer_objets_s3(manifeste, bundle, client)
    assert client.appels == 0


def test_cli_prefixe_different_refuse_avant_creation_base(tmp_path, monkeypatch):
    import json

    from seamtech_search import sauvegarde, storage
    from seamtech_search.config import AppConfig

    (tmp_path / "piece").write_bytes(b"original")
    manifeste = {"dump": {"fichier": "dump", "sha256": "sans-objet"}, "objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    chemin = tmp_path / "manifeste.json"
    chemin.write_text(json.dumps(manifeste))
    class Client:
        def __init__(self, **_kwargs): pass
        def _apply_prefix(self, cle): return "autre/" + cle
    appels = []
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(AppConfig, "load", lambda: object())
    monkeypatch.setattr(storage, "S3StorageClient", Client)
    monkeypatch.setattr(sauvegarde, "restaurer", lambda *_a, **_k: appels.append(True))
    with pytest.raises(SauvegardeError, match="préfixe"):
        sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", "postgresql://test/base",
                         "--restaurer-objets"])
    assert appels == []


def test_substitution_dossier_source_apres_precontrole_ne_sort_pas_du_bundle(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    parent = bundle / "dossier"
    parent.mkdir(parents=True)
    (parent / "piece").write_bytes(b"original")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "piece").write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "dossier/piece", "sha256": _sha(b"original")},
    ]}}
    original = sauvegarde._instantane_bundle_prive
    @contextmanager
    def substituer(racine, chemin):
        parent.rename(bundle / "dossier-original")
        parent.symlink_to(externe, target_is_directory=True)
        with original(racine, chemin) as copie:
            yield copie
    monkeypatch.setattr(sauvegarde, "_instantane_bundle_prive", substituer)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_substitution_dossier_avant_hash_initial_ne_sort_pas_du_bundle(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    parent = bundle / "dossier"
    parent.mkdir(parents=True)
    (parent / "piece").write_bytes(b"original")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "piece").write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "dossier/piece", "sha256": _sha(b"original")},
    ]}}
    ouvrir = sauvegarde._ouvrir_source_bundle
    @contextmanager
    def substitution(racine, relatif):
        parent.rename(bundle / "dossier-original")
        parent.symlink_to(externe, target_is_directory=True)
        with ouvrir(racine, relatif) as flux:
            yield flux
    monkeypatch.setattr(sauvegarde, "_ouvrir_source_bundle", substitution)
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_lien_source_interne_refuse_meme_si_reste_dans_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "original").write_bytes(b"original")
    (bundle / "lien").symlink_to(bundle / "original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "lien", "sha256": _sha(b"original")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="FIFO indisponible")
def test_source_bundle_fifo_refusee_sans_blocage(tmp_path):
    import os

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    os.mkfifo(bundle / "fifo")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "fifo", "sha256": _sha(b"original")},
    ]}}
    with pytest.raises(SauvegardeError):
        restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")


def test_racine_bundle_substituee_entre_plan_et_staging_est_refusee(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "piece").write_bytes(b"original")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original")},
    ]}}
    figer = sauvegarde._figer_plan_restauration
    @contextmanager
    def substituer(plan, racine):
        bundle.rename(tmp_path / "bundle-original")
        bundle.symlink_to(externe, target_is_directory=True)
        with figer(plan, racine) as fige:
            yield fige
    monkeypatch.setattr(sauvegarde, "_figer_plan_restauration", substituer)
    with pytest.raises(SauvegardeError, match="racine"):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_cli_objet_dossier_substitue_refuse_avant_creation_base(tmp_path, monkeypatch):
    import json
    from contextlib import contextmanager

    from seamtech_search import sauvegarde

    parent = tmp_path / "dossier"
    parent.mkdir()
    (parent / "piece").write_bytes(b"original")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "piece").write_bytes(b"original")
    manifeste = {"dump": {"fichier": "dump", "sha256": "sans-objet"}, "objets_s3": {"copies": [
        {"id": 1, "path_relatif": "dossier/piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    chemin = tmp_path / "manifeste.json"
    chemin.write_text(json.dumps(manifeste))
    original = sauvegarde._instantane_bundle_prive
    @contextmanager
    def substitution(racine, chemin_source):
        parent.rename(tmp_path / "dossier-original")
        parent.symlink_to(externe, target_is_directory=True)
        with original(racine, chemin_source) as copie:
            yield copie
    appels = []
    monkeypatch.setattr(sauvegarde, "_instantane_bundle_prive", substitution)
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(sauvegarde, "restaurer", lambda *_a, **_k: appels.append(True))
    with pytest.raises(SauvegardeError):
        sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", "postgresql://test/base",
                         "--restaurer-objets"])
    assert appels == []


@pytest.mark.parametrize("nom", ["../hors.dump", "/tmp/hors.dump", "dossier/interne.dump", "dossier\\dump", "", ".", "..", "a\x00b", None, 123])
def test_cli_refuse_nom_dump_non_local_avant_restauration(tmp_path, monkeypatch, nom):
    import json

    from seamtech_search import sauvegarde

    manifeste = tmp_path / "manifeste.json"
    manifeste.write_text(json.dumps({"dump": {"fichier": nom}}))
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(sauvegarde, "restaurer", lambda *_a, **_k: pytest.fail("aucune restauration hors bundle"))
    with pytest.raises(SauvegardeError, match="nom du dump"):
        sauvegarde.main(["restaurer", "--manifeste", str(manifeste), "--base-cible", "postgresql://u@localhost/cible"])


def test_copie_dump_refuse_parent_lien_symbolique(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "dump").write_bytes(b"exterieur-synthetique")
    lien = tmp_path / "bundle"
    lien.symlink_to(externe, target_is_directory=True)
    with pytest.raises(SauvegardeError):
        with sauvegarde._instantane_dump_prive(lien / "dump"):
            pytest.fail("lecture via répertoire substitué")


def test_copie_dump_refuse_parent_substitue_au_moment_de_l_ouverture(tmp_path, monkeypatch):
    import os
    if os.open not in os.supports_dir_fd:
        pytest.skip("lecture confinée du bundle refusée sur cette plateforme : "
                    "le produit n'ouvre rien avant de refuser, donc ce scénario "
                    "n'a pas lieu d'être mesuré — démontré, sans Windows, par "
                    "tests/test_plateforme_sans_dir_fd.py")

    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    dump = bundle / "dump"
    dump.write_bytes(b"original")
    externe = tmp_path / "externe"
    externe.mkdir()
    (externe / "dump").write_bytes(b"exterieur")
    original_open = os.open
    change = False
    def ouvrir(chemin, flags, *args, **kwargs):
        nonlocal change
        if not change and (os.fspath(chemin) == str(dump) or (chemin == "bundle" and "dir_fd" in kwargs)):
            change = True
            bundle.rename(tmp_path / "ancien")
            bundle.symlink_to(externe, target_is_directory=True)
        return original_open(chemin, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", ouvrir)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {ouvrir})
    with pytest.raises(SauvegardeError):
        with sauvegarde._instantane_dump_prive(dump):
            pytest.fail("lecture par répertoire substitué")
    assert change
    assert (externe / "dump").read_bytes() == b"exterieur"


def test_cli_dump_explicite_n_utilise_pas_nom_malveillant_du_manifeste(tmp_path, monkeypatch):
    import json

    from seamtech_search import sauvegarde

    manifeste = tmp_path / "manifeste.json"
    manifeste.write_text(json.dumps({"dump": {"fichier": "../hors.dump"}}))
    dump_explicite = tmp_path / "choisi.dump"
    appels = []
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    def restaure(_base, _manifeste, chemin_dump_local, **_kwargs):
        appels.append(chemin_dump_local)
        return {"base": "cible", "duree_s": 0.0}
    monkeypatch.setattr(sauvegarde, "restaurer", restaure)
    assert sauvegarde.main(["restaurer", "--manifeste", str(manifeste), "--dump", str(dump_explicite),
                            "--base-cible", "postgresql://u@localhost/cible"]) == 0
    assert appels == [dump_explicite]


def test_publication_locale_non_visible_pendant_ecriture(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"donnees-originales")
    cible = tmp_path / "destination" / "piece"
    def copier(entree, sortie, **_kwargs):
        sortie.write(b"donnees-")
        sortie.flush()
        assert not cible.exists(), "un fichier partiel ne doit pas être visible sous le nom final"
        sortie.write(entree.read()[len(b"donnees-"):])
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", copier)
    sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
    assert cible.read_bytes() == b"donnees-originales"
    assert sorted(p.name for p in cible.parent.iterdir()) == ["piece"]


def test_publication_locale_interrompue_ne_laisse_pas_de_fichier(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"donnees-originales")
    cible = tmp_path / "destination" / "piece"
    def interrompre(_entree, sortie, **_kwargs):
        sortie.write(b"partiel")
        raise KeyboardInterrupt
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", interrompre)
    with pytest.raises(KeyboardInterrupt):
        sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
    assert not cible.exists()
    assert not list(cible.parent.iterdir())


def test_publication_locale_concurrente_ne_remplace_pas_la_cible(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"donnees-originales")
    cible = tmp_path / "destination" / "piece"
    copier = sauvegarde.shutil.copyfileobj
    def concurrent(entree, sortie, **kwargs):
        copier(entree, sortie, **kwargs)
        cible.write_bytes(b"autre-auteur")
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", concurrent)
    with pytest.raises(SauvegardeError):
        sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
    assert cible.read_bytes() == b"autre-auteur"
    assert sorted(p.name for p in cible.parent.iterdir()) == ["piece"]


def test_publication_locale_sigkill_ne_publie_pas_fichier_partiel(tmp_path):
    import multiprocessing
    import os
    import signal

    from seamtech_search import sauvegarde

    if os.name != "posix":
        pytest.skip("SIGKILL POSIX requis")
    source = tmp_path / "source"
    source.write_bytes(b"donnees-originales")
    cible = tmp_path / "destination" / "piece"
    contexte = multiprocessing.get_context("fork")
    lecture, ecriture = contexte.Pipe(duplex=False)
    def travail():
        def interrompre(_entree, sortie, **_kwargs):
            sortie.write(b"partiel")
            sortie.flush()
            ecriture.send(True)
            signal.pause()
        sauvegarde.shutil.copyfileobj = interrompre
        sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
    processus = contexte.Process(target=travail)
    processus.start()
    try:
        assert lecture.poll(10), "processus de test non arrivé à l'écriture"
        assert lecture.recv() is True
        assert not cible.exists()
        os.kill(processus.pid, signal.SIGKILL)
        processus.join(10)
        assert processus.exitcode == -signal.SIGKILL
        assert not cible.exists()
        restes = list(cible.parent.iterdir())
        assert len(restes) == 1 and restes[0].name.startswith(".seamtech-restore-")
    finally:
        if processus.is_alive():
            processus.kill()
            processus.join(10)
        lecture.close()
        ecriture.close()


def test_deux_processus_publication_meme_cible_un_seul_gagne(tmp_path):
    import multiprocessing
    import os

    from seamtech_search import sauvegarde

    if os.name != "posix":
        pytest.skip("fork POSIX requis")
    sources = [tmp_path / "source-a", tmp_path / "source-b"]
    for fichier, contenu in zip(sources, (b"a" * 1024, b"b" * 1024), strict=True):
        fichier.write_bytes(contenu)
    cible = tmp_path / "destination" / "piece"
    contexte = multiprocessing.get_context("fork")
    barriere = contexte.Barrier(2)
    resultats = contexte.Queue()
    def travail(source):
        try:
            barriere.wait(timeout=10)
            sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
            resultats.put(("ok", source.name))
        except SauvegardeError:
            resultats.put(("refuse", source.name))
    processus = [contexte.Process(target=travail, args=(source,)) for source in sources]
    try:
        for p in processus:
            p.start()
        resultats_recus = [resultats.get(timeout=15) for _ in processus]
        for p in processus:
            p.join(10)
            assert p.exitcode == 0
        assert sorted(resultat for resultat, _ in resultats_recus) == ["ok", "refuse"]
        gagnant = next(nom for resultat, nom in resultats_recus if resultat == "ok")
        assert cible.read_bytes() == (tmp_path / gagnant).read_bytes()
        assert sorted(p.name for p in cible.parent.iterdir()) == ["piece"]
    finally:
        for p in processus:
            if p.is_alive():
                p.kill()
                p.join(10)
        resultats.close()


def test_publication_locale_lien_dur_refuse_sans_fichier_cible(tmp_path, monkeypatch):
    import errno
    import os

    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(b"contenu")
    cible = tmp_path / "destination" / "piece"
    def lien_non_supporte(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "non pris en charge")
    monkeypatch.setattr(os, "link", lien_non_supporte)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {lien_non_supporte})
    monkeypatch.setattr(os, "supports_follow_symlinks", os.supports_follow_symlinks | {lien_non_supporte})
    with pytest.raises(SauvegardeError):
        sauvegarde._ecrire_copie_confinee(source, cible, _sha(source.read_bytes()))
    assert not cible.exists()
    assert not list(cible.parent.iterdir())


def test_fichier_local_tronque_ne_peut_pas_etre_signale_restaure(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original-synthetique")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original-synthetique")},
    ]}}
    copier = sauvegarde.shutil.copyfileobj
    appels = 0
    def transport_tronque(entree, sortie, **kwargs):
        nonlocal appels
        appels += 1
        if appels == 2:
            sortie.write(entree.read(3))
            return
        return copier(entree, sortie, **kwargs)
    monkeypatch.setattr(sauvegarde.shutil, "copyfileobj", transport_tronque)
    with pytest.raises(SauvegardeError, match="empreinte"):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out" / "1" / "piece").exists()
    assert not list((tmp_path / "out" / "1").glob(".seamtech-restore-*"))


def test_copie_privee_modifiee_apres_staging_ne_peut_pas_etre_publiee(tmp_path, monkeypatch):
    from seamtech_search import sauvegarde

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original-synthetique")
    manifeste = {"fichiers_locaux": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original-synthetique")},
    ]}}
    ecrire = sauvegarde._ecrire_copie_confinee
    def alterer_copie_privee(source, cible, empreinte):
        source.write_bytes(b"substitue-synthetique")
        return ecrire(source, cible, empreinte)
    monkeypatch.setattr(sauvegarde, "_ecrire_copie_confinee", alterer_copie_privee)
    with pytest.raises(SauvegardeError, match="empreinte"):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, tmp_path / "out")
    assert not (tmp_path / "out" / "1" / "piece").exists()


@pytest.mark.parametrize("contenu", [b"", b"z" * (1024 * 1024 + 7)])
def test_relecture_locale_accepte_fichier_vide_ou_plusieurs_blocs(tmp_path, contenu):
    from seamtech_search import sauvegarde

    source = tmp_path / "source"
    source.write_bytes(contenu)
    cible = tmp_path / "out" / "piece"
    sauvegarde._ecrire_copie_confinee(source, cible, _sha(contenu))
    assert cible.read_bytes() == contenu


def test_objets_meme_cle_contenus_differents_refuses_avant_ecriture(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"premier")
    (bundle / "b").write_bytes(b"second")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"premier"), "object_key": "meme-cle"},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"second"), "object_key": "meme-cle"},
    ]}}
    class Client:
        appels = []
        def upload_file(self, chemin, cle, **_kwargs):
            self.appels.append((chemin, cle))
            return cle
        def download_file(self, _cle, chemin):
            chemin.write_bytes(Path(self.appels[-1][0]).read_bytes())
    client = Client()
    with pytest.raises(SauvegardeError, match="clé objet"):
        restaurer_objets_s3(manifeste, bundle, client)
    assert client.appels == []


def test_objets_meme_cle_et_meme_contenu_envoyes_une_seule_fois(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"identique")
    (bundle / "b").write_bytes(b"identique")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "a", "sha256": _sha(b"identique"), "object_key": "meme-cle"},
        {"id": 2, "path_relatif": "b", "sha256": _sha(b"identique"), "object_key": "meme-cle"},
    ]}}
    class Client:
        appels = 0
        def creer_fichier_exclusif(self, chemin, cle, **_kwargs):
            self.appels += 1
            self.contenu = Path(chemin).read_bytes()
            return cle
        def download_file(self, _cle, chemin):
            chemin.write_bytes(self.contenu)
    client = Client()
    resultat = restaurer_objets_s3(manifeste, bundle, client)
    assert resultat["nb"] == 2  # deux références, une seule clé physique
    assert client.appels == 1
    assert client.contenu == b"identique"


def test_cli_cle_objet_conflictuelle_refuse_avant_base_et_fichiers(tmp_path, monkeypatch):
    import json

    from seamtech_search import sauvegarde

    (tmp_path / "a").write_bytes(b"premier")
    (tmp_path / "b").write_bytes(b"second")
    (tmp_path / "local").write_bytes(b"local")
    manifeste = {
        "dump": {"fichier": "dump"},
        "fichiers_locaux": {"copies": [{"id": 1, "path_relatif": "local", "sha256": _sha(b"local")}]},
        "objets_s3": {"copies": [
            {"id": 1, "path_relatif": "a", "sha256": _sha(b"premier"), "object_key": "meme-cle"},
            {"id": 2, "path_relatif": "b", "sha256": _sha(b"second"), "object_key": "meme-cle"},
        ]},
    }
    chemin = tmp_path / "manifest.json"
    chemin.write_text(json.dumps(manifeste))
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(sauvegarde, "restaurer", lambda *_a, **_k: pytest.fail("aucune création de base"))
    with pytest.raises(SauvegardeError, match="clé objet"):
        sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", "postgresql://u@localhost/cible",
                         "--fichiers-cible", str(tmp_path / "out"), "--restaurer-objets"])
    assert not (tmp_path / "out").exists()


def test_dump_avec_parent_et_lien_ne_lit_pas_autre_fichier(tmp_path):
    from seamtech_search import sauvegarde

    base = tmp_path / "base"
    base.mkdir()
    externe = tmp_path / "externe"
    (externe / "dossier").mkdir(parents=True)
    (base / "dump").write_bytes(b"mauvais-dump")
    (externe / "dump").write_bytes(b"dump-attendu")
    (base / "lien").symlink_to(externe / "dossier", target_is_directory=True)
    chemin = base / "lien" / ".." / "dump"
    assert chemin.read_bytes() == b"dump-attendu"
    with pytest.raises(SauvegardeError):
        with sauvegarde._instantane_dump_prive(chemin):
            pytest.fail("le chemin ambigu ne doit pas être normalisé silencieusement")


def test_restaurer_objet_refuse_client_sans_creation_exclusive(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde"), "object_key": "cle"},
    ]}}
    class Client:
        appels = 0
        def upload_file(self, _source, cle, **_options):
            self.appels += 1
            return cle
        def download_file(self, _cle, destination):
            destination.write_bytes(b"sauvegarde")
    client = Client()
    with pytest.raises(SauvegardeError, match="exclusive"):
        restaurer_objets_s3(manifeste, bundle, client)
    assert client.appels == 0


def test_cli_refuse_stockage_non_exclusif_avant_base_et_fichiers(tmp_path, monkeypatch):
    import json

    from seamtech_search import sauvegarde, storage
    from seamtech_search.config import AppConfig

    (tmp_path / "objet").write_bytes(b"objet")
    (tmp_path / "local").write_bytes(b"local")
    manifeste = {
        "dump": {"fichier": "dump"},
        "fichiers_locaux": {"copies": [{"id": 1, "path_relatif": "local", "sha256": _sha(b"local")}]},
        "objets_s3": {"copies": [{"id": 1, "path_relatif": "objet", "sha256": _sha(b"objet"), "object_key": "cle"}]},
    }
    chemin = tmp_path / "manifeste.json"
    chemin.write_text(json.dumps(manifeste))
    class Client:
        def __init__(self, **_kwargs): pass
        def upload_file(self, *_a, **_k): pytest.fail("écriture objet interdite")
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(AppConfig, "load", lambda: object())
    monkeypatch.setattr(storage, "S3StorageClient", Client)
    monkeypatch.setattr(sauvegarde, "restaurer", lambda *_a, **_k: pytest.fail("création base interdite"))
    with pytest.raises(SauvegardeError, match="exclusive"):
        sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", "postgresql://u@localhost/cible",
                         "--fichiers-cible", str(tmp_path / "out"), "--restaurer-objets"])
    assert not (tmp_path / "out").exists()


def test_deux_restaurations_objet_concurrentes_avec_adaptateur_atomique_simule(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"original")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"original"), "object_key": "cle"},
    ]}}
    class MagasinAtomiqueSimule:
        def __init__(self):
            self.verrou = Lock()
            self.barriere = Barrier(2)
            self.objets = {}
        def creer_fichier_exclusif(self, source, cle):
            self.barriere.wait(timeout=5)
            with self.verrou:
                if cle in self.objets:
                    raise FileExistsError("conflit")
                self.objets[cle] = source.read_bytes()
            return cle
        def download_file(self, cle, destination):
            destination.write_bytes(self.objets[cle])
    client = MagasinAtomiqueSimule()
    def executer(_):
        try:
            restaurer_objets_s3(manifeste, bundle, client)
            return "ok"
        except SauvegardeError:
            return "refus"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(executer, range(2))) == ["ok", "refus"]
    assert client.objets == {"cle": b"original"}


def test_adaptateur_exclusif_simule_refuse_une_cle_deja_presente(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "piece").write_bytes(b"sauvegarde")
    manifeste = {"objets_s3": {"copies": [
        {"id": 1, "path_relatif": "piece", "sha256": _sha(b"sauvegarde"), "object_key": "cle"},
    ]}}
    class Magasin:
        objets = {"cle": b"temoin"}
        def creer_fichier_exclusif(self, source, cle):
            if cle in self.objets:
                raise FileExistsError("chemin-ou-secret-synthetique")
            self.objets[cle] = source.read_bytes()
            return cle
    magasin = Magasin()
    with pytest.raises(SauvegardeError, match="exclusive") as erreur:
        restaurer_objets_s3(manifeste, bundle, magasin)
    assert "chemin-ou-secret-synthetique" not in str(erreur.value)
    assert magasin.objets == {"cle": b"temoin"}
