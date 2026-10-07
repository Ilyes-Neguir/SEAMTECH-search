# -*- coding: utf-8 -*-
"""Recette COMPLÈTE du corpus réel (7 dossiers REF-001..REF-007) — Lot 2.

Exécutée UNIQUEMENT par le job CI dédié « recette-corpus-reel » (services
réels : PostgreSQL+pgvector, Redis, MinIO, Tesseract+fra) — jamais dans les
suites ordinaires : le marqueur ``s3`` l'exclut de la couverture et de la
suite SQLite, l'absence de marqueur ``postgres`` l'exclut de la suite
PostgreSQL, et la garde ``SEAMTECH_RECETTE_CORPUS`` la neutralise partout
ailleurs.

CONFIDENTIALITÉ (contrat du corpus) : ce fichier et les journaux CI ne
contiennent AUCUNE valeur métier — uniquement des comptes, des codes
REF-00x, des statuts HTTP, des empreintes et des mesures agrégées. Les
valeurs extraites du corpus sont comparées DYNAMIQUEMENT (jamais écrites,
jamais imprimées) : les assertions échouent sur un message sans valeur, et
les journaux applicatifs sont réduits à WARNING pour ne pas recopier les
chemins des dossiers.

Étapes prouvées, dans l'ordre (une étape rouge = job rouge) :

1. ZIP d'origine intacts avant recette (empreintes SHA-256, RG13).
2. Corpus extrait complet : 7 dossiers, 14 PDF uniques, 30 pages natives.
3. Services vivants + migrations 001..018 appliquées sur une base neuve.
4. Import des 7 dossiers par le FLUX RÉEL : upload (REF-001), scan,
   confirmation avec sélection du candidat technique ; dépôt Lot C
   (fiche + pièces jointes) pour chaque dossier.
5. Classement automatique « technique » + champs proposés (gabarits),
   fiches A_VALIDER — jamais validées automatiquement.
6. OCR par étages : les 30 pages natives ne sont PAS océrisées ; une page
   rendue en image EST océrisée réellement par Tesseract (fra).
7. Recherches par mots-clés (référence, client, bateau, type, matière,
   accents, multi-termes) — termes tirés dynamiquement de la base.
8. Ouverture d'un PDF depuis un résultat : URL présignée RÉELLE contre
   MinIO (HTTP 302, Location, ExpiresIn=900 s, contenu identique).
9. Téléchargement d'un rapport PDF d'import.
10. Sauvegarde puis restauration de la base PostgreSQL RÉELLE dans une base
    neuve ; fiches, object_key, recherche et ouverture PDF revérifiés.
11. ZIP d'origine inchangés après recette (RG13).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Garde d'exécution : ce module ne vit que dans son job CI dédié.
#
# La garde est un skip D'EXÉCUTION (fixture autouse), pas un skip de module :
# les tests restent COLLECTABLES (le garde-fou tests/test_selection_ci.py
# vérifie que « -m s3 » retrouve exactement l'inventaire), mais ne s'exécutent
# que sous SEAMTECH_RECETTE_CORPUS=1 — soit le job « recette-corpus-reel ».
# ---------------------------------------------------------------------------

ACTIF = os.environ.get("SEAMTECH_RECETTE_CORPUS", "").strip().lower() in {"1", "true", "yes"}
GARDE = "recette corpus réel : job CI « recette-corpus-reel » uniquement (SEAMTECH_RECETTE_CORPUS=1)"

pytestmark = [pytest.mark.s3, pytest.mark.recette_corpus]


@pytest.fixture(autouse=True)
def _garde_job_dedie() -> None:
    if not ACTIF:
        pytest.skip(GARDE)

#: Les journaux applicatifs recopieraient des chemins de dossiers et des
#: codes de fiche : on se limite à WARNING pendant la recette — les échecs
#: restent visibles, les valeurs ne défilent plus dans les journaux publics.
logging.getLogger("seamtech_search").setLevel(logging.WARNING)

RACINE_CORPUS = Path(os.environ.get("SEAMTECH_RECETTE_CORPUS_RACINE", ""))
URL_BASE = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_BASE_RESTAUREE = os.environ.get(
    "SEAMTECH_RECETTE_DATABASE_RESTAURATION",
    (URL_BASE.rsplit("/", 1)[0] + "/seamtech_search_recette_restauree") if URL_BASE else "",
)
REDIS_URL = os.environ.get("SEAMTECH_REDIS_URL", "")
S3_ENDPOINT = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
S3_BUCKET = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
S3_BACKUP_BUCKET = os.environ.get("SEAMTECH_RECETTE_BUCKET_SAUVEGARDE", "seamtech-backups")
S3_ACCESS_KEY = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("SEAMTECH_S3_SECRET_KEY", "minioadmin123")
JETON = "recette-corpus-jeton"

#: Racine du dépôt (les 7 ZIP originaux y sont commités par le commanditaire).
RACINE_DEPOT = Path(__file__).resolve().parents[1]

#: Invariants agrégés du corpus (mesurés — aucune valeur métier).
NB_ZIP = 7
NB_PDF_UNIQUES = 14
NB_PAGES_NATIVES = 30
SEUIL_CHAMPS_PAR_FICHE = 20
SEUIL_CONFIANCE_MOYENNE = 0.8
EXPIRATION_PRESIGNEE_S = 900

#: État partagé entre étapes (chemins et clés — jamais imprimés).
RECETTE: dict[str, dict] = {"refs": {}, "fiche_attendue": None}


def _empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _sans_accents(texte: str) -> str:
    """Version sans accents (NFKD + retrait des diacritiques combinants)."""
    import unicodedata

    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texte)
        if not unicodedata.combining(caractere)
    )


def _client_s3(bucket: str | None = None):
    """Client stockage du PROJET (S3StorageClient) — pas un client boto3 nu :
    la sauvegarde appelle ``upload_file(..., avoid_overwrite=True)``, paramètre
    du wrapper seul (le client brut lève TypeError)."""
    from seamtech_search.storage import S3StorageClient

    return S3StorageClient(
        endpoint_url=S3_ENDPOINT,
        bucket_name=bucket or S3_BUCKET,
        access_key_id=S3_ACCESS_KEY,
        secret_access_key=S3_SECRET_KEY,
    )


# ---------------------------------------------------------------------------
# Fixtures de session : empreintes, corpus, application réelle.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def empreintes_zip_avant() -> dict[str, str]:
    zips = sorted(RACINE_DEPOT.glob("*.zip"))
    assert len(zips) == NB_ZIP, f"attendu {NB_ZIP} ZIP à la racine du dépôt, trouvé {len(zips)}"
    return {z.name: _empreinte(z) for z in zips}


@pytest.fixture(scope="session")
def dossiers() -> dict[str, Path]:
    """REF-001..REF-007 → dossier extrait (ordre alphabétique stable)."""
    if not ACTIF:
        pytest.skip(GARDE)
    if not RACINE_CORPUS.is_dir():
        pytest.fail(f"corpus extrait introuvable : {RACINE_CORPUS} (SEAMTECH_RECETTE_CORPUS_RACINE)")
    candidats = sorted(p for p in RACINE_CORPUS.iterdir() if p.is_dir())
    if len(candidats) != NB_ZIP:
        pytest.fail(f"attendu {NB_ZIP} dossiers extraits, trouvé {len(candidats)}")
    return {f"REF-{i:03d}": p for i, p in enumerate(candidats, start=1)}


def _config_recette(database_url: str, travail: Path):
    from seamtech_search.config import AppConfig

    return AppConfig(
        root_paths=[RACINE_CORPUS, RACINE_DEPOT, travail],
        database_path=travail / "search.db",
        database_url=database_url,
        redis_url=REDIS_URL or None,
        min_free_bytes=0,
        auth_token=JETON,
        s3_endpoint_url=S3_ENDPOINT,
        s3_bucket=S3_BUCKET,
        s3_access_key=S3_ACCESS_KEY,
        s3_secret_key=S3_SECRET_KEY,
        max_file_size_bytes=512 * 1024 * 1024,
    )


@pytest.fixture(scope="session")
def app_client(tmp_path_factory: pytest.TempPathFactory):
    """Application RÉELLE (create_app) sur PostgreSQL + Redis + MinIO."""
    if not ACTIF:
        pytest.skip(GARDE)
    if not URL_BASE:
        pytest.fail("SEAMTECH_TEST_DATABASE_URL absente : la recette exige PostgreSQL")
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.indexer import SearchIndex

    travail = tmp_path_factory.mktemp("recette")
    RECETTE["travail"] = str(travail)  # l'étape 10.6 réutilisera ces racines
    app = create_app(_config_recette(URL_BASE, travail))
    # Amorçage des gabarits embarqués (geste opérateur du CLI, cf. MISE_EN
    # SERVICE) : create_app applique les migrations mais n'enregistre PAS les
    # gabarits — sans eux, le dépôt Lot C n'a aucun gabarit actif.
    from seamtech_search.fiches.gabarits import initialiser_gabarits

    index_semis = SearchIndex(travail / "semis.db", URL_BASE)
    try:
        initialiser_gabarits(index_semis)
    finally:
        index_semis.close()
    client = TestClient(app, follow_redirects=False)
    client.headers.update({"X-SEAMTECH-TOKEN": JETON})
    with client:
        yield client


# ---------------------------------------------------------------------------
# 1 + 2. ZIP intacts, corpus complet.
# ---------------------------------------------------------------------------


def test_01_zip_intacts_avant_recette(empreintes_zip_avant: dict[str, str]) -> None:
    """RG13 : les 7 ZIP originaux sont présents et hashés AVANT tout traitement."""
    assert len(empreintes_zip_avant) == NB_ZIP
    assert all(h for h in empreintes_zip_avant.values()), "empreinte vide"


def test_02_corpus_extrait_complet(dossiers: dict[str, Path]) -> None:
    """Le corpus extrait porte les invariants mesurés (14 PDF, 30 pages)."""
    import pdfplumber

    vus: set[str] = set()
    pages = 0
    for ref, dossier in dossiers.items():
        pdfs = sorted(dossier.rglob("*.pdf"))
        assert pdfs, f"{ref} : aucun PDF dans le dossier"
        for pdf in pdfs:
            cle = _empreinte(pdf)[:16]
            if cle in vus:
                continue
            vus.add(cle)
            with pdfplumber.open(pdf) as doc:
                pages += len(doc.pages)
    assert len(vus) == NB_PDF_UNIQUES, f"attendu {NB_PDF_UNIQUES} PDF uniques, mesuré {len(vus)}"
    assert pages == NB_PAGES_NATIVES, f"attendu {NB_PAGES_NATIVES} pages natives, mesuré {pages}"


# ---------------------------------------------------------------------------
# 3. Services vivants + migrations 001..019 sur base neuve.
# ---------------------------------------------------------------------------


def test_03_services_et_migrations_018_base_neuve(app_client) -> None:
    """PostgreSQL (migrations 001..019, base vide), Redis (ping), MinIO (bucket).

    Identifiant historique conservé (inventaire S3 réel de test_selection_ci) ;
    les assertions vérifient la séquence complète 001..019."""
    import psycopg2
    import redis as module_redis

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.schema_metier import VERSION_SCHEMA_METIER

    # --- PostgreSQL : create_app a appliqué TOUTES les migrations.
    index = SearchIndex(Path("/tmp/recette-lecture.db"), URL_BASE)
    try:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
                versions = {str(ligne[0]) for ligne in cursor.fetchall()}
                cursor.execute("SELECT COUNT(*) FROM fiche")
                fiches_avant = int(cursor.fetchone()[0])
    finally:
        index.close()
    attendues = {
        "001_initial",
        "002_object_storage_columns",
        "003_category_backfill_guard",
        "004_uploaded_at_epoch",
        "005_unaccent_search_vector",
        "006_fiche_technique",
        "007_recherche_index",
        "008_ml_corpus",
        "009_qualite_et_gabarits",
        "010_lots_ingestion",
        "011_pieces_catalogue_documents",
        "012_recherche_hybride",
        "013_recherche_fonds_reel",
        "014_facette_dimension",
        "015_qualite_gabarit_brouillon",
        "016_dedup_comptes_nominatifs",
        "017_ocr_etage3",
        "018_recherche_a_valider",
        "019_recherche_dimension",
        "020_file_durable",
    }
    manquantes = attendues - versions
    assert not manquantes, f"migrations non appliquées : {sorted(manquantes)}"
    assert VERSION_SCHEMA_METIER in versions, "VERSION_SCHEMA_METIER absente de schema_migrations"
    assert fiches_avant == 0, f"base non neuve : {fiches_avant} fiches déjà présentes"

    # --- Redis : le service répond vraiment (le job l'exige).
    if not REDIS_URL:
        pytest.fail("SEAMTECH_REDIS_URL absente : la recette exige Redis")
    conn_redis = module_redis.from_url(REDIS_URL, socket_connect_timeout=5, socket_timeout=5)
    assert conn_redis.ping(), "Redis ne répond pas au ping"

    # --- MinIO : le bucket documents existe et répond (le wrapper porte le
    # bucket ; ensure_bucket_exists sonde par head_bucket et crée si absent,
    # le listage vide confirme que l'API répond).
    client = _client_s3(S3_BUCKET)
    client.ensure_bucket_exists()
    client.list_keys("")

    # --- PostgreSQL tout court (sonde bas niveau, distincte de l'app).
    with psycopg2.connect(URL_BASE, connect_timeout=5) as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# 4. Import des 7 dossiers — flux réel (upload, scan, confirmation).
# ---------------------------------------------------------------------------


def _meilleur_candidat_technique(rapport_scan: dict) -> dict:
    candidates = [
        c for c in rapport_scan.get("candidates", []) if c.get("classification") == "technical_pdf"
    ]
    assert candidates, "scan : aucun candidat classé technical_pdf"
    return max(candidates, key=lambda c: c.get("anchor_count", 0))


def _importer_dossier(client, ref: str, dossier: Path, *, via_upload: bool = False) -> None:
    if via_upload:
        # Étape « upload » du flux réel : les fichiers du dossier transitent
        # par /imports/upload (multipart), avec leurs chemins RELATIFS (sinon
        # deux fichiers de noms identiques dans des sous-dossiers se
        # écraseraient dans le staging) ; le scan porte sur la copie stagée.
        fichiers = sorted(p for p in dossier.rglob("*") if p.is_file())
        assert fichiers, f"{ref} : dossier vide"
        assert len(fichiers) <= 500, f"{ref} : trop de fichiers pour un upload unique"
        multipart: list[tuple[str, tuple[str, bytes, str]]] = []
        poignees = []
        try:
            for fichier in fichiers:
                poignee = fichier.open("rb")
                poignees.append(poignee)
                nom_relatif = str(fichier.relative_to(dossier))
                multipart.append(("files", (nom_relatif, poignee.read(), "application/octet-stream")))
            reponse = client.post(
                "/imports/upload",
                files=multipart,
                data={"folder": f"recette-{ref.lower()}"},
            )
        finally:
            for poignee in poignees:
                poignee.close()
        assert reponse.status_code == 200, f"{ref} : upload HTTP {reponse.status_code}"
        dossier_scan = Path(reponse.json()["staged_path"])
    else:
        dossier_scan = dossier

    # Étape « scan » : candidats proposés avec leur classement.
    reponse = client.post("/imports/scan", json={"source_path": str(dossier_scan)})
    assert reponse.status_code == 200, f"{ref} : scan HTTP {reponse.status_code}"
    candidat = _meilleur_candidat_technique(reponse.json())

    # Étape « confirmation » avec SÉLECTION DU CANDIDAT (wait=true : synchrone).
    reponse = client.post(
        "/imports/confirm",
        params={"wait": "true"},
        json={"source_path": str(dossier_scan), "technical_pdf": candidat["path"]},
    )
    assert reponse.status_code == 200, f"{ref} : confirmation HTTP {reponse.status_code}"
    resultat = reponse.json()

    assert resultat["status"] in {"completed", "needs_review"}, f"{ref} : statut d'import inattendu"
    assert resultat["upload_status"] == "uploaded", f"{ref} : envoi S3 en échec"
    assert resultat.get("data"), f"{ref} : aucune donnée extraite"

    # Le candidat sélectionné est le PDF technique importé, classé technique.
    assert resultat["technical_pdf"] == candidat["path"], f"{ref} : le candidat sélectionné n'a pas été retenu"
    fichier_technique = next((f for f in resultat["files"] if f["path"] == candidat["path"]), None)
    assert fichier_technique is not None, f"{ref} : PDF technique absent des fichiers importés"
    assert fichier_technique["category"] == "technical_pdf", f"{ref} : classement automatique ≠ technique"
    assert fichier_technique.get("object_key"), f"{ref} : PDF technique sans object_key"

    # Rapport PDF généré pour l'import.
    rapport = resultat.get("report_path")
    assert rapport and Path(rapport).suffix == ".pdf", f"{ref} : rapport PDF absent"

    RECETTE["refs"][ref] = {
        "dossier": str(dossier),
        "scan": str(dossier_scan),
        "technique": candidat["path"],
        "object_key": fichier_technique["object_key"],
        "import_id": resultat["import_id"],
        "rapport": rapport,
    }


def test_04a_import_flux_reel_ref001_via_upload(app_client, dossiers: dict[str, Path]) -> None:
    """REF-001 par la voie complète : upload multipart → scan → confirmation."""
    _importer_dossier(app_client, "REF-001", dossiers["REF-001"], via_upload=True)


@pytest.mark.parametrize("ref", [f"REF-{i:03d}" for i in range(2, 8)])
def test_04b_import_flux_reel(app_client, dossiers: dict[str, Path], ref: str) -> None:
    """REF-002..REF-007 : scan du dossier → confirmation avec le candidat."""
    _importer_dossier(app_client, ref, dossiers[ref])


@pytest.mark.parametrize("ref", [f"REF-{i:03d}" for i in range(1, 8)])
def test_04c_depot_fiche_lot_c(app_client, dossiers: dict[str, Path], ref: str) -> None:
    """Dépôt Lot C de chaque dossier : fiche + pièces jointes en une transaction."""
    reponse = app_client.post("/imports/dossier", json={"dossier": str(dossiers[ref])})
    assert reponse.status_code == 201, f"{ref} : dépôt HTTP {reponse.status_code}"
    corps = reponse.json()
    # La raison d'un refus embarque l'exception applicative, qui peut recopier
    # du texte documentaire : on n'en retient que le TYPE (dépôt public —
    # aucune valeur métier ne doit fuiter dans les journaux d'assertion).
    raison = re.sub(r"'[^']*'", "«…»", str(corps.get("raison") or ""))[:120]
    assert corps["statut"] == "traite", f"{ref} : dépôt non traité ({raison})"
    assert corps.get("fiche"), f"{ref} : aucune fiche créée"
    RECETTE["refs"][ref]["fiche"] = corps["fiche"]
    # PDF source de la fiche déposée : retenir son nom, jamais un chemin local.
    # Les étapes de consultation récupèrent ensuite le fichier par l'id catalogue.
    reponse_pieces = app_client.get(f"/fiches/{urllib.parse.quote(corps['fiche'])}/pieces")
    assert reponse_pieces.status_code == 200, f"{ref} : lecture pieces HTTP {reponse_pieces.status_code}"
    pieces = reponse_pieces.json()
    pdf_fiche = next((p for p in pieces.get("pieces", []) if p.get("is_primary_pdf")), None)
    assert pdf_fiche and isinstance(pdf_fiche.get("id"), int), f"{ref} : PDF source par id absent de la fiche déposée"
    assert pieces.get("pdf_source") is None and pieces.get("fichier_source") is None
    RECETTE["refs"][ref]["pdf_fiche"] = pdf_fiche["name"]
    RECETTE["refs"][ref]["pdf_fiche_id"] = pdf_fiche["id"]


# ---------------------------------------------------------------------------
# 5. Classement automatique + champs proposés, fiches A_VALIDER.
# ---------------------------------------------------------------------------


def test_05_fiches_proposees_a_valider(app_client) -> None:
    """7 fiches proposées, gabarits atelier, champs nombreux, JAMAIS validées."""
    reponse = app_client.get("/fiches", params={"statut": "a_valider", "taille": 50})
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["total"] == NB_ZIP, f"attendu {NB_ZIP} fiches a_valider, mesuré {corps['total']}"
    assert set(corps["facettes"]) == {"a_valider"}, "des fiches ont un statut autre que a_valider"

    gabarits = set()
    for fiche in corps["fiches"]:
        code = fiche["code"]
        gabarit = fiche["gabarit"]
        assert gabarit in {"FICHE_JADE_V1", "FICHE_GV_FULLBATTEN_V1"}, (
            f"gabarit inattendu pour une fiche du corpus (gabarits vus : {gabarit})"
        )
        gabarits.add(gabarit)
        reponse_champs = app_client.get(f"/fiches/{urllib.parse.quote(code)}/champs")
        assert reponse_champs.status_code == 200, "lecture des champs impossible"
        champs = reponse_champs.json()
        assert len(champs) >= SEUIL_CHAMPS_PAR_FICHE, (
            f"fiche à {len(champs)} champs < {SEUIL_CHAMPS_PAR_FICHE} (seuil)"
        )
        confiances = [float(c["confiance"]) for c in champs if c.get("confiance") is not None]
        moyenne = sum(confiances) / len(confiances) if confiances else 0.0
        assert moyenne >= SEUIL_CONFIANCE_MOYENNE, (
            f"confiance moyenne {moyenne:.3f} < {SEUIL_CONFIANCE_MOYENNE} (seuil)"
        )
    # Les deux familles de gabarits du corpus sont représentées.
    assert gabarits == {"FICHE_JADE_V1", "FICHE_GV_FULLBATTEN_V1"}, (
        f"gabarits observés : {sorted(gabarits)}"
    )


# ---------------------------------------------------------------------------
# 6. OCR par étages.
# ---------------------------------------------------------------------------


@pytest.mark.ocr_suite
def test_06_ocr_par_etages(dossiers: dict[str, Path], tmp_path: Path) -> None:
    """30 pages natives NON océrisées ; une page-image océrisée par Tesseract."""
    if shutil.which("tesseract") is None:
        pytest.fail("tesseract absent : le job recette exige Tesseract (paquet fra)")
    if shutil.which("pdftoppm") is None:
        pytest.fail("pdftoppm absent : le job recette exige poppler-utils")
    import pdfplumber
    from reportlab.pdfgen import canvas as canvas_reportlab

    from seamtech_search.ocr.pipeline import ocriser_fichier

    langues = subprocess.run(
        ["tesseract", "--list-langs"], capture_output=True, text=True, check=True
    ).stdout
    assert "fra" in langues, "langue tesseract fra absente"

    # Étage 2 : les pages natives du corpus ne doivent PAS être océrisées.
    vus: set[str] = set()
    pages_natives = 0
    pages_ocerisees_corpus = 0
    for dossier in dossiers.values():
        for pdf in sorted(dossier.rglob("*.pdf")):
            cle = _empreinte(pdf)[:16]
            if cle in vus:
                continue
            vus.add(cle)
            resultat = ocriser_fichier(pdf, langue="fra")
            for page in resultat["pages"]:
                pages_natives += 1
                if page["page_ocerisee"]:
                    pages_ocerisees_corpus += 1
                else:
                    assert page["motif"] == "texte natif présent", (
                        f"page non océrisée pour un autre motif : {page['motif']}"
                    )
                    assert page["texte_final"] == page["texte_natif"], (
                        "le texte natif a été altéré sans OCR"
                    )
    assert pages_natives == NB_PAGES_NATIVES, (
        f"attendu {NB_PAGES_NATIVES} pages natives, mesuré {pages_natives}"
    )
    assert pages_ocerisees_corpus == 0, (
        f"{pages_ocerisees_corpus} pages natives ont été océrisées à tort"
    )

    # Étage 3 : une page du corpus rendue en IMAGE est océrisée réellement.
    premiere_fiche = Path(RECETTE["refs"]["REF-001"]["technique"])
    subprocess.run(
        ["pdftoppm", "-f", "1", "-l", "1", "-r", "300", "-png", str(premiere_fiche), str(tmp_path / "page")],
        check=True,
        capture_output=True,
    )
    generes = sorted(tmp_path.glob("page*.png"))
    assert generes, "pdftoppm n'a pas produit l'image de la page"
    png = generes[0]

    with pdfplumber.open(premiere_fiche) as doc:
        largeur, hauteur = float(doc.pages[0].width), float(doc.pages[0].height)
        texte_natif = doc.pages[0].extract_text() or ""

    pdf_image = tmp_path / "page-image-seule.pdf"
    canvas = canvas_reportlab.Canvas(str(pdf_image), pagesize=(largeur, hauteur))
    canvas.drawImage(str(png), 0, 0, width=largeur, height=hauteur)
    canvas.showPage()
    canvas.save()

    # Garde : le PDF image ne porte AUCUN texte natif (sinon l'étage 3
    # ne se déclencherait pas et la preuve serait vide).
    with pdfplumber.open(pdf_image) as doc:
        assert not (doc.pages[0].extract_text() or "").strip(), "le PDF image porte du texte natif"

    resultat = ocriser_fichier(pdf_image, langue="fra")
    page = resultat["pages"][0]
    assert page["page_ocerisee"], f"la page image n'a pas été océrisée (motif : {page['motif']})"
    assert page["texte_final"] == page["texte_ocr"], "le texte final n'est pas le texte OCR"
    assert len(page["texte_ocr"].strip()) >= 50, "texte OCR trop court : Tesseract n'a pas vraiment tourné"

    # Preuve SANS divulgation : au moins un des mots les plus longs de la
    # page native est retrouvé par l'OCR (le plus long peut être coupé par
    # le rendu, on en propose cinq).
    mots = sorted(
        (m for m in re.split(r"[^A-Za-zÀ-ÿ]+", texte_natif) if len(m) >= 6),
        key=len,
        reverse=True,
    )[:5]
    assert mots, "page sans mot exploitable pour la preuve OCR"
    texte_ocr = page["texte_ocr"].lower()
    assert any(m.lower() in texte_ocr for m in mots), (
        "l'OCR n'a retrouvé aucun mot significatif de la page rendue en image"
    )


# ---------------------------------------------------------------------------
# 7. Recherches par mots-clés.
# ---------------------------------------------------------------------------


def _termes_de_la_base() -> tuple[dict[str, str], dict[str, str]]:
    """Termes de recherche tirés de la base (valeurs JAMAIS imprimées).

    Retourne (termes, origine) : pour chaque famille, le code REF dont la
    fiche a fourni le terme — le document attendu dans les résultats est
    LE PDF SOURCE DE CETTE FICHE DÉPOSÉE (un dossier peut porter plusieurs
    fiches : le scan Phase 0 et le dépôt Lot C ne retiennent pas
    forcément la même).
    """
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path("/tmp/recette-termes.db"), URL_BASE)
    try:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT f.code, c.nom, b.nom, t.libelle, f.tissu_texte "
                    "FROM fiche f "
                    "LEFT JOIN client c ON c.id_client = f.id_client "
                    "LEFT JOIN bateau b ON b.id_bateau = f.id_bateau "
                    "LEFT JOIN type_voile t ON t.id_type_voile = f.id_type_voile "
                    "ORDER BY f.code"
                )
                lignes = cursor.fetchall()
    finally:
        index.close()
    assert lignes, "aucune fiche en base pour tirer les termes de recherche"

    # code fiche → REF du dossier (mémorisé à l'import, étape 4).
    code_vers_ref = {infos["fiche"]: ref for ref, infos in RECETTE["refs"].items() if infos.get("fiche")}

    termes: dict[str, str] = {}
    origine: dict[str, str] = {}
    accentue: tuple[str, str] | None = None
    for code, client, bateau, type_voile, tissu in lignes:
        ref = code_vers_ref.get(code)
        if ref is None:
            continue
        for famille, valeur in (
            ("reference", code),
            ("client", client),
            ("bateau", bateau),
            ("type", type_voile),
            ("matiere", tissu),
        ):
            if valeur and famille not in termes:
                termes[famille] = valeur
                origine[famille] = ref
            # Terme accentué : cherché sur TOUTES les lignes (le premier
            # fournisseur de chaque famille peut être sans accent).
            if valeur and accentue is None and any(c in valeur for c in "éèêëàâäîïôöùüç"):
                accentue = (valeur, ref)
    assert "reference" in termes, "aucune référence exploitable en base"

    # Un terme accentué + sa version sans accent (parité accents) : le corpus
    # contient nécessairement des libellés accentués (invariant mesuré).
    assert accentue, "aucun terme accentué en base : la preuve accents est impossible"
    termes["accentue"] = accentue[0]
    origine["accentue"] = accentue[1]
    termes["sans_accent"] = _sans_accents(termes["accentue"])
    origine["sans_accent"] = origine["accentue"]
    termes["multi"] = f"{termes['reference']} {termes.get('bateau') or termes.get('client')}".strip()
    origine["multi"] = origine["reference"]
    return termes, origine


def _tous_les_resultats(app_client, terme: str, pages_max: int = 5) -> list[str]:
    """Résultats de /search PAGINÉS jusqu'à épuisement (bornés), sans chemins."""
    noms: list[str] = []
    for page in range(pages_max):
        reponse = app_client.get("/search", params={"q": terme, "limit": 200, "offset": page * 200})
        assert reponse.status_code == 200, f"recherche paginée : HTTP {reponse.status_code}"
        corps = reponse.json()
        noms.extend(str(r.get("name") or "") for r in corps["results"])
        assert all("path" not in r and "path_key" not in r and "object_key" not in r for r in corps["results"])
        if not corps.get("has_more"):
            break
    return noms


