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

from pathlib import Path
from unittest.mock import patch

from seamtech_search.config import AppConfig
from seamtech_search.storage import (
    S3StorageClient,
    UploadBatch,
    upload_artifacts_to_storage,
)
from tests.s3_en_memoire import S3EnMemoire


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


class _MagasinPatch:
    """Context manager : le client réel parle au magasin en mémoire."""

    def __init__(self, magasin: S3EnMemoire) -> None:
        self.magasin = magasin
        self._patchs: list = []

    def __enter__(self) -> S3EnMemoire:
        self._patchs = [
            patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: self.magasin_ref),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
            patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
        ]
        return self.magasin

    def __exit__(self, *_exc: object) -> None:
        for p in self._patchs:
            p.stop()


def _brancher(magasin: S3EnMemoire) -> None:
    """Branche `S3StorageClient` sur le magasin : les méthodes RÉELLES tournent."""
    patcheurs = [
        patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
        patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
    ]
    for p in patcheurs:
        p.start()


# ---------------------------------------------------------------------------
# 1. La relecture prouve les octets ; la métadonnée ne prouve rien
# ---------------------------------------------------------------------------


def test_relecture_recalcule_l_empreinte_sur_les_octets_stockes(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    resultat = client.verifier_integrite(cle, fichier, relire=True)
    assert resultat.integrite_prouvee is True
    assert resultat.methode == "relecture_sha256"
    # Le fournisseur a bien été prié de valider l'empreinte à l'ingestion.
    assert magasin.envois[0]["extra"].get("ChecksumAlgorithm") == "SHA256"


def test_objet_tronque_avec_metadonnee_intacte_est_detecte(tmp_path: Path) -> None:
    """LE test qui distingue vérification et déclaration.

    L'objet stocké est tronqué, mais la métadonnée ``seamtech-sha256`` envoyée
    par l'application est toujours là et correspond au fichier local. Une
    vérification qui se contenterait de comparer cette métadonnée déclarerait
    l'objet conforme — et supprimerait la copie locale d'un fichier corrompu.
    La relecture, elle, recalcule l'empreinte et refuse.
    """
    magasin = S3EnMemoire()
    _brancher(magasin)
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


def test_objet_altere_de_meme_taille_est_detecte_par_l_empreinte(tmp_path: Path) -> None:
    """Corruption « invisible » : mêmes octets en nombre, contenu différent."""
    magasin = S3EnMemoire()
    _brancher(magasin)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    donnees = magasin.objets[cle]["donnees"]
    magasin.corrompre(cle, octets=b"X" * len(donnees))

    resultat = client.verifier_integrite(cle, fichier, relire=True)
    assert resultat.integrite_prouvee is False
    assert "relecture: sha256 different" in resultat.detail


def test_objet_absent_est_un_echec(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin)
    fichier = _fichier(tmp_path)
    resultat = _client(magasin).verifier_integrite("IMP-1/absent.pdf", fichier, relire=True)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "echec"


# ---------------------------------------------------------------------------
# 2. Quand la relecture est désactivée : métadonnée ≠ preuve
# ---------------------------------------------------------------------------


def test_metadonnee_seule_ne_prouve_pas_et_ne_permet_pas_la_purge(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin)
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


def test_taille_seule_ne_prouve_pas_et_ne_permet_pas_la_purge(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    _brancher(magasin)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")
    magasin.objets[cle]["entete"]["Metadata"] = {}  # objet déposé hors application

    resultat = client.verifier_integrite(cle, fichier, relire=False)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "taille_seule"


def test_checksum_serveur_full_object_n_est_accepte_que_pour_l_objet_entier(tmp_path: Path) -> None:
    """Un checksum multipart n'est PAS l'empreinte du fichier : il ne prouve rien."""
    magasin = S3EnMemoire()
    _brancher(magasin)
    fichier = _fichier(tmp_path)
    client = _client(magasin)
    cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    magasin.ajouter_checksum_serveur(cle, type_checksum="COMPOSITE")
    composite = client.verifier_integrite(cle, fichier, relire=False)
    assert composite.integrite_prouvee is False, "checksum multipart accepté à tort"
    assert composite.methode == "metadata_seule"

    magasin.ajouter_checksum_serveur(cle, type_checksum="FULL_OBJECT")
    entier = client.verifier_integrite(cle, fichier, relire=False)
    assert entier.integrite_prouvee is True
    assert entier.methode == "checksum_serveur"

    # Corrompu + checksum recalculé par le fournisseur ⇒ divergence détectée.
    magasin.corrompre(cle, octets=b"Z" * 10)
    magasin.ajouter_checksum_serveur(cle, type_checksum="FULL_OBJECT")
    divergente = client.verifier_integrite(cle, fichier, relire=False)
    assert divergente.integrite_prouvee is False
    assert divergente.methode == "echec"


# ---------------------------------------------------------------------------
# 3. Conséquence opérationnelle : all_verified (donc la purge) exige une preuve
# ---------------------------------------------------------------------------


def _lot(tmp_path: Path, magasin: S3EnMemoire, **config_extra: object) -> UploadBatch:
    fichier = _fichier(tmp_path)
    _brancher(magasin)
    return upload_artifacts_to_storage(
        "dossier",
        [fichier],
        _config(tmp_path, **config_extra),
        import_id="IMP-1",
        source_root=tmp_path,
    )


def test_all_verified_exige_une_preuve_sur_les_octets(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    lot = _lot(tmp_path, magasin)
    assert lot.status == "uploaded"
    assert lot.all_verified is True
    assert lot.artifacts[0].verification == "relecture_sha256"
    assert lot.artifacts[0].to_dict()["verification"] == "relecture_sha256"


def test_objet_corrompu_interdit_la_purge_locale(tmp_path: Path) -> None:
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
        _brancher(magasin)
        lot = upload_artifacts_to_storage(
            "dossier", [_fichier(tmp_path)], _config(tmp_path), import_id="IMP-1", source_root=tmp_path
        )
    assert lot.status != "uploaded"
    assert lot.all_verified is False
    assert not lot.artifacts[0].verified
    assert lot.artifacts[0].error


def test_relecture_desactivee_par_configuration_conserve_la_copie_locale(tmp_path: Path) -> None:
    magasin = S3EnMemoire()
    lot = _lot(tmp_path, magasin, storage_verify_reread=False)
    assert lot.artifacts[0].verified is False
    assert lot.artifacts[0].verification == "metadata_seule"
    assert lot.all_verified is False, (
        "sans relecture, rien ne prouve les octets stockés : la purge locale doit être refusée"
    )
    assert "copie locale conservée" in (lot.artifacts[0].error or "")
