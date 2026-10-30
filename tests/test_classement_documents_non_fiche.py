"""Ce que contient un dossier quand ce n'est PAS une fiche (29/10/2026).

Trois familles de documents réels de l'archive produisaient le même refus sec
« aucune fiche de fabrication reconnue … (gabarit inconnu) », qui se lit comme
une panne de reconnaissance alors que le dossier ne contient simplement pas de
fiche de fabrication :

1. **Formulaire de prise de cotes** — mesuré sur
   `/VOILES 2024/CAVAILLE ST76/CavailleGV.pdf`,
   `/VOILES 2024/FIRST 210 ST106/GSE First210.pdf`,
   `/VOILES 2024/FIRST 325 ST74A/LF DeveloppementSolentFirst325.pdf` :
   titre « Fiche de prise de cotes », mention « Compléter et retourner à
   contact@seamtech.tn », champs vides (« Nom : », « Tel : »,
   « N° de commande : » sans valeur). Aucun code commande, aucune cote.
2. **Planche de tracé SailPack** — mesuré sur
   `/VOILES 2026/ATHENA 44 ST761/JW26050B.pdf`,
   `/VOILES 2026/AURA 51 ST795/20260410 Gennak Aura51.pdf`,
   `/WESTERLY PENTLAND ST660B/ST 660b.pdf` : « Panel layout » + pied de page
   « S a i l P a c k V3.0 ». Score 0 sur tous les gabarits.
3. **PDF sans couche texte** — mesuré sur
   `/ACCESSOIRES 2026/CAGNARD ST756/*.pdf`, `/TOILE ST792 COUPE.pdf`,
   `/ACCESSOIRES 2025/…/TAUD DE GV.pdf` : 0 caractère extrait. Ce n'est pas
   un fichier abîmé : c'est une image. L'OCR n'étant pas branché sur le dépôt
   de fiches, le dossier relève d'une revue manuelle — et le refus doit le
   dire.

Les PDF sont synthétiques (reportlab) : seuls les libellés mesurés sont
reproduits, les valeurs sont inventées. Aucun PDF client n'entre dans Git.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from seamtech_search.fiches.depot import scanner_dossier
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

LARGEUR, HAUTEUR = 595.0, 842.0


def _ecrire(chemin: Path, lignes: list[str]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for indice, texte in enumerate(lignes):
        pdf.drawString(30.0, HAUTEUR - 80.0 - indice * 18.0, texte)
    pdf.save()
    return chemin


def _formulaire_prise_de_cotes(chemin: Path) -> Path:
    return _ecrire(
        chemin,
        [
            "Fiche de prise de cotes",
            "Grand-voile Artimon",
            "Compléter et retourner à contact@seamtech.tn",
            "Date :",
            "Nom : Prénom :",
            "Port : Emplacement:",
            "Tel :",
            "Modéle de coulisseau :",
            "N° de commande :",
        ],
    )


def _planche_sailpack(chemin: Path) -> Path:
    return _ecrire(
        chemin,
        [
            "Date: 1/27/2031 Sail: GENNAKER",
            "Designer: Boat: Bateau Demo 44",
            "Company: Chantier Demo Customer: Client Demo",
            "Filename: DEMO01.pak",
            "Panel layout",
            "PZ5_0 P8 Z5 M1 M1",
            "Copyright 2003-2031 BSG Développements",
            "S a i l P a c k V3.0 1 / 4",
        ],
    )


def _pdf_sans_texte(chemin: Path) -> Path:
    """PDF d'une seule image : aucune couche texte (forme mesurée des plans)."""
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    # Un rectangle plein : le PDF est valide et lisible, mais il n'y a aucun texte.
    pdf.rect(50, 50, 400, 600, fill=1)
    pdf.save()
    return chemin


def _fiche_voile(chemin: Path, code: str) -> Path:
    return _ecrire(
        chemin,
        [
            'Fiche de fabrication "Grand-voile"',
            f"Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
            f"(45') de Client Demo (Commande #508 DEMO1234) {code}",
            "Découpe et fabrication Atelier Démo",
            f"Fichier  {code} (commande {code}) dessiné par A. le 28 janvier 2031",
            "Tissu(s) (Pas de carbon): Newport 9.45 AP Blanc - White",
            "Type des coulisseaux : A031 (X3)",
            "Nombre de lattes : 4",
        ],
    )


def test_formulaire_de_prise_de_cotes_entre_en_fiche_de_reprise(tmp_path) -> None:
    """Un formulaire ne fait PLUS perdre le dossier (passe du 29/10, soir).

    Avant : le dossier était refusé (`plan.pdf_fiche is None`), donc 21 dossiers
    réels de `VOILES 2024` restaient invisibles dans l'application. Désormais il
    entre en « fiche de reprise » : ce que le document EST est écrit tel quel
    (« Prise de cotes »), le code vient du nom du dossier avec sa provenance, et
    une anomalie haute exige la saisie manuelle. Le formulaire n'est jamais
    présenté comme une fiche de fabrication.
    """
    dossier = tmp_path / "ZZ 701"
    dossier.mkdir()
    _formulaire_prise_de_cotes(dossier / "Cotes client.pdf")
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert plan.raison_refus is None
    assert plan.pdf_fiche is not None and plan.pdf_fiche.name == "Cotes client.pdf"
    fiche = plan.fiche_extraite
    assert fiche is not None
    assert fiche.titre == "Prise de cotes"
    assert fiche.code == "ZZ 701"
    provenance = next(c for c in fiche.champs if c.champ == "fiche.code")
    assert provenance.methode == "nom_dossier"
    assert [a.code for a in fiche.anomalies] == ["fiche_de_fabrication_absente"]


