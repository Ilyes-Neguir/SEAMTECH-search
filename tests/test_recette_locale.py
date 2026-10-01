"""Épingles statiques de la recette locale (Phase 2).

La recette « ça marche sur ma machine » est livrée par trois fichiers :

* ``scripts/recette_locale.ps1`` — orchestrateur Windows (commanditaire) ;
* ``scripts/recette_locale.sh`` — variante Linux (CI) ;
* ``scripts/recette_verif.py`` — vérificateur fonctionnel UNIQUE, exécuté dans
  le conteneur web (aucun dérive possible entre les deux orchestrateurs :
  les contrôles fonctionnels vivent au même endroit).

Ces tests n'ont besoin d'aucun Docker : ils épinglent les contrats statiques
qui feraient régresser la recette silencieusement sinon — Tesseract + pack
français dans l'image (l'OCR métier appelle ``tesseract -l fra``), présence et
alignement des contrôles du rapport, secrets générés sur machine vierge, et
branchage CI. Le passage complet de la recette est prouvé par le job CI
``recette-locale``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent
DOCKERFILE = RACINE / "Dockerfile"
COMPOSE = RACINE / "docker-compose.yml"
VERIF = RACINE / "scripts" / "recette_verif.py"
WRAPPER_SH = RACINE / "scripts" / "recette_locale.sh"
WRAPPER_PS1 = RACINE / "scripts" / "recette_locale.ps1"
ENSURE_POSTGRES = RACINE / "scripts" / "ensure_postgres.ps1"
CI = RACINE / ".github" / "workflows" / "ci.yml"
GITIGNORE = RACINE / ".gitignore"
GITATTRIBUTES = RACINE / ".gitattributes"

# Contrôles fonctionnels du vérificateur (sortie CONTROLE|<id>|...).
CONTROLES_VERIF = (
    "compte-recette",
    "depot-archives",
    "suivi-lots",
    "fiches-a-valider",
    "validation-fiche",
    "recherche-texte",
    "recherche-dimension",
    "filtres-facettes",
    "suggestions",
    "pdf-presigne",
    "zone-surlignee",
    "rejeu-idempotent",
    "parcours-recherche",
    "parcours-dossiers",
    "parcours-validation",
    "parcours-fiche",
    "pieces-par-id",
    "pdf-7-fiches-integrite",
    "fichiers-catalogue",
)

# Contrôles tenus par les orchestrateurs (prérequis, pile, sauvegarde).
CONTROLES_WRAPPER = (
    "prereqs",
    "ports-libres",
    "env-secrets",
    "image-minio",
    "pile-sante",
    "sauvegarde",
    "restauration",
    "persistance-volumes",
)


def test_dockerfile_installe_tesseract_et_le_pack_francais() -> None:
    """L'image web doit porter l'OCR métier : binaire tesseract + langue fra.

    L'OCR (seamtech_search/ocr) appelle ``tesseract -l fra`` ; sans
    ``tesseract-ocr`` ET ``tesseract-ocr-fra`` dans l'image, tout PDF scanné
    est dégradé silencieusement (« tesseract absent ») — exactement le défaut
    constaté sur la pile Docker locale. ``poppler-utils`` fournit pdftoppm,
    utilisé pour rendre les pages scannées avant OCR.
    """
    contenu = DOCKERFILE.read_text(encoding="utf-8")
    paquets = ("tesseract-ocr", "tesseract-ocr-fra", "poppler-utils")
    manquants = [p for p in paquets if p not in contenu]
    assert not manquants, f"Dockerfile : paquets OCR absents : {manquants}"


def test_scripts_recette_locale_existants() -> None:
    for chemin in (VERIF, WRAPPER_SH, WRAPPER_PS1):
        assert chemin.is_file(), f"script de recette manquant : {chemin.name}"


def test_recette_verif_declare_tous_les_controles_functionnels() -> None:
    """Le vérificateur unique déclare exactement la liste des contrôles."""
    arbre = ast.parse(VERIF.read_text(encoding="utf-8"))
    constantes = {
        noeud.targets[0].id: noeud.value
        for noeud in arbre.body
        if isinstance(noeud, ast.Assign)
        and len(noeud.targets) == 1
        and isinstance(noeud.targets[0], ast.Name)
    }
    assert "CONTROLES" in constantes, "recette_verif.py doit déclarer CONTROLES"
    declarés = tuple(
        elt.value for elt in constantes["CONTROLES"].elts  # type: ignore[attr-defined]
    )
    assert declarés == CONTROLES_VERIF


def test_recette_etend_les_20_controles_existants_a_27_sans_regression() -> None:
    """Les 8 contrôles d'orchestration + 19 fonctionnels donnent 27 au total.

    Les douze contrôles fonctionnels d'origine restent dans le même ordre ; les
    sept nouveaux couvrent Recherche, Dossiers, Validation, fiche, accès fichier
    par id, intégrité SHA-256 des sept PDF et navigateur Fichiers.
    """
    historiques = (
        "compte-recette", "depot-archives", "suivi-lots", "fiches-a-valider",
        "validation-fiche", "recherche-texte", "recherche-dimension", "filtres-facettes",
        "suggestions", "pdf-presigne", "zone-surlignee", "rejeu-idempotent",
    )
    assert CONTROLES_VERIF[: len(historiques)] == historiques
    assert len(CONTROLES_VERIF) == 19
    assert len(CONTROLES_WRAPPER) == 8
    assert len(CONTROLES_VERIF) + len(CONTROLES_WRAPPER) == 27


def test_recette_epingle_les_sept_pdf_sources_et_gib_sea() -> None:
    """Les 7 PDF principaux sont comparés par SHA-256; GIB SEA est requis."""
    import scripts.recette_verif as recette

    assert len(recette.PDFS_SEPT_FICHES) == 7
    assert any("GIBSEA 284 250328 AJA.pdf" in nom for nom in recette.PDFS_SEPT_FICHES)
    assert all(len(empreinte) == 64 and all(c in "0123456789abcdef" for c in empreinte) for empreinte in recette.PDFS_SEPT_FICHES.values())


def test_scripts_powershell_utf8_bom_crlf_et_gitattributes() -> None:
    """Les scripts PowerShell s'ouvrent en Windows avec BOM UTF-8 et CRLF."""
    attributs = GITATTRIBUTES.read_text(encoding="utf-8")
    assert "*.ps1 text eol=crlf" in attributs
    for script in (RACINE / "scripts").glob("*.ps1"):
        contenu = script.read_bytes()
        assert contenu.startswith(b"\xef\xbb\xbf"), f"{script.name}: BOM UTF-8 absent"
        assert b"\r\n" in contenu and b"\n" not in contenu.replace(b"\r\n", b""), f"{script.name}: CRLF attendu"


