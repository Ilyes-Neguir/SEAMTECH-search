"""Genere la fiche de reference SYNTHETIQUE (remplacement de la fiche client reelle).

Pourquoi ce generateur : le depot contenait un document client REEL
(une fiche de portant de 166 990 o, recue du commanditaire) utilise comme
verite terrain d'extraction. Ce document appartenait au commanditaire et
devait etre purge. Il est remplace par une fiche SYNTHETIQUE qui :

  * reprend EXACTEMENT la mise en page (positions et corps) de la fiche
    d'origine, pour que les regles du gabarit FICHE_PORTANT_V1 - dont
    certaines lisent des valeurs ALIGNEES SUR UNE COLONNE - continuent de
    s'appliquer a l'identique ;
  * conserve les 67 valeurs TECHNIQUES (cotes, matieres, accastillage) :
    elles n'identifient aucun client ;
  * remplace les 7 valeurs identifiantes (client, raison sociale, atelier,
    code de commande, dessinateur, fichier source) par des valeurs fictives.

Les positions sont donc une donnee, pas un calcul : toute regeneration
reproduit la meme page. Seules les metadonnees PDF (date) varient, ce qui
est pourquoi l'inventaire de tests/test_confidentialite_depots.py n'epingle
PAS ce fichier par SHA-256.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.pdfgen import canvas

LARGEUR, HAUTEUR = 841.92, 595.32  # A4 paysage, comme la fiche d'origine
POLICE = "Helvetica"

# Le filet du tableau des cotes. Il n'est pas decoratif : la detection de
# fiche (seamtech_search/detection_fiches.py) tient `grille_tracee` pour
# structure suffisante, et cette grille est ce que pdfplumber lit par
# `extract_tables`. Sans elle, la fiche synthetique perdait son caractere de
# tableau et le garde `test_reference_est_detectee_avec_sa_grille` tombait.
# Les coordonnees encadrent les trois rangees de cotes (en-tete, mesures
# dessin, mesures finies) et les colonnes de valeurs.
TRAITS_H: tuple[tuple[float, float, float], ...] = (  # (x0, x1, y)
    (58.0, 802.0, 474.0),
    (58.0, 802.0, 463.0),
    (58.0, 802.0, 449.5),
    (58.0, 802.0, 436.0),
)
TRAITS_V: tuple[tuple[float, float, float], ...] = (  # (y0, y1, x)
    (436.0, 474.0, 58.0),
    (436.0, 474.0, 122.0),
    (436.0, 474.0, 200.0),
    (436.0, 474.0, 272.0),
    (436.0, 474.0, 358.0),
    (436.0, 474.0, 430.0),
    (436.0, 474.0, 500.0),
    (436.0, 474.0, 565.0),
    (436.0, 474.0, 635.0),
    (436.0, 474.0, 700.0),
    (436.0, 474.0, 760.0),
    (436.0, 474.0, 802.0),
)

# (x, y, corps, texte) - y compte depuis le BAS de la page, comme en PDF.
SPANS: tuple[tuple[float, float, float, str], ...] = (
    (349.4, 564.2, 7.8, 'Fiche de fabrication "Voile de portant"'),
    (39.5, 520.1, 9.0, "Spi Asymétrique Medium Régate pour 29er (15') de Exemplia (Demo)"),
    (756.6, 519.1, 11.2, ' 1042-EX'),
    (39.2, 507.0, 7.3, 'Découpe et fabrication EX'),
    (553.5, 507.1, 7.3, ' Fichier 1042-EX (commande 1042-EX) dessiné par Demo le 6 mars 2026'),
    (761.7, 493.4, 7.3, 'Quantité : 1'),
    (39.2, 477.6, 7.3, 'Cotes :'),
    (129.1, 466.4, 7.3, 'Guindant (SLU)'),
    (206.9, 466.4, 7.3, ' Chute (SLE)'),
    (278.4, 466.4, 7.3, ' Bordure (SF)'),
    (364.2, 466.4, 7.3, ' SHW'),
    (436.9, 466.4, 7.3, ' Surface (SPA)'),
    (541.8, 466.4, 7.3, ' Têtière'),
    (612.0, 466.4, 7.3, ' Poids (tissu)'),
    (65.4, 452.9, 7.3, 'Mesures Dessin'),
    (142.9, 452.6, 7.3, ' 6,60 m'),
    (215.4, 452.6, 7.3, ' 5,50 m'),
    (287.9, 452.6, 7.3, ' 3,08 m'),
    (361.4, 452.6, 7.3, ' 3,14 m'),
    (457.8, 452.6, 7.3, ' ~'),
    (544.9, 452.6, 7.3, ' 3 cm'),
    (621.8, 452.6, 7.3, ' 0,7 kg'),
    (68.3, 439.3, 7.3, 'Mesures Finies'),
    (142.9, 439.1, 7.3, ' 6,60 m'),
    (215.4, 439.1, 7.3, ' 5,50 m'),
    (287.9, 439.1, 7.3, ' 3,08 m'),
    (361.4, 439.1, 7.3, ' 3,14 m'),
    (445.5, 439.1, 7.3, ' 15,71 m²'),
    (39.2, 409.8, 7.3, 'Montage Collé/Cousu'),
    (46.3, 398.6, 7.3, 'Montage en Fil: V46'),
    (306.9, 382.6, 7.3, 'Surplus'),
    (39.2, 369.1, 7.3, 'Guindant - Libre'),
    (134.2, 357.9, 7.3, 'Galon - Bleu'),
    (208.7, 357.9, 7.3, ' Grammage'),
    (418.9, 357.9, 7.3, ' Distance "cosse à cosse"'),
    (551.1, 357.9, 7.3, ' ~'),
    (143.3, 342.9, 7.3, '50 mm'),
    (203.1, 342.9, 7.3, ' Nylon 65 gr/m²'),
    (457.8, 342.7, 7.3, ' ~'),
    (551.1, 342.9, 7.3, ' ~'),
    (682.5, 342.9, 7.3, ' Grammage'),
    (759.0, 342.9, 7.3, ' Rayon'),
    (39.2, 325.6, 7.3, 'Chute'),
    (615.9, 325.6, 7.3, ' Cache Insignia'),
    (688.2, 325.6, 7.3, ' Insignia'),
    (756.2, 325.6, 7.3, ' 210 mm'),
    (130.7, 314.5, 7.3, 'Galon - Rouge'),
    (208.7, 314.5, 7.3, ' Grammage'),
    (621.6, 312.1, 7.3, ' Epaisseur 01'),
    (676.3, 312.1, 7.3, ' Monofilm K903'),
    (756.2, 312.1, 7.3, ' 190 mm'),
    (143.3, 300.9, 7.3, '50 mm'),
    (203.1, 300.9, 7.3, ' Nylon 65 gr/m²'),
    (621.6, 298.5, 7.3, ' Epaisseur 02'),
    (686.4, 298.5, 7.3, ' 270 g/m²'),
    (756.2, 298.5, 7.3, ' 220 mm'),
    (39.2, 284.9, 7.3, 'Bordure'),
    (621.6, 284.9, 7.3, ' Epaisseur 03'),
    (686.4, 284.9, 7.3, ' 210 g/m²'),
    (756.2, 284.9, 7.3, ' 260 mm'),
    (132.3, 273.8, 7.3, 'Galon - Blanc'),
    (208.7, 273.8, 7.3, ' Grammage'),
    (621.6, 271.4, 7.3, ' Epaisseur 04'),
    (686.4, 271.4, 7.3, ' 170 g/m²'),
    (756.2, 271.4, 7.3, ' 300 mm'),
    (143.3, 260.2, 7.3, '50 mm'),
    (203.1, 260.2, 7.3, ' Nylon 65 gr/m²'),
    (621.6, 257.8, 7.3, ' Epaisseur 05'),
    (698.7, 257.8, 7.3, ' ~'),
    (767.4, 257.8, 7.3, ' ~'),
    (39.2, 244.2, 7.3, 'Finition'),
    (621.6, 244.2, 7.3, ' Epaisseur 06'),
    (698.7, 244.2, 7.3, ' ~'),
    (767.4, 244.2, 7.3, ' ~'),
    (360.5, 233.1, 7.3, 'Emmagasineur'),
    (621.6, 230.7, 7.3, ' Epaisseur 07'),
    (698.7, 230.7, 7.3, ' ~'),
    (767.4, 230.7, 7.3, ' ~'),
    (345.9, 219.5, 7.3, 'Bout de manoeuvre'),
    (621.6, 217.1, 7.3, ' Epaisseur 08'),
    (698.7, 217.1, 7.3, ' ~'),
    (767.4, 217.1, 7.3, ' ~'),
    (95.3, 206.0, 7.3, 'Ecoute'),
    (372.8, 206.0, 7.3, ' Chaussette'),
    (621.6, 203.6, 7.3, ' Epaisseur 09'),
    (698.7, 203.6, 7.3, ' ~'),
    (767.4, 203.6, 7.3, ' ~'),
    (97.3, 192.4, 7.3, 'Drisse'),
    (397.2, 192.4, 7.3, ' Sac'),
    (621.6, 190.0, 7.3, ' Epaisseur 10'),
    (698.7, 190.0, 7.3, ' ~'),
    (767.4, 190.0, 7.3, ' ~'),
    (390.4, 178.8, 7.3, 'V-trim'),
    (51.0, 165.3, 7.3, "Velcro anti-deroulement au point d'écoute"),
    (102.4, 151.7, 7.3, 'Retenue de contre écoute'),
    (72.8, 138.1, 7.3, 'Protection anti-UV WeatherMax-65'),
    (39.2, 122.8, 7.3, 'Notes'),
    (39.2, 111.0, 7.3, "2 x œillets n°1 au milieu d'un renforts rond Ø 200mm en Nylon + 1 dacron Ø 150 + 1 dacron 110mm pour avaleur."),
    (667.7, 111.0, 7.3, ' Logo'),
    (736.3, 111.0, 7.3, ' Exemplia'),
    (254.7, 138.6, 7.3, 'Non'),
    (254.7, 152.2, 7.3, 'Non'),
    (254.7, 165.8, 7.3, 'Non'),
    (96.6, 226.7, 7.3, 'Amure'),
    (187.3, 192.9, 7.3, 'Œillet SR12, non-sanglé'),
    (187.3, 206.4, 7.3, 'Œillet SR12, non-sanglé'),
    (485.4, 192.7, 7.3, 'Pas de sac.'),
    (502.8, 179.3, 7.3, '~'),
    (684.9, 179.3, 7.3, ' Voile régate - Insignia quadrillé 3cm'),
    (706.8, 355.5, 7.3, 'Cible_Regate_01'),
    (796.6, 158.7, 7.3, '~'),
    (502.8, 206.4, 7.3, '~'),
    (502.8, 220.0, 7.3, '~'),
    (502.8, 233.6, 7.3, '~'),
    (187.3, 233.6, 7.3, 'Œillet SR12, non-sanglé'),
    (585.4, 382.6, 7.3, '~'),
    (733.1, 382.6, 7.3, ' ~'),
    (703.7, 409.8, 7.3, 'Jonction verticale 2'),
    (419.1, 382.6, 7.3, '~'),
    (384.8, 409.8, 7.3, 'Jonctions horizontales'),
    (733.1, 399.1, 7.3, '~'),
    (556.0, 409.8, 7.3, 'Jonction verticale 1'),
    (553.3, 399.1, 7.3, '2 Zigzag 6 tps 30mm'),
    (39.2, 493.9, 7.3, 'Tissu(s):          Voir avec ABC suivant stock                                             '),
    (193.2, 399.1, 7.3, '1 zigzag 6 tps 15mm'),
    (216.4, 409.8, 7.3, 'Laizes'),
    (386.9, 399.1, 7.3, '2 Zigzag 6 tps 30mm'),
    (682.8, 25.9, 6.7, 'Fichier 1042-EX.xlsm édité le 06/03/2026'),
)


def generer(sortie: Path) -> Path:
    sortie.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(sortie), pagesize=(LARGEUR, HAUTEUR))
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.setLineWidth(0.5)
    for x0, x1, y in TRAITS_H:
        c.line(x0, y, x1, y)
    for y0, y1, x in TRAITS_V:
        c.line(x, y0, x, y1)
    for x, y, corps, texte in SPANS:
        c.setFont(POLICE, corps)
        c.drawString(x, y, texte)
    c.showPage()
    c.save()
    return sortie


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sortie", default="sample_data/CLIENT-1042-EX/fiche-1042-EX_ffab.pdf")
    a = p.parse_args()
    chemin = generer(Path(a.sortie))
    print(f"ecrit {chemin} ({chemin.stat().st_size} octets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