def test_07_recherches_par_mots_cles(app_client) -> None:
    """Chaque famille de mots-clés retrouve le document technique attendu."""
    termes, origine = _termes_de_la_base()

    for famille in ("reference", "client", "bateau", "type", "matiere", "accentue", "sans_accent", "multi"):
        terme = termes.get(famille)
        if not terme:
            continue
        # Le document attendu est le PDF SOURCE DE LA FICHE qui a fourni le
        # terme (la valeur y figure par construction). Un dossier peut porter
        # plusieurs fiches : le scan Phase 0 et le dépôt Lot C ne retiennent
        # pas forcément la même. La comparaison se fait par NOM DE FICHIER :
        # pour la REF importée par upload, l'exemplaire indexé est la copie
        # stagée (même nom, même contenu, même empreinte).
        nom_attendu = Path(RECETTE["refs"][origine[famille]]["pdf_fiche"]).name
        resultats = _tous_les_resultats(app_client, terme)
        assert nom_attendu in resultats, (
            f"recherche {famille} : le document de la fiche d'origine n'est pas ressorti "
            f"({len(resultats)} résultats parcourus)"
        )

    familles_eprouvees = sum(1 for k in ("reference", "client", "bateau", "type", "matiere") if termes.get(k))
    assert familles_eprouvees >= 4, (
        f"seulement {familles_eprouvees} familles de termes éprouvées (attendu ≥ 4)"
    )

    # Recherche FICHES (/recherche) — contrat 2026-09-29 (production-readiness
    # Phase 2.1) : le texte de recherche est rempli DÈS L'ÉCRITURE (migration
    # 018 + ecrire_fiche) — la fiche A_VALIDER déposée est donc retrouvée PAR
    # DÉFAUT, avec son statut exposé pour le badge « non vérifiée ». RG3
    # inchangé sur le fond : la recette ne valide JAMAIS automatiquement, et
    # inclure_a_valider=false restreint à l'archive de confiance.
    reponse = app_client.get("/recherche", params={"q": termes["reference"], "limit": 20})
    assert reponse.status_code == 200, f"recherche fiches (défaut) : HTTP {reponse.status_code}"
    corps = reponse.json()
    resultats = corps.get("resultats") or corps.get("fiches") or []
    fiches_trouvees = {r.get("code"): r.get("statut") for r in resultats if r.get("code")}
    assert termes["reference"] in fiches_trouvees, (
        "contrat 018 rompu : la fiche a_valider déposée doit être retrouvée par défaut"
    )
    assert fiches_trouvees[termes["reference"]] == "a_valider", (
        "le statut doit être exposé (badge « non vérifiée »), jamais masqué"
    )
    reponse = app_client.get(
        "/recherche",
        params={"q": termes["reference"], "inclure_a_valider": "false", "limit": 20},
    )
    assert reponse.status_code == 200, f"recherche fiches (confiance) : HTTP {reponse.status_code}"
    corps_confiance = reponse.json()
    resultats_confiance = corps_confiance.get("resultats") or corps_confiance.get("fiches") or []
    codes_confiance = [r.get("code") for r in resultats_confiance]
    assert termes["reference"] not in codes_confiance, (
        "inclure_a_valider=false : une fiche a_valider est ressortie de l'archive de confiance"
    )
    RECETTE["fiche_attendue"] = termes["reference"]


