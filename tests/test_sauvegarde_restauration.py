"""Sauvegarde hors-site (Lot H.1) — ALLER-RETOUR RÉEL sur PostgreSQL.

Le critère d'acceptation : « base détruite reconstruite à l'identique,
prouvé par exécution ». Ce fichier détruit VRAIMENT la base semée
(DROP DATABASE), puis la restaure DEPUIS LE BUCKET SEUL (les copies locales
du dump sont supprimées avant restauration), et prouve l'égalité :

- comptes par table identiques au manifeste ;
- VERSION_SCHEMA_METIER présente dans schema_migrations ;
- la recherche sur la base restaurée renvoie la même fiche qu'avant destruction ;
- inventaire de l'archive comparé (chemins + tailles + sha256).

La durée de restauration sur ~50 000 fiches est MESURÉE et publiée dans
``SEAMTECH_SAUVEGARDE_JSON`` (même mécanique que ``perf-latence``).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from seamtech_search import sauvegarde
from seamtech_search.sauvegarde import (
    SauvegardeError,
    restaurer,
    sauver,
    verifier,
)
from tests.test_sauvegarde_unites import S3EnMemoire

pytestmark = pytest.mark.postgres

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

#: Nombre de fiches du test de montée en charge (~50 000, chiffre publié).
N_FICHES_50K = 50_000

#: Plafond anti-dérive du test 50 000 fiches — très large : la mesure exacte
#: est PUBLIÉE dans le JSONL et le runbook ; ce seuil ne protège que contre un
#: effondrement complet (10 minutes). Étiquette : seuil d'environnement.
SEUIL_RESTAURATION_50K_S = 600.0


def _detruire_base(url_base: str) -> None:
    """DROP DATABASE WITH FORCE — la destruction que la restauration doit réparer."""
    nom = url_base.rsplit("/", 1)[1]
    administrateur = psycopg2.connect(URL_PG)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
    finally:
        administrateur.close()


def _creer_archive_fixture(tmp_path: Path) -> Path:
    """Archive de test : copies des VRAIES fiches PDF du dépôt (empreintes
    épinglées dans tests/test_empreintes_fixtures.py)."""
    archive = tmp_path / "archive"
    destinations = [
        ("CLIENT-7792-SO", "fiche-7792-SO_ffab.pdf", "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
        ("CLIENT-GENOA", "fiche-genois.pdf", "sample_data/CLIENT-GENOA/fiche-genois.pdf"),
    ]
    for dossier, nom, source in destinations:
        chemin_source = Path(source)
        if not chemin_source.exists():
            pytest.skip(f"fixture absente : {source}")
        destination = archive / dossier
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(chemin_source, destination / nom)
    return archive


def _meilleur_code_recherche(url_base: str, requete: str) -> str | None:
    """Code de la première fiche renvoyée par la recherche — la preuve que la
    recherche fonctionne APRÈS restauration."""
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.recherche import rechercher_fiches

    index = SearchIndex(Path(f"/tmp/lecture-{requete}.db"), url_base)
    try:
        reponse = rechercher_fiches(index, requete=requete)
        resultats = reponse.get("resultats") or reponse.get("fiches") or []
        if not resultats:
            return None
        premier = resultats[0]
        return premier.get("code") or premier.get("fiche", {}).get("code")
    finally:
        index.close()


# ---------------------------------------------------------------------------
# Aller-retour complet : base détruite puis reconstruite depuis le bucket
# ---------------------------------------------------------------------------


def test_aller_retour_complet_base_detruite(
    base_recherche: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url_base = base_recherche["url"]
    nom_base = base_recherche["nom"]
    archive = _creer_archive_fixture(tmp_path)

    mesures = Path(os.environ.get("SEAMTECH_SAUVEGARDE_JSON") or tmp_path / "sauvegarde-mesures.jsonl")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_JSON", str(mesures))
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path / "restaure"))

    # 1) SAUVEGARDE — dump + inventaire archive + manifeste + envoi S3 vérifié.
    client = S3EnMemoire()
    dossiers_backups = tmp_path / "backups"
    manifeste = sauver(url_base, [archive], dossiers_backups, client_s3=client, retention=5)

    assert manifeste["version_schema_metier"] == "013_recherche_fonds_reel"
    assert manifeste["dump"]["octets"] > 0
    assert manifeste["dump"]["sha256"]
    assert manifeste["dump"]["cle_s3"]
    assert manifeste["archive"]["nb_fichiers"] == 2
    assert sum(manifeste["fiches_par_statut"].values()) == len(base_recherche["fiches"])
    assert manifeste["documents"] >= 1

    # Preuve AVANT destruction : la recherche répond.
    code_avant = _meilleur_code_recherche(url_base, "régatier")
    assert code_avant, "la recherche ne répond pas avant destruction"

    # 2) DESTRUCTION — copies locales supprimées, base DROPée : il ne reste
    # QUE le bucket.
    for fichier in dossiers_backups.glob("*.dump*"):
        fichier.unlink()
    _detruire_base(url_base)
    with pytest.raises(Exception):
        _meilleur_code_recherche(url_base, "régatier")

    # 3) RESTAURATION — depuis le bucket seul (dump re-téléchargé).
    manifeste_local = dict(manifeste)
    resume = restaurer(url_base, manifeste_local, client_s3=client)
    assert resume["base"] == nom_base

    # 4) VÉRIFICATION — comptes par table, schéma, statuts, documents, archive.
    resultat = verifier(url_base, manifeste, [archive])
    assert resultat["ok"], f"écarts de restauration : {resultat['ecarts']}"

    # 5) PREUVE FONCTIONNELLE — la recherche renvoie la même fiche.
    code_apres = _meilleur_code_recherche(url_base, "régatier")
    assert code_apres == code_avant, f"recherche dégradée après restauration : {code_avant} → {code_apres}"

    # 6) PUBLICATION — les mesures sont dans le JSONL (CI les lira).
    lignes = mesures.read_text(encoding="utf-8").splitlines()
    assert len(lignes) >= 3  # sauver, restaurer, verifier


def test_verifier_detecte_archive_alteree(base_recherche: dict, tmp_path: Path) -> None:
    """L'inventaire d'archive est la preuve que l'archive n'a pas bougé : une
    altération DOIT être détectée (chemins + tailles + sha256)."""
    url_base = base_recherche["url"]
    archive = _creer_archive_fixture(tmp_path)

    client = S3EnMemoire()
    manifeste = sauver(url_base, [archive], tmp_path / "backups", client_s3=client)

    # Altération silencieuse d'un fichier de l'archive (même taille).
    fiche = archive / "CLIENT-GENOA" / "fiche-genois.pdf"
    octets = bytearray(fiche.read_bytes())
    octets[-1] = octets[-1] ^ 0xFF
    fiche.write_bytes(bytes(octets))

    resultat = verifier(url_base, manifeste, [archive])
    assert not resultat["ok"]
    assert any("ALTÉRÉ" in e for e in resultat["ecarts"])


def test_restauration_refuse_dump_altere(base_recherche: dict, tmp_path: Path) -> None:
    """Un dump corrompu hors-site est REFUSÉ avant toute écriture."""
    url_base = base_recherche["url"]
    archive = _creer_archive_fixture(tmp_path)

    client = S3EnMemoire()
    manifeste = sauver(url_base, [archive], tmp_path / "backups", client_s3=client)
    cle = manifeste["dump"]["cle_s3"]
    client.objets[cle] = client.objets[cle][:-1] + b"X"  # corruption d'1 octet
    for fichier in (tmp_path / "backups").glob("*.dump*"):
        fichier.unlink()

    with pytest.raises(SauvegardeError, match="restauration REFUSÉE"):
        restaurer(url_base, manifeste, client_s3=client)


def test_restauration_refuse_base_existante(base_recherche: dict, tmp_path: Path) -> None:
    """La restauration n'écrase JAMAIS une base existante par surprise."""
    url_base = base_recherche["url"]
    archive = _creer_archive_fixture(tmp_path)

    client = S3EnMemoire()
    manifeste = sauver(url_base, [archive], tmp_path / "backups", client_s3=client)
    dump_local = next((tmp_path / "backups").glob("*.dump"))

    with pytest.raises(SauvegardeError, match="existe déjà"):
        restaurer(url_base, manifeste, chemin_dump_local=dump_local)


