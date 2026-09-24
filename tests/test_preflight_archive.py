"""Tests du préflight d'arrivée d'archive — scripts/preflight_archive.py.

Contrat testé (docs/ARRIVEE_ARCHIVE.md) :
- refus propre d'un chemin vide / absent / inexistant (codes 2 et 3) ;
- refus d'une sortie (ou d'un travail) situé dans la source, liens symboliques
  compris (code 4) ;
- refus d'une configuration ambiguë (code 5) ;
- parcours RÉEL de la source uniquement quand le chemin est fourni
  explicitement — jamais de repli silencieux sur sample_data ;
- production d'un rapport JSON ET d'un rapport texte ;
- échantillon de travail léger (jamais de traitement massif par défaut) ;
- contrôle des empreintes : SHA-256, taille, mtime ;
- RG13 : la source n'est JAMAIS modifiée (empreintes + mtime avant/après) ;
- RG14-compatible : aucune bibliothèque réseau dans le script (audit ailleurs) ;
- aucun secret dans les rapports, chemins masquables pour partage.

Les « fixtures locales » citées ci-dessous sont les données SYNTHÉTIQUES du
dépôt (sample_data/, tests/fixtures/) — en AUCUN cas l'archive réelle.
"""

from __future__ import annotations

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
SCRIPT_PATH = RACINE / "scripts" / "preflight_archive.py"
FIXTURES_LOCALES = RACINE / "sample_data"  # corpus synthétique du dépôt, PAS l'archive réelle


def _load_preflight() -> Any:
    spec = importlib.util.spec_from_file_location("preflight_archive", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["preflight_archive"] = module
    spec.loader.exec_module(module)
    return module


pf = _load_preflight()


# ---------------------------------------------------------------------------
# Outils fake (déterministes quel que soit l'hôte — RG14 : aucun réseau)
# ---------------------------------------------------------------------------


def _fabriquer_binaires(
    tmp_path: Path,
    *,
    avec_tesseract: bool = True,
    avec_fra: bool = True,
    avec_pdftoppm: bool = True,
    avec_psql: bool = False,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if avec_tesseract:
        langues = "eng\nfra\n" if avec_fra else "eng\n"
        (bin_dir / "tesseract").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "tesseract 5.3.4-fake"; exit 0; fi\n'
            'if [ "$1" = "--list-langs" ]; then printf "List of available languages (2):\\n'
            + langues.replace("\n", "\\n")
            + '"; exit 0; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (bin_dir / "tesseract").chmod((bin_dir / "tesseract").stat().st_mode | stat.S_IXUSR)
    if avec_pdftoppm:
        (bin_dir / "pdftoppm").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "pdftoppm").chmod((bin_dir / "pdftoppm").stat().st_mode | stat.S_IXUSR)
    if avec_psql:
        (bin_dir / "psql").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "psql").chmod((bin_dir / "psql").stat().st_mode | stat.S_IXUSR)
    return bin_dir


@pytest.fixture()
def chemin_isole(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """PATH réduit au dossier de faux binaires : aucun outil hôte ne fuit."""

    def _poser(**kwargs) -> Path:
        bin_dir = _fabriquer_binaires(tmp_path, **kwargs)
        monkeypatch.setenv("PATH", str(bin_dir))
        monkeypatch.delenv("SEAMTECH_DATABASE_URL", raising=False)
        monkeypatch.delenv("SEAMTECH_ARCHIVE_SOURCE", raising=False)
        return bin_dir

    return _poser


def _arbre_source(tmp_path: Path) -> Path:
    """Petite archive synthétique (documents de test — jamais de vraie fiche)."""
    source = tmp_path / "ARCHIVE_SYNTHETIQUE"
    (source / "CLIENT-A").mkdir(parents=True)
    (source / "CLIENT-B").mkdir(parents=True)
    (source / "CLIENT-A" / "fiche-a.pdf").write_bytes(b"%PDF-1.4 synthetique A")
    (source / "CLIENT-A" / "plan-a.txt").write_text("plan synthétique", encoding="utf-8")
    (source / "CLIENT-B" / "fiche-b.pdf").write_bytes(b"%PDF-1.4 synthetique B")
    return source


def _empreintes_arbre(source: Path) -> dict[str, tuple[str, float]]:
    etat = {}
    for chemin in sorted(source.rglob("*")):
        if chemin.is_file():
            contenu = hashlib.sha256(chemin.read_bytes()).hexdigest()
            etat[str(chemin.relative_to(source))] = (contenu, chemin.stat().st_mtime)
    return etat


# ---------------------------------------------------------------------------
# A. Refus propres (codes d'erreur)
# ---------------------------------------------------------------------------


def test_refuse_chemin_source_vide_code_2(tmp_path: Path) -> None:
    code = pf.main(["preflight", "--source", "", "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s")])
    assert code == pf.CODE_SOURCE_ABSENTE == 2


