"""Tests de la détection structurelle des fiches (PR 1 — angle mort de classement).

Le constat Phase 0 : des fiches réelles (variante « génois ») portent un
vocabulaire absent de ``TECHNICAL_ANCHORS`` et sont classées ``plan_pdf`` —
elles sortaient du recensement. Ces tests épinglent le correctif :

1. les trois fiches connues sont vues comme candidates par la détection
   structurelle, y compris celle que le classifieur rate aujourd'hui ;
2. les cas négatifs restent exclus (plan, scan sans texte, non-PDF) ;
3. le lexique est lu depuis la configuration et un ajout à chaud change le
   verdict (sans redéploiement) ;
4. l'inventaire expose les deux vues et la section « désaccords » ;
5. l'empreinte de gabarit utilise des positions relatives à la page.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "inventaire_archive.py"

FIXTURE_CLIENT123 = REPO_ROOT / "sample_data" / "CLIENT-123" / "fiche-technique.pdf"
FIXTURE_7792 = REPO_ROOT / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"
FIXTURE_GENOA = REPO_ROOT / "sample_data" / "CLIENT-GENOA" / "fiche-genois.pdf"


def _load_inventory() -> Any:
    spec = importlib.util.spec_from_file_location("inventaire_archive", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["inventaire_archive"] = module
    spec.loader.exec_module(module)
    return module


inventory = _load_inventory()


@pytest.fixture(scope="module")
def lexique_defaut() -> Any:
    from seamtech_search.lexique import charger_lexique

    return charger_lexique()


def detection_sur(chemin: Path, lexique: Any) -> Any:
    page = inventory.analyser_page_pdf(chemin)
    from seamtech_search.detection_fiches import detecter_fiche

    return detecter_fiche(page.mots, page.largeur, page.hauteur, page.grille_tracee, lexique)


# ---------------------------------------------------------------------------
# 1. Les trois fiches connues sont vues par la détection structurelle.
# ---------------------------------------------------------------------------


def test_les_trois_fiches_connues_sont_candidates(lexique_defaut: Any) -> None:
    for fixture in (FIXTURE_CLIENT123, FIXTURE_7792, FIXTURE_GENOA):
        assert fixture.is_file(), f"fixture manquante : {fixture}"
        resultat = detection_sur(fixture, lexique_defaut)
        assert resultat.est_candidat, f"{fixture.name} : {resultat.motif_exclusion}"


def test_le_point_mort_genois_est_reproduce_et_corrige(lexique_defaut: Any) -> None:
    """La fiche génois reste ratée par le classifieur, mais pas par la détection.

    C'est le test de non-régression sur l'angle mort lui-même : si quelqu'un
    « corrige » le classifieur sans le savoir, ce test documente toujours que
    la détection structurelle couvre ce cas indépendamment.
    """
    from seamtech_search.anchors import classify_pdf_text
    from seamtech_search.extractors import extract_file

    texte = extract_file(FIXTURE_GENOA).text
    assert classify_pdf_text(texte) == "plan_pdf", "le classifieur a changé : mettre à jour ce test"

    resultat = detection_sur(FIXTURE_GENOA, lexique_defaut)
    assert resultat.est_candidat
    assert {"guindant", "bordure", "tissu", "surface"} <= set(resultat.vocabulaire_trouve)


def test_7792_est_detectee_avec_sa_grille(lexique_defaut: Any) -> None:
    """La reconstruction de la fiche de référence porte une vraie grille réglée."""
    resultat = detection_sur(FIXTURE_7792, lexique_defaut)
    assert resultat.est_candidat
    assert resultat.grille_tracee, "le tableau réglé doit être vu par pdfplumber"
    assert "guindant" in resultat.vocabulaire_trouve
    composantes = resultat.composantes()
    assert composantes["nb_termes_vocabulaire"] >= 5
    assert composantes["score"] > 0


# ---------------------------------------------------------------------------
# 2. Cas négatifs toujours exclus.
# ---------------------------------------------------------------------------


def _construire_pdf(tmp_path: Path, lignes: list[str], nom: str = "doc.pdf") -> Path:
    from reportlab.pdfgen import canvas

    chemin = tmp_path / nom
    pdf = canvas.Canvas(str(chemin))
    y = 750
    for ligne in lignes:
        pdf.drawString(50, y, ligne)
        y -= 20
    pdf.save()
    return chemin


def test_plan_et_scan_exclus(tmp_path: Path, lexique_defaut: Any) -> None:
    plan = _construire_pdf(tmp_path, ["Plan de coupe", "Profil avant", "Echelle 1:20"])
    resultat = detection_sur(plan, lexique_defaut)
    assert not resultat.est_candidat
    assert resultat.motif_exclusion.startswith("vocabulaire insuffisant")

    from reportlab.pdfgen import canvas

    scan = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(scan))
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, 400, 400, stroke=0, fill=1)
    pdf.save()
    resultat = detection_sur(scan, lexique_defaut)
    assert not resultat.est_candidat
    assert resultat.motif_exclusion == "sans couche texte (scan probable)"


def test_detection_sur_page_vide(lexique_defaut: Any) -> None:
    from seamtech_search.detection_fiches import detecter_fiche

    resultat = detecter_fiche([], 0.0, 0.0, False, lexique_defaut)
    assert not resultat.est_candidat
    assert "sans couche texte" in resultat.motif_exclusion


def test_les_non_pdf_ne_sont_jamais_detectes(tmp_path: Path) -> None:
    """xlsx, txt et XIN ne passent jamais par la détection (hors périmètre PDF)."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "nomenclature.xlsx").write_bytes(b"PK\x03\x04")
    (archive / "notes.txt").write_text("guindant bordure tissu", encoding="utf-8")
    (archive / "programme.XIN").write_text("G01 X100", encoding="ascii")
    rapport_json = tmp_path / "rapport" / "inventaire.json"

    assert inventory.main(["inventaire_archive.py", str(archive), "--sortie", str(tmp_path / "rapport")]) == 0
    donnees = json.loads(rapport_json.read_text(encoding="utf-8"))
    assert donnees["detection_structurelle"]["nb_candidats"] == 0
    # Aucun fichier non-PDF n'apparaît dans les candidats :
    for candidat in donnees["detection_structurelle"]["candidats"]:
        assert candidat["chemin"].endswith(".pdf")


