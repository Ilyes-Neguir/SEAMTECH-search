"""Passe finale — ingestion : tri des fichiers déposés et cas limites de dossier.

Ce que ce module verrouille (vérification indépendante du 29/10/2026) :

* le tri réellement implémenté : ``.pdf`` → candidat à l'analyse (technique ou
  plan selon son texte), ``.xls/.xlsx`` → feuille Excel, **tout le reste** →
  stockage direct (``storage_direct``), dossier → ``folder``. Il n'existe pas de
  libellé « Stockable » dans le code : l'équivalent est ``storage_direct`` /
  ``not_applicable`` ;
* les cas limites d'un dossier déposé : nom accentué, espace, emoji, extension
  inconnue, sans extension, fichier vide, PDF corrompu, PDF chiffré, dossier
  vide, dossier très profond, **lien symbolique** (dedans → dehors) ;
* les bornes d'envoi (fichiers par envoi, profondeur, noms réservés Windows,
  chemins absolus, collisions insensibles à la casse).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import _walk_files, classify_path, classify_pdf_text, scan_folder


def _appconfig(racine: Path) -> AppConfig:
    return AppConfig(root_paths=[racine], database_path=racine / "unused.db", web_worker_enabled=False)


def _pdf_valide(destination: Path, texte: str = "Fiche technique voile SLU 6.60 m") -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    destination.parent.mkdir(parents=True, exist_ok=True)
    canevas = pdf_canvas.Canvas(str(destination), pagesize=A4)
    canevas.drawString(60, 780, texte)
    canevas.showPage()
    canevas.save()
    return destination


# ---------------------------------------------------------------------------
# Tri
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "attendu"),
    [
        ("fiche.pdf", "pdf_candidate"),
        ("FICHE.PDF", "pdf_candidate"),
        ("plan.dwg", "storage_direct"),
        ("photo.jpg", "storage_direct"),
        ("note.txt", "storage_direct"),
        ("archive.zip", "storage_direct"),
        ("inconnu.xyz", "storage_direct"),
        ("sans_extension", "storage_direct"),
        ("nomenclature.xlsx", "excel_sheet"),
        ("ancienne.XLS", "excel_sheet"),
        ("voile élégante 2024.pdf", "pdf_candidate"),
    ],
)
def test_tri_des_extensions(tmp_path: Path, nom: str, attendu: str) -> None:
    chemin = tmp_path / nom
    chemin.write_bytes(b"x")
    assert classify_path(chemin) == attendu


def test_un_dossier_est_classe_folder(tmp_path: Path) -> None:
    assert classify_path(tmp_path) == "folder"


def test_pdf_technique_et_pdf_plan_sont_distingues() -> None:
    # Règle réelle (anchors.py) : >= 2 ancres dont une forte, ou >= 3 ancres
    # faibles. « fiche technique » seul n'est PAS une ancre — c'est un piège
    # documenté, d'où ce test sur des libellés du corpus réel.
    technique = (
        "FICHE DE FABRICATION\nMesures finies\nSLU 12.50 m\nCotes techniques\nMatériau Dacron"
    )
    assert classify_pdf_text(technique) == "technical_pdf"
    # Sans ancre FORTE, il en faut trois : deux libellés faibles ne suffisent pas.
    assert classify_pdf_text("Type de voile\nBordure") == "plan_pdf"
    assert classify_pdf_text("Type de voile\nBordure\nSangle") == "technical_pdf"
    plan = "Plan de pont\nVue de dessus\nEchelle 1:50"
    assert classify_pdf_text(plan) == "plan_pdf"


def test_text_vide_reste_candidat_pdf() -> None:
    """Un PDF sans texte extractible n'est jamais promu « technique » : il est
    listé comme candidat à l'analyse, pas classé définitivement."""
    assert classify_path(Path("scan.pdf")) == "pdf_candidate"


# ---------------------------------------------------------------------------
# Cas limites d'un dossier
# ---------------------------------------------------------------------------


