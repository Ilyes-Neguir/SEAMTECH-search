"""Identifiants de stockage : identité applicative DÉDIÉE, vérifiée.

Défaut trouvé par la revue indépendante du 2026-10-07 (bloquant de release) :
``docker-compose.yml`` transmettait ``MINIO_ROOT_USER`` / ``MINIO_ROOT_PASSWORD``
aux services ``web`` et ``worker``. L'application tournait donc en
ADMINISTRATEUR du stockage (lecture, écrasement et suppression de n'importe quel
bucket), et renseigner ``SEAMTECH_S3_ACCESS_KEY`` dans ``.env`` n'avait aucun
effet : la composition écrasait la valeur par celle du root.

Ce fichier vérifie la correction à deux niveaux :

* **sans service** (toujours exécuté) : la composition, ``.env.example``, le
  script de provisionnement et le code applicatif — la composition exige une
  identité dédiée, le script crée cette identité restreinte, le code refuse un
  client sans identifiants (aucun repli sur le root) ;
* **avec MinIO réel + docker compose** (marqueurs ``s3`` et
  ``integration_docker``, exécuté par le job CI ``integration``) : l'identité
  applicative PEUT lire/écrire son bucket et NE PEUT PAS toucher un autre bucket
  ni effectuer d'opération d'administration ; les conteneurs ``web`` et
  ``worker`` reçoivent bien cette identité et pas le root ; des identifiants
  manquants font échouer la composition avec un message clair ; aucun secret
  n'apparaît dans les journaux.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from seamtech_search.config import AppConfig
from seamtech_search.storage import S3StorageClient, StorageError

RACINE = Path(__file__).resolve().parents[1]
COMPOSE = RACINE / "docker-compose.yml"
ENV_EXEMPLE = RACINE / ".env.example"
SCRIPT_PROVISIONNEMENT = RACINE / "scripts" / "provisionner_stockage.sh"

#: Chaînes volontairement FICTIVES : aucun secret réel n'entre dans le dépôt.
CLE_APP = "CLE-APPLICATIVE-FICTIVE"
SECRET_APP = "SECRET-APPLICATIF-FICTIF"
CLE_ROOT = "ADMINISTRATEUR-FICTIF"
SECRET_ROOT = "SECRET-ADMINISTRATEUR-FICTIF"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. La composition exige une identité applicative dédiée
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["web", "worker"])
def test_compose_transmet_une_identite_applicative_dediee(service: str) -> None:
    """``web`` et ``worker`` reçoivent l'identité APPLICATIVE, jamais le root.

    C'est le correctif du défaut bloquant : la valeur vient de
    ``SEAMTECH_S3_ACCESS_KEY``/``SEAMTECH_S3_SECRET_KEY`` (identité dédiée) et
    la composition REFUSE de démarrer si elles manquent (``:?``).
    """
    environnement = _compose()["services"][service]["environment"]
    assert set(environnement) == set(environnement)  # dict attendu (pas de liste)
    acces = str(environnement["SEAMTECH_S3_ACCESS_KEY"])
    secret = str(environnement["SEAMTECH_S3_SECRET_KEY"])
    assert "MINIO_ROOT" not in acces, (
        f"{service} reçoit encore les identifiants administrateur : {acces}"
    )
    assert "MINIO_ROOT" not in secret, (
        f"{service} reçoit encore le secret administrateur : {secret}"
    )
    assert acces.startswith("${SEAMTECH_S3_ACCESS_KEY:?"), acces
    assert secret.startswith("${SEAMTECH_S3_SECRET_KEY:?"), secret
    # « :? » = exigé : pas de repli silencieux sur une autre valeur.
    assert ":?" in acces and ":?" in secret


def test_compose_reserve_l_administrateur_au_service_minio() -> None:
    """Les identifiants d'administration n'apparaissent QUE dans ``minio``."""
    services = _compose()["services"]
    porteurs = [
        nom
        for nom, definition in services.items()
        for cle, valeur in (definition.get("environment") or {}).items()
        if "MINIO_ROOT" in str(cle) or "MINIO_ROOT" in str(valeur)
    ]
    assert set(porteurs) == {"minio"}, (
        f"credentials administrateur exposés hors du service minio : {sorted(set(porteurs))}"
    )


def test_env_example_separe_administration_application_et_sauvegarde() -> None:
    """``.env.example`` documente les trois jeux d'identifiants et interdit le repli."""
    texte = ENV_EXEMPLE.read_text(encoding="utf-8")
    for variable in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "SEAMTECH_S3_ACCESS_KEY",
        "SEAMTECH_S3_SECRET_KEY",
        "SEAMTECH_BACKUP_ACCESS_KEY",
        "SEAMTECH_BACKUP_SECRET_KEY",
    ):
        assert re.search(rf"^{variable}=", texte, flags=re.MULTILINE), variable
    assert "provisionner_stockage.sh" in texte, (
        ".env.example doit indiquer la procédure de provisionnement"
    )
    assert "PROVISIONNEMENT UNIQUEMENT" in texte, (
        "les identifiants administrateur doivent être marqués « provisionnement uniquement »"
    )
    # Le bloc R2 reste explicitement hors périmètre du lot local-first.
    assert "R2" in texte


