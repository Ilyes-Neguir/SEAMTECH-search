"""Tests du mode « mesure » de scripts/validate_extraction.py (Phase 0).

Ce banc mesure, champ par champ, le taux de lecture correcte de l'extraction
sur un échantillon de fiches décrites par une vérité terrain JSON. Les tests
épinglent : les verdicts (OK / ECART / MANQUANT / SUSPECT / INATTENDU /
OK_ABSENCE), la comparaison des cotes en millimètres avec tolérance, les
agrégats par champ et par gabarit, l'export JSON, le refus d'écrire le
rapport dans le dossier des fiches, et le comportement en garde (--seuil).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "validate_extraction.py"


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location("validate_extraction", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_extraction"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()

FICHE_COMPLETE = [
    "FICHE DE FABRICATION",
    "Référence : REF-MESURE-1",
    "Matière : Dacron Pro 340",
    "Quantité : 2",
    "Description : Grand voile lattée",
    "Longueur : 12,5 m",
    "Largeur : 4,2 m",
]
FICHE_SANS_MATIERE = [
    "FICHE DE FABRICATION",
    "Référence : REF-MESURE-2",
    "Quantité : 1",
    "Description : Génois",
    "Longueur : 9,8 m",
]


def make_pdf(path: Path, lines: list[str]) -> Path:
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path))
    y = 750
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 20
    pdf.save()
    return path


VERITE_COMPLETE = {
    "gabarit": "grand_voile_ref",
    "attendu": {
        "reference": "REF-MESURE-1",
        "material": "Dacron Pro 340",
        "quantity": 2,
        "description": "Grand voile lattée",
        "dimensions": {"length": 12.5, "width": 4.2, "unit": "m"},
    },
}


@pytest.fixture()
def echantillon(tmp_path: Path) -> Path:
    dossier = tmp_path / "fiches"
    make_pdf(dossier / "fiche-complete.pdf", FICHE_COMPLETE)
    make_pdf(dossier / "fiche-sans-matiere.pdf", FICHE_SANS_MATIERE)
    return dossier


def ecrire_verite(tmp_path: Path, contenu: dict[str, Any]) -> str:
    chemin = tmp_path / "verite.json"
    chemin.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
    return str(chemin)


def lancer_mesure(echantillon_path: Path, tmp_path: Path, verite: dict[str, Any], *options: str) -> int:
    return harness.main(
        [
            "validate_extraction.py",
            str(echantillon_path),
            "--verite",
            ecrire_verite(tmp_path, verite),
            "--sortie-json",
            str(tmp_path / "mesure.json"),
            *options,
        ]
    )


def charger_mesure(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "mesure.json").read_text(encoding="utf-8"))


def verdicts_par_champ(mesure: dict[str, Any], nom_fichier: str) -> dict[str, str]:
    fiche = next(f for f in mesure["fiches"] if f["fichier"].endswith(nom_fichier))
    return {v["champ"]: v["verdict"] for v in fiche["champs"]}


# ---------------------------------------------------------------------------
# Mesure de bout en bout.
# ---------------------------------------------------------------------------


def test_mesure_pleine_reussite(echantillon: Path, tmp_path: Path) -> None:
    code = lancer_mesure(echantillon, tmp_path, {"fiche-complete.pdf": VERITE_COMPLETE})

    assert code == 0
    mesure = charger_mesure(tmp_path)
    assert mesure["meta"]["nb_fiches"] == 1
    assert mesure["global"]["taux_ok"] == 1.0
    assert mesure["global"]["attendus"] == 6
    verdicts = verdicts_par_champ(mesure, "fiche-complete.pdf")
    assert verdicts == {
        "reference": "OK",
        "material": "OK",
        "quantity": "OK",
        "description": "OK",
        "dimensions.length": "OK",
        "dimensions.width": "OK",
    }


def test_mesure_ecart_et_manquant(echantillon: Path, tmp_path: Path) -> None:
    verite = {
        "fiche-complete.pdf": {
            "gabarit": "grand_voile_ref",
            "attendu": {**VERITE_COMPLETE["attendu"], "reference": "REF-FAUX"},
        },
        "fiche-sans-matiere.pdf": {
            "gabarit": "genois_ref",
            "attendu": {"reference": "REF-MESURE-2", "material": "Ripstop 60", "quantity": 1},
        },
    }
    code = lancer_mesure(echantillon, tmp_path, verite)

    assert code == 0
    mesure = charger_mesure(tmp_path)
    verdicts_complete = verdicts_par_champ(mesure, "fiche-complete.pdf")
    verdicts_sans = verdicts_par_champ(mesure, "fiche-sans-matiere.pdf")

    assert verdicts_complete["reference"] == "ECART"
    assert verdicts_sans["material"] == "MANQUANT"
    assert verdicts_sans["reference"] == "OK"

    # Taux : reference 1/2, material 1/2, quantity 2/2, description 1/1, cotes 2/2.
    assert mesure["par_champ"]["reference"]["taux_ok"] == 0.5
    assert mesure["par_champ"]["material"]["taux_ok"] == 0.5
    assert mesure["par_champ"]["material"]["manquant"] == 1
    assert mesure["par_champ"]["quantity"]["taux_ok"] == 1.0
    assert mesure["global"]["ok"] == 7
    assert mesure["global"]["attendus"] == 9
    assert mesure["global"]["taux_ok"] == round(7 / 9, 3)  # le rapport arrondit à 3 décimales

    # Regroupement par gabarit.
    assert set(mesure["par_gabarit"]) == {"grand_voile_ref", "genois_ref"}
    assert mesure["par_gabarit"]["genois_ref"]["nb_fiches"] == 1


def test_mesure_temps_par_fiche_present(echantillon: Path, tmp_path: Path) -> None:
    lancer_mesure(echantillon, tmp_path, {"fiche-complete.pdf": VERITE_COMPLETE})
    mesure = charger_mesure(tmp_path)

    assert mesure["meta"]["temps_moyen_ms"] >= 0
    assert isinstance(mesure["fiches"][0]["temps_ms"], int)


def test_seuil_en_garde_hard(echantillon: Path, tmp_path: Path) -> None:
    verite = {
        "fiche-complete.pdf": {
            "gabarit": "grand_voile_ref",
            "attendu": {**VERITE_COMPLETE["attendu"], "reference": "REF-FAUX"},
        }
    }
    code = lancer_mesure(echantillon, tmp_path, verite, "--seuil", "0.95")

    assert code == 1


def test_seuil_atteint_code_zero(echantillon: Path, tmp_path: Path) -> None:
    code = lancer_mesure(echantillon, tmp_path, {"fiche-complete.pdf": VERITE_COMPLETE}, "--seuil", "1.0")

    assert code == 0


def test_cle_de_verite_sans_pdf(echantillon: Path, tmp_path: Path, capsys) -> None:
    verite = {"fiche-complete.pdf": VERITE_COMPLETE, "fiche-absente.pdf": {"attendu": {"reference": "REF-ABSENTE-1"}}}
    code = lancer_mesure(echantillon, tmp_path, verite)

    assert code == 0
    mesure = charger_mesure(tmp_path)
    assert mesure["meta"]["nb_fiches"] == 1
    assert mesure["meta"]["cles_verite_non_trouvees"] == ["fiche-absente.pdf"]
    assert "fiche-absente.pdf" in capsys.readouterr().err


def test_aucune_fiche_appariee_code_2(echantillon: Path, tmp_path: Path) -> None:
    verite = {"autre-fiche.pdf": {"attendu": {"reference": "X"}}}
    code = lancer_mesure(echantillon, tmp_path, verite)

    assert code == 2


def test_rapport_refuse_dans_le_dossier_des_fiches(echantillon: Path, tmp_path: Path) -> None:
    code = harness.main(
        [
            "validate_extraction.py",
            str(echantillon),
            "--verite",
            ecrire_verite(tmp_path, {"fiche-complete.pdf": VERITE_COMPLETE}),
            "--sortie-json",
            str(echantillon / "mesure.json"),
        ]
    )

    assert code == 2
    assert not (echantillon / "mesure.json").exists()


def test_verite_invalide_leve_clairement(tmp_path: Path) -> None:
    chemin = tmp_path / "verite.json"
    chemin.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        harness.charger_verite(chemin)
    chemin.write_text('{"f.pdf": {"gabarit": "x"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        harness.charger_verite(chemin)


# ---------------------------------------------------------------------------
# Unités pures : comparaisons et agrégats, sans PDF.
# ---------------------------------------------------------------------------


def _dimensions(**valeurs: Any) -> Any:
    from seamtech_search.import_pipeline import Dimensions

    return Dimensions.model_validate(valeurs)


def test_cotes_comparees_en_mm_avec_tolerance() -> None:
    # Attendu 12,5 m (12 500 mm) ; lu 12 500 mm → OK.
    verdicts = harness.comparer_dimensions(
        {"length": 12.5, "unit": "m"}, _dimensions(length=12.5, unit="m", length_mm=12500.0), suspect=False
    )
    assert verdicts[0]["verdict"] == "OK"

    # Lu 12 610 mm pour 12 500 attendus : au-delà de 1 mm + 0,1 % → ECART.
    verdicts = harness.comparer_dimensions(
        {"length": 12.5, "unit": "m"}, _dimensions(length=12.61, unit="m", length_mm=12610.0), suspect=False
    )
    assert verdicts[0]["verdict"] == "ECART"

    # Unité non détectée dans le PDF mais valeur conforme → SUSPECT.
    verdicts = harness.comparer_dimensions(
        {"length": 12.5, "unit": "m"}, _dimensions(length=12.5, unit=None, length_mm=12500.0), suspect=False
    )
    assert verdicts[0]["verdict"] == "SUSPECT"
    assert "unité" in verdicts[0]["note"]


def test_cote_manquante_et_absence_attendue() -> None:
    verdicts = harness.comparer_dimensions({"width": 4.2, "unit": "m"}, _dimensions(), suspect=False)
    assert verdicts[0]["verdict"] == "MANQUANT"

    verdicts = harness.comparer_dimensions(
        {"width": None, "unit": "m"}, _dimensions(width=None, width_mm=None), suspect=False
    )
    assert verdicts[0]["verdict"] == "OK_ABSENCE"


def test_comparateur_champ_suspect_mais_conforme() -> None:
    verdict = harness.comparer_champ_simple("reference", "REF-1", " REF-1 ", suspect=True)
    assert verdict["verdict"] == "SUSPECT"
    assert "à confirmer" in verdict["note"]

    verdict = harness.comparer_champ_simple("reference", "REF-1", "REF-2", suspect=True)
    assert verdict["verdict"] == "ECART"


def test_comparateur_champ_manquant_et_accents() -> None:
    assert harness.comparer_champ_simple("material", "Dacron", None, suspect=False)["verdict"] == "MANQUANT"
    # Insensible aux accents et à la casse : « lattee » = « Lattée ».
    assert harness.comparer_champ_simple("description", "Grand voile lattée", "grand voile lattee", suspect=False)[
        "verdict"
    ] == "OK"


def test_agregation_exclut_absences_attendues() -> None:
    mesure = {
        "meta": {"nb_fiches": 1},
        "fiches": [
            {
                "fichier": "a.pdf",
                "gabarit": "g1",
                "temps_ms": 5,
                "champs": [
                    {"champ": "reference", "verdict": "OK", "attendu": "R", "lu": "R", "note": ""},
                    {"champ": "reference", "verdict": "OK_ABSENCE", "attendu": None, "lu": None, "note": ""},
                    {"champ": "material", "verdict": "INATTENDU", "attendu": None, "lu": "X", "note": ""},
                ],
            }
        ],
    }

    agregats = harness.aggreger_mesure(mesure)

    assert agregats["global"]["attendus"] == 1
    assert agregats["global"]["taux_ok"] == 1.0
    assert "material" not in agregats["par_champ"]
    assert agregats["par_champ"]["reference"]["ok"] == 1


def test_normalisation_valeur_sans_accents_et_espaces() -> None:
    assert harness.normaliser_valeur("Épaisses  Lattée") == "epaisses lattee"
    assert harness.normaliser_valeur(6.6) == "6.6"

# ---------------------------------------------------------------------------
# Vérité terrain : contrôle de format (un rapport bâti sur une vérité
# partielle serait trompeur) + fixtures de référence livrées.
# ---------------------------------------------------------------------------

CHEMIN_MODELE = Path(harness.REPO_ROOT) / "docs" / "verite_terrain" / "modele_verite_terrain.json"
CHEMIN_VERITE_7792 = Path(harness.REPO_ROOT) / "docs" / "verite_terrain" / "7792-SO_ffab.json"
FIXTURE_7792 = Path(harness.REPO_ROOT) / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"


def test_le_modele_vide_est_refuse() -> None:
    """Le template documenté, non rempli, doit être refusé par le contrôle."""
    with pytest.raises(ValueError) as erreur:
        harness.charger_verite(CHEMIN_MODELE)
    assert "placeholder" in str(erreur.value)


def test_les_placeholders_sont_refuses_champ_par_champ(tmp_path: Path) -> None:
    for placeholder in ("...", "à remplir", "todo", "?", "TBD"):
        verite = {"f.pdf": {"attendu": {"reference": placeholder, "quantity": 1}}}
        chemin = tmp_path / "verite.json"
        chemin.write_text(json.dumps(verite, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="placeholder"):
            harness.charger_verite(chemin)


def test_attendu_tout_null_est_refuse(tmp_path: Path) -> None:
    verite = {"f.pdf": {"attendu": {"reference": None, "quantity": None}}}
    chemin = tmp_path / "verite.json"
    chemin.write_text(json.dumps(verite), encoding="utf-8")
    with pytest.raises(ValueError, match="tout-null"):
        harness.charger_verite(chemin)


def test_null_sur_un_champ_seul_est_accepte(tmp_path: Path) -> None:
    """null = absence attendue : vérité légitime sur un champ, refusée seulement si tout-null."""
    verite = {"f.pdf": {"attendu": {"reference": "R-1", "quantity": None}}}
    chemin = tmp_path / "verite.json"
    chemin.write_text(json.dumps(verite), encoding="utf-8")
    assert harness.charger_verite(chemin)["f.pdf"]["attendu"]["quantity"] is None


def test_cles_de_documentation_ignorees() -> None:
    """Les clés « _documentation » ne sont pas des fiches à mesurer."""
    verite = harness.charger_verite(CHEMIN_VERITE_7792)
    assert "_documentation" in verite  # présente mais ignorée
    fiches = [cle for cle in verite if not cle.startswith("_")]
    assert fiches == ["fiche-7792-SO_ffab.pdf"]


def test_verite_7792_sert_de_reference_mesurable(tmp_path: Path) -> None:
    """La fiche de référence n°2 est mesurable de bout en bout par le banc.

    On épingle la MÉCANIQUE (une fiche mesurée, agrégée par gabarit), pas les
    taux actuels : le lot B va les améliorer, ce test doit rester vert avant
    comme après.
    """
    code = harness.main(
        [
            "validate_extraction.py",
            str(FIXTURE_7792),
            "--verite",
            str(CHEMIN_VERITE_7792),
            "--sortie-json",
            str(tmp_path / "mesure-7792.json"),
        ]
    )
    assert code == 0
    mesure = json.loads((tmp_path / "mesure-7792.json").read_text(encoding="utf-8"))
    assert mesure["meta"]["nb_fiches"] == 1
    assert "spi_asymetrique_ref" in mesure["par_gabarit"]
    assert set(mesure["par_champ"]) == {"description", "dimensions.length", "dimensions.width", "material", "quantity", "reference"}


# ---------------------------------------------------------------------------
# Moteur « gabarit » (lot B) : le banc mesure aussi le nouveau moteur.
# ---------------------------------------------------------------------------


def test_moteur_gabarit_lecture_integrale_7792(tmp_path: Path, capsys) -> None:
    """Le moteur gabarit lit 6/6 champs hérités sur la fixture 7792 (§13).

    Baseline « avant » (moteur heuristique, préparation lot B) : 16,7 % (1/6).
    """
    racine = Path(__file__).resolve().parent.parent
    verite = racine / "docs/verite_terrain/7792-SO_ffab.json"
    code = harness.main(
        [
            "validate_extraction.py",
            str(racine / "sample_data/CLIENT-7792-SO"),
            "--verite",
            str(verite),
            "--moteur",
            "gabarit",
            "--sortie-json",
            str(tmp_path / "mesure.json"),
        ]
    )
    assert code == 0
    mesure = json.loads((tmp_path / "mesure.json").read_text(encoding="utf-8"))
    assert mesure["global"]["taux_ok"] == 1.0
    sortie = capsys.readouterr().out
    assert "16.7" not in sortie  # la baseline est battue
    assert "100.0 %" in sortie


def test_moteur_gabarit_inconnu_conserve_valeurs_et_avertit(tmp_path: Path) -> None:
    """Un PDF hors gabarits ne fait pas planter le banc : statut gabarit_inconnu
    + avertissement RG6 (mesures libres), le rapport reste exploitable."""
    racine = Path(__file__).resolve().parent.parent
    code = harness.main(
        [
            "validate_extraction.py",
            str(racine / "sample_data/CLIENT-123"),
            "--verite",
            ecrire_verite(tmp_path, {"fiche-technique.pdf": {"attendu": {"reference": "REF-ABSENTE-1"}}}),
            "--moteur",
            "gabarit",
            "--sortie-json",
            str(tmp_path / "mesure.json"),
        ]
    )
    mesure = json.loads((tmp_path / "mesure.json").read_text(encoding="utf-8"))
    fiche = mesure["fiches"][0]
    assert fiche["extraction_status"] == "gabarit_inconnu"
    assert any("gabarit inconnu" in a for a in fiche.get("avertissements", []))
    verdicts = {v["champ"]: v["verdict"] for v in fiche["champs"]}
    assert verdicts["reference"] == "MANQUANT"  # la valeur n'est pas INVENTÉE
    assert code in (0, 1)  # le taux peut être faible : ce n'est pas un crash


def test_moteur_gabarit_verite_etendue_74_cibles(tmp_path: Path, capsys) -> None:
    """Banc étendu sur document réel : 74/74 cibles = 100.0 % (script .py et json)."""
    racine = Path(__file__).resolve().parent.parent
    verite_py = racine / "docs/verite_terrain/VERITE_7792_COMPLETE.py"
    verite_json = racine / "docs/verite_terrain/7792-SO_ffab_complete.json"

    # 1. Via le fichier Python
    code_py = harness.main(
        [
            "validate_extraction.py",
            str(racine / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
            "--verite",
            str(verite_py),
            "--moteur",
            "gabarit",
            "--sortie-json",
            str(tmp_path / "mesure_py.json"),
        ]
    )
    assert code_py == 0
    mesure_py = json.loads((tmp_path / "mesure_py.json").read_text(encoding="utf-8"))
    assert mesure_py["global"]["attendus"] == 74
    assert mesure_py["global"]["ok"] == 74
    assert mesure_py["global"]["taux_ok"] == 1.0

    # 2. Via le fichier JSON
    code_json = harness.main(
        [
            "validate_extraction.py",
            str(racine / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
            "--verite",
            str(verite_json),
            "--moteur",
            "gabarit",
            "--sortie-json",
            str(tmp_path / "mesure_json.json"),
        ]
    )
    assert code_json == 0
    mesure_json = json.loads((tmp_path / "mesure_json.json").read_text(encoding="utf-8"))
    assert mesure_json["global"]["attendus"] == 74
    assert mesure_json["global"]["ok"] == 74
    assert mesure_json["global"]["taux_ok"] == 1.0