def test_proxies_s3_ne_transmettent_pas_les_identifiants_au_stockage() -> None:
    """Les redirects signés sont suivis sans relayer le jeton de service."""
    piece_proxy = (RACINE / "frontend" / "lib" / "piece-proxy.ts").read_text(encoding="utf-8")
    artifacts = (
        RACINE / "frontend" / "app" / "api" / "imports" / "[id]" / "artifacts" / "[artifact]" / "route.ts"
    ).read_text(encoding="utf-8")
    assert 'redirect: "manual"' in piece_proxy
    assert 'if (upstream.status >= 300 && upstream.status < 400)' in piece_proxy
    assert 'redirect: "manual"' in artifacts
    assert 'upstream = await fetch(cible, { cache: "no-store", redirect: "follow" })' in artifacts
    assert '"location"' not in artifacts.split("const RESPONSE_HEADERS", 1)[1].split("]", 1)[0]


def test_anciens_proxys_par_chemin_sont_desactives() -> None:
    for route in ("open", "preview"):
        contenu = (RACINE / "frontend" / "app" / "api" / route / "route.ts").read_text(encoding="utf-8")
        assert "status: 410" in contenu, f"/api/{route} doit refuser l'ancien accès par chemin"


def test_wrappers_partagent_le_verificateur_unique() -> None:
    """Les deux orchestrateurs appellent le MÊME vérificateur (pas de copie)."""
    for wrapper in (WRAPPER_SH, WRAPPER_PS1):
        contenu = wrapper.read_text(encoding="utf-8")
        assert "recette_verif.py" in contenu, (
            f"{wrapper.name} doit exécuter scripts/recette_verif.py"
        )


def test_wrappers_declarent_leurs_controles_et_sortent_en_erreur() -> None:
    for wrapper in (WRAPPER_SH, WRAPPER_PS1):
        contenu = wrapper.read_text(encoding="utf-8")
        manquants = [c for c in CONTROLES_WRAPPER if c not in contenu]
        assert not manquants, (
            f"{wrapper.name} : contrôles wrapper absents du rapport : {manquants}"
        )
        # Code sortie non nul si un seul contrôle échoue (exigence recette).
        assert "exit 1" in contenu, f"{wrapper.name} doit sortir en erreur sur FAIL"