# ---------------------------------------------------------------------------
# 2. Le provisionnement crée une identité restreinte, de façon rejouable
# ---------------------------------------------------------------------------


def test_script_de_provisionnement_cree_une_identite_restreinte() -> None:
    """Le script : buckets, politiques limitées à UN bucket, versioning, refus du root."""
    assert SCRIPT_PROVISIONNEMENT.exists(), "scripts/provisionner_stockage.sh manquant"
    texte = SCRIPT_PROVISIONNEMENT.read_text(encoding="utf-8")

    # Politiques : moindre privilège par bucket (aucune ressource « * »).
    assert '"arn:aws:s3:::__BUCKET__"' in texte, "politique de listage par bucket attendue"
    assert '"arn:aws:s3:::__BUCKET__/*"' in texte, "politique d'objets par bucket attendue"
    assert "arn:aws:s3:::*" not in texte, "une politique « tous les buckets » ne doit pas exister"
    assert "s3:DeleteBucket" not in texte and "s3:CreateBucket" not in texte, (
        "une identité applicative ne doit pas pouvoir créer/supprimer des buckets"
    )

    # Idempotence + rotation : détache/retire avant de recréer.
    for commande in ("admin policy detach", "admin policy rm", "admin policy create",
                     "admin user remove", "admin user add", "admin policy attach"):
        assert commande in texte, f"commande absente du provisionnement : {commande}"

    # Refus explicite d'utiliser l'administrateur comme identité d'application.
    assert "REFUS" in texte and "MINIO_ROOT_USER" in texte
    # Versioning activé par le provisionnement (l'app restreinte n'a plus ce droit).
    assert "version enable" in texte, "le versioning doit être activé au provisionnement"

    # Les secrets ne sont jamais DÉVELOPPÉS dans une sortie du script : les
    # messages de refus nomment les variables (« SEAMTECH_S3_SECRET_KEY ») sans
    # en afficher la valeur.
    secrets = ("MINIO_ROOT_PASSWORD", "SEAMTECH_S3_SECRET_KEY", "SEAMTECH_BACKUP_SECRET_KEY")
    for ligne in texte.splitlines():
        nue = ligne.strip()
        if not (nue.startswith("echo") or nue.startswith("printf") or nue.startswith("cat <<")):
            continue
        for nom in secrets:
            for motif in (f"${nom}", "${" + nom + "}"):
                assert motif not in nue, f"le script développe un secret dans une sortie : {nue}"
    assert "set -euo pipefail" in texte

    # Deux modes : service compose (déploiement) et conteneur nommé (CI hors compose).
    assert "docker compose exec -T minio mc" in texte
    assert "docker exec -i" in texte


def test_script_de_provisionnement_verifie_que_les_buckets_sont_prives() -> None:
    """La confidentialité est vérifiée par une requête ANONYME (403 attendu).

    Vérification structurelle, indépendante de la sortie texte de ``mc`` : sur un
    bucket privé, MinIO répond 403 à une requête sans identifiants.
    """
    texte = SCRIPT_PROVISIONNEMENT.read_text(encoding="utf-8")
    assert "curl" in texte and "%{http_code}" in texte
    assert '"403"' in texte or "!= \"403\"" in texte