def test_refuse_absence_de_source_sans_repli_sample_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun argument --source ⇒ refus, même si SEAMTECH_ARCHIVE_SOURCE existe.

    Le chemin doit être fourni EXPLICITEMENT : sample_data (ou tout autre
    dossier) ne remplace jamais l'archive silencieusement.
    """
    appels = []
    monkeypatch.setattr(pf, "_parcourir_source", lambda s: appels.append(s))
    monkeypatch.setenv("SEAMTECH_ARCHIVE_SOURCE", str(FIXTURES_LOCALES))
    code = pf.main(["preflight", "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s")])
    assert code == pf.CODE_SOURCE_ABSENTE == 2
    assert appels == [], "la source ne doit jamais être parcourue sans chemin explicite"


def test_refuse_source_inexistante_code_3(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    code = pf.main(
        ["preflight", "--source", str(tmp_path / "nulle_part"), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"]
    )
    assert code == pf.CODE_SOURCE_INVALIDE == 3


def test_refuse_source_sans_fichiers_code_3(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    vide = tmp_path / "archive_vide"
    vide.mkdir()
    code = pf.main(["preflight", "--source", str(vide), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == pf.CODE_SOURCE_INVALIDE == 3


def test_refuse_sortie_dans_la_source_code_4(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(source / "rapports"), "--min-libre-o", "0"]
    )
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


def test_refuse_sortie_dans_un_sous_sous_repertoire_de_la_source_code_4(tmp_path: Path, chemin_isole) -> None:
    """Sortie dans un sous-répertoire PROFOND de la source : refusée pareil."""
    chemin_isole()
    source = _arbre_source(tmp_path)
    profond = source / "CLIENT-A" / "sous-dossier" / "rapports"
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(profond), "--min-libre-o", "0"])
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


def test_refuse_travail_dans_la_source_code_4(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(source / "travail"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"]
    )
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


def test_refuse_source_dans_la_sortie_code_4(tmp_path: Path, chemin_isole) -> None:
    """L'inclusion dans l'autre sens est refusée aussi (garde-fou du dépôt)."""
    chemin_isole()
    sortie = tmp_path / "rapports"
    sortie.mkdir()
    source = sortie / "archive"
    source.mkdir()
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(sortie), "--min-libre-o", "0"])
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


def test_refuse_sortie_symlinkee_dans_la_source_code_4(tmp_path: Path, chemin_isole) -> None:
    """Un lien symbolique pointant dans la source ne trompe pas le contrôle."""
    chemin_isole()
    source = _arbre_source(tmp_path)
    lien = tmp_path / "sortie_leurre"
    try:
        lien.symlink_to(source / "CLIENT-A", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks indisponibles sur ce système")
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(lien), "--min-libre-o", "0"])
    assert code == pf.CODE_INCLUSION_CHEMINS == 4


def test_refuse_configuration_ambigue_deux_modes_echantillon(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "t"),
            "--sortie",
            str(tmp_path / "s"),
            "--min-libre-o",
            "0",
            "--echantillon-pdfs",
            "25",
            "--echantillon-dossiers",
            "6",
        ]
    )
    assert code == pf.CODE_CONFIG_AMBIGUE == 5


def test_refuse_configuration_ambigue_source_env_divergente(tmp_path: Path, chemin_isole, monkeypatch: pytest.MonkeyPatch) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    autre = tmp_path / "autre_archive"
    autre.mkdir()
    monkeypatch.setenv("SEAMTECH_ARCHIVE_SOURCE", str(autre))
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == pf.CODE_CONFIG_AMBIGUE == 5


def test_refuse_taille_echantillon_negative_code_5(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0", "--echantillon-pdfs", "-3"]
    )
    assert code == pf.CODE_CONFIG_AMBIGUE == 5


def test_refuse_espace_disque_insuffisant_code_7(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", str(10**18)]
    )
    assert code == pf.CODE_ESPACE == 7


def test_refuse_outils_absents_code_6(tmp_path: Path, chemin_isole) -> None:
    chemin_isole(avec_tesseract=False, avec_pdftoppm=False)
    source = _arbre_source(tmp_path)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == pf.CODE_PREREQUIS == 6


def test_refuse_langue_francaise_absente_code_6(tmp_path: Path, chemin_isole) -> None:
    chemin_isole(avec_fra=False)
    source = _arbre_source(tmp_path)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == pf.CODE_PREREQUIS == 6


def test_refuse_postgres_absent_si_staging_code_8(tmp_path: Path, chemin_isole) -> None:
    chemin_isole(avec_psql=False)
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0", "--staging"]
    )
    assert code == pf.CODE_POSTGRES == 8


def test_accepte_staging_avec_client_postgres(tmp_path: Path, chemin_isole) -> None:
    chemin_isole(avec_psql=True)
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0", "--staging"]
    )
    assert code == 0


def test_postgres_non_verifie_sans_staging(tmp_path: Path, chemin_isole) -> None:
    """Sans --staging, PostgreSQL n'est pas un prérequis (statut non_demande)."""
    chemin_isole(avec_psql=False)
    source = _arbre_source(tmp_path)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == 0