def test_ensure_postgres_genere_tous_les_secrets_exiges_par_compose() -> None:
    """Machine vierge : aucun `${VAR:?}` de compose ne doit rester sans valeur.

    ``docker compose up`` refuse de démarrer tant qu'une variable obligatoire
    est absente ; ensure_postgres.ps1 (appelé par « SEAMTECH Search.cmd ») doit
    donc générer chaque secret local aléatoire, sans jamais les écrire dans Git.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    exiges: set[str] = set()
    brut = COMPOSE.read_text(encoding="utf-8")
    for variable in (
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "REDIS_PASSWORD",
        "SEAMTECH_AUTH_TOKEN",
        "SEAMTECH_UI_PASSWORD",
        "SEAMTECH_SESSION_SECRET",
    ):
        if f"${{{variable}:?" in brut:
            exiges.add(variable)
    assert exiges, "docker-compose.yml : aucune variable obligatoire trouvée ?"
    contenu = ENSURE_POSTGRES.read_text(encoding="utf-8")
    manquantes = sorted(v for v in exiges if v not in contenu)
    assert not manquantes, f"ensure_postgres.ps1 ne génère pas : {manquantes}"
    assert compose["services"], "docker-compose.yml illisible"


def test_ci_declare_le_job_recette_locale() -> None:
    """Le passage de la recette est un fait CI : job dédié + script .sh."""
    ci = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job = ci.get("jobs", {}).get("recette-locale")
    assert job is not None, "ci.yml : job « recette-locale » absent"
    etapes = "\n".join(
        str(e.get("run", "")) for e in job.get("steps", []) if isinstance(e, dict)
    )
    assert "recette_locale.sh" in etapes, (
        "le job recette-locale doit exécuter scripts/recette_locale.sh"
    )


def test_secrets_et_sauvegardes_ne_sont_jamais_dans_git() -> None:
    contenu = GITIGNORE.read_text(encoding="utf-8")
    lignes = {ligne.strip() for ligne in contenu.splitlines()}
    assert ".env" in lignes, ".gitignore doit ignorer .env (secrets locaux)"
    assert "data/" in lignes, ".gitignore doit ignorer data/ (sauvegardes)"


# ---------------------------------------------------------------------------
# Normalisation des sources (RG13 : les sources sont lues, jamais modifiées).
# ---------------------------------------------------------------------------


def _fabriquer_zip(chemin: Path, racine_interne: str) -> None:
    import zipfile

    with zipfile.ZipFile(chemin, "w") as archive:
        archive.writestr(f"{racine_interne}/fiche.pdf", b"%PDF-1.4 recette\n")
        archive.writestr(f"{racine_interne}/annexe.pdf", b"%PDF-1.4 annexe\n")


def test_normalisation_zip_et_dossier_en_affaires(tmp_path: Path, monkeypatch) -> None:
    """Un ZIP + un dossier deviennent deux affaires ; la source reste intacte."""
    from scripts import recette_verif

    sources = tmp_path / "sources"
    sources.mkdir()
    _fabriquer_zip(sources / "AFFAIRE-A.zip", "AFFAIRE-A-20260101")
    dossier = sources / "Dossier été"
    (dossier / "sous").mkdir(parents=True)
    (dossier / "sous" / "fiche.pdf").write_bytes(b"%PDF-1.4\n")
    empreinte_zip = (sources / "AFFAIRE-A.zip").read_bytes()
    empreinte_pdf = (dossier / "sous" / "fiche.pdf").read_bytes()

    travail = tmp_path / "travail"
    monkeypatch.setattr(recette_verif, "SOURCES", sources)
    monkeypatch.setattr(recette_verif, "TRAVAIL", travail)

    affaires = recette_verif._normaliser_sources()

    assert len(affaires) == 2, f"attendu 2 affaires, obtenu : {affaires}"
    racine = travail / "affaires"
    for nom in affaires:
        sous = racine / nom
        assert sous.is_dir()
        assert any(sous.rglob("*.pdf")), f"affaire {nom} sans PDF"
    # RG13 : les sources sont bit pour bit inchangées.
    assert (sources / "AFFAIRE-A.zip").read_bytes() == empreinte_zip
    assert (dossier / "sous" / "fiche.pdf").read_bytes() == empreinte_pdf


def test_normalisation_refuse_la_traversee_d_archive(tmp_path: Path, monkeypatch) -> None:
    """Un ZIP malveillant (../) doit être refusé, pas extrait hors de la zone."""
    import zipfile

    from scripts import recette_verif

    sources = tmp_path / "sources"
    sources.mkdir()
    with zipfile.ZipFile(sources / "malveillant.zip", "w") as archive:
        archive.writestr("../../evil.pdf", b"%PDF-1.4\n")

    travail = tmp_path / "travail"
    monkeypatch.setattr(recette_verif, "SOURCES", sources)
    monkeypatch.setattr(recette_verif, "TRAVAIL", travail)

    with pytest.raises(ValueError, match="traversée"):
        recette_verif._normaliser_sources()
    assert not (tmp_path / "evil.pdf").exists()