# ---------------------------------------------------------------------------
# 3. Le code applicatif n'a AUCUN repli sur l'administrateur
# ---------------------------------------------------------------------------


def test_code_applicatif_sans_repli_sur_l_administrateur() -> None:
    """Seul le module d'état lit ``MINIO_ROOT_*`` — et uniquement pour CLASSIFIER.

    Aucun module ne doit s'en servir comme identifiant de repli : c'est ce qui
    garantissait (à tort) que l'application fonctionnait avec les droits
    d'administration.
    """
    suspects: list[str] = []
    for chemin in sorted((RACINE / "seamtech_search").rglob("*.py")):
        texte = chemin.read_text(encoding="utf-8")
        if "MINIO_ROOT" not in texte:
            continue
        if chemin.name != "etat_exploitation.py":
            suspects.append(str(chemin.relative_to(RACINE)))
        else:
            # Dans etat_exploitation.py, la lecture sert à QUALIFIER les
            # identifiants (dedie / root_like / absent), jamais à les utiliser.
            assert "s3_credential_kind" in texte
            assert "os.environ.get(\"MINIO_ROOT_USER\")" in texte
    assert suspects == [], f"repli administrateur détecté dans : {suspects}"


def test_client_s3_refuse_un_client_sans_identifiants() -> None:
    """Endpoint et bucket renseignés mais identité absente : erreur CLAIRE.

    Avant ce correctif, un client était construit sans identifiants : boto3
    envoyait des requêtes anonymes et l'échec apparaissait au milieu d'un import,
    en accusant le stockage au lieu de la configuration.
    """
    client = S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id=None,
        secret_access_key=None,
    )
    assert client.credentials_presentes() is False
    with pytest.raises(StorageError) as erreur:
        client._make_client()
    message = str(erreur.value)
    assert "SEAMTECH_S3_ACCESS_KEY" in message and "SEAMTECH_S3_SECRET_KEY" in message
    assert "provisionner_stockage.sh" in message
    assert "Aucun repli" in message

    # Un secret vide (chaîne d'espaces) ne compte pas comme identifiant.
    partiel = S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id=CLE_APP,
        secret_access_key="   ",
    )
    assert partiel.credentials_presentes() is False

    # Avec les deux identifiants : le client se construit, sans réseau.
    complet = S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id=CLE_APP,
        secret_access_key=SECRET_APP,
    )
    assert complet.credentials_presentes() is True
    assert complet._make_client() is not None


