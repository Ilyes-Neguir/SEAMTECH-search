"""Tests du banc d'inventaire Phase 0 — scripts/inventaire_archive.py.

Le contrat central est la SÉCURITÉ : le script parcourt une arborescence
d'archive en lecture seule et ne doit jamais la modifier. Ces tests le
prouvent par empreintes SHA-256 de chaque fichier (et de l'arborescence
complète) avant et après exécution, sur une archive synthétique construite
avec reportlab/openpyxl — les mêmes briques que les PDF réels.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "inventaire_archive.py"


def _load_inventory() -> Any:
    spec = importlib.util.spec_from_file_location("inventaire_archive", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["inventaire_archive"] = module
    spec.loader.exec_module(module)
    return module


inventory = _load_inventory()

# Deux fiches de même gabarit (mêmes libellés, mêmes positions) mais de
# valeurs différentes : elles doivent tomber dans la MÊME famille de gabarit
# et ne PAS être des doublons.
FICHE_LABELS = [
    "Fiche de fabrication",
    "Référence : {reference}",
    "Quantité : {quantite}",
    "Matière : Monofilm K{grammage}",
    "Description : Spi asymétrique",
    "Cotes",
    "Longueur : {longueur} m",
    "Largeur : {largeur} m",
    "Mesures finies",
]
PLAN_LINES = ["Plan de coupe", "Profil avant", "Echelle 1:20"]


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


def make_scan_pdf(path: Path) -> Path:
    """PDF sans aucun texte : l'image d'un vieux document scanné."""
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path))
    # Aucun drawString : le PDF ne contient aucune couche texte.
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, 400, 400, stroke=0, fill=1)
    pdf.save()
    return path


def make_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    classeur = Workbook()
    classeur.active.append(["Désignation", "Valeur"])
    classeur.save(path)
    return path


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    """Archive synthétique : 2 fiches (même gabarit), 1 plan + sa copie, 1 scan, 1 xlsx, 1 txt."""
    racine = tmp_path / "archive"
    make_pdf(
        racine / "2024" / "CLIENT-A" / "fiche-7792.pdf",
        [ligne.format(reference="REF-7792-SO", quantite=1, grammage=903, longueur="6,60", largeur="3,08") for ligne in FICHE_LABELS],
    )
    make_pdf(
        racine / "2025" / "CLIENT-B" / "fiche-8842.pdf",
        [ligne.format(reference="REF-8842-AB", quantite=3, grammage=402, longueur="5,20", largeur="2,95") for ligne in FICHE_LABELS],
    )
    plan = make_pdf(racine / "2024" / "CLIENT-A" / "plan-coupe.pdf", PLAN_LINES)
    plan.read_bytes()  # s'assurer que le PDF est bien écrit avant la copie
    (racine / "2025" / "CLIENT-B").mkdir(parents=True, exist_ok=True)
    (racine / "2025" / "CLIENT-B" / "plan-coupe.pdf").write_bytes(plan.read_bytes())
    make_scan_pdf(racine / "2024" / "CLIENT-A" / "scan-ancien.pdf")
    make_xlsx(racine / "2024" / "CLIENT-A" / "nomenclature.xlsx")
    (racine / "2025" / "CLIENT-C").mkdir(parents=True, exist_ok=True)
    (racine / "2025" / "CLIENT-C" / "notes.txt").write_text("Notes d'atelier", encoding="utf-8")
    return racine


def snapshot_arbre(racine: Path) -> dict[str, tuple[int, str, int]]:
    """Empreinte de tout l'arbre (fichiers + dossiers) : taille, sha256, mtime_ns."""
    etat: dict[str, tuple[int, str, int]] = {}
    for chemin in sorted(racine.rglob("*")):
        stat = chemin.stat()
        contenu = b"" if chemin.is_dir() else chemin.read_bytes()
        etat[str(chemin.relative_to(racine))] = (stat.st_size, hashlib.sha256(contenu).hexdigest(), stat.st_mtime_ns)
    return etat


def lancer(archive_path: Path, tmp_path: Path, *options: str) -> int:
    sortie = tmp_path / "rapport"
    return inventory.main(
        ["inventaire_archive.py", str(archive_path), "--sortie", str(sortie), *options]
    )