# ---------------------------------------------------------------------------
# 8. Ouverture d'un PDF : URL présignée RÉELLE (302, Location, 900 s).
# ---------------------------------------------------------------------------


def test_08_ouverture_pdf_url_presignee_reelle(app_client) -> None:
    """Le PDF s'ouvre par redirection 302 vers une URL présignée MinIO vivante."""
    chemin_pdf = RECETTE["refs"]["REF-001"]["technique"]
    reponse = app_client.post("/open", params={"path": chemin_pdf})
    assert reponse.status_code == 302, (
        f"ouverture : HTTP {reponse.status_code} (attendu 302 présigné)"
    )
    location = reponse.headers.get("location")
    assert location, "redirection 302 sans en-tête Location"

    analyse = urllib.parse.urlparse(location)
    parametres = urllib.parse.parse_qs(analyse.query)
    expiration = parametres.get("X-Amz-Expires", [None])[0]
    assert expiration is not None, "URL présignée sans paramètre X-Amz-Expires"
    assert 0 < int(expiration) <= EXPIRATION_PRESIGNEE_S, (
        f"expiration {expiration} s hors borne (attendu ≤ {EXPIRATION_PRESIGNEE_S} s)"
    )
    hote_attendu = urllib.parse.urlparse(S3_ENDPOINT).netloc
    assert analyse.netloc == hote_attendu, "l'URL présignée ne pointe pas vers le S3 configuré"

    # Téléchargement RÉEL de l'URL présignée (HTTP, hors TestClient).
    with urllib.request.urlopen(location, timeout=60) as telechargement:
        assert telechargement.status == 200
        contenu = telechargement.read()
    assert hashlib.sha256(contenu).hexdigest() == _empreinte(Path(chemin_pdf)), (
        "le contenu téléchargé via l'URL présignée diffère du fichier source (SHA-256)"
    )


