"""Dossier réel SANS fiche de fabrication : fiche de reprise (29/10/2026, soir).

Mesure de départ : sur `VOILES 2024` (24 dossiers réels), **21 étaient refusés**.
Trois familles de documents, toutes mesurées sur les PDF de l'atelier :

- planche **SailPack** (`ST 076.pdf`, `ST 088.pdf`, `GuepardRFG_CC.pdf`…) qui
  imprime pourtant `Date:`, `Sail:`, `Boat:`, `Customer:`, `Order Ref.:` et une
  grille `Panel layout` / `Measurement Check List` ;
- **formulaire de prise de cotes** (à remplir : aucune cote de fabrication) ;
- **PDF image** (aucune couche texte, OCR non disponible sur le poste d'essai).

Les PDF de ces tests sont synthétiques (reportlab) : seuls les libellés mesurés
sont reproduits, les valeurs sont inventées — aucun document client dans Git.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from seamtech_search.fiches.normalisation import cote_en_metres
from seamtech_search.fiches.reprise import (
    lire_sailpack,
    nom_sans_code,
)

LARGEUR, HAUTEUR = 595.0, 842.0


def _ecrire(chemin: Path, lignes: list[str]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for indice, texte in enumerate(lignes):
        pdf.drawString(30.0, HAUTEUR - 40.0 - indice * 16.0, texte)
    pdf.save()
    return chemin


def _planche_complete(chemin: Path) -> Path:
    return _ecrire(
        chemin,
        [
            "Date: 25/10/2031 Sail: Gvsl",
            "Designer: Yann Boat: Bateau Demo 44",
            "Company: Chantier Demo",
            "Customer: Atelier Demo / Commande #88 DEMOAAAA",
            "Filename: ZZ 001.pak",
            "Order Ref.: ZZ 001",
            "Panel layout",
            "True area = 36.360 m2",
            "S a i l P a c k V3.0 1 / 8",
        ],
    )


def test_planche_sailpack_lue_champ_par_champ(tmp_path: Path) -> None:
    lecture = lire_sailpack(_planche_complete(tmp_path / "ZZ 001.pdf"))
    assert lecture is not None
    assert lecture.code == "ZZ 001"
    assert lecture.bateau == "Bateau Demo 44"
    assert lecture.client == "Atelier Demo"
    assert lecture.reference == "88"
    assert lecture.sail == "Gvsl"
    assert lecture.date == "25/10/2031"
    assert lecture.surface_m2 == 36.36


def test_planche_check_list_lue_avec_ses_cotes(tmp_path: Path) -> None:
    """« Geoluff / Geoleech / Geofoot » = guindant / chute / bordure."""
    chemin = _ecrire(
        tmp_path / "ZZ 002.pdf",
        [
            "Date: 02/04/2031 Sail: MSL",
            "Designer: Flavio Boat: Bateau Demo 38",
            "Company: Customer:",
            "Filename: ZZ 002.pak",
            "Order Ref.:",
            "Measurement Check List",
            "Measurement rules Ref.: mainsails",
            "True area = 32.000 m2 [_____Geoluff] = 12.900 m [___Geoleech] = 13.286 m [____Geofoot] = 4.200 m",
        ],
    )
    lecture = lire_sailpack(chemin)
    assert lecture is not None
    assert lecture.code is None, "un « Order Ref.: » vide ne devient jamais un code"
    assert lecture.regles == "mainsails"
    assert (lecture.guindant_m, lecture.chute_m, lecture.bordure_m) == (12.9, 13.286, 4.2)


def test_une_valeur_d_etiquette_ne_devient_jamais_le_client(tmp_path: Path) -> None:
    """Mesuré : « Company: » et « Customer: » vides ⇒ « Filename: … » suit."""
    chemin = _ecrire(
        tmp_path / "ZZ 003.pdf",
        [
            "Date: 20/02/2031 Sail: RFG",
            "Designer: Flavio Boat: Bateau Demo",
            "Company:",
            "Customer:",
            "Filename: ZZ 003.pak",
            "Order Ref.: 24032",
        ],
    )
    lecture = lire_sailpack(chemin)
    assert lecture is not None
    assert lecture.client is None
    assert lecture.company is None
    assert lecture.code == "24032"


def test_un_couple_de_dimensions_n_est_jamais_une_cote() -> None:
    """« chute 60x260 polyester » (renfort) donnait une bordure de 60 m."""
    assert cote_en_metres("60x260") is None
    assert cote_en_metres("chute 60x260 polyester") is None
    assert cote_en_metres("6,87") == 6.87
    assert cote_en_metres("7.65 m") == 7.65


def test_nom_du_bateau_deduit_du_dossier_sans_le_code() -> None:
    assert nom_sans_code("CAVAILLE ST76", "ST76") == "Cavaille"
    assert nom_sans_code("FIRST 38 MSL - 030424OS", "030424OS") == "First 38 Msl"
    assert nom_sans_code("SANS REFERENCE", None) is None

def test_le_gabarit_de_reprise_est_connu_des_deux_cotes() -> None:
    """Le code du gabarit de reprise ne peut pas diverger entre les modules.

    `reprise.py` le pose sur la fiche, `gabarits.py` l'enregistre pour que
    l'écran affiche une provenance ; la persistance résout `id_gabarit` sur le
    COUPLE (code, version) — une divergence laisserait la colonne vide.
    """
    from seamtech_search.fiches import gabarits, reprise

    assert gabarits.GABARIT_REPRISE.code == reprise.CODE_GABARIT_REPRISE
    assert gabarits.GABARIT_REPRISE.version == reprise.VERSION_GABARIT_REPRISE
    # Aucun document ne porte cette ancre : le gabarit ne doit jamais être
    # proposé à la détection.
    assert gabarits.GABARIT_REPRISE.champs == []