# ---------------------------------------------------------------------------
# Montée en charge : ~50 000 fiches — durée MESURÉE et publiée
# ---------------------------------------------------------------------------


def test_restauration_50000_fiches_duree_publiee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Durée de restauration sur ~50 000 fiches : le chiffre est MESURÉ,
    publié dans le JSONL, et reporté dans RUNBOOK_RESTAURATION.md. Le seuil
    d'assertion (10 min) n'est qu'un garde-fou anti-effondrement, étiqueté
    seuil d'environnement — la valeur de référence est la mesure publiée."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    mesures = Path(os.environ.get("SEAMTECH_SAUVEGARDE_JSON") or tmp_path / "sauvegarde-50k.jsonl")
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_JSON", str(mesures))
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path / "restaure"))

    import uuid

    nom_base = f"restaur50k_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(URL_PG)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"

    try:
        index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
        index.initialize()
        index.run_migrations()
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO fiche (code, titre, statut) "
                    "SELECT 'VOL-' || lpad(i::text, 6, '0'), 'Voile de lot H.1 n°' || i, "
                    "CASE WHEN i %% 5 = 0 THEN 'valide' ELSE 'a_valider' END "
                    "FROM generate_series(1, %s) AS i",
                    (N_FICHES_50K,),
                )
        index.close()

        client = S3EnMemoire()
        manifeste = sauver(url_base, [], tmp_path / "backups", client_s3=client)
        assert sum(manifeste["fiches_par_statut"].values()) == N_FICHES_50K

        for fichier in (tmp_path / "backups").glob("*.dump*"):
            fichier.unlink()
        _detruire_base(url_base)

        debut = time.perf_counter()
        resume = restaurer(url_base, manifeste, client_s3=client)
        duree = time.perf_counter() - debut

        resultat = verifier(url_base, manifeste)
        assert resultat["ok"], f"écarts : {resultat['ecarts']}"
        assert resume["etat"]["tables"]["fiche"] == N_FICHES_50K
        # Publication explicite de la mesure (le runbook citera ce chiffre).
        with open(mesures, "a", encoding="utf-8") as f:
            import json

            f.write(
                json.dumps(
                    {
                        "operation": "restaurer",
                        "scenario": "50k_fiches",
                        "nb_fiches": N_FICHES_50K,
                        "duree_s": round(duree, 2),
                        "dump_octets": manifeste["dump"]["octets"],
                    }
                )
                + "\n"
            )
        assert duree < SEUIL_RESTAURATION_50K_S, (
            f"restauration 50k : {duree:.0f} s ≥ seuil environnement {SEUIL_RESTAURATION_50K_S:.0f} s"
        )
    finally:
        _detruire_base(url_base)