# ---------------------------------------------------------------------------
# B. Fonctionnement sur fixtures locales + rapports
# ---------------------------------------------------------------------------


def test_preflight_fonctionne_sur_fixtures_locales(tmp_path: Path, chemin_isole) -> None:
    """Fixtures synthétiques du dépôt (sample_data) — PAS une mesure d'archive réelle."""
    chemin_isole()
    code = pf.main(
        ["preflight", "--source", str(FIXTURES_LOCALES), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"]
    )
    assert code == 0
    rapport = json.loads((tmp_path / "s" / "preflight_rapport.json").read_text(encoding="utf-8"))
    assert (tmp_path / "s" / "preflight_rapport.txt").exists()
    assert (tmp_path / "s" / "echantillon.json").exists()
    ids = {c["id"] for c in rapport["controles"]}
    for attendu in (
        "source_fournie",
        "source_existante",
        "source_lisible",
        "source_contient_fichiers",
        "espace_disque",
        "chemins_disjoints",
        "source_non_ecrivable",
        "python_present",
        "tesseract_present",
        "langue_francaise",
        "pdftoppm_present",
        "postgres_present",
        "version_depot",
        "version_schema",
        "config_ocr",
        "echantillon_travail",
    ):
        assert attendu in ids, f"contrôle manquant : {attendu}"


def test_rapports_json_et_texte_produits_meme_en_refus(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    sortie = tmp_path / "s"
    code = pf.main(["preflight", "--source", str(tmp_path / "inex"), "--travail", str(tmp_path / "t"), "--sortie", str(sortie), "--min-libre-o", "0"])
    assert code == 3
    assert (sortie / "preflight_rapport.json").exists()
    assert (sortie / "preflight_rapport.txt").exists()
    rapport = json.loads((sortie / "preflight_rapport.json").read_text(encoding="utf-8"))
    assert rapport["code_sortie"] == 3
    assert rapport["resume"] == "refus"
    _ = source


def test_aucun_parcours_sans_chemin_explicite(tmp_path: Path, chemin_isole, monkeypatch: pytest.MonkeyPatch) -> None:
    """« ne parcourir réellement la source que lorsque le chemin est fourni »."""
    chemin_isole()
    appels = []
    monkeypatch.setattr(pf, "_parcourir_source", lambda s: appels.append(s) or {})
    code = pf.main(["preflight", "--source", "  ", "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s")])
    assert code == 2
    assert appels == []


def test_version_schema_et_config_ocr_dans_le_rapport(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == 0
    rapport = json.loads((tmp_path / "s" / "preflight_rapport.json").read_text(encoding="utf-8"))
    assert rapport["configuration"]["version_schema"]  # ex : 017_ocr_etage3
    config_ocr = rapport["configuration"]["config_ocr"]
    assert config_ocr["langue"] == "fra"
    assert config_ocr["seuil_texte_natif"] >= 1
    assert config_ocr["resolution_dpi"] >= 1


def test_avertissement_si_source_ecrivable(tmp_path: Path, chemin_isole) -> None:
    """L'absence de droit d'écriture est vérifiée « lorsque le système le permet »."""
    if os.geteuid() == 0:
        pytest.skip("sous root, os.access(W_OK) n'est pas discriminant")
    chemin_isole()
    source = _arbre_source(tmp_path)
    rapport = pf.executer_preflight(str(source), str(tmp_path / "t"), str(tmp_path / "s"), min_libre_o=0, env={"PATH": os.environ["PATH"]})
    par_id = {c["id"]: c for c in rapport["controles"]}
    assert par_id["source_non_ecrivable"]["statut"] in ("ok", "avertissement")
    source.chmod(0o555)
    try:
        rapport = pf.executer_preflight(str(source), str(tmp_path / "t"), str(tmp_path / "s"), min_libre_o=0, env={"PATH": os.environ["PATH"]})
        par_id = {c["id"]: c for c in rapport["controles"]}
        assert par_id["source_non_ecrivable"]["statut"] == "ok"
        assert "droit" in par_id["source_non_ecrivable"]["detail"]
    finally:
        source.chmod(0o755)


def test_chemins_espaces_et_accents_windows_compatibles(tmp_path: Path, chemin_isole) -> None:
    """Chemins du type « Pièces détachées Été 2024 » (bureau Windows) acceptés."""
    chemin_isole()
    source = tmp_path / "Pièces détachées Été 2024" / "Archive 77 92 SO"
    source.mkdir(parents=True)
    (source / "fiche été.pdf").write_bytes("%PDF-1.4 accentué".encode("utf-8"))
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "travail opérationnel"),
            "--sortie",
            str(tmp_path / "rapports validés"),
            "--min-libre-o",
            "0",
        ]
    )
    assert code == 0


