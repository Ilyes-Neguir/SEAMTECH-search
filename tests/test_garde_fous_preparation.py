"""Garde-fous — préparation de l'arrivée de l'archive (chantier 5).

Audite et renforce les protections listées dans le lot de préparation :

A. Intégrité de la source — RG13 de bout en bout (préflight + inventaire +
   OCR d'échantillon) : aucune écriture dans la source, chemins relatifs et
   absolus, espaces/accents, liens symboliques, détection des changements
   SHA-256 / taille / mtime (compléments dans test_preflight_archive.py).
B. RG14 — AUCUNE dépendance réseau dans le code de traitement, par analyse
   AST répo-large (seamtech_search/ + scripts/), exceptions DOCUMENTÉES.
C. Reprise — les scénarios (Ctrl-C, SIGTERM, budget, verrou, verrou obsolète,
   manifeste corrompu, déjà traité, fichier modifié) sont couverts par
   test_ocr_etages.py / test_ocr_comportement.py : ce fichier PROTÈGE ces
   tests contre la suppression (« ne supprime pas les garde-fous existants »).
D. Sécurité des logs — aucun secret (mot de passe, token, URL avec
   identifiants) dans les rapports OCR ni dans les messages d'erreur.
E. Configuration — aucun chemin de production codé en dur, aucun secret par
   défaut, aucune URL externe implicite, aucun mode production activé par
   défaut, aucun traitement massif par défaut.

Plus : les modèles de validation humaine (chantier 3) sont présents,
documentés, et exempts de données réelles (EXEMPLE_SYNTHETIQUE uniquement).
"""

from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

RACINE = Path(__file__).resolve().parent.parent
FIXTURES_OCR = RACINE / "tests" / "fixtures" / "ocr"
FAKE_TESSERACT = FIXTURES_OCR / "fake_tesseract.py"
PDF_PROPRE = FIXTURES_OCR / "ocr_propre.pdf"
SUIVI_CSV = RACINE / "docs" / "templates" / "SUIVI_VALIDATION_REFERENCE.csv"
SUIVI_COLONNES = RACINE / "docs" / "templates" / "SUIVI_VALIDATION_REFERENCE.colonnes.md"
FICHE_MODELE = RACINE / "docs" / "templates" / "FICHE_VALIDATION_REFERENCE.md"
PROTOCOLE = RACINE / "docs" / "PROTOCOLE_VALIDATION_HUMAINE.md"


# ---------------------------------------------------------------------------
# A. Intégrité de la source — RG13 de bout en bout
# ---------------------------------------------------------------------------


def _empreintes_arbre(source: Path) -> dict[str, tuple[str, float]]:
    etat: dict[str, tuple[str, float]] = {}
    for chemin in sorted(source.rglob("*")):
        if chemin.is_file():
            etat[str(chemin.relative_to(source))] = (hashlib.sha256(chemin.read_bytes()).hexdigest(), chemin.stat().st_mtime)
    return etat


def test_rg13_bout_en_bout_source_intacte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Préflight + inventaire + run OCR d'échantillon : source bit pour bit intacte."""
    source = tmp_path / "Pièces clients Été"  # espaces + accents (bureau Windows)
    source.mkdir()
    if PDF_PROPRE.exists():
        (source / "scan été.pdf").write_bytes(PDF_PROPRE.read_bytes())
    (source / "notes.txt").write_text("repère synthétique", encoding="utf-8")

    travail = tmp_path / "travail"
    sortie = tmp_path / "rapports"
    avant = _empreintes_arbre(source)

    # Préflight (binaires fake déterministes, hors PATH hôte)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tesseract").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "tesseract 5.3.4-fake"; exit 0; fi\n'
        'if [ "$1" = "--list-langs" ]; then printf "List of available languages (2):\\neng\\nfra\\n"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "pdftoppm").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for binaire in ("tesseract", "pdftoppm"):
        (bin_dir / binaire).chmod((bin_dir / binaire).stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))

    spec = importlib.util.spec_from_file_location("preflight_archive_gf", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_gf"] = pf
    spec.loader.exec_module(pf)

    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(travail), "--sortie", str(sortie), "--min-libre-o", "0", "--echantillon-pdfs", "2"]
    )
    assert code == 0

    # OCR sur la copie d'échantillon JAMAIS dans la source : on traite le
    # dossier de travail préparé par l'opérateur (contrat RG13 du pipeline).
    monkeypatch.setenv("SEAMTECH_OCR_TRAVAIL_DIR", str(travail))
    from seamtech_search.ocr.cli import main as ocr_main

    rc = ocr_main(["nuit", "--dossier", str(source), "--limite", "2", "--tesseract-command", str(FAKE_TESSERACT), "--json"])
    assert rc == 0

    apres = _empreintes_arbre(source)
    assert avant == apres, "la source a été modifiée (interdit — RG13)"