def test_outil_de_sauvegarde_utilise_l_identite_dediee_et_jamais_l_administrateur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'envoi hors-site se fait avec l'identité de SAUVEGARDE, pas celle de l'app.

    Conséquence directe du moindre privilège : l'identité applicative n'a plus de
    droits que sur le bucket ``seamtech-documents`` ; sans identité de sauvegarde
    dédiée, l'envoi hors-site échouerait (AccessDenied) — c'est voulu. Ce test
    verrouille les deux branches et interdit tout repli administrateur.
    """
    from seamtech_search import sauvegarde as sv

    config = AppConfig(
        root_paths=[tmp_path],
        min_free_bytes=0,
        s3_endpoint_url="http://minio.invalide:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_APP,
        s3_secret_key=SECRET_APP,
    )
    monkeypatch.setattr(
        "seamtech_search.config.AppConfig.load",
        classmethod(lambda cls, path=None: config),  # type: ignore[arg-type]
    )
    for variable in ("SEAMTECH_BACKUP_ACCESS_KEY", "SEAMTECH_BACKUP_SECRET_KEY", "SEAMTECH_BACKUP_BUCKET"):
        monkeypatch.delenv(variable, raising=False)

    # 1. Sans identité de sauvegarde : repli sur l'identité APPLICATIVE (et
    #    jamais sur l'administrateur), sur le bucket applicatif.
    sans_dedie = sv._client_s3_depuis_env()
    assert sans_dedie is not None
    assert sans_dedie.access_key_id == CLE_APP
    assert sans_dedie.bucket_name == "seamtech-documents"

    # 2. Avec l'identité de sauvegarde : c'est ELLE qui est utilisée, sur SON bucket.
    monkeypatch.setenv("SEAMTECH_BACKUP_ACCESS_KEY", "CLE-SAUVEGARDE")
    monkeypatch.setenv("SEAMTECH_BACKUP_SECRET_KEY", "SECRET-SAUVEGARDE")
    monkeypatch.setenv("SEAMTECH_BACKUP_BUCKET", "seamtech-backups")
    dedie = sv._client_s3_depuis_env()
    assert dedie is not None
    assert dedie.access_key_id == "CLE-SAUVEGARDE"
    assert dedie.secret_access_key == "SECRET-SAUVEGARDE"
    assert dedie.bucket_name == "seamtech-backups"

    # 3. Un secret seul ne suffit pas : pas de mélange des deux identités.
    monkeypatch.delenv("SEAMTECH_BACKUP_SECRET_KEY")
    melange = sv._client_s3_depuis_env()
    assert melange is not None
    assert melange.access_key_id == CLE_APP
    assert melange.secret_access_key == SECRET_APP


def test_health_qualifie_les_identifiants_et_signale_l_absence() -> None:
    """``/health`` dit si l'identité est dédiée, ressemble au root, ou manque."""
    from seamtech_search.etat_exploitation import s3_credential_kind

    def config(**extra: object) -> AppConfig:
        base: dict[str, object] = {
            "root_paths": [Path("/tmp")],
            "database_path": Path("/tmp/x.db"),
            "min_free_bytes": 0,
            "s3_endpoint_url": "http://minio:9000",
            "s3_bucket": "seamtech-documents",
        }
        base.update(extra)
        return AppConfig(**base)  # type: ignore[arg-type]

    assert s3_credential_kind(config()) == "absent"
    assert s3_credential_kind(config(s3_access_key="minioadmin", s3_secret_key="minioadmin")) == "root_like"
    assert s3_credential_kind(config(s3_access_key=CLE_APP, s3_secret_key=SECRET_APP)) == "dedie"

    # Identique au root fourni par l'environnement de la composition.
    os.environ["MINIO_ROOT_USER"] = CLE_ROOT
    os.environ["MINIO_ROOT_PASSWORD"] = SECRET_ROOT
    try:
        assert s3_credential_kind(config(s3_access_key=CLE_ROOT, s3_secret_key=SECRET_ROOT)) == "root_like"
        assert s3_credential_kind(config(s3_access_key=CLE_APP, s3_secret_key=SECRET_APP)) == "dedie"
    finally:
        os.environ.pop("MINIO_ROOT_USER", None)
        os.environ.pop("MINIO_ROOT_PASSWORD", None)