def charger_rapport(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "rapport" / "inventaire.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Le contrat de sécurité : l'archive n'est pas modifiée, jamais.
# ---------------------------------------------------------------------------


def test_archive_inchangee_apres_inventaire(archive: Path, tmp_path: Path) -> None:
    avant = snapshot_arbre(archive)
    code = lancer(archive, tmp_path)
    apres = snapshot_arbre(archive)

    assert code == 0
    assert apres == avant, "l'inventaire a modifié l'archive (taille, contenu ou horodatage)"


def test_aucun_fichier_ajoute_ou_supprime(archive: Path, tmp_path: Path) -> None:
    chemins_avant = {str(p.relative_to(archive)) for p in archive.rglob("*")}
    dossiers_avant = {p for p in chemins_avant if (archive / p).is_dir()}
    code = lancer(archive, tmp_path)
    chemins_apres = {str(p.relative_to(archive)) for p in archive.rglob("*")}

    assert code == 0
    assert chemins_apres == chemins_avant
    assert dossiers_avant <= chemins_apres


def test_sortie_refusee_dans_l_archive(archive: Path, tmp_path: Path) -> None:
    avant = snapshot_arbre(archive)
    code = inventory.main(
        ["inventaire_archive.py", str(archive), "--sortie", str(archive / "rapport-interdit")]
    )

    assert code == 2
    assert not (archive / "rapport-interdit").exists()
    assert snapshot_arbre(archive) == avant


def test_racine_absente_code_2(tmp_path: Path) -> None:
    assert inventory.main(["inventaire_archive.py", str(tmp_path / "n-existe-pas")]) == 2


# ---------------------------------------------------------------------------
# Chiffres de l'inventaire.
# ---------------------------------------------------------------------------


def test_comptages_et_volumes(archive: Path, tmp_path: Path) -> None:
    assert lancer(archive, tmp_path) == 0
    rapport = charger_rapport(tmp_path)

    nb_dossiers = 1 + sum(1 for p in archive.rglob("*") if p.is_dir())  # la racine incluse
    nb_fichiers = sum(1 for p in archive.rglob("*") if p.is_file())
    assert rapport["nb_dossiers"] == nb_dossiers
    assert rapport["nb_fichiers"] == nb_fichiers == 7
    assert rapport["volume_total_octets"] == sum(p.stat().st_size for p in archive.rglob("*") if p.is_file())
    assert rapport["par_type"][".pdf"]["nb"] == 5  # 2 fiches + 2 plans (l'original et sa copie) + 1 scan
    assert rapport["par_type"][".xlsx"]["nb"] == 1
    assert rapport["par_type"][".txt"]["nb"] == 1
    # L'année vient de la date de modification (les fichiers viennent d'être créés).
    annee_courante = str(datetime.now().year)
    assert rapport["par_annee"][annee_courante]["nb"] == 7


def test_pdf_natifs_plans_et_scans(archive: Path, tmp_path: Path) -> None:
    lancer(archive, tmp_path)
    pdf = charger_rapport(tmp_path)["pdf"]

    assert pdf["techniques"] == 2
    assert pdf["plans"] == 2  # l'original et sa copie
    assert pdf["natifs"] == 4
    assert pdf["scans_probables"] == 1
    assert pdf["erreurs_extraction"] == 0


def test_doublons_detectes(archive: Path, tmp_path: Path) -> None:
    assert lancer(archive, tmp_path) == 0
    rapport = charger_rapport(tmp_path)
    taille_plan = (archive / "2024" / "CLIENT-A" / "plan-coupe.pdf").stat().st_size

    assert rapport["doublons"]["groupes"] == 1
    assert rapport["doublons"]["fichiers_redondants"] == 1
    assert rapport["doublons"]["octets_redondants"] == taille_plan

    with (tmp_path / "rapport" / "inventaire_doublons.csv").open(encoding="utf-8-sig", newline="") as fichier:
        lignes = list(csv.DictReader(fichier, delimiter=";"))
    assert len(lignes) == 2
    assert {Path(ligne["chemin"]).parent.name for ligne in lignes} == {"CLIENT-A", "CLIENT-B"}


def test_les_fiches_ne_sont_pas_des_doublons(archive: Path, tmp_path: Path) -> None:
    lancer(archive, tmp_path)
    rapport = charger_rapport(tmp_path)

    assert rapport["doublons"]["groupes"] == 1  # seulement la copie du plan


def test_localisation_des_fiches(archive: Path, tmp_path: Path) -> None:
    lancer(archive, tmp_path)
    localisations = charger_rapport(tmp_path)["fiches_techniques"]["localisations_principales"]

    dossiers = {entree["dossier"]: entree["nb_fiches"] for entree in localisations}
    assert dossiers == {"archive/2024/CLIENT-A": 1, "archive/2025/CLIENT-B": 1}
    assert "archive" in charger_rapport(tmp_path)["racines"][0]


def test_familles_de_gabarits(archive: Path, tmp_path: Path) -> None:
    lancer(archive, tmp_path)
    familles = charger_rapport(tmp_path)["familles_gabarits"]

    assert len(familles) == 1, "deux fiches de même structure doivent former UNE famille"
    famille = familles[0]
    assert famille["nb_pdf"] == 2
    assert famille["empreintes_fines_distinctes"] == 1, "mêmes libellés aux mêmes positions"
    assert {"cotes", "mesures", "finies", "longueur", "largeur"} <= set(famille["libelles_communs"])
    noms = {Path(exemple).name for exemple in famille["exemples"]}
    assert noms == {"fiche-7792.pdf", "fiche-8842.pdf"}


def test_determinisme_deux_executions(archive: Path, tmp_path: Path) -> None:
    assert lancer(archive, tmp_path) == 0
    premiere = charger_rapport(tmp_path)
    premiere.pop("meta")

    sortie_deux = tmp_path / "rapport-2"
    assert inventory.main(["inventaire_archive.py", str(archive), "--sortie", str(sortie_deux)]) == 0
    deuxieme = json.loads((sortie_deux / "inventaire.json").read_text(encoding="utf-8"))
    deuxieme.pop("meta")

    assert deuxieme == premiere


def test_sans_empreintes_desactive_les_doublons(archive: Path, tmp_path: Path) -> None:
    assert lancer(archive, tmp_path, "--sans-empreintes") == 0
    rapport = charger_rapport(tmp_path)

    assert rapport["doublons"]["groupes"] == 0
    assert rapport["doublons"]["fichiers_redondants"] == 0


def test_csv_fichiers_couvre_tout(archive: Path, tmp_path: Path) -> None:
    lancer(archive, tmp_path)
    with (tmp_path / "rapport" / "inventaire_fichiers.csv").open(encoding="utf-8-sig", newline="") as fichier:
        lignes = list(csv.DictReader(fichier, delimiter=";"))

    assert len(lignes) == 7
    par_nom = {Path(ligne["chemin"]).name: ligne for ligne in lignes}
    assert par_nom["scan-ancien.pdf"]["categorie_pdf"] == "scan_probable"
    assert par_nom["fiche-7792.pdf"]["categorie_pdf"] == "technique"
    assert par_nom["plan-coupe.pdf"]["categorie_pdf"] == "plan"
    assert par_nom["nomenclature.xlsx"]["categorie_pdf"] == ""
    assert int(par_nom["plan-coupe.pdf"]["groupe_doublon"]) > 0


# ---------------------------------------------------------------------------
# Unités pures : empreinte et regroupement en familles.
# ---------------------------------------------------------------------------


def mot(texte: str, x: float, y: float) -> dict[str, Any]:
    return {"text": texte, "x0": x, "top": y}


def test_empreinte_gabarit_ignore_les_valeurs_numeriques() -> None:
    mots_a = [mot("Référence", 50, 700), mot("REF-7792-SO", 200, 700), mot("Quantité", 50, 680), mot("Longueur", 50, 660)]
    mots_b = [mot("Référence", 50, 700), mot("REF-9911-ZZ", 200, 700), mot("Quantité", 50, 680), mot("Longueur", 50, 660)]

    empreinte_a = inventory.empreinte_gabarit(mots_a)
    empreinte_b = inventory.empreinte_gabarit(mots_b)

    assert empreinte_a is not None and empreinte_b is not None
    assert empreinte_a["cle_famille"] == empreinte_b["cle_famille"]
    assert empreinte_a["empreinte_fine"] == empreinte_b["empreinte_fine"]
    assert "ref-7792-so" not in empreinte_a["libelles"]


def test_empreinte_gabarit_sans_mots_alphabetiques() -> None:
    assert inventory.empreinte_gabarit([mot("7792", 50, 700), mot("6,60", 200, 700)]) is None
    assert inventory.empreinte_gabarit([]) is None


def test_regrouper_familles_fusionne_variantes_proches() -> None:
    base = [f"libelle-{index}" for index in range(20)]
    variante = base[:-1] + ["libelle-nouveau"]  # 19/20 libellés communs → Jaccard 0,9
    loin = [f"autre-{index}" for index in range(20)]  # aucun libellé commun

    def empreinte(libelles: list[str]) -> dict[str, Any]:
        return {
            "cle_famille": inventory.hashlib.sha256("|".join(sorted(libelles)).encode()).hexdigest(),
            "libelles": sorted(libelles),
            "empreinte_fine": inventory.hashlib.sha256("|".join(sorted(libelles)).encode()).hexdigest(),
        }

    familles = inventory.regrouper_familles(
        [
            ("a.pdf", empreinte(base)),
            ("b.pdf", empreinte(variante)),
            ("c.pdf", empreinte(loin)),
        ]
    )

    assert len(familles) == 2
    par_taille = sorted(familles, key=lambda famille: -len(famille.fichiers))
    assert {Path(chemin).name for chemin in par_taille[0].fichiers} == {"a.pdf", "b.pdf"}
    assert par_taille[0].libelles == sorted(set(base) | {"libelle-nouveau"})
    assert {Path(chemin).name for chemin in par_taille[1].fichiers} == {"c.pdf"}


def test_similarite_jaccard_bornes() -> None:
    assert inventory.similarite_jaccard(set(), {"a"}) == 0.0
    assert inventory.similarite_jaccard({"a"}, {"a"}) == 1.0
    assert inventory.similarite_jaccard({"a", "b"}, {"a"}) == pytest.approx(0.5)


def test_verifier_sortie_hors_archive() -> None:
    inventory.verifier_sortie_hors_archive(Path("/tmp/rapport"), [Path("/data/archive")])
    with pytest.raises(ValueError):
        inventory.verifier_sortie_hors_archive(Path("/data/archive/rapport"), [Path("/data/archive")])
    with pytest.raises(ValueError):
        inventory.verifier_sortie_hors_archive(Path("/data"), [Path("/data/archive")])