def test_planche_sailpack_devient_un_plan_de_fabrication(tmp_path) -> None:
    """La planche SailPack entre comme PLAN, jamais comme fiche de fabrication."""
    dossier = tmp_path / "ZZ 702"
    dossier.mkdir()
    _planche_sailpack(dossier / "DEMO01.pdf")
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert plan.raison_refus is None and plan.pdf_fiche is not None
    fiche = plan.fiche_extraite
    assert fiche is not None
    assert fiche.titre == "Plan de fabrication"
    assert fiche.type_voile_libelle == "Gennaker"
    assert {a.code for a in fiche.anomalies} == {"fiche_de_fabrication_absente"}


def test_pdf_sans_couche_texte_entre_en_revue_manuelle(tmp_path) -> None:
    """Un PDF image n'est PAS un fichier abîmé : il entre, marqué à relire."""
    dossier = tmp_path / "ZZ 703"
    dossier.mkdir()
    _pdf_sans_texte(dossier / "PLAN COUPE.pdf")
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert plan.raison_refus is None and plan.pdf_fiche is not None
    fiche = plan.fiche_extraite
    assert fiche is not None
    assert fiche.titre == "Document scanné"
    # Aucune cote n'est inventée : la fiche est vide et le dit.
    assert fiche.cotes == []
    assert [a.code for a in fiche.anomalies] == ["fiche_de_fabrication_absente"]


def test_melange_de_documents_non_fiche_ne_perd_aucune_piece(tmp_path) -> None:
    """Plan + PDF image : le dossier entre, et les DEUX fichiers restent attachés."""
    dossier = tmp_path / "ZZ 704"
    dossier.mkdir()
    _planche_sailpack(dossier / "PLAN.pdf")
    _pdf_sans_texte(dossier / "COUPE.pdf")
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert plan.raison_refus is None and plan.pdf_fiche is not None
    noms = {plan.pdf_fiche.name} | {piece.chemin.name for piece in plan.pieces}
    assert noms == {"PLAN.pdf", "COUPE.pdf"}


def test_une_vraie_fiche_a_cote_d_une_planche_reste_deposee(tmp_path) -> None:
    """La classification ne doit JAMAIS bloquer un dossier qui contient sa fiche."""
    dossier = tmp_path / "ZZ 705"
    dossier.mkdir()
    _planche_sailpack(dossier / "PLAN.pdf")
    _fiche_voile(dossier / "ZZ 705_ffab.pdf", "ZZ 705")
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert plan.raison_refus is None
    assert plan.pdf_fiche is not None and plan.pdf_fiche.name == "ZZ 705_ffab.pdf"
    assert {piece.chemin.name for piece in plan.pieces} == {"PLAN.pdf"}


# ---------------------------------------------------------------------------
# Un formulaire n'est jamais une fiche, et un mot n'est jamais un code
# (mesuré sur /VOILES 2024/NS44 ST88 : une fiche « pour » était apparue)
# ---------------------------------------------------------------------------


def test_formulaire_riche_n_est_jamais_retenu_comme_fiche(tmp_path) -> None:
    """Un formulaire de cotes reste un formulaire même si un gabarit l'accroche.

    Mesure réelle : `/VOILES 2024/NS44 ST88/NS44 GV.pdf` — le gabarit
    accessoire le reconnaissait faiblement (score 1) et le dossier était
    « traité » avec un code lu dans le formulaire. Le formulaire ne doit plus
    être candidat du tout.
    """
    dossier = tmp_path / "ZZ 706"
    dossier.mkdir()
    _ecrire(
        dossier / "NS44 GV.pdf",
        [
            "Fiche de prise de cotes",
            "Ancienne Grand-voile Ancien Artimon",
            "Compléter et retourner à contact@seamtech.tn",
            "Nom : Prénom :",
            "Port : Emplacement:",
            "Tel :",
            "Ris automatiques : Non Oui",
            "Ris auto. pour : 1 1 et 2 1 et 2 et 3",
            "Commentaires",
        ],
    )
    plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    # Le dossier entre désormais (rien ne se perd), mais le mot « pour » du
    # formulaire ne devient JAMAIS un code : le code vient du nom du dossier.
    fiche = plan.fiche_extraite
    assert fiche is not None
    assert fiche.code == "ZZ 706"
    assert fiche.titre == "Prise de cotes"
    assert all(c.valeur_normalisee != "pour" for c in fiche.champs)


def test_un_mot_sans_chiffre_n_est_jamais_un_code() -> None:
    """Tous les codes réels portent un chiffre ; « pour » n'en est pas un."""
    from seamtech_search.fiches.extraction import _code_plausible

    for vrai in ("ST64", "ST 074b", "170124CS", "250821JA", "ST 508V2", "ST 089"):
        assert _code_plausible(vrai) is True, vrai
    for faux in ("pour", "Commande", "", "   ", None, "Longueur du lazy"):
        assert _code_plausible(faux) is False, faux