# ---------------------------------------------------------------------------
# Preuve CLI : les commandes documentées dans le runbook fonctionnent
# ---------------------------------------------------------------------------


def test_cli_sans_s3_aller_retour(base_recherche: dict, tmp_path: Path) -> None:
    """Les commandes exactes du RUNBOOK_RESTAURATION.md (mode local, sans S3)
    s'exécutent telles quelles : sauver → destruction → restaurer → vérifier."""
    import subprocess
    import sys

    url_base = base_recherche["url"]
    archive = _creer_archive_fixture(tmp_path)
    dossiers = tmp_path / "backups"

    def cli(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "seamtech_search.sauvegarde", *arguments],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )

    # 1) SAUVEGARDE locale (sans S3 — le mode repli documenté).
    resultat = cli("sauver", "--base-url", url_base, "--archive", str(archive), "--dossier", str(dossiers), "--sans-s3")
    assert resultat.returncode == 0, resultat.stderr
    sortie = json.loads(resultat.stdout.strip().splitlines()[-1])
    manifeste_local = sortie["manifeste"]

    # 2) DESTRUCTION.
    _detruire_base(url_base)

    # 3) RESTAURATION depuis le manifeste local (dump à côté du manifeste).
    resultat = cli("restaurer", "--manifeste", manifeste_local, "--base-cible", url_base)
    assert resultat.returncode == 0, resultat.stderr

    # 4) VÉRIFICATION : comptes, schéma, archive.
    resultat = cli("verifier", "--manifeste", manifeste_local, "--base-url", url_base, "--archive", str(archive))
    assert resultat.returncode == 0, resultat.stderr
    assert json.loads(resultat.stdout.strip().splitlines()[-1])["ok"]


# ---------------------------------------------------------------------------
# Épreuve hors-site en CI : client S3 RÉEL (MinIO), pas un faux en mémoire
# ---------------------------------------------------------------------------


@pytest.mark.sauvegarde
def test_aller_retour_via_client_s3_reel(base_recherche: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'aller-retour complet avec le VRAI client S3 contre le VRAI bucket
    (MinIO en CI). Saute hors CI quand SEAMTECH_S3_ENDPOINT_URL est absent —
    le job CI dédié fournit le MinIO, donc là-bas ce test ne saute JAMAIS."""
    if not os.environ.get("SEAMTECH_S3_ENDPOINT_URL"):
        pytest.skip("SEAMTECH_S3_ENDPOINT_URL absent : réservé au job CI sauvegarde (MinIO réel)")

    from seamtech_search.config import AppConfig
    from seamtech_search.storage import S3StorageClient

    url_base = base_recherche["url"]
    archive = _creer_archive_fixture(tmp_path)
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path / "restaure"))

    client = S3StorageClient(config=AppConfig.load())
    client.ensure_bucket_exists()

    # Sauvegarde avec envoi hors-site RÉEL (re-lecture vérifiée par le module).
    dossiers = tmp_path / "backups"
    manifeste = sauver(url_base, [archive], dossiers, client_s3=client, retention=3)
    cle_dump = manifeste["dump"]["cle_s3"]
    assert client.object_exists(cle_dump), "le dump n'est pas réellement dans le bucket"

    # Le listing réel du bucket voit bien la sauvegarde envoyée.
    cles = client.list_keys(sauvegarde.PREFIXE_SAUVEGARDES)
    assert any(c.endswith(".dump") for c in cles), f"dump introuvable parmi {cles}"

    # Destruction : copies locales ET base supprimées — seul le bucket survit.
    for fichier in dossiers.glob("*.dump*"):
        fichier.unlink()
    _detruire_base(url_base)

    # Restauration depuis le bucket seul.
    restaurer(url_base, manifeste, client_s3=client)

    # Vérification complète + preuve fonctionnelle.
    resultat = verifier(url_base, manifeste, [archive])
    assert resultat["ok"], f"écarts : {resultat['ecarts']}"
    code_apres = _meilleur_code_recherche(url_base, "régatier")
    assert code_apres is not None

    # Nettoyage du bucket (les objets de test ne doivent pas s'accumuler).
    client.delete_file(cle_dump)
    if manifeste.get("manifeste_cle_s3"):
        client.delete_file(manifeste["manifeste_cle_s3"])