# ---------------------------------------------------------------------------
# 3. Lexique configurable : lecture depuis config/ + ajout à chaud.
# ---------------------------------------------------------------------------


def test_lexique_lu_depuis_la_configuration() -> None:
    from seamtech_search.lexique import CHEMIN_LEXIQUE_PAR_DEFAUT, charger_lexique

    lexique = charger_lexique(CHEMIN_LEXIQUE_PAR_DEFAUT)
    assert lexique.nb_termes >= 20
    # Normalisation : le fichier peut porter accents et majuscules.
    assert "guindant" in lexique.vocabulaire_normalise
    assert "matiere" in lexique.vocabulaire_normalise  # « matière » dans le JSON


def test_lexique_manquant_ou_invalide_echoue_bruyamment(tmp_path: Path) -> None:
    from seamtech_search.lexique import charger_lexique

    with pytest.raises(FileNotFoundError):
        charger_lexique(tmp_path / "absent.json")
    invalide = tmp_path / "invalide.json"
    invalide.write_text('{"version": 1, "vocabulaire_cotes": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        charger_lexique(invalide)


def test_ajout_a_chaud_au_lexique_change_le_verdict(tmp_path: Path, lexique_defaut: Any) -> None:
    """Un terme ajouté au fichier de configuration change le résultat, sans code."""
    # « halebas » (termes de marine) n'est pas dans le lexique engagé :
    assert "halebas" not in lexique_defaut.vocabulaire_normalise
    fiche = _construire_pdf(
        tmp_path,
        ["Halebas : 4,20 m", "Etarquage : bordure 40 mm", "Remarque : RAS"],
        nom="fiche-halebas.pdf",
    )
    avant = detection_sur(fiche, lexique_defaut)
    assert not avant.est_candidat, "hors lexique, la fiche ne doit pas être candidate"

    lexique_enrichi_data = json.loads((REPO_ROOT / "config" / "lexique_fiches.json").read_text(encoding="utf-8"))
    lexique_enrichi_data["vocabulaire_cotes"].extend(["halebas", "etarquage"])
    lexique_enrichi_data["version"] += 1
    chemin_enrichi = tmp_path / "lexique-enrichi.json"
    chemin_enrichi.write_text(json.dumps(lexique_enrichi_data, ensure_ascii=False), encoding="utf-8")

    from seamtech_search.lexique import charger_lexique

    apres = detection_sur(fiche, charger_lexique(chemin_enrichi))
    assert apres.est_candidat, "l'ajout à chaud doit faire entrer la fiche dans le recensement"
    assert {"halebas", "etarquage"} <= set(apres.vocabulaire_trouve)


# ---------------------------------------------------------------------------
# 4. Double vue de l'inventaire + désaccords.
# ---------------------------------------------------------------------------


def test_inventaire_rapporte_deux_vues_et_les_desaccords(tmp_path: Path) -> None:
    """Le rapport liste les documents vus par la détection et ratés par le classifieur."""
    archive = tmp_path / "archive"
    (archive / "2026").mkdir(parents=True)
    (archive / "2026" / "fiche-genois.pdf").write_bytes(FIXTURE_GENOA.read_bytes())
    plan = _construire_pdf(tmp_path, ["Plan de coupe", "Profil avant", "Echelle 1:20"])
    (archive / "2026" / "plan-coupe.pdf").write_bytes(plan.read_bytes())
    sortie = tmp_path / "rapport"

    def empreintes_arbre(racine: Path) -> dict[str, str]:
        import hashlib

        return {
            str(fichier.relative_to(racine)): hashlib.sha256(fichier.read_bytes()).hexdigest()
            for fichier in sorted(racine.rglob("*"))
            if fichier.is_file()
        }

    avant = empreintes_arbre(archive)
    code = inventory.main(["inventaire_archive.py", str(archive), "--sortie", str(sortie)])
    apres = empreintes_arbre(archive)

    assert code == 0
    assert avant == apres, "l'inventaire a modifié l'archive"

    donnees = json.loads((sortie / "inventaire.json").read_text(encoding="utf-8"))
    detection = donnees["detection_structurelle"]
    assert detection["lexique"]["nb_termes"] >= 20
    assert detection["lexique"]["fichier"] == "config/lexique_fiches.json", "chemin relatif au dépôt, stable entre machines"
    assert len(detection["lexique"]["empreinte_sha256"]) == 64
    assert detection["nb_candidats"] == 1
    assert detection["candidats"][0]["chemin"].endswith("fiche-genois.pdf")
    desaccords = detection["desaccords"]
    assert desaccords["nb_rates_par_le_classifieur"] == 1
    rate = desaccords["rates_par_le_classifieur"][0]
    assert rate["chemin"].endswith("fiche-genois.pdf")
    assert rate["categorie_classifieur"] == "plan"
    assert "guindant" in rate["vocabulaire_trouve"]
    # Le plan n'est ni candidat ni désaccord :
    assert all(not c["chemin"].endswith("plan-coupe.pdf") for c in detection["candidats"])


def test_inventaire_csv_porte_les_deux_vues(tmp_path: Path) -> None:
    import csv as csv_module

    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "fiche-genois.pdf").write_bytes(FIXTURE_GENOA.read_bytes())
    sortie = tmp_path / "rapport"
    assert inventory.main(["inventaire_archive.py", str(archive), "--sortie", str(sortie), "--silencieux"]) == 0

    with (sortie / "inventaire_fichiers.csv").open(encoding="utf-8-sig", newline="") as fichier:
        lignes = list(csv_module.DictReader(fichier, delimiter=";"))
    assert len(lignes) == 1
    assert lignes[0]["candidat_fiche"] == "1"
    assert float(lignes[0]["score_fiche"]) > 0
    assert lignes[0]["categorie_pdf"] == "plan"