# ---------------------------------------------------------------------------
# C. Échantillon de travail (jamais massif par défaut)
# ---------------------------------------------------------------------------


def test_echantillon_pdf_par_defaut_25_et_fourchette_procedure(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = tmp_path / "archive"
    source.mkdir()
    for i in range(40):
        (source / f"fiche-{i:02d}.pdf").write_bytes(b"%PDF-1.4 synthetique")
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"])
    assert code == 0
    echantillon = json.loads((tmp_path / "s" / "echantillon.json").read_text(encoding="utf-8"))
    assert echantillon["mode"] == "pdf"
    assert len(echantillon["elements"]) == 25  # défaut 25, fourchette procédure 20-30
    assert echantillon["resume"]["fourchette_procedure_pdf"] == [20, 30]


def test_echantillon_dossiers_representatifs(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = tmp_path / "archive"
    for i in range(12):
        dossier = source / f"CLIENT-{i:02d}"
        dossier.mkdir(parents=True)
        (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4")
        (dossier / "notes.txt").write_text("x", encoding="utf-8")
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "t"),
            "--sortie",
            str(tmp_path / "s"),
            "--min-libre-o",
            "0",
            "--echantillon-pdfs",
            "0",
            "--echantillon-dossiers",
            "7",
        ]
    )
    assert code == 0
    echantillon = json.loads((tmp_path / "s" / "echantillon.json").read_text(encoding="utf-8"))
    assert echantillon["mode"] == "dossiers"
    assert 5 <= len(echantillon["elements"]) <= 10  # fourchette procédure
    assert echantillon["resume"]["fourchette_procedure_dossiers"] == [5, 10]