def test_chemins_relatifs_et_absolus_equivalents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le préflight accepte les deux formes de chemins et ne diverge pas."""
    source = tmp_path / "archive_rel"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"%PDF-1.4")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tesseract").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "tesseract 5.3.4-fake"; exit 0; fi\n'
        'if [ "$1" = "--list-langs" ]; then printf "List of available languages (2):\\neng\\nfra\\n"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "pdftoppm").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for binaire in ("tesseract", "pdftoppm"):
        (bin_dir / binaire).chmod((bin_dir / binaire).stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))

    spec = importlib.util.spec_from_file_location("preflight_archive_rel", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_rel"] = pf
    spec.loader.exec_module(pf)

    # absolu
    code_abs = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t1"), "--sortie", str(tmp_path / "s1"), "--min-libre-o", "0"])
    # relatif (depuis tmp_path)
    monkeypatch.chdir(tmp_path)
    code_rel = pf.main(["preflight", "--source", "archive_rel", "--travail", "t2", "--sortie", "s2", "--min-libre-o", "0"])
    assert code_abs == code_rel == 0
    rapport = json.loads((tmp_path / "s2" / "preflight_rapport.json").read_text(encoding="utf-8"))
    assert Path(rapport["source"]).resolve() == source.resolve()


def test_lien_symbolique_source_dans_sortie_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un chemin qui PARAÎT hors source mais y retombe par lien est refusé."""
    source = tmp_path / "archive"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"%PDF-1.4")
    leurre = tmp_path / "sortie_exterieure"
    try:
        leurre.symlink_to(source, target_is_directory=True)  # retombe DANS la source
    except OSError:
        pytest.skip("symlinks indisponibles")
    spec = importlib.util.spec_from_file_location("preflight_archive_sym", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_sym"] = pf
    spec.loader.exec_module(pf)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(leurre / "rapports"), "--min-libre-o", "0"])
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


# ---------------------------------------------------------------------------
# B. RG14 — aucune dépendance réseau répo-large (analyse AST)
# ---------------------------------------------------------------------------

MODULES_RESEAU_INTERDITS = {
    "requests",
    "httpx",
    "socket",
    "ssl",
    "ftplib",
    "telnetlib",
    "smtplib",
    "poplib",
    "imaplib",
    "http",
    "urllib.request",
    "urllib.error",
    "urllib.response",
}

# Exceptions DOCUMENTÉES (vérifiées plus bas) — aucune autre n'est admise.
EXCEPTIONS_RG14: dict[str, str] = {
    "seamtech_search/ml/telecharger.py": (
        "téléchargement EXPLICITE des poids e5-small ONNX — commande opérateur "
        "unique (python -m seamtech_search.ml.telecharger), jamais au runtime ; "
        "échec fort sans repli silencieux"
    ),
    "scripts/mesure_assistant.py": (
        "outil de MESURE RG14 : import socket volontaire pour prouver qu'une "
        "connexion sortante est impossible (namespace réseau isolé), aucun "
        "appel sortant à l'exécution normale"
    ),
}

DOCS_MARQUEURS_EXCEPTIONS = {
    "seamtech_search/ml/telecharger.py": "EXPLICITE",
    "scripts/mesure_assistant.py": "socket",
}