def test_technique_du_classifieur_sans_structure_est_signalee(tmp_path: Path, lexique_defaut: Any) -> None:
    """Une fiche du classifieur sans structure détectable sort dans l'autre sens du désaccord."""
    from seamtech_search.detection_fiches import detecter_fiche

    # Mots du lexique mais tous sur une même ligne (aucune structure de tableau) :
    mots = [
        {"text": terme, "x0": 50.0 + i * 90, "top": 700.0}
        for i, terme in enumerate(["Guindant", "chute", "bordure", "surface"])
    ]
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert not resultat.est_candidat
    assert "structure de tableau" in resultat.motif_exclusion


# ---------------------------------------------------------------------------
# 5. Empreinte de gabarit : positions relatives à la page.
# ---------------------------------------------------------------------------


def mot(texte: str, x: float, y: float) -> dict[str, Any]:
    return {"text": texte, "x0": x, "top": y}


def test_empreinte_relative_meme_gabarit_sur_formats_differents() -> None:
    libelles = [("Guindant", 50, 700), ("Chute", 50, 680), ("Surface", 200, 700), ("Tissu", 200, 680)]
    mots_grand = [mot(t, x, y) for t, x, y in libelles]
    # Même mise en page sur une page moitié plus petite :
    mots_petit = [mot(t, x / 2, y / 2) for t, x, y in libelles]

    grand = inventory.empreinte_gabarit(mots_grand, 612.0, 792.0)
    petit = inventory.empreinte_gabarit(mots_petit, 306.0, 396.0)

    assert grand is not None and petit is not None
    assert grand["cle_famille"] == petit["cle_famille"]
    assert grand["empreinte_fine"] == petit["empreinte_fine"]