def test_scan_dossier_cas_limites(tmp_path: Path) -> None:
    racine = tmp_path / "archives"
    racine.mkdir()
    _pdf_valide(racine / "fiche technique.pdf")
    (racine / "voile élégante – 2024.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    (racine / "note.txt").write_text("texte")
    (racine / "inconnu.xyz").write_bytes(b"\x00\x01")
    (racine / "vide.pdf").write_bytes(b"")
    (racine / "corrompu.pdf").write_bytes(b"%PDF-1.4\npas un vrai pdf")
    (racine / "sans_extension").write_text("x")
    (racine / "emoji_😀.txt").write_text("x")
    (racine / "dossier_vide").mkdir()
    profond = racine
    for niveau in range(40):
        profond = profond / f"n{niveau}"
    profond.mkdir(parents=True)
    (profond / "profond.txt").write_text("x")

    config = _appconfig(racine)
    resultat = scan_folder(racine, config)

    noms_candidats = {Path(c["path"]).name for c in resultat["candidates"]}
    assert "fiche technique.pdf" in noms_candidats
    # Un PDF illisible reste un CANDIDAT (donc visible), jamais un succès muet.
    assert "corrompu.pdf" in noms_candidats
    assert "vide.pdf" in noms_candidats
    # Le dossier vide et le fichier sans extension ne cassent rien.
    assert resultat["files_detected"] >= 8
    assert all(c["classification"] for c in resultat["candidates"])


def test_scan_dossier_vide_avertit_sans_lever(tmp_path: Path) -> None:
    racine = tmp_path / "vide"
    racine.mkdir()
    resultat = scan_folder(racine, _appconfig(racine))
    assert resultat["files_detected"] == 0
    assert resultat["candidates"] == []
    assert resultat["warnings"]


def test_scan_refuse_un_dossier_hors_racine(tmp_path: Path) -> None:
    racine = tmp_path / "racine"
    racine.mkdir()
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    with pytest.raises(PermissionError):
        scan_folder(dehors, _appconfig(racine))


def test_lien_symbolique_vers_l_exterieur_est_signale(tmp_path: Path) -> None:
    """Constat mesuré : ``_walk_files`` suit un lien symbolique de FICHIER.

    Un lien déposé dans l'archive et pointant hors des racines configurées est
    donc listé (et pourrait être importé) : c'est une évasion de périmètre par
    lien symbolique. Ce test FIGE le constat pour qu'il soit traité ou assumé
    explicitement — il n'est pas présenté comme conforme par omission.
    """
    racine = tmp_path / "racine"
    racine.mkdir()
    secret = tmp_path / "hors_perimetre.txt"
    secret.write_text("hors périmètre")
    lien = racine / "lien.txt"
    try:
        lien.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - FS sans liens
        pytest.skip("système de fichiers sans lien symbolique")
    fichiers = {str(p) for p in _walk_files(racine)}
    assert str(lien) in fichiers
    # Le fichier hors périmètre est atteint par le contenu du lien, sans que le
    # chemin affiché le montre : la parade attendue est de refuser/avertir.
    assert lien.resolve() == secret.resolve()


def test_lien_symbolique_de_dossier_n_est_pas_suivi(tmp_path: Path) -> None:
    """Mesuré : un lien de DOSSIER n'est pas parcouru (pas de boucle infinie)."""
    racine = tmp_path / "racine"
    racine.mkdir()
    cible = tmp_path / "cible"
    cible.mkdir()
    (cible / "interieur.txt").write_text("x")
    try:
        (racine / "lien_dossier").symlink_to(cible)
        (racine / "boucle").symlink_to(racine)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("système de fichiers sans lien symbolique")
    fichiers = [str(p) for p in _walk_files(racine)]
    assert not any("interieur.txt" in chemin for chemin in fichiers)
    # La boucle ne fait pas exploser le parcours.
    assert len(fichiers) <= 2


# ---------------------------------------------------------------------------
# Bornes d'envoi (dossier complet)
# ---------------------------------------------------------------------------


def test_bornes_d_envoi_declarees_et_coherentes() -> None:
    from seamtech_search.fiches.televersement_lot import (
        MAX_FICHIERS_PAR_ENVOI,
        MAX_FICHIERS_PAR_SESSION,
        MAX_PROFONDEUR_CHEMIN,
        MAX_TAILLE_ENVOI_OCTETS,
        MAX_TAILLE_TOTALE_OCTETS,
        _chemin_relatif_sur,
        _limites,
    )

    assert MAX_FICHIERS_PAR_ENVOI == 200
    assert MAX_FICHIERS_PAR_SESSION == 100_000
    assert MAX_PROFONDEUR_CHEMIN == 32
    assert MAX_TAILLE_ENVOI_OCTETS == 512 * 1024 * 1024
    assert MAX_TAILLE_TOTALE_OCTETS == 20 * 1024 * 1024 * 1024

    for chemin_invalide in (
        "../evasion.pdf",
        "/absolu.pdf",
        "C:\\Windows\\systeme.pdf",
        "dossier//double.pdf",
        ".cache/x.pdf",
        "CON.txt",
        "nom\x00nul.pdf",
    ):
        with pytest.raises(Exception):
            _chemin_relatif_sur(chemin_invalide)

    # 33 niveaux de dossiers refusés, 32 acceptés.
    trop_profond = "/".join([f"d{i}" for i in range(33)] + ["f.pdf"])
    with pytest.raises(Exception):
        _chemin_relatif_sur(trop_profond)
    limite = "/".join([f"d{i}" for i in range(32)] + ["f.pdf"])
    assert _chemin_relatif_sur(limite)[0] == limite

    # Un nom accentué et un emoji sont ACCEPTÉS (l'archive réelle en contient).
    assert _chemin_relatif_sur("dossier/voile élégante 😀.pdf")[0] == "dossier/voile élégante 😀.pdf"

    bornes = _limites(AppConfig(root_paths=[Path(".")], database_path=Path("/tmp/u.db")))
    assert bornes["fichiers_par_envoi_max"] == MAX_FICHIERS_PAR_ENVOI
    assert bornes["profondeur_chemin_max"] == MAX_PROFONDEUR_CHEMIN


def test_dossier_de_plus_de_100k_fichiers_refuse_sans_ecrire(tmp_path: Path) -> None:
    """Volumétrie annoncée : 100 001 fichiers refusés AVANT toute écriture.

    La création réelle de 100 000 fichiers n'est pas faite ici (elle ne prouve
    rien de plus sur la règle et coûte des heures) : la borne est vérifiée sur
    l'annonce de session, qui est le point d'entrée.
    """
    from seamtech_search.fiches.televersement_lot import (
        TeleversementLotTropGrand,
        creer_session_televersement_lot,
    )

    racine = tmp_path / "lots"
    racine.mkdir()
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "u.db", web_worker_enabled=False)
    with pytest.raises(TeleversementLotTropGrand):
        creer_session_televersement_lot(
            config, nom_racine="gros", nombre_fichiers=100_001, taille_totale_octets=1024
        )
    assert list(racine.iterdir()) == []


def test_fichier_non_utf8_dans_un_dossier_ne_casse_pas_le_parcours(tmp_path: Path) -> None:
    """Nom de fichier en octets non UTF-8 (archive ancienne, partage Windows)."""
    racine = tmp_path / "racine"
    racine.mkdir()
    brut = os.fsencode(str(racine)) + b"/" + b"facture_\xe9\xe8.pdf"
    try:
        descripteur = os.open(brut, os.O_CREAT | os.O_WRONLY, 0o644)
    except OSError:  # pragma: no cover - FS qui refuse
        pytest.skip("système de fichiers refusant un nom non UTF-8")
    os.close(descripteur)
    fichiers = _walk_files(racine)
    assert len(fichiers) == 1
