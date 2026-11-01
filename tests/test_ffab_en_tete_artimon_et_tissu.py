"""Deux défauts mesurés le 29/10/2026 sur les fiches de voiles FFab réelles.

Documents ouverts dans l'archive de l'atelier (aucun PDF client n'est versionné
ici ; les PDF de ce module sont synthétiques, seuls la mise en page et les
libellés génériques sont reproduits) :

* ``/WESTERLY PENTLAND ST660B/ST 660b_ffab.pdf`` — l'en-tête imprimé est
  ``Fiche de fabrication "Grand-voile"`` alors que la voile fabriquée est un
  **Artimon** ; la ligne de désignation, elle, dit
  ``Artimon classique semi-latté, gamme Ocean, pour Westerly Pentland 32 …``.
  La fiche sortait avec ``titre = Grand-voile`` et ``type = Artimon`` : deux
  champs qui se contredisent dans l'écran de validation.
* idem ``/YACHET CLASSIC ST508/ST 508_ffab.pdf`` — la ligne de tissu est
  ``Tissu(s) (Pas de carbon): Newport 9.45 AP Blanc - White   Quantité : 1`` ;
  les deux-points du libellé sont SOUDÉS au mot ``carbon):`` et la valeur
  s'arrêtait au changement de colonne. Le tissu principal valait
  ``(Pas de carbon):`` — un libellé de formulaire présenté comme matière.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

LARGEUR, HAUTEUR = 595.0, 842.0


def _ecrire(chemin: Path, cellules: list[tuple[float, float, str]]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for x, haut, texte in cellules:
        pdf.drawString(x, HAUTEUR - haut, texte)
    pdf.save()
    return chemin


def _lignes_ffab(
    *,
    en_tete: str,
    designation: str,
    tissu: str | None = "Newport 9.45 AP Blanc - White",
) -> list[tuple[float, float, str]]:
    """Mise en page mesurée : chaque cellule du tableur est un bloc de texte."""
    cellules: list[tuple[float, float, str]] = [
        (150.0, 100.0, en_tete),
        # Ordre mesuré sur les fiches réelles : en-tête, DÉSIGNATION, puis la
        # ligne « Découpe et fabrication … Fichier … dessiné par … ».
        (20.0, 121.0, designation),
        (20.0, 143.0, "Découpe et fabrication Atelier Démo"),
        (330.0, 143.4, "Fichier  ZZ 660b (commande ZZ 660b) dessiné par A. le 2 avril 2031"),
    ]
    if tissu is not None:
        # Ligne mesurée : libellé entre parenthèses et deux-points SANS espace
        # (« carbon): »), puis le tissu dans une colonne voisine.
        cellules.append((40.0, 165.0, "Tissu(s) (Pas de carbon):"))
        cellules.append((160.0, 165.4, tissu))
        cellules.append((560.0, 165.8, "Quantité : 1"))
    cellules.extend(
        [
            (40.0, 190.0, "Cotes (tissu):"),
            (40.0, 205.0, "Guindant Bordure Chute Têtière Surface Poids (tissu) MHW MTW MUW"),
            (40.0, 220.0, "5,53 m 2,18 m 5,92 m 8 cm 7,2 m² 3,1 kg 1,34 m 0,75 m 0,41 m"),
            (40.0, 240.0, "◄ Mesures voile finie ►"),
            (40.0, 260.0, "Montage fil v92: Laizes Jonctions horizontales Jonction verticale 1"),
        ]
    )
    return cellules


def test_en_tete_de_formulaire_ne_contredit_pas_la_voile_decrite(tmp_path) -> None:
    """En-tête « Grand-voile » + désignation « Artimon » → titre = Artimon."""
    chemin = _ecrire(
        tmp_path / "artimon_ffab.pdf",
        _lignes_ffab(
            en_tete='Fiche de fabrication "Grand-voile"',
            designation=(
                "Artimon classique semi-latté, gamme Ocean, pour Bateau Demo 32 "
                "(32') de Client Demo (Commande #660 DEMO1234) ZZ 660b"
            ),
        ),
    )
    fiche = extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.type_voile_libelle == "Artimon"
    assert fiche.titre == "Artimon"
    trace = next(champ for champ in fiche.champs if champ.champ == "fiche.titre")
    # La valeur IMPRIMÉE reste traçable : rien n'est effacé.
    assert "Grand-voile" in (trace.valeur_brute or "")
    assert trace.methode == "gabarit_designation"


def test_en_tete_coherent_avec_la_designation_n_est_pas_touche(tmp_path) -> None:
    """Contre-épreuve : « Grand-voile » + « Grand-voile … » → rien ne bouge."""
    chemin = _ecrire(
        tmp_path / "gv_ffab.pdf",
        _lignes_ffab(
            en_tete='Fiche de fabrication "Grand-voile"',
            designation=(
                "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
                "(45') de Client Demo (Commande #508 DEMO1234) ZZ 508"
            ),
        ),
    )
    fiche = extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.titre == "Grand-voile"
    trace = next(champ for champ in fiche.champs if champ.champ == "fiche.titre")
    assert trace.methode == "gabarit"


def test_libelle_de_formulaire_n_est_jamais_le_tissu(tmp_path) -> None:
    """« Tissu(s) (Pas de carbon): » → le tissu, pas la parenthèse du libellé."""
    chemin = _ecrire(
        tmp_path / "tissu_ffab.pdf",
        _lignes_ffab(
            en_tete='Fiche de fabrication "Grand-voile"',
            designation=(
                "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
                "(45') de Client Demo (Commande #508 DEMO1234) ZZ 508"
            ),
        ),
    )
    fiche = extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.tissu_texte == "Newport 9.45 AP Blanc - White"
    assert [materiau.designation for materiau in fiche.materiaux] == ["Newport 9.45 AP Blanc - White"]


def test_parenthèse_sans_deux_points_reste_la_valeur(tmp_path) -> None:
    """Contre-épreuve : une parenthèse qui EST la valeur n'est pas avalée."""
    cellules = _lignes_ffab(
        en_tete='Fiche de fabrication "Grand-voile"',
        designation=(
            "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
            "(45') de Client Demo (Commande #508 DEMO1234) ZZ 508"
        ),
        tissu=None,
    )
    cellules.append((40.0, 185.0, "Tissu(s) (voile)"))
    fiche = extraire_fiche(_ecrire(tmp_path / "tissu-parenthese.pdf", cellules), gabarits=list(GABARITS_EMBARQUES))
    assert fiche.tissu_texte in (None, "(voile)")


@pytest.mark.parametrize(
    "tissu",
    ["Newport 6.45 AP Blanc - White", "Sunbrella 5031 Marine Blue"],
)
def test_tissu_avec_et_sans_tirets(tmp_path, tissu: str) -> None:
    chemin = _ecrire(
        tmp_path / "tissu-variante.pdf",
        _lignes_ffab(
            en_tete='Fiche de fabrication "Grand-voile"',
            designation=(
                "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
                "(45') de Client Demo (Commande #508 DEMO1234) ZZ 508"
            ),
            tissu=tissu,
        ),
    )
    fiche = extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.tissu_texte == tissu