def _fichiers_python_audites() -> list[Path]:
    fichiers = []
    for racine in (RACINE / "seamtech_search", RACINE / "scripts"):
        for chemin in sorted(racine.rglob("*.py")):
            if "__pycache__" in chemin.parts:
                continue
            fichiers.append(chemin)
    return fichiers


def _modules_importes(chemin: Path) -> set[str]:
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                modules.add(alias.name)
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.module:
                modules.add(noeud.module)
    return modules


def test_rg14_aucune_dependance_reseau_repolarge() -> None:
    """RG14 : ni requests, ni httpx, ni urllib réseau, ni socket, ni http.client.

    Exceptions documentées uniquement (liste figée ci-dessus) ; toute nouvelle
    dépendance réseau doit être justifiée ici ET dans la doc.
    """
    violations: list[str] = []
    for chemin in _fichiers_python_audites():
        rel = str(chemin.relative_to(RACINE)).replace(os.sep, "/")
        modules = _modules_importes(chemin)
        for module in sorted(modules):
            racine_module = module.split(".")[0]
            cible = module if module in MODULES_RESEAU_INTERDITS else (racine_module if racine_module in MODULES_RESEAU_INTERDITS else None)
            if cible is None:
                continue
            # urllib.parse : analyse d'URL pure, aucun réseau (sauvegarde.py).
            if module == "urllib.parse":
                continue
            if rel in EXCEPTIONS_RG14:
                continue
            violations.append(f"{rel} importe {module} (interdit RG14)")
    assert not violations, "dépendances réseau trouvées : " + "; ".join(violations)


def test_rg14_exceptions_documentees_et_signalees() -> None:
    """Chaque exception RG14 reste documentée dans son module ET justifiée ici."""
    for rel, justification in EXCEPTIONS_RG14.items():
        chemin = RACINE / rel
        assert chemin.exists(), f"exception RG14 orpheline : {rel}"
        assert len(justification) > 40, f"justification trop vague pour {rel}"
        contenu = chemin.read_text(encoding="utf-8")
        marqueur = DOCS_MARQUEURS_EXCEPTIONS[rel]
        assert marqueur in contenu, f"{rel} doit rappeler son statut d'exception ({marqueur})"


def test_rg14_aucune_url_externe_hors_exception() -> None:
    """Aucune URL https:// dans le code hors le module de téléchargement explicite."""
    suspects: list[str] = []
    for chemin in _fichiers_python_audites():
        rel = str(chemin.relative_to(RACINE)).replace(os.sep, "/")
        if rel in EXCEPTIONS_RG14:
            continue
        for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
            if "https://" in ligne or "http://" in ligne:
                suspects.append(f"{rel}:{numero}")
    assert not suspects, f"URL externes implicites : {suspects}"


# ---------------------------------------------------------------------------
# C. Reprise — les tests de scénarios existants sont PROTÉGÉS
# ---------------------------------------------------------------------------

TESTS_REPRISE_IMPOSES = {
    "tests/test_ocr_etages.py": (
        "test_idempotence_meme_empreinte_jamais_retraite",
        "test_rg13_aucune_ecriture_dossier_source",
        "test_verrou_second_lancement_refuse_proprement",
        "test_reprise_apres_interruption",
        "test_budget_respecte_a_la_minute",
    ),
    "tests/test_ocr_comportement.py": (
        "test_cli_nuit_budget_atteint",
        "test_cli_nuit_verrou_deja_present",
        "test_reprise_apres_interruption_et_empreinte_inchangee",
        "test_reprise_apres_modification_empreinte",
        "test_reprise_apres_sigterm_simulation",
        "test_verrou_stale_et_corrompu",
        "test_etat_corrompu_et_sauvegarde",
    ),
    "tests/test_inventaire_archive.py": (
        "test_sortie_refusee_dans_l_archive",
        "test_archive_inchangee_apres_inventaire",
    ),
}