def test_echantillon_limite_configurable_et_empreintes_limitees(tmp_path: Path, chemin_isole) -> None:
    """Limite configurable + plafond d'empreintes : aucun traitement massif."""
    chemin_isole()
    source = tmp_path / "archive"
    source.mkdir()
    for i in range(30):
        (source / f"fiche-{i:02d}.pdf").write_bytes(b"%PDF-1.4")
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "t"),
            "--sortie",
            str(tmp_path / "s"),
            "--min-libre-o",
            "0",
            "--echantillon-pdfs",
            "4",
            "--limite-empreintes",
            "2",
        ]
    )
    assert code == 0
    echantillon = json.loads((tmp_path / "s" / "echantillon.json").read_text(encoding="utf-8"))
    assert len(echantillon["elements"]) == 4
    statuts = [e["statut"] for e in echantillon["elements"]]
    assert statuts.count("empreinte_ok") == 2
    assert statuts.count("empreinte_limite_atteinte") == 2


def test_echantillon_sans_empreintes_sur_demande(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "t"),
            "--sortie",
            str(tmp_path / "s"),
            "--min-libre-o",
            "0",
            "--sans-empreintes",
        ]
    )
    assert code == 0
    echantillon = json.loads((tmp_path / "s" / "echantillon.json").read_text(encoding="utf-8"))
    assert all(e["sha256"] is None for e in echantillon["elements"])


# ---------------------------------------------------------------------------
# D. Contrôle des empreintes (SHA-256, taille, mtime)
# ---------------------------------------------------------------------------


