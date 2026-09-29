"""Garde-fou de confidentialité des dépôts (Phase 0, prompt production-readiness).

Complète ``tests/test_empreintes_fixtures.py`` (qui épingle 4 fixtures PDF par
SHA-256) en verrouillant l'INVENTAIRE COMPLET des fichiers documentaires du
dépôt : tout PDF, ZIP ou format machine (``.plx``/``.xin``/``.dxf``/``.db``)
nouvellement suivi par Git — ou modifié — rend ce test ROUGE.

Pourquoi : le dépôt a déjà contenu des documents clients réels (la fiche
7792-SO, les 7 ZIP de dossiers atelier). Le commanditaire exige qu'aucun
document réel ne soit ajouté au dépôt sans décision explicite. Ce test fait
de l'ajout un acte CONSCIENT : pour légitimer un nouveau fichier, il faut
l'ajouter à l'inventaire épinglé ci-dessous avec son SHA-256 mesuré — et un
document réel ne doit JAMAIS y être ajouté (le référencer par empreinte dans
la documentation, le garder hors Git).

Règles :
- un PDF suivi doit être dans l'inventaire épinglé (chemin + SHA-256 + taille) ;
- une archive ZIP suivie doit être dans l'inventaire épinglé ;
- aucun format machine (.plx, .xin, .dxf, .db) ne doit être suivi PAR Git :
  ces fichiers n'existent que DANS les ZIP du corpus, jamais en vrac ;
- aucun PDF ne doit être suivi à la racine du dépôt ; la copie canonique
  7792-SO demeure épinglée sous ``sample_data/``.
- les PDF du répertoire ``tests/fixtures/ocr/`` sont des données SYNTHÉTIQUES
  régénérables explicitement autorisées ; leurs octets ne sont pas épinglés,
  car les métadonnées PDF diffèrent selon les versions de reportlab.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Inventaire épinglé : (chemin relatif, sha256, taille en octets).
# Tout est SYNTHÉTIQUE sauf mention contraire explicite.
INVENTAIRE_EPINGLE: tuple[tuple[str, str, int], ...] = (
    # --- Fiche client RÉELLE 7792-SO (reçue du commanditaire 21/09, vérité
    # --- terrain 74/74 — cf. docs/verite_terrain/EMPREINTES.md). Les 7 ZIP
    # --- réels du corpus 2026-09-28 sont épinglés eux aussi : tant que le
    # --- commanditaire n'a pas décidé de les retirer/purger, toute
    # --- modification de l'un d'eux doit être explicite.
    (
        "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf",
        "43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40",
        166_990,
    ),
    # --- Fixtures SYNTHÉTIQUES ---
    (
        "sample_data/CLIENT-123/fiche-technique.pdf",
        "abf6aaaaa1833f75585a364d240963232ae46d344e63661286f8e4f5e2355803",
        1_938,
    ),
    (
        "sample_data/CLIENT-GENOA/fiche-genois.pdf",
        "3c073703e4a8e9d5fdcdcfa50781896b4e42f72441a1a94041f2d04e06870804",
        1_584,
    ),
    (
        "frontend/e2e/live-fixtures/CLIENT-E2E-TROIS/fiche-trois.pdf",
        "c250b0771b0ac7b8a6a949ec9dc250aae703b9e6338472b19e558d478f5076ac",
        1_589,
    ),
    # --- Corpus réel 2026-09-28 : 7 dossiers atelier (ZIP) ---
    (
        "AQUILA 250216AJA-20260928T182324Z-1-001.zip",
        "fd1fca0930494328687ecf5d6905af139c24f93a8b9e2e51b2f6075772446be6",
        573_860,
    ),
    (
        "ATTALIA 250121JA-20260928T182325Z-1-001.zip",
        "a71ec77d6d5abf3c55f5ca8aa5c79d3347d3b3765b91f54950065b9c2161d699",
        594_852,
    ),
    (
        "BAVARIA 32 - 250604JA-20260928T182326Z-1-001.zip",
        "a9ca298cae78ccbacfaab69260cede299062ef0d20e0a7fba62c3539ac3a538f",
        745_910,
    ),
    (
        "BAVARIA 34 - 250323JA-20260928T182327Z-1-001.zip",
        "483f89716b953734315b7bbb4ba81050dc24b33a162e2a2623604eee268b8f6f",
        618_247,
    ),
    (
        "DAMIEN 4 - 250821JA-20260928T182330Z-1-001.zip",
        "2f1ecb0205b817c3dbdb324bf444aad9fb5ddce1d3f415859698fbb21fb428bf",
        846_621,
    ),
    (
        "DEHLER 39 - 250329AJA-20260928T182330Z-1-001.zip",
        "b3171d070f6b332366263626a665e7f7985bb8406062c2957895401850df6a06",
        919_707,
    ),
    (
        "GIB SEA 284 - 250328AJA-20260928T182331Z-1-001.zip",
        "103359b561ef32f5a8ce04b373864c26a0b496156efc1f728e75f0c0911b4aa3",
        581_169,
    ),
)

# Les trois entrées suivantes conservent les anciens nodeid de la suite.
# Leur contrat a été migré sans perdre leur identité de collecte : le doublon
# racine doit être absent (la copie canonique garde l'empreinte), et les fixtures
# OCR sont validées par structure/contenu, pas par octets non déterministes.
_ANCIENS_NODEID_INVENTAIRE: tuple[tuple[str, str, int], ...] = (
    (
        "7792-SO_ffab.pdf",
        "43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40",
        166_990,
    ),
    (
        "tests/fixtures/ocr/ocr_degrade.pdf",
        "e16b7cdd211ddac3aa41f38f56e9055ddeaaf06b0a419119e26ec126fea5adc7",
        34_546,
    ),
    (
        "tests/fixtures/ocr/ocr_propre.pdf",
        "01fa26a6192b01b3227165c21f5961f3a330f29c04d34a5b382a6eb2baa62c78",
        36_964,
    ),
)

_SUFFIXES_SURVEILLES = {".pdf", ".zip", ".plx", ".xin", ".dxf", ".db"}


def _fichiers_suivis() -> list[str]:
    """Chemins relatifs suivis par Git (triés). Skip si pas un dépôt Git."""
    try:
        sortie = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:  # pas un checkout Git
        pytest.skip(f"inventory guard hors dépôt Git ({exc})")
    return sorted(ligne for ligne in sortie.stdout.splitlines() if ligne.strip())


def test_aucun_document_hors_inventaire_epingle() -> None:
    """Tout PDF/ZIP/format machine suivi doit être épinglé ci-dessus."""
    inventaire = {chemin for chemin, _sha, _taille in INVENTAIRE_EPINGLE}
    suspects = [
        chemin
        for chemin in _fichiers_suivis()
        if Path(chemin).suffix.lower() in _SUFFIXES_SURVEILLES
        and chemin not in inventaire
        and not (chemin.startswith("tests/fixtures/ocr/") and Path(chemin).suffix.lower() == ".pdf")
    ]
    assert not suspects, (
        "Fichier(s) documentaire(s) suivi(s) par Git mais ABSENT(S) de l'inventaire "
        "épinglé de tests/test_confidentialite_depots.py :\n  - "
        + "\n  - ".join(suspects)
        + "\nUn document SYNTHÉTIQUE nécessaire aux tests s'ajoute consciemment à "
        "l'inventaire (chemin + SHA-256 mesuré). Un document client RÉEL ne "
        "s'ajoute JAMAIS : il reste hors Git et est référencé par empreinte."
    )


def test_aucun_format_machine_en_vrac() -> None:
    """Aucun .plx/.xin/.dxf/.db suivi : ces formats ne vivent que DANS les ZIP."""
    inventaire = {chemin for chemin, _sha, _taille in INVENTAIRE_EPINGLE}
    en_vrac = [
        chemin
        for chemin in _fichiers_suivis()
        if Path(chemin).suffix.lower() in {".plx", ".xin", ".dxf", ".db"}
        and chemin not in inventaire
    ]
    assert not en_vrac, f"Formats machine suivis en vrac (interdit) : {en_vrac}"


@pytest.mark.ocr_suite
@pytest.mark.parametrize(
    ("chemin_relatif", "sha256_attendu", "taille_attendue"),
    (*INVENTAIRE_EPINGLE, *_ANCIENS_NODEID_INVENTAIRE),
    ids=[chemin for chemin, _sha, _taille in (*INVENTAIRE_EPINGLE, *_ANCIENS_NODEID_INVENTAIRE)],
)
def test_empreinte_document_epingle(chemin_relatif: str, sha256_attendu: str, taille_attendue: int) -> None:
    """Épingle les documents; les anciens nodeid vérifient explicitement leur migration."""
    chemin = REPO / chemin_relatif
    if chemin_relatif == "7792-SO_ffab.pdf":
        assert not chemin.exists(), "l'ancien doublon PDF à la racine doit rester retiré"
        canonique = REPO / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
        contenu = canonique.read_bytes()
        assert len(contenu) == taille_attendue
        assert hashlib.sha256(contenu).hexdigest() == sha256_attendu
        return
    if chemin_relatif.startswith("tests/fixtures/ocr/"):
        # Compatibilité des anciens nodeid sans imposer le SHA/taille du PDF :
        # ReportLab écrit des métadonnées temporelles/version-dépendantes.
        from pypdf import PdfReader

        assert chemin.is_file(), f"fixture OCR manquante : {chemin_relatif}"
        reader = PdfReader(str(chemin))
        assert len(reader.pages) == 1
        assert not (reader.pages[0].extract_text() or "").strip()
        return
    assert chemin.is_file(), f"document épinglé manquant : {chemin_relatif}"
    contenu = chemin.read_bytes()
    assert len(contenu) == taille_attendue, (
        f"{chemin_relatif} : taille {len(contenu)} != {taille_attendue} (modifié ?)"
    )
    sha256 = hashlib.sha256(contenu).hexdigest()
    assert sha256 == sha256_attendu, (
        f"{chemin_relatif} : SHA-256 modifié ({sha256}). Un document du dépôt ne "
        "change pas sans décision explicite — cf. tests/test_empreintes_fixtures.py."
    )


def test_doublon_racine_7792_identique_a_la_copie_canonique() -> None:
    """Ancien garde conservé : si le doublon existe, il doit être identique; le garde suivant exige son absence."""
    racine = REPO / "7792-SO_ffab.pdf"
    canonique = REPO / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
    if racine.is_file():
        assert racine.read_bytes() == canonique.read_bytes(), (
            "Le PDF racine 7792-SO_ffab.pdf diffère de la copie canonique de "
            "sample_data/ — un document réel ne doit jamais être modifié."
        )


def test_aucun_pdf_a_la_racine_du_depot() -> None:
    """Aucun PDF réel ou synthétique ne doit être exposé à la racine Git."""
    pdf_racine = sorted(p.name for p in REPO.glob("*.pdf") if p.is_file())
    assert not pdf_racine, f"PDF interdit(s) à la racine du dépôt : {pdf_racine}"


def test_ancien_doublon_racine_7792_reste_absent() -> None:
    """Vérifie nommément le retrait du doublon public documenté dans FUSION_MAIN."""
    assert not (REPO / "7792-SO_ffab.pdf").exists()


@pytest.mark.parametrize("nom", ["ocr_degrade.pdf", "ocr_propre.pdf"])
@pytest.mark.ocr_suite
def test_fixture_ocr_synthetique_regenerable_et_lisible(nom: str) -> None:
    """Les 2 PDF OCR autorisés restent présents, lisibles et synthétiques sans épingler leurs octets."""
    from pypdf import PdfReader

    dossier = REPO / "tests/fixtures/ocr"
    attendus = {"ocr_degrade.pdf", "ocr_propre.pdf"}
    pdfs = {p.name for p in dossier.glob("*.pdf")}
    assert pdfs == attendus, f"PDF OCR synthétiques inattendus/manquants : {sorted(pdfs ^ attendus)}"
    reader = PdfReader(str(dossier / nom))
    assert len(reader.pages) == 1
    assert not (reader.pages[0].extract_text() or "").strip(), "la fixture OCR doit rester une image sans couche texte"