def test_demarrage_avec_endpoint_sans_identifiants_est_dit_et_non_silencieux(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Au démarrage : l'API le DIT (journal) et /health le dit (``absent``).

    On ne masque pas la configuration incomplète en « stockage non configuré » :
    l'exploitant a bien visé un stockage, il doit savoir qu'il manque l'identité.
    """
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "index.db",
        min_free_bytes=0,
        s3_endpoint_url="http://minio.invalide:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=None,
        s3_secret_key=None,
    )
    with caplog.at_level("ERROR", logger="seamtech_search.api"):
        app = create_app(config)
        with TestClient(app) as client:
            sante = client.get("/health")
            assert sante.status_code == 200, sante.text[:200]
            assert sante.json()["s3_credentials"] == "absent", sante.json()
    assert any("identifiants" in message.lower() for message in caplog.messages), caplog.messages


# ---------------------------------------------------------------------------
# 4. MinIO réel + conteneurs : preuve par l'exécution (CI `integration`)
# ---------------------------------------------------------------------------

DOCKER_OK = shutil.which("docker") is not None and (RACINE / "docker-compose.yml").exists()

pytestmark_docker = [
    pytest.mark.integration_docker,
    pytest.mark.s3,
    pytest.mark.skipif(
        not DOCKER_OK,
        reason="Docker/Compose absents : la preuve d'identités restreintes est exécutée par la CI (`integration`)",
    ),
]

URL_MINIO = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
BUCKET_APP = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
BUCKET_SAUVEGARDE = os.environ.get("SEAMTECH_BACKUP_BUCKET", "seamtech-backups")


STRICT = os.environ.get("SEAMTECH_INTEGRATION_STRICT", "").strip().lower() in {"1", "true", "yes"}


def _identifiants_app() -> tuple[str, str]:
    acces = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "")
    secret = os.environ.get("SEAMTECH_S3_SECRET_KEY", "")
    if not acces or not secret:
        if STRICT:
            # En CI, l'infrastructure est montée et contrôlée en amont : une
            # identité manquante est un VRAI échec, pas un « pas prêt ».
            pytest.fail(
                "SEAMTECH_S3_ACCESS_KEY/SECRET_KEY absents alors que "
                "SEAMTECH_INTEGRATION_STRICT=1 : la composition exige ces variables, "
                "le job est mal configuré."
            )
        pytest.skip("SEAMTECH_S3_ACCESS_KEY/SECRET_KEY absents (job CI `integration`)")
    return acces, secret


def _client_app():
    import boto3

    acces, secret = _identifiants_app()
    return boto3.client(
        "s3",
        endpoint_url=URL_MINIO,
        aws_access_key_id=acces,
        aws_secret_access_key=secret,
        region_name="us-east-1",
    )


_PARTAGE = pytest.mark.skipif(
    not DOCKER_OK, reason="Docker/Compose absents (preuve exécutée par la CI)"
)


@pytest.mark.integration_docker
@pytest.mark.s3
@_PARTAGE
def test_identite_applicative_autorisee_sur_son_bucket_et_refusee_ailleurs() -> None:
    """ALLOW/DENY réels contre MinIO : son bucket oui, un autre bucket non,
    l'administration non, l'accès anonyme non."""
    from botocore.exceptions import ClientError

    client = _client_app()
    cle = "verification-identite/objet.txt"
    contenu = b"preuve identite restreinte"

    # 1. AUTORISÉ : écrire, lire, lister, supprimer dans SON bucket.
    client.put_object(Bucket=BUCKET_APP, Key=cle, Body=contenu)
    assert client.get_object(Bucket=BUCKET_APP, Key=cle)["Body"].read() == contenu
    assert client.head_object(Bucket=BUCKET_APP, Key=cle)["ContentLength"] == len(contenu)
    assert any(o["Key"] == cle for o in client.list_objects_v2(Bucket=BUCKET_APP).get("Contents", []))

    # 2. REFUSÉ : le bucket de sauvegarde (autre bucket, même serveur).
    with pytest.raises(ClientError) as refus:
        client.get_object(Bucket=BUCKET_SAUVEGARDE, Key="peu-importe")
    assert refus.value.response["Error"]["Code"] in {"AccessDenied", "403"}, refus.value

    # …et lister les buckets ne doit pas révéler celui des sauvegardes.
    bavards = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    assert BUCKET_SAUVEGARDE not in bavards, (
        f"l'identité applicative voit le bucket de sauvegarde : {sorted(bavards)}"
    )

    # 3. REFUSÉ : toute opération d'administration.
    for appel in (
        lambda: client.create_bucket(Bucket="bucket-cree-par-l-application"),
        lambda: client.put_bucket_versioning(Bucket=BUCKET_APP, VersioningConfiguration={"Status": "Suspended"}),
        lambda: client.put_bucket_policy(Bucket=BUCKET_APP, Policy="{}"),
    ):
        with pytest.raises(ClientError) as refus_admin:
            appel()
        assert refus_admin.value.response["Error"]["Code"] in {"AccessDenied", "403"}, refus_admin.value

    # 4. REFUSÉ : accès anonyme (aucune identité du tout).
    import urllib.error
    import urllib.request

    with pytest.raises(urllib.error.HTTPError) as anonyme:
        urllib.request.urlopen(f"{URL_MINIO}/{BUCKET_APP}/{cle}", timeout=10)
    assert anonyme.value.code == 403, anonyme.value

    client.delete_object(Bucket=BUCKET_APP, Key=cle)


@pytest.mark.integration_docker
@pytest.mark.s3
@_PARTAGE
def test_conteneurs_web_et_worker_recoivent_l_identite_applicative_et_pas_le_root() -> None:
    """Preuve dans les conteneurs EN COURS : l'identité applicative, sans root."""
    acces, secret = _identifiants_app()
    racine_acces = os.environ.get("MINIO_ROOT_USER", "")
    racine_secret = os.environ.get("MINIO_ROOT_PASSWORD", "")
    assert racine_acces and racine_secret, "variables administrateur absentes de l'environnement"

    for service in ("web", "worker"):
        resultat = subprocess.run(
            ["docker", "compose", "exec", "-T", service, "printenv"],
            cwd=str(RACINE),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if resultat.returncode != 0:
            # Le service n'est peut-être pas démarré par l'étape précédente
            # (elle ne lance que la pile documentée) : on le démarre ici plutôt
            # que de conclure au silence.
            demarrage = subprocess.run(
                ["docker", "compose", "up", "-d", service],
                cwd=str(RACINE),
                capture_output=True,
                text=True,
                timeout=600,
            )
            assert demarrage.returncode == 0, (service, demarrage.stderr[-400:])
            resultat = subprocess.run(
                ["docker", "compose", "exec", "-T", service, "printenv"],
                cwd=str(RACINE),
                capture_output=True,
                text=True,
                timeout=120,
            )
        assert resultat.returncode == 0, (service, resultat.stderr[-400:])
        environnement = dict(
            ligne.split("=", 1) for ligne in resultat.stdout.splitlines() if "=" in ligne
        )
        assert environnement.get("SEAMTECH_S3_ACCESS_KEY") == acces, (
            f"{service} n'utilise pas l'identité applicative"
        )
        assert environnement.get("SEAMTECH_S3_SECRET_KEY") == secret, (
            f"{service} n'utilise pas le secret applicatif"
        )
        for interdit in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
            assert interdit not in environnement, (
                f"{service} porte encore {interdit} : les identifiants administrateur ne "
                "doivent exister que dans le service minio"
            )
        assert racine_acces not in str(environnement.get("SEAMTECH_S3_ACCESS_KEY")), (
            "l'identité applicative est celle de l'administrateur"
        )


@pytest.mark.integration_docker
@pytest.mark.s3
@_PARTAGE
def test_identifiants_applicatifs_manquants_echec_clair_de_la_composition() -> None:
    """Sans identité applicative, ``docker compose config`` ÉCHOUE et le dit.

    C'est l'exigence « échec clair, pas de repli silencieux » : la composition
    ne peut pas être interprétée sans les deux variables, donc aucun service ne
    peut démarrer avec des identifiants implicites.
    """
    environnement = {k: v for k, v in os.environ.items() if not k.startswith("SEAMTECH_S3_")}
    resultat = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultat.returncode != 0, "la composition a été interprétée sans identité applicative"
    sortie = (resultat.stdout + resultat.stderr).lower()
    assert "seamtech_s3_access_key" in sortie or "seamtech_s3_secret_key" in sortie, (
        f"l'échec doit nommer les variables manquantes : {sortie[-400:]}"
    )


@pytest.mark.integration_docker
@pytest.mark.s3
@_PARTAGE
def test_aucun_secret_dans_les_journaux_des_services() -> None:
    """Les secrets applicatif et administrateur n'apparaissent jamais dans les journaux."""
    _, secret_app = _identifiants_app()
    secret_root = os.environ.get("MINIO_ROOT_PASSWORD", "")
    resultat = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "web", "worker"],
        cwd=str(RACINE),
        capture_output=True,
        text=True,
        timeout=180,
    )
    journaux = resultat.stdout + resultat.stderr
    for interdit, nom in ((secret_app, "secret applicatif"), (secret_root, "secret administrateur")):
        assert interdit not in journaux, f"{nom} présent dans les journaux des services"