def _produire_manifeste(tmp_path: Path, source: Path, *extra: str) -> Path:
    sortie = tmp_path / "rapports"
    code = pf.main(["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(sortie), "--min-libre-o", "0", *extra])
    assert code == 0
    return sortie / "echantillon.json"


def test_empreintes_identiques_code_0(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == 0


def test_empreintes_changement_sha256_code_9(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    (source / "CLIENT-A" / "fiche-a.pdf").write_bytes(b"%PDF-1.4 contenu remplace")
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == pf.CODE_EMPREINTES == 9
    rapport = json.loads((tmp_path / "rapports" / "empreintes_rapport.json").read_text(encoding="utf-8"))
    modifie = [r for r in rapport["resultats"] if r["chemin_relatif"].endswith("fiche-a.pdf")]
    assert modifie and "sha256" in modifie[0]["changements"]


def test_empreintes_changement_taille_code_9(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    cible = source / "CLIENT-A" / "fiche-a.pdf"
    cible.write_bytes(cible.read_bytes() + b"ajout")
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == 9
    rapport = json.loads((tmp_path / "rapports" / "empreintes_rapport.json").read_text(encoding="utf-8"))
    modifie = [r for r in rapport["resultats"] if r["chemin_relatif"].endswith("fiche-a.pdf")]
    assert modifie and "taille" in modifie[0]["changements"]


def test_empreintes_changement_mtime_seul_avertissement(tmp_path: Path, chemin_isole) -> None:
    """Un touch sans changement de contenu = avertissement, pas un refus."""
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    cible = source / "CLIENT-A" / "fiche-a.pdf"
    os.utime(cible, (0, 0))
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == 0
    rapport = json.loads((tmp_path / "rapports" / "empreintes_rapport.json").read_text(encoding="utf-8"))
    ligne = [r for r in rapport["resultats"] if r["chemin_relatif"].endswith("fiche-a.pdf")]
    assert ligne and ligne[0]["statut"] == "modifie_mtime"


def test_empreintes_fichier_manquant_code_9(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    (source / "CLIENT-A" / "fiche-a.pdf").unlink()
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == 9
    rapport = json.loads((tmp_path / "rapports" / "empreintes_rapport.json").read_text(encoding="utf-8"))
    assert any(r["statut"] == "manquant" for r in rapport["resultats"])


def test_empreintes_refuse_source_absente_code_2(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--sortie", str(tmp_path / "rapports")])
    assert code == 2


# ---------------------------------------------------------------------------
# E. RG13 — la source n'est JAMAIS modifiée
# ---------------------------------------------------------------------------


def test_rg13_source_intacte_apres_preflight_et_empreintes(tmp_path: Path, chemin_isole) -> None:
    """SHA-256 ET mtime de chaque fichier : identiques avant / après (RG13)."""
    chemin_isole()
    source = _arbre_source(tmp_path)
    avant = _empreintes_arbre(source)
    manifeste = _produire_manifeste(tmp_path, source, "--echantillon-pdfs", "3")
    code = pf.main(["empreintes", "--manifeste", str(manifeste), "--source", str(source), "--sortie", str(tmp_path / "rapports")])
    assert code == 0
    apres = _empreintes_arbre(source)
    assert avant == apres, "le préflight a modifié la source (interdit, RG13)"


def test_rg13_source_fixtures_locales_intacte(tmp_path: Path, chemin_isole) -> None:
    """Idem sur les fixtures versionnées du dépôt (sample_data)."""
    chemin_isole()
    avant = _empreintes_arbre(FIXTURES_LOCALES)
    code = pf.main(
        ["preflight", "--source", str(FIXTURES_LOCALES), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0"]
    )
    assert code == 0
    assert _empreintes_arbre(FIXTURES_LOCALES) == avant


# ---------------------------------------------------------------------------
# F. Sécurité des rapports (secrets, chemins partageables)
# ---------------------------------------------------------------------------


def test_rapports_sans_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, chemin_isole) -> None:
    """Aucune variable d'environnement secrète ne fuit dans les rapports."""
    chemin_isole()
    secret = "TopSecretTokenValue123"
    monkeypatch.setenv("SEAMTECH_AUTH_TOKEN", secret)
    monkeypatch.setenv("SEAMTECH_DATABASE_URL", f"postgresql://seamtech:{secret}@localhost:5432/seamtech")
    monkeypatch.setenv("SEAMTECH_UI_PASSWORD", secret)
    source = _arbre_source(tmp_path)
    code = pf.main(
        ["preflight", "--source", str(source), "--travail", str(tmp_path / "t"), "--sortie", str(tmp_path / "s"), "--min-libre-o", "0", "--staging", "--json"]
    )
    assert code == 0  # staging vert : database_url configurée compte comme présence
    for rapport in ("preflight_rapport.json", "preflight_rapport.txt", "echantillon.json"):
        contenu = (tmp_path / "s" / rapport).read_text(encoding="utf-8")
        assert secret not in contenu, f"secret retrouvé dans {rapport}"


def test_masquer_chemins_pour_partage(tmp_path: Path, chemin_isole) -> None:
    chemin_isole()
    source = _arbre_source(tmp_path)
    sortie = tmp_path / "s"
    code = pf.main(
        [
            "preflight",
            "--source",
            str(source),
            "--travail",
            str(tmp_path / "t"),
            "--sortie",
            str(sortie),
            "--min-libre-o",
            "0",
            "--masquer-chemins",
        ]
    )
    assert code == 0
    contenu = (sortie / "preflight_rapport.json").read_text(encoding="utf-8")
    assert "<SOURCE>" in contenu
    assert str(source) not in contenu


def test_masquer_chemins_domicile() -> None:
    domicile = str(Path.home() / "Documents" / "fiches clients")
    rendu = pf.masquer_chemins({"chemin": domicile})
    assert rendu["chemin"] == "<DOMICILE>/Documents/fiches clients"


def test_masquer_chemins_structure_complete() -> None:
    source = Path("/data/archive-test")
    structure = {"a": [str(source / "x"), "autre"], "b": {"c": f"{source}/y"}}
    rendu = pf.masquer_chemins(structure, source)
    assert rendu["a"][0] == "<SOURCE>/x"
    assert rendu["b"]["c"] == "<SOURCE>/y"
    assert rendu["a"][1] == "autre"