def test_reprise_et_integrite_toujours_presents() -> None:
    """« Ne supprime pas les garde-fous existants » : les scénarios de reprise
    (arrêt, verrou, manifeste corrompu, déjà traité, fichier modifié, budget)
    et d'intégrité source restent couverts par leurs tests nommés."""
    manquants: list[str] = []
    for fichier, noms in TESTS_REPRISE_IMPOSES.items():
        contenu = (RACINE / fichier).read_text(encoding="utf-8")
        for nom in noms:
            if f"def {nom}(" not in contenu:
                manquants.append(f"{fichier}::{nom}")
    assert not manquants, f"tests de garde-fous supprimés : {manquants}"


# ---------------------------------------------------------------------------
# D. Sécurité des logs — aucun secret dans les rapports ni les erreurs
# ---------------------------------------------------------------------------


def test_rapports_ocr_sans_identifiants_de_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--database-url avec identifiants : jamais recopié dans rapports ni stderr."""
    from seamtech_search.ocr.cli import main as ocr_main

    secret = "MotDePasseUltraSecret42"
    source = tmp_path / "src"
    source.mkdir()
    if PDF_PROPRE.exists():
        (source / "scan.pdf").write_bytes(PDF_PROPRE.read_bytes())
    travail = tmp_path / "travail"
    monkeypatch.setenv("SEAMTECH_OCR_TRAVAIL_DIR", str(travail))
    rc = ocr_main(
        [
            "nuit",
            "--dossier",
            str(source),
            "--tesseract-command",
            str(FAKE_TESSERACT),
            "--json",
            "--database-url",
            f"postgresql://seamtech:{secret}@localhost:5432/seamtech",
        ]
    )
    assert rc == 0  # le staging est optionnel et non bloquant
    rapports = list((travail / "rapports").glob("*"))
    assert rapports, "aucun rapport OCR produit"
    for rapport in rapports:
        contenu = rapport.read_text(encoding="utf-8", errors="replace")
        assert secret not in contenu, f"secret de base de données dans {rapport.name}"


def test_masquer_identifiants_redige_le_mot_de_passe_url() -> None:
    from seamtech_search.ocr.cli import _masquer_identifiants

    message = "échec connexion postgresql://seamtech:MotDePasseUltraSecret42@localhost:5432/seamtech indisponible"
    rendu = _masquer_identifiants(message)
    assert "MotDePasseUltraSecret42" not in rendu
    assert "seamtech:***@localhost" in rendu  # lisible : utilisateur et hôte conservés


def test_erreur_staging_base_ne_revele_pas_les_identifiants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Même si l'exception de la base est bavarde, stderr reste sans secret."""
    from seamtech_search.ocr import cli as ocr_cli

    secret = "MotDePasseUltraSecret42"
    source = tmp_path / "src"
    source.mkdir()
    if PDF_PROPRE.exists():
        (source / "scan.pdf").write_bytes(PDF_PROPRE.read_bytes())
    monkeypatch.setenv("SEAMTECH_OCR_TRAVAIL_DIR", str(tmp_path / "travail"))

    class _BaseBavarde:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(f"connexion à postgresql://seamtech:{secret}@localhost:5432/seamtech refusée")

    monkeypatch.setattr("seamtech_search.indexer.SearchIndex", _BaseBavarde)
    rc = ocr_cli.main(
        [
            "nuit",
            "--dossier",
            str(source),
            "--tesseract-command",
            str(FAKE_TESSERACT),
            "--database-url",
            f"postgresql://seamtech:{secret}@localhost:5432/seamtech",
            "--json",
        ]
    )
    assert rc == 0  # staging non bloquant
    sortie = capsys.readouterr()
    assert secret not in sortie.err, "le mot de passe de base est sorti sur stderr"
    assert secret not in sortie.out
    for rapport in (tmp_path / "travail" / "rapports").glob("*"):
        assert secret not in rapport.read_text(encoding="utf-8", errors="replace")