def test_empreinte_relative_separe_les_mises_en_page() -> None:
    mots_a = [mot(t, x, y) for t, x, y in [("Guindant", 50, 700), ("Chute", 50, 680), ("Surface", 200, 700)]]
    mots_b = [mot(t, x, y) for t, x, y in [("Guindant", 50, 500), ("Chute", 50, 480), ("Surface", 200, 500)]]

    a = inventory.empreinte_gabarit(mots_a, 612.0, 792.0)
    b = inventory.empreinte_gabarit(mots_b, 612.0, 792.0)

    assert a is not None and b is not None
    assert a["cle_famille"] == b["cle_famille"], "même vocabulaire → même famille"
    assert a["empreinte_fine"] != b["empreinte_fine"], "positions différentes → mises en page distinctes"


def test_empreinte_sans_dimensions_comportement_historique() -> None:
    mots = [mot("Guindant", 50, 700), mot("Chute", 50, 680)]
    empreinte = inventory.empreinte_gabarit(mots)
    assert empreinte is not None
    # Sans dimensions de page : quantification absolue (pas de 6 pt).
    assert inventory.empreinte_gabarit(mots) == empreinte


# ---------------------------------------------------------------------------
# Constat 2 de revue — fiches mono-colonne (deux voies d'admission).
# ---------------------------------------------------------------------------


def _mots_colonne_unique(termes: list[str]) -> list[dict[str, Any]]:
    """« Libellé : valeur » une par ligne : tous les libellés au même x0."""
    mots: list[dict[str, Any]] = []
    for index, terme in enumerate(termes):
        mots.append({"text": terme, "x0": 50.0, "top": 740.0 - index * 18.0})
        mots.append({"text": "valeur", "x0": 200.0, "top": 740.0 - index * 18.0})
    return mots


def test_fiche_mono_colonne_riche_candidate_par_voie_vocabulaire_fort(lexique_defaut: Any) -> None:
    """15 termes du lexique, une seule colonne détectée → candidate quand même."""
    from seamtech_search.detection_fiches import detecter_fiche

    termes = ["guindant", "chute", "bordure", "surface", "tissu", "quantite", "client", "galon"]
    mots = _mots_colonne_unique(termes)
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert resultat.nb_colonnes < lexique_defaut.seuils.nb_colonnes_min
    assert resultat.nb_lignes >= lexique_defaut.seuils.nb_lignes_min
    assert resultat.est_candidat, "la voie vocabulaire fort doit admettre les fiches mono-colonne"
    assert resultat.motif_exclusion == ""


def test_fiche_mono_colonne_pauvre_exclue_avec_les_deux_voies(lexique_defaut: Any) -> None:
    """2 termes, une seule colonne : ni la voie forte ni la voie structure ne passe."""
    from seamtech_search.detection_fiches import detecter_fiche

    mots = _mots_colonne_unique(["guindant", "chute"])
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert not resultat.est_candidat
    assert "structure de tableau non détectée" in resultat.motif_exclusion, "voie faible : échec sur la structure"
    assert "voie vocabulaire fort manquée (2/5)" in resultat.motif_exclusion, "voie forte : échec sur le vocabulaire"


def test_fiche_structure_requise_sous_le_seuil_fort(lexique_defaut: Any) -> None:
    """3-4 termes (≥ min, < fort) sans structure : exclu, les DEUX voies citées."""
    from seamtech_search.detection_fiches import detecter_fiche

    mots = _mots_colonne_unique(["guindant", "chute", "bordure", "surface"])
    mots[-1]["x0"] = 260.0  # casse l'alignement colonne sans créer de tableau
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert not resultat.est_candidat
    assert "voie vocabulaire fort manquée" in resultat.motif_exclusion
    assert "structure de tableau non détectée" in resultat.motif_exclusion


def test_seuil_fort_lu_depuis_le_lexique_seulement(tmp_path: Path, lexique_defaut: Any) -> None:
    """Le seuil fort vient du fichier de configuration, pas du code."""
    donnees = json.loads((REPO_ROOT / "config" / "lexique_fiches.json").read_text(encoding="utf-8"))
    donnees["seuils"]["vocabulaire_fort"] = 7
    chemin = tmp_path / "lexique-fort7.json"
    chemin.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")

    from seamtech_search.detection_fiches import detecter_fiche
    from seamtech_search.lexique import charger_lexique

    lexique_7 = charger_lexique(chemin)
    mots = _mots_colonne_unique(["guindant", "chute", "bordure", "surface", "tissu", "quantite"])
    # 6 termes : candidat au seuil par défaut (5), exclu au seuil 7.
    assert detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut).est_candidat
    assert not detecter_fiche(mots, 612.0, 792.0, False, lexique_7).est_candidat