# ---------------------------------------------------------------------------
# 9. Rapport PDF d'import téléchargeable.
# ---------------------------------------------------------------------------


def test_09_rapport_pdf_telecharge(app_client) -> None:
    """Le rapport d'import se télécharge (302 présigné ou 200 direct) en PDF."""
    import_id = RECETTE["refs"]["REF-001"]["import_id"]
    reponse = app_client.get(f"/imports/{import_id}/artifacts/report_pdf")
    assert reponse.status_code in {200, 302}, (
        f"téléchargement rapport : HTTP {reponse.status_code}"
    )
    if reponse.status_code == 302:
        location = reponse.headers.get("location")
        assert location, "302 rapport sans Location"
        with urllib.request.urlopen(location, timeout=60) as telechargement:
            assert telechargement.status == 200
            contenu = telechargement.read()
    else:
        contenu = reponse.content
    assert contenu[:5] == b"%PDF-", "le rapport téléchargé n'est pas un PDF"


# ---------------------------------------------------------------------------
# 10. Sauvegarde puis restauration de la base réelle dans une base neuve.
# ---------------------------------------------------------------------------


def test_10_sauvegarde_restauration_base_neuve(
    app_client, dossiers: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sauver → restaurer dans une base NEUVE → fiches, object_key, recherche,
    ouverture PDF revérifiés sur la base restaurée."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.recherche import rechercher_fiches
    from seamtech_search.sauvegarde import restaurer, sauver, verifier

    client = _client_s3(S3_BACKUP_BUCKET)
    client.ensure_bucket_exists()
    monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path / "sauvegarde-tmp"))

    # 10.1 SAUVEGARDE de la base réelle (dump + manifeste + envoi MinIO vérifié).
    manifeste = sauver(URL_BASE, [RACINE_CORPUS], tmp_path / "backups", client_s3=client, retention=2)
    assert manifeste["dump"]["octets"] > 0, "dump vide"
    assert manifeste["dump"]["sha256"], "dump sans empreinte"
    assert manifeste["dump"]["cle_s3"], "dump non envoyé hors-site"
    assert manifeste["fiches_par_statut"].get("a_valider") == NB_ZIP, (
        f"manifeste : {manifeste['fiches_par_statut']} (attendu {NB_ZIP} a_valider)"
    )

    # 10.2 RESTAURATION dans une base NEUVE (l'originale reste en place).
    assert URL_BASE_RESTAUREE and URL_BASE_RESTAUREE != URL_BASE, (
        "base de restauration mal configurée (SEAMTECH_RECETTE_DATABASE_RESTAURATION)"
    )
    resume = restaurer(URL_BASE_RESTAUREE, dict(manifeste), client_s3=client)
    assert resume["base"], "restauration sans nom de base"

    # 10.3 La base restaurée porte les fiches et les object_key.
    index = SearchIndex(Path("/tmp/recette-restauree.db"), URL_BASE_RESTAUREE)
    try:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche")
                nb_fiches = int(cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM documents WHERE object_key IS NOT NULL")
                nb_cles = int(cursor.fetchone()[0])
    finally:
        index.close()
    assert nb_fiches == NB_ZIP, f"base restaurée : {nb_fiches} fiches (attendu {NB_ZIP})"
    assert nb_cles >= NB_ZIP, f"base restaurée : {nb_cles} object_key (attendu ≥ {NB_ZIP})"

    # 10.4 Vérification croisée complète (manifeste vs base d'origine).
    resultat_verif = verifier(URL_BASE, manifeste, [RACINE_CORPUS])
    assert resultat_verif["ok"], f"écarts de restauration : {resultat_verif['ecarts']}"

    # 10.5 La RECHERCHE fonctionne sur la base restaurée :
    # - DOCUMENTS (/search) : le document technique de REF-001 est retrouvé
    #   par le CODE de sa fiche (le search_vector des documents est restauré
    #   avec la base) ;
    # - FICHES (/recherche) : contrat 2026-09-29 (Phase 2.1) — la fiche
    #   A_VALIDER reste cherchable PAR DÉFAUT après restauration (texte de
    #   recherche rempli à l'écriture, migration 018), statut exposé pour le
    #   badge ; inclure_a_valider=False restreint à l'archive de confiance.
    code_ref001 = RECETTE["refs"]["REF-001"]["fiche"]
    index = SearchIndex(Path("/tmp/recette-restauree-2.db"), URL_BASE_RESTAUREE)
    try:
        # Utiliser la fiche REF-001 directement : cette étape ne doit pas
        # dépendre de l'état partagé écrit à la fin du test 07, qui peut échouer.
        reponse_defaut = rechercher_fiches(index, requete=code_ref001)
        lignes_defaut = reponse_defaut.get("resultats") or reponse_defaut.get("fiches") or []
        statuts_defaut = {r.get("code"): r.get("statut") for r in lignes_defaut if r.get("code")}
        reponse_confiance = rechercher_fiches(index, requete=code_ref001, inclure_a_valider=False)
        codes_confiance = [
            r.get("code")
            for r in (reponse_confiance.get("resultats") or reponse_confiance.get("fiches") or [])
        ]
        documents = index.search(code_ref001, limit=50)
    finally:
        index.close()
    nom_technique = Path(RECETTE["refs"]["REF-001"]["technique"]).name
    assert any(Path(d.get("path") or "").name == nom_technique for d in documents), (
        "la recherche de documents ne retrouve pas le technique de REF-001 sur la base restaurée"
    )
    assert statuts_defaut.get(code_ref001) == "a_valider", (
        "contrat 018 rompu sur la base restaurée : la fiche a_valider doit rester "
        "cherchable par défaut, statut exposé (badge « non vérifiée »)"
    )
    assert code_ref001 not in codes_confiance, (
        "inclure_a_valider=False : une fiche a_valider est ressortie de l'archive "
        "de confiance sur la base restaurée"
    )

    # 10.6 OUVERTURE PDF depuis la base restaurée : 302 présigné, contenu identique.
    # L'app restaurée doit garder les MÊMES racines que la session d'origine :
    # le document technique de REF-001 est la copie STAGÉE (répertoire de
    # travail de la session) — sous d'autres racines, /open répondrait 403
    # « outside configured search roots ».
    assert RECETTE.get("travail"), "le répertoire de travail de la session n'a pas été mémorisé"
    app = create_app(_config_recette(URL_BASE_RESTAUREE, Path(RECETTE["travail"])))
    chemin_pdf = RECETTE["refs"]["REF-001"]["technique"]
    with TestClient(app, follow_redirects=False) as client_rest:
        reponse = client_rest.post(
            "/open", params={"path": chemin_pdf}, headers={"X-SEAMTECH-TOKEN": JETON}
        )
        assert reponse.status_code == 302, (
            f"ouverture sur base restaurée : HTTP {reponse.status_code}"
        )
        location = reponse.headers.get("location")
        assert location, "302 sans Location sur base restaurée"
        with urllib.request.urlopen(location, timeout=60) as telechargement:
            assert telechargement.status == 200
            contenu = telechargement.read()
    assert hashlib.sha256(contenu).hexdigest() == _empreinte(Path(chemin_pdf)), (
        "contenu PDF différent après restauration (SHA-256)"
    )


# ---------------------------------------------------------------------------
# 11. ZIP inchangés après la recette (RG13).
# ---------------------------------------------------------------------------


def test_11_zip_inchanges_apres_recette(empreintes_zip_avant: dict[str, str]) -> None:
    """RG13 : les empreintes AVANT et APRÈS la recette sont identiques."""
    zips = sorted(RACINE_DEPOT.glob("*.zip"))
    assert len(zips) == NB_ZIP, f"attendu {NB_ZIP} ZIP après recette, trouvé {len(zips)}"
    modifie = [
        nom
        for nom, empreinte in empreintes_zip_avant.items()
        if _empreinte(RACINE_DEPOT / nom) != empreinte
    ]
    assert not modifie, f"ZIP modifiés pendant la recette : {len(modifie)} fichier(s)"