def test_erreur_interne_preflight_ne_revele_pas_le_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Les erreurs internes sont lisibles (type d'erreur) mais pas bavardes."""
    spec = importlib.util.spec_from_file_location("preflight_archive_err", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_err"] = pf
    spec.loader.exec_module(pf)

    secret = "JetonSuperSecret999"

    def _exploser(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(f"échec avec token={secret}")

    monkeypatch.setattr(pf, "executer_preflight", _exploser)
    code = pf.main(["preflight", "--source", str(tmp_path), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s")])
    assert code == pf.CODE_ERREUR == 1
    sortie = capsys.readouterr()
    assert secret not in sortie.err
    assert secret not in sortie.out
    assert "RuntimeError" in sortie.err  # lisible : le type est communiqué


def test_rapports_preflight_ocr_ne_recopient_pas_les_variables_environnement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Balayage large : aucun marqueur secret injecté dans les rapports produits."""
    marqueur = "MarqueurSecretXYZ789"
    for nom in ("SEAMTECH_AUTH_TOKEN", "SEAMTECH_UI_PASSWORD", "SEAMTECH_SESSION_SECRET", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "REDIS_PASSWORD"):
        monkeypatch.setenv(nom, marqueur)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"%PDF-1.4")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tesseract").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "tesseract 5.3.4-fake"; exit 0; fi\n'
        'if [ "$1" = "--list-langs" ]; then printf "List of available languages (2):\\neng\\nfra\\n"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "pdftoppm").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for binaire in ("tesseract", "pdftoppm"):
        (bin_dir / binaire).chmod((bin_dir / binaire).stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("SEAMTECH_OCR_TRAVAIL_DIR", str(tmp_path / "travail"))

    spec = importlib.util.spec_from_file_location("preflight_archive_sec", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_sec"] = pf
    spec.loader.exec_module(pf)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "travail"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0", "--staging"])
    assert code in (0, 8)  # staging sans postgres : code 8 propre, pas de crash
    for rapport in (tmp_path / "s").glob("*"):
        assert marqueur not in rapport.read_text(encoding="utf-8"), f"secret environnement dans {rapport.name}"

    from seamtech_search.ocr.cli import main as ocr_main

    rc = ocr_main(["nuit", "--dossier", str(source), "--tesseract-command", str(FAKE_TESSERACT), "--json"])
    assert rc == 0
    for rapport in (tmp_path / "travail" / "rapports").glob("*"):
        assert marqueur not in rapport.read_text(encoding="utf-8", errors="replace"), f"secret environnement dans {rapport.name}"


# ---------------------------------------------------------------------------
# E. Configuration — pas de valeur de production cachée
# ---------------------------------------------------------------------------


CHEMINS_PRODUCTION_INTERDITS = (
    "/home/",
    "C:\\Users",
    "C:/Users",
    "\\\\serveur",
    "//serveur",
    "/mnt/archive",
    "/Volumes/",
    "192.168.",
    "10.0.0.",
)


def test_aucun_chemin_de_production_code_en_dur() -> None:
    suspects: list[str] = []
    for chemin in _fichiers_python_audites():
        rel = str(chemin.relative_to(RACINE)).replace(os.sep, "/")
        for numero, ligne in enumerate(chemin.read_text(encoding="utf-8").splitlines(), 1):
            for motif in CHEMINS_PRODUCTION_INTERDITS:
                if motif in ligne:
                    suspects.append(f"{rel}:{numero} ({motif})")
    for ps1 in sorted((RACINE / "scripts").glob("*.ps1")):
        for numero, ligne in enumerate(ps1.read_text(encoding="utf-8").splitlines(), 1):
            for motif in CHEMINS_PRODUCTION_INTERDITS:
                if motif in ligne:
                    suspects.append(f"scripts/{ps1.name}:{numero} ({motif})")
    assert not suspects, f"chemins de production codés en dur : {suspects}"


def test_aucun_secret_par_defaut_dans_env_example() -> None:
    """Les secrets de .env.example valent 'change-me' (ou vide) — jamais un vrai."""
    contenu = (RACINE / ".env.example").read_text(encoding="utf-8")
    lignes_secret = [ligne for ligne in contenu.splitlines() if ligne.startswith(("POSTGRES_PASSWORD=", "SEAMTECH_AUTH_TOKEN=", "SEAMTECH_UI_PASSWORD=", "SEAMTECH_SESSION_SECRET="))]
    assert lignes_secret, "secrets attendus dans .env.example"
    for ligne in lignes_secret:
        valeur = ligne.split("=", 1)[1].strip()
        assert valeur in ("", "change-me"), f"secret par défaut non neutre : {ligne.split('=')[0]}={valeur!r}"


def test_aucune_url_par_defaut_dans_la_configuration() -> None:
    """Aucun endpoint externe activé par défaut (S3/Redis/DB null)."""
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[Path("/tmp/racine-test-synthetique")])
    assert config.s3_endpoint_url is None
    assert config.redis_url is None
    assert config.database_url is None
    assert config.auth_token is None
    assert config.allow_network_access is False


def test_aucun_mode_production_active_par_defaut() -> None:
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[Path("/tmp/racine-test-synthetique")])
    assert config.enable_ocr is False  # OCR opt-in
    assert config.enable_legacy_office is False
    assert config.behind_tls_proxy is False
    assert config.delete_local_after_upload is False


def test_aucun_traitement_massif_par_defaut() -> None:
    """L'échantillonnage par défaut reste borné (25 PDF, 100 empreintes max)."""
    spec = importlib.util.spec_from_file_location("preflight_archive_cfg", RACINE / "scripts" / "preflight_archive.py")
    assert spec is not None and spec.loader is not None
    pf = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive_cfg"] = pf
    spec.loader.exec_module(pf)
    assert 20 <= pf.ECH_PDF_DEFAUT <= 30
    assert pf.ECH_DOSSIERS_DEFAUT == 0
    assert pf.LIMITE_EMPREINTES_DEFAUT <= 200


# ---------------------------------------------------------------------------
# Modèles de validation humaine — structure et propreté
# ---------------------------------------------------------------------------

COLONNES_ATTENDUES = [
    "code_fiche",
    "chemin_document",
    "version_gabarit",
    "champ",
    "valeur_extraite",
    "valeur_attendue",
    "resultat",
    "commentaire",
    "page",
    "zone_pdf",
    "operateur_correction",
    "validateur",
    "date",
    "controle_secondaire",
    "statut_final",
]


def test_suivi_validation_csv_conforme_et_documente() -> None:
    assert SUIVI_CSV.exists(), "docs/templates/SUIVI_VALIDATION_REFERENCE.csv manquant"
    with SUIVI_CSV.open(encoding="utf-8-sig", newline="") as flux:
        lecteur = csv.reader(flux, delimiter=";")
        lignes = list(lecteur)
    assert lignes[0] == COLONNES_ATTENDUES, "colonnes du CSV non conformes au protocole"
    for ligne in lignes[1:]:
        assert len(ligne) == len(COLONNES_ATTENDUES), f"ligne CSV mal formée : {ligne}"
        assert "EXEMPLE_SYNTHETIQUE" in ligne[0], "donnée non synthétique dans le modèle (interdit)"
    documentation = SUIVI_COLONNES.read_text(encoding="utf-8")
    for colonne in COLONNES_ATTENDUES:
        assert colonne in documentation, f"colonne non documentée : {colonne}"


def test_csv_nombre_et_ordre_des_colonnes() -> None:
    """Test DÉDIÉ : exactement 15 colonnes, dans l'ordre documenté (import contrôlé)."""
    with SUIVI_CSV.open(encoding="utf-8-sig", newline="") as flux:
        lignes = list(csv.reader(flux, delimiter=";"))
    assert len(COLONNES_ATTENDUES) == 15
    for numero, ligne in enumerate(lignes):
        assert len(ligne) == 15, f"ligne {numero} : {len(ligne)} colonnes au lieu de 15 (séparateur parasite ?)"
    assert lignes[0] == COLONNES_ATTENDUES  # ordre exact, tête de fichier


def test_csv_encodage_utf8_sig() -> None:
    """Test DÉDIÉ : le fichier est encodé utf-8-sig (BOM EF BB BF, Excel)."""
    brut = SUIVI_CSV.read_bytes()
    assert brut[:3] == b"\xef\xbb\xbf", "BOM utf-8-sig absent du CSV de suivi"
    SUIVI_CSV.read_text(encoding="utf-8-sig")  # décodable sans erreur


def test_csv_separateur_point_virgule() -> None:
    """Test DÉDIÉ : séparateur ';' — ni virgule ni tabulation (zone_pdf est quotée)."""
    with SUIVI_CSV.open(encoding="utf-8-sig", newline="") as flux:
        lignes = list(csv.reader(flux, delimiter=";"))
    assert lignes[0][0] == "code_fiche" and len(lignes[0]) == 15
    # Avec la virgule comme séparateur, la tête ne doit PAS se découper en 15 :
    with SUIVI_CSV.open(encoding="utf-8-sig", newline="") as flux:
        lignes_virgule = list(csv.reader(flux, delimiter=","))
    assert len(lignes_virgule[0]) != 15, "la virgule ne doit pas être le séparateur"
    # Aucune tabulation dans le fichier brut (après retrait du BOM).
    assert "\t" not in SUIVI_CSV.read_text(encoding="utf-8-sig")


def test_csv_lecture_dictreader_correcte() -> None:
    """Test DÉDIÉ : lecture csv.DictReader — futur import contrôlé."""
    with SUIVI_CSV.open(encoding="utf-8-sig", newline="") as flux:
        lignes = list(csv.DictReader(flux, delimiter=";"))
    assert lignes, "CSV de suivi vide"
    assert list(lignes[0].keys()) == COLONNES_ATTENDUES
    for ligne in lignes:
        assert ligne["resultat"] in ("OK", "ABSENTE", "NON_APPLICABLE", "CORRIGEE", "ANOMALIE")
        assert ligne["statut_final"] in ("VALIDEE", "A_CORRIGER", "REJETEE")
        assert ligne["chemin_document"].startswith("<SOURCE>/"), "chemin attendu relatif à <SOURCE>"
        assert all(valeur is not None for valeur in ligne.values()), f"colonne orpheline : {ligne}"


def test_csv_aucun_chemin_absolu() -> None:
    """Le modèle de suivi ne contient aucun chemin absolu (fichier partageable)."""
    texte = SUIVI_CSV.read_text(encoding="utf-8-sig")
    for motif in ("C:\\", "C:/", "/home/", "/Users/", "/data/", "\\\\"):
        assert motif not in texte, f"chemin absolu interdit dans le CSV : {motif}"


def test_fiche_modele_contient_les_rubriques_exigees() -> None:
    assert FICHE_MODELE.exists(), "docs/templates/FICHE_VALIDATION_REFERENCE.md manquant"
    texte = FICHE_MODELE.read_text(encoding="utf-8")
    for rubrique in (
        "Code de fiche",
        "Chemin du document",
        "Version du gabarit",
        "Champ",
        "Valeur extraite",
        "Valeur attendue",
        "Résultat",
        "Commentaire",
        "Page",
        "Zone PDF",
        "Validateur",
        "Date",
        "Statut final",
    ):
        assert rubrique in texte, f"rubrique manquante dans le modèle de fiche : {rubrique}"
    assert "EXEMPLE_SYNTHETIQUE" in texte


def test_protocole_validation_couvre_tous_les_sujets() -> None:
    assert PROTOCOLE.exists(), "docs/PROTOCOLE_VALIDATION_HUMAINE.md manquant"
    texte = PROTOCOLE.read_text(encoding="utf-8").lower()
    for sujet in (
        "20 à 30 fiches",
        "valeur absente",
        "non applicable",
        "correction",
        "anomalie",
        "unité",
        "accent",
        "cote",
        "galon",
        "matériau",
        "jonction",
        "zone",
        "validat",
        "quatre yeux",
    ):
        assert sujet in texte, f"sujet non couvert par le protocole : {sujet}"