def test_seuil_fort_inferieur_au_minimum_refuse(tmp_path: Path) -> None:
    donnees = json.loads((REPO_ROOT / "config" / "lexique_fiches.json").read_text(encoding="utf-8"))
    donnees["seuils"]["vocabulaire_fort"] = 1
    donnees["seuils"]["vocabulaire_min"] = 2
    chemin = tmp_path / "lexique-incoherent.json"
    chemin.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")

    from seamtech_search.lexique import charger_lexique

    with pytest.raises(ValueError, match="vocabulaire_fort"):
        charger_lexique(chemin)


# ---------------------------------------------------------------------------
# Constat 3 de revue — singulier/pluriel et variantes d'écriture.
# ---------------------------------------------------------------------------


def test_appariement_tolere_le_pluriel_mono_mot(lexique_defaut: Any) -> None:
    from seamtech_search.detection_fiches import detecter_fiche

    # Termes du lexique au singulier (« jonction »), texte au pluriel.
    mots = [
        {"text": "Jonctions", "x0": 50.0, "top": 700.0},
        {"text": "horizontales", "x0": 140.0, "top": 700.0},
        {"text": "Epaisseur", "x0": 50.0, "top": 680.0},
        {"text": "01", "x0": 140.0, "top": 680.0},
        {"text": "Finition", "x0": 50.0, "top": 660.0},
        {"text": "oeillet", "x0": 140.0, "top": 660.0},
    ]
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert "jonction" in resultat.vocabulaire_trouve
    assert "epaisseurs" in resultat.vocabulaire_trouve, "lexique pluriel ↔ texte singulier"
    assert "finitions" in resultat.vocabulaire_trouve, "lexique pluriel ↔ texte singulier"


def test_le_pluriel_ne_colle_pas_aux_mots_voisins(lexique_defaut: Any) -> None:
    """La tolérance s? ne doit pas faire matcher un préfixe d'un autre mot."""
    from seamtech_search.detection_fiches import detecter_fiche

    # « mesures » seul ne doit PAS déclencher « mesures finies » (multi-mot),
    # et un mot comme « surfaçage » ne doit pas matcher « surface ».
    mots = [
        {"text": "Surfacage", "x0": 50.0, "top": 700.0},
        {"text": "mesures", "x0": 50.0, "top": 680.0},
    ]
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert "surface" not in resultat.vocabulaire_trouve
    assert "mesures finies" not in resultat.vocabulaire_trouve


def test_grand_voile_avec_et_sans_trait_d_union(lexique_defaut: Any) -> None:
    from seamtech_search.detection_fiches import detecter_fiche

    mots = [
        {"text": "Grand voile", "x0": 50.0, "top": 700.0},
        {"text": "guindant", "x0": 50.0, "top": 680.0},
    ]
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert "grand voile" in resultat.vocabulaire_trouve, "la variante sans trait d'union (constat 3) doit matcher"

    mots = [
        {"text": "Grand-voile", "x0": 50.0, "top": 700.0},
        {"text": "guindant", "x0": 50.0, "top": 680.0},
    ]
    resultat = detecter_fiche(mots, 612.0, 792.0, False, lexique_defaut)
    assert "grand-voile" in resultat.vocabulaire_trouve, "l'entrée d'origine (avec trait d'union) doit continuer à matcher"


def test_mesure_appariement_7792_avant_apres(lexique_defaut: Any) -> None:
    """Mesure rejouée sur la fiche de référence (constat 3).

    Mesure sur le DOCUMENT CLIENT RÉEL 7792-SO (reçu le 21/09, SHA-256
    43afc51e…) : 27 termes du lexique y sont trouvés — chiffre mesuré le
    21/09/2026, qui remplace l'ancien 31 obtenu sur la reconstruction (la
    mise en page réelle n'imprime pas tous les libellés de la reconstruction,
    p. ex. « désignation »). La tolérance au pluriel reste vérifiée :
    « Jonction verticale » (libellé réel) est couvert par le terme singulier
    du lexique.
    """
    page = inventory.analyser_page_pdf(FIXTURE_7792)
    from seamtech_search.detection_fiches import detecter_fiche

    resultat = detecter_fiche(page.mots, page.largeur, page.hauteur, page.grille_tracee, lexique_defaut)
    assert len(resultat.vocabulaire_trouve) == 27, "mesuré sur le document réel 7792-SO le 21/09/2026"
    assert "jonction" in resultat.vocabulaire_trouve
